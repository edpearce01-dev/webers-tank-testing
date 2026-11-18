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


@router.post("/api/binder")
def set_binder_sheet(payload: BinderActionInput, request: Request):
    require_owner_or_admin(request)
    conn = get_conn()
    s_map = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings;").fetchall()}
    b = get_binder_status(conn, s_map)
    if payload.action == "fresh_next":
        target = b["active_sheet_no"] + (1 if b["rows_used_on_active"] > 0 else 0)
    elif payload.action == "set_custom" and payload.sheet_no:
        target = max(1, int(payload.sheet_no))
    else:
        target = b["active_sheet_no"]
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('current_sheet_no', ?);", (str(target),))
    bump_db_version(conn)
    conn.commit()
    s_map["current_sheet_no"] = str(target)
    updated_b = get_binder_status(conn, s_map)
    conn.close()
    return updated_b


@router.get("/api/ping")
def ping_version():
    conn = get_conn()
    ver = int(conn.execute("SELECT value FROM settings WHERE key='db_version';").fetchone()["value"])
    unsynced = conn.execute(
        "SELECT (SELECT COUNT(*) FROM daily_verifications WHERE synced=0) + "
        "(SELECT COUNT(*) FROM cylinder_records WHERE synced=0 AND status='COMPLETED') + "
        "(SELECT COUNT(*) FROM invoices WHERE synced=0) + "
        "(SELECT COUNT(*) FROM dropoffs WHERE synced=0) + "
        "(SELECT COUNT(*) FROM crew_feedback WHERE synced=0);"
    ).fetchone()[0]
    backroom_waiting = conn.execute(
        "SELECT COUNT(*) FROM dropoffs WHERE (status IN ('IN_BACKROOM', 'CHECKED_IN') AND COALESCE(imported_count,0) < bottle_count) OR status='PICKUP_LOGGED';"
    ).fetchone()[0]
    low_stock_count = conn.execute("SELECT COUNT(*) FROM shop_supplies WHERE low_stock=1;").fetchone()[0]
    open_inquiries_count = conn.execute("SELECT COUNT(*) FROM shop_messages WHERE channel='WEB_INQUIRY' AND status='OPEN';").fetchone()[0]
    latest_msg_id = conn.execute("SELECT COALESCE(MAX(id), 0) FROM shop_messages;").fetchone()[0]
    open_tasks_count = conn.execute("SELECT COUNT(*) FROM crew_tasks WHERE status='OPEN';").fetchone()[0]
    conn.close()
    return {
        "db_version": ver,
        "unsynced_count": unsynced,
        "backroom_waiting": backroom_waiting,
        "low_stock_count": low_stock_count,
        "open_inquiries_count": open_inquiries_count,
        "latest_msg_id": latest_msg_id,
        "open_tasks_count": open_tasks_count,
    }


@router.get("/api/tv")
def get_tv_leaderboard(work_date: Optional[str] = None):
    est = now_eastern()
    est_today = est.date().isoformat()
    target_date = work_date or est_today
    conn = get_conn()
    s_map = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings;").fetchall()}
    default_cap = int(s_map.get("daily_hydro_capacity") or 24)
    binder = get_binder_status(conn, s_map)

    cal_start = parse_iso_date(target_date) or est.date()
    plans_rows = conn.execute(
        "SELECT * FROM shop_day_plans WHERE work_date >= ? AND work_date <= ?;",
        (cal_start.isoformat(), (cal_start + timedelta(days=20)).isoformat()),
    ).fetchall()
    plans_map = {r["work_date"]: dict(r) for r in plans_rows}
    today_plan = resolve_day_plan(cal_start, plans_map, default_cap)

    upcoming_jobs = [
        dict(r)
        for r in conn.execute(
            """SELECT * FROM schedule_jobs
               WHERE status != 'COMPLETED' AND status != 'CANCELLED'
               ORDER BY CASE WHEN rush_fee=1 OR priority='RUSH' THEN 0 ELSE 1 END,
                        scheduled_date ASC, id ASC LIMIT 40;"""
        ).fetchall()
    ]

    calendar_days = []
    for i in range(14):
        d_cur = cal_start + timedelta(days=i)
        p = resolve_day_plan(d_cur, plans_map, default_cap)
        day_jobs = [enrich_schedule_job_view(j, "PRIMARY") for j in upcoming_jobs if j["scheduled_date"] == p["work_date"]] + [enrich_schedule_job_view(j, "RETURN") for j in upcoming_jobs if j["job_type"] == "ROAD_ROUNDTRIP" and j.get("due_date") == p["work_date"] and j["due_date"] != j["scheduled_date"]]
        due_jobs = [j for j in upcoming_jobs if j.get("due_date") == p["work_date"] and j["scheduled_date"] != p["work_date"]]
        booked_bottles = sum(j["bottle_count"] for j in day_jobs if j["job_type"] in ("SHOP_BATCH", "ROAD_PICKUP", "ROAD_ROUNDTRIP"))
        p["jobs"] = day_jobs
        p["due_jobs"] = due_jobs
        p["booked_bottles"] = booked_bottles
        calendar_days.append(p)

    jacket_ceilings = {}
    for j_name in ("Short Jacket", "Tall Jacket"):
        row = conn.execute(
            """SELECT COALESCE(MAX(target_pressure_psi), 0) AS max_cal,
                      MAX(test_time) AS last_time
               FROM daily_verifications
               WHERE test_date=? AND jacket_type=? AND passed=1;""",
            (target_date, j_name),
        ).fetchone()
        max_cal = int(row["max_cal"] or 0)
        jacket_ceilings[j_name] = {
            "max_calibrated_psi": max_cal,
            "max_allowed_test_psi": dot_max_allowed_test_psi(max_cal),
            "verified_at": row["last_time"] or "",
        }

    queued_raw = [
        enrich_record_meta(dict(r), est_today)
        for r in conn.execute("SELECT * FROM cylinder_records WHERE status='QUEUED';").fetchall()
    ]
    queued_raw.sort(key=queue_sort_key)

    completed_today_rows = [
        enrich_record_meta(dict(r), est_today)
        for r in conn.execute(
            "SELECT * FROM cylinder_records WHERE work_date=? AND status='COMPLETED' ORDER BY id ASC;",
            (target_date,),
        ).fetchall()
    ]
    passed_today = sum(1 for r in completed_today_rows if r.get("hydro_disposition") == "A")
    condemned_today = sum(1 for r in completed_today_rows if r.get("hydro_disposition") != "A")

    active_valves = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM cylinder_records WHERE valve_tag != '' AND valve_reinstalled = 0 ORDER BY id DESC LIMIT 36;"
        ).fetchall()
    ]

    dropoffs = [
        dict(r)
        for r in conn.execute(
            """SELECT * FROM dropoffs
               WHERE (status IN ('IN_BACKROOM', 'CHECKED_IN') AND COALESCE(imported_count,0) < bottle_count)
                  OR status='PICKUP_LOGGED'
               ORDER BY rush_requested DESC, id DESC LIMIT 16;"""
        ).fetchall()
    ]

    needs_stamp_count = conn.execute(
        """SELECT COUNT(*) FROM cylinder_records
           WHERE status='COMPLETED' AND hydro_disposition='A' AND COALESCE(rin_marked,0)=0 AND shop_stage!='PICKED_UP';"""
    ).fetchone()[0]

    ready_shelf_count = conn.execute(
        "SELECT COUNT(*) FROM cylinder_records WHERE status='COMPLETED' AND shop_stage='READY_BACKROOM';"
    ).fetchone()[0]

    expected_map: dict[str, int] = {}
    for j in upcoming_jobs:
        expected_map[j["customer_name"]] = max(expected_map.get(j["customer_name"], 0), int(j["bottle_count"] or 0))
    for dr in conn.execute("SELECT customer_name, bottle_count FROM dropoffs WHERE status != 'ARCHIVED';").fetchall():
        expected_map[dr["customer_name"]] = max(expected_map.get(dr["customer_name"], 0), int(dr["bottle_count"] or 0))

    batch_rows = [
        dict(r)
        for r in conn.execute(
            """SELECT customer_name,
                      MAX(customer_phone) AS customer_phone,
                      MAX(rush_fee) AS is_rush,
                      MIN(CASE WHEN promised_date != '' THEN promised_date ELSE '9999-12-31' END) AS earliest_due,
                      MIN(COALESCE(NULLIF(SUBSTR(intake_at,1,10),''), work_date)) AS oldest_date,
                      COUNT(*) AS scanned_total,
                      SUM(CASE WHEN status='COMPLETED' THEN 1 ELSE 0 END) AS completed,
                      SUM(CASE WHEN status='COMPLETED' AND hydro_disposition='A' AND COALESCE(rin_marked,0)=0 THEN 1 ELSE 0 END) AS needs_stamp,
                      SUM(CASE WHEN shop_stage='READY_BACKROOM' THEN 1 ELSE 0 END) AS ready_backroom
               FROM cylinder_records
               WHERE status='QUEUED' OR work_date=? OR shop_stage != 'PICKED_UP'
               GROUP BY customer_name
               ORDER BY is_rush DESC, earliest_due ASC, completed ASC;""",
            (target_date,),
        ).fetchall()
    ]
    for b in batch_rows:
        if b["earliest_due"] == "9999-12-31":
            b["earliest_due"] = ""
        b["total"] = max(int(b["scanned_total"]), expected_map.get(b["customer_name"], 0))
        b["dwell_days"] = days_since(b.get("oldest_date"), est_today)

    low_supplies = [
        r["item_name"]
        for r in conn.execute("SELECT item_name FROM shop_supplies WHERE low_stock=1 ORDER BY sort_order ASC;").fetchall()
    ]

    shop_tasks = [
        dict(r)
        for r in conn.execute(
            """SELECT * FROM crew_tasks
               WHERE status='OPEN' OR work_date=?
               ORDER BY CASE status WHEN 'OPEN' THEN 0 ELSE 1 END,
                        CASE priority WHEN 'HIGH' THEN 0 ELSE 1 END,
                        id DESC LIMIT 28;""",
            (target_date,),
        ).fetchall()
    ]
    open_tasks = [t for t in shop_tasks if t.get("status") == "OPEN"]

    unsynced = conn.execute(
        "SELECT (SELECT COUNT(*) FROM daily_verifications WHERE synced=0) + "
        "(SELECT COUNT(*) FROM cylinder_records WHERE synced=0 AND status='COMPLETED') + "
        "(SELECT COUNT(*) FROM invoices WHERE synced=0);"
    ).fetchone()[0]
    conn.close()

    ticker = [
        f"📅 TODAY: {today_plan['shift_label'].upper()}" + (f" ({today_plan['day_notes']})" if today_plan["day_notes"] else ""),
        f"📖 RETEST BOOK: SHEET #{binder['active_sheet_no']} • NEXT ROW #{binder['next_row_number']} OF 12",
    ]
    for j_name, jc in jacket_ceilings.items():
        if jc["max_allowed_test_psi"]:
            ticker.append(f"🟢 {j_name.upper()}: CERTIFIED ≤ {jc['max_allowed_test_psi']} PSI ({jc['verified_at']})")
        elif today_plan["shift_mode"] in ("FULL_HYDRO", "ROAD_RUN"):
            ticker.append(f"🟡 {j_name.upper()}: AWAITING MORNING CALIBRATION")

    for j in upcoming_jobs[:6]:
        j_icon = "🔄 PICKUP+DELIV" if j["job_type"] == "ROAD_ROUNDTRIP" else ("🚚 PICKUP" if j["job_type"] == "ROAD_PICKUP" else ("🚚 DELIVERY" if j["job_type"] == "ROAD_DELIVERY" else "📌 SCHEDULED"))
        rush_tag = " [🔥 RUSH]" if (j["rush_fee"] or j["priority"] == "RUSH") else ""
        ticker.append(f"{j_icon} ({j['scheduled_date']}): {j['customer_name']} — {j['bottle_count']} cyl{rush_tag}")

    for d in dropoffs:
        r_lbl = " [🔥 RUSH]" if d.get("rush_requested") else ""
        rem = max(1, int(d["bottle_count"]) - int(d.get("imported_count") or 0))
        ticker.append(f"📥 BACKROOM DOOR: {d['customer_name']} — {rem} tank(s) waiting{r_lbl}")

    if needs_stamp_count:
        ticker.append(f"🔨 DRYING RACK: {needs_stamp_count} DRIED CYLINDER(S) NEED B386 STAMP / STICKER")

    for t in open_tasks[:5]:
        t_ico = "🚜 PROPERTY TO-DO" if t.get("category") == "PROPERTY" else "🛠️ SHOP TO-DO"
        p_tag = " [⚡ HIGH]" if t.get("priority") == "HIGH" else ""
        ticker.append(f"{t_ico}: {t['title']}{p_tag}")

    if low_supplies:
        ticker.append(f"📦 LOW SHOP SUPPLY: {', '.join(low_supplies[:3]).upper()}")

    return {
        "db_version": int(s_map.get("db_version", "1")),
        "testing_mode": s_map.get("testing_mode", "1") == "1",
        "dot_rin": s_map.get("dot_rin", "B386"),
        "work_date": target_date,
        "server_time": est.strftime("%I:%M %p"),
        "today_plan": today_plan,
        "binder_state": binder,
        "calendar_days": calendar_days,
        "shop_tasks": shop_tasks,
        "unsynced_count": unsynced,
        "has_sheets_webhook": bool(s_map.get("sheets_webhook_url")),
        "jacket_ceilings": jacket_ceilings,
        "on_stand": queued_raw[0] if queued_raw else None,
        "up_next": queued_raw[1:12],
        "recent_completed": list(reversed(completed_today_rows[-8:])),
        "active_valves": active_valves,
        "backroom_dropoffs": dropoffs,
        "upcoming_schedule": upcoming_jobs[:12],
        "batch_leaderboard": batch_rows,
        "scoreboard": {
            "queued_count": len(queued_raw),
            "completed_today": len(completed_today_rows),
            "passed_today": passed_today,
            "condemned_today": condemned_today,
            "active_tags_count": len(active_valves),
            "needs_stamp_count": needs_stamp_count,
            "backroom_waiting_count": len(dropoffs),
            "ready_pickup_count": ready_shelf_count,
            "open_tasks_count": len(open_tasks),
        },
        "ticker": ticker,
    }


@router.get("/api/ops/live")
def ops_live_state(work_date: Optional[str] = None):
    """Lightweight operational delta for /ops (skips month grid, 5-yr recalls, invoices & static catalogs)."""
    est_today = today_eastern()
    target_date = work_date or est_today
    conn = get_conn()
    s_map = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings;").fetchall()}
    cal_serial = s_map.get("calibrated_serial", "1SF11904")
    default_cap = int(s_map.get("daily_hydro_capacity") or 24)
    has_key = bool(os.getenv("GEMINI_API_KEY") or s_map.get("gemini_api_key"))
    has_webhook = bool(s_map.get("sheets_webhook_url"))

    binder_state = get_binder_status(conn, s_map)
    compliance_alerts = get_compliance_alerts(s_map, est_today)

    cal_anchor = parse_iso_date(target_date) or now_eastern().date()
    p_row = conn.execute("SELECT * FROM shop_day_plans WHERE work_date=?;", (cal_anchor.isoformat(),)).fetchone()
    plans_map = {p_row["work_date"]: dict(p_row)} if p_row else {}

    schedule_jobs = [
        dict(r)
        for r in conn.execute(
            """SELECT * FROM schedule_jobs
               WHERE status != 'CANCELLED' AND status != 'COMPLETED'
               ORDER BY CASE WHEN rush_fee=1 OR priority='RUSH' THEN 0 ELSE 1 END,
                        scheduled_date ASC, id ASC LIMIT 50;"""
        ).fetchall()
    ]
    for sj in schedule_jobs:
        sj["dwell_days"] = days_since(sj.get("created_at"), est_today)
        sj["holding_zone_label"] = HOLDING_ZONES.get(sj.get("holding_zone") or "", sj.get("holding_zone") or "")
        sj["maps_route_url"] = build_shop_maps_url(sj.get("location_notes") or "")

    selected_day_plan = resolve_day_plan(cal_anchor, plans_map, default_cap)
    selected_day_open = [j for j in schedule_jobs if j["scheduled_date"] == selected_day_plan["work_date"]]
    selected_day_plan["booked_bottles"] = sum(
        j["bottle_count"] for j in selected_day_open if j["job_type"] in ("SHOP_BATCH", "ROAD_PICKUP", "ROAD_ROUNDTRIP")
    )

    crew = [dict(r) for r in conn.execute("SELECT * FROM crew_members WHERE active=1 ORDER BY id;").fetchall()]
    targets = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM calibration_targets WHERE serial_number=? ORDER BY pressure_psi;",
            (cal_serial,),
        ).fetchall()
    ]
    verifications = [
        dict(r)
        for r in conn.execute("SELECT * FROM daily_verifications WHERE test_date=? ORDER BY id ASC;", (target_date,)).fetchall()
    ]

    jacket_ceilings = {}
    for j_name in ("Short Jacket", "Tall Jacket"):
        row = conn.execute(
            "SELECT COALESCE(MAX(target_pressure_psi), 0) AS max_cal FROM daily_verifications WHERE test_date=? AND jacket_type=? AND passed=1;",
            (target_date, j_name),
        ).fetchone()
        max_cal = int(row["max_cal"] or 0)
        jacket_ceilings[j_name] = {
            "max_calibrated_psi": max_cal,
            "max_allowed_test_psi": dot_max_allowed_test_psi(max_cal),
        }

    raw_records = []
    for r in conn.execute("SELECT * FROM cylinder_records WHERE work_date=? OR status='QUEUED' ORDER BY id ASC;", (target_date,)).fetchall():
        er = enrich_record_meta(dict(r), est_today)
        er["dot_row_note"] = _format_dot_row_notes(er)
        raw_records.append(er)
    queued_part = sorted([r for r in raw_records if r["status"] == "QUEUED"], key=queue_sort_key)
    done_part = [r for r in raw_records if r["status"] == "COMPLETED" and r["work_date"] == target_date]
    records = queued_part + done_part

    active_sheet_records = []
    for r in conn.execute(
        "SELECT * FROM cylinder_records WHERE sheet_no=? AND service_mode='HYDRO_AND_VISUAL' AND status='COMPLETED' ORDER BY id ASC;",
        (binder_state["active_sheet_no"],),
    ).fetchall():
        er = enrich_record_meta(dict(r), est_today)
        er["dot_row_note"] = _format_dot_row_notes(er)
        active_sheet_records.append(er)

    shop_tasks = [
        dict(r)
        for r in conn.execute(
            """SELECT * FROM crew_tasks
               WHERE status='OPEN' OR work_date=?
               ORDER BY CASE status WHEN 'OPEN' THEN 0 ELSE 1 END,
                        CASE priority WHEN 'HIGH' THEN 0 ELSE 1 END,
                        id DESC LIMIT 50;""",
            (target_date,),
        ).fetchall()
    ]
    open_inquiries_count = conn.execute("SELECT COUNT(*) FROM shop_messages WHERE channel='WEB_INQUIRY' AND status='OPEN';").fetchone()[0]
    latest_msg_id = conn.execute("SELECT COALESCE(MAX(id), 0) FROM shop_messages;").fetchone()[0]

    in_shop_post_test = [
        enrich_record_meta(dict(r), est_today)
        for r in conn.execute(
            "SELECT * FROM cylinder_records WHERE status='COMPLETED' AND shop_stage != 'PICKED_UP' ORDER BY work_date ASC, id ASC LIMIT 120;"
        ).fetchall()
    ]
    active_valves = [
        enrich_record_meta(dict(r), est_today)
        for r in conn.execute("SELECT * FROM cylinder_records WHERE valve_tag != '' AND valve_reinstalled = 0 ORDER BY id DESC;").fetchall()
    ]
    dropoffs = [
        dict(r)
        for r in conn.execute(
            """SELECT * FROM dropoffs
               WHERE status NOT IN ('ARCHIVED', 'DISPATCH_SCHEDULED')
                 AND (entry_type='PICKUP_NOTICE' OR COALESCE(imported_count, 0) < bottle_count)
               ORDER BY CASE WHEN status IN ('IN_BACKROOM', 'PICKUP_LOGGED') THEN 0 ELSE 1 END,
                        rush_requested DESC, id DESC LIMIT 40;"""
        ).fetchall()
    ]
    for d in dropoffs:
        d["dwell_days"] = days_since(d.get("work_date"), est_today)

    rolo_rows = conn.execute("SELECT customer_name, phone, tax_exempt, payment_terms, account_type FROM customers;").fetchall()
    rolo_map = {r["customer_name"].upper(): dict(r) for r in rolo_rows}

    customers_summary = [
        dict(r)
        for r in conn.execute(
            """SELECT customer_name,
                      MAX(customer_phone) AS customer_phone,
                      COUNT(*) AS total_bottles,
                      MAX(work_date) AS last_date,
                      MIN(CASE WHEN shop_stage='READY_BACKROOM' THEN work_date ELSE NULL END) AS oldest_shelf_date,
                      SUM(CASE WHEN status='COMPLETED' AND invoice_id IS NULL THEN 1 ELSE 0 END) AS unbilled_count,
                      SUM(CASE WHEN status='COMPLETED' AND hydro_disposition='A' AND COALESCE(rin_marked,0)=0 AND shop_stage!='PICKED_UP' THEN 1 ELSE 0 END) AS needs_stamp_count,
                      SUM(CASE WHEN shop_stage='READY_BACKROOM' THEN 1 ELSE 0 END) AS ready_backroom_count
               FROM cylinder_records
               GROUP BY customer_name
               ORDER BY CASE WHEN SUM(CASE WHEN status='COMPLETED' AND invoice_id IS NULL THEN 1 ELSE 0 END) > 0 THEN 0 ELSE 1 END,
                        last_date DESC, customer_name ASC;"""
        ).fetchall()
    ]
    for c in customers_summary:
        c["shelf_dwell_days"] = days_since(c.get("oldest_shelf_date"), est_today) if c.get("oldest_shelf_date") else 0
        rm = rolo_map.get(c["customer_name"].upper())
        if rm:
            if not c["customer_phone"] and rm["phone"]:
                c["customer_phone"] = rm["phone"]
            c["tax_exempt"] = bool(rm["tax_exempt"])
            c["payment_terms"] = rm["payment_terms"]
            c["account_type"] = rm["account_type"]
        else:
            c["tax_exempt"] = False
            c["payment_terms"] = "DUE_AT_PICKUP"
            c["account_type"] = "WALK_IN"

    max_queued_test_psi = max((int(r.get("test_psi") or 0) for r in queued_part), default=0)
    recommended_cal_psi = 0
    if max_queued_test_psi > 0:
        for t in targets:
            if dot_max_allowed_test_psi(int(t["pressure_psi"])) >= max_queued_test_psi:
                recommended_cal_psi = int(t["pressure_psi"])
                break
        if not recommended_cal_psi and targets:
            recommended_cal_psi = int(targets[-1]["pressure_psi"])

    low_supplies_rows = [dict(r) for r in conn.execute("SELECT id, item_name, on_hand_qty, reorder_threshold, unit_label FROM shop_supplies WHERE low_stock=1 ORDER BY sort_order ASC;").fetchall()]
    low_stock_count = len(low_supplies_rows)
    feedback_items = [dict(r) for r in conn.execute("SELECT * FROM crew_feedback ORDER BY id DESC LIMIT 30;").fetchall()]
    unsynced = conn.execute(
        "SELECT (SELECT COUNT(*) FROM daily_verifications WHERE synced=0) + "
        "(SELECT COUNT(*) FROM cylinder_records WHERE synced=0 AND status='COMPLETED') + "
        "(SELECT COUNT(*) FROM invoices WHERE synced=0) + "
        "(SELECT COUNT(*) FROM dropoffs WHERE synced=0) + "
        "(SELECT COUNT(*) FROM crew_feedback WHERE synced=0);"
    ).fetchone()[0]
    conn.close()

    return {
        "selected_date": target_date,
        "today": est_today,
        "db_version": int(s_map.get("db_version", "1")),
        "testing_mode": s_map.get("testing_mode", "1") == "1",
        "dot_rin": s_map.get("dot_rin", "B386"),
        "visual_rin": s_map.get("visual_rin", "V143159"),
        "shop_phone": s_map.get("phone", "724-266-8008"),
        "starting_sheet_no": int(s_map.get("starting_sheet_no", "8095")),
        "daily_hydro_capacity": default_cap,
        "default_tax_rate": float(s_map.get("default_tax_rate", "6.0") or 0),
        "rin_expires_date": s_map.get("rin_expires_date", ""),
        "gauge_cert_expires_date": s_map.get("gauge_cert_expires_date", ""),
        "compliance_alerts": compliance_alerts,
        "calibrated_serial": cal_serial,
        "has_gemini_key": has_key,
        "has_sheets_webhook": has_webhook,
        "sheets_webhook_url": s_map.get("sheets_webhook_url", ""),
        "feedback_webhook_url": s_map.get("feedback_webhook_url", ""),
        "alert_email": s_map.get("alert_email", ""),
        "smtp_sender_email": s_map.get("smtp_sender_email", ""),
        "has_smtp_password": bool(os.getenv("WEBER_SMTP_PASS") or s_map.get("smtp_app_password")),
        "binder_state": binder_state,
        "huddle": {
            "selected_day_plan": selected_day_plan,
            "max_queued_test_psi": max_queued_test_psi,
            "recommended_cal_psi": recommended_cal_psi,
            "rush_queued_count": sum(1 for r in queued_part if r.get("rush_fee") or r.get("priority") == "RUSH"),
            "needs_stamp_count": sum(1 for r in in_shop_post_test if r.get("hydro_disposition") == "A" and not r.get("rin_marked")),
            "ready_shelf_count": sum(1 for r in in_shop_post_test if r.get("shop_stage") == "READY_BACKROOM"),
        },
        "schedule_jobs": schedule_jobs,
        "crew": crew,
        "calibration_targets": targets,
        "verifications": verifications,
        "jacket_ceilings": jacket_ceilings,
        "records": records,
        "active_sheet_records": active_sheet_records,
        "shop_tasks": shop_tasks,
        "open_inquiries_count": open_inquiries_count,
        "latest_msg_id": latest_msg_id,
        "in_shop_post_test": in_shop_post_test,
        "active_valves": active_valves,
        "dropoffs": dropoffs,
        "customers_summary": customers_summary,
        "low_stock_count": low_stock_count,
        "low_supplies_alert": low_supplies_rows,
        "has_resend_key": bool(os.getenv("RESEND_API_KEY") or s_map.get("resend_api_key")),
        "resend_from_email": s_map.get("resend_from_email", "alerts@weberstanktesting.com"),
        "master_sheet_url": s_map.get("master_sheet_url", ""),
        "sms_shortcut_key": s_map.get("sms_shortcut_key", ""),
        "feedback_items": feedback_items,
        "unsynced_count": unsynced,
        "cal_draft": json.loads(s_map["live_cal_draft"]) if s_map.get("live_cal_draft") else None,
    }


@router.get("/api/bootstrap")
def bootstrap(work_date: Optional[str] = None, cal_year: Optional[int] = None, cal_month: Optional[int] = None):
    est_today = today_eastern()
    target_date = work_date or est_today
    conn = get_conn()
    s_map = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings;").fetchall()}
    cal_serial = s_map.get("calibrated_serial", "1SF11904")
    default_cap = int(s_map.get("daily_hydro_capacity") or 24)
    has_key = bool(os.getenv("GEMINI_API_KEY") or s_map.get("gemini_api_key"))
    has_webhook = bool(s_map.get("sheets_webhook_url"))

    binder_state = get_binder_status(conn, s_map)
    quote_preview = calculate_turnaround_quote(conn, 4)
    compliance_alerts = get_compliance_alerts(s_map, est_today)

    cal_anchor = parse_iso_date(target_date) or now_eastern().date()
    eff_year = cal_year if (cal_year and 2020 <= cal_year <= 2040) else cal_anchor.year
    eff_month = cal_month if (cal_month and 1 <= cal_month <= 12) else cal_anchor.month
    month_calendar = build_month_calendar_grid(conn, eff_year, eff_month, s_map)

    win_start = cal_anchor - timedelta(days=1)
    plans_rows = conn.execute(
        "SELECT * FROM shop_day_plans WHERE work_date >= ? AND work_date <= ?;",
        (win_start.isoformat(), (win_start + timedelta(days=20)).isoformat()),
    ).fetchall()
    plans_map = {r["work_date"]: dict(r) for r in plans_rows}

    schedule_jobs = [
        dict(r)
        for r in conn.execute(
            """SELECT * FROM schedule_jobs
               WHERE status != 'CANCELLED'
               ORDER BY CASE WHEN status='COMPLETED' THEN 1 ELSE 0 END,
                        CASE WHEN rush_fee=1 OR priority='RUSH' THEN 0 ELSE 1 END,
                        scheduled_date ASC, id ASC LIMIT 90;"""
        ).fetchall()
    ]
    for sj in schedule_jobs:
        sj["dwell_days"] = days_since(sj.get("created_at"), est_today)
        sj["holding_zone_label"] = HOLDING_ZONES.get(sj.get("holding_zone") or "", sj.get("holding_zone") or "")
        sj["maps_route_url"] = build_shop_maps_url(sj.get("location_notes") or "")

    day_plans = []
    for i in range(14):
        p = resolve_day_plan(win_start + timedelta(days=i), plans_map, default_cap)
        open_day = [j for j in schedule_jobs if j["status"] != "COMPLETED" and j["scheduled_date"] == p["work_date"]]
        p["booked_bottles"] = sum(j["bottle_count"] for j in open_day if j["job_type"] in ("SHOP_BATCH", "ROAD_PICKUP", "ROAD_ROUNDTRIP"))
        p["job_count"] = len(open_day)
        day_plans.append(p)
    selected_day_plan = resolve_day_plan(cal_anchor, plans_map, default_cap)
    selected_day_open = [j for j in schedule_jobs if j["status"] != "COMPLETED" and j["scheduled_date"] == selected_day_plan["work_date"]]
    selected_day_plan["booked_bottles"] = sum(j["bottle_count"] for j in selected_day_open if j["job_type"] in ("SHOP_BATCH", "ROAD_PICKUP", "ROAD_ROUNDTRIP"))

    crew = [dict(r) for r in conn.execute("SELECT * FROM crew_members WHERE active=1 ORDER BY id;").fetchall()]
    targets = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM calibration_targets WHERE serial_number=? ORDER BY pressure_psi;",
            (cal_serial,),
        ).fetchall()
    ]
    verifications = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM daily_verifications WHERE test_date=? ORDER BY id ASC;",
            (target_date,),
        ).fetchall()
    ]

    jacket_ceilings = {}
    for j_name in ("Short Jacket", "Tall Jacket"):
        row = conn.execute(
            """SELECT COALESCE(MAX(target_pressure_psi), 0) AS max_cal
               FROM daily_verifications
               WHERE test_date=? AND jacket_type=? AND passed=1;""",
            (target_date, j_name),
        ).fetchone()
        max_cal = int(row["max_cal"] or 0)
        jacket_ceilings[j_name] = {
            "max_calibrated_psi": max_cal,
            "max_allowed_test_psi": dot_max_allowed_test_psi(max_cal),
        }

    specs = [dict(r) for r in conn.execute("SELECT * FROM cga_specs ORDER BY id ASC;").fetchall()]

    raw_records = []
    for r in conn.execute(
        "SELECT * FROM cylinder_records WHERE work_date=? OR status='QUEUED' ORDER BY id ASC;",
        (target_date,),
    ).fetchall():
        er = enrich_record_meta(dict(r), est_today)
        er["dot_row_note"] = _format_dot_row_notes(er)
        raw_records.append(er)
    queued_part = sorted([r for r in raw_records if r["status"] == "QUEUED"], key=queue_sort_key)
    done_part = [r for r in raw_records if r["status"] == "COMPLETED" and r["work_date"] == target_date]
    records = queued_part + done_part

    active_sheet_records = []
    for r in conn.execute(
        "SELECT * FROM cylinder_records WHERE sheet_no=? AND service_mode='HYDRO_AND_VISUAL' AND status='COMPLETED' ORDER BY id ASC;",
        (binder_state["active_sheet_no"],),
    ).fetchall():
        er = enrich_record_meta(dict(r), est_today)
        er["dot_row_note"] = _format_dot_row_notes(er)
        active_sheet_records.append(er)

    shop_tasks = [
        dict(r)
        for r in conn.execute(
            """SELECT * FROM crew_tasks
               WHERE status='OPEN' OR work_date=?
               ORDER BY CASE status WHEN 'OPEN' THEN 0 ELSE 1 END,
                        CASE priority WHEN 'HIGH' THEN 0 ELSE 1 END,
                        id DESC LIMIT 50;""",
            (target_date,),
        ).fetchall()
    ]
    open_inquiries_count = conn.execute("SELECT COUNT(*) FROM shop_messages WHERE channel='WEB_INQUIRY' AND status='OPEN';").fetchone()[0]
    latest_msg_id = conn.execute("SELECT COALESCE(MAX(id), 0) FROM shop_messages;").fetchone()[0]
    site_content = {
        k: s_map.get(k, "")
        for k in (
            "site_banner_announcement",
            "site_about_eyebrow",
            "site_about_heading",
            "site_about_story",
            "site_business_details",
            "site_dropoff_note",
            "site_owner_signoff",
        )
    }

    in_shop_post_test = [
        enrich_record_meta(dict(r), est_today)
        for r in conn.execute(
            """SELECT * FROM cylinder_records
               WHERE status='COMPLETED' AND shop_stage != 'PICKED_UP'
               ORDER BY work_date ASC, id ASC LIMIT 120;"""
        ).fetchall()
    ]

    active_valves = [
        enrich_record_meta(dict(r), est_today)
        for r in conn.execute(
            "SELECT * FROM cylinder_records WHERE valve_tag != '' AND valve_reinstalled = 0 ORDER BY id DESC;"
        ).fetchall()
    ]

    dropoffs = [
        dict(r)
        for r in conn.execute(
            """SELECT * FROM dropoffs
               WHERE status NOT IN ('ARCHIVED', 'DISPATCH_SCHEDULED')
                 AND (entry_type='PICKUP_NOTICE' OR COALESCE(imported_count, 0) < bottle_count)
               ORDER BY CASE WHEN status IN ('IN_BACKROOM', 'PICKUP_LOGGED') THEN 0 ELSE 1 END,
                        rush_requested DESC, id DESC LIMIT 40;"""
        ).fetchall()
    ]
    for d in dropoffs:
        d["dwell_days"] = days_since(d.get("work_date"), est_today)

    dates_summary = [
        dict(r)
        for r in conn.execute(
            """SELECT work_date,
                      COUNT(*) AS total_bottles,
                      SUM(CASE WHEN status='COMPLETED' THEN 1 ELSE 0 END) AS completed_bottles,
                      COUNT(DISTINCT customer_name) AS customer_count
               FROM cylinder_records
               GROUP BY work_date
               ORDER BY work_date DESC LIMIT 60;"""
        ).fetchall()
    ]

    rolodex = [
        dict(r)
        for r in conn.execute("SELECT * FROM customers ORDER BY customer_name COLLATE NOCASE ASC;").fetchall()
    ]
    rolo_map = {r["customer_name"].upper(): r for r in rolodex}

    customers_summary = [
        dict(r)
        for r in conn.execute(
            """SELECT customer_name,
                      MAX(customer_phone) AS customer_phone,
                      COUNT(*) AS total_bottles,
                      MAX(work_date) AS last_date,
                      MIN(CASE WHEN shop_stage='READY_BACKROOM' THEN work_date ELSE NULL END) AS oldest_shelf_date,
                      SUM(CASE WHEN status='COMPLETED' AND invoice_id IS NULL THEN 1 ELSE 0 END) AS unbilled_count,
                      SUM(CASE WHEN status='COMPLETED' AND hydro_disposition='A' AND COALESCE(rin_marked,0)=0 AND shop_stage!='PICKED_UP' THEN 1 ELSE 0 END) AS needs_stamp_count,
                      SUM(CASE WHEN shop_stage='READY_BACKROOM' THEN 1 ELSE 0 END) AS ready_backroom_count
               FROM cylinder_records
               GROUP BY customer_name
               ORDER BY CASE WHEN SUM(CASE WHEN status='COMPLETED' AND invoice_id IS NULL THEN 1 ELSE 0 END) > 0 THEN 0 ELSE 1 END,
                        last_date DESC, customer_name ASC;"""
        ).fetchall()
    ]
    for c in customers_summary:
        c["shelf_dwell_days"] = days_since(c.get("oldest_shelf_date"), est_today) if c.get("oldest_shelf_date") else 0
        rm = rolo_map.get(c["customer_name"].upper())
        if rm:
            if not c["customer_phone"] and rm["phone"]:
                c["customer_phone"] = rm["phone"]
            c["tax_exempt"] = bool(rm["tax_exempt"])
            c["payment_terms"] = rm["payment_terms"]
            c["account_type"] = rm["account_type"]
        else:
            c["tax_exempt"] = False
            c["payment_terms"] = "DUE_AT_PICKUP"
            c["account_type"] = "WALK_IN"

    max_queued_test_psi = max((int(r.get("test_psi") or 0) for r in queued_part), default=0)
    recommended_cal_psi = 0
    if max_queued_test_psi > 0:
        for t in targets:
            if dot_max_allowed_test_psi(int(t["pressure_psi"])) >= max_queued_test_psi:
                recommended_cal_psi = int(t["pressure_psi"])
                break
        if not recommended_cal_psi and targets:
            recommended_cal_psi = int(targets[-1]["pressure_psi"])

    supplies = [dict(r) for r in conn.execute("SELECT * FROM shop_supplies ORDER BY low_stock DESC, sort_order ASC, id ASC;").fetchall()]
    op_sched_bundle = get_operating_schedule_bundle(conn)
    recalls = compute_recall_list(conn, est_today)
    rates = [dict(r) for r in conn.execute("SELECT * FROM pricing_rates ORDER BY sort_order ASC, code ASC;").fetchall()]
    invoices = [dict(r) for r in conn.execute("SELECT * FROM invoices ORDER BY id DESC LIMIT 60;").fetchall()]
    feedback_items = [dict(r) for r in conn.execute("SELECT * FROM crew_feedback ORDER BY id DESC LIMIT 40;").fetchall()]
    unsynced = conn.execute(
        "SELECT (SELECT COUNT(*) FROM daily_verifications WHERE synced=0) + "
        "(SELECT COUNT(*) FROM cylinder_records WHERE synced=0 AND status='COMPLETED') + "
        "(SELECT COUNT(*) FROM invoices WHERE synced=0) + "
        "(SELECT COUNT(*) FROM dropoffs WHERE synced=0) + "
        "(SELECT COUNT(*) FROM crew_feedback WHERE synced=0);"
    ).fetchone()[0]
    conn.close()

    return {
        "selected_date": target_date,
        "today": est_today,
        "db_version": int(s_map.get("db_version", "1")),
        "testing_mode": s_map.get("testing_mode", "1") == "1",
        "dot_rin": s_map.get("dot_rin", "B386"),
        "visual_rin": s_map.get("visual_rin", "V143159"),
        "shop_phone": s_map.get("phone", "724-266-8008"),
        "starting_sheet_no": int(s_map.get("starting_sheet_no", "8095")),
        "daily_hydro_capacity": default_cap,
        "default_tax_rate": float(s_map.get("default_tax_rate", "6.0") or 0),
        "rin_expires_date": s_map.get("rin_expires_date", ""),
        "gauge_cert_expires_date": s_map.get("gauge_cert_expires_date", ""),
        "compliance_alerts": compliance_alerts,
        "calibrated_serial": cal_serial,
        "has_gemini_key": has_key,
        "has_sheets_webhook": has_webhook,
        "sheets_webhook_url": s_map.get("sheets_webhook_url", ""),
        "feedback_webhook_url": s_map.get("feedback_webhook_url", ""),
        "alert_email": s_map.get("alert_email", ""),
        "smtp_sender_email": s_map.get("smtp_sender_email", ""),
        "has_smtp_password": bool(os.getenv("WEBER_SMTP_PASS") or s_map.get("smtp_app_password")),
        "binder_state": binder_state,
        "huddle": {
            "selected_day_plan": selected_day_plan,
            "max_queued_test_psi": max_queued_test_psi,
            "recommended_cal_psi": recommended_cal_psi,
            "rush_queued_count": sum(1 for r in queued_part if r.get("rush_fee") or r.get("priority") == "RUSH"),
            "needs_stamp_count": sum(1 for r in in_shop_post_test if r.get("hydro_disposition") == "A" and not r.get("rin_marked")),
            "ready_shelf_count": sum(1 for r in in_shop_post_test if r.get("shop_stage") == "READY_BACKROOM"),
            "quote_preview": quote_preview,
        },
        "shift_modes": SHIFT_MODES,
        "holding_zones": HOLDING_ZONES,
        "month_calendar": month_calendar,
        "day_plans": day_plans,
        "schedule_jobs": schedule_jobs,
        "crew": crew,
        "calibration_targets": targets,
        "verifications": verifications,
        "jacket_ceilings": jacket_ceilings,
        "cga_specs": specs,
        "records": records,
        "active_sheet_records": active_sheet_records,
        "shop_tasks": shop_tasks,
        "open_inquiries_count": open_inquiries_count,
        "latest_msg_id": latest_msg_id,
        "site_content": site_content,
        "in_shop_post_test": in_shop_post_test,
        "active_valves": active_valves,
        "dropoffs": dropoffs,
        "dates_summary": dates_summary,
        "customers_summary": customers_summary,
        "customers_rolodex": rolodex,
        "recalls": recalls,
        "shop_supplies": supplies,
        "low_stock_count": sum(1 for s in supplies if s.get("low_stock")),
        "low_supplies_alert": [s for s in supplies if s.get("low_stock")],
        "operating_schedule": op_sched_bundle,
        "has_resend_key": bool(os.getenv("RESEND_API_KEY") or s_map.get("resend_api_key")),
        "resend_from_email": s_map.get("resend_from_email", "alerts@weberstanktesting.com"),
        "master_sheet_url": s_map.get("master_sheet_url", ""),
        "sms_shortcut_key": s_map.get("sms_shortcut_key", ""),
        "pricing_rates": rates,
        "invoices": invoices,
        "feedback_items": feedback_items,
        "unsynced_count": unsynced,
        "cal_draft": json.loads(s_map["live_cal_draft"]) if s_map.get("live_cal_draft") else None,
    }


@router.post("/api/ops/cal-draft")
def save_live_calibration_draft(payload: CalibrationDraftInput, request: Request):
    sess_u = getattr(request.state, "user", None) or get_current_session_user(request)
    if not sess_u:
        raise HTTPException(401, "Sign in first.")
    t_date = payload.test_date or today_eastern()
    clean_vals = {str(k): str(v)[:16] for k, v in (payload.values or {}).items() if str(v).strip() != ""}
    draft_obj = {
        "test_date": t_date,
        "jacket_type": payload.jacket_type,
        "max_target_psi": int(payload.max_target_psi or 4000),
        "values": clean_vals,
        "permanent_cc": str(payload.permanent_cc or "0")[:16],
        "device_id": str(payload.device_id or "")[:40],
        "updated_at": now_eastern().isoformat(),
    }
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES ('live_cal_draft', ?);",
        (json.dumps(draft_obj),),
    )
    ver = bump_db_version(conn)
    conn.commit()
    conn.close()
    return {"saved": True, "db_version": ver, "cal_draft": draft_obj}


@router.post("/api/verify-ladder")
def log_verification_ladder(payload: VerificationLadderInput, request: Request):
    sess_u = getattr(request.state, "user", None) or get_current_session_user(request)
    if not sess_u or sess_u["role"] not in ("OWNER", "SYSTEM_ADMIN"):
        raise HTTPException(403, "Only Ryan Pearce (Owner) is authorized to certify water-jacket calibrations.")
    is_adm = sess_u["role"] == "SYSTEM_ADMIN"
    est = now_eastern()
    t_date = payload.test_date or est.date().isoformat()
    t_time = est.strftime("%I:%M %p")
    run_id = f"{t_date}-{payload.jacket_type[:5]}-{est.strftime('%H%M%S')}"
    logged_by = "EP-ADMIN" if is_adm else "RP"
    op_name = "ADMIN DIAGNOSTIC (NO DOT SIGN-OFF)" if is_adm else "Ryan Pearce"
    op_init = "ADMIN" if is_adm else "RP"

    if not payload.steps:
        raise HTTPException(400, "Enter at least one burette calibration reading before saving.")

    conn = get_conn()
    cal_serial = conn.execute("SELECT value FROM settings WHERE key='calibrated_serial';").fetchone()["value"]
    target_rows = conn.execute(
        "SELECT pressure_psi, expected_cc FROM calibration_targets WHERE serial_number=? ORDER BY pressure_psi ASC;",
        (cal_serial,),
    ).fetchall()
    target_map = {int(r["pressure_psi"]): float(r["expected_cc"]) for r in target_rows}

    submitted_sorted = sorted(payload.steps, key=lambda s: int(s.pressure_psi))
    evaluated = []
    all_within_one_pct = True
    max_expected_cc = 0.0
    eff_max_psi = 0

    for idx, step in enumerate(submitted_sorted):
        exp_psi = int(step.pressure_psi)
        if exp_psi <= 0 or step.actual_total_cc <= 0:
            continue
        expected_a = target_map.get(exp_psi)
        if expected_a is None:
            # Interpolate/fallback cleanly if custom PSI
            expected_a = float(step.actual_total_cc)
        actual_b = float(step.actual_total_cc)
        pct_dev = round(((expected_a - actual_b) / expected_a) * 100.0, 2) if expected_a else 0.0
        step_ok = (abs(pct_dev) <= 1.0) or (abs(expected_a - actual_b) <= 0.15)
        if not step_ok:
            all_within_one_pct = False
        max_expected_cc = max(max_expected_cc, expected_a)
        eff_max_psi = max(eff_max_psi, exp_psi)
        is_last = idx == (len(submitted_sorted) - 1)
        perm = payload.permanent_cc if is_last else 0.0
        elastic = round(actual_b - perm, 2)
        evaluated.append((exp_psi, expected_a, actual_b, perm, elastic, pct_dev, step_ok))

    if not evaluated:
        conn.close()
        raise HTTPException(400, "Enter at least one positive burette reading (cc) before saving.")

    perm_limit = max(0.15, round(max_expected_cc * 0.01, 2))
    perm_ok = abs(payload.permanent_cc) <= perm_limit
    run_passed = 1 if ((all_within_one_pct and perm_ok) or payload.force_pass) else 0
    cur = conn.cursor()
    for exp_psi, expected_a, actual_b, perm, elastic, pct_dev, step_ok in evaluated:
        step_passed = 1 if (run_passed and (step_ok or payload.force_pass)) else 0
        cur.execute(
            """INSERT INTO daily_verifications
               (run_id, test_date, test_time, jacket_type, calibrated_serial, max_target_psi,
                target_pressure_psi, expected_cc, actual_total_cc, permanent_cc,
                elastic_cc, percent_deviation, passed, logged_by, operator_name, operator_initials)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);""",
            (
                run_id, t_date, t_time, payload.jacket_type, cal_serial,
                eff_max_psi, exp_psi, expected_a, actual_b,
                perm, elastic, pct_dev, step_passed, logged_by, op_name, op_init,
            ),
        )
    conn.execute("DELETE FROM settings WHERE key='live_cal_draft';")
    bump_db_version(conn)
    conn.commit()
    conn.close()
    trigger_sheets_sync_bg()
    return {
        "passed": bool(run_passed),
        "jacket_type": payload.jacket_type,
        "steps_saved": len(evaluated),
        "max_calibrated_psi": eff_max_psi if run_passed else 0,
        "max_allowed_test_psi": dot_max_allowed_test_psi(eff_max_psi) if run_passed else 0,
    }


@router.delete("/api/verifications/run/{run_id}")
def delete_verification_run(run_id: str, request: Request):
    require_owner_or_admin(request)
    conn = get_conn()
    cur = conn.execute("DELETE FROM daily_verifications WHERE run_id=?;", (run_id,))
    deleted = cur.rowcount
    bump_db_version(conn)
    conn.commit()
    conn.close()
    return {"deleted": deleted, "run_id": run_id}


@router.post("/api/intake")
def add_intake(payload: IntakeInput):
    return insert_cylinder_record(payload)


@router.post("/api/records/{record_id}/priority")
def update_record_priority(record_id: int, payload: RecordPriorityInput):
    conn = get_conn()
    rec = conn.execute("SELECT * FROM cylinder_records WHERE id=?;", (record_id,)).fetchone()
    if not rec:
        conn.close()
        raise HTTPException(404, "Cylinder record not found.")
    pri = payload.priority.strip().upper() if payload.priority is not None else (rec["priority"] or "STANDARD")
    rfee = (1 if payload.rush_fee else 0) if payload.rush_fee is not None else int(rec["rush_fee"] or 0)
    if pri == "RUSH":
        rfee = 1
    elif payload.priority is not None and pri != "RUSH" and payload.rush_fee is None:
        rfee = 0
    prom = payload.promised_date.strip() if payload.promised_date is not None else (rec["promised_date"] or "")
    zone = payload.holding_zone.strip() if payload.holding_zone is not None else (rec["holding_zone"] or "ZONE_C_READY_RACK")
    conn.execute(
        "UPDATE cylinder_records SET priority=?, rush_fee=?, promised_date=?, holding_zone=?, synced=0 WHERE id=?;",
        (pri, rfee, prom, zone, record_id),
    )
    bump_db_version(conn)
    conn.commit()
    conn.close()
    return {"updated": record_id, "priority": pri, "rush_fee": bool(rfee)}


@router.post("/api/records/{record_id}/addons")
def update_record_addons(record_id: int, payload: RecordAddonsInput):
    """1-Tap Revalve Add-On Parts (Neck O-Ring, Burst Disc, Internal Clean) & Condemned Action."""
    conn = get_conn()
    rec = conn.execute("SELECT * FROM cylinder_records WHERE id=?;", (record_id,)).fetchone()
    if not rec:
        conn.close()
        raise HTTPException(404, "Cylinder record not found.")

    oring = (1 if payload.addon_oring else 0) if payload.addon_oring is not None else int(rec["addon_oring"] or 0)
    bdisc = (1 if payload.addon_burst_disc else 0) if payload.addon_burst_disc is not None else int(rec["addon_burst_disc"] or 0)
    clean = (1 if payload.addon_clean else 0) if payload.addon_clean is not None else int(rec["addon_clean"] or 0)
    c_act = payload.condemn_action.strip().upper() if payload.condemn_action is not None else (rec["condemn_action"] or "")
    rel_chg = "Yes" if bdisc else (rec["relief_valve_changed"] or "No")

    conn.execute(
        """UPDATE cylinder_records
           SET addon_oring=?, addon_burst_disc=?, addon_clean=?, condemn_action=?, relief_valve_changed=?, synced=0
           WHERE id=?;""",
        (oring, bdisc, clean, c_act, rel_chg, record_id),
    )
    bump_db_version(conn)
    conn.commit()
    conn.close()
    return {
        "updated": record_id,
        "addon_oring": bool(oring),
        "addon_burst_disc": bool(bdisc),
        "addon_clean": bool(clean),
        "condemn_action": c_act,
    }


@router.post("/api/valve-tag")
def assign_or_update_valve_tag(payload: ValveTagAssignInput):
    conn = get_conn()
    rec = None
    if payload.record_id is not None:
        rec = conn.execute("SELECT * FROM cylinder_records WHERE id=?;", (payload.record_id,)).fetchone()
    elif payload.serial_number:
        sn = payload.serial_number.strip().upper()
        if payload.work_date:
            rec = conn.execute(
                "SELECT * FROM cylinder_records WHERE UPPER(serial_number)=? AND work_date=? ORDER BY id DESC LIMIT 1;",
                (sn, payload.work_date),
            ).fetchone()
        if not rec:
            rec = conn.execute(
                "SELECT * FROM cylinder_records WHERE UPPER(serial_number)=? ORDER BY id DESC LIMIT 1;",
                (sn,),
            ).fetchone()

    if not rec:
        conn.close()
        raise HTTPException(404, "Cylinder serial number not found. Scan or log the bottle in first.")

    new_tag = rec["valve_tag"] if payload.valve_tag is None else payload.valve_tag.strip().lstrip("#")
    new_reinst = (
        int(rec["valve_reinstalled"] or 0)
        if payload.valve_reinstalled is None
        else (1 if payload.valve_reinstalled else 0)
    )

    if new_tag and not new_reinst:
        dup = check_duplicate_active_valve_tag(conn, new_tag, exclude_id=rec["id"])
        if dup:
            conn.close()
            raise HTTPException(
                409,
                f"Valve Tag #{new_tag} is currently active on SN {dup['serial_number']} ({dup['customer_name']}).",
            )

    conn.execute(
        "UPDATE cylinder_records SET valve_tag=?, valve_reinstalled=?, synced=0 WHERE id=?;",
        (new_tag, new_reinst, rec["id"]),
    )
    bump_db_version(conn)
    conn.commit()
    updated = dict(conn.execute("SELECT * FROM cylinder_records WHERE id=?;", (rec["id"],)).fetchone())
    conn.close()
    if updated["status"] == "COMPLETED":
        trigger_sheets_sync_bg()
    return updated


@router.post("/api/records/{record_id}/stage")
def update_record_stage(record_id: int, payload: ShopStageInput):
    stage = payload.shop_stage.strip().upper()
    conn = get_conn()
    rec = conn.execute("SELECT * FROM cylinder_records WHERE id=?;", (record_id,)).fetchone()
    if not rec:
        conn.close()
        raise HTTPException(404, "Cylinder record not found.")

    rin_marked = int(rec["rin_marked"] or 0)
    rin_type = rec["rin_mark_type"] or ""
    if payload.rin_marked is not None:
        rin_marked = 1 if payload.rin_marked else 0
    if payload.rin_mark_type is not None:
        rin_type = payload.rin_mark_type.strip().upper()
        if rin_type:
            rin_marked = 1
    if stage == "STAMPED_STICKERED":
        rin_marked = 1
        if not rin_type:
            is_comp = (rec["dot_spec"] or "").upper().startswith("SP")
            rin_type = "STICKERED" if is_comp else "STAMPED"

    if stage == "READY_BACKROOM" and rec["hydro_disposition"] == "A" and not rin_marked and not payload.force:
        conn.close()
        raise HTTPException(
            400,
            f"Confirm SN {rec['serial_number']} has been Stamped or B386 Stickered before moving to the Pickup Shelf.",
        )

    reinst = int(rec["valve_reinstalled"] or 0)
    if payload.valve_reinstalled is not None:
        reinst = 1 if payload.valve_reinstalled else 0
    elif stage in ("READY_BACKROOM", "PICKED_UP"):
        reinst = 1

    zone = payload.holding_zone or (
        "ZONE_E_PICKUP_SHELF" if stage == "READY_BACKROOM"
        else ("PICKED_UP" if stage == "PICKED_UP" else "ZONE_D_DRY_STAMP")
    )
    p_by = (payload.picked_up_by or rec["picked_up_by"] or "").strip()[:60]
    p_at = now_eastern().strftime("%Y-%m-%d %I:%M %p") if stage == "PICKED_UP" else (rec["picked_up_at"] or "")

    conn.execute(
        """UPDATE cylinder_records
           SET shop_stage=?, rin_marked=?, rin_mark_type=?, valve_reinstalled=?,
               holding_zone=?, picked_up_by=?, picked_up_at=?, synced=0
           WHERE id=?;""",
        (stage, rin_marked, rin_type, reinst, zone, p_by, p_at, record_id),
    )
    bump_db_version(conn)
    conn.commit()
    conn.close()
    return {"id": record_id, "shop_stage": stage, "rin_marked": bool(rin_marked), "rin_mark_type": rin_type}


@router.post("/api/batch/stage")
def update_batch_stage(payload: BatchStageInput):
    stage = payload.shop_stage.strip().upper()
    conn = get_conn()

    where_sql = "WHERE customer_name=? AND status='COMPLETED' AND shop_stage != 'PICKED_UP'"
    params: list = [payload.customer_name]
    if payload.work_date:
        where_sql = "WHERE work_date=? AND customer_name=? AND status='COMPLETED'"
        params = [payload.work_date, payload.customer_name]

    if stage == "STAMPED_STICKERED":
        mtype = (payload.rin_mark_type or "MARKED").strip().upper()
        conn.execute(
            f"UPDATE cylinder_records SET shop_stage='STAMPED_STICKERED', rin_marked=1, rin_mark_type=?, holding_zone='ZONE_D_DRY_STAMP', synced=0 {where_sql} AND hydro_disposition='A';",
            tuple([mtype] + params),
        )
    elif stage == "READY_BACKROOM":
        if payload.rin_marked:
            mtype = (payload.rin_mark_type or "MARKED").strip().upper()
            conn.execute(
                f"UPDATE cylinder_records SET shop_stage='READY_BACKROOM', rin_marked=1, rin_mark_type=COALESCE(NULLIF(rin_mark_type,''), ?), valve_reinstalled=1, holding_zone='ZONE_E_PICKUP_SHELF', synced=0 {where_sql};",
                tuple([mtype] + params),
            )
        else:
            unmarked = conn.execute(
                f"SELECT COUNT(*) FROM cylinder_records {where_sql} AND hydro_disposition='A' AND COALESCE(rin_marked,0)=0;",
                tuple(params),
            ).fetchone()[0]
            if unmarked > 0:
                conn.close()
                raise HTTPException(
                    400,
                    f"{unmarked} bottle(s) for {payload.customer_name} are not marked Stamped/Stickered yet.",
                )
            conn.execute(
                f"UPDATE cylinder_records SET shop_stage='READY_BACKROOM', valve_reinstalled=1, holding_zone='ZONE_E_PICKUP_SHELF', synced=0 {where_sql};",
                tuple(params),
            )
    elif stage == "PICKED_UP":
        who = (payload.picked_up_by or payload.customer_name).strip()[:60]
        ts = now_eastern().strftime("%Y-%m-%d %I:%M %p")
        cutoff_date = payload.work_date or today_eastern()
        conn.execute(
            f"UPDATE cylinder_records SET shop_stage='PICKED_UP', valve_reinstalled=1, holding_zone='PICKED_UP', picked_up_by=?, picked_up_at=?, synced=0 {where_sql};",
            tuple([who, ts] + params),
        )
        conn.execute(
            "UPDATE dropoffs SET status='ARCHIVED', synced=0 WHERE customer_name=? AND work_date <= ? AND status!='ARCHIVED';",
            (payload.customer_name, cutoff_date),
        )
        conn.execute(
            "UPDATE schedule_jobs SET status='COMPLETED', synced=0 WHERE customer_name=? AND scheduled_date <= ? AND status!='COMPLETED';",
            (payload.customer_name, cutoff_date),
        )
    else:
        conn.execute(
            f"UPDATE cylinder_records SET shop_stage=?, synced=0 {where_sql};",
            tuple([stage] + params),
        )

    bump_db_version(conn)
    conn.commit()
    conn.close()
    return {"updated": True, "shop_stage": stage}


@router.delete("/api/records/{record_id}")
def delete_cylinder_record(record_id: int):
    conn = get_conn()
    rec = conn.execute("SELECT id, invoice_id FROM cylinder_records WHERE id=?;", (record_id,)).fetchone()
    if not rec:
        conn.close()
        raise HTTPException(404, "Cylinder record not found.")
    if rec["invoice_id"]:
        inv = conn.execute("SELECT invoice_no, status FROM invoices WHERE id=?;", (rec["invoice_id"],)).fetchone()
        if inv and inv["status"] == "PAID":
            conn.close()
            raise HTTPException(400, f"Cannot delete record tied to paid invoice {inv['invoice_no']}.")
        conn.execute("DELETE FROM invoice_items WHERE record_id=?;", (record_id,))
    conn.execute("DELETE FROM cylinder_records WHERE id=?;", (record_id,))
    bump_db_version(conn)
    conn.commit()
    conn.close()
    return {"deleted": record_id}


@router.post("/api/scan-bottle")
async def scan_and_import_bottle(payload: ScanBottleJsonInput):
    conn = get_conn()
    saved_key = conn.execute("SELECT value FROM settings WHERE key='gemini_api_key';").fetchone()["value"]
    conn.close()

    api_key = (os.getenv("GEMINI_API_KEY") or saved_key or "").strip()
    if not api_key:
        raise HTTPException(503, "Gemini API Key not set. Open 'Settings' and paste your Gemini API key.")

    prompt = (
        "Examine ALL provided photos (1 to 6 angles of the SAME compressed gas or paintball HPA cylinder) and stitch wraparound stamps/labels across overlapping angles:\n"
        "- MULTI-ANGLE STITCHING: Serial numbers (e.g. 'GTS-0291277'), DOT specs, and owner stencils curve around the neck or gel-coat label. Align overlapping characters across photos so no prefix or trailing digits are dropped.\n"
        "- serial_number: Complete cylinder serial number without spaces (e.g. 'GTS-0291277', 'W370277'). Ignore 4-digit 2x2 RIN retest grids.\n"
        "- valve_tag: If a numbered brass, plastic, or paper shop tag is hanging on or near the valve/regulator/neck, return its number only (e.g. '14'), else null.\n"
        "- dot_spec: Spec code only without pressure (e.g. 'ICC3A1800'->'3A', 'DOT-3AA2015'->'3AA', '3AL3000'->'3AL', 'DOT-SP14387-4500'->'SP14387', 'DOT-SP10915-4500'->'SP10915').\n"
        "- service_psi: Integer service pressure (e.g. 1800, 2015, 2216, 2250, 2400, 3000, 3442, 4500).\n"
        "- mfg_id: Manufacturer mark/name (e.g. 'LUXFER', 'WK', 'NINJA', 'HK ARMY', 'EMPIRE', 'FIRST STRIKE', 'M4625', 'PST', 'TW').\n"
        "- mfg_date: Earliest original manufacture date M-YY or MM-YY near the DOT/mfg stamp or composite label (ignore newer retest dates accompanied by 4-character RINs).\n"
        "- gas_service: Short gas code ONLY ('AIR', 'O2', 'CO2', 'N2', 'AR', 'HE', 'AR/CO2'). NEVER output UN hazmat numbers like 'UN1956'; map hazmat labels or HPA paintball tanks to 'AIR'.\n"
        "- detected_customer: Owner, fire department, or gas distributor name on neck ring, stencil, or collar label (e.g. 'GTS INC', 'CRESCENT TWP FD'), else null (do NOT put paintball tank brand names like Ninja/HK Army as customer).\n"
        "- outside_dia: Cylinder outside diameter in inches as string. Read from label if printed; otherwise estimate visually (stubby carbon paintball 68ci/4500 HPA is ~'4.5', 48ci/3000 alum HPA is ~'3.5', firefighter SCBA is ~'6.8' or '7.3', tall industrial steel is '7' or '9').\n"
        "- length_in: Cylinder body length (excluding valve/regulator) in inches as string. Read from label if printed; otherwise estimate visually (paintball 68ci HPA is ~'9.3', firefighter SCBA is ~'21.5'–'23', industrial steel is '32 1/2', '43', or '51').\n"
        "- size_source: 'LABEL' if OD/Length were explicitly printed on cylinder, else 'ESTIMATED'.\n"
        "- ree_limit: Float REE (Rejection Elastic Expansion in cc) ONLY if 'REE' is explicitly printed on a composite/Special Permit cylinder label; ALWAYS return null for standard steel (3A/3AA) and aluminum (3AL) cylinders."
    )

    try:
        extracted = await asyncio.wait_for(
            asyncio.to_thread(call_gemini_vision_rest, api_key, payload.images_b64[:6], prompt),
            timeout=70.0,
        )
    except Exception as exc:
        raise HTTPException(502, f"AI Scan Error: {exc}")

    w_date = payload.work_date or today_eastern()
    dot_spec = (extracted.get("dot_spec") or "").strip().upper().replace("SP-", "SP")
    svc_psi = int(extracted["service_psi"]) if extracted.get("service_psi") else 0
    serial_clean = (extracted.get("serial_number") or "").replace(" ", "").strip().upper()
    scanned_vtag = (payload.valve_tag or extracted.get("valve_tag") or "").strip().lstrip("#")
    final_customer = payload.customer_name.strip() or (extracted.get("detected_customer") or "").strip()
    gas_svc = normalize_gas_service(extracted.get("gas_service"), fallback=normalize_gas_service(payload.gas_service))
    mfg_date_clean = (extracted.get("mfg_date") or "").strip()

    conn = get_conn()
    cga_match = lookup_spec_defaults(conn, dot_spec, svc_psi) if (dot_spec and svc_psi) else None
    dup_sn = check_duplicate_serial_queued(conn, serial_clean, w_date) if serial_clean else None
    conn.close()

    dia = payload.outside_dia.strip() or (extracted.get("outside_dia") or "").strip()
    length = payload.length_in.strip() or (extracted.get("length_in") or "").strip()
    size_source = extracted.get("size_source") or "ESTIMATED"

    if cga_match and cga_match.get("exact_size"):
        if not dia or size_source != "LABEL":
            dia = dia or cga_match["outside_dia"]
            if size_source != "LABEL":
                size_source = "CGA_CHART"
        if not length or size_source != "LABEL":
            length = length or cga_match["length_in"]

    test_psi = int(cga_match["test_psi"]) if cga_match else (round((svc_psi * 5) / 3) if svc_psi else 0)
    ree_val = extracted.get("ree_limit")
    if dot_spec in ("3A", "3AA", "3AL"):
        ree_val = None
    elif ree_val is None and cga_match:
        ree_val = cga_match["ree_limit"]

    comp_life = evaluate_composite_life(dot_spec, mfg_date_clean, w_date)

    form_fill = {
        "customer_name": final_customer,
        "serial_number": serial_clean,
        "valve_tag": scanned_vtag,
        "mfg_id": (extracted.get("mfg_id") or "").strip().upper(),
        "mfg_date": mfg_date_clean,
        "gas_service": gas_svc,
        "dot_spec": dot_spec,
        "service_psi": svc_psi or "",
        "test_psi": test_psi or "",
        "outside_dia": dia,
        "length_in": length,
        "size_source": size_source,
        "ree_limit": ree_val if ree_val is not None else "",
    }

    missing = []
    if not final_customer:
        missing.append("Customer Name")
    if not serial_clean:
        missing.append("Serial Number")
    if not dot_spec:
        missing.append("DOT Spec")
    if not svc_psi:
        missing.append("Service Pressure")

    duplicate_warning = None
    if dup_sn:
        duplicate_warning = f"Serial #{serial_clean} is already logged ({dup_sn['status']} for {dup_sn['customer_name']})."

    if payload.auto_import and not missing and not duplicate_warning and not comp_life.get("expired"):
        rec = insert_cylinder_record(
            IntakeInput(
                work_date=w_date,
                customer_name=final_customer,
                customer_phone=payload.customer_phone,
                dropoff_id=payload.dropoff_id,
                schedule_job_id=payload.schedule_job_id,
                priority=payload.priority,
                rush_fee=payload.rush_fee,
                promised_date=payload.promised_date,
                serial_number=serial_clean,
                valve_tag=scanned_vtag,
                mfg_id=form_fill["mfg_id"],
                mfg_date=mfg_date_clean,
                gas_service=gas_svc,
                outside_dia=dia,
                length_in=length,
                dot_spec=dot_spec,
                service_psi=svc_psi,
                test_psi=test_psi,
                ree_limit=ree_val,
                service_mode=payload.service_mode,
                intake_by=payload.intake_by,
            )
        )
        return {
            "imported": True,
            "record": rec,
            "form_fill": form_fill,
            "duplicate_warning": duplicate_warning,
            "composite_life": comp_life,
        }
    return {
        "imported": False,
        "form_fill": form_fill,
        "missing": missing,
        "duplicate_warning": duplicate_warning,
        "composite_life": comp_life,
    }


@router.post("/api/records/{record_id}/complete")
def complete_record(record_id: int, payload: TestCompleteInput, request: Request):
    sess_u = getattr(request.state, "user", None) or get_current_session_user(request)
    is_adm = bool(sess_u and sess_u["role"] == "SYSTEM_ADMIN")
    conn = get_conn()
    rec = conn.execute("SELECT * FROM cylinder_records WHERE id=?;", (record_id,)).fetchone()
    if not rec:
        conn.close()
        raise HTTPException(404, "Cylinder record not found.")

    eff_test_date = payload.test_date or today_eastern()
    final_vtag = rec["valve_tag"] if payload.valve_tag is None else payload.valve_tag.strip().lstrip("#")
    final_reinst = 1 if payload.valve_reinstalled else int(rec["valve_reinstalled"] or 0)
    if final_vtag and not final_reinst:
        dup = check_duplicate_active_valve_tag(conn, final_vtag, exclude_id=record_id)
        if dup:
            conn.close()
            raise HTTPException(
                409,
                f"Valve Tag #{final_vtag} is currently active on SN {dup['serial_number']} ({dup['customer_name']}).",
            )

    checks = [
        payload.chk_corrosion, payload.chk_dents_dings, payload.chk_cuts_gouges,
        payload.chk_leaks, payload.chk_fire_damage, payload.chk_bulges, payload.chk_neck_defects,
    ]
    vis_pass = 1 if all(c == "OK" for c in checks) else 0
    vis_disp = "OK" if vis_pass else "SC"

    ver_id = None
    elastic = None
    pct_perm = None
    hydro_disp = "A" if vis_pass else "D"
    assigned_sheet_no = int(rec["sheet_no"] or 0)

    if rec["service_mode"] == "HYDRO_AND_VISUAL":
        ver_row = conn.execute(
            """SELECT id, target_pressure_psi FROM daily_verifications
               WHERE test_date=? AND jacket_type=? AND passed=1
               ORDER BY target_pressure_psi DESC, id DESC LIMIT 1;""",
            (eff_test_date, payload.jacket_type),
        ).fetchone()
        if not ver_row:
            conn.close()
            raise HTTPException(400, f"Calibrate {payload.jacket_type} for {eff_test_date} first.")
        allowed_max = dot_max_allowed_test_psi(int(ver_row["target_pressure_psi"]))
        if int(rec["test_psi"]) > allowed_max:
            conn.close()
            raise HTTPException(
                400,
                f"{payload.jacket_type} calibrated to {ver_row['target_pressure_psi']} PSI (covers <= {allowed_max} PSI). Switch jacket or calibrate higher for {rec['test_psi']} PSI.",
            )
        ver_id = ver_row["id"]
        if payload.total_cc is None or payload.permanent_cc is None or payload.total_cc <= 0:
            conn.close()
            raise HTTPException(400, "Enter Burette Total and Permanent expansion readings.")

        elastic = round(payload.total_cc - payload.permanent_cc, 2)
        pct_perm = round((payload.permanent_cc / payload.total_cc) * 100.0, 1)
        eff_ree = None if (rec["dot_spec"] in ("3A", "3AA", "3AL") and rec["ree_limit"] == 7.2) else rec["ree_limit"]
        ree_failed = eff_ree is not None and elastic > float(eff_ree)
        perm_failed = pct_perm > payload.max_percent_perm
        if not vis_pass or ree_failed or perm_failed:
            hydro_disp = "D"

        if rec["status"] != "COMPLETED" or assigned_sheet_no <= 0:
            s_map = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings;").fetchall()}
            binder = get_binder_status(conn, s_map)
            assigned_sheet_no = binder["active_sheet_no"]
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('current_sheet_no', ?);", (str(assigned_sheet_no),))

    tested_by = "EP-ADMIN" if is_adm else ((payload.tested_by or "RP").strip().upper()[:4] or "RP")
    op_name = "ADMIN DIAGNOSTIC (NO DOT SIGN-OFF)" if is_adm else "Ryan Pearce"
    op_init = "ADMIN" if is_adm else "RP"
    completed_ts = now_eastern().strftime("%I:%M %p")
    rin_marked = 1 if (payload.rin_marked or payload.rin_mark_type) else 0
    rin_type = payload.rin_mark_type.strip().upper() if payload.rin_mark_type else ("MARKED" if rin_marked else "")
    star_val = (1 if payload.star_10yr else 0) if payload.star_10yr is not None else int(rec["star_10yr"] or 0)
    if (rec["dot_spec"] or "").upper() not in ("3A", "3AA") or hydro_disp != "A":
        star_val = 0

    oring = 1 if payload.addon_oring else int(rec["addon_oring"] or 0)
    bdisc = 1 if (payload.addon_burst_disc or payload.relief_valve_changed == "Yes") else int(rec["addon_burst_disc"] or 0)
    clean = 1 if payload.addon_clean else int(rec["addon_clean"] or 0)
    rel_chg = "Yes" if (payload.relief_valve_changed == "Yes" or bdisc) else "No"
    c_act = payload.condemn_action.strip().upper()
    if hydro_disp == "D" and not c_act:
        c_act = "STAMPED_CONDEMNED"

    if hydro_disp != "A":
        next_stage = "CONDEMNED_HOLD"
        next_zone = "ZONE_D_DRY_STAMP"
    elif final_reinst and rin_marked:
        next_stage = "READY_BACKROOM"
        next_zone = "ZONE_E_PICKUP_SHELF"
    elif rin_marked:
        next_stage = "STAMPED_STICKERED"
        next_zone = "ZONE_D_DRY_STAMP"
    else:
        next_stage = "DRYING"
        next_zone = "ZONE_D_DRY_STAMP"

    conn.execute(
        """UPDATE cylinder_records SET
           work_date=?, sheet_no=?, status='COMPLETED', shop_stage=?, holding_zone=?, verification_id=?, jacket_type=?,
           valve_tag=?, valve_reinstalled=?, rin_marked=?, rin_mark_type=?, star_10yr=?,
           total_cc=?, permanent_cc=?, elastic_cc=?, percent_permanent=?,
           coating_type=?, coating_condition=?,
           chk_corrosion=?, chk_dents_dings=?, chk_cuts_gouges=?, chk_leaks=?,
           chk_fire_damage=?, chk_bulges=?, chk_neck_defects=?,
           relief_valve_changed=?, addon_oring=?, addon_burst_disc=?, addon_clean=?, condemn_action=?,
           visual_pass=?, hydro_disposition=?, visual_disposition=?,
           tested_by=?, completed_at=?, operator_name=?, operator_initials=?, notes=?, synced=0
           WHERE id=?;""",
        (
            eff_test_date, assigned_sheet_no, next_stage, next_zone, ver_id,
            payload.jacket_type if rec["service_mode"] == "HYDRO_AND_VISUAL" else "Road/Visual",
            final_vtag, final_reinst, rin_marked, rin_type, star_val,
            payload.total_cc, payload.permanent_cc, elastic, pct_perm,
            payload.coating_type, payload.coating_condition,
            payload.chk_corrosion, payload.chk_dents_dings, payload.chk_cuts_gouges,
            payload.chk_leaks, payload.chk_fire_damage, payload.chk_bulges, payload.chk_neck_defects,
            rel_chg, oring, bdisc, clean, c_act,
            vis_pass, hydro_disp, vis_disp,
            tested_by, completed_ts, op_name, op_init, payload.notes, record_id,
        ),
    )
    bump_db_version(conn)
    conn.commit()
    conn.close()
    trigger_sheets_sync_bg()
    return {
        "id": record_id,
        "sheet_no": assigned_sheet_no,
        "hydro_disposition": hydro_disp,
        "visual_disposition": vis_disp,
        "percent_permanent": pct_perm,
        "shop_stage": next_stage,
    }


@router.put("/api/records/{record_id}")
def edit_cylinder_record(record_id: int, payload: RecordEditInput):
    conn = get_conn()
    rec = conn.execute("SELECT * FROM cylinder_records WHERE id=?;", (record_id,)).fetchone()
    if not rec:
        conn.close()
        raise HTTPException(404, "Cylinder record not found.")

    vtag = payload.valve_tag.strip().lstrip("#")
    reinst = 1 if payload.valve_reinstalled else 0
    if vtag and not reinst:
        dup = check_duplicate_active_valve_tag(conn, vtag, exclude_id=record_id)
        if dup:
            conn.close()
            raise HTTPException(
                409,
                f"Valve Tag #{vtag} is currently active on SN {dup['serial_number']} ({dup['customer_name']}).",
            )

    dia = payload.outside_dia.strip()
    length = payload.length_in.strip()
    phys_size = f"{dia}x{length}".strip("x") if (dia or length) else ""
    spec_clean = payload.dot_spec.strip().upper().replace("SP-", "SP")
    gas_clean = normalize_gas_service(payload.gas_service)
    cust_clean = payload.customer_name.strip() or "Walk-In"
    phone_clean = clean_phone(payload.customer_phone) or (rec["customer_phone"] or "")
    upsert_rolodex_from_activity(conn, cust_clean, phone_clean)

    pri = "RUSH" if payload.rush_fee else (payload.priority or rec["priority"] or "STANDARD").strip().upper()
    rfee = 1 if (payload.rush_fee or pri == "RUSH") else 0
    rin_marked = 1 if (payload.rin_marked or payload.rin_mark_type) else 0
    rin_type = payload.rin_mark_type.strip().upper() if payload.rin_mark_type else (rec["rin_mark_type"] or "")

    checks = [
        payload.chk_corrosion, payload.chk_dents_dings, payload.chk_cuts_gouges,
        payload.chk_leaks, payload.chk_fire_damage, payload.chk_bulges, payload.chk_neck_defects,
    ]
    vis_pass = 1 if all(c == "OK" for c in checks) else 0
    vis_disp = "OK" if vis_pass else "SC"

    eff_ree = None if (spec_clean in ("3A", "3AA", "3AL") and payload.ree_limit == 7.2) else payload.ree_limit
    elastic = rec["elastic_cc"]
    pct_perm = rec["percent_permanent"]
    hydro_disp = rec["hydro_disposition"]

    if rec["status"] == "COMPLETED":
        if rec["service_mode"] == "HYDRO_AND_VISUAL" and payload.total_cc and payload.total_cc > 0:
            perm_v = payload.permanent_cc or 0.0
            elastic = round(payload.total_cc - perm_v, 2)
            pct_perm = round((perm_v / payload.total_cc) * 100.0, 1)
            ree_failed = eff_ree is not None and elastic > float(eff_ree)
            perm_failed = pct_perm > payload.max_percent_perm
            hydro_disp = "D" if (not vis_pass or ree_failed or perm_failed) else "A"
        else:
            hydro_disp = "A" if vis_pass else "D"
        if payload.hydro_disposition_override in ("A", "B", "C", "D", "E"):
            hydro_disp = payload.hydro_disposition_override

    oring = 1 if payload.addon_oring else 0
    bdisc = 1 if (payload.addon_burst_disc or payload.relief_valve_changed == "Yes") else 0
    clean = 1 if payload.addon_clean else 0
    rel_chg = "Yes" if (payload.relief_valve_changed == "Yes" or bdisc) else "No"
    c_act = payload.condemn_action.strip().upper()
    keep_op_name = rec["operator_name"] or "Ryan Pearce"
    keep_op_init = rec["operator_initials"] or "RP"

    conn.execute(
        """UPDATE cylinder_records SET
           customer_name=?, customer_phone=?, priority=?, rush_fee=?, promised_date=?, holding_zone=?,
           serial_number=?, valve_tag=?, valve_reinstalled=?, rin_marked=?, rin_mark_type=?, star_10yr=?, shop_stage=?,
           mfg_id=?, mfg_date=?, gas_service=?, outside_dia=?, length_in=?, physical_size=?,
           dot_spec=?, service_psi=?, test_psi=?, ree_limit=?,
           total_cc=?, permanent_cc=?, elastic_cc=?, percent_permanent=?,
           coating_condition=?, relief_valve_changed=?,
           addon_oring=?, addon_burst_disc=?, addon_clean=?, condemn_action=?,
           chk_corrosion=?, chk_dents_dings=?, chk_cuts_gouges=?, chk_leaks=?,
           chk_fire_damage=?, chk_bulges=?, chk_neck_defects=?,
           visual_pass=?, hydro_disposition=?, visual_disposition=?,
           operator_name=?, operator_initials=?, notes=?, synced=0
           WHERE id=?;""",
        (
            cust_clean, phone_clean, pri, rfee,
            payload.promised_date.strip(), payload.holding_zone or rec["holding_zone"] or "ZONE_C_READY_RACK",
            payload.serial_number.strip().upper().replace(" ", ""),
            vtag, reinst, rin_marked, rin_type, (1 if (payload.star_10yr and spec_clean in ("3A", "3AA")) else 0), payload.shop_stage or rec["shop_stage"] or "IN_SHOP",
            payload.mfg_id.strip().upper(), payload.mfg_date.strip(), gas_clean,
            dia, length, phys_size, spec_clean, payload.service_psi, payload.test_psi, eff_ree,
            payload.total_cc, payload.permanent_cc, elastic, pct_perm,
            payload.coating_condition, rel_chg, oring, bdisc, clean, c_act,
            payload.chk_corrosion, payload.chk_dents_dings, payload.chk_cuts_gouges, payload.chk_leaks,
            payload.chk_fire_damage, payload.chk_bulges, payload.chk_neck_defects,
            vis_pass, hydro_disp, vis_disp, keep_op_name, keep_op_init, payload.notes, record_id,
        ),
    )
    bump_db_version(conn)
    conn.commit()
    updated = dict(conn.execute("SELECT * FROM cylinder_records WHERE id=?;", (record_id,)).fetchone())
    conn.close()
    if updated["status"] == "COMPLETED":
        trigger_sheets_sync_bg()
    return updated


@router.get("/api/search")
def search_records(q: str = "", customer: str = "", active_tag_only: bool = False):
    est_today = today_eastern()
    conn = get_conn()
    if customer:
        rows = conn.execute(
            "SELECT * FROM cylinder_records WHERE customer_name=? ORDER BY work_date DESC, id ASC;",
            (customer,),
        ).fetchall()
    elif active_tag_only:
        tag_q = q.strip().lstrip("#")
        rows = conn.execute(
            """SELECT * FROM cylinder_records
               WHERE valve_reinstalled = 0 AND valve_tag != '' AND UPPER(valve_tag) = UPPER(?)
               ORDER BY id DESC;""",
            (tag_q,),
        ).fetchall()
    else:
        raw_q = q.strip().lstrip("#")
        like = f"%{raw_q}%"
        rows = conn.execute(
            """SELECT * FROM cylinder_records
               WHERE customer_name LIKE ?
                  OR serial_number LIKE ?
                  OR dot_spec LIKE ?
                  OR work_date LIKE ?
                  OR (valve_reinstalled = 0 AND valve_tag != '' AND UPPER(valve_tag) = UPPER(?))
               ORDER BY work_date DESC, id DESC LIMIT 150;""",
            (like, like, like, like, raw_q),
        ).fetchall()
    conn.close()
    return [enrich_record_meta(dict(r), est_today) for r in rows]
