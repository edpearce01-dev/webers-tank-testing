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


@router.get("/api/public/turnaround")
def get_public_turnaround(bottles: int = 4):
    conn = get_conn()
    q = calculate_turnaround_quote(conn, max(1, min(100, bottles)))
    conn.close()
    return {
        "standard_ready_label": q["standard_promise_label"],
        "rush_ready_label": q["rush_promise_label"],
        "rush_max_bottles": MAX_RUSH_BOTTLES,
        "rush_eligible": bottles <= MAX_RUSH_BOTTLES,
    }


@router.get("/api/public/track")
def track_public_ticket(request: Request, ticket: str = "", phone_last4: str = ""):
    raw_t = re.sub(r"[^0-9]", "", (ticket or "").upper())
    last4 = re.sub(r"\D", "", phone_last4 or "")[-4:]
    if not raw_t:
        raise HTTPException(400, "Enter your Ticket # (e.g. BR-0001).")
    did = int(raw_t)
    is_staff = bool(get_current_session_user(request))
    conn = get_conn()
    d = conn.execute("SELECT * FROM dropoffs WHERE id=?;", (did,)).fetchone()
    if not d:
        conn.close()
        raise HTTPException(404, "Ticket not found. Check your BR-XXXX number or call 724-266-8008.")
    stored_digits = re.sub(r"\D", "", d["customer_phone"] or "")
    if not is_staff and len(stored_digits) >= 4 and (len(last4) != 4 or not stored_digits.endswith(last4)):
        conn.close()
        raise HTTPException(403, "Enter the matching last 4 digits of the phone number on this ticket.")

    recs = [
        dict(r)
        for r in conn.execute(
            "SELECT status, shop_stage, rin_marked, hydro_disposition FROM cylinder_records WHERE dropoff_id=?;",
            (did,),
        ).fetchall()
    ]
    if not recs and d["entry_type"] == "DROPOFF":
        recs = [
            dict(r)
            for r in conn.execute(
                "SELECT status, shop_stage, rin_marked, hydro_disposition FROM cylinder_records WHERE UPPER(customer_name)=UPPER(?) AND work_date >= ?;",
                (d["customer_name"], d["work_date"]),
            ).fetchall()
        ]
    conn.close()

    expected = max(int(d["bottle_count"] or 1), len(recs))
    scanned = len(recs)
    tested = sum(1 for r in recs if r["status"] == "COMPLETED")
    ready_shelf = sum(1 for r in recs if r["shop_stage"] == "READY_BACKROOM")
    picked_up = sum(1 for r in recs if r["shop_stage"] == "PICKED_UP")

    if picked_up >= expected or (d["status"] == "ARCHIVED" and ready_shelf == 0 and tested > 0 and picked_up > 0):
        stage_label = "Picked Up / Completed"
        step_num = 5
    elif ready_shelf >= expected and expected > 0:
        stage_label = "Certified & Ready for 24/7 Loading Dock Pickup"
        step_num = 4
    elif tested > 0:
        stage_label = f"Hydro Testing & Certification ({tested}/{expected} Tested)"
        step_num = 3
    elif scanned > 0:
        stage_label = f"Checked In & Prepped for Stand ({scanned}/{expected} Logged)"
        step_num = 2
    else:
        stage_label = "Logged in Loading Dock Drop-Off Queue"
        step_num = 1

    return {
        "ticket_no": f"BR-{did:04d}",
        "bottle_count": expected,
        "service_requested": d["service_requested"],
        "estimated_ready": d["promised_date"] or "",
        "stage_label": stage_label,
        "step_num": step_num,
        "scanned_count": scanned,
        "tested_count": tested,
        "ready_count": ready_shelf,
    }


@router.get("/api/public/receipt/{ticket_no}.pdf")
def download_public_dropoff_receipt(ticket_no: str, request: Request, p: str = "", inline: bool = False):
    raw_num = re.sub(r"[^0-9]", "", (ticket_no or "").upper())
    if not raw_num:
        raise HTTPException(400, "Invalid ticket number.")
    did = int(raw_num)
    is_staff = bool(get_current_session_user(request))
    conn = get_conn()
    d = conn.execute("SELECT * FROM dropoffs WHERE id=?;", (did,)).fetchone()
    s_map = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings;").fetchall()}
    conn.close()
    if not d:
        raise HTTPException(404, "Ticket not found.")
    stored_digits = re.sub(r"\D", "", d["customer_phone"] or "")
    req_last4 = re.sub(r"\D", "", p or "")[-4:]
    if not is_staff and len(stored_digits) >= 4 and (len(req_last4) != 4 or not stored_digits.endswith(req_last4)):
        raise HTTPException(403, "Phone verification mismatch.")

    t_code = f"BR-{did:04d}"
    t_last4 = stored_digits[-4:] if stored_digits else ""
    track_link = f"https://weberstanktesting.com/?ticket={t_code}&p={t_last4}#tracker"
    pdf_bytes = build_dropoff_receipt_pdf(
        dict(d),
        phone=s_map.get("phone", "724-266-8008"),
        dot_rin=s_map.get("dot_rin", "B386"),
        track_url=track_link,
    )
    disp_mode = "inline" if inline else "attachment"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'{disp_mode}; filename="Webers_Ticket_{t_code}.pdf"'},
    )


@router.post("/api/public/dropoff")
def submit_public_dropoff(payload: PublicDropoffInput):
    if payload.website_hp.strip():
        return {"logged": True}
    cust = payload.customer_name.strip()[:80]
    if len(cust) < 2:
        raise HTTPException(400, "Please enter your name, fire department, or company name.")
    ph_clean = clean_phone(payload.customer_phone)
    ph_digits = re.sub(r"\D", "", ph_clean)
    if payload.entry_type.strip().upper() != "PICKUP_NOTICE" and len(ph_digits) < 10:
        raise HTTPException(400, "Please enter a valid 10-digit cell or station phone number.")

    cnt = max(1, min(250, int(payload.bottle_count or 1)))
    raw_etype = payload.entry_type.strip().upper()
    if raw_etype == "PICKUP_NOTICE":
        etype = "PICKUP_NOTICE"
        init_status = "PICKUP_LOGGED"
    elif raw_etype == "TRUCK_REQUEST":
        etype = "TRUCK_REQUEST"
        init_status = "TRUCK_REQUESTED"
    else:
        etype = "DROPOFF"
        init_status = "IN_BACKROOM"

    svc_txt = payload.service_requested.strip()[:60] or "Hydro + Visual"
    is_rush = 1 if (payload.rush_requested or "RUSH" in svc_txt.upper()) else 0
    if is_rush and cnt > MAX_RUSH_BOTTLES:
        raise HTTPException(400, f"Paid Rush Service is limited to {MAX_RUSH_BOTTLES} cylinders maximum. Uncheck Paid Rush for batches over {MAX_RUSH_BOTTLES} cylinders or call 724-266-8008.")
    acct = (payload.account_type or "WALK_IN").strip().upper()
    po_st = payload.po_station.strip()[:60]
    contact_p = payload.contact_person.strip()[:60]
    em_clean = payload.customer_email.strip()[:100]
    full_mail_str = ", ".join(filter(None, [
        payload.street_address.strip(),
        payload.city.strip(),
        f"{payload.state.strip() or 'PA'} {payload.zip_code.strip()}".strip(),
    ]))
    addr_clean = (payload.pickup_address.strip() or full_mail_str)[:140]
    pref_date = payload.preferred_date.strip()[:16]

    if etype == "DROPOFF" and acct in ("FIRE_EMS", "COMMERCIAL") and len(contact_p) < 2:
        raise HTTPException(400, "Please enter your individual name in 'Dropped Off By'.")
    if etype == "DROPOFF" and acct in ("FIRE_EMS", "COMMERCIAL") and len(payload.street_address.strip() or addr_clean) < 4:
        raise HTTPException(400, "Please enter your Department / Company Billing Street Address & City.")
    if etype == "TRUCK_REQUEST" and len(addr_clean) < 5:
        raise HTTPException(400, "Please enter the street / station address for Truck Pickup & Delivery.")

    picked_by = payload.picked_up_by.strip()[:60]
    tkt_ref = payload.pickup_ticket_ref.strip().upper()[:24]

    note_parts = []
    if contact_p:
        note_parts.append(f"Contact: {contact_p}")
    if po_st:
        note_parts.append(f"PO/Sta: {po_st}")
    if addr_clean:
        note_parts.append(f"Addr: {addr_clean}")
    if picked_by:
        note_parts.append(f"Picked up by: {picked_by}")
    if payload.notes.strip():
        note_parts.append(payload.notes.strip()[:180])
    combined_notes = " | ".join(note_parts)[:280]

    est = now_eastern()
    ts = est.strftime("%Y-%m-%d %I:%M %p")
    w_date = est.date().isoformat()
    sched_date = pref_date if re.match(r"^\d{4}-\d{2}-\d{2}$", pref_date) else w_date

    conn = get_conn()
    upsert_rolodex_from_activity(conn, cust, ph_clean, acct, contact_p, em_clean, payload.street_address.strip() or addr_clean, payload.city.strip(), payload.state.strip(), payload.zip_code.strip())
    quote = calculate_turnaround_quote(conn, cnt)
    promised = quote["rush_promise_date"] if is_rush else quote["standard_promise_date"]
    zone = "OUTBOUND_TRUCK" if etype == "TRUCK_REQUEST" else "ZONE_A_BACKROOM"
    d_miles, d_eta = estimate_route_from_shop(addr_clean) if etype == "TRUCK_REQUEST" else (0.0, 0)

    cur = conn.cursor()
    cur.execute(
        """INSERT INTO dropoffs
           (created_at, work_date, entry_type, account_type, po_station, contact_person,
            customer_name, customer_phone, customer_email, pickup_address, preferred_date,
            bottle_count, tank_summary, service_requested, valves_attached, needs_air_fill,
            rush_requested, promised_date, holding_zone, picked_up_by, pickup_ticket_ref, notes, status, synced)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, 0);""",
        (
            ts, w_date, etype, acct, po_st, contact_p,
            cust, ph_clean, em_clean, addr_clean, sched_date,
            cnt, payload.tank_summary.strip()[:140], svc_txt,
            1 if payload.valves_attached else 0,
            is_rush,
            promised if etype in ("DROPOFF", "TRUCK_REQUEST") else "",
            zone, picked_by, tkt_ref, combined_notes, init_status,
        ),
    )
    did = cur.lastrowid

    # If customer requested Truck Pickup / Delivery, auto-create the dispatch job on the Wall Calendar!
    if etype == "TRUCK_REQUEST":
        jtype = payload.truck_mode.strip().upper() if payload.truck_mode else "ROAD_ROUNDTRIP"
        if jtype not in ("ROAD_ROUNDTRIP", "ROAD_PICKUP", "ROAD_DELIVERY"):
            jtype = "ROAD_ROUNDTRIP"
        ret_d = payload.return_date.strip()[:10] if re.match(r"^\d{4}-\d{2}-\d{2}$", payload.return_date.strip()) else promised
        cur.execute(
            """INSERT INTO schedule_jobs
               (created_at, scheduled_date, due_date, pickup_time, dropoff_time, customer_email,
                drive_miles, drive_eta_mins, job_type, account_type, po_station, contact_person,
                customer_name, customer_phone, location_notes, bottle_count, tank_summary,
                valves_attached, needs_air_fill, priority, rush_fee, holding_zone, prep_status, status, notes, synced)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, ?, ?, 'OUTBOUND_TRUCK', 'WAITING', 'OPEN', ?, 0);""",
            (
                ts, sched_date, ret_d, payload.preferred_time.strip()[:24], payload.return_time.strip()[:24], em_clean, d_miles, d_eta, jtype, acct, po_st, contact_p,
                cust, ph_clean, addr_clean, cnt, payload.tank_summary.strip()[:140],
                "RUSH" if is_rush else "STANDARD", is_rush,
                f"Web Request BR-{did:04d}" + (f" | {payload.notes.strip()[:160]}" if payload.notes.strip() else ""),
            ),
        )
        # Mark dropoffs row archived from backroom door inbox since it lives in Truck Dispatch now
        conn.execute("UPDATE dropoffs SET status='DISPATCH_SCHEDULED' WHERE id=?;", (did,))

    released_bottles = 0
    if etype == "PICKUP_NOTICE":
        who = picked_by or cust
        raw_ref_num = re.sub(r"[^0-9]", "", tkt_ref)
        if raw_ref_num:
            ref_id = int(raw_ref_num)
            res_upd = conn.execute(
                """UPDATE cylinder_records
                   SET shop_stage='PICKED_UP', holding_zone='PICKED_UP', valve_reinstalled=1,
                       picked_up_by=?, picked_up_at=?, synced=0
                   WHERE dropoff_id=? AND status='COMPLETED' AND shop_stage='READY_BACKROOM';""",
                (who, ts, ref_id),
            )
            released_bottles += res_upd.rowcount
            if released_bottles > 0:
                conn.execute("UPDATE dropoffs SET status='ARCHIVED', synced=0 WHERE id=?;", (ref_id,))
        if released_bottles == 0:
            res_cust = conn.execute(
                """UPDATE cylinder_records
                   SET shop_stage='PICKED_UP', holding_zone='PICKED_UP', valve_reinstalled=1,
                       picked_up_by=?, picked_up_at=?, synced=0
                   WHERE UPPER(customer_name)=UPPER(?) AND status='COMPLETED' AND shop_stage='READY_BACKROOM';""",
                (who, ts, cust),
            )
            released_bottles += res_cust.rowcount

    ticket_code = f"BR-{did:04d}"
    if etype in ("DROPOFF", "TRUCK_REQUEST") and len(ph_digits) >= 10:
        sms_msg = (
            f"Weber's Tank Testing: Ticket {ticket_code} logged ({cnt} cyl). "
            f"Est. ready: ~{promised}. Track status: https://weberstanktesting.com/?ticket={ticket_code}&p={ph_digits[-4:]}#tracker"
        )
        queue_customer_sms_outbox(conn, cust, ph_clean, sms_msg, kind="TICKET", ref_id=ticket_code)
    bump_db_version(conn)
    conn.commit()
    conn.close()
    trigger_sheets_sync_bg()
    last4 = ph_digits[-4:] if len(ph_digits) >= 4 else ""
    receipt_pdf_path = f"/api/public/receipt/{ticket_code}.pdf?p={last4}"
    if em_clean or etype in ("DROPOFF", "TRUCK_REQUEST", "PICKUP_NOTICE"):
        trigger_ticket_email_bg({
            "ticket_no": ticket_code,
            "entry_type": etype,
            "customer_name": cust,
            "contact_person": picked_by or contact_p,
            "service_requested": svc_txt,
            "customer_phone": ph_clean,
            "customer_email": em_clean,
            "pickup_address": addr_clean,
            "preferred_date": sched_date,
            "bottle_count": cnt,
            "tank_summary": payload.tank_summary.strip()[:140],
            "rush_requested": bool(is_rush),
            "estimated_ready": promised if etype in ("DROPOFF", "TRUCK_REQUEST") else "",
            "created_at": ts,
            "track_url": f"https://weberstanktesting.com/?ticket={ticket_code}&p={last4}#tracker",
            "pdf_url": f"https://weberstanktesting.com{receipt_pdf_path}",
        })
    return {
        "logged": True,
        "ticket_no": ticket_code,
        "phone_last4": last4,
        "email_queued": bool(em_clean and "@" in em_clean),
        "receipt_pdf_url": receipt_pdf_path,
        "created_at": ts,
        "estimated_ready": promised,
        "released_bottles": released_bottles,
    }


@router.post("/api/dropoffs/{dropoff_id}/status")
def update_dropoff_status(dropoff_id: int, payload: DropoffStatusInput):
    conn = get_conn()
    row = conn.execute("SELECT * FROM dropoffs WHERE id=?;", (dropoff_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Drop-off entry not found.")
    new_st = payload.status.strip().upper() if payload.status is not None else row["status"]
    new_zone = payload.holding_zone.strip() if payload.holding_zone is not None else (row["holding_zone"] or "ZONE_A_BACKROOM")
    new_rush = (1 if payload.rush_requested else 0) if payload.rush_requested is not None else int(row["rush_requested"] or 0)
    new_prom = payload.promised_date.strip() if payload.promised_date is not None else (row["promised_date"] or "")
    conn.execute(
        "UPDATE dropoffs SET status=?, holding_zone=?, rush_requested=?, promised_date=?, synced=0 WHERE id=?;",
        (new_st, new_zone, new_rush, new_prom, dropoff_id),
    )
    bump_db_version(conn)
    conn.commit()
    conn.close()
    trigger_sheets_sync_bg()
    return {"updated": dropoff_id}


@router.delete("/api/dropoffs/{dropoff_id}")
def delete_dropoff(dropoff_id: int):
    conn = get_conn()
    conn.execute("DELETE FROM dropoffs WHERE id=?;", (dropoff_id,))
    bump_db_version(conn)
    conn.commit()
    conn.close()
    return {"deleted": dropoff_id}


@router.get("/api/public/hours")
def get_public_operating_hours():
    conn = get_conn()
    data = get_operating_schedule_bundle(conn)
    conn.close()
    return {
        "weekly_hours": data["weekly_hours"],
        "upcoming_closures": data["upcoming_closures"],
        "summary_line": data["summary_line"],
        "active_banner": data["active_banner"],
    }


@router.get("/api/public/iphone-sms-bridge")
def iphone_shortcut_pull_sms(key: str = "", auto_ack: bool = True):
    conn = get_conn()
    saved_k = conn.execute("SELECT value FROM settings WHERE key='sms_shortcut_key';").fetchone()
    if not saved_k or not key or key.strip() != saved_k["value"]:
        conn.close()
        raise HTTPException(403, "Invalid Shop iPhone Shortcut key.")
    rows = [dict(r) for r in conn.execute("SELECT id, customer_name, phone, message FROM sms_outbox WHERE status='PENDING' ORDER BY id ASC LIMIT 10;").fetchall()]
    if rows and auto_ack:
        ts = now_eastern().strftime("%I:%M %p")
        conn.executemany("UPDATE sms_outbox SET status='SENT', sent_at=? WHERE id=?;", [(ts, r["id"]) for r in rows])
        conn.commit()
    conn.close()
    return {"messages": rows}


@router.api_route("/api/public/iphone-sms-bridge/ack", methods=["GET", "POST"])
async def iphone_shortcut_ack_sms(request: Request, key: str = "", ids: str = ""):
    if not ids and request.method == "POST":
        try:
            body = await request.json()
            key = key or str(body.get("key") or "")
            raw_ids = body.get("ids") or []
            ids = ",".join(str(x) for x in raw_ids) if isinstance(raw_ids, list) else str(raw_ids)
        except Exception:
            pass
    conn = get_conn()
    saved_k = conn.execute("SELECT value FROM settings WHERE key='sms_shortcut_key';").fetchone()
    if not saved_k or not key or key.strip() != saved_k["value"]:
        conn.close()
        raise HTTPException(403, "Invalid Shop iPhone Shortcut key.")
    parsed_ids = [int(x) for x in re.findall(r"\d+", ids or "")]
    if parsed_ids:
        ts = now_eastern().strftime("%I:%M %p")
        conn.executemany("UPDATE sms_outbox SET status='SENT', sent_at=? WHERE id=?;", [(ts, i) for i in parsed_ids])
        conn.commit()
    conn.close()
    return {"acked": len(parsed_ids)}


@router.get("/api/public/merch")
def get_merch_locker_state(request: Request):
    user = getattr(request.state, "user", None) or get_current_session_user(request)
    conn = get_conn()
    s_rows = conn.execute(
        "SELECT key, value FROM settings WHERE key IN ('merch_popup_url', 'merch_allowance_note');"
    ).fetchall()
    conn.close()
    cfg = {r["key"]: r["value"] for r in s_rows}
    return {
        "authenticated": bool(user),
        "user": user,
        "popup_store_url": cfg.get("merch_popup_url", ""),
        "allowance_note": cfg.get(
            "merch_allowance_note",
            "Order any official Weber's Tank Testing apparel, hats, tumblers, mugs, or blankets directly through our Printify Storefront.",
        ),
    }


@router.get("/api/public/theme")
def get_public_holiday_theme():
    conn = get_conn()
    data = compute_active_theme(conn)
    conn.close()
    return data


@router.get("/api/public/site-content")
def get_public_site_content():
    keys = (
        "site_banner_announcement",
        "site_about_eyebrow",
        "site_about_heading",
        "site_about_story",
        "site_business_details",
        "site_dropoff_note",
        "site_owner_signoff",
        "phone",
        "dot_rin",
    )
    conn = get_conn()
    q_marks = ",".join("?" for _ in keys)
    rows = conn.execute(f"SELECT key, value FROM settings WHERE key IN ({q_marks});", keys).fetchall()
    conn.close()
    s_map = {r["key"]: r["value"] for r in rows}
    return {
        "site_banner_announcement": s_map.get("site_banner_announcement", ""),
        "site_about_eyebrow": s_map.get("site_about_eyebrow", "Firefighter Owned & Operated • Baden, PA"),
        "site_about_heading": s_map.get("site_about_heading", "About Weber's Tank Testing"),
        "site_about_story": s_map.get("site_about_story", ""),
        "site_business_details": s_map.get("site_business_details", ""),
        "site_dropoff_note": s_map.get("site_dropoff_note", ""),
        "site_owner_signoff": s_map.get("site_owner_signoff", "— Ryan Pearce, Facility Manager (DOT RIN B386)"),
        "phone": s_map.get("phone", "724-266-8008"),
        "dot_rin": s_map.get("dot_rin", "B386"),
    }


@router.post("/api/public/inquiry")
def submit_public_website_inquiry(payload: PublicInquiryInput):
    import threading

    if payload.website_hp.strip():
        return {"sent": True}
    name = payload.customer_name.strip()[:80]
    msg_txt = payload.message.strip()[:800]
    topic = (payload.topic or "General Question").strip()[:60]
    ph_clean = clean_phone(payload.customer_phone)
    ph_digits = re.sub(r"\D", "", ph_clean)
    em_clean = payload.customer_email.strip()[:100]

    if len(name) < 2:
        raise HTTPException(400, "Please enter your name or department.")
    if len(ph_digits) < 10 and "@" not in em_clean:
        raise HTTPException(400, "Please include a 10-digit phone number or email address so Ryan can reply.")
    if len(msg_txt) < 3:
        raise HTTPException(400, "Please enter your question or message.")

    est = now_eastern()
    ts = est.strftime("%Y-%m-%d %I:%M %p")
    w_date = est.date().isoformat()
    full_body = f"[{topic}] {msg_txt}" if topic and topic.lower() != "general question" else msg_txt

    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO shop_messages
           (created_at, work_date, channel, sender_name, sender_initials, sender_role,
            sender_phone, sender_email, recipient, body, status)
           VALUES (?, ?, 'WEB_INQUIRY', ?, 'WEB', 'CUSTOMER', ?, ?, 'RP', ?, 'OPEN');""",
        (ts, w_date, name, ph_clean, em_clean, full_body),
    )
    msg_id = cur.lastrowid
    alert_row = conn.execute("SELECT value FROM settings WHERE key='alert_email';").fetchone()
    alert_to = (os.getenv("WEBER_ALERT_EMAIL") or (alert_row["value"] if alert_row else "") or "").strip()
    bump_db_version(conn)
    conn.commit()
    conn.close()

    if alert_to and "@" in alert_to:
        subj = f"💬 Website Question from {name} — Weber's Tank Testing"
        email_body = (
            f"New Customer Message from weberstanktesting.com\n"
            f"================================================\n"
            f"From:    {name}\n"
            f"Phone:   {ph_clean or 'Not provided'}\n"
            f"Email:   {em_clean or 'Not provided'}\n"
            f"Topic:   {topic}\n"
            f"Time:    {ts}\n\n"
            f"Message:\n{msg_txt}\n\n"
            f"Open your Shop Portal Messages drawer to text/email back or mark resolved:\n"
            f"https://ops.weberstanktesting.com/ops"
        )
        threading.Thread(
            target=send_raw_email_resend_or_smtp,
            args=(alert_to, subj, email_body, em_clean),
            daemon=True,
        ).start()

    return {"sent": True, "id": msg_id}
