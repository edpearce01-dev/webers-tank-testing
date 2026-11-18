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


@router.post("/api/admin/wipe-test-data")
async def admin_wipe_test_data(request: Request, wipe_customers: bool = False):
    require_owner_or_admin(request)
    conn = get_conn()
    webhook_row = conn.execute("SELECT value FROM settings WHERE key='sheets_webhook_url';").fetchone()
    webhook_url = (webhook_row["value"] if webhook_row else "").strip()
    wipe_all_operational_data(conn, wipe_customers=wipe_customers)
    conn.commit()
    conn.close()

    sheets_wiped = False
    if webhook_url:
        try:
            sheets_wiped = await asyncio.to_thread(wipe_sheets_remote, webhook_url)
        except Exception:
            sheets_wiped = False
    return {"wiped": True, "sheets_wiped": sheets_wiped}


@router.post("/api/settings/test-email")
async def trigger_test_shop_email(request: Request):
    require_owner_or_admin(request)
    res = await asyncio.to_thread(send_ticket_email_via_webhook, {}, True)
    if not res.get("sent_smtp") and not res.get("sent_webhook"):
        raise HTTPException(400, res.get("detail") or "Could not send test email.")
    return res


@router.post("/api/settings")
def update_settings(payload: SettingsInput, request: Request):
    require_owner_or_admin(request)
    # reset cached vision model
    conn = get_conn()
    if payload.calibrated_serial:
        new_sn = payload.calibrated_serial.strip().upper()
        old_sn = conn.execute("SELECT value FROM settings WHERE key='calibrated_serial';").fetchone()["value"]
        conn.execute("UPDATE settings SET value=? WHERE key='calibrated_serial';", (new_sn,))
        conn.execute("UPDATE calibration_targets SET serial_number=? WHERE serial_number=?;", (new_sn, old_sn))
    if payload.starting_sheet_no is not None:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('starting_sheet_no', ?);", (str(payload.starting_sheet_no),))
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('current_sheet_no', ?);", (str(payload.starting_sheet_no),))
    if payload.daily_hydro_capacity is not None:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('daily_hydro_capacity', ?);", (str(max(4, payload.daily_hydro_capacity)),))
    if payload.default_tax_rate is not None:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('default_tax_rate', ?);", (str(payload.default_tax_rate),))
    if payload.rin_expires_date is not None:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('rin_expires_date', ?);", (payload.rin_expires_date.strip(),))
    if payload.gauge_cert_expires_date is not None:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('gauge_cert_expires_date', ?);", (payload.gauge_cert_expires_date.strip(),))
    if payload.gemini_api_key is not None and payload.gemini_api_key.strip():
        clear_vision_cache()
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('gemini_api_key', ?);", (payload.gemini_api_key.strip(),))
    if payload.sheets_webhook_url is not None:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('sheets_webhook_url', ?);", (payload.sheets_webhook_url.strip(),))
    if payload.feedback_webhook_url is not None:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('feedback_webhook_url', ?);", (payload.feedback_webhook_url.strip(),))
    if payload.alert_email is not None:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('alert_email', ?);", (payload.alert_email.strip(),))
    if payload.smtp_sender_email is not None:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('smtp_sender_email', ?);", (payload.smtp_sender_email.strip(),))
    if payload.smtp_app_password is not None and payload.smtp_app_password.strip():
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('smtp_app_password', ?);", (payload.smtp_app_password.strip(),))
    if payload.resend_api_key is not None and payload.resend_api_key.strip():
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('resend_api_key', ?);", (payload.resend_api_key.strip(),))
    if payload.resend_from_email is not None and payload.resend_from_email.strip():
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('resend_from_email', ?);", (payload.resend_from_email.strip(),))
    if payload.master_sheet_url is not None:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('master_sheet_url', ?);", (payload.master_sheet_url.strip(),))
    if payload.gbp_edit_url is not None and payload.gbp_edit_url.strip():
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('gbp_edit_url', ?);", (payload.gbp_edit_url.strip(),))
    if payload.testing_mode is not None:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('testing_mode', ?);", ("1" if payload.testing_mode else "0",))
    if payload.shop_pin is not None and payload.shop_pin.strip():
        new_p = payload.shop_pin.strip()
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('shop_pin', ?);", (new_p,))
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('crew_pin', ?);", (new_p,))
        conn.execute("UPDATE crew_users SET pin=? WHERE role='OWNER' AND initials='RP';", (new_p,))
    bump_db_version(conn)
    conn.commit()
    conn.close()
    return JSONResponse({"saved": True})


@router.post("/api/sync-now")
async def manual_sync_now():
    try:
        return await run_guarded_sheets_sync()
    except Exception as exc:
        raise HTTPException(502, f"Google Sheets sync error: {exc}")


@router.get("/api/customers")
def list_customers_rolodex():
    conn = get_conn()
    rows = [dict(r) for r in conn.execute("SELECT * FROM customers ORDER BY customer_name COLLATE NOCASE ASC;").fetchall()]
    conn.close()
    return rows


@router.post("/api/customers")
def save_customer_rolodex(payload: CustomerRolodexInput):
    cname = payload.customer_name.strip()
    if not cname:
        raise HTTPException(400, "Customer / Department name is required.")
    ph = clean_phone(payload.phone)
    acct = (payload.account_type or "WALK_IN").strip().upper()
    terms = (payload.payment_terms or "DUE_AT_PICKUP").strip().upper()
    tax_ex = 1 if payload.tax_exempt else 0
    ts = now_eastern().strftime("%Y-%m-%d %I:%M %p")

    conn = get_conn()
    if payload.id:
        old = conn.execute("SELECT customer_name FROM customers WHERE id=?;", (payload.id,)).fetchone()
        conn.execute(
            """UPDATE customers SET
               customer_name=?, account_type=?, contact_person=?, phone=?, email=?,
               street_address=?, city=?, state=?, zip_code=?, payment_terms=?, tax_exempt=?, notes=?, updated_at=?
               WHERE id=?;""",
            (
                cname, acct, payload.contact_person.strip(), ph, payload.email.strip(),
                payload.street_address.strip(), payload.city.strip(), payload.state.strip().upper()[:4] or "PA",
                payload.zip_code.strip()[:12], terms, tax_ex, payload.notes.strip(), ts, payload.id,
            ),
        )
        if old and ph:
            conn.execute("UPDATE cylinder_records SET customer_phone=? WHERE customer_name=? AND customer_phone='';", (ph, cname))
    else:
        conn.execute(
            """INSERT INTO customers
               (customer_name, account_type, contact_person, phone, email, street_address,
                city, state, zip_code, payment_terms, tax_exempt, notes, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(customer_name) DO UPDATE SET
                   account_type=excluded.account_type,
                   contact_person=excluded.contact_person,
                   phone=excluded.phone,
                   email=excluded.email,
                   street_address=excluded.street_address,
                   city=excluded.city,
                   state=excluded.state,
                   zip_code=excluded.zip_code,
                   payment_terms=excluded.payment_terms,
                   tax_exempt=excluded.tax_exempt,
                   notes=excluded.notes,
                   updated_at=excluded.updated_at;""",
            (
                cname, acct, payload.contact_person.strip(), ph, payload.email.strip(),
                payload.street_address.strip(), payload.city.strip(), payload.state.strip().upper()[:4] or "PA",
                payload.zip_code.strip()[:12], terms, tax_ex, payload.notes.strip(), ts,
            ),
        )
    bump_db_version(conn)
    conn.commit()
    conn.close()
    return {"saved": True, "customer_name": cname}


@router.delete("/api/customers/{cust_id}")
def delete_customer_rolodex(cust_id: int, request: Request):
    require_owner_or_admin(request)
    conn = get_conn()
    conn.execute("DELETE FROM customers WHERE id=?;", (cust_id,))
    bump_db_version(conn)
    conn.commit()
    conn.close()
    return {"deleted": cust_id}


@router.get("/api/customers/export.csv")
def export_customers_csv():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM customers ORDER BY customer_name COLLATE NOCASE ASC;").fetchall()
    conn.close()
    import csv, io
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "Customer / Department", "Account Type", "Contact Person", "Phone", "Email",
        "Street Address", "City", "State", "ZIP", "Payment Terms", "Tax Exempt", "Notes",
    ])
    for r in rows:
        writer.writerow([
            r["customer_name"], r["account_type"], r["contact_person"], r["phone"], r["email"],
            r["street_address"], r["city"], r["state"], r["zip_code"],
            r["payment_terms"], "Yes" if r["tax_exempt"] else "No", r["notes"],
        ])
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="Webers_Customer_Rolodex.csv"'},
    )


@router.get("/api/recalls")
def get_due_recalls():
    conn = get_conn()
    rows = compute_recall_list(conn, today_eastern())
    conn.close()
    return rows


@router.get("/api/supplies")
def list_shop_supplies():
    conn = get_conn()
    rows = [dict(r) for r in conn.execute("SELECT * FROM shop_supplies ORDER BY low_stock DESC, sort_order ASC, id ASC;").fetchall()]
    conn.close()
    return rows


@router.post("/api/supplies")
def save_shop_supply(payload: SupplyUpsertInput):
    name = payload.item_name.strip()
    if not name:
        raise HTTPException(400, "Supply item name is required.")
    qty = max(0, int(payload.on_hand_qty))
    thresh = max(0, int(payload.reorder_threshold))
    unit = (payload.unit_label or "units").strip()[:20] or "units"
    is_low = 1 if (payload.low_stock or qty <= thresh) else 0

    conn = get_conn()
    if payload.id:
        conn.execute(
            """UPDATE shop_supplies SET
               category=?, item_name=?, spec_note=?, vendor_name=?, reorder_url=?,
               on_hand_qty=?, reorder_threshold=?, unit_label=?,
               low_stock=?, last_ordered_date=?, sort_order=?
               WHERE id=?;""",
            (
                payload.category.strip() or "Shop Supplies", name, payload.spec_note.strip(),
                payload.vendor_name.strip(), payload.reorder_url.strip(),
                qty, thresh, unit, is_low, payload.last_ordered_date.strip(), payload.sort_order, payload.id,
            ),
        )
    else:
        conn.execute(
            """INSERT INTO shop_supplies
               (category, item_name, spec_note, vendor_name, reorder_url,
                on_hand_qty, reorder_threshold, unit_label, low_stock, last_ordered_date, sort_order)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);""",
            (
                payload.category.strip() or "Shop Supplies", name, payload.spec_note.strip(),
                payload.vendor_name.strip(), payload.reorder_url.strip(),
                qty, thresh, unit, is_low, payload.last_ordered_date.strip(), payload.sort_order,
            ),
        )
    bump_db_version(conn)
    conn.commit()
    conn.close()
    return {"saved": True}


@router.post("/api/supplies/{supply_id}/qty")
def adjust_supply_qty_or_threshold(supply_id: int, payload: SupplyQtyPatchInput):
    conn = get_conn()
    row = conn.execute("SELECT * FROM shop_supplies WHERE id=?;", (supply_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Supply item not found.")

    cur_qty = int(row["on_hand_qty"] if row["on_hand_qty"] is not None else 5)
    thresh = int(payload.reorder_threshold) if payload.reorder_threshold is not None else int(row["reorder_threshold"] if row["reorder_threshold"] is not None else 2)
    thresh = max(0, thresh)
    if payload.on_hand_qty is not None:
        new_qty = max(0, int(payload.on_hand_qty))
    elif payload.delta is not None:
        new_qty = max(0, cur_qty + int(payload.delta))
    else:
        new_qty = cur_qty

    unit = payload.unit_label.strip()[:20] if payload.unit_label is not None else (row["unit_label"] or "units")
    is_low = 1 if new_qty <= thresh else 0
    was_alerted = int(row["alert_sent"] or 0)
    fire_alert = bool(is_low and not was_alerted)
    next_alert_sent = 1 if is_low else 0

    conn.execute(
        "UPDATE shop_supplies SET on_hand_qty=?, reorder_threshold=?, unit_label=?, low_stock=?, alert_sent=? WHERE id=?;",
        (new_qty, thresh, unit, is_low, next_alert_sent, supply_id),
    )
    bump_db_version(conn)
    conn.commit()
    updated = dict(conn.execute("SELECT * FROM shop_supplies WHERE id=?;", (supply_id,)).fetchone())
    conn.close()
    if fire_alert:
        _notify_ryan_low_supply_bg(updated)
    return updated


@router.post("/api/supplies/{supply_id}/stock")
def toggle_supply_stock(supply_id: int, payload: SupplyLowStockInput):
    conn = get_conn()
    row = conn.execute("SELECT * FROM shop_supplies WHERE id=?;", (supply_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Supply item not found.")
    if payload.mark_ordered_today:
        restored_qty = max(int(row["on_hand_qty"] or 0), int(row["reorder_threshold"] or 2) + 3)
        conn.execute(
            "UPDATE shop_supplies SET low_stock=0, alert_sent=0, on_hand_qty=?, last_ordered_date=? WHERE id=?;",
            (restored_qty, today_eastern(), supply_id),
        )
    else:
        is_low = 1 if payload.low_stock else 0
        conn.execute("UPDATE shop_supplies SET low_stock=?, alert_sent=? WHERE id=?;", (is_low, is_low, supply_id))
    bump_db_version(conn)
    conn.commit()
    updated = dict(conn.execute("SELECT * FROM shop_supplies WHERE id=?;", (supply_id,)).fetchone())
    conn.close()
    if payload.low_stock and not payload.mark_ordered_today:
        _notify_ryan_low_supply_bg(updated)
    return {"updated": supply_id}


@router.delete("/api/supplies/{supply_id}")
def delete_shop_supply(supply_id: int):
    conn = get_conn()
    conn.execute("DELETE FROM shop_supplies WHERE id=?;", (supply_id,))
    bump_db_version(conn)
    conn.commit()
    conn.close()
    return {"deleted": supply_id}


@router.get("/api/office/operating-schedule")
def get_office_operating_schedule():
    conn = get_conn()
    data = get_operating_schedule_bundle(conn)
    conn.close()
    return data


@router.post("/api/office/operating-hours")
def save_office_operating_hours(payload: OperatingScheduleSaveInput, request: Request):
    require_owner_or_admin(request)
    conn = get_conn()
    for h in payload.weekly_hours:
        conn.execute(
            """INSERT OR REPLACE INTO operating_hours
               (day_of_week, day_name, is_open, open_time, close_time, dock_24_7, note)
               VALUES (?, ?, ?, ?, ?, ?, ?);""",
            (
                int(h.day_of_week), h.day_name.strip()[:16], 1 if h.is_open else 0,
                h.open_time.strip()[:8], h.close_time.strip()[:8], 1 if h.dock_24_7 else 0, h.note.strip()[:80],
            ),
        )
    if payload.gbp_edit_url is not None:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('gbp_edit_url', ?);", (payload.gbp_edit_url.strip(),))
    if payload.master_sheet_url is not None:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('master_sheet_url', ?);", (payload.master_sheet_url.strip(),))
    bump_db_version(conn)
    conn.commit()
    updated = get_operating_schedule_bundle(conn)
    conn.close()
    return updated


@router.post("/api/office/closures")
def add_holiday_or_vacation_closure(payload: HolidayClosureInput, request: Request):
    require_owner_or_admin(request)
    s_dt = payload.start_date.strip()[:10]
    e_dt = (payload.end_date.strip()[:10] or s_dt)
    if e_dt < s_dt:
        e_dt = s_dt
    title = payload.title.strip()[:80]
    if not s_dt or not title:
        raise HTTPException(400, "Enter a start date and holiday/vacation title.")
    conn = get_conn()
    if payload.id:
        conn.execute(
            """UPDATE holiday_closures
               SET start_date=?, end_date=?, title=?, staffed_open=?, special_hours=?, dock_open=?, public_note=?
               WHERE id=?;""",
            (
                s_dt, e_dt, title, 1 if payload.staffed_open else 0,
                payload.special_hours.strip()[:60], 1 if payload.dock_open else 0,
                payload.public_note.strip()[:200], int(payload.id),
            ),
        )
    else:
        conn.execute(
            """INSERT INTO holiday_closures
               (start_date, end_date, title, staffed_open, special_hours, dock_open, public_note, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?);""",
            (
                s_dt, e_dt, title, 1 if payload.staffed_open else 0,
                payload.special_hours.strip()[:60], 1 if payload.dock_open else 0,
                payload.public_note.strip()[:200], now_eastern().strftime("%Y-%m-%d %I:%M %p"),
            ),
        )
    bump_db_version(conn)
    conn.commit()
    updated = get_operating_schedule_bundle(conn)
    conn.close()
    return updated


@router.delete("/api/office/closures/{closure_id}")
def delete_holiday_or_vacation_closure(closure_id: int, request: Request):
    require_owner_or_admin(request)
    conn = get_conn()
    conn.execute("DELETE FROM holiday_closures WHERE id=?;", (closure_id,))
    bump_db_version(conn)
    conn.commit()
    conn.close()
    return {"deleted": closure_id}


@router.get("/api/sms-outbox")
def list_pending_sms_outbox():
    conn = get_conn()
    rows = [dict(r) for r in conn.execute("SELECT * FROM sms_outbox WHERE status='PENDING' ORDER BY id DESC LIMIT 30;").fetchall()]
    conn.close()
    return rows


@router.post("/api/sms-outbox/{sms_id}/ack")
def ack_sms_outbox_item(sms_id: int):
    conn = get_conn()
    conn.execute("UPDATE sms_outbox SET status='SENT', sent_at=? WHERE id=?;", (now_eastern().strftime("%I:%M %p"), sms_id))
    conn.commit()
    conn.close()
    return {"ok": True}


@router.post("/api/pricing")
def upsert_pricing_rates(rates: list[PricingRateUpsert], request: Request):
    require_owner_or_admin(request)
    conn = get_conn()
    for idx, r in enumerate(rates):
        clean_code = re.sub(r"[^A-Z0-9_]", "_", r.code.strip().upper())[:28]
        if not clean_code:
            continue
        existing = conn.execute("SELECT * FROM pricing_rates WHERE code=?;", (clean_code,)).fetchone()
        desc = (r.description.strip() if r.description is not None else (existing["description"] if existing else clean_code))
        notes = r.notes if r.notes is not None else (existing["notes"] if existing else "")
        s_ord = r.sort_order if r.sort_order is not None else (existing["sort_order"] if existing else (idx + 1) * 10)
        conn.execute(
            """INSERT INTO pricing_rates (code, description, unit_price, notes, sort_order)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(code) DO UPDATE SET
                   description=excluded.description,
                   unit_price=excluded.unit_price,
                   notes=excluded.notes,
                   sort_order=excluded.sort_order;""",
            (clean_code, desc, float(r.unit_price or 0.0), notes, s_ord),
        )
    bump_db_version(conn)
    conn.commit()
    conn.close()
    return {"updated": True}


@router.delete("/api/pricing/{code}")
def delete_pricing_rate(code: str, request: Request):
    require_owner_or_admin(request)
    conn = get_conn()
    conn.execute("DELETE FROM pricing_rates WHERE UPPER(code)=UPPER(?);", (code.strip(),))
    bump_db_version(conn)
    conn.commit()
    conn.close()
    return {"deleted": code}


@router.get("/api/unbilled")
def get_unbilled(customer_name: str, work_date: str = "", all_dates: bool = True):
    conn = get_conn()
    if all_dates or not work_date:
        rows = conn.execute(
            """SELECT * FROM cylinder_records
               WHERE customer_name=? AND status='COMPLETED' AND invoice_id IS NULL
               ORDER BY work_date ASC, id ASC;""",
            (customer_name,),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT * FROM cylinder_records
               WHERE work_date=? AND customer_name=? AND status='COMPLETED' AND invoice_id IS NULL
               ORDER BY id ASC;""",
            (work_date, customer_name),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.post("/api/invoices")
def create_invoice(payload: InvoiceCreateInput):
    if not payload.items:
        raise HTTPException(400, "Add at least one line item to create an invoice.")
    est_today = today_eastern()
    conn = get_conn()
    nxt_num = conn.execute("SELECT COALESCE(MAX(CAST(SUBSTR(invoice_no, -4) AS INTEGER)), 0) + 1 FROM invoices WHERE invoice_no LIKE ?;", (f"INV-{est_today[:4]}-%",)).fetchone()[0]
    inv_no = f"INV-{est_today[:4]}-{nxt_num:04d}"

    phone_clean = clean_phone(payload.customer_phone)
    if not phone_clean:
        ph_row = conn.execute(
            "SELECT phone FROM customers WHERE UPPER(customer_name)=UPPER(?) AND phone!='' LIMIT 1;",
            (payload.customer_name,),
        ).fetchone()
        if ph_row:
            phone_clean = ph_row["phone"]

    upsert_rolodex_from_activity(conn, payload.customer_name, phone_clean)
    subtotal = round(sum(it.qty * it.unit_price for it in payload.items), 2)
    tax_amt = round(subtotal * (payload.tax_rate / 100.0), 2)
    total = round(subtotal + tax_amt, 2)
    created_by = (payload.created_by or "RP").strip().upper()[:4] or "RP"
    paid_ts = now_eastern().strftime("%Y-%m-%d %I:%M %p") if payload.status == "PAID" else ""

    cur = conn.cursor()
    cur.execute(
        """INSERT INTO invoices
           (invoice_no, invoice_date, work_date, customer_name, customer_phone,
            subtotal, tax_rate, tax_amount, total_amount, status, payment_method, paid_at, created_by, notes, synced)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0);""",
        (
            inv_no, est_today, payload.work_date, payload.customer_name, phone_clean,
            subtotal, payload.tax_rate, tax_amt, total, payload.status,
            payload.payment_method.strip(), paid_ts, created_by, payload.notes,
        ),
    )
    inv_id = cur.lastrowid
    for it in payload.items:
        line_tot = round(it.qty * it.unit_price, 2)
        cur.execute(
            """INSERT INTO invoice_items (invoice_id, record_id, description, qty, unit_price, line_total)
               VALUES (?, ?, ?, ?, ?, ?);""",
            (inv_id, it.record_id, it.description, it.qty, it.unit_price, line_tot),
        )
        if it.record_id:
            cur.execute("UPDATE cylinder_records SET invoice_id=?, synced=0 WHERE id=?;", (inv_id, it.record_id))
    bump_db_version(conn)
    conn.commit()
    conn.close()
    trigger_sheets_sync_bg()
    return {"id": inv_id, "invoice_no": inv_no, "total_amount": total}


@router.post("/api/invoices/{inv_id}/status")
def set_invoice_status(inv_id: int, payload: InvoiceStatusInput):
    st = payload.status.strip().upper()
    paid_ts = now_eastern().strftime("%Y-%m-%d %I:%M %p") if st == "PAID" else ""
    conn = get_conn()
    if payload.payment_method is not None:
        conn.execute(
            "UPDATE invoices SET status=?, payment_method=?, paid_at=?, synced=0 WHERE id=?;",
            (st, payload.payment_method.strip(), paid_ts, inv_id),
        )
    else:
        conn.execute(
            "UPDATE invoices SET status=?, paid_at=?, synced=0 WHERE id=?;",
            (st, paid_ts, inv_id),
        )
    bump_db_version(conn)
    conn.commit()
    conn.close()
    trigger_sheets_sync_bg()
    return {"updated": True}


@router.delete("/api/invoices/{inv_id}")
def void_and_delete_invoice(inv_id: int, request: Request):
    require_owner_or_admin(request)
    conn = get_conn()
    inv = conn.execute("SELECT id, invoice_no, status FROM invoices WHERE id=?;", (inv_id,)).fetchone()
    if not inv:
        conn.close()
        raise HTTPException(404, "Invoice not found.")
    if inv["status"] == "PAID":
        conn.close()
        raise HTTPException(400, f"Invoice {inv['invoice_no']} is marked PAID and cannot be deleted.")
    conn.execute("UPDATE cylinder_records SET invoice_id=NULL, synced=0 WHERE invoice_id=?;", (inv_id,))
    conn.execute("DELETE FROM invoice_items WHERE invoice_id=?;", (inv_id,))
    conn.execute("DELETE FROM invoices WHERE id=?;", (inv_id,))
    bump_db_version(conn)
    conn.commit()
    conn.close()
    return {"voided": inv_id}


@router.post("/api/assistant/chat")
async def chat_with_shop_assistant(payload: HelpAssistantInput):
    msg = payload.message.strip()
    if not msg:
        raise HTTPException(400, "Ask a question or describe what happened first.")

    est_today = today_eastern()
    conn = get_conn()
    s_map = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings;").fetchall()}
    api_key = (os.getenv("GEMINI_API_KEY") or s_map.get("gemini_api_key") or "").strip()

    binder = get_binder_status(conn, s_map)
    short_cal = conn.execute(
        "SELECT COALESCE(MAX(target_pressure_psi), 0) FROM daily_verifications WHERE test_date=? AND jacket_type='Short Jacket' AND passed=1;",
        (est_today,),
    ).fetchone()[0]
    tall_cal = conn.execute(
        "SELECT COALESCE(MAX(target_pressure_psi), 0) FROM daily_verifications WHERE test_date=? AND jacket_type='Tall Jacket' AND passed=1;",
        (est_today,),
    ).fetchone()[0]
    queued_cnt = conn.execute("SELECT COUNT(*) FROM cylinder_records WHERE status='QUEUED';").fetchone()[0]
    active_tags = [
        f"#{r['valve_tag']}->SN {r['serial_number']} ({r['customer_name']})"
        for r in conn.execute("SELECT valve_tag, serial_number, customer_name FROM cylinder_records WHERE valve_tag!='' AND valve_reinstalled=0 LIMIT 25;").fetchall()
    ]
    unmarked_cnt = conn.execute(
        "SELECT COUNT(*) FROM cylinder_records WHERE status='COMPLETED' AND hydro_disposition='A' AND COALESCE(rin_marked,0)=0 AND shop_stage!='PICKED_UP';"
    ).fetchone()[0]
    zero_rates = conn.execute("SELECT COUNT(*) FROM pricing_rates WHERE unit_price <= 0;").fetchone()[0]
    open_trucks = conn.execute("SELECT COUNT(*) FROM schedule_jobs WHERE status!='COMPLETED' AND job_type LIKE 'ROAD_%';").fetchone()[0]
    conn.close()

    if not api_key:
        raise HTTPException(503, "Gemini API Key is not set yet. Open Settings (⚙️) on Shop Ops and paste your Gemini key.")

    live_context = (
        f"[LIVE SHOP SNAPSHOT — Today Eastern: {est_today} | User is viewing: {payload.current_page} (tab: {payload.current_tab or 'main'}) | Crew: {payload.submitted_by}]\n"
        f"- Short Jacket Today: {'Calibrated to ' + str(short_cal) + ' PSI (allows <=' + str(dot_max_allowed_test_psi(int(short_cal))) + ' PSI)' if short_cal else 'NOT CALIBRATED TODAY'}\n"
        f"- Tall Jacket Today: {'Calibrated to ' + str(tall_cal) + ' PSI (allows <=' + str(dot_max_allowed_test_psi(int(tall_cal))) + ' PSI)' if tall_cal else 'NOT CALIBRATED TODAY'}\n"
        f"- Active DOT Retest Sheet: #{binder['active_sheet_no']} (Next Row #{binder['next_row_number']} of 12)\n"
        f"- Queued Cylinders Waiting for Stand: {queued_cnt} | Needs B386 Stamp/Sticker: {unmarked_cnt} | Open Truck Runs: {open_trucks}\n"
        f"- Active Valve Tags Currently In Use: {', '.join(active_tags) if active_tags else 'None'}\n"
        f"- Service Rate Codes still at $0.00: {zero_rates}"
    )

    contents: list[dict] = []
    for turn in payload.history[-8:]:
        role = "model" if turn.role == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": turn.text[:1200]}]})
    contents.append({"role": "user", "parts": [{"text": f"{live_context}\n\nCREW QUESTION / REPORT:\n{msg}"}]})

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(call_gemini_help_assistant, api_key, contents),
            timeout=38.0,
        )
    except Exception as exc:
        raise HTTPException(502, f"Help AI temporarily unavailable: {exc}")

    logged_ticket_id = None
    if result.get("log_ticket"):
        ts = now_eastern().strftime("%Y-%m-%d %I:%M %p")
        by = (payload.submitted_by or "RP").strip().upper()[:4] or "RP"
        cat = (result.get("ticket_category") or "Bug / Fix").strip()[:30]
        prio = (result.get("ticket_priority") or "High").strip()[:16]
        summary = (result.get("ticket_summary") or msg).strip()[:360]
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO crew_feedback (created_at, submitted_by, category, page_tab, priority, message, status, synced)
               VALUES (?, ?, ?, ?, ?, ?, 'OPEN', 0);""",
            (ts, by, f"🤖 {cat}", f"{payload.current_page}:{payload.current_tab}", prio, summary),
        )
        logged_ticket_id = cur.lastrowid
        bump_db_version(conn)
        conn.commit()
        conn.close()
        trigger_sheets_sync_bg()

    return {
        "reply": result.get("reply") or "Let me know what happened on screen and I can walk you through it or log a fix for Eric.",
        "logged_ticket": bool(logged_ticket_id),
        "ticket_id": logged_ticket_id,
        "ticket_summary": result.get("ticket_summary") or "",
    }


@router.get("/api/admin/backup-db")
def download_sqlite_backup(request: Request):
    require_owner_or_admin(request)
    import sqlite3 as _sq3
    import tempfile as _tmp
    src_conn = get_conn()
    with _tmp.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        tmp_path = Path(tf.name)
    try:
        dst_conn = _sq3.connect(tmp_path)
        src_conn.backup(dst_conn)
        dst_conn.close()
        src_conn.close()
        data = tmp_path.read_bytes()
    finally:
        tmp_path.unlink(missing_ok=True)
    stamp = today_eastern()
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="webers_hydro_backup_{stamp}.db"'},
    )


@router.get("/api/pdf/merch-qr")
def print_merch_counter_qr_pdf():
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas as rl_canvas
    from reportlab.graphics.barcode import qr
    from reportlab.graphics.shapes import Drawing
    from reportlab.graphics import renderPDF

    conn = get_conn()
    row = conn.execute("SELECT value FROM settings WHERE key='merch_popup_url';").fetchone()
    conn.close()
    target_url = (row["value"].strip() if row and row["value"] else "") or "https://ops.weberstanktesting.com/merch-go"

    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=letter)
    W, H = letter
    cx, cy, cw, ch = (W - 360) / 2, (H - 470) / 2, 360, 470

    c.setStrokeColorRGB(0.05, 0.15, 0.11)
    c.setLineWidth(2.5)
    c.roundRect(cx, cy, cw, ch, 14, stroke=1, fill=0)

    logo_p = STATIC_DIR / "images" / "logo-crest.png"
    if logo_p.exists():
        c.drawImage(str(logo_p), (W - 114) / 2, cy + ch - 130, width=114, height=114, preserveAspectRatio=True, mask="auto")

    c.setFillColorRGB(0.05, 0.15, 0.11)
    c.setFont("Helvetica-Bold", 17)
    c.drawCentredString(W / 2, cy + ch - 154, "WEBER'S CREW & FAMILY GEAR")
    c.setFont("Helvetica", 10)
    c.drawCentredString(W / 2, cy + ch - 170, "Hoodies  •  Polos  •  Work Tees  •  Hats  •  Tumblers")

    qr_code = qr.QrCodeWidget(target_url)
    b = qr_code.getBounds()
    q_size = 185
    d = Drawing(q_size, q_size, transform=[q_size / (b[2] - b[0]), 0, 0, q_size / (b[3] - b[1]), 0, 0])
    d.add(qr_code)
    renderPDF.draw(d, c, (W - q_size) / 2, cy + 88)

    c.setFont("Helvetica-Bold", 11)
    c.drawCentredString(W / 2, cy + 62, "SCAN WITH YOUR PHONE CAMERA")
    c.setFont("Helvetica", 8.5)
    c.setFillColorRGB(0.35, 0.4, 0.45)
    c.drawCentredString(W / 2, cy + 44, "Baden, PA  •  724-266-8008")
    c.showPage()
    c.save()
    return Response(content=buf.getvalue(), media_type="application/pdf")


@router.post("/api/merch/settings")
def update_merch_store_settings(payload: MerchStoreSettingsInput, request: Request):
    require_owner_or_admin(request)
    url = payload.popup_store_url.strip()
    if url and not url.startswith(("http://", "https://")):
        url = "https://" + url
    conn = get_conn()
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('merch_popup_url', ?);", (url,))
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES ('merch_allowance_note', ?);",
        (payload.allowance_note.strip(),),
    )
    bump_db_version(conn)
    conn.commit()
    conn.close()
    return {"ok": True, "popup_store_url": url}


@router.post("/api/theme/settings")
def save_holiday_theme_settings(payload: ThemeSettingsInput, request: Request):
    require_owner_or_admin(request)
    mode = payload.theme_mode.strip().upper()
    if mode not in ("AUTO", "OFF") and mode not in HOLIDAY_CATALOG:
        mode = "AUTO"
    conn = get_conn()
    for k, v in [
        ("theme_mode", mode),
        ("theme_enable_public", "1" if payload.enable_public else "0"),
        ("theme_enable_tv", "1" if payload.enable_tv else "0"),
        ("theme_enable_ops", "1" if payload.enable_ops else "0"),
        ("theme_custom_dog_msg", payload.custom_dog_message.strip()[:260]),
    ]:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?);", (k, v))
    bump_db_version(conn)
    data = compute_active_theme(conn)
    conn.commit()
    conn.close()
    return data


@router.get("/api/feedback")
def list_crew_feedback():
    conn = get_conn()
    rows = [dict(r) for r in conn.execute("SELECT * FROM crew_feedback ORDER BY id DESC LIMIT 50;").fetchall()]
    conn.close()
    return rows


@router.post("/api/feedback")
def submit_crew_feedback(payload: CrewFeedbackInput):
    msg = payload.message.strip()
    if not msg:
        raise HTTPException(400, "Enter a note, bug report, or idea first.")
    ts = now_eastern().strftime("%Y-%m-%d %I:%M %p")
    by = (payload.submitted_by or "RP").strip().upper()[:4] or "RP"
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO crew_feedback (created_at, submitted_by, category, page_tab, priority, message, status, synced)
           VALUES (?, ?, ?, ?, ?, ?, 'OPEN', 0);""",
        (ts, by, payload.category.strip(), payload.page_tab.strip(), payload.priority.strip(), msg),
    )
    fb_id = cur.lastrowid
    bump_db_version(conn)
    conn.commit()
    row = dict(conn.execute("SELECT * FROM crew_feedback WHERE id=?;", (fb_id,)).fetchone())
    conn.close()
    trigger_sheets_sync_bg()
    return row


@router.post("/api/feedback/{fb_id}/status")
def update_feedback_status(fb_id: int, payload: FeedbackStatusInput):
    conn = get_conn()
    conn.execute("UPDATE crew_feedback SET status=?, synced=0 WHERE id=?;", (payload.status.strip().upper(), fb_id))
    bump_db_version(conn)
    conn.commit()
    conn.close()
    trigger_sheets_sync_bg()
    return {"updated": fb_id}


@router.get("/api/pdf/retest")
def download_retest_pdf(work_date: str = "", customer: str = "", sheet_no: Optional[int] = None, by_sheet: bool = False):
    conn = get_conn()
    s_map = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings;").fetchall()}
    if (by_sheet or not work_date) and sheet_no:
        sql = (
            "SELECT * FROM cylinder_records WHERE sheet_no=? AND service_mode='HYDRO_AND_VISUAL' AND status='COMPLETED'"
            + (" AND customer_name=?" if customer else "")
            + " ORDER BY id ASC;"
        )
        rows = conn.execute(sql, (sheet_no, customer) if customer else (sheet_no,)).fetchall()
        eff_date = rows[-1]["work_date"] if rows else (work_date or today_eastern())
        fname = f"DOT_Form_393-001_Sheet_{sheet_no}.pdf"
    else:
        eff_date = work_date or today_eastern()
        sql = "SELECT * FROM cylinder_records WHERE work_date=? AND status='COMPLETED'" + (" AND customer_name=?" if customer else "") + " ORDER BY id ASC;"
        rows = conn.execute(sql, (eff_date, customer) if customer else (eff_date,)).fetchall()
        fname = f"DOT_Retest_Sheet_{eff_date}.pdf"
    conn.close()
    pdf_bytes = build_retest_datasheet_pdf(
        work_date=eff_date,
        records=[dict(r) for r in rows],
        dot_rin=s_map.get("dot_rin", "B386"),
        tc_reg=s_map.get("tc_reg", ""),
        base_sheet_no=sheet_no or int(s_map.get("current_sheet_no") or s_map.get("starting_sheet_no") or 8095),
    )
    return Response(content=pdf_bytes, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{fname}"'})


@router.get("/api/pdf/verification")
def download_verification_pdf(work_date: str):
    conn = get_conn()
    s_map = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings;").fetchall()}
    rows = conn.execute("SELECT * FROM daily_verifications WHERE test_date=? ORDER BY id ASC;", (work_date,)).fetchall()
    conn.close()
    pdf_bytes = build_verification_pdf(work_date=work_date, verifications=[dict(r) for r in rows], cal_serial=s_map.get("calibrated_serial", "1SF11904"))
    return Response(content=pdf_bytes, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="DOT_Verification_{work_date}.pdf"'})


@router.get("/api/pdf/visual")
def download_visual_pdf(work_date: str, customer: str = ""):
    conn = get_conn()
    s_map = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings;").fetchall()}
    sql = "SELECT * FROM cylinder_records WHERE work_date=? AND status='COMPLETED'" + (" AND customer_name=?" if customer else "") + " ORDER BY id ASC;"
    rows = conn.execute(sql, (work_date, customer) if customer else (work_date,)).fetchall()
    conn.close()
    pdf_bytes = build_visual_log_pdf(work_date=work_date, records=[dict(r) for r in rows], dot_rin=s_map.get("dot_rin", "B386"))
    return Response(content=pdf_bytes, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="DOT_Visual_Log_{work_date}.pdf"'})


@router.get("/api/pdf/stickers")
def download_stickers_pdf(work_date: str, count: int = 30, skip: int = 0, compact: bool = False):
    conn = get_conn()
    rin = conn.execute("SELECT value FROM settings WHERE key='dot_rin';").fetchone()["value"]
    conn.close()
    pdf_bytes = build_rin_stickers_pdf(work_date=work_date, count=count, dot_rin=rin, skip_slots=skip, compact=compact)
    suffix = "Mini_HPA" if compact else "Standard"
    return Response(content=pdf_bytes, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="B386_Stickers_{suffix}_{work_date}.pdf"'})


@router.get("/api/pdf/valvetags")
def download_valve_tags_pdf(work_date: str, customer: str = ""):
    conn = get_conn()
    rin = conn.execute("SELECT value FROM settings WHERE key='dot_rin';").fetchone()["value"]
    if customer:
        rows = conn.execute("SELECT * FROM cylinder_records WHERE work_date=? AND customer_name=? ORDER BY id ASC;", (work_date, customer)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM cylinder_records WHERE valve_tag != '' AND valve_reinstalled = 0 ORDER BY id ASC;").fetchall()
    conn.close()
    pdf_bytes = build_valve_tags_pdf(work_date=work_date, records=[dict(r) for r in rows], dot_rin=rin)
    return Response(content=pdf_bytes, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="Valve_Tag_Sheet_{work_date}.pdf"'})


@router.get("/api/pdf/poster")
def download_backroom_poster_pdf(checkin_url: str = "https://weberstanktesting.com/#dropoff"):
    conn = get_conn()
    s_map = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings;").fetchall()}
    conn.close()
    pdf_bytes = build_backroom_poster_pdf(checkin_url=checkin_url, phone=s_map.get("phone", "724-266-8008"), dot_rin=s_map.get("dot_rin", "B386"))
    return Response(content=pdf_bytes, media_type="application/pdf", headers={"Content-Disposition": 'inline; filename="Webers_Backroom_DropOff_QR_Sign.pdf"'})


@router.get("/api/pdf/claim-slip")
def download_shelf_claim_slip_pdf(customer: str, work_date: str = ""):
    conn = get_conn()
    s_map = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings;").fetchall()}
    rows = conn.execute(
        "SELECT * FROM cylinder_records WHERE customer_name=? AND status='COMPLETED' AND shop_stage != 'PICKED_UP' ORDER BY id ASC;",
        (customer,),
    ).fetchall()
    if not rows and work_date:
        rows = conn.execute(
            "SELECT * FROM cylinder_records WHERE customer_name=? AND work_date=? AND status='COMPLETED' ORDER BY id ASC;",
            (customer, work_date),
        ).fetchall()
    if not rows:
        rows = conn.execute(
            "SELECT * FROM cylinder_records WHERE customer_name=? AND status='COMPLETED' ORDER BY id DESC LIMIT 25;",
            (customer,),
        ).fetchall()

    rolo = conn.execute("SELECT * FROM customers WHERE UPPER(customer_name)=UPPER(?) LIMIT 1;", (customer,)).fetchone()
    inv = conn.execute("SELECT * FROM invoices WHERE customer_name=? ORDER BY id DESC LIMIT 1;", (customer,)).fetchone()
    conn.close()

    recs = [dict(r) for r in rows]
    ph = (rolo["phone"] if rolo and rolo["phone"] else "") or next((r["customer_phone"] for r in recs if r.get("customer_phone")), "")
    terms = rolo["payment_terms"] if rolo else "DUE_AT_PICKUP"
    pdf_bytes = build_shelf_claim_slip_pdf(
        customer_name=customer,
        customer_phone=ph,
        records=recs,
        invoice=dict(inv) if inv else None,
        payment_terms=terms,
        phone=s_map.get("phone", "724-266-8008"),
        dot_rin=s_map.get("dot_rin", "B386"),
        pickup_url=f"https://weberstanktesting.com/?mode=pickup&customer={urllib.parse.quote(customer)}#dropoff",
    )
    safe_c = re.sub(r"[^A-Za-z0-9_-]", "_", customer)[:24]
    return Response(content=pdf_bytes, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="BackRoom_Claim_Slip_{safe_c}.pdf"'})


@router.get("/api/pdf/invoice/{inv_id}")
def download_invoice_pdf(inv_id: int):
    conn = get_conn()
    inv = conn.execute("SELECT * FROM invoices WHERE id=?;", (inv_id,)).fetchone()
    if not inv:
        conn.close()
        raise HTTPException(404, "Invoice not found.")
    items = [dict(r) for r in conn.execute("SELECT * FROM invoice_items WHERE invoice_id=? ORDER BY id;", (inv_id,)).fetchall()]
    rolo = conn.execute("SELECT * FROM customers WHERE UPPER(customer_name)=UPPER(?) LIMIT 1;", (inv["customer_name"],)).fetchone()
    conn.close()
    pdf_bytes = build_invoice_pdf(dict(inv), items, customer_profile=dict(rolo) if rolo else None)
    return Response(content=pdf_bytes, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{inv["invoice_no"]}.pdf"'})


@router.get("/api/cabinet/index")
def get_filing_cabinet_index(year: str = ""):
    yr_filter = year.strip()[:4] if (year and year.strip().isdigit() and len(year.strip()) == 4) else ""
    conn = get_conn()
    s_map = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings;").fetchall()}

    # 1. 12-Row Retest Sheets (grouped by exact sheet_no across multi-day spans!)
    sheet_sql = """
        SELECT sheet_no,
               COUNT(*) AS row_count,
               MIN(work_date) AS first_date,
               MAX(work_date) AS last_date,
               SUM(CASE WHEN hydro_disposition='A' THEN 1 ELSE 0 END) AS pass_count,
               SUM(CASE WHEN hydro_disposition!='A' THEN 1 ELSE 0 END) AS condemn_count,
               GROUP_CONCAT(DISTINCT NULLIF(TRIM(customer_name), '')) AS customers,
               MIN(serial_number) AS first_sn,
               MAX(serial_number) AS last_sn
        FROM cylinder_records
        WHERE service_mode='HYDRO_AND_VISUAL' AND status='COMPLETED' AND sheet_no > 0
        GROUP BY sheet_no
        ORDER BY sheet_no DESC;
    """
    all_sheets = [dict(r) for r in conn.execute(sheet_sql).fetchall()]
    if yr_filter:
        all_sheets = [s for s in all_sheets if str(s["last_date"] or "").startswith(yr_filter) or str(s["first_date"] or "").startswith(yr_filter)]
    for sh in all_sheets:
        sh["sealed"] = int(sh["row_count"] or 0) >= 12

    # 2. Daily Water-Jacket Calibrations (Method B)
    cal_sql = """
        SELECT test_date,
               MAX(CASE WHEN jacket_type='Short Jacket' AND passed=1 THEN target_pressure_psi ELSE 0 END) AS short_cal_psi,
               MAX(CASE WHEN jacket_type='Tall Jacket' AND passed=1 THEN target_pressure_psi ELSE 0 END) AS tall_cal_psi,
               MAX(calibrated_serial) AS calibrated_serial,
               COUNT(*) AS step_count,
               MIN(passed) AS all_passed,
               MAX(test_time) AS last_time
        FROM daily_verifications
        GROUP BY test_date
        ORDER BY test_date DESC;
    """
    cal_days = [dict(r) for r in conn.execute(cal_sql).fetchall()]
    if yr_filter:
        cal_days = [c for c in cal_days if str(c["test_date"] or "").startswith(yr_filter)]

    # 3. Visual & Propane Inspection Logs by Date + Condemned Cylinder Register
    vis_sql = """
        SELECT work_date,
               COUNT(*) AS total_count,
               SUM(CASE WHEN service_mode='VISUAL_ONLY' THEN 1 ELSE 0 END) AS visual_only_count,
               SUM(CASE WHEN gas_service='LPG' OR dot_spec LIKE '4B%' THEN 1 ELSE 0 END) AS propane_count,
               SUM(CASE WHEN hydro_disposition='A' AND visual_pass=1 THEN 1 ELSE 0 END) AS pass_count,
               SUM(CASE WHEN hydro_disposition!='A' OR visual_pass=0 THEN 1 ELSE 0 END) AS fail_count,
               GROUP_CONCAT(DISTINCT NULLIF(TRIM(customer_name), '')) AS customers
        FROM cylinder_records
        WHERE status='COMPLETED'
        GROUP BY work_date
        ORDER BY work_date DESC;
    """
    vis_days = [dict(r) for r in conn.execute(vis_sql).fetchall()]
    if yr_filter:
        vis_days = [v for v in vis_days if str(v["work_date"] or "").startswith(yr_filter)]

    condemned = [
        dict(r)
        for r in conn.execute(
            """SELECT id, work_date, sheet_no, customer_name, serial_number, dot_spec, service_psi,
                      hydro_disposition, condemn_action, notes
               FROM cylinder_records
               WHERE status='COMPLETED' AND (hydro_disposition!='A' OR visual_pass=0)
               ORDER BY work_date DESC, id DESC LIMIT 300;"""
        ).fetchall()
    ]
    if yr_filter:
        condemned = [c for c in condemned if str(c["work_date"] or "").startswith(yr_filter)]

    # 4. Customer Invoices & Loading Dock BR-XXXX Tickets
    invs = [
        dict(r)
        for r in conn.execute(
            """SELECT i.*, (SELECT COUNT(*) FROM invoice_items it WHERE it.invoice_id=i.id) AS line_count
               FROM invoices i ORDER BY i.id DESC LIMIT 300;"""
        ).fetchall()
    ]
    if yr_filter:
        invs = [iv for iv in invs if str(iv["invoice_date"] or "").startswith(yr_filter)]

    tickets = [
        dict(r)
        for r in conn.execute("SELECT * FROM dropoffs ORDER BY id DESC LIMIT 200;").fetchall()
    ]
    if yr_filter:
        tickets = [t for t in tickets if str(t["work_date"] or "").startswith(yr_filter)]
    for t in tickets:
        t["ticket_no"] = f"BR-{int(t['id']):04d}"
        digits = re.sub(r"\D", "", t.get("customer_phone") or "")
        t["phone_last4"] = digits[-4:] if len(digits) >= 4 else ""

    # 5. Worker Timecards Grouped by Month (YYYY-MM) + Recent Shifts
    tc_months = [
        dict(r)
        for r in conn.execute(
            """SELECT SUBSTR(work_date, 1, 7) AS ym,
                      COUNT(*) AS shift_count,
                      COUNT(DISTINCT crew_initials) AS worker_count,
                      COALESCE(SUM(duration_minutes), 0) AS total_minutes
               FROM time_punches
               WHERE status='CLOSED'
               GROUP BY SUBSTR(work_date, 1, 7)
               ORDER BY ym DESC;"""
        ).fetchall()
    ]
    if yr_filter:
        tc_months = [tm for tm in tc_months if str(tm["ym"] or "").startswith(yr_filter)]
    for tm in tc_months:
        tm["total_hours"] = round(int(tm["total_minutes"] or 0) / 60.0, 2)

    recent_punches = [
        dict(r)
        for r in conn.execute("SELECT * FROM time_punches ORDER BY id DESC LIMIT 120;").fetchall()
    ]
    if yr_filter:
        recent_punches = [p for p in recent_punches if str(p["work_date"] or "").startswith(yr_filter)]

    # Available years for quick filter dropdown
    years_rows = conn.execute(
        """SELECT DISTINCT SUBSTR(d, 1, 4) AS yr FROM (
               SELECT work_date AS d FROM cylinder_records
               UNION SELECT test_date AS d FROM daily_verifications
               UNION SELECT invoice_date AS d FROM invoices
               UNION SELECT work_date AS d FROM time_punches
           ) WHERE LENGTH(d) >= 4 ORDER BY yr DESC;"""
    ).fetchall()
    avail_years = [r["yr"] for r in years_rows if r["yr"] and r["yr"].isdigit()]
    cur_yr = today_eastern()[:4]
    if cur_yr not in avail_years:
        avail_years.insert(0, cur_yr)

    unsynced = conn.execute(
        "SELECT (SELECT COUNT(*) FROM daily_verifications WHERE synced=0) + "
        "(SELECT COUNT(*) FROM cylinder_records WHERE synced=0 AND status='COMPLETED') + "
        "(SELECT COUNT(*) FROM invoices WHERE synced=0) + "
        "(SELECT COUNT(*) FROM dropoffs WHERE synced=0);"
    ).fetchone()[0]
    conn.close()

    return {
        "selected_year": yr_filter or "ALL",
        "available_years": avail_years,
        "retest_sheets": all_sheets,
        "calibration_days": cal_days,
        "visual_days": vis_days,
        "condemned_records": condemned,
        "invoices": invs,
        "tickets": tickets,
        "timecard_months": tc_months,
        "recent_punches": recent_punches,
        "stats": {
            "total_sheets": len(all_sheets),
            "sealed_sheets": sum(1 for s in all_sheets if s["sealed"]),
            "cal_days_count": len(cal_days),
            "condemned_count": len(condemned),
            "invoices_count": len(invs),
            "tickets_count": len(tickets),
            "unsynced_count": unsynced,
            "has_sheets_webhook": bool((s_map.get("sheets_webhook_url") or "").strip()),
            "master_sheet_url": (s_map.get("master_sheet_url") or "").strip(),
        },
    }


@router.get("/api/cabinet/audit-pack.zip")
def download_complete_audit_packet_zip(request: Request, year: str = ""):
    require_owner_or_admin(request)
    import sqlite3 as _sq3
    import tempfile as _tmp
    import zipfile as _zf

    yr_filter = year.strip()[:4] if (year and year.strip().isdigit() and len(year.strip()) == 4) else ""
    scope_lbl = yr_filter or "ALL_YEARS"
    today_iso = today_eastern()

    conn = get_conn()
    s_map = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings;").fetchall()}
    dot_rin = s_map.get("dot_rin", "B386")
    tc_reg = s_map.get("tc_reg", "")
    cal_sn = s_map.get("calibrated_serial", "1SF11904")

    mem_zip = io.BytesIO()
    with _zf.ZipFile(mem_zip, mode="w", compression=_zf.ZIP_DEFLATED) as zf:
        # 1. Every Form 393-001 12-Row Retest Sheet PDF (grouped by sheet_no)
        sheet_nums = [
            r["sheet_no"]
            for r in conn.execute(
                "SELECT DISTINCT sheet_no, MAX(work_date) AS ld FROM cylinder_records WHERE service_mode='HYDRO_AND_VISUAL' AND status='COMPLETED' AND sheet_no > 0 GROUP BY sheet_no ORDER BY sheet_no ASC;"
            ).fetchall()
            if (not yr_filter or str(r["ld"] or "").startswith(yr_filter))
        ]
        for sn in sheet_nums[:500]:
            s_rows = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM cylinder_records WHERE sheet_no=? AND service_mode='HYDRO_AND_VISUAL' AND status='COMPLETED' ORDER BY id ASC;",
                    (sn,),
                ).fetchall()
            ]
            if s_rows:
                ld = s_rows[-1]["work_date"]
                pdf_b = build_retest_datasheet_pdf(work_date=ld, records=s_rows, dot_rin=dot_rin, tc_reg=tc_reg, base_sheet_no=sn)
                zf.writestr(f"01_DOT_Retest_Sheets_Form_393-001/Sheet_{sn}_{ld}.pdf", pdf_b)

        # 2. Daily Water-Jacket Calibrations (Method B PDFs)
        cal_dates = [
            r["test_date"]
            for r in conn.execute("SELECT DISTINCT test_date FROM daily_verifications ORDER BY test_date ASC;").fetchall()
            if (not yr_filter or str(r["test_date"] or "").startswith(yr_filter))
        ]
        for cd in cal_dates[:400]:
            v_rows = [dict(r) for r in conn.execute("SELECT * FROM daily_verifications WHERE test_date=? ORDER BY id ASC;", (cd,)).fetchall()]
            if v_rows:
                zf.writestr(
                    f"02_Daily_Jacket_Verifications/Verification_{cd}.pdf",
                    build_verification_pdf(work_date=cd, verifications=v_rows, cal_serial=cal_sn),
                )

        # 3. Daily 7-Point Visual & Propane Inspection Logs
        vis_dates = [
            r["work_date"]
            for r in conn.execute("SELECT DISTINCT work_date FROM cylinder_records WHERE status='COMPLETED' ORDER BY work_date ASC;").fetchall()
            if (not yr_filter or str(r["work_date"] or "").startswith(yr_filter))
        ]
        for vd in vis_dates[:400]:
            vr = [dict(r) for r in conn.execute("SELECT * FROM cylinder_records WHERE work_date=? AND status='COMPLETED' ORDER BY id ASC;", (vd,)).fetchall()]
            if vr:
                zf.writestr(f"03_Visual_and_Propane_Logs/Visual_Log_{vd}.pdf", build_visual_log_pdf(work_date=vd, records=vr, dot_rin=dot_rin))

        # 4. Condemned Cylinders CSV Register
        cond_buf = io.StringIO()
        cw = csv.writer(cond_buf)
        cw.writerow(["Test Date", "Sheet #", "Customer", "Serial #", "DOT Spec", "Service PSI", "Disposition", "Legal Condemn Action", "Notes"])
        for cr in conn.execute("SELECT * FROM cylinder_records WHERE status='COMPLETED' AND (hydro_disposition!='A' OR visual_pass=0) ORDER BY work_date DESC, id DESC;").fetchall():
            if not yr_filter or str(cr["work_date"] or "").startswith(yr_filter):
                cw.writerow([cr["work_date"], cr["sheet_no"], cr["customer_name"], cr["serial_number"], cr["dot_spec"], cr["service_psi"], cr["hydro_disposition"], cr["condemn_action"], cr["notes"]])
        zf.writestr("04_Condemned_Cylinders_Register.csv", cond_buf.getvalue())

        # 5. Customer Invoices (PDFs + Ledger CSV)
        inv_buf = io.StringIO()
        iw = csv.writer(inv_buf)
        iw.writerow(["Invoice #", "Invoice Date", "Work Date", "Customer", "Phone", "Subtotal", "Tax", "Total", "Status", "Payment Method", "Paid At"])
        invs_all = conn.execute("SELECT * FROM invoices ORDER BY id ASC;").fetchall()
        for iv in invs_all:
            if yr_filter and not str(iv["invoice_date"] or "").startswith(yr_filter):
                continue
            iw.writerow([iv["invoice_no"], iv["invoice_date"], iv["work_date"], iv["customer_name"], iv["customer_phone"], iv["subtotal"], iv["tax_amount"], iv["total_amount"], iv["status"], iv["payment_method"], iv["paid_at"]])
            items = [dict(x) for x in conn.execute("SELECT * FROM invoice_items WHERE invoice_id=? ORDER BY id;", (iv["id"],)).fetchall()]
            rolo = conn.execute("SELECT * FROM customers WHERE UPPER(customer_name)=UPPER(?) LIMIT 1;", (iv["customer_name"],)).fetchone()
            zf.writestr(f"05_Customer_Invoices/{iv['invoice_no']}.pdf", build_invoice_pdf(dict(iv), items, customer_profile=dict(rolo) if rolo else None))
        zf.writestr("05_Customer_Invoices/Invoices_Ledger.csv", inv_buf.getvalue())

        # 6. Worker Timecards CSV
        tc_buf = io.StringIO()
        tw = csv.writer(tc_buf)
        tw.writerow(["Date", "Worker", "Initials", "Clock In", "Clock Out", "Minutes", "Hours", "Status", "Notes"])
        for tp in conn.execute("SELECT * FROM time_punches ORDER BY work_date DESC, id DESC;").fetchall():
            if not yr_filter or str(tp["work_date"] or "").startswith(yr_filter):
                mins = int(tp["duration_minutes"] or 0)
                tw.writerow([tp["work_date"], tp["crew_name"], tp["crew_initials"], tp["clock_in_at"], tp["clock_out_at"], mins, round(mins / 60.0, 2), tp["status"], tp["notes"]])
        zf.writestr(f"06_Worker_Timecards/Timecards_{scope_lbl}.csv", tc_buf.getvalue())

        # 7. Master Cylinder CSV + Full SQLite .db Snapshot
        mc_buf = io.StringIO()
        mw = csv.writer(mc_buf)
        mw.writerow(["ID", "Test Date", "Sheet #", "Customer", "Serial #", "MFG", "MFG Date", "DOT Spec", "Service PSI", "Test PSI", "Total cc", "Perm cc", "Elastic cc", "% Perm", "REE", "10-Yr Star", "Disp", "RIN Mark", "Operator", "Notes"])
        for rec in conn.execute("SELECT * FROM cylinder_records WHERE status='COMPLETED' ORDER BY id ASC;").fetchall():
            if not yr_filter or str(rec["work_date"] or "").startswith(yr_filter):
                mw.writerow([rec["id"], rec["work_date"], rec["sheet_no"], rec["customer_name"], rec["serial_number"], rec["mfg_id"], rec["mfg_date"], rec["dot_spec"], rec["service_psi"], rec["test_psi"], rec["total_cc"], rec["permanent_cc"], rec["elastic_cc"], rec["percent_permanent"], rec["ree_limit"], "Yes" if rec["star_10yr"] else "No", rec["hydro_disposition"], rec["rin_mark_type"], rec["operator_initials"], rec["notes"]])
        zf.writestr("07_Master_Database_Backup/Master_Completed_Cylinders.csv", mc_buf.getvalue())

        with _tmp.NamedTemporaryFile(suffix=".db", delete=False) as tf:
            tmp_db_p = Path(tf.name)
        try:
            dst = _sq3.connect(tmp_db_p)
            conn.backup(dst)
            dst.close()
            zf.writestr(f"07_Master_Database_Backup/webers_hydro_backup_{today_iso}.db", tmp_db_p.read_bytes())
        finally:
            tmp_db_p.unlink(missing_ok=True)

    conn.close()
    return Response(
        content=mem_zip.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="Webers_DOT_Filing_Cabinet_{scope_lbl}_{today_iso}.zip"'},
    )
