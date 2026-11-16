import asyncio
import calendar
import csv
import hashlib
import hmac
import io
import json
import os
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel

from database import bump_db_version, get_conn, init_db, wipe_all_operational_data
from pdf_generator import (
    _format_dot_row_notes,
    build_backroom_poster_pdf,
    build_dropoff_receipt_pdf,
    build_invoice_pdf,
    build_retest_datasheet_pdf,
    build_rin_stickers_pdf,
    build_shelf_claim_slip_pdf,
    build_valve_tags_pdf,
    build_verification_pdf,
    build_visual_log_pdf,
)
from schemas import *
from services.core_ops import *
from services.ai_and_theme import *

router = APIRouter()


@router.get("/api/calendar/month")
def get_full_month_calendar(year: Optional[int] = None, month: Optional[int] = None):
    est_dt = now_eastern().date()
    y = year if (year and 2020 <= year <= 2040) else est_dt.year
    m = month if (month and 1 <= month <= 12) else est_dt.month
    conn = get_conn()
    s_map = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings;").fetchall()}
    grid = build_month_calendar_grid(conn, y, m, s_map)
    conn.close()
    return grid


@router.get("/api/schedule/quote")
def get_phone_turnaround_quote(bottles: int = 1):
    conn = get_conn()
    q = calculate_turnaround_quote(conn, bottles)
    conn.close()
    return q


@router.post("/api/schedule/day-plan")
def save_shop_day_plan(payload: DayPlanInput):
    mode = payload.shift_mode.strip().upper()
    if mode not in SHIFT_MODES:
        mode = "FULL_HYDRO"
    cap = payload.bottle_capacity if payload.bottle_capacity is not None else SHIFT_MODES[mode]["default_cap"]
    if mode in ("PREP_ONLY", "BACKROOM_ONLY"):
        cap = 0
    conn = get_conn()
    conn.execute(
        """INSERT INTO shop_day_plans (work_date, shift_mode, bottle_capacity, day_notes)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(work_date) DO UPDATE SET
               shift_mode=excluded.shift_mode,
               bottle_capacity=excluded.bottle_capacity,
               day_notes=excluded.day_notes;""",
        (payload.work_date.strip(), mode, max(0, int(cap)), payload.day_notes.strip()[:200]),
    )
    bump_db_version(conn)
    conn.commit()
    conn.close()
    return {"saved": True, "work_date": payload.work_date, "shift_mode": mode}


@router.post("/api/schedule/jobs")
def create_schedule_job(payload: ScheduleJobInput):
    cust = payload.customer_name.strip()
    if not cust:
        raise HTTPException(400, "Customer / Department name is required.")
    ph = clean_phone(payload.customer_phone)
    acct = (payload.account_type or "WALK_IN").strip().upper()
    ts = now_eastern().strftime("%Y-%m-%d %I:%M %p")
    pri = "RUSH" if payload.rush_fee else payload.priority.strip().upper()
    jtype = payload.job_type.strip().upper()
    loc = (payload.location_notes.strip() or payload.street_address.strip())[:140]
    ret_addr = payload.return_address.strip()[:140]
    d_miles, d_eta = estimate_route_from_shop(loc) if (loc and jtype != "SHOP_BATCH") else (0.0, 0)

    contact_p = payload.contact_person.strip()[:60]
    conn = get_conn()
    upsert_rolodex_from_activity(
        conn, cust, ph, acct, contact_p, payload.customer_email.strip(),
        payload.street_address.strip() or loc[:120], payload.city.strip(), payload.state.strip(), payload.zip_code.strip(),
    )
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO schedule_jobs
           (created_at, scheduled_date, due_date, pickup_time, dropoff_time, return_address, customer_email,
            drive_miles, drive_eta_mins, job_type, account_type, po_station, contact_person,
            customer_name, customer_phone, location_notes, bottle_count, tank_summary,
            valves_attached, needs_air_fill, priority, rush_fee, holding_zone,
            prep_status, status, notes, synced)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, 0);""",
        (
            ts,
            payload.scheduled_date.strip(),
            payload.due_date.strip() or payload.scheduled_date.strip(),
            payload.pickup_time.strip()[:24],
            payload.dropoff_time.strip()[:24],
            ret_addr,
            payload.customer_email.strip()[:100],
            d_miles,
            d_eta,
            jtype,
            acct,
            payload.po_station.strip()[:60],
            contact_p,
            cust,
            ph,
            loc,
            max(1, int(payload.bottle_count or 1)),
            payload.tank_summary.strip()[:160],
            1 if payload.valves_attached else 0,
            pri,
            1 if (payload.rush_fee or pri == "RUSH") else 0,
            payload.holding_zone.strip() or "ZONE_A_BACKROOM",
            payload.prep_status.strip().upper() or "WAITING",
            payload.status.strip().upper() or "OPEN",
            payload.notes.strip()[:280],
        ),
    )
    jid = cur.lastrowid
    bump_db_version(conn)
    conn.commit()
    row = enrich_schedule_job_view(dict(conn.execute("SELECT * FROM schedule_jobs WHERE id=?;", (jid,)).fetchone()))
    conn.close()
    return row


@router.put("/api/schedule/jobs/{job_id}")
def update_schedule_job(job_id: int, payload: ScheduleJobPatchInput):
    conn = get_conn()
    rec = conn.execute("SELECT * FROM schedule_jobs WHERE id=?;", (job_id,)).fetchone()
    if not rec:
        conn.close()
        raise HTTPException(404, "Scheduled job not found.")

    pri = payload.priority.strip().upper() if payload.priority is not None else rec["priority"]
    rfee = (1 if payload.rush_fee else 0) if payload.rush_fee is not None else int(rec["rush_fee"] or 0)
    if pri == "RUSH":
        rfee = 1

    new_jtype = payload.job_type if payload.job_type is not None else rec["job_type"]
    new_loc = payload.location_notes.strip()[:140] if payload.location_notes is not None else (rec["location_notes"] or "")
    d_miles, d_eta = rec["drive_miles"] or 0.0, rec["drive_eta_mins"] or 0
    if (payload.location_notes is not None and new_loc != (rec["location_notes"] or "")) or (new_loc and not d_eta and new_jtype != "SHOP_BATCH"):
        d_miles, d_eta = estimate_route_from_shop(new_loc) if new_loc else (0.0, 0)

    leg1_done = (1 if payload.pickup_leg_done else 0) if payload.pickup_leg_done is not None else int(rec["pickup_leg_done"] or 0)

    conn.execute(
        """UPDATE schedule_jobs SET
           scheduled_date=?, due_date=?, pickup_time=?, dropoff_time=?, return_address=?,
           drive_miles=?, drive_eta_mins=?, pickup_leg_done=?,
           job_type=?, customer_name=?, customer_phone=?,
           location_notes=?, bottle_count=?, tank_summary=?, priority=?, rush_fee=?,
           holding_zone=?, prep_status=?, status=?, notes=?, synced=0
           WHERE id=?;""",
        (
            payload.scheduled_date if payload.scheduled_date is not None else rec["scheduled_date"],
            payload.due_date if payload.due_date is not None else rec["due_date"],
            payload.pickup_time.strip()[:24] if payload.pickup_time is not None else (rec["pickup_time"] or ""),
            payload.dropoff_time.strip()[:24] if payload.dropoff_time is not None else (rec["dropoff_time"] or ""),
            payload.return_address.strip()[:140] if payload.return_address is not None else (rec["return_address"] or ""),
            d_miles,
            d_eta,
            leg1_done,
            new_jtype,
            payload.customer_name.strip() if payload.customer_name is not None else rec["customer_name"],
            clean_phone(payload.customer_phone) if payload.customer_phone is not None else rec["customer_phone"],
            new_loc,
            payload.bottle_count if payload.bottle_count is not None else rec["bottle_count"],
            payload.tank_summary if payload.tank_summary is not None else rec["tank_summary"],
            pri,
            rfee,
            payload.holding_zone if payload.holding_zone is not None else rec["holding_zone"],
            payload.prep_status if payload.prep_status is not None else rec["prep_status"],
            payload.status if payload.status is not None else rec["status"],
            payload.notes if payload.notes is not None else rec["notes"],
            job_id,
        ),
    )
    bump_db_version(conn)
    conn.commit()
    updated = enrich_schedule_job_view(dict(conn.execute("SELECT * FROM schedule_jobs WHERE id=?;", (job_id,)).fetchone()))
    conn.close()
    return updated


@router.delete("/api/schedule/jobs/{job_id}")
def delete_schedule_job(job_id: int):
    conn = get_conn()
    conn.execute("DELETE FROM schedule_jobs WHERE id=?;", (job_id,))
    bump_db_version(conn)
    conn.commit()
    conn.close()
    return {"deleted": job_id}


@router.get("/api/timeclock/status")
def get_worker_timeclock_status(request: Request):
    u = getattr(request.state, "user", None) or get_current_session_user(request)
    if not u:
        raise HTTPException(401, "Not authenticated.")
    now_e = now_eastern()
    today_iso = now_e.date().isoformat()
    conn = get_conn()
    open_p = conn.execute(
        "SELECT * FROM time_punches WHERE crew_initials=? AND status='OPEN' ORDER BY id DESC LIMIT 1;",
        (u["initials"],),
    ).fetchone()
    open_dict = dict(open_p) if open_p else None
    if open_dict:
        try:
            dt_in = datetime.fromisoformat(open_dict["clock_in_iso"])
            open_dict["elapsed_minutes"] = max(0, int((now_e - dt_in).total_seconds() // 60))
        except Exception:
            open_dict["elapsed_minutes"] = 0

    today_mins = conn.execute(
        "SELECT COALESCE(SUM(duration_minutes), 0) FROM time_punches WHERE crew_initials=? AND work_date=? AND status='CLOSED';",
        (u["initials"], today_iso),
    ).fetchone()[0]
    active_shop = [
        dict(r) for r in conn.execute("SELECT crew_initials, crew_name, clock_in_at, work_date FROM time_punches WHERE status='OPEN' ORDER BY id ASC;").fetchall()
    ]
    conn.close()
    return {
        "clocked_in": bool(open_dict),
        "open_punch": open_dict,
        "today_total_minutes": int(today_mins) + (open_dict["elapsed_minutes"] if open_dict else 0),
        "active_workers": active_shop,
    }


@router.post("/api/timeclock/punch")
def punch_worker_timeclock(payload: TimePunchActionInput, request: Request):
    u = getattr(request.state, "user", None) or get_current_session_user(request)
    if not u:
        raise HTTPException(401, "Not authenticated.")
    now_e = now_eastern()
    today_iso = now_e.date().isoformat()
    ts_lbl = now_e.strftime("%I:%M %p")
    iso_full = now_e.isoformat()
    act = payload.action.strip().upper()

    conn = get_conn()
    open_p = conn.execute(
        "SELECT * FROM time_punches WHERE crew_initials=? AND status='OPEN' ORDER BY id DESC LIMIT 1;",
        (u["initials"],),
    ).fetchone()

    if act == "IN" or (act == "TOGGLE" and not open_p):
        if open_p:
            conn.close()
            return {"status": "ALREADY_IN", "punch": dict(open_p)}
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO time_punches
               (user_id, crew_initials, crew_name, work_date, clock_in_at, clock_in_iso, notes, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'OPEN');""",
            (u["id"], u["initials"], u["name"], today_iso, ts_lbl, iso_full, payload.notes.strip()[:160]),
        )
        pid = cur.lastrowid
        bump_db_version(conn)
        conn.commit()
        row = dict(conn.execute("SELECT * FROM time_punches WHERE id=?;", (pid,)).fetchone())
        conn.close()
        return {"status": "CLOCKED_IN", "punch": row}

    if not open_p:
        conn.close()
        raise HTTPException(400, "You are not currently clocked in.")

    try:
        dt_in = datetime.fromisoformat(open_p["clock_in_iso"])
        mins = max(1, int(round((now_e - dt_in).total_seconds() / 60.0)))
    except Exception:
        mins = 1
    merged_note = " | ".join(filter(None, [open_p["notes"], payload.notes.strip()[:160]]))[:220]
    conn.execute(
        """UPDATE time_punches
           SET clock_out_at=?, clock_out_iso=?, duration_minutes=?, notes=?, status='CLOSED', synced=0
           WHERE id=?;""",
        (ts_lbl, iso_full, mins, merged_note, open_p["id"]),
    )
    bump_db_version(conn)
    conn.commit()
    closed_row = dict(conn.execute("SELECT * FROM time_punches WHERE id=?;", (open_p["id"],)).fetchone())
    conn.close()
    trigger_sheets_sync_bg()
    return {"status": "CLOCKED_OUT", "punch": closed_row}


@router.get("/api/timeclock/report")
def get_timeclock_report(request: Request, days: int = 14):
    u = getattr(request.state, "user", None) or get_current_session_user(request)
    if not u:
        raise HTTPException(401, "Not authenticated.")
    cutoff = (now_eastern().date() - timedelta(days=max(1, min(90, days)))).isoformat()
    is_boss = u["role"] in ("OWNER", "SYSTEM_ADMIN")
    conn = get_conn()
    if is_boss:
        rows = [dict(r) for r in conn.execute("SELECT * FROM time_punches WHERE work_date >= ? ORDER BY id DESC LIMIT 250;", (cutoff,)).fetchall()]
    else:
        rows = [dict(r) for r in conn.execute("SELECT * FROM time_punches WHERE crew_initials=? AND work_date >= ? ORDER BY id DESC LIMIT 100;", (u["initials"], cutoff)).fetchall()]
    conn.close()

    totals: dict[str, dict] = {}
    for r in rows:
        k = r["crew_initials"]
        totals.setdefault(k, {"initials": k, "name": r["crew_name"], "minutes": 0, "shifts": 0})
        totals[k]["minutes"] += int(r["duration_minutes"] or 0)
        if r["status"] == "CLOSED":
            totals[k]["shifts"] += 1
    for v in totals.values():
        v["hours"] = round(v["minutes"] / 60.0, 2)
    return {"punches": rows, "summary": list(totals.values()), "is_boss": is_boss}


@router.put("/api/timeclock/{punch_id}")
def edit_timeclock_punch(punch_id: int, payload: ManualPunchEditInput, request: Request):
    u = require_owner_or_admin(request)
    conn = get_conn()
    row = conn.execute("SELECT * FROM time_punches WHERE id=?;", (punch_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Time punch not found.")
    conn.execute(
        """UPDATE time_punches
           SET duration_minutes=?, clock_in_at=COALESCE(?, clock_in_at),
               clock_out_at=COALESCE(?, clock_out_at), notes=COALESCE(?, notes),
               status='CLOSED', edited_by=?
           WHERE id=?;""",
        (max(0, int(payload.duration_minutes)), payload.clock_in_at, payload.clock_out_at, payload.notes, u["initials"], punch_id),
    )
    bump_db_version(conn)
    conn.commit()
    conn.close()
    return {"updated": punch_id}


@router.delete("/api/timeclock/{punch_id}")
def delete_timeclock_punch(punch_id: int, request: Request):
    require_owner_or_admin(request)
    conn = get_conn()
    conn.execute("DELETE FROM time_punches WHERE id=?;", (punch_id,))
    bump_db_version(conn)
    conn.commit()
    conn.close()
    return {"deleted": punch_id}


@router.get("/api/timeclock/export.csv")
def export_timeclock_csv(request: Request, days: int = 30):
    require_owner_or_admin(request)
    cutoff = (now_eastern().date() - timedelta(days=max(1, min(365, days)))).isoformat()
    conn = get_conn()
    rows = conn.execute("SELECT * FROM time_punches WHERE work_date >= ? ORDER BY work_date DESC, id DESC;", (cutoff,)).fetchall()
    conn.close()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Date", "Worker", "Initials", "Clock In", "Clock Out", "Minutes", "Hours", "Status", "Notes"])
    for r in rows:
        mins = int(r["duration_minutes"] or 0)
        w.writerow([r["work_date"], r["crew_name"], r["crew_initials"], r["clock_in_at"], r["clock_out_at"], mins, round(mins / 60.0, 2), r["status"], r["notes"]])
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="Webers_Timecards_{today_eastern()}.csv"'},
    )


@router.get("/api/day/overview")
def get_day_overview(request: Request):
    u = getattr(request.state, "user", None) or get_current_session_user(request)
    if not u:
        raise HTTPException(401, "Not authenticated.")
    today_iso = today_eastern()
    today_d = date.fromisoformat(today_iso)
    tomorrow_iso = (today_d + timedelta(days=1)).isoformat()

    conn = get_conn()
    s_map = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings;").fetchall()}
    default_cap = int(s_map.get("daily_hydro_capacity", "24") or 24)

    p_row = conn.execute("SELECT * FROM shop_day_plans WHERE work_date=?;", (today_iso,)).fetchone()
    if p_row:
        day_plan = dict(p_row)
    else:
        mode, cap = default_day_plan_for_date(today_d, default_cap)
        day_plan = {
            "work_date": today_iso, "shift_mode": mode, "bottle_capacity": cap,
            "day_notes": "", "lineup_locked": 0, "allow_extra_autopace": 1,
        }

    ceilings = get_date_jacket_ceilings(conn, today_iso)
    s_psi = ceilings["Short Jacket"]["max_calibrated_psi"]
    t_psi = ceilings["Tall Jacket"]["max_calibrated_psi"]

    todays_jobs = [
        dict(r) for r in conn.execute(
            "SELECT * FROM schedule_jobs WHERE status!='COMPLETED' AND scheduled_date=? ORDER BY locked_for_day DESC, rush_fee DESC, id ASC;",
            (today_iso,),
        ).fetchall()
    ]
    carryover_jobs = [
        dict(r) for r in conn.execute(
            "SELECT * FROM schedule_jobs WHERE status!='COMPLETED' AND scheduled_date < ? ORDER BY scheduled_date ASC, id ASC;",
            (today_iso,),
        ).fetchall()
    ]
    upcoming_jobs = [
        dict(r) for r in conn.execute(
            "SELECT * FROM schedule_jobs WHERE status!='COMPLETED' AND scheduled_date > ? ORDER BY scheduled_date ASC, rush_fee DESC LIMIT 12;",
            (today_iso,),
        ).fetchall()
    ]
    waiting_drops = [
        dict(r) for r in conn.execute(
            "SELECT * FROM dropoffs WHERE status IN ('IN_BACKROOM','CHECKED_IN') AND entry_type='DROPOFF' AND COALESCE(imported_count,0) < bottle_count ORDER BY rush_requested DESC, id DESC;",
        ).fetchall()
    ]

    if u["role"] in ("OWNER", "SYSTEM_ADMIN"):
        tasks = [
            dict(r) for r in conn.execute(
                "SELECT * FROM crew_tasks WHERE work_date=? OR status='OPEN' ORDER BY CASE status WHEN 'OPEN' THEN 0 ELSE 1 END, id DESC;",
                (today_iso,),
            ).fetchall()
        ]
        crew_list = [
            {"initials": r["initials"], "name": r["name"]}
            for r in conn.execute("SELECT initials, name FROM crew_users WHERE active=1 AND role!='SYSTEM_ADMIN' ORDER BY CASE role WHEN 'OWNER' THEN 0 ELSE 1 END, id;").fetchall()
        ]
    else:
        tasks = [
            dict(r) for r in conn.execute(
                "SELECT * FROM crew_tasks WHERE (work_date=? OR status='OPEN') AND (assigned_to='ALL' OR assigned_to=?) ORDER BY CASE status WHEN 'OPEN' THEN 0 ELSE 1 END, id DESC;",
                (today_iso, u["initials"]),
            ).fetchall()
        ]
        crew_list = []

    # End-of-Day Stats
    tested_today = conn.execute("SELECT COUNT(*) FROM cylinder_records WHERE work_date=? AND status='COMPLETED';", (today_iso,)).fetchone()[0]
    passed_today = conn.execute("SELECT COUNT(*) FROM cylinder_records WHERE work_date=? AND status='COMPLETED' AND hydro_disposition='A';", (today_iso,)).fetchone()[0]
    condemned_today = conn.execute("SELECT COUNT(*) FROM cylinder_records WHERE work_date=? AND status='COMPLETED' AND hydro_disposition!='A';", (today_iso,)).fetchone()[0]
    queued_now = conn.execute("SELECT COUNT(*) FROM cylinder_records WHERE status='QUEUED';", ()).fetchone()[0]
    unmarked_pass = conn.execute("SELECT COUNT(*) FROM cylinder_records WHERE status='COMPLETED' AND hydro_disposition='A' AND COALESCE(rin_marked,0)=0 AND shop_stage!='PICKED_UP';").fetchone()[0]
    unbilled_shelf = conn.execute("SELECT COUNT(DISTINCT customer_name) FROM cylinder_records WHERE status='COMPLETED' AND invoice_id IS NULL AND shop_stage='READY_BACKROOM';").fetchone()[0]
    conn.close()

    return {
        "today": today_iso,
        "tomorrow": tomorrow_iso,
        "user": u,
        "cal_status": {
            "short_calibrated": bool(s_psi),
            "tall_calibrated": bool(t_psi),
            "any_calibrated": bool(s_psi or t_psi),
            "both_calibrated": bool(s_psi and t_psi),
            "short_allowed_psi": ceilings["Short Jacket"]["max_allowed_test_psi"],
            "tall_allowed_psi": ceilings["Tall Jacket"]["max_allowed_test_psi"],
        },
        "day_plan": day_plan,
        "todays_jobs": todays_jobs,
        "carryover_jobs": carryover_jobs,
        "upcoming_jobs": upcoming_jobs,
        "waiting_dropoffs": waiting_drops,
        "tasks": tasks,
        "crew_list": crew_list,
        "eod_summary": {
            "tested_today": tested_today,
            "passed_today": passed_today,
            "condemned_today": condemned_today,
            "queued_remaining": queued_now,
            "unmarked_pass": unmarked_pass,
            "unbilled_shelf_customers": unbilled_shelf,
        },
    }


@router.post("/api/day/lock-lineup")
def lock_todays_lineup(payload: LockDayLineupInput, request: Request):
    u = require_owner_or_admin(request)
    today_iso = today_eastern()
    cap = max(0, min(200, int(payload.bottle_capacity)))
    allow_extra = 1 if payload.allow_extra_autopace else 0
    ts = now_eastern().strftime("%Y-%m-%d %I:%M %p")

    conn = get_conn()
    conn.execute(
        """INSERT INTO shop_day_plans
           (work_date, shift_mode, bottle_capacity, day_notes, lineup_locked, allow_extra_autopace, started_by, started_at, updated_at)
           VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?)
           ON CONFLICT(work_date) DO UPDATE SET
             shift_mode=excluded.shift_mode,
             bottle_capacity=excluded.bottle_capacity,
             day_notes=excluded.day_notes,
             lineup_locked=1,
             allow_extra_autopace=excluded.allow_extra_autopace,
             started_by=excluded.started_by,
             started_at=excluded.started_at,
             updated_at=excluded.updated_at;""",
        (today_iso, payload.shift_mode, cap, payload.day_notes.strip()[:160], allow_extra, u["initials"], ts, ts),
    )
    # Freeze all jobs currently sitting on today's lineup so auto-pace cannot bump them
    conn.execute(
        "UPDATE schedule_jobs SET locked_for_day=1 WHERE scheduled_date=? AND status!='COMPLETED';",
        (today_iso,),
    )
    pace_res = run_smart_autopace(conn)
    bump_db_version(conn)
    conn.commit()
    conn.close()
    return {"locked": True, "autopace": pace_res}


@router.post("/api/day/job-move")
def move_or_edit_day_job(payload: DayJobMoveInput, request: Request):
    require_owner_or_admin(request)
    today_iso = today_eastern()
    tomorrow_iso = (date.fromisoformat(today_iso) + timedelta(days=1)).isoformat()
    act = payload.action.strip().upper()

    conn = get_conn()
    if act == "PULL_TODAY":
        conn.execute(
            "UPDATE schedule_jobs SET scheduled_date=?, locked_for_day=1, auto_paced=0, synced=0 WHERE id=?;",
            (today_iso, payload.job_id),
        )
    elif act == "BUMP_TOMORROW":
        conn.execute(
            "UPDATE schedule_jobs SET scheduled_date=?, locked_for_day=0, auto_paced=0, synced=0 WHERE id=?;",
            (tomorrow_iso, payload.job_id),
        )
    elif act == "SET_COUNT" and payload.bottle_count is not None:
        conn.execute(
            "UPDATE schedule_jobs SET bottle_count=?, synced=0 WHERE id=?;",
            (max(1, min(250, int(payload.bottle_count))), payload.job_id),
        )
    elif act == "TOGGLE_LOCK":
        conn.execute(
            "UPDATE schedule_jobs SET locked_for_day = CASE WHEN COALESCE(locked_for_day,0)=1 THEN 0 ELSE 1 END WHERE id=?;",
            (payload.job_id,),
        )
    bump_db_version(conn)
    conn.commit()
    conn.close()
    return {"updated": True}


@router.post("/api/day/autopace")
def trigger_manual_autopace(request: Request):
    require_owner_or_admin(request)
    conn = get_conn()
    res = run_smart_autopace(conn)
    bump_db_version(conn)
    conn.commit()
    conn.close()
    return res


@router.post("/api/day/tasks")
def create_crew_task(payload: CrewTaskInput, request: Request):
    u = require_owner_or_admin(request)
    title = payload.title.strip()[:200]
    if not title:
        raise HTTPException(400, "Enter a task description first.")
    w_date = payload.work_date.strip() or today_eastern()
    assigned = payload.assigned_to.strip().upper()[:8] or "ALL"
    cat = "PROPERTY" if payload.category.strip().upper() == "PROPERTY" else "SHOP"

    conn = get_conn()
    a_name = "All Crew"
    if assigned != "ALL":
        urow = conn.execute("SELECT name FROM crew_users WHERE initials=?;", (assigned,)).fetchone()
        if urow:
            a_name = urow["name"]
    conn.execute(
        """INSERT INTO crew_tasks
           (work_date, assigned_to, assigned_name, category, title, priority, status, created_by, created_at)
           VALUES (?, ?, ?, ?, ?, ?, 'OPEN', ?, ?);""",
        (w_date, assigned, a_name, cat, title, payload.priority.strip().upper() or "NORMAL", u["initials"], now_eastern().strftime("%I:%M %p")),
    )
    bump_db_version(conn)
    conn.commit()
    conn.close()
    return {"created": True}


@router.put("/api/day/tasks/{task_id}")
def update_crew_task(task_id: int, payload: CrewTaskUpdateInput, request: Request):
    u = getattr(request.state, "user", None) or get_current_session_user(request)
    if not u:
        raise HTTPException(401, "Not authenticated.")
    conn = get_conn()
    t = conn.execute("SELECT * FROM crew_tasks WHERE id=?;", (task_id,)).fetchone()
    if not t:
        conn.close()
        raise HTTPException(404, "Task not found.")
    new_status = payload.status if payload.status in ("OPEN", "DONE") else t["status"]
    comp_by = u["initials"] if new_status == "DONE" else ""
    comp_at = now_eastern().strftime("%I:%M %p") if new_status == "DONE" else ""
    note = payload.crew_note.strip()[:200] if payload.crew_note is not None else t["crew_note"]
    conn.execute(
        "UPDATE crew_tasks SET status=?, completed_by=?, completed_at=?, crew_note=? WHERE id=?;",
        (new_status, comp_by, comp_at, note, task_id),
    )
    bump_db_version(conn)
    conn.commit()
    conn.close()
    return {"updated": True}


@router.delete("/api/day/tasks/{task_id}")
def delete_crew_task(task_id: int, request: Request):
    require_owner_or_admin(request)
    conn = get_conn()
    conn.execute("DELETE FROM crew_tasks WHERE id=?;", (task_id,))
    bump_db_version(conn)
    conn.commit()
    conn.close()
    return {"deleted": task_id}


@router.post("/api/day/end-day")
def complete_end_of_day(payload: EndOfDayInput, request: Request):
    u = getattr(request.state, "user", None) or get_current_session_user(request)
    if not u:
        raise HTTPException(401, "Not authenticated.")
    today_iso = today_eastern()
    tomorrow_iso = (date.fromisoformat(today_iso) + timedelta(days=1)).isoformat()
    ts = now_eastern().strftime("%Y-%m-%d %I:%M %p")
    note_txt = payload.shift_note.strip()[:240]

    conn = get_conn()
    if note_txt:
        # Log crew/owner shift wrap-up note onto today's day plan
        existing = conn.execute("SELECT shift_notes FROM shop_day_plans WHERE work_date=?;", (today_iso,)).fetchone()
        prev_n = (existing["shift_notes"] if existing and existing["shift_notes"] else "").strip()
        merged = (f"{prev_n} | {u['initials']}: {note_txt}" if prev_n else f"{u['initials']}: {note_txt}")[:400]
        conn.execute(
            """INSERT INTO shop_day_plans (work_date, shift_mode, bottle_capacity, shift_notes, updated_at)
               VALUES (?, 'FULL_HYDRO', 24, ?, ?)
               ON CONFLICT(work_date) DO UPDATE SET shift_notes=excluded.shift_notes, updated_at=excluded.updated_at;""",
            (today_iso, merged, ts),
        )

    rolled = 0
    if u["role"] in ("OWNER", "SYSTEM_ADMIN") and payload.roll_unfinished:
        res = conn.execute(
            "UPDATE schedule_jobs SET scheduled_date=?, locked_for_day=0, auto_paced=0, synced=0 WHERE scheduled_date<=? AND status!='COMPLETED' AND job_type='SHOP_BATCH';",
            (tomorrow_iso, today_iso),
        )
        rolled = res.rowcount
        conn.execute("UPDATE shop_day_plans SET closed_out_at=? WHERE work_date=?;", (ts, today_iso))

    bump_db_version(conn)
    conn.commit()
    conn.close()
    trigger_sheets_sync_bg()
    return {"closed": True, "rolled_jobs": rolled}


@router.post("/api/schedule/{job_id}/status")
def quick_update_schedule_job_status(job_id: int, payload: dict):
    st = str(payload.get("status") or "COMPLETED").strip().upper()
    conn = get_conn()
    conn.execute("UPDATE schedule_jobs SET status=? WHERE id=?;", (st, job_id))
    bump_db_version(conn)
    conn.commit()
    conn.close()
    return {"ok": True, "status": st}


@router.get("/api/ops/dispatch-radar")
def get_ops_dispatch_radar():
    today = today_eastern()
    conn = get_conn()
    jobs = [
        dict(r)
        for r in conn.execute(
            """
            SELECT * FROM schedule_jobs
            WHERE COALESCE(status, 'SCHEDULED') NOT IN ('COMPLETED', 'DONE', 'CANCELLED')
            ORDER BY scheduled_date ASC, id ASC;
            """
        ).fetchall()
    ]
    truck_reqs = [
        dict(d)
        for d in conn.execute(
            """
            SELECT * FROM dropoffs
            WHERE entry_type='TRUCK_REQUEST' AND COALESCE(status, 'PENDING') NOT IN ('IMPORTED', 'PICKED_UP', 'COMPLETED', 'ARCHIVED')
            ORDER BY id DESC;
            """
        ).fetchall()
    ]
    conn.close()
    return {"today": today, "jobs": [enrich_schedule_job_view(j) for j in jobs], "truck_requests": truck_reqs}
