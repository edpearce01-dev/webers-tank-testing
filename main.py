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
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from database import bump_db_version, get_conn, init_db, wipe_all_operational_data
from pdf_generator import (
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

STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(exist_ok=True)

COOKIE_NAME = "webers_crew_session"
CACHED_VISION_MODEL: Optional[str] = None
SHEETS_SYNC_LOCK = asyncio.Lock()

NO_CACHE_HEADERS = {
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Pragma": "no-cache",
    "Expires": "0",
}

SHIFT_MODES = {
    "FULL_HYDRO": {"label": "🟢 Full Hydro & Prep Window", "default_cap": 24},
    "PREP_ONLY": {"label": "🔧 Prep, Devalve & Drop-Off Only", "default_cap": 0},
    "ROAD_RUN": {"label": "🚚 Road Pickup / Delivery & Partial Hydro", "default_cap": 10},
    "BACKROOM_ONLY": {"label": "🚪 24/7 Backroom Self-Drop Only", "default_cap": 0},
}

HOLDING_ZONES = {
    "ZONE_A_BACKROOM": "Zone A • Backroom Drop-Off",
    "ZONE_B_DEVALVE": "Zone B • Devalve & Tag Bench",
    "ZONE_C_READY_RACK": "Zone C • Test Room Ready Rack",
    "ZONE_D_DRY_STAMP": "Zone D • Dryer & Stamp/Sticker",
    "ZONE_E_PICKUP_SHELF": "Zone E • Backroom Pickup Shelf",
    "OUTBOUND_TRUCK": "🚚 Staged for Road Delivery",
    "PICKED_UP": "✓ Released / Delivered",
}

GAS_CODE_MAP = {
    "UN1956": "AIR",
    "UN1002": "AIR",
    "COMPRESSED AIR": "AIR",
    "BREATHING AIR": "AIR",
    "AIR COMPRESSED": "AIR",
    "HPA": "AIR",
    "N2/AIR": "AIR",
    "UN1072": "O2",
    "OXYGEN": "O2",
    "MEDICAL OXYGEN": "O2",
    "UN1013": "CO2",
    "CARBON DIOXIDE": "CO2",
    "UN1066": "N2",
    "NITROGEN": "N2",
    "UN1006": "AR",
    "ARGON": "AR",
    "UN1046": "HE",
    "HELIUM": "HE",
    "UN1954": "MIX",
    "C25": "AR/CO2",
    "75/25": "AR/CO2",
}

DEPT_KEYWORDS = ("VFD", " FD", "FIRE", "EMS", "AMBULANCE", "RESCUE", "BORO", "TOWNSHIP", "TWP", "MUNICIPAL")


def now_eastern() -> datetime:
    try:
        return datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        utc_now = datetime.now(timezone.utc)
        yr = utc_now.year
        mar1 = datetime(yr, 3, 1, tzinfo=timezone.utc)
        dst_start = mar1 + timedelta(days=(6 - mar1.weekday()) % 7 + 7, hours=7)
        nov1 = datetime(yr, 11, 1, tzinfo=timezone.utc)
        dst_end = nov1 + timedelta(days=(6 - nov1.weekday()) % 7, hours=6)
        offset = -4 if dst_start <= utc_now < dst_end else -5
        return utc_now.astimezone(timezone(timedelta(hours=offset)))


def today_eastern() -> str:
    return now_eastern().date().isoformat()


def parse_iso_date(raw: Optional[str]) -> Optional[date]:
    if not raw:
        return None
    m = re.match(r"^(\d{4}-\d{2}-\d{2})", str(raw).strip())
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y-%m-%d").date()
    except Exception:
        return None


def days_since(raw_date_or_ts: Optional[str], ref_iso: str) -> int:
    d0 = parse_iso_date(raw_date_or_ts)
    d_ref = parse_iso_date(ref_iso) or now_eastern().date()
    if not d0:
        return 0
    return max(0, (d_ref - d0).days)


def days_until(raw_date: Optional[str], ref_iso: str) -> Optional[int]:
    d_target = parse_iso_date(raw_date)
    d_ref = parse_iso_date(ref_iso) or now_eastern().date()
    if not d_target:
        return None
    return (d_target - d_ref).days


def normalize_gas_service(raw: Optional[str], fallback: str = "AIR") -> str:
    clean = re.sub(r"\s+", " ", (raw or "").strip().upper())
    if not clean:
        return fallback
    if clean in GAS_CODE_MAP:
        return GAS_CODE_MAP[clean]
    for k, mapped in GAS_CODE_MAP.items():
        if k in clean:
            return mapped
    if re.match(r"^UN\d{4}$", clean):
        return fallback
    return clean[:10]


def clean_phone(raw: Optional[str]) -> str:
    if not raw:
        return ""
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) == 10:
        return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"
    return raw.strip()[:20]


def split_mailing_address(raw_addr: str, city: str = "", state: str = "", zip_code: str = "") -> tuple[str, str, str, str]:
    """Splits a combined '123 Main St, Baden, PA 15005' string or explicit fields into (street, city, state, zip)."""
    st_line = (raw_addr or "").strip()
    c_val = (city or "").strip()
    s_val = (state or "").strip().upper()[:4]
    z_val = (zip_code or "").strip()[:12]

    if st_line and not c_val and "," in st_line:
        parts = [p.strip() for p in st_line.split(",") if p.strip()]
        if len(parts) >= 2:
            st_line = parts[0]
            tail = " ".join(parts[1:])
            m_zip = re.search(r"\b(\d{5}(?:-\d{4})?)\b", tail)
            if m_zip and not z_val:
                z_val = m_zip.group(1)
                tail = (tail[:m_zip.start()] + tail[m_zip.end():]).strip(" ,")
            m_st = re.search(r"\b([A-Za-z]{2})\s*$", tail)
            if m_st and not s_val:
                s_val = m_st.group(1).upper()
                tail = tail[:m_st.start()].strip(" ,")
            c_val = tail.strip(" ,")

    return (st_line[:120], c_val[:60], s_val or "PA", z_val)


def upsert_rolodex_from_activity(
    conn,
    customer_name: str,
    phone: str = "",
    account_type: str = "",
    contact_person: str = "",
    email: str = "",
    street_address: str = "",
    city: str = "",
    state: str = "",
    zip_code: str = "",
) -> None:
    """Automatically creates or enriches a Customer Rolodex entry (including mailing address) whenever a job or tank is logged."""
    cname = (customer_name or "").strip()
    if not cname:
        return
    ph = clean_phone(phone)
    cp = (contact_person or "").strip()[:60]
    em = (email or "").strip()[:100]
    st_clean, ci_clean, st_abbr, zp_clean = split_mailing_address(street_address, city, state, zip_code)

    up = cname.upper()
    is_dept = account_type.upper() == "FIRE_EMS" or any(k in up for k in DEPT_KEYWORDS)
    eff_acct = account_type.upper() if account_type else ("FIRE_EMS" if is_dept else "WALK_IN")
    terms = "NET_30" if eff_acct in ("FIRE_EMS", "COMMERCIAL") else "DUE_AT_PICKUP"
    tax_ex = 1 if eff_acct == "FIRE_EMS" else 0
    ts = now_eastern().strftime("%Y-%m-%d %I:%M %p")

    existing = conn.execute(
        "SELECT id, phone, contact_person, email, street_address, city, state, zip_code, account_type FROM customers WHERE UPPER(customer_name)=UPPER(?);",
        (cname,),
    ).fetchone()
    if not existing:
        conn.execute(
            """INSERT OR IGNORE INTO customers
               (customer_name, account_type, contact_person, phone, email, street_address, city, state, zip_code, payment_terms, tax_exempt, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);""",
            (cname, eff_acct, cp, ph, em, st_clean, ci_clean, st_abbr, zp_clean, terms, tax_ex, ts),
        )
    else:
        new_ph = ph or existing["phone"]
        new_cp = cp or existing["contact_person"]
        new_em = em or existing["email"]
        new_st = st_clean or existing["street_address"]
        new_ci = ci_clean or existing["city"]
        new_sa = (state.strip().upper()[:4] if state.strip() else "") or existing["state"] or "PA"
        new_zp = zp_clean or existing["zip_code"]
        if (new_ph, new_cp, new_em, new_st, new_ci, new_sa, new_zp) != (
            existing["phone"], existing["contact_person"], existing["email"],
            existing["street_address"], existing["city"], existing["state"], existing["zip_code"]
        ):
            conn.execute(
                """UPDATE customers
                   SET phone=?, contact_person=?, email=?, street_address=?, city=?, state=?, zip_code=?, updated_at=?
                   WHERE id=?;""",
                (new_ph, new_cp, new_em, new_st, new_ci, new_sa, new_zp, ts, existing["id"]),
            )


def get_compliance_alerts(s_map: dict, ref_today: str) -> list[dict]:
    """Tracks DOT RIN B386 5-Year Renewal and Master Pressure Gauge Calibration Cert deadlines."""
    alerts = []
    checks = [
        ("rin_expires_date", "DOT RIN B386 License Renewal"),
        ("gauge_cert_expires_date", "Master PID Gauge Calibration Cert"),
    ]
    for key, title in checks:
        raw = (s_map.get(key) or "").strip()
        if not raw:
            continue
        rem = days_until(raw, ref_today)
        if rem is None:
            continue
        level = "EXPIRED" if rem < 0 else ("WARN" if rem <= 90 else "OK")
        alerts.append({
            "key": key,
            "title": title,
            "expires_date": raw,
            "days_remaining": rem,
            "level": level,
        })
    return alerts


def evaluate_composite_life(dot_spec: str, mfg_date: str, ref_iso_date: Optional[str] = None) -> dict:
    sp = (dot_spec or "").strip().upper().replace("SP-", "SP")
    is_comp = sp.startswith("SP") or sp.startswith("E-") or "CCM" in sp or "CFFC" in sp
    if not is_comp:
        return {"is_composite": False, "expired": False, "expiring_soon": False, "warning": None}

    raw_d = (mfg_date or "").strip().lstrip("'")
    m = re.match(r"^(\d{1,2})\s*[-/\.]\s*(\d{2,4})$", raw_d)
    if not m:
        return {"is_composite": True, "expired": False, "expiring_soon": False, "warning": None}

    mfg_month = int(m.group(1))
    mfg_year = int(m.group(2))
    if not (1 <= mfg_month <= 12):
        return {"is_composite": True, "expired": False, "expiring_soon": False, "warning": None}

    ref_dt = parse_iso_date(ref_iso_date) or now_eastern().date()
    if mfg_year < 100:
        cutoff = (ref_dt.year % 100) + 1
        mfg_year = (2000 + mfg_year) if mfg_year <= cutoff else (1900 + mfg_year)

    exp_year = mfg_year + 15
    exp_month = mfg_month
    months_left = (exp_year - ref_dt.year) * 12 + (exp_month - ref_dt.month)

    if months_left < 0:
        return {
            "is_composite": True,
            "expired": True,
            "expiring_soon": False,
            "expires_label": f"{exp_month:02d}-{exp_year}",
            "warning": f"EXPIRED COMPOSITE (>15 YRS): Mfg {mfg_month:02d}-{mfg_year} expired {exp_month:02d}-{exp_year}. Do NOT hydro unless covered by a valid 30-yr MAE permit.",
        }
    if months_left <= 6:
        return {
            "is_composite": True,
            "expired": False,
            "expiring_soon": True,
            "expires_label": f"{exp_month:02d}-{exp_year}",
            "warning": f"COMPOSITE NEAR END-OF-LIFE: 15-yr service life ends {exp_month:02d}-{exp_year} ({months_left} mo left).",
        }
    return {
        "is_composite": True,
        "expired": False,
        "expiring_soon": False,
        "expires_label": f"{exp_month:02d}-{exp_year}",
        "warning": None,
    }


def dot_max_allowed_test_psi(max_cal_psi: int) -> int:
    if max_cal_psi <= 0:
        return 0
    return max_cal_psi + 500 if max_cal_psi >= 3000 else int(round(max_cal_psi * 1.10))


def queue_sort_key(r: dict) -> tuple:
    pri = (r.get("priority") or "STANDARD").upper()
    aged = (r.get("pre_test_days") or 0) >= 4
    tier = 0 if (r.get("rush_fee") or pri == "RUSH") else (1 if (pri == "HIGH" or aged) else 2)
    due = r.get("promised_date") or "9999-12-31"
    arrived = (r.get("intake_at") or r.get("work_date") or "")[:10]
    return (tier, due, arrived, r.get("id") or 0)


def enrich_record_meta(r: dict, ref_today: str) -> dict:
    life = evaluate_composite_life(r.get("dot_spec", ""), r.get("mfg_date", ""), ref_today)
    r["composite_warning"] = life.get("warning")
    r["composite_expired"] = life.get("expired", False)
    origin_date = (r.get("intake_at") or r.get("work_date") or "")[:10]
    r["pre_test_days"] = (
        days_since(origin_date, ref_today)
        if r.get("status") == "QUEUED"
        else days_since(origin_date, r.get("work_date") or ref_today)
    )
    r["post_test_days"] = (
        days_since(r.get("work_date"), ref_today)
        if (r.get("status") == "COMPLETED" and r.get("shop_stage") != "PICKED_UP")
        else 0
    )
    r["holding_zone_label"] = HOLDING_ZONES.get(r.get("holding_zone") or "", r.get("holding_zone") or "")
    return r


def resolve_day_plan(d: date, plans_map: dict, default_cap: int = 24) -> dict:
    iso = d.isoformat()
    saved = plans_map.get(iso)
    if saved:
        mode = saved["shift_mode"] if saved["shift_mode"] in SHIFT_MODES else "FULL_HYDRO"
        cap = int(saved["bottle_capacity"]) if mode in ("FULL_HYDRO", "ROAD_RUN") else 0
        return {
            "work_date": iso,
            "weekday": d.strftime("%a"),
            "month_day": d.strftime("%b %d"),
            "shift_mode": mode,
            "shift_label": SHIFT_MODES[mode]["label"],
            "bottle_capacity": cap,
            "day_notes": saved.get("day_notes") or "",
            "is_custom": True,
        }
    is_weekend = d.weekday() >= 5
    mode = "BACKROOM_ONLY" if is_weekend else "FULL_HYDRO"
    cap = 0 if is_weekend else default_cap
    return {
        "work_date": iso,
        "weekday": d.strftime("%a"),
        "month_day": d.strftime("%b %d"),
        "shift_mode": mode,
        "shift_label": SHIFT_MODES[mode]["label"],
        "bottle_capacity": cap,
        "day_notes": "",
        "is_custom": False,
    }


def build_month_calendar_grid(conn, year: int, month: int, s_map: dict) -> dict:
    """Builds a full Sunday-Saturday month calendar grid for any past or future month."""
    default_cap = int(s_map.get("daily_hydro_capacity") or 24)
    est_today = today_eastern()
    cal = calendar.Calendar(firstweekday=6)  # Sunday = column 0
    weeks = cal.monthdatescalendar(year, month)
    flat_dates = [d for wk in weeks for d in wk]
    start_iso = flat_dates[0].isoformat()
    end_iso = flat_dates[-1].isoformat()

    plans_rows = conn.execute(
        "SELECT * FROM shop_day_plans WHERE work_date >= ? AND work_date <= ?;",
        (start_iso, end_iso),
    ).fetchall()
    plans_map = {r["work_date"]: dict(r) for r in plans_rows}

    jobs_rows = [
        dict(r)
        for r in conn.execute(
            """SELECT * FROM schedule_jobs
               WHERE status != 'CANCELLED'
                 AND ((scheduled_date >= ? AND scheduled_date <= ?) OR (due_date >= ? AND due_date <= ?))
               ORDER BY CASE WHEN rush_fee=1 OR priority='RUSH' THEN 0 ELSE 1 END, id ASC;""",
            (start_iso, end_iso, start_iso, end_iso),
        ).fetchall()
    ]

    comp_rows = conn.execute(
        """SELECT work_date, COUNT(*) AS cnt FROM cylinder_records
           WHERE status='COMPLETED' AND work_date >= ? AND work_date <= ?
           GROUP BY work_date;""",
        (start_iso, end_iso),
    ).fetchall()
    comp_map = {r["work_date"]: int(r["cnt"]) for r in comp_rows}

    days_out = []
    for d in flat_dates:
        p = resolve_day_plan(d, plans_map, default_cap)
        iso = p["work_date"]
        day_jobs = [j for j in jobs_rows if j["scheduled_date"] == iso and j["status"] != "COMPLETED"]
        done_jobs = [j for j in jobs_rows if j["scheduled_date"] == iso and j["status"] == "COMPLETED"]
        due_jobs = [j for j in jobs_rows if j.get("due_date") == iso and j["scheduled_date"] != iso and j["status"] != "COMPLETED"]
        booked = sum(int(j["bottle_count"] or 0) for j in day_jobs if j["job_type"] in ("SHOP_BATCH", "ROAD_PICKUP", "ROAD_ROUNDTRIP"))
        p.update({
            "day_num": d.day,
            "in_month": d.month == month,
            "is_today": iso == est_today,
            "booked_bottles": booked,
            "completed_bottles": comp_map.get(iso, 0),
            "jobs": day_jobs,
            "completed_jobs_count": len(done_jobs),
            "due_jobs": due_jobs,
        })
        days_out.append(p)

    prev_dt = date(year, month, 1) - timedelta(days=1)
    next_dt = (date(year, month, 28) + timedelta(days=10)).replace(day=1)
    return {
        "year": year,
        "month": month,
        "month_label": date(year, month, 1).strftime("%B %Y"),
        "prev_year": prev_dt.year,
        "prev_month": prev_dt.month,
        "next_year": next_dt.year,
        "next_month": next_dt.month,
        "today": est_today,
        "days": days_out,
    }


def compute_recall_list(conn, ref_today: str) -> list[dict]:
    """Calculates 5-Year Hydro, 10-Year Star, and 1-Year Visual retest recall dates for repeat business."""
    rows = conn.execute(
        """SELECT r.id, r.serial_number, r.customer_name, r.customer_phone,
                  r.dot_spec, r.service_psi, r.service_mode, r.work_date, r.notes,
                  c.phone AS rolodex_phone, c.email, c.contact_person, c.account_type
           FROM cylinder_records r
           LEFT JOIN customers c ON UPPER(c.customer_name) = UPPER(r.customer_name)
           WHERE r.status='COMPLETED' AND r.hydro_disposition='A'
           ORDER BY r.work_date DESC, r.id DESC;"""
    ).fetchall()

    seen_serials = set()
    recalls = []
    ref_dt = parse_iso_date(ref_today) or now_eastern().date()

    for r in rows:
        sn_key = f"{(r['customer_name'] or '').upper()}::{(r['serial_number'] or '').upper()}"
        if sn_key in seen_serials:
            continue
        seen_serials.add(sn_key)

        w_dt = parse_iso_date(r["work_date"])
        if not w_dt:
            continue
        notes_up = (r["notes"] or "").upper()
        if r["service_mode"] == "VISUAL_ONLY":
            cycle_yrs = 1
            cycle_lbl = "1-Yr Annual Visual"
        elif "10-YR" in notes_up or "STAR" in notes_up or "★" in (r["dot_spec"] or ""):
            cycle_yrs = 10
            cycle_lbl = "10-Yr Star Hydro"
        else:
            cycle_yrs = 5
            cycle_lbl = "5-Yr DOT Hydro"

        try:
            due_dt = w_dt.replace(year=w_dt.year + cycle_yrs)
        except ValueError:
            due_dt = w_dt + timedelta(days=365 * cycle_yrs)

        days_left = (due_dt - ref_dt).days
        status_bucket = "OVERDUE" if days_left < 0 else ("DUE_SOON" if days_left <= 120 else "FUTURE")
        recalls.append({
            "serial_number": r["serial_number"],
            "customer_name": r["customer_name"],
            "phone": r["rolodex_phone"] or r["customer_phone"] or "",
            "email": r["email"] or "",
            "contact_person": r["contact_person"] or "",
            "account_type": r["account_type"] or "WALK_IN",
            "spec_label": f"{r['dot_spec']}-{r['service_psi']}",
            "last_test_date": r["work_date"],
            "cycle_label": cycle_lbl,
            "due_date": due_dt.isoformat(),
            "due_month_label": due_dt.strftime("%b %Y"),
            "days_until_due": days_left,
            "status_bucket": status_bucket,
        })

    recalls.sort(key=lambda x: x["due_date"])
    return recalls


def get_binder_status(conn, s_map: dict) -> dict:
    base_sheet = int(s_map.get("starting_sheet_no") or 8095)
    saved_cur = int(s_map.get("current_sheet_no") or base_sheet)
    max_row = conn.execute(
        "SELECT COALESCE(MAX(sheet_no), 0) AS m FROM cylinder_records WHERE service_mode='HYDRO_AND_VISUAL' AND status='COMPLETED';"
    ).fetchone()
    active_sheet = max(base_sheet, saved_cur, int(max_row["m"] or 0))

    used_on_active = conn.execute(
        "SELECT COUNT(*) AS cnt FROM cylinder_records WHERE service_mode='HYDRO_AND_VISUAL' AND status='COMPLETED' AND sheet_no=?;",
        (active_sheet,),
    ).fetchone()["cnt"]

    if used_on_active >= 12:
        active_sheet += 1
        used_on_active = 0

    last_rec = conn.execute(
        """SELECT sheet_no, work_date, serial_number, customer_name
           FROM cylinder_records
           WHERE service_mode='HYDRO_AND_VISUAL' AND status='COMPLETED'
           ORDER BY id DESC LIMIT 1;"""
    ).fetchone()
    last_sheet_count = 0
    if last_rec:
        last_sheet_count = conn.execute(
            "SELECT COUNT(*) FROM cylinder_records WHERE service_mode='HYDRO_AND_VISUAL' AND status='COMPLETED' AND sheet_no=?;",
            (last_rec["sheet_no"],),
        ).fetchone()[0]

    return {
        "active_sheet_no": active_sheet,
        "rows_used_on_active": int(used_on_active),
        "next_row_number": int(used_on_active) + 1,
        "rows_remaining_on_active": 12 - int(used_on_active),
        "last_recorded": {
            "sheet_no": last_rec["sheet_no"],
            "row_ended_at": min(12, int(last_sheet_count)),
            "work_date": last_rec["work_date"],
            "serial_number": last_rec["serial_number"],
            "customer_name": last_rec["customer_name"],
        } if last_rec else None,
    }


def calculate_turnaround_quote(conn, requested_bottles: int = 1) -> dict:
    est = now_eastern()
    start_dt = est.date()
    s_map = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings;").fetchall()}
    default_cap = max(4, int(s_map.get("daily_hydro_capacity") or 24))

    plans_rows = conn.execute(
        "SELECT * FROM shop_day_plans WHERE work_date >= ? ORDER BY work_date ASC;",
        (start_dt.isoformat(),),
    ).fetchall()
    plans_map = {r["work_date"]: dict(r) for r in plans_rows}

    queued_total = conn.execute("SELECT COUNT(*) FROM cylinder_records WHERE status='QUEUED';").fetchone()[0]
    queued_rush = conn.execute(
        "SELECT COUNT(*) FROM cylinder_records WHERE status='QUEUED' AND (rush_fee=1 OR priority='RUSH');"
    ).fetchone()[0]

    drop_row = conn.execute(
        """SELECT COALESCE(SUM(CASE WHEN bottle_count > COALESCE(imported_count,0) THEN bottle_count - COALESCE(imported_count,0) ELSE 0 END), 0) AS tot,
                  COALESCE(SUM(CASE WHEN rush_requested=1 AND bottle_count > COALESCE(imported_count,0) THEN bottle_count - COALESCE(imported_count,0) ELSE 0 END), 0) AS rush_tot
           FROM dropoffs WHERE status IN ('IN_BACKROOM', 'CHECKED_IN') AND entry_type='DROPOFF';"""
    ).fetchone()

    sched_row = conn.execute(
        """SELECT COALESCE(SUM(bottle_count), 0) AS tot,
                  COALESCE(SUM(CASE WHEN rush_fee=1 OR priority='RUSH' THEN bottle_count ELSE 0 END), 0) AS rush_tot
           FROM schedule_jobs WHERE status IN ('OPEN', 'PREPPED') AND job_type IN ('SHOP_BATCH', 'ROAD_PICKUP', 'ROAD_ROUNDTRIP');"""
    ).fetchone()

    total_backlog = int(queued_total) + int(drop_row["tot"] or 0) + int(sched_row["tot"] or 0)
    rush_backlog = int(queued_rush) + int(drop_row["rush_tot"] or 0) + int(sched_row["rush_tot"] or 0)

    req = max(1, int(requested_bottles or 1))
    std_needed = total_backlog + req
    rush_needed = rush_backlog + req

    std_hydro_done: Optional[date] = None
    rush_hydro_done: Optional[date] = None
    std_cum = 0
    rush_cum = 0
    upcoming_hydro_days = []

    for offset in range(0, 35):
        d = start_dt + timedelta(days=offset)
        plan = resolve_day_plan(d, plans_map, default_cap)
        cap = plan["bottle_capacity"]
        if offset == 0 and est.hour >= 13:
            cap = cap // 2
        if cap > 0:
            upcoming_hydro_days.append({"date": d.isoformat(), "label": d.strftime("%a %b %d"), "capacity": cap})
            std_cum += cap
            rush_cum += cap
            if rush_hydro_done is None and rush_cum >= rush_needed:
                rush_hydro_done = d
            if std_hydro_done is None and std_cum >= std_needed:
                std_hydro_done = d

    if rush_hydro_done is None:
        rush_hydro_done = start_dt + timedelta(days=2)
    if std_hydro_done is None:
        std_hydro_done = start_dt + timedelta(days=7)

    std_ready = std_hydro_done + timedelta(days=1)
    while std_ready.weekday() >= 5:
        std_ready += timedelta(days=1)

    rush_ready = rush_hydro_done
    if rush_ready == start_dt and est.hour >= 12:
        rush_ready = start_dt + timedelta(days=1)
    while rush_ready.weekday() >= 5 and not plans_map.get(rush_ready.isoformat(), {}).get("bottle_capacity"):
        rush_ready += timedelta(days=1)
    if rush_ready > std_ready:
        std_ready = rush_ready

    return {
        "requested_bottles": req,
        "total_backlog_bottles": total_backlog,
        "rush_backlog_bottles": rush_backlog,
        "rush_promise_date": rush_ready.isoformat(),
        "rush_promise_label": rush_ready.strftime("%a, %b %d"),
        "standard_promise_date": std_ready.isoformat(),
        "standard_promise_label": std_ready.strftime("%a, %b %d"),
        "next_hydro_windows": upcoming_hydro_days[:5],
    }


def compute_session_token() -> str:
    conn = get_conn()
    rows = {
        r["key"]: r["value"]
        for r in conn.execute(
            "SELECT key, value FROM settings WHERE key IN ('shop_pin', 'session_secret');"
        ).fetchall()
    }
    conn.close()
    pin = os.getenv("WEBER_SHOP_PIN") or rows.get("shop_pin", "0386")
    secret = rows.get("session_secret", "webers-default-secret")
    return hmac.new(secret.encode("utf-8"), f"B386:{pin}".encode("utf-8"), hashlib.sha256).hexdigest()


def is_authenticated(request: Request) -> bool:
    token = request.cookies.get(COOKIE_NAME, "")
    if not token:
        return False
    return hmac.compare_digest(token, compute_session_token())


def check_duplicate_active_valve_tag(
    conn, valve_tag: str, exclude_id: Optional[int] = None
) -> Optional[dict]:
    clean = (valve_tag or "").strip()
    if not clean:
        return None
    sql = """SELECT id, serial_number, customer_name, work_date FROM cylinder_records
             WHERE UPPER(valve_tag)=UPPER(?) AND valve_reinstalled=0 AND valve_tag != ''"""
    params: list = [clean]
    if exclude_id is not None:
        sql += " AND id != ?"
        params.append(exclude_id)
    row = conn.execute(sql + " LIMIT 1;", tuple(params)).fetchone()
    return dict(row) if row else None


def check_duplicate_serial_queued(
    conn, serial_number: str, work_date: str, exclude_id: Optional[int] = None
) -> Optional[dict]:
    clean = (serial_number or "").strip().upper()
    if not clean:
        return None
    sql = """SELECT id, serial_number, customer_name, status, work_date FROM cylinder_records
             WHERE UPPER(serial_number)=? AND (status='QUEUED' OR work_date=?)"""
    params: list = [clean, work_date]
    if exclude_id is not None:
        sql += " AND id != ?"
        params.append(exclude_id)
    row = conn.execute(sql + " ORDER BY id DESC LIMIT 1;", tuple(params)).fetchone()
    return dict(row) if row else None
def push_unsynced_to_sheets() -> dict:
    conn = get_conn()
    s_map = {
        r["key"]: r["value"]
        for r in conn.execute(
            "SELECT key, value FROM settings WHERE key IN ('sheets_webhook_url', 'feedback_webhook_url');"
        ).fetchall()
    }
    webhook_url = (s_map.get("sheets_webhook_url") or "").strip()
    fb_webhook_url = (s_map.get("feedback_webhook_url") or webhook_url).strip()

    cals = [
        dict(r)
        for r in conn.execute("SELECT * FROM daily_verifications WHERE synced=0 ORDER BY id ASC LIMIT 50;").fetchall()
    ]
    recs = [
        dict(r)
        for r in conn.execute(
            """SELECT r.*, COALESCE(i.invoice_no, 'UNBILLED') AS invoice_no_label
               FROM cylinder_records r
               LEFT JOIN invoices i ON r.invoice_id = i.id
               WHERE r.synced=0 AND r.status='COMPLETED'
               ORDER BY r.id ASC LIMIT 50;"""
        ).fetchall()
    ]
    invs = [
        dict(r)
        for r in conn.execute(
            """SELECT i.*, (SELECT COUNT(*) FROM cylinder_records r WHERE r.invoice_id=i.id) AS bottle_count
               FROM invoices i WHERE i.synced=0 ORDER BY i.id ASC LIMIT 30;"""
        ).fetchall()
    ]
    drops = [
        dict(r)
        for r in conn.execute("SELECT * FROM dropoffs WHERE synced=0 ORDER BY id ASC LIMIT 30;").fetchall()
    ]
    fbs = [
        dict(r)
        for r in conn.execute("SELECT * FROM crew_feedback WHERE synced=0 ORDER BY id ASC LIMIT 30;").fetchall()
    ]
    rates = [dict(r) for r in conn.execute("SELECT * FROM pricing_rates ORDER BY sort_order ASC, code ASC;").fetchall()]
    conn.close()

    if not webhook_url and not fb_webhook_url:
        return {"synced": False, "reason": "No Google Sheets Webhook URL set yet."}

    pushed_total = 0
    ctx = ssl.create_default_context()

    if webhook_url and (cals or recs or invs or drops):
        sheets_recs = []
        for r in recs:
            rc = dict(r)
            if rc.get("percent_permanent") is not None:
                rc["percent_permanent"] = round(float(rc["percent_permanent"]), 1)
            rc["operator_initials"] = "RP"
            mfg_d = (rc.get("mfg_date") or "").strip()
            if mfg_d and not mfg_d.startswith("'") and re.match(r"^\d{1,2}[-/]\d{2,4}$", mfg_d):
                rc["mfg_date"] = f"'{mfg_d}"
            sheets_recs.append(rc)

        payload_bytes = json.dumps(
            {
                "calibrations": cals,
                "records": sheets_recs,
                "invoices": invs,
                "dropoffs": drops,
                "pricing_rates": rates,
                "feedback": fbs if fb_webhook_url == webhook_url else [],
            }
        ).encode("utf-8")

        req = urllib.request.Request(
            webhook_url,
            data=payload_bytes,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=20, context=ctx) as resp:
            _ = resp.read()

        conn = get_conn()
        if cals:
            conn.executemany("UPDATE daily_verifications SET synced=1 WHERE id=?;", [(c["id"],) for c in cals])
        if recs:
            conn.executemany("UPDATE cylinder_records SET synced=1 WHERE id=?;", [(r["id"],) for r in recs])
        if invs:
            conn.executemany("UPDATE invoices SET synced=1 WHERE id=?;", [(iv["id"],) for iv in invs])
        if drops:
            conn.executemany("UPDATE dropoffs SET synced=1 WHERE id=?;", [(d["id"],) for d in drops])
        if fbs and fb_webhook_url == webhook_url:
            conn.executemany("UPDATE crew_feedback SET synced=1 WHERE id=?;", [(f["id"],) for f in fbs])
            pushed_total += len(fbs)
            fbs = []
        bump_db_version(conn)
        conn.commit()
        conn.close()
        pushed_total += len(cals) + len(recs) + len(invs) + len(drops)

    if fb_webhook_url and fbs:
        fb_bytes = json.dumps({"action": "crew_feedback", "feedback": fbs}).encode("utf-8")
        req = urllib.request.Request(
            fb_webhook_url,
            data=fb_bytes,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=20, context=ctx) as resp:
            _ = resp.read()
        conn = get_conn()
        conn.executemany("UPDATE crew_feedback SET synced=1 WHERE id=?;", [(f["id"],) for f in fbs])
        bump_db_version(conn)
        conn.commit()
        conn.close()
        pushed_total += len(fbs)

    return {"synced": True, "pushed": pushed_total}


async def run_guarded_sheets_sync() -> dict:
    async with SHEETS_SYNC_LOCK:
        return await asyncio.to_thread(push_unsynced_to_sheets)



def send_ticket_email_via_webhook(ticket_info: dict) -> None:
    """Sends instant customer email receipt + shop alert via the Google Apps Script webhook."""
    try:
        conn = get_conn()
        row = conn.execute("SELECT value FROM settings WHERE key='sheets_webhook_url';").fetchone()
        conn.close()
        webhook_url = (row["value"] if row else "").strip()
        if not webhook_url:
            return
        payload_bytes = json.dumps({"action": "dropoff_receipt_email", "ticket": ticket_info}).encode("utf-8")
        req = urllib.request.Request(
            webhook_url,
            data=payload_bytes,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            _ = resp.read()
    except Exception:
        pass


def trigger_ticket_email_bg(ticket_info: dict) -> None:
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(asyncio.to_thread(send_ticket_email_via_webhook, ticket_info))
    except RuntimeError:
        pass


def trigger_sheets_sync_bg() -> None:
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_quiet_bg_sync())
    except RuntimeError:
        pass


async def _quiet_bg_sync() -> None:
    try:
        await run_guarded_sheets_sync()
    except Exception:
        pass


def wipe_sheets_remote(webhook_url: str) -> bool:
    if not webhook_url:
        return False
    req = urllib.request.Request(
        webhook_url,
        data=json.dumps({"action": "wipe_test_data"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=20, context=ctx) as resp:
        _ = resp.read()
    return True


async def background_sheets_sync_loop():
    while True:
        await asyncio.sleep(15)
        await _quiet_bg_sync()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    task = asyncio.create_task(background_sheets_sync_loop())
    yield
    task.cancel()


app = FastAPI(title="Weber's Tank Testing - DOT RIN B386", lifespan=lifespan)


@app.middleware("http")
async def auth_gate_middleware(request: Request, call_next):
    path = request.url.path
    public_paths = {
        "/", "/ops", "/schedule", "/office", "/tv", "/checkin",
        "/manifest.json", "/sw.js", "/icon.svg",
        "/api/auth/status", "/api/auth/login", "/api/auth/logout",
        "/api/public/dropoff", "/api/public/turnaround", "/api/public/track",
    }
    if path in public_paths or path.startswith("/static") or path.startswith("/api/public/receipt/"):
        return await call_next(request)
    if not is_authenticated(request):
        return JSONResponse({"detail": "Crew PIN login required."}, status_code=401)
    return await call_next(request)


# --- PYDANTIC SCHEMAS ---
class LoginInput(BaseModel):
    pin: str


class PublicDropoffInput(BaseModel):
    entry_type: str = "DROPOFF"
    account_type: str = "WALK_IN"
    po_station: str = ""
    contact_person: str = ""
    customer_name: str
    customer_phone: str = ""
    customer_email: str = ""
    pickup_address: str = ""
    preferred_date: str = ""
    truck_mode: str = "ROAD_ROUNDTRIP"
    bottle_count: int = 1
    tank_summary: str = ""
    service_requested: str = "Hydro + Visual"
    valves_attached: bool = True
    needs_air_fill: bool = False
    rush_requested: bool = False
    picked_up_by: str = ""
    pickup_ticket_ref: str = ""
    notes: str = ""
    website_hp: str = ""

class DropoffStatusInput(BaseModel):
    status: Optional[str] = None
    holding_zone: Optional[str] = None
    rush_requested: Optional[bool] = None
    promised_date: Optional[str] = None


class BinderActionInput(BaseModel):
    action: str = "continue"
    sheet_no: Optional[int] = None


class DayPlanInput(BaseModel):
    work_date: str
    shift_mode: str = "FULL_HYDRO"
    bottle_capacity: Optional[int] = None
    day_notes: str = ""


class ScheduleJobInput(BaseModel):
    scheduled_date: str
    due_date: str = ""
    job_type: str = "SHOP_BATCH"
    account_type: str = "WALK_IN"
    po_station: str = ""
    contact_person: str = ""
    customer_name: str
    customer_phone: str = ""
    location_notes: str = ""
    bottle_count: int = 1
    tank_summary: str = ""
    valves_attached: bool = True
    needs_air_fill: bool = False
    priority: str = "STANDARD"
    rush_fee: bool = False
    holding_zone: str = "ZONE_A_BACKROOM"
    prep_status: str = "WAITING"
    status: str = "OPEN"
    notes: str = ""


class ScheduleJobPatchInput(BaseModel):
    scheduled_date: Optional[str] = None
    due_date: Optional[str] = None
    job_type: Optional[str] = None
    account_type: Optional[str] = None
    po_station: Optional[str] = None
    contact_person: Optional[str] = None
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    location_notes: Optional[str] = None
    bottle_count: Optional[int] = None
    tank_summary: Optional[str] = None
    valves_attached: Optional[bool] = None
    needs_air_fill: Optional[bool] = None
    priority: Optional[str] = None
    rush_fee: Optional[bool] = None
    holding_zone: Optional[str] = None
    prep_status: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None


class LadderStep(BaseModel):
    pressure_psi: int
    actual_total_cc: float


class VerificationLadderInput(BaseModel):
    test_date: Optional[str] = None
    jacket_type: str
    max_target_psi: int
    steps: list[LadderStep]
    permanent_cc: float = 0.0
    logged_by: str = "RP"


class SettingsInput(BaseModel):
    calibrated_serial: Optional[str] = None
    starting_sheet_no: Optional[int] = None
    daily_hydro_capacity: Optional[int] = None
    default_tax_rate: Optional[float] = None
    rin_expires_date: Optional[str] = None
    gauge_cert_expires_date: Optional[str] = None
    gemini_api_key: Optional[str] = None
    sheets_webhook_url: Optional[str] = None
    feedback_webhook_url: Optional[str] = None
    shop_pin: Optional[str] = None
    testing_mode: Optional[bool] = None


class CrewInput(BaseModel):
    full_name: str
    initials: str
    role: str = "Visual Inspection"


class IntakeInput(BaseModel):
    work_date: Optional[str] = None
    customer_name: str
    customer_phone: str = ""
    dropoff_id: Optional[int] = None
    schedule_job_id: Optional[int] = None
    priority: str = "STANDARD"
    rush_fee: bool = False
    promised_date: str = ""
    holding_zone: str = "ZONE_C_READY_RACK"
    serial_number: str
    valve_tag: str = ""
    mfg_id: str = ""
    mfg_date: str = ""
    gas_service: str = "AIR"
    outside_dia: str = ""
    length_in: str = ""
    dot_spec: str
    service_psi: int
    test_psi: Optional[int] = None
    ree_limit: Optional[float] = None
    service_mode: str = "HYDRO_AND_VISUAL"
    intake_by: str = "RP"
    notes: str = ""
    allow_duplicate: bool = False


class RecordPriorityInput(BaseModel):
    priority: Optional[str] = None
    rush_fee: Optional[bool] = None
    promised_date: Optional[str] = None
    holding_zone: Optional[str] = None


class RecordAddonsInput(BaseModel):
    addon_oring: Optional[bool] = None
    addon_burst_disc: Optional[bool] = None
    addon_clean: Optional[bool] = None
    condemn_action: Optional[str] = None


class ValveTagAssignInput(BaseModel):
    record_id: Optional[int] = None
    serial_number: Optional[str] = None
    work_date: Optional[str] = None
    valve_tag: Optional[str] = None
    valve_reinstalled: Optional[bool] = None


class ShopStageInput(BaseModel):
    shop_stage: str
    rin_marked: Optional[bool] = None
    rin_mark_type: Optional[str] = None
    valve_reinstalled: Optional[bool] = None
    holding_zone: Optional[str] = None
    picked_up_by: Optional[str] = None
    force: bool = False


class BatchStageInput(BaseModel):
    work_date: Optional[str] = None
    customer_name: str
    shop_stage: str
    rin_marked: Optional[bool] = None
    rin_mark_type: Optional[str] = None
    release_valves: bool = False
    picked_up_by: Optional[str] = None
    holding_zone: Optional[str] = None


class ScanBottleJsonInput(BaseModel):
    images_b64: list[str]
    work_date: Optional[str] = None
    customer_name: str = ""
    customer_phone: str = ""
    dropoff_id: Optional[int] = None
    schedule_job_id: Optional[int] = None
    priority: str = "STANDARD"
    rush_fee: bool = False
    promised_date: str = ""
    valve_tag: str = ""
    service_mode: str = "HYDRO_AND_VISUAL"
    gas_service: str = "AIR"
    outside_dia: str = ""
    length_in: str = ""
    intake_by: str = "RP"
    auto_import: bool = False


class TestCompleteInput(BaseModel):
    test_date: Optional[str] = None
    jacket_type: str = "Tall Jacket"
    total_cc: Optional[float] = None
    permanent_cc: Optional[float] = None
    max_percent_perm: float = 10.0
    valve_tag: Optional[str] = None
    valve_reinstalled: bool = False
    rin_marked: bool = False
    rin_mark_type: str = ""
    shop_stage: str = "DRYING"
    coating_type: str = "Painted"
    coating_condition: str = "Good"
    chk_corrosion: str = "OK"
    chk_dents_dings: str = "OK"
    chk_cuts_gouges: str = "OK"
    chk_leaks: str = "OK"
    chk_fire_damage: str = "OK"
    chk_bulges: str = "OK"
    chk_neck_defects: str = "OK"
    relief_valve_changed: str = "No"
    addon_oring: bool = False
    addon_burst_disc: bool = False
    addon_clean: bool = False
    condemn_action: str = ""
    tested_by: str = "RP"
    notes: str = ""


class RecordEditInput(BaseModel):
    customer_name: str
    customer_phone: str = ""
    priority: str = "STANDARD"
    rush_fee: bool = False
    promised_date: str = ""
    holding_zone: str = "ZONE_C_READY_RACK"
    serial_number: str
    valve_tag: str = ""
    valve_reinstalled: bool = False
    rin_marked: bool = False
    rin_mark_type: str = ""
    shop_stage: str = "IN_SHOP"
    mfg_id: str = ""
    mfg_date: str = ""
    gas_service: str = "AIR"
    outside_dia: str = ""
    length_in: str = ""
    dot_spec: str
    service_psi: int
    test_psi: int
    ree_limit: Optional[float] = None
    total_cc: Optional[float] = None
    permanent_cc: Optional[float] = None
    max_percent_perm: float = 10.0
    coating_condition: str = "Good"
    relief_valve_changed: str = "No"
    addon_oring: bool = False
    addon_burst_disc: bool = False
    addon_clean: bool = False
    condemn_action: str = ""
    chk_corrosion: str = "OK"
    chk_dents_dings: str = "OK"
    chk_cuts_gouges: str = "OK"
    chk_leaks: str = "OK"
    chk_fire_damage: str = "OK"
    chk_bulges: str = "OK"
    chk_neck_defects: str = "OK"
    hydro_disposition_override: Optional[str] = None
    notes: str = ""


class CustomerRolodexInput(BaseModel):
    id: Optional[int] = None
    customer_name: str
    account_type: str = "WALK_IN"
    contact_person: str = ""
    phone: str = ""
    email: str = ""
    street_address: str = ""
    city: str = ""
    state: str = "PA"
    zip_code: str = ""
    payment_terms: str = "DUE_AT_PICKUP"
    tax_exempt: bool = False
    notes: str = ""


class SupplyUpsertInput(BaseModel):
    id: Optional[int] = None
    category: str = "Shop Supplies"
    item_name: str
    spec_note: str = ""
    vendor_name: str = "Amazon"
    reorder_url: str = ""
    low_stock: bool = False
    last_ordered_date: str = ""
    sort_order: int = 100


class SupplyLowStockInput(BaseModel):
    low_stock: bool
    mark_ordered_today: bool = False


class PricingRateUpsert(BaseModel):
    code: str
    description: Optional[str] = None
    unit_price: float = 0.0
    notes: Optional[str] = None
    sort_order: Optional[int] = None


class InvoiceLineInput(BaseModel):
    record_id: Optional[int] = None
    description: str
    qty: float = 1.0
    unit_price: float = 0.0


class InvoiceCreateInput(BaseModel):
    work_date: str
    customer_name: str
    customer_phone: str = ""
    tax_rate: float = 0.0
    status: str = "DRAFT"
    payment_method: str = ""
    created_by: str = "RP"
    notes: str = ""
    items: list[InvoiceLineInput]


class InvoiceStatusInput(BaseModel):
    status: str
    payment_method: Optional[str] = None


class CrewFeedbackInput(BaseModel):
    submitted_by: str = "RP"
    category: str = "Improvement"
    page_tab: str = ""
    priority: str = "Normal"
    message: str


class FeedbackStatusInput(BaseModel):
    status: str

class HelpChatTurn(BaseModel):
    role: str
    text: str


class HelpAssistantInput(BaseModel):
    message: str
    current_page: str = "/ops"
    current_tab: str = ""
    submitted_by: str = "RP"
    history: list[HelpChatTurn] = []


# --- WORKSPACE PAGE ROUTES ---
@app.get("/")
def serve_root(request: Request):
    host = (request.headers.get("host") or "").split(":")[0].lower()
    landing_file = STATIC_DIR / "landing.html"
    if (host in ("weberstanktesting.com", "www.weberstanktesting.com") or request.query_params.get("view") == "public") and landing_file.exists():
        return FileResponse(landing_file, headers=NO_CACHE_HEADERS)
    return FileResponse(STATIC_DIR / "index.html", headers=NO_CACHE_HEADERS)


@app.get("/checkin")
def serve_public_checkin():
    landing_file = STATIC_DIR / "landing.html"
    return FileResponse(landing_file if landing_file.exists() else STATIC_DIR / "index.html", headers=NO_CACHE_HEADERS)


@app.get("/ops")
def serve_ops():
    return FileResponse(STATIC_DIR / "index.html", headers=NO_CACHE_HEADERS)


@app.get("/schedule")
def serve_schedule_page():
    f = STATIC_DIR / "schedule.html"
    return FileResponse(f if f.exists() else STATIC_DIR / "index.html", headers=NO_CACHE_HEADERS)


@app.get("/office")
def serve_office_page():
    f = STATIC_DIR / "office.html"
    return FileResponse(f if f.exists() else STATIC_DIR / "index.html", headers=NO_CACHE_HEADERS)


@app.get("/tv")
def serve_tv_board():
    tv_file = STATIC_DIR / "tv.html"
    if not tv_file.exists():
        raise HTTPException(404, "tv.html not found in static/ folder.")
    return FileResponse(tv_file, headers=NO_CACHE_HEADERS)


@app.get("/icon.svg")
def serve_icon():
    svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 192 192">
      <rect width="192" height="192" rx="36" fill="#1b4332"/>
      <rect x="74" y="24" width="44" height="18" rx="6" fill="#fbbf24"/>
      <rect x="84" y="42" width="24" height="14" fill="#d1d5db"/>
      <rect x="54" y="54" width="84" height="114" rx="28" fill="#ffffff"/>
      <text x="96" y="108" font-family="Arial,sans-serif" font-size="24" font-weight="bold" text-anchor="middle" fill="#1b4332">B386</text>
      <text x="96" y="136" font-family="Arial,sans-serif" font-size="14" font-weight="bold" text-anchor="middle" fill="#2d6a4f">WEBER'S</text>
    </svg>"""
    return Response(content=svg, media_type="image/svg+xml")


@app.get("/manifest.json")
def serve_manifest():
    return {
        "name": "Weber's Tank Testing — Ops (DOT B386)",
        "short_name": "Weber's Ops",
        "start_url": "/ops",
        "display": "standalone",
        "background_color": "#0b2219",
        "theme_color": "#1b4332",
        "icons": [{"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any maskable"}],
    }


@app.get("/sw.js")
def serve_sw():
    js = "self.addEventListener('install', e => self.skipWaiting()); self.addEventListener('activate', e => self.clients.claim()); self.addEventListener('fetch', () => {});"
    return Response(content=js, media_type="application/javascript", headers=NO_CACHE_HEADERS)


@app.get("/api/auth/status")
def auth_status(request: Request, tv: int = 0):
    user = get_current_session_user(request)
    if tv and not user and is_tv_authorized(request):
        return {"authenticated": True, "today": today_eastern(), "user": {"name": "Live TV Display", "initials": "TV", "role": "TV", "can_sign_dot": False}}
    return {
        "authenticated": bool(user),
        "today": today_eastern(),
        "user": user,
    }


@app.post("/api/auth/login")
def auth_login(payload: LoginInput, response: Response):
    entered = payload.pin.strip()
    if not entered:
        raise HTTPException(401, "Enter your individual PIN.")

    conn = get_conn()
    u = conn.execute("SELECT * FROM crew_users WHERE pin=? AND active=1;", (entered,)).fetchone()
    s_row = conn.execute("SELECT value FROM settings WHERE key='crew_pin';").fetchone()
    tv_seed = s_row["value"] if s_row else "0386"
    conn.close()

    if not u:
        raise HTTPException(401, "Incorrect PIN.")

    today_iso = today_eastern()
    token = make_user_auth_token(u["id"], u["initials"], u["role"], u["pin"], today_iso)
    ttl = seconds_until_midnight_eastern()
    response.set_cookie(
        key=AUTH_COOKIE,
        value=token,
        max_age=ttl,
        httponly=True,
        samesite="lax",
    )
    # Also set TV display cookie so /tv stays running smoothly on the wall TV
    response.set_cookie(
        key="webers_tv_auth",
        value=make_tv_kiosk_token(tv_seed),
        max_age=60 * 60 * 24 * 90,
        httponly=True,
        samesite="lax",
    )
    return {
        "authenticated": True,
        "today": today_iso,
        "seconds_until_midnight": ttl,
        "user": {
            "id": u["id"],
            "name": u["name"],
            "initials": u["initials"],
            "role": u["role"],
            "can_sign_dot": bool(u["can_sign_dot"] and u["role"] == "OWNER"),
        },
    }


@app.post("/api/auth/logout")
def auth_logout(response: Response):
    response.delete_cookie(AUTH_COOKIE)
    return {"authenticated": False}


@app.get("/")
def serve_root(request: Request):
    host = (request.headers.get("host") or "").split(":")[0].lower()
    landing_file = STATIC_DIR / "landing.html"
    if (host in ("weberstanktesting.com", "www.weberstanktesting.com") or request.query_params.get("view") == "public") and landing_file.exists():
        return FileResponse(landing_file, headers=NO_CACHE_HEADERS)
    return FileResponse(STATIC_DIR / "index.html", headers=NO_CACHE_HEADERS)


@app.get("/checkin")
def serve_public_checkin():
    landing_file = STATIC_DIR / "landing.html"
    return FileResponse(landing_file if landing_file.exists() else STATIC_DIR / "index.html", headers=NO_CACHE_HEADERS)


@app.get("/ops")
def serve_ops():
    return FileResponse(STATIC_DIR / "index.html", headers=NO_CACHE_HEADERS)


@app.get("/schedule")
def serve_schedule_page():
    f = STATIC_DIR / "schedule.html"
    return FileResponse(f if f.exists() else STATIC_DIR / "index.html", headers=NO_CACHE_HEADERS)


@app.get("/office")
def serve_office_page():
    f = STATIC_DIR / "office.html"
    return FileResponse(f if f.exists() else STATIC_DIR / "index.html", headers=NO_CACHE_HEADERS)


@app.get("/tv")
def serve_tv_board():
    tv_file = STATIC_DIR / "tv.html"
    if not tv_file.exists():
        raise HTTPException(404, "tv.html not found in static/ folder.")
    return FileResponse(tv_file, headers=NO_CACHE_HEADERS)


@app.get("/icon.svg")
def serve_icon():
    svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 192 192">
      <rect width="192" height="192" rx="36" fill="#1b4332"/>
      <rect x="74" y="24" width="44" height="18" rx="6" fill="#fbbf24"/>
      <rect x="84" y="42" width="24" height="14" fill="#d1d5db"/>
      <rect x="54" y="54" width="84" height="114" rx="28" fill="#ffffff"/>
      <text x="96" y="108" font-family="Arial,sans-serif" font-size="24" font-weight="bold" text-anchor="middle" fill="#1b4332">B386</text>
      <text x="96" y="136" font-family="Arial,sans-serif" font-size="14" font-weight="bold" text-anchor="middle" fill="#2d6a4f">WEBER'S</text>
    </svg>"""
    return Response(content=svg, media_type="image/svg+xml")


@app.get("/manifest.json")
def serve_manifest():
    return {
        "name": "Weber's Tank Testing — Ops (DOT B386)",
        "short_name": "Weber's Ops",
        "start_url": "/ops",
        "display": "standalone",
        "background_color": "#0b2219",
        "theme_color": "#1b4332",
        "icons": [{"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any maskable"}],
    }


@app.get("/sw.js")
def serve_sw():
    js = "self.addEventListener('install', e => self.skipWaiting()); self.addEventListener('activate', e => self.clients.claim()); self.addEventListener('fetch', () => {});"
    return Response(content=js, media_type="application/javascript", headers=NO_CACHE_HEADERS)


@app.get("/api/auth/status")
def auth_status(request: Request):
    return {"authenticated": is_authenticated(request)}


@app.post("/api/auth/login")
def auth_login(payload: LoginInput, request: Request):
    conn = get_conn()
    saved_pin = conn.execute("SELECT value FROM settings WHERE key='shop_pin';").fetchone()["value"]
    conn.close()
    expected_pin = os.getenv("WEBER_SHOP_PIN") or saved_pin
    if not hmac.compare_digest(payload.pin.strip(), expected_pin):
        raise HTTPException(401, "Incorrect Shop PIN.")

    resp = JSONResponse({"authenticated": True})
    is_https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    resp.set_cookie(
        key=COOKIE_NAME,
        value=compute_session_token(),
        max_age=60 * 60 * 24 * 30,
        httponly=True,
        samesite="lax",
        secure=is_https,
    )
    return resp


@app.post("/api/auth/logout")
def auth_logout():
    resp = JSONResponse({"authenticated": False})
    resp.delete_cookie(COOKIE_NAME)
    return resp


# --- PUBLIC STOREFRONT ENDPOINTS (DROP-OFF, VERIFIED PICKUP, SLA & TRACKER) ---
@app.get("/api/public/turnaround")
def get_public_turnaround(bottles: int = 4):
    conn = get_conn()
    q = calculate_turnaround_quote(conn, max(1, min(100, bottles)))
    conn.close()
    return {
        "standard_ready_label": q["standard_promise_label"],
        "rush_ready_label": q["rush_promise_label"],
    }


@app.get("/api/public/track")
def track_public_ticket(ticket: str = "", phone_last4: str = ""):
    raw_t = re.sub(r"[^0-9]", "", (ticket or "").upper())
    last4 = re.sub(r"\D", "", phone_last4 or "")[-4:]
    if not raw_t:
        raise HTTPException(400, "Enter your Ticket # (e.g. BR-0001).")
    did = int(raw_t)
    conn = get_conn()
    d = conn.execute("SELECT * FROM dropoffs WHERE id=?;", (did,)).fetchone()
    if not d:
        conn.close()
        raise HTTPException(404, "Ticket not found. Check your BR-XXXX number or call 724-266-8008.")
    stored_digits = re.sub(r"\D", "", d["customer_phone"] or "")
    if stored_digits and len(last4) == 4 and not stored_digits.endswith(last4):
        conn.close()
        raise HTTPException(403, "Last 4 digits of phone number did not match this ticket.")

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
        stage_label = "Certified & Ready on Zone E Pickup Shelf"
        step_num = 4
    elif tested > 0:
        stage_label = f"Hydro Testing & B386 Marking ({tested}/{expected} Tested)"
        step_num = 3
    elif scanned > 0:
        stage_label = f"Checked In & Prepped for Stand ({scanned}/{expected} Logged)"
        step_num = 2
    else:
        stage_label = "Logged in Backroom Drop-Off Queue"
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



@app.get("/api/public/receipt/{ticket_no}.pdf")
def download_public_dropoff_receipt(ticket_no: str, p: str = ""):
    raw_num = re.sub(r"[^0-9]", "", (ticket_no or "").upper())
    if not raw_num:
        raise HTTPException(400, "Invalid ticket number.")
    did = int(raw_num)
    conn = get_conn()
    d = conn.execute("SELECT * FROM dropoffs WHERE id=?;", (did,)).fetchone()
    s_map = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings;").fetchall()}
    conn.close()
    if not d:
        raise HTTPException(404, "Ticket not found.")
    stored_digits = re.sub(r"\D", "", d["customer_phone"] or "")
    req_last4 = re.sub(r"\D", "", p or "")[-4:]
    if stored_digits and len(req_last4) == 4 and not stored_digits.endswith(req_last4):
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
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="Webers_Ticket_{t_code}.pdf"'},
    )


@app.post("/api/public/dropoff")
def submit_public_dropoff(payload: PublicDropoffInput):
    if payload.website_hp.strip():
        return {"logged": True}
    cust = payload.customer_name.strip()[:80]
    if len(cust) < 2:
        raise HTTPException(400, "Please enter your name, fire department, or company name.")
    ph_clean = clean_phone(payload.customer_phone)
    ph_digits = re.sub(r"\D", "", ph_clean)
    if len(ph_digits) < 10:
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
    if etype == "DROPOFF" and len(payload.street_address.strip() or addr_clean) < 4:
        raise HTTPException(400, "Please enter your Billing / Mailing Street Address & City.")
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
        cur.execute(
            """INSERT INTO schedule_jobs
               (created_at, scheduled_date, due_date, job_type, account_type, po_station, contact_person,
                customer_name, customer_phone, location_notes, bottle_count, tank_summary,
                valves_attached, needs_air_fill, priority, rush_fee, holding_zone, prep_status, status, notes, synced)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, ?, ?, 'OUTBOUND_TRUCK', 'WAITING', 'OPEN', ?, 0);""",
            (
                ts, sched_date, promised, jtype, acct, po_st, contact_p,
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

    bump_db_version(conn)
    conn.commit()
    conn.close()
    trigger_sheets_sync_bg()

    ticket_code = f"BR-{did:04d}"
    last4 = ph_digits[-4:]
    receipt_pdf_path = f"/api/public/receipt/{ticket_code}.pdf?p={last4}"
    if em_clean or etype in ("DROPOFF", "TRUCK_REQUEST"):
        trigger_ticket_email_bg({
            "ticket_no": ticket_code,
            "entry_type": etype,
            "customer_name": cust,
            "contact_person": contact_p,
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


@app.post("/api/dropoffs/{dropoff_id}/status")
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


@app.delete("/api/dropoffs/{dropoff_id}")
def delete_dropoff(dropoff_id: int):
    conn = get_conn()
    conn.execute("DELETE FROM dropoffs WHERE id=?;", (dropoff_id,))
    bump_db_version(conn)
    conn.commit()
    conn.close()
    return {"deleted": dropoff_id}


# --- BINDER, FULL MONTH CALENDAR & SCHEDULE / DISPATCH ROUTES ---
@app.post("/api/binder")
def set_binder_sheet(payload: BinderActionInput):
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


@app.get("/api/calendar/month")
def get_full_month_calendar(year: Optional[int] = None, month: Optional[int] = None):
    est_dt = now_eastern().date()
    y = year if (year and 2020 <= year <= 2040) else est_dt.year
    m = month if (month and 1 <= month <= 12) else est_dt.month
    conn = get_conn()
    s_map = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings;").fetchall()}
    grid = build_month_calendar_grid(conn, y, m, s_map)
    conn.close()
    return grid


@app.get("/api/schedule/quote")
def get_phone_turnaround_quote(bottles: int = 1):
    conn = get_conn()
    q = calculate_turnaround_quote(conn, bottles)
    conn.close()
    return q


@app.post("/api/schedule/day-plan")
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


@app.post("/api/schedule/jobs")
def create_schedule_job(payload: ScheduleJobInput):
    cust = payload.customer_name.strip()
    if not cust:
        raise HTTPException(400, "Customer / Department name is required.")
    ph = clean_phone(payload.customer_phone)
    acct = (payload.account_type or "WALK_IN").strip().upper()
    ts = now_eastern().strftime("%Y-%m-%d %I:%M %p")
    pri = "RUSH" if payload.rush_fee else payload.priority.strip().upper()

    contact_p = payload.contact_person.strip()[:60]
    conn = get_conn()
    upsert_rolodex_from_activity(conn, cust, ph, acct, contact_p, '', payload.street_address.strip() or payload.location_notes.strip()[:120], payload.city.strip(), payload.state.strip(), payload.zip_code.strip())
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO schedule_jobs
           (created_at, scheduled_date, due_date, job_type, account_type, po_station, contact_person,
            customer_name, customer_phone, location_notes, bottle_count, tank_summary,
            valves_attached, needs_air_fill, priority, rush_fee, holding_zone,
            prep_status, status, notes, synced)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0);""",
        (
            ts,
            payload.scheduled_date.strip(),
            payload.due_date.strip() or payload.scheduled_date.strip(),
            payload.job_type.strip().upper(),
            acct,
            payload.po_station.strip()[:60],
            contact_p,
            cust,
            ph,
            payload.location_notes.strip()[:140],
            max(1, int(payload.bottle_count or 1)),
            payload.tank_summary.strip()[:160],
            1 if payload.valves_attached else 0,
            0,
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
    row = dict(conn.execute("SELECT * FROM schedule_jobs WHERE id=?;", (jid,)).fetchone())
    conn.close()
    return row


@app.put("/api/schedule/jobs/{job_id}")
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

    conn.execute(
        """UPDATE schedule_jobs SET
           scheduled_date=?, due_date=?, job_type=?, customer_name=?, customer_phone=?,
           location_notes=?, bottle_count=?, tank_summary=?, priority=?, rush_fee=?,
           holding_zone=?, prep_status=?, status=?, notes=?, synced=0
           WHERE id=?;""",
        (
            payload.scheduled_date if payload.scheduled_date is not None else rec["scheduled_date"],
            payload.due_date if payload.due_date is not None else rec["due_date"],
            payload.job_type if payload.job_type is not None else rec["job_type"],
            payload.customer_name.strip() if payload.customer_name is not None else rec["customer_name"],
            clean_phone(payload.customer_phone) if payload.customer_phone is not None else rec["customer_phone"],
            payload.location_notes if payload.location_notes is not None else rec["location_notes"],
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
    updated = dict(conn.execute("SELECT * FROM schedule_jobs WHERE id=?;", (job_id,)).fetchone())
    conn.close()
    return updated


@app.delete("/api/schedule/jobs/{job_id}")
def delete_schedule_job(job_id: int):
    conn = get_conn()
    conn.execute("DELETE FROM schedule_jobs WHERE id=?;", (job_id,))
    bump_db_version(conn)
    conn.commit()
    conn.close()
    return {"deleted": job_id}
# --- CORE TELEMETRY, LIVE TV & BOOTSTRAP ---
@app.get("/api/ping")
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
    conn.close()
    return {
        "db_version": ver,
        "unsynced_count": unsynced,
        "backroom_waiting": backroom_waiting,
        "low_stock_count": low_stock_count,
    }


@app.get("/api/tv")
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
        day_jobs = [j for j in upcoming_jobs if j["scheduled_date"] == p["work_date"]]
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
        ticker.append(f"🔨 ZONE D FINISH: {needs_stamp_count} DRIED CYLINDER(S) NEED B386 STAMP / STICKER")

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
        },
        "ticker": ticker,
    }


@app.get("/api/bootstrap")
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

    raw_records = [
        enrich_record_meta(dict(r), est_today)
        for r in conn.execute(
            "SELECT * FROM cylinder_records WHERE work_date=? OR status='QUEUED' ORDER BY id ASC;",
            (target_date,),
        ).fetchall()
    ]
    queued_part = sorted([r for r in raw_records if r["status"] == "QUEUED"], key=queue_sort_key)
    done_part = [r for r in raw_records if r["status"] == "COMPLETED" and r["work_date"] == target_date]
    records = queued_part + done_part

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
        "in_shop_post_test": in_shop_post_test,
        "active_valves": active_valves,
        "dropoffs": dropoffs,
        "dates_summary": dates_summary,
        "customers_summary": customers_summary,
        "customers_rolodex": rolodex,
        "recalls": recalls,
        "shop_supplies": supplies,
        "low_stock_count": sum(1 for s in supplies if s.get("low_stock")),
        "pricing_rates": rates,
        "invoices": invoices,
        "feedback_items": feedback_items,
        "unsynced_count": unsynced,
    }


@app.post("/api/admin/wipe-test-data")
async def admin_wipe_test_data():
    conn = get_conn()
    webhook_row = conn.execute("SELECT value FROM settings WHERE key='sheets_webhook_url';").fetchone()
    webhook_url = (webhook_row["value"] if webhook_row else "").strip()
    wipe_all_operational_data(conn)
    conn.commit()
    conn.close()

    sheets_wiped = False
    if webhook_url:
        try:
            sheets_wiped = await asyncio.to_thread(wipe_sheets_remote, webhook_url)
        except Exception:
            sheets_wiped = False
    return {"wiped": True, "sheets_wiped": sheets_wiped}


@app.post("/api/settings")
def update_settings(payload: SettingsInput, request: Request):
    global CACHED_VISION_MODEL
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
        CACHED_VISION_MODEL = None
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('gemini_api_key', ?);", (payload.gemini_api_key.strip(),))
    if payload.sheets_webhook_url is not None:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('sheets_webhook_url', ?);", (payload.sheets_webhook_url.strip(),))
    if payload.feedback_webhook_url is not None:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('feedback_webhook_url', ?);", (payload.feedback_webhook_url.strip(),))
    if payload.testing_mode is not None:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('testing_mode', ?);", ("1" if payload.testing_mode else "0",))
    pin_changed = False
    if payload.shop_pin is not None and payload.shop_pin.strip():
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('shop_pin', ?);", (payload.shop_pin.strip(),))
        pin_changed = True
    bump_db_version(conn)
    conn.commit()
    conn.close()

    resp = JSONResponse({"saved": True})
    if pin_changed:
        is_https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
        resp.set_cookie(
            key=COOKIE_NAME,
            value=compute_session_token(),
            max_age=60 * 60 * 24 * 30,
            httponly=True,
            samesite="lax",
            secure=is_https,
        )
    return resp


@app.post("/api/crew")
def add_crew_member(payload: CrewInput):
    name = payload.full_name.strip()
    init = payload.initials.strip().upper()[:4]
    if not name or not init:
        raise HTTPException(400, "Enter both crew name and initials.")
    conn = get_conn()
    conn.execute(
        """INSERT INTO crew_members (full_name, initials, role, active) VALUES (?, ?, ?, 1)
           ON CONFLICT(full_name) DO UPDATE SET initials=excluded.initials, role=excluded.role, active=1;""",
        (name, init, payload.role.strip() or "Visual Inspection"),
    )
    bump_db_version(conn)
    conn.commit()
    conn.close()
    return {"added": True}


@app.delete("/api/crew/{crew_id}")
def deactivate_crew_member(crew_id: int):
    conn = get_conn()
    row = conn.execute("SELECT initials FROM crew_members WHERE id=?;", (crew_id,)).fetchone()
    if row and row["initials"] == "RP":
        conn.close()
        raise HTTPException(400, "Ryan Pearce (RP) is the primary DOT Facility Manager and cannot be removed.")
    conn.execute("UPDATE crew_members SET active=0 WHERE id=?;", (crew_id,))
    bump_db_version(conn)
    conn.commit()
    conn.close()
    return {"removed": crew_id}


@app.post("/api/sync-now")
async def manual_sync_now():
    try:
        return await run_guarded_sheets_sync()
    except Exception as exc:
        raise HTTPException(502, f"Google Sheets sync error: {exc}")


@app.post("/api/verify-ladder")
def log_verification_ladder(payload: VerificationLadderInput):
    est = now_eastern()
    t_date = payload.test_date or est.date().isoformat()
    t_time = est.strftime("%I:%M %p")
    run_id = f"{t_date}-{payload.jacket_type[:5]}-{est.strftime('%H%M%S')}"
    logged_by = (payload.logged_by or "RP").strip().upper()[:4] or "RP"

    conn = get_conn()
    cal_serial = conn.execute("SELECT value FROM settings WHERE key='calibrated_serial';").fetchone()["value"]
    expected_rows = conn.execute(
        """SELECT pressure_psi, expected_cc FROM calibration_targets
           WHERE serial_number=? AND pressure_psi <= ? ORDER BY pressure_psi ASC;""",
        (cal_serial, payload.max_target_psi),
    ).fetchall()

    if not expected_rows or len(payload.steps) != len(expected_rows):
        conn.close()
        raise HTTPException(400, "Complete all calibration steps in succession.")

    evaluated = []
    all_within_one_pct = True
    for idx, exp_row in enumerate(expected_rows):
        step = payload.steps[idx]
        exp_psi = int(exp_row["pressure_psi"])
        expected_a = float(exp_row["expected_cc"])
        actual_b = float(step.actual_total_cc)
        pct_dev = round(((expected_a - actual_b) / expected_a) * 100.0, 2)
        if abs(pct_dev) > 1.0:
            all_within_one_pct = False
        is_last = idx == (len(expected_rows) - 1)
        perm = payload.permanent_cc if is_last else 0.0
        elastic = round(actual_b - perm, 2)
        evaluated.append((exp_psi, expected_a, actual_b, perm, elastic, pct_dev))

    run_passed = 1 if (all_within_one_pct and payload.permanent_cc == 0.0) else 0
    cur = conn.cursor()
    for exp_psi, expected_a, actual_b, perm, elastic, pct_dev in evaluated:
        step_passed = 1 if (run_passed and abs(pct_dev) <= 1.0) else 0
        cur.execute(
            """INSERT INTO daily_verifications
               (run_id, test_date, test_time, jacket_type, calibrated_serial, max_target_psi,
                target_pressure_psi, expected_cc, actual_total_cc, permanent_cc,
                elastic_cc, percent_deviation, passed, logged_by, operator_name, operator_initials)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Ryan Pearce', 'RP');""",
            (
                run_id, t_date, t_time, payload.jacket_type, cal_serial,
                payload.max_target_psi, exp_psi, expected_a, actual_b,
                perm, elastic, pct_dev, step_passed, logged_by,
            ),
        )
    bump_db_version(conn)
    conn.commit()
    conn.close()
    trigger_sheets_sync_bg()
    return {
        "passed": bool(run_passed),
        "max_calibrated_psi": payload.max_target_psi if run_passed else 0,
        "max_allowed_test_psi": dot_max_allowed_test_psi(payload.max_target_psi) if run_passed else 0,
    }
# --- INTAKE, REVALVE ADD-ONS, STAGE FLOW, VISION SCANNER & HYDRO COMPLETION ---
def lookup_spec_defaults(conn, dot_spec: str, service_psi: int) -> Optional[dict]:
    sp = (dot_spec or "").strip().upper().replace("SP-", "SP")
    rows = conn.execute(
        """SELECT test_psi, outside_dia, length_in, ree_limit, category FROM cga_specs
           WHERE UPPER(dot_spec)=? AND service_psi=? ORDER BY id ASC;""",
        (sp, service_psi),
    ).fetchall()
    if not rows:
        return None
    if len(rows) == 1:
        d = dict(rows[0])
        d["exact_size"] = True
        return d
    unique_rees = {r["ree_limit"] for r in rows if r["ree_limit"] is not None}
    return {
        "test_psi": rows[0]["test_psi"],
        "outside_dia": "",
        "length_in": "",
        "ree_limit": next(iter(unique_rees)) if len(unique_rees) == 1 else None,
        "exact_size": False,
    }


def insert_cylinder_record(payload: IntakeInput) -> dict:
    est = now_eastern()
    w_date = payload.work_date or est.date().isoformat()
    intake_ts = est.strftime("%Y-%m-%d %I:%M %p")
    v_tag = payload.valve_tag.strip().lstrip("#")
    serial_clean = payload.serial_number.strip().upper().replace(" ", "")
    cust_clean = payload.customer_name.strip()
    phone_clean = clean_phone(payload.customer_phone)
    spec_clean = payload.dot_spec.strip().upper().replace("SP-", "SP")
    if not cust_clean or not serial_clean or not spec_clean or payload.service_psi <= 0:
        raise HTTPException(400, "Customer, Serial #, DOT Spec, and Service Pressure are required.")

    conn = get_conn()
    dup_tag = check_duplicate_active_valve_tag(conn, v_tag)
    if dup_tag:
        conn.close()
        raise HTTPException(
            409,
            f"Valve Tag #{v_tag} is currently active on SN {dup_tag['serial_number']} ({dup_tag['customer_name']}). Mark that valve reinstalled before reusing Tag #{v_tag}.",
        )

    if not payload.allow_duplicate:
        dup_sn = check_duplicate_serial_queued(conn, serial_clean, w_date)
        if dup_sn and dup_sn["status"] == "QUEUED":
            conn.close()
            raise HTTPException(
                409,
                f"Serial #{serial_clean} is already in the active intake queue for {dup_sn['customer_name']}.",
            )

    if not phone_clean:
        rolo_ph = conn.execute(
            "SELECT phone FROM customers WHERE UPPER(customer_name)=UPPER(?) AND phone!='' LIMIT 1;",
            (cust_clean,),
        ).fetchone()
        if rolo_ph:
            phone_clean = rolo_ph["phone"]
        else:
            prev_ph = conn.execute(
                "SELECT customer_phone FROM cylinder_records WHERE UPPER(customer_name)=UPPER(?) AND customer_phone!='' ORDER BY id DESC LIMIT 1;",
                (cust_clean,),
            ).fetchone()
            if prev_ph:
                phone_clean = prev_ph["customer_phone"]

    upsert_rolodex_from_activity(conn, cust_clean, phone_clean)

    pri = "RUSH" if payload.rush_fee else (payload.priority or "STANDARD").strip().upper()
    rfee = 1 if (payload.rush_fee or pri == "RUSH") else 0
    prom_date = payload.promised_date.strip()

    if payload.dropoff_id:
        dr = conn.execute("SELECT rush_requested, promised_date FROM dropoffs WHERE id=?;", (payload.dropoff_id,)).fetchone()
        if dr:
            if dr["rush_requested"]:
                pri, rfee = "RUSH", 1
            if not prom_date and dr["promised_date"]:
                prom_date = dr["promised_date"]

    if payload.schedule_job_id:
        sj = conn.execute("SELECT priority, rush_fee, due_date FROM schedule_jobs WHERE id=?;", (payload.schedule_job_id,)).fetchone()
        if sj:
            if sj["rush_fee"] or sj["priority"] == "RUSH":
                pri, rfee = "RUSH", 1
            elif sj["priority"] == "HIGH" and pri == "STANDARD":
                pri = "HIGH"
            if not prom_date and sj["due_date"]:
                prom_date = sj["due_date"]

    dia = payload.outside_dia.strip()
    length = payload.length_in.strip()
    ree = payload.ree_limit
    calc_test_psi = payload.test_psi

    cga_match = lookup_spec_defaults(conn, spec_clean, payload.service_psi)
    if not calc_test_psi:
        calc_test_psi = int(cga_match["test_psi"]) if cga_match else round((payload.service_psi * 5) / 3)
    if cga_match and cga_match.get("exact_size"):
        if not dia:
            dia = cga_match["outside_dia"]
        if not length:
            length = cga_match["length_in"]
        if ree is None:
            ree = cga_match["ree_limit"]

    if spec_clean in ("3A", "3AA", "3AL") and ree == 7.2:
        ree = None

    phys_size = f"{dia}x{length}".strip("x") if (dia or length) else ""
    gas_clean = normalize_gas_service(payload.gas_service)
    zone = payload.holding_zone.strip() or "ZONE_C_READY_RACK"

    cur = conn.cursor()
    cur.execute(
        """INSERT INTO cylinder_records
           (work_date, intake_at, sheet_no, customer_name, customer_phone, dropoff_id, schedule_job_id,
            priority, rush_fee, promised_date, holding_zone,
            serial_number, valve_tag, mfg_id, mfg_date, gas_service,
            outside_dia, length_in, physical_size, dot_spec, service_psi, test_psi,
            ree_limit, service_mode, shop_stage, intake_by, operator_name, operator_initials, notes)
           VALUES (?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'IN_SHOP', ?, 'Ryan Pearce', 'RP', ?);""",
        (
            w_date, intake_ts, cust_clean, phone_clean,
            payload.dropoff_id, payload.schedule_job_id, pri, rfee, prom_date, zone,
            serial_clean, v_tag, payload.mfg_id.strip().upper(), payload.mfg_date.strip(),
            gas_clean, dia, length, phys_size,
            spec_clean, payload.service_psi, calc_test_psi,
            ree, payload.service_mode, payload.intake_by.strip().upper()[:4] or "RP", payload.notes,
        ),
    )
    rid = cur.lastrowid

    if payload.dropoff_id:
        conn.execute(
            """UPDATE dropoffs
               SET imported_count = COALESCE(imported_count, 0) + 1,
                   holding_zone = 'ZONE_C_READY_RACK',
                   status = CASE
                       WHEN COALESCE(imported_count, 0) + 1 >= bottle_count THEN 'ARCHIVED'
                       WHEN status='IN_BACKROOM' THEN 'CHECKED_IN'
                       ELSE status
                   END,
                   synced = 0
               WHERE id = ?;""",
            (payload.dropoff_id,),
        )
    if payload.schedule_job_id:
        conn.execute(
            "UPDATE schedule_jobs SET prep_status='DEVALVED_TAGGED', holding_zone='ZONE_C_READY_RACK', status='PREPPED' WHERE id=?;",
            (payload.schedule_job_id,),
        )

    bump_db_version(conn)
    conn.commit()
    row = dict(conn.execute("SELECT * FROM cylinder_records WHERE id=?;", (rid,)).fetchone())
    conn.close()
    return row


@app.post("/api/intake")
def add_intake(payload: IntakeInput):
    return insert_cylinder_record(payload)


@app.post("/api/records/{record_id}/priority")
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


@app.post("/api/records/{record_id}/addons")
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


@app.post("/api/valve-tag")
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


@app.post("/api/records/{record_id}/stage")
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


@app.post("/api/batch/stage")
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
        conn.execute(
            f"UPDATE cylinder_records SET shop_stage='PICKED_UP', valve_reinstalled=1, holding_zone='PICKED_UP', picked_up_by=?, picked_up_at=?, synced=0 {where_sql};",
            tuple([who, ts] + params),
        )
        conn.execute(
            "UPDATE dropoffs SET status='ARCHIVED', synced=0 WHERE customer_name=? AND status!='ARCHIVED';",
            (payload.customer_name,),
        )
        conn.execute(
            "UPDATE schedule_jobs SET status='COMPLETED' WHERE customer_name=? AND status!='COMPLETED';",
            (payload.customer_name,),
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


@app.delete("/api/records/{record_id}")
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


def discover_vision_models(api_key: str) -> list[str]:
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    skip_tokens = (
        "tts", "embedding", "imagen", "veo", "audio", "robotics",
        "aqa", "learnlm", "image", "computer-use", "live", "1.5-",
    )
    scored = []
    for m in data.get("models", []):
        if "generateContent" not in m.get("supportedGenerationMethods", []):
            continue
        short = m.get("name", "").replace("models/", "")
        low = short.lower()
        if not low.startswith("gemini-") or any(tok in low for tok in skip_tokens):
            continue
        is_25_flash = 1 if low == "gemini-2.5-flash" else 0
        is_flash = 1 if ("flash" in low and "lite" not in low) else 0
        is_stable = 0 if ("preview" in low or "exp" in low) else 1
        ver = re.search(r"gemini-(\d+)(?:\.(\d+))?", low)
        major = int(ver.group(1)) if ver else 0
        minor = int(ver.group(2) or 0) if ver else 0
        scored.append(((is_25_flash, is_flash, is_stable, major, minor), short))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [name for _, name in scored]


def call_gemini_vision_rest(api_key: str, images_b64: list[str], prompt: str) -> dict:
    global CACHED_VISION_MODEL
    candidates: list[str] = []
    if CACHED_VISION_MODEL:
        candidates.append(CACHED_VISION_MODEL)
    try:
        for d in discover_vision_models(api_key):
            if d not in candidates:
                candidates.append(d)
    except Exception:
        pass
    for fb in ("gemini-2.5-flash", "gemini-3-flash-preview", "gemini-2.0-flash"):
        if fb not in candidates:
            candidates.append(fb)

    schema = {
        "type": "OBJECT",
        "properties": {
            "detected_customer": {"type": "STRING", "nullable": True},
            "serial_number": {"type": "STRING", "nullable": True},
            "valve_tag": {"type": "STRING", "nullable": True},
            "mfg_id": {"type": "STRING", "nullable": True},
            "mfg_date": {"type": "STRING", "nullable": True},
            "dot_spec": {"type": "STRING", "nullable": True},
            "service_psi": {"type": "INTEGER", "nullable": True},
            "outside_dia": {"type": "STRING", "nullable": True},
            "length_in": {"type": "STRING", "nullable": True},
            "size_source": {"type": "STRING", "nullable": True},
            "ree_limit": {"type": "NUMBER", "nullable": True},
            "gas_service": {"type": "STRING", "nullable": True},
        },
    }

    parts = [{"inline_data": {"mime_type": "image/jpeg", "data": b64}} for b64 in images_b64]
    parts.append({"text": prompt})
    ctx = ssl.create_default_context()
    last_error = "No compatible Gemini vision model responded."

    for model_name in candidates[:5]:
        low_m = model_name.lower()
        gen_cfg: dict = {
            "temperature": 0.1,
            "responseMimeType": "application/json",
            "responseSchema": schema,
        }
        if "2.5-flash" in low_m:
            gen_cfg["thinkingConfig"] = {"thinkingBudget": 1024}
        elif low_m.startswith("gemini-3"):
            gen_cfg["thinkingConfig"] = {"thinkingLevel": "LOW"}

        for attempt in range(2):
            body_bytes = json.dumps({"contents": [{"parts": parts}], "generationConfig": gen_cfg}).encode("utf-8")
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
            req = urllib.request.Request(url, data=body_bytes, headers={"Content-Type": "application/json"}, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=55, context=ctx) as resp:
                    resp_json = json.loads(resp.read().decode("utf-8"))
                    cand_parts = resp_json["candidates"][0]["content"]["parts"]
                    raw_text = next(
                        (p["text"].strip() for p in reversed(cand_parts) if "text" in p and not p.get("thought")),
                        cand_parts[-1].get("text", "").strip(),
                    )
                    if raw_text.startswith("```"):
                        raw_text = re.sub(r"^```(?:json)?|```$", "", raw_text, flags=re.MULTILINE).strip()
                    parsed = json.loads(raw_text)
                    CACHED_VISION_MODEL = model_name
                    return parsed
            except urllib.error.HTTPError as http_err:
                err_detail = http_err.read().decode("utf-8", errors="ignore")
                if http_err.code == 400 and "thinkingConfig" in gen_cfg and attempt == 0:
                    gen_cfg.pop("thinkingConfig", None)
                    continue
                last_error = f"Gemini HTTP {http_err.code} ({model_name}): {err_detail[:180]}"
                break
            except Exception as exc:
                last_error = f"{model_name}: {exc}"
                break
    raise RuntimeError(last_error)


@app.post("/api/scan-bottle")
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


@app.post("/api/records/{record_id}/complete")
def complete_record(record_id: int, payload: TestCompleteInput):
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

    tested_by = (payload.tested_by or "RP").strip().upper()[:4] or "RP"
    completed_ts = now_eastern().strftime("%I:%M %p")
    rin_marked = 1 if (payload.rin_marked or payload.rin_mark_type) else 0
    rin_type = payload.rin_mark_type.strip().upper() if payload.rin_mark_type else ("MARKED" if rin_marked else "")

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
           valve_tag=?, valve_reinstalled=?, rin_marked=?, rin_mark_type=?,
           total_cc=?, permanent_cc=?, elastic_cc=?, percent_permanent=?,
           coating_type=?, coating_condition=?,
           chk_corrosion=?, chk_dents_dings=?, chk_cuts_gouges=?, chk_leaks=?,
           chk_fire_damage=?, chk_bulges=?, chk_neck_defects=?,
           relief_valve_changed=?, addon_oring=?, addon_burst_disc=?, addon_clean=?, condemn_action=?,
           visual_pass=?, hydro_disposition=?, visual_disposition=?,
           tested_by=?, completed_at=?, operator_name='Ryan Pearce', operator_initials='RP', notes=?, synced=0
           WHERE id=?;""",
        (
            eff_test_date, assigned_sheet_no, next_stage, next_zone, ver_id,
            payload.jacket_type if rec["service_mode"] == "HYDRO_AND_VISUAL" else "Road/Visual",
            final_vtag, final_reinst, rin_marked, rin_type,
            payload.total_cc, payload.permanent_cc, elastic, pct_perm,
            payload.coating_type, payload.coating_condition,
            payload.chk_corrosion, payload.chk_dents_dings, payload.chk_cuts_gouges,
            payload.chk_leaks, payload.chk_fire_damage, payload.chk_bulges, payload.chk_neck_defects,
            rel_chg, oring, bdisc, clean, c_act,
            vis_pass, hydro_disp, vis_disp,
            tested_by, completed_ts, payload.notes, record_id,
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
@app.put("/api/records/{record_id}")
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
    cust_clean = payload.customer_name.strip()
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

    elastic = rec["elastic_cc"]
    pct_perm = rec["percent_permanent"]
    hydro_disp = rec["hydro_disposition"]

    if rec["status"] == "COMPLETED":
        if rec["service_mode"] == "HYDRO_AND_VISUAL" and payload.total_cc and payload.total_cc > 0:
            perm_v = payload.permanent_cc or 0.0
            elastic = round(payload.total_cc - perm_v, 2)
            pct_perm = round((perm_v / payload.total_cc) * 100.0, 1)
            ree_failed = payload.ree_limit is not None and elastic > float(payload.ree_limit)
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

    conn.execute(
        """UPDATE cylinder_records SET
           customer_name=?, customer_phone=?, priority=?, rush_fee=?, promised_date=?, holding_zone=?,
           serial_number=?, valve_tag=?, valve_reinstalled=?, rin_marked=?, rin_mark_type=?, shop_stage=?,
           mfg_id=?, mfg_date=?, gas_service=?, outside_dia=?, length_in=?, physical_size=?,
           dot_spec=?, service_psi=?, test_psi=?, ree_limit=?,
           total_cc=?, permanent_cc=?, elastic_cc=?, percent_permanent=?,
           coating_condition=?, relief_valve_changed=?,
           addon_oring=?, addon_burst_disc=?, addon_clean=?, condemn_action=?,
           chk_corrosion=?, chk_dents_dings=?, chk_cuts_gouges=?, chk_leaks=?,
           chk_fire_damage=?, chk_bulges=?, chk_neck_defects=?,
           visual_pass=?, hydro_disposition=?, visual_disposition=?,
           operator_name='Ryan Pearce', operator_initials='RP', notes=?, synced=0
           WHERE id=?;""",
        (
            cust_clean, phone_clean, pri, rfee,
            payload.promised_date.strip(), payload.holding_zone or rec["holding_zone"] or "ZONE_C_READY_RACK",
            payload.serial_number.strip().upper().replace(" ", ""),
            vtag, reinst, rin_marked, rin_type, payload.shop_stage or rec["shop_stage"] or "IN_SHOP",
            payload.mfg_id.strip().upper(), payload.mfg_date.strip(), gas_clean,
            dia, length, phys_size, spec_clean, payload.service_psi, payload.test_psi, payload.ree_limit,
            payload.total_cc, payload.permanent_cc, elastic, pct_perm,
            payload.coating_condition, rel_chg, oring, bdisc, clean, c_act,
            payload.chk_corrosion, payload.chk_dents_dings, payload.chk_cuts_gouges, payload.chk_leaks,
            payload.chk_fire_damage, payload.chk_bulges, payload.chk_neck_defects,
            vis_pass, hydro_disp, vis_disp, payload.notes, record_id,
        ),
    )
    bump_db_version(conn)
    conn.commit()
    updated = dict(conn.execute("SELECT * FROM cylinder_records WHERE id=?;", (record_id,)).fetchone())
    conn.close()
    if updated["status"] == "COMPLETED":
        trigger_sheets_sync_bg()
    return updated


@app.get("/api/search")
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


# --- CUSTOMER ROLODEX, MAILING CSV EXPORT & 5-YR / 1-YR RECALLS ---
@app.get("/api/customers")
def list_customers_rolodex():
    conn = get_conn()
    rows = [dict(r) for r in conn.execute("SELECT * FROM customers ORDER BY customer_name COLLATE NOCASE ASC;").fetchall()]
    conn.close()
    return rows


@app.post("/api/customers")
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


@app.delete("/api/customers/{cust_id}")
def delete_customer_rolodex(cust_id: int):
    conn = get_conn()
    conn.execute("DELETE FROM customers WHERE id=?;", (cust_id,))
    bump_db_version(conn)
    conn.commit()
    conn.close()
    return {"deleted": cust_id}


@app.get("/api/customers/export.csv")
def export_customers_csv():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM customers ORDER BY customer_name COLLATE NOCASE ASC;").fetchall()
    conn.close()
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


@app.get("/api/recalls")
def get_due_recalls():
    conn = get_conn()
    rows = compute_recall_list(conn, today_eastern())
    conn.close()
    return rows


# --- 1-CLICK REORDER SHOP SUPPLIES HUB ---
@app.get("/api/supplies")
def list_shop_supplies():
    conn = get_conn()
    rows = [dict(r) for r in conn.execute("SELECT * FROM shop_supplies ORDER BY low_stock DESC, sort_order ASC, id ASC;").fetchall()]
    conn.close()
    return rows


@app.post("/api/supplies")
def save_shop_supply(payload: SupplyUpsertInput):
    name = payload.item_name.strip()
    if not name:
        raise HTTPException(400, "Supply item name is required.")
    conn = get_conn()
    if payload.id:
        conn.execute(
            """UPDATE shop_supplies SET
               category=?, item_name=?, spec_note=?, vendor_name=?, reorder_url=?,
               low_stock=?, last_ordered_date=?, sort_order=?
               WHERE id=?;""",
            (
                payload.category.strip() or "Shop Supplies", name, payload.spec_note.strip(),
                payload.vendor_name.strip(), payload.reorder_url.strip(),
                1 if payload.low_stock else 0, payload.last_ordered_date.strip(), payload.sort_order, payload.id,
            ),
        )
    else:
        conn.execute(
            """INSERT INTO shop_supplies
               (category, item_name, spec_note, vendor_name, reorder_url, low_stock, last_ordered_date, sort_order)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?);""",
            (
                payload.category.strip() or "Shop Supplies", name, payload.spec_note.strip(),
                payload.vendor_name.strip(), payload.reorder_url.strip(),
                1 if payload.low_stock else 0, payload.last_ordered_date.strip(), payload.sort_order,
            ),
        )
    bump_db_version(conn)
    conn.commit()
    conn.close()
    return {"saved": True}


@app.post("/api/supplies/{supply_id}/stock")
def toggle_supply_stock(supply_id: int, payload: SupplyLowStockInput):
    conn = get_conn()
    if payload.mark_ordered_today:
        conn.execute(
            "UPDATE shop_supplies SET low_stock=0, last_ordered_date=? WHERE id=?;",
            (today_eastern(), supply_id),
        )
    else:
        conn.execute(
            "UPDATE shop_supplies SET low_stock=? WHERE id=?;",
            (1 if payload.low_stock else 0, supply_id),
        )
    bump_db_version(conn)
    conn.commit()
    conn.close()
    return {"updated": supply_id}


@app.delete("/api/supplies/{supply_id}")
def delete_shop_supply(supply_id: int):
    conn = get_conn()
    conn.execute("DELETE FROM shop_supplies WHERE id=?;", (supply_id,))
    bump_db_version(conn)
    conn.commit()
    conn.close()
    return {"deleted": supply_id}


# --- PRICING & BILLING ---
@app.post("/api/pricing")
def upsert_pricing_rates(rates: list[PricingRateUpsert]):
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


@app.delete("/api/pricing/{code}")
def delete_pricing_rate(code: str):
    conn = get_conn()
    conn.execute("DELETE FROM pricing_rates WHERE UPPER(code)=UPPER(?);", (code.strip(),))
    bump_db_version(conn)
    conn.commit()
    conn.close()
    return {"deleted": code}


@app.get("/api/unbilled")
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


@app.post("/api/invoices")
def create_invoice(payload: InvoiceCreateInput):
    if not payload.items:
        raise HTTPException(400, "Add at least one line item to create an invoice.")
    est_today = today_eastern()
    conn = get_conn()
    nxt_num = conn.execute("SELECT COUNT(*) + 1 FROM invoices;").fetchone()[0]
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


@app.post("/api/invoices/{inv_id}/status")
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


@app.delete("/api/invoices/{inv_id}")
def void_and_delete_invoice(inv_id: int):
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



SHOP_HELP_SYSTEM_GUIDE = """You are the built-in Shop Operations & Troubleshooting AI Assistant for Weber's Tank Testing (U.S. D.O.T. RIN B386) at 3086 Legionville Rd, Baden, PA 15005 (Phone: 724-266-8008).
You assist Facility Manager Ryan Pearce (RP), Kenny, and the shop crew. Keep answers straight to the point, practical, friendly, and brief (bullet points or 2-4 short steps).

EXACT SYSTEM MAP & RULES:
1. PUBLIC SITE (weberstanktesting.com):
   - Has 3 buttons under #dropoff: '📥 BACKROOM DROP-OFF' (captures Name, Contact, Phone, Email, Billing/Mailing Address, Bottle Count & Types), '✅ BACKROOM PICK-UP', and '🚚 TRUCK PICKUP & DELIVERY'.
   - Issues a digital BR-XXXX ticket card + downloadable PDF receipt + live 5-step tracker. Every address & email auto-saves to the Customer Rolodex for billing, newsletters, and 5-year re-cert notices.
   - TESTING FACILITY ONLY: Weber's NEVER refills cylinders. Passing tanks return clean, heated-air dried, revalved, and depressurized.
2. SHOP OPS (/ops — 4 Stations on Left Menu):
   - '1. Start & Cal': Shows active 12-row Form 393-001 Retest Sheet # (assigns ONLY when a hydro test completes, never when queued). Calibrates Short Jacket (usually 3000 PSI -> covers test PSI up to 3500#) and Tall Jacket (Method B +-1.0% with Master Cyl 1SF11904). Official DOT PDFs always sign off under Ryan Pearce (RP).
   - '2. Prep & Scan': Import waiting 24/7 Backroom Door batches or Scheduled jobs via 'Lock & Scan Batch'. Turn ON 'Lock Batch Specs' for multi-bottle batches so specs stay locked while Serial # and random Valve Tag # reset blank after each bottle.
   - RANDOM VALVE TAGS: Tags (#47, #12, etc.) are grabbed randomly from the bin. If blocked by 'Valve Tag #X is currently active on SN...', go to '4. Finish & Shelf' -> 'Active Valve & Regulator Tags' and click 'Reinstall & Free #X' (or pick another tag).
   - '3. Hydro Stand': Tap any queued cylinder pill at top -> enter Total Expansion (cc) & Permanent Expansion (cc). If >10% perm, >REE, or visual fail, it flips to Code D Condemned and asks for PHMSA legal disposition.
   - '4. Finish & Shelf': Passing tanks CANNOT move to Zone E Pickup Shelf until marked 'Stamped' or 'Stickered' (B386). Tap '+ Neck O-Ring', '+ Burst Disc', or '+ Tumble/Clean' here so parts auto-add to the invoice. Click 'Claim Slip' to print the Zone E slip (QR code auto-opens PICK-UP sign-out).
3. SCHEDULE (/schedule — 4 Left-Menu Tabs):
   - '1. Month Calendar': Full Sunday-Saturday month grid. Use 'Prev / Next', month picker, or yellow 'Jump to Today' (also 'Prev | Today | Next' date stepper in top-right bar of every page). Click any day square to set Operating Window (Full Hydro, Prep Only, Road Run, or 24/7 Backroom Only) & bottle capacity.
   - '2. Book & Quote': Full booking form + Phone Turnaround Calculator.
   - '3. Active Runs & Prep' & '4. Backroom Door'.
4. OFFICE & PRINT (/office — 7 Left-Menu Tabs):
   - '📞 Call: Shop Drop-Off': Fast 45-second phone logger when caller is bringing tanks to the shop/backroom. Auto-fills repeat customers & addresses from Rolodex and gives a green wrap-up sentence to read & hang up.
   - '🚚 Dispatch: Truck Run': Dedicated fast-call logger for Weber's truck (Round-Trip Pickup & Return Delivery, Pickup Only, or Return Delivery Only) with Google Maps address & driver read-back script.
   - '💲 Create Invoice' & '📄 Saved Invoices': Click any Unbilled Customer Pill to auto-build invoice (auto-sets 0% PA tax-exempt & Net-30 for Fire/EMS, and prints Rolodex mailing address on PDF). If prices show $0.00, set prices once in '⚙️ Service Rates'. Mistake unpaid invoices can be unlocked via 'Void & Re-Bill' in Saved Invoices.
   - '📇 Customer Rolodex': Contact/mailing list, CSV export for mailers/newsletters, and 5-Yr Hydro / 1-Yr Visual Recall list.
   - '🖨️ DOT & Claim PDFs': Retest Sheet, Daily Verification, Visual Log, Active Valve Tags, 24/7 Door QR Sign, Zone E Claim Slip, and Avery 5160 B386 Stickers (30 Standard or 60 Mini 2-up HPA + 'Skip Used Slots').
   - '📦 Reorder Supplies': Flag items 'Low' or click 'Order Now'.

HOW TO HANDLE INPUT ERRORS VS. REAL SYSTEM BUGS:
- Check the LIVE SHOP SNAPSHOT provided below first! If Ryan's issue is caused by uncalibrated jacket, wrong selected date (not Today), active duplicate valve tag, unmarked B386 stamp gate, or $0.00 Service Rates, point out the exact live cause and how to fix it in 1-2 clicks.
- BE CANDID & HUMBLE ABOUT BUGS/UPDATES: If Ryan reports an actual software bug, layout glitch, something confusing/clunky, or a feature change needed from Eric, DO NOT make excuses or blame Ryan. Clearly validate it ("Good catch — that's a system bug / layout quirk that needs a code update from Eric"), set log_ticket=true, and summarize it cleanly in ticket_summary so it gets logged automatically to Eric's update queue!
"""


def call_gemini_help_assistant(api_key: str, prompt_parts: list[dict]) -> dict:
    candidates: list[str] = []
    if CACHED_VISION_MODEL:
        candidates.append(CACHED_VISION_MODEL)
    try:
        for d in discover_vision_models(api_key):
            if d not in candidates:
                candidates.append(d)
    except Exception:
        pass
    for fb in ("gemini-2.5-flash", "gemini-3-flash-preview", "gemini-2.0-flash"):
        if fb not in candidates:
            candidates.append(fb)

    schema = {
        "type": "OBJECT",
        "properties": {
            "reply": {"type": "STRING"},
            "log_ticket": {"type": "BOOLEAN"},
            "ticket_category": {"type": "STRING", "nullable": True},
            "ticket_priority": {"type": "STRING", "nullable": True},
            "ticket_summary": {"type": "STRING", "nullable": True},
        },
        "required": ["reply", "log_ticket"],
    }

    ctx = ssl.create_default_context()
    last_err = "No Gemini model available."
    for model_name in candidates[:4]:
        gen_cfg: dict = {
            "temperature": 0.25,
            "responseMimeType": "application/json",
            "responseSchema": schema,
        }
        body_bytes = json.dumps({
            "system_instruction": {"parts": [{"text": SHOP_HELP_SYSTEM_GUIDE}]},
            "contents": prompt_parts,
            "generationConfig": gen_cfg,
        }).encode("utf-8")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        req = urllib.request.Request(url, data=body_bytes, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=35, context=ctx) as resp:
                resp_json = json.loads(resp.read().decode("utf-8"))
                cand_parts = resp_json["candidates"][0]["content"]["parts"]
                raw_text = next(
                    (p["text"].strip() for p in reversed(cand_parts) if "text" in p and not p.get("thought")),
                    cand_parts[-1].get("text", "").strip(),
                )
                if raw_text.startswith("```"):
                    raw_text = re.sub(r"^```(?:json)?|```$", "", raw_text, flags=re.MULTILINE).strip()
                return json.loads(raw_text)
        except Exception as exc:
            last_err = f"{model_name}: {exc}"
            continue
    raise RuntimeError(last_err)


@app.post("/api/assistant/chat")
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



class PinUserInput(BaseModel):
    id: Optional[int] = None
    name: str
    initials: str
    pin: str
    role: str = "CREW"


class LockDayLineupInput(BaseModel):
    shift_mode: str = "FULL_HYDRO"
    bottle_capacity: int = 24
    allow_extra_autopace: bool = True
    day_notes: str = ""


class DayJobMoveInput(BaseModel):
    job_id: int
    action: str  # PULL_TODAY | BUMP_TOMORROW | TOGGLE_LOCK | SET_COUNT
    bottle_count: Optional[int] = None


class CrewTaskInput(BaseModel):
    assigned_to: str = "ALL"
    category: str = "SHOP"
    title: str
    priority: str = "NORMAL"
    work_date: str = ""


class CrewTaskUpdateInput(BaseModel):
    status: Optional[str] = None
    crew_note: Optional[str] = None


class EndOfDayInput(BaseModel):
    shift_note: str = ""
    roll_unfinished: bool = True


def require_owner_or_admin(request: Request) -> dict:
    u = getattr(request.state, "user", None) or get_current_session_user(request)
    if not u or u["role"] not in ("OWNER", "SYSTEM_ADMIN"):
        raise HTTPException(403, "Only Ryan (Owner) or Admin can perform this action.")
    return u


def run_smart_autopace(conn) -> dict:
    """
    Respects Ryan's locked lineup ('locked_for_day=1' and 'ROAD_*' truck stops never move off today).
    1) Rolls unlocked past-date (< today) open SHOP_BATCH jobs forward to the next open hydro window.
    2) If today's plan has allow_extra_autopace=1 and open capacity left, pulls unpinned future SHOP_BATCH
       work into today as bonus catch-up work so the crew stays ahead of schedule!
    """
    today_iso = today_eastern()
    today_d = date.fromisoformat(today_iso)
    s_map = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings;").fetchall()}
    default_cap = int(s_map.get("daily_hydro_capacity", "24") or 24)

    # Ensure day plans exist for next 14 days
    plans: dict[str, dict] = {}
    for offset in range(14):
        d = today_d + timedelta(days=offset)
        iso = d.isoformat()
        row = conn.execute("SELECT * FROM daily_schedule_plans WHERE work_date=?;", (iso,)).fetchone()
        if row:
            plans[iso] = dict(row)
        else:
            mode, cap = default_day_plan_for_date(d, default_cap)
            plans[iso] = {"work_date": iso, "shift_mode": mode, "bottle_capacity": cap, "lineup_locked": 0, "allow_extra_autopace": 1}

    rolled_forward = 0
    pulled_ahead = 0

    # Find first day >= today with hydro capacity > 0 (prefer tomorrow if today is locked with allow_extra=0)
    def next_open_hydro_date(start_offset: int = 0) -> str:
        for off in range(start_offset, 14):
            iso = (today_d + timedelta(days=off)).isoformat()
            p = plans.get(iso, {})
            if off == 0 and p.get("lineup_locked") and not p.get("allow_extra_autopace", 1):
                continue
            if int(p.get("bottle_capacity") or 0) > 0:
                return iso
        return (today_d + timedelta(days=1)).isoformat()

    # 1) Roll unlocked past-date SHOP_BATCH carryovers forward
    past_open = conn.execute(
        """SELECT id, bottle_count FROM schedule_jobs
           WHERE status != 'COMPLETED' AND scheduled_date < ?
             AND job_type='SHOP_BATCH' AND COALESCE(locked_for_day,0)=0;""",
        (today_iso,),
    ).fetchall()
    if past_open:
        target_iso = next_open_hydro_date(0)
        for j in past_open:
            conn.execute("UPDATE schedule_jobs SET scheduled_date=?, synced=0 WHERE id=?;", (target_iso, j["id"]))
            rolled_forward += 1

    # 2) Pull-ahead into Today if allowed & capacity remains
    tp = plans[today_iso]
    today_cap = int(tp.get("bottle_capacity") or 0)
    if today_cap > 0 and int(tp.get("allow_extra_autopace", 1)):
        done_today = conn.execute("SELECT COUNT(*) FROM cylinder_records WHERE work_date=? AND status='COMPLETED';", (today_iso,)).fetchone()[0]
        booked_today = conn.execute(
            "SELECT COALESCE(SUM(bottle_count),0) FROM schedule_jobs WHERE scheduled_date=? AND status!='COMPLETED' AND job_type IN ('SHOP_BATCH','ROAD_PICKUP','ROAD_ROUNDTRIP');",
            (today_iso,),
        ).fetchone()[0]
        open_slots = max(0, today_cap - max(done_today, booked_today))
        if open_slots > 0:
            future_batches = conn.execute(
                """SELECT id, bottle_count FROM schedule_jobs
                   WHERE status != 'COMPLETED' AND scheduled_date > ?
                     AND job_type='SHOP_BATCH' AND COALESCE(locked_for_day,0)=0
                   ORDER BY CASE WHEN rush_fee=1 OR priority='RUSH' THEN 0 ELSE 1 END,
                            CASE WHEN due_date!='' THEN due_date ELSE scheduled_date END ASC, id ASC;""",
                (today_iso,),
            ).fetchall()
            for fb in future_batches:
                bc = int(fb["bottle_count"] or 1)
                if bc <= open_slots:
                    conn.execute(
                        "UPDATE schedule_jobs SET scheduled_date=?, auto_paced=1, synced=0 WHERE id=?;",
                        (today_iso, fb["id"]),
                    )
                    open_slots -= bc
                    pulled_ahead += 1
                    if open_slots <= 0:
                        break

    return {"rolled_forward": rolled_forward, "pulled_ahead": pulled_ahead}


@app.get("/api/crew/pins")
def list_crew_pins(request: Request):
    u = require_owner_or_admin(request)
    conn = get_conn()
    if u["role"] == "SYSTEM_ADMIN":
        rows = [dict(r) for r in conn.execute("SELECT * FROM crew_users WHERE active=1 ORDER BY CASE role WHEN 'SYSTEM_ADMIN' THEN 0 WHEN 'OWNER' THEN 1 ELSE 2 END, id;").fetchall()]
    else:
        # Ryan sees Owner + all Crew PINs visibly (System Admin account stays separate)
        rows = [dict(r) for r in conn.execute("SELECT * FROM crew_users WHERE active=1 AND role != 'SYSTEM_ADMIN' ORDER BY CASE role WHEN 'OWNER' THEN 0 ELSE 1 END, id;").fetchall()]
    conn.close()
    return {"users": rows, "viewer_role": u["role"]}


@app.post("/api/crew/pins")
def save_crew_pin_user(payload: PinUserInput, request: Request):
    u = require_owner_or_admin(request)
    name = payload.name.strip()[:50]
    initials = re.sub(r"[^A-Z0-9]", "", payload.initials.strip().upper())[:4]
    pin = re.sub(r"\s+", "", payload.pin.strip())[:10]
    role = payload.role.strip().upper()
    if role not in ("SYSTEM_ADMIN", "OWNER", "CREW"):
        role = "CREW"
    if u["role"] != "SYSTEM_ADMIN" and role == "SYSTEM_ADMIN":
        raise HTTPException(403, "Only System Admin can create or edit System Admin accounts.")
    if len(name) < 2 or len(initials) < 2 or len(pin) < 4:
        raise HTTPException(400, "Enter Name, 2–4 character Initials, and at least a 4-digit PIN.")

    conn = get_conn()
    dup_pin = conn.execute(
        "SELECT name FROM crew_users WHERE pin=? AND active=1 AND id != COALESCE(?, -1);",
        (pin, payload.id),
    ).fetchone()
    if dup_pin:
        conn.close()
        raise HTTPException(400, f"PIN '{pin}' is already assigned to {dup_pin['name']}. Every person needs a unique PIN.")
    dup_init = conn.execute(
        "SELECT name FROM crew_users WHERE initials=? AND active=1 AND id != COALESCE(?, -1);",
        (initials, payload.id),
    ).fetchone()
    if dup_init:
        conn.close()
        raise HTTPException(400, f"Initials '{initials}' are already used by {dup_init['name']}.")

    can_sign = 1 if role == "OWNER" else 0
    if payload.id:
        target = conn.execute("SELECT * FROM crew_users WHERE id=?;", (payload.id,)).fetchone()
        if not target:
            conn.close()
            raise HTTPException(404, "User not found.")
        if target["role"] == "SYSTEM_ADMIN" and u["role"] != "SYSTEM_ADMIN":
            conn.close()
            raise HTTPException(403, "Cannot modify System Admin account.")
        conn.execute(
            "UPDATE crew_users SET name=?, initials=?, pin=?, role=?, can_sign_dot=? WHERE id=?;",
            (name, initials, pin, role, can_sign, payload.id),
        )
    else:
        conn.execute(
            "INSERT INTO crew_users (name, initials, pin, role, can_sign_dot, active, created_at) VALUES (?, ?, ?, ?, ?, 1, ?);",
            (name, initials, pin, role, can_sign, now_eastern().strftime("%Y-%m-%d %I:%M %p")),
        )

    if role == "OWNER":
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('crew_pin', ?);", (pin,))

    # Sync crew_roster setting string so top dropdowns stay current
    roster_inits = [r["initials"] for r in conn.execute("SELECT initials FROM crew_users WHERE active=1 AND role!='SYSTEM_ADMIN' ORDER BY CASE role WHEN 'OWNER' THEN 0 ELSE 1 END, id;").fetchall()]
    if roster_inits:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('crew_roster', ?);", (",".join(roster_inits),))

    bump_db_version(conn)
    conn.commit()
    conn.close()
    return {"saved": True}


@app.delete("/api/crew/pins/{uid}")
def delete_crew_pin_user(uid: int, request: Request):
    u = require_owner_or_admin(request)
    conn = get_conn()
    target = conn.execute("SELECT * FROM crew_users WHERE id=?;", (uid,)).fetchone()
    if not target:
        conn.close()
        raise HTTPException(404, "User not found.")
    if target["role"] in ("SYSTEM_ADMIN", "OWNER"):
        conn.close()
        raise HTTPException(400, "Owner and System Admin accounts cannot be deleted (edit their PIN instead).")
    conn.execute("DELETE FROM crew_users WHERE id=?;", (uid,))
    bump_db_version(conn)
    conn.commit()
    conn.close()
    return {"deleted": uid}


@app.get("/api/day/overview")
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

    p_row = conn.execute("SELECT * FROM daily_schedule_plans WHERE work_date=?;", (today_iso,)).fetchone()
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


@app.post("/api/day/lock-lineup")
def lock_todays_lineup(payload: LockDayLineupInput, request: Request):
    u = require_owner_or_admin(request)
    today_iso = today_eastern()
    cap = max(0, min(200, int(payload.bottle_capacity)))
    allow_extra = 1 if payload.allow_extra_autopace else 0
    ts = now_eastern().strftime("%Y-%m-%d %I:%M %p")

    conn = get_conn()
    conn.execute(
        """INSERT INTO daily_schedule_plans
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


@app.post("/api/day/job-move")
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


@app.post("/api/day/autopace")
def trigger_manual_autopace(request: Request):
    require_owner_or_admin(request)
    conn = get_conn()
    res = run_smart_autopace(conn)
    bump_db_version(conn)
    conn.commit()
    conn.close()
    return res


@app.post("/api/day/tasks")
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


@app.put("/api/day/tasks/{task_id}")
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


@app.delete("/api/day/tasks/{task_id}")
def delete_crew_task(task_id: int, request: Request):
    require_owner_or_admin(request)
    conn = get_conn()
    conn.execute("DELETE FROM crew_tasks WHERE id=?;", (task_id,))
    bump_db_version(conn)
    conn.commit()
    conn.close()
    return {"deleted": task_id}


@app.post("/api/day/end-day")
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
        existing = conn.execute("SELECT shift_notes FROM daily_schedule_plans WHERE work_date=?;", (today_iso,)).fetchone()
        prev_n = (existing["shift_notes"] if existing and existing["shift_notes"] else "").strip()
        merged = (f"{prev_n} | {u['initials']}: {note_txt}" if prev_n else f"{u['initials']}: {note_txt}")[:400]
        conn.execute(
            """INSERT INTO daily_schedule_plans (work_date, shift_mode, bottle_capacity, shift_notes, updated_at)
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
        conn.execute("UPDATE daily_schedule_plans SET closed_out_at=? WHERE work_date=?;", (ts, today_iso))

    bump_db_version(conn)
    conn.commit()
    conn.close()
    trigger_sheets_sync_bg()
    return {"closed": True, "rolled_jobs": rolled}


# --- CREW FEEDBACK & PDF ENDPOINTS ---
@app.get("/api/feedback")
def list_crew_feedback():
    conn = get_conn()
    rows = [dict(r) for r in conn.execute("SELECT * FROM crew_feedback ORDER BY id DESC LIMIT 50;").fetchall()]
    conn.close()
    return rows


@app.post("/api/feedback")
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


@app.post("/api/feedback/{fb_id}/status")
def update_feedback_status(fb_id: int, payload: FeedbackStatusInput):
    conn = get_conn()
    conn.execute("UPDATE crew_feedback SET status=?, synced=0 WHERE id=?;", (payload.status.strip().upper(), fb_id))
    bump_db_version(conn)
    conn.commit()
    conn.close()
    trigger_sheets_sync_bg()
    return {"updated": fb_id}


@app.get("/api/pdf/retest")
def download_retest_pdf(work_date: str, customer: str = "", sheet_no: Optional[int] = None):
    conn = get_conn()
    s_map = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings;").fetchall()}
    sql = "SELECT * FROM cylinder_records WHERE work_date=? AND status='COMPLETED'" + (" AND customer_name=?" if customer else "") + " ORDER BY id ASC;"
    rows = conn.execute(sql, (work_date, customer) if customer else (work_date,)).fetchall()
    conn.close()
    pdf_bytes = build_retest_datasheet_pdf(
        work_date=work_date,
        records=[dict(r) for r in rows],
        dot_rin=s_map.get("dot_rin", "B386"),
        tc_reg=s_map.get("tc_reg", ""),
        base_sheet_no=sheet_no or int(s_map.get("current_sheet_no") or s_map.get("starting_sheet_no") or 8095),
    )
    return Response(content=pdf_bytes, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="DOT_Retest_Sheet_{work_date}.pdf"'})


@app.get("/api/pdf/verification")
def download_verification_pdf(work_date: str):
    conn = get_conn()
    s_map = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings;").fetchall()}
    rows = conn.execute("SELECT * FROM daily_verifications WHERE test_date=? ORDER BY id ASC;", (work_date,)).fetchall()
    conn.close()
    pdf_bytes = build_verification_pdf(work_date=work_date, verifications=[dict(r) for r in rows], cal_serial=s_map.get("calibrated_serial", "1SF11904"))
    return Response(content=pdf_bytes, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="DOT_Verification_{work_date}.pdf"'})


@app.get("/api/pdf/visual")
def download_visual_pdf(work_date: str, customer: str = ""):
    conn = get_conn()
    s_map = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings;").fetchall()}
    sql = "SELECT * FROM cylinder_records WHERE work_date=? AND status='COMPLETED'" + (" AND customer_name=?" if customer else "") + " ORDER BY id ASC;"
    rows = conn.execute(sql, (work_date, customer) if customer else (work_date,)).fetchall()
    conn.close()
    pdf_bytes = build_visual_log_pdf(work_date=work_date, records=[dict(r) for r in rows], dot_rin=s_map.get("dot_rin", "B386"))
    return Response(content=pdf_bytes, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="DOT_Visual_Log_{work_date}.pdf"'})


@app.get("/api/pdf/stickers")
def download_stickers_pdf(work_date: str, count: int = 30, skip: int = 0, compact: bool = False):
    conn = get_conn()
    rin = conn.execute("SELECT value FROM settings WHERE key='dot_rin';").fetchone()["value"]
    conn.close()
    pdf_bytes = build_rin_stickers_pdf(work_date=work_date, count=count, dot_rin=rin, skip_slots=skip, compact=compact)
    suffix = "Mini_HPA" if compact else "Standard"
    return Response(content=pdf_bytes, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="B386_Stickers_{suffix}_{work_date}.pdf"'})


@app.get("/api/pdf/valvetags")
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


@app.get("/api/pdf/poster")
def download_backroom_poster_pdf(checkin_url: str = "https://weberstanktesting.com/#dropoff"):
    conn = get_conn()
    s_map = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings;").fetchall()}
    conn.close()
    pdf_bytes = build_backroom_poster_pdf(checkin_url=checkin_url, phone=s_map.get("phone", "724-266-8008"), dot_rin=s_map.get("dot_rin", "B386"))
    return Response(content=pdf_bytes, media_type="application/pdf", headers={"Content-Disposition": 'inline; filename="Webers_Backroom_DropOff_QR_Sign.pdf"'})


@app.get("/api/pdf/claim-slip")
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
    return Response(content=pdf_bytes, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="ZoneE_Claim_Slip_{safe_c}.pdf"'})


@app.get("/api/pdf/invoice/{inv_id}")
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


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")