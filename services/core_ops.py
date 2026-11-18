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


ROOT_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT_DIR / "static"


STATIC_DIR.mkdir(exist_ok=True)


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
    "BACKROOM_ONLY": {"label": "🚪 24/7 Loading Dock Self-Drop Only", "default_cap": 0},
}


HOLDING_ZONES = {
    "ZONE_A_BACKROOM": "Loading Dock • Drop-Off",
    "ZONE_B_DEVALVE": "Stamp & Valve Bench",
    "ZONE_C_READY_RACK": "Test Room • Ready Rack",
    "ZONE_D_DRY_STAMP": "Main Bay • Drying Rack",
    "ZONE_E_PICKUP_SHELF": "Loading Dock • Pickup",
    "OUTBOUND_TRUCK": "🚚 Staged for Truck Delivery",
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
    "PROPANE": "LPG",
    "LPG": "LPG",
    "LP GAS": "LPG",
    "UN1075": "LPG",
    "UN1978": "LPG",
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


MAX_RUSH_BOTTLES = 12


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


SHOP_LAT = 40.6429


SHOP_LON = -80.2255


SHOP_ORIGIN_ADDR = "3086 Legionville Rd, Baden, PA 15005"


LOCAL_PA_COORDS = {
    "BADEN": (40.6353, -80.2317), "AMBRIDGE": (40.5870, -80.2259), "ECONOMY": (40.6501, -80.1931),
    "CONWAY": (40.6606, -80.2406), "FREEDOM": (40.6845, -80.2548), "ROCHESTER": (40.7023, -80.2867),
    "MONACA": (40.6867, -80.2764), "BEAVER FALLS": (40.7584, -80.3192), "CHIPPEWA": (40.7795, -80.3542),
    "BEAVER": (40.6953, -80.3048), "ALIQUIPPA": (40.6151, -80.2542), "HOPEWELL": (40.5698, -80.2584),
    "CENTER": (40.6531, -80.3031), "NEW BRIGHTON": (40.7306, -80.3106), "CRANBERRY": (40.6848, -80.1070),
    "MARS": (40.6959, -80.0117), "ZELIENOPLE": (40.7945, -80.1398), "HARMONY": (40.8031, -80.1289),
    "SEWICKLEY": (40.5365, -80.1845), "CORAOPOLIS": (40.5184, -80.1667), "MOON": (40.5170, -80.2070),
    "ROBINSON": (40.4534, -80.1328), "WEXFORD": (40.6256, -80.0562), "MCCANDLESS": (40.5798, -80.0259),
    "ROSS": (40.5256, -80.0089), "PITTSBURGH": (40.4406, -79.9959), "PITT": (40.4444, -79.9608), "EAST LIBERTY": (40.4615, -79.9255), "E LIBERTY": (40.4615, -79.9255), "OAKLAND": (40.4417, -79.9569), "SHADYSIDE": (40.4551, -79.9329), "LAWRENCEVILLE": (40.4681, -79.9605), "NORTH HILLS": (40.5428, -80.0160), "WASHINGTON": (40.1740, -80.2462), "BUTLER": (40.8612, -79.8953),
    "ELLWOOD CITY": (40.8584, -80.2887), "MIDLAND": (40.6326, -80.4465), "NEW CASTLE": (40.9998, -80.3470),
    "IMPERIAL": (40.4517, -80.2451), "OAKDALE": (40.3987, -80.1859), "CARNEGIE": (40.4087, -80.0834),
}


def build_shop_maps_url(dest_addr: str) -> str:
    clean = (dest_addr or "").strip()
    if not clean:
        return ""
    return (
        "https://www.google.com/maps/dir/?api=1"
        f"&origin={urllib.parse.quote(SHOP_ORIGIN_ADDR)}"
        f"&destination={urllib.parse.quote(clean)}&travelmode=driving"
    )


def estimate_route_from_shop(raw_addr: str) -> tuple[float, int]:
    """Calculates driving miles & ETA minutes from 3086 Legionville Rd, Baden, PA to customer address."""
    import math as _math
    clean = (raw_addr or "").strip()
    if len(clean) < 4:
        return (0.0, 0)

    # 1. Try live Nominatim + OSRM road routing (free, accurate, 3s cap)
    try:
        q = clean if ("PA" in clean.upper() or "OH" in clean.upper() or "WV" in clean.upper()) else f"{clean}, PA"
        g_url = f"https://nominatim.openstreetmap.org/search?format=json&limit=1&q={urllib.parse.quote(q)}"
        req = urllib.request.Request(g_url, headers={"User-Agent": "WebersTankTestingOps/13.0"})
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=2.8, context=ctx) as r:
            hits = json.loads(r.read().decode("utf-8"))
        if hits:
            d_lat, d_lon = float(hits[0]["lat"]), float(hits[0]["lon"])
            osrm_url = f"https://router.project-osrm.org/route/v1/driving/{SHOP_LON},{SHOP_LAT};{d_lon},{d_lat}?overview=false"
            r_req = urllib.request.Request(osrm_url, headers={"User-Agent": "WebersTankTestingOps/13.0"})
            with urllib.request.urlopen(r_req, timeout=2.8, context=ctx) as rr:
                rdata = json.loads(rr.read().decode("utf-8"))
            if rdata.get("routes"):
                meters = float(rdata["routes"][0]["distance"])
                secs = float(rdata["routes"][0]["duration"])
                miles = round(max(0.8, meters / 1609.344), 1)
                mins = max(4, int(round((secs / 60.0) + 3)))
                return (miles, mins)
    except Exception:
        pass

    # 2. Instant Western PA Haversine + 1.28x road-winding fallback
    up = clean.upper()
    for town, (t_lat, t_lon) in sorted(LOCAL_PA_COORDS.items(), key=lambda kv: -len(kv[0])):
        if town in up:
            dlat = _math.radians(t_lat - SHOP_LAT)
            dlon = _math.radians(t_lon - SHOP_LON)
            a = _math.sin(dlat / 2) ** 2 + _math.cos(_math.radians(SHOP_LAT)) * _math.cos(_math.radians(t_lat)) * _math.sin(dlon / 2) ** 2
            straight_mi = 3958.8 * 2 * _math.atan2(_math.sqrt(a), _math.sqrt(1 - a))
            road_mi = round(max(1.5, straight_mi * 1.28), 1)
            eta_m = max(5, int(round((road_mi / 34.0) * 60 + 3)))
            return (road_mi, eta_m)
    return (0.0, 0)


def _fmt_12h(hhmm: str) -> str:
    m_t = re.match(r"^(\d{1,2}):(\d{2})$", (hhmm or "").strip())
    if not m_t:
        return hhmm or ""
    h, m_val = int(m_t.group(1)), int(m_t.group(2))
    ampm = "PM" if h >= 12 else "AM"
    h12 = h % 12 or 12
    return f"{h12}:{m_val:02d} {ampm}" if m_val else f"{h12} {ampm}"


def get_operating_schedule_bundle(conn) -> dict:
    today_iso = today_eastern()
    hours = [dict(r) for r in conn.execute("SELECT * FROM operating_hours ORDER BY day_of_week ASC;").fetchall()]
    closures = [dict(r) for r in conn.execute("SELECT * FROM holiday_closures WHERE end_date >= ? ORDER BY start_date ASC;", (today_iso,)).fetchall()]
    s_map = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings WHERE key IN ('gbp_edit_url', 'master_sheet_url');").fetchall()}

    # Build concise summary line for website top bar & footer
    open_days = [h for h in hours if h["is_open"]]
    grouped = []
    for h in open_days:
        rng = f"{_fmt_12h(h['open_time'])}–{_fmt_12h(h['close_time'])}"
        sh = h["day_name"][:3]
        if grouped and grouped[-1][1] == rng:
            grouped[-1][0].append(sh)
        else:
            grouped.append(([sh], rng))
    parts = []
    for d_list, rng in grouped:
        lbl = f"{d_list[0]}–{d_list[-1]}" if len(d_list) >= 3 else " & ".join(d_list)
        parts.append(f"{lbl} {rng}")
    summary_line = (" | ".join(parts) if parts else "By Appointment") + " | 24/7 Dock"

    # Check if a holiday/vacation closure is active right now or upcoming within 10 days
    active_closure = None
    for cl in closures:
        rem = days_until(cl["start_date"], today_iso)
        if cl["start_date"] <= today_iso <= cl["end_date"]:
            active_closure = {**cl, "banner_state": "ACTIVE"}
            break
        if rem is not None and 0 <= rem <= 10 and not active_closure:
            active_closure = {**cl, "banner_state": "UPCOMING", "days_until": rem}

    # Pre-formatted Google Business Profile hours copy block
    gbp_lines = ["WEBER'S TANK TESTING — GOOGLE BUSINESS PROFILE HOURS:"]
    for h in hours:
        st = f"{_fmt_12h(h['open_time'])} – {_fmt_12h(h['close_time'])}" if h["is_open"] else "Closed (24/7 Loading Dock Open)"
        gbp_lines.append(f"{h['day_name']}: {st}")
    if closures:
        gbp_lines.append("\nSPECIAL HOLIDAY / VACATION HOURS:")
        for cl in closures:
            span = cl["start_date"] if cl["start_date"] == cl["end_date"] else f"{cl['start_date']} to {cl['end_date']}"
            hrs = cl["special_hours"] if (cl["staffed_open"] and cl["special_hours"]) else "Closed"
            dock_n = " (24/7 Loading Dock Open)" if cl["dock_open"] else " (Facility & Dock Closed)"
            gbp_lines.append(f"- {span} ({cl['title']}): {hrs}{dock_n}")

    return {
        "weekly_hours": hours,
        "upcoming_closures": closures,
        "summary_line": summary_line,
        "active_banner": active_closure,
        "gbp_copy_text": "\n".join(gbp_lines),
        "gbp_edit_url": s_map.get("gbp_edit_url") or "https://business.google.com/",
        "master_sheet_url": s_map.get("master_sheet_url") or "",
    }


def _fast_local_eta(raw_addr: str) -> tuple[float, int]:
    import math as _math
    up = (raw_addr or "").strip().upper()
    if len(up) < 3:
        return (0.0, 0)
    for town, (t_lat, t_lon) in sorted(LOCAL_PA_COORDS.items(), key=lambda kv: -len(kv[0])):
        if town in up:
            dlat = _math.radians(t_lat - SHOP_LAT)
            dlon = _math.radians(t_lon - SHOP_LON)
            a = _math.sin(dlat / 2) ** 2 + _math.cos(_math.radians(SHOP_LAT)) * _math.cos(_math.radians(t_lat)) * _math.sin(dlon / 2) ** 2
            straight_mi = 3958.8 * 2 * _math.atan2(_math.sqrt(a), _math.sqrt(1 - a))
            road_mi = round(max(1.5, straight_mi * 1.28), 1)
            eta_m = max(5, int(round((road_mi / 34.0) * 60 + 3)))
            return (road_mi, eta_m)
    return (0.0, 0)


def _compute_leave_by_time(raw_time: str, drive_mins: int) -> str:
    if not raw_time or drive_mins <= 0:
        return ""
    s = raw_time.strip()
    mt = re.search(r"\b(\d{1,2}):(\d{2})\s*(am|pm)?\b", s, flags=re.I)
    if mt:
        h, mn, ap = int(mt.group(1)), int(mt.group(2)), (mt.group(3) or "").upper()
    else:
        mt2 = re.search(r"\b(\d{1,2})\s*(am|pm)\b", s, flags=re.I)
        if not mt2:
            return ""
        h, mn, ap = int(mt2.group(1)), 0, mt2.group(2).upper()
    if ap == "PM" and h < 12:
        h += 12
    elif ap == "AM" and h == 12:
        h = 0
    total_m = (h * 60 + mn - int(drive_mins)) % (24 * 60)
    lh, lm = total_m // 60, total_m % 60
    lap = "PM" if lh >= 12 else "AM"
    lh12 = lh % 12 or 12
    return f"{lh12}:{lm:02d} {lap}"


def enrich_schedule_job_view(j: dict, leg: str = "PRIMARY") -> dict:
    out = dict(j)
    addr = (out.get("return_address") if leg == "RETURN" and out.get("return_address") else out.get("location_notes")) or ""
    out["maps_route_url"] = build_shop_maps_url(addr)
    jt = (out.get("job_type") or "SHOP_BATCH").upper()
    if jt != "SHOP_BATCH" and addr and not int(out.get("drive_eta_mins") or 0):
        f_mi, f_eta = _fast_local_eta(addr)
        if f_eta > 0:
            out["drive_miles"] = f_mi
            out["drive_eta_mins"] = f_eta
    if leg == "RETURN":
        out["cal_leg"] = "RETURN"
        out["cal_leg_label"] = "📤 Return Delivery"
        out["cal_time"] = out.get("dropoff_time") or ""
        out["cal_done"] = out.get("status") == "COMPLETED"
    elif jt == "ROAD_ROUNDTRIP":
        out["cal_leg"] = "PICKUP"
        out["cal_leg_label"] = "📥 Leg 1 Pickup"
        out["cal_time"] = out.get("pickup_time") or ""
        out["cal_done"] = bool(out.get("pickup_leg_done")) or out.get("status") == "COMPLETED"
    elif jt == "ROAD_PICKUP":
        out["cal_leg"] = "PICKUP"
        out["cal_leg_label"] = "🚚 Truck Pickup"
        out["cal_time"] = out.get("pickup_time") or ""
        out["cal_done"] = out.get("status") == "COMPLETED"
    elif jt == "ROAD_DELIVERY":
        out["cal_leg"] = "RETURN"
        out["cal_leg_label"] = "📤 Truck Delivery"
        out["cal_time"] = out.get("dropoff_time") or out.get("pickup_time") or ""
        out["cal_done"] = out.get("status") == "COMPLETED"
    else:
        out["cal_leg"] = "SHOP"
        out["cal_leg_label"] = "📌 Shop Drop-Off"
        out["cal_time"] = out.get("pickup_time") or ""
        out["cal_done"] = out.get("status") == "COMPLETED"
    out["leave_by_time"] = _compute_leave_by_time(out.get("cal_time") or "", int(out.get("drive_eta_mins") or 0))
    return out


def resolve_day_plan(d: date, plans_map: dict, default_cap: int = 24, hours_map: Optional[dict] = None, closures: Optional[list] = None) -> dict:
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
            "is_closure": False,
        }

    # Honor Holiday / Vacation closures automatically!
    if closures:
        for cl in closures:
            if cl["start_date"] <= iso <= cl["end_date"]:
                if not cl.get("staffed_open"):
                    return {
                        "work_date": iso,
                        "weekday": d.strftime("%a"),
                        "month_day": d.strftime("%b %d"),
                        "shift_mode": "BACKROOM_ONLY",
                        "shift_label": f"🏖️ Closed: {cl['title']}" + (" (24/7 Dock)" if cl.get("dock_open", 1) else ""),
                        "bottle_capacity": 0,
                        "day_notes": cl["title"],
                        "is_custom": False,
                        "is_closure": True,
                    }

    # Honor Ryan's Weekly Operating Schedule automatically!
    if hours_map and d.weekday() in hours_map:
        wh = hours_map[d.weekday()]
        if not wh["is_open"]:
            return {
                "work_date": iso,
                "weekday": d.strftime("%a"),
                "month_day": d.strftime("%b %d"),
                "shift_mode": "BACKROOM_ONLY",
                "shift_label": SHIFT_MODES["BACKROOM_ONLY"]["label"],
                "bottle_capacity": 0,
                "day_notes": wh.get("note") or "",
                "is_custom": False,
                "is_closure": False,
            }
        return {
            "work_date": iso,
            "weekday": d.strftime("%a"),
            "month_day": d.strftime("%b %d"),
            "shift_mode": "FULL_HYDRO",
            "shift_label": f"🟢 Open {_fmt_12h(wh['open_time'])}–{_fmt_12h(wh['close_time'])}",
            "bottle_capacity": default_cap,
            "day_notes": wh.get("note") or "",
            "is_custom": False,
            "is_closure": False,
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
        "is_closure": False,
    }


def build_month_calendar_grid(conn, year: int, month: int, s_map: dict) -> dict:
    """Builds a full Sunday-Saturday month grid with Dual Round-Trip Legs (Pickup + Return), Times & Route ETAs."""
    default_cap = int(s_map.get("daily_hydro_capacity") or 24)
    est_today = today_eastern()
    cal = calendar.Calendar(firstweekday=6)
    weeks = cal.monthdatescalendar(year, month)
    flat_dates = [d for wk in weeks for d in wk]
    start_iso = flat_dates[0].isoformat()
    end_iso = flat_dates[-1].isoformat()

    plans_rows = conn.execute(
        "SELECT * FROM shop_day_plans WHERE work_date >= ? AND work_date <= ?;",
        (start_iso, end_iso),
    ).fetchall()
    plans_map = {r["work_date"]: dict(r) for r in plans_rows}
    hours_map = {r["day_of_week"]: dict(r) for r in conn.execute("SELECT * FROM operating_hours;").fetchall()}
    closures = [dict(r) for r in conn.execute("SELECT * FROM holiday_closures WHERE end_date >= ? AND start_date <= ?;", (start_iso, end_iso)).fetchall()]

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
        p = resolve_day_plan(d, plans_map, default_cap, hours_map, closures)
        iso = p["work_date"]

        # Place Leg 1 (scheduled_date) AND Round-Trip Leg 2 Return (due_date) directly onto the calendar!
        day_jobs = []
        for j in jobs_rows:
            if j["status"] == "COMPLETED":
                continue
            if j["scheduled_date"] == iso:
                day_jobs.append(enrich_schedule_job_view(j, "PRIMARY"))
            elif j["job_type"] == "ROAD_ROUNDTRIP" and j.get("due_date") == iso and j["due_date"] != j["scheduled_date"]:
                day_jobs.append(enrich_schedule_job_view(j, "RETURN"))

        done_jobs = [j for j in jobs_rows if j["scheduled_date"] == iso and j["status"] == "COMPLETED"]
        due_jobs = [
            enrich_schedule_job_view(j, "RETURN")
            for j in jobs_rows
            if j.get("due_date") == iso and j["scheduled_date"] != iso and j["job_type"] != "ROAD_ROUNDTRIP" and j["status"] != "COMPLETED"
        ]
        booked = sum(int(j["bottle_count"] or 0) for j in day_jobs if j.get("cal_leg") != "RETURN" and j["job_type"] in ("SHOP_BATCH", "ROAD_PICKUP", "ROAD_ROUNDTRIP"))
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
                  r.dot_spec, r.service_psi, r.service_mode, r.star_10yr, r.work_date, r.notes,
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
        elif r["star_10yr"] or "10-YR" in notes_up or "STAR" in notes_up or "★" in (r["dot_spec"] or ""):
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
    hours_map_q = {r["day_of_week"]: dict(r) for r in conn.execute("SELECT * FROM operating_hours;").fetchall()}
    closures_q = [dict(r) for r in conn.execute("SELECT * FROM holiday_closures WHERE end_date >= ?;", (start_dt.isoformat(),)).fetchall()]

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
        plan = resolve_day_plan(d, plans_map, default_cap, hours_map_q, closures_q)
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
        "rush_max_bottles": MAX_RUSH_BOTTLES,
        "rush_eligible": req <= MAX_RUSH_BOTTLES,
        "standard_promise_date": std_ready.isoformat(),
        "standard_promise_label": std_ready.strftime("%a, %b %d"),
        "next_hydro_windows": upcoming_hydro_days[:5],
    }


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
    punches = [
        dict(r)
        for r in conn.execute("SELECT * FROM time_punches WHERE synced=0 AND status='CLOSED' ORDER BY id ASC LIMIT 50;").fetchall()
    ]
    rates = [dict(r) for r in conn.execute("SELECT * FROM pricing_rates ORDER BY sort_order ASC, code ASC;").fetchall()]
    custs = [dict(r) for r in conn.execute("SELECT * FROM customers ORDER BY customer_name COLLATE NOCASE ASC;").fetchall()]
    conn.close()

    if not webhook_url and not fb_webhook_url:
        return {"synced": False, "reason": "No Google Sheets Webhook URL set yet."}

    pushed_total = 0
    ctx = ssl.create_default_context()

    if webhook_url and (cals or recs or invs or drops or punches):
        sheets_recs = []
        for r in recs:
            rc = dict(r)
            if rc.get("percent_permanent") is not None:
                rc["percent_permanent"] = round(float(rc["percent_permanent"]), 1)
            if "ADMIN" not in str(rc.get("operator_initials") or "") and "ADMIN" not in str(rc.get("tested_by") or ""):
                rc["operator_initials"] = "RP"
            if rc.get("addon_burst_disc"):
                rc["relief_valve_changed"] = "Yes"
            rc["notes"] = _format_dot_row_notes(rc)
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
                "customers": custs,
                "time_punches": punches,
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
        if punches:
            conn.executemany("UPDATE time_punches SET synced=1 WHERE id=?;", [(p["id"],) for p in punches])
        if fbs and fb_webhook_url == webhook_url:
            conn.executemany("UPDATE crew_feedback SET synced=1 WHERE id=?;", [(f["id"],) for f in fbs])
            pushed_total += len(fbs)
            fbs = []
        bump_db_version(conn)
        conn.commit()
        conn.close()
        pushed_total += len(cals) + len(recs) + len(invs) + len(drops) + len(punches)

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


def queue_customer_sms_outbox(conn, customer_name: str, phone: str, message: str, kind: str = "INFO", ref_id: str = "") -> None:
    ph = clean_phone(phone)
    digits = re.sub(r"\D", "", ph)
    if len(digits) < 10 or not message.strip():
        return
    ts = now_eastern().strftime("%Y-%m-%d %I:%M %p")
    conn.execute(
        """INSERT INTO sms_outbox (created_at, customer_name, phone, message, kind, ref_id, status)
           VALUES (?, ?, ?, ?, ?, ?, 'PENDING');""",
        (ts, customer_name[:80], ph, message.strip()[:320], kind, str(ref_id)),
    )


def send_raw_email_resend_or_smtp(to_email: str, subject: str, body_txt: str, reply_to: str = "") -> dict:
    """Sends email via Resend HTTPS API ($0/mo, port 443) first, falling back to Gmail SMTP."""
    import smtplib
    from email.message import EmailMessage

    conn = get_conn()
    s_map = {
        r["key"]: r["value"]
        for r in conn.execute(
            "SELECT key, value FROM settings WHERE key IN ('resend_api_key', 'resend_from_email', 'alert_email', 'smtp_sender_email', 'smtp_app_password');"
        ).fetchall()
    }
    conn.close()

    resend_key = (os.getenv("RESEND_API_KEY") or s_map.get("resend_api_key") or "").strip()
    from_addr = (os.getenv("RESEND_FROM_EMAIL") or s_map.get("resend_from_email") or "alerts@weberstanktesting.com").strip()
    eff_reply = reply_to or (s_map.get("alert_email") or "ryan@weberstanktesting.com").strip()
    out = {"sent_resend": False, "sent_smtp": False, "detail": ""}

    if resend_key and to_email and "@" in to_email:
        try:
            payload = {
                "from": f"Weber's Tank Testing <{from_addr}>",
                "to": [e.strip() for e in to_email.split(",") if "@" in e],
                "subject": subject,
                "text": body_txt,
            }
            if eff_reply and "@" in eff_reply:
                payload["reply_to"] = eff_reply
            req = urllib.request.Request(
                "https://api.resend.com/emails",
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {resend_key}",
                    "Content-Type": "application/json",
                    "User-Agent": "WebersTankTestingOps/13.0",
                },
                method="POST",
            )
            ctx = ssl.create_default_context()
            with urllib.request.urlopen(req, timeout=14, context=ctx) as resp:
                _ = resp.read()
            out["sent_resend"] = True
            return out
        except urllib.error.HTTPError as he:
            err_b = he.read().decode("utf-8", errors="ignore")
            out["detail"] = f"Resend HTTP {he.code}: {err_b[:180]}"
        except Exception as exc:
            out["detail"] = f"Resend error: {exc}"

    smtp_user = (os.getenv("WEBER_SMTP_USER") or s_map.get("smtp_sender_email") or "").strip()
    smtp_pass = (os.getenv("WEBER_SMTP_PASS") or s_map.get("smtp_app_password") or "").replace(" ", "").strip()
    if smtp_user and smtp_pass and to_email and "@" in to_email:
        try:
            msg = EmailMessage()
            msg["Subject"] = subject
            msg["From"] = f"Weber's Tank Testing <{smtp_user}>"
            msg["To"] = to_email
            if eff_reply and "@" in eff_reply:
                msg["Reply-To"] = eff_reply
            msg.set_content(body_txt)
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=14) as s:
                s.login(smtp_user, smtp_pass)
                s.send_message(msg)
            out["sent_smtp"] = True
        except Exception as exc:
            if not out["detail"]:
                out["detail"] = f"SMTP error: {exc}"
    return out


def send_ticket_email_via_webhook(ticket_info: dict, force_test: bool = False) -> dict:
    """Sends shop email notification to Ryan (via Gmail SMTP and/or Google Apps Script webhook) + customer receipt."""
    import smtplib
    from email.message import EmailMessage

    conn = get_conn()
    s_map = {
        r["key"]: r["value"]
        for r in conn.execute(
            "SELECT key, value FROM settings WHERE key IN ('sheets_webhook_url', 'alert_email', 'smtp_sender_email', 'smtp_app_password');"
        ).fetchall()
    }
    conn.close()

    webhook_url = (s_map.get("sheets_webhook_url") or "").strip()
    alert_to = (os.getenv("WEBER_ALERT_EMAIL") or s_map.get("alert_email") or "").strip()
    smtp_user = (os.getenv("WEBER_SMTP_USER") or s_map.get("smtp_sender_email") or "").strip()
    smtp_pass = (os.getenv("WEBER_SMTP_PASS") or s_map.get("smtp_app_password") or "").replace(" ", "").strip()

    etype = (ticket_info.get("entry_type") or "DROPOFF").upper()
    t_no = ticket_info.get("ticket_no") or "BR-TEST"
    cust = ticket_info.get("customer_name") or "Test Customer"
    cnt = ticket_info.get("bottle_count") or 4
    ph = ticket_info.get("customer_phone") or "724-266-8008"
    rush_flag = "🔥 RUSH " if ticket_info.get("rush_requested") else ""
    svc = ticket_info.get("service_requested") or "Hydro + Visual"
    summary = ticket_info.get("tank_summary") or ""
    addr = ticket_info.get("pickup_address") or ""

    if force_test:
        subj = "[WEBER'S ALERT TEST] Loading Dock & Truck Email Alerts Active"
        body_txt = (
            "Weber's Tank Testing — Shop Email Alerts Connected!\n\n"
            "Whenever a customer submits a 24/7 Loading Dock Drop-Off, Loading Dock Pick-Up, or Truck Request "
            "on weberstanktesting.com, an instant summary email will arrive at this address.\n\n"
            "Tip: In Gmail/Apple Mail on your phone, you can set a custom notification chime or VIP filter "
            "for '[WEBER'S' if you want audible alerts during shop hours.\n\n"
            "Open Shop Ops: https://ops.weberstanktesting.com/ops"
        )
    else:
        kind_lbl = "TRUCK REQUEST" if etype == "TRUCK_REQUEST" else ("DOCK PICK-UP" if etype == "PICKUP_NOTICE" else "DOCK DROP-OFF")
        subj = f"[WEBER'S {kind_lbl}] {rush_flag}{t_no} — {cust} ({cnt} Cyl)"
        lines = [
            f"New {kind_lbl} logged on weberstanktesting.com:",
            f"--------------------------------------------------",
            f"Ticket #:        {t_no}" + ("  [PAID RUSH]" if ticket_info.get("rush_requested") else ""),
            f"Customer / Dept: {cust}",
        ]
        if ticket_info.get("contact_person"):
            lines.append(f"Contact / By:    {ticket_info['contact_person']}")
        lines.append(f"Phone:           {ph}")
        if ticket_info.get("customer_email"):
            lines.append(f"Customer Email:  {ticket_info['customer_email']}")
        if addr:
            lines.append(f"Address:         {addr}")
        if ticket_info.get("preferred_date"):
            lines.append(f"Preferred Date:  {ticket_info['preferred_date']}")
        lines.append(f"Cylinders:       {cnt} cylinder(s) — {svc}")
        if summary:
            lines.append(f"Tank Notes:      {summary}")
        if ticket_info.get("estimated_ready"):
            lines.append(f"Est. Ready:      {ticket_info['estimated_ready']}")
        lines.append("")
        lines.append("Open Shop Ops:   https://ops.weberstanktesting.com/ops")
        body_txt = "\n".join(lines)

    res_info = {"sent_smtp": False, "sent_webhook": False, "detail": ""}

    # Channel 0: Resend HTTPS API (alerts@weberstanktesting.com)
    if alert_to:
        r0 = send_raw_email_resend_or_smtp(alert_to, subj, body_txt)
        if r0.get("sent_resend"):
            res_info["sent_smtp"] = True
            cust_em = (ticket_info.get("customer_email") or "").strip()
            if not force_test and cust_em and "@" in cust_em:
                send_raw_email_resend_or_smtp(
                    cust_em,
                    f"Weber's Tank Testing Ticket {t_no} ({cnt} Cyl)",
                    f"Thank you for checking in with Weber's Tank Testing (3086 Legionville Rd, Baden, PA).\n\n"
                    f"Ticket #: {t_no}\nCylinders: {cnt} ({svc})\n"
                    f"Estimated Ready: {ticket_info.get('estimated_ready') or 'Pending Schedule'}\n\n"
                    f"Track Status: {ticket_info.get('track_url', 'https://weberstanktesting.com/#tracker')}\n"
                    f"Ticket PDF: {ticket_info.get('pdf_url', '')}\n"
                    f"Questions? Call or text 724-266-8008.",
                )
            return res_info
        elif r0.get("detail"):
            res_info["detail"] = r0["detail"]

    # Channel 1: Direct Gmail SMTP (allows dedicated sender account for custom phone notification rules!)
    if smtp_user and smtp_pass and alert_to:
        try:
            msg = EmailMessage()
            msg["Subject"] = subj
            msg["From"] = f"Weber's Dock Alerts <{smtp_user}>"
            msg["To"] = alert_to
            msg.set_content(body_txt)
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=14) as s:
                s.login(smtp_user, smtp_pass)
                s.send_message(msg)
                cust_em = (ticket_info.get("customer_email") or "").strip()
                if not force_test and cust_em and "@" in cust_em:
                    cmsg = EmailMessage()
                    cmsg["Subject"] = f"Weber's Tank Testing Ticket {t_no} ({cnt} Cyl)"
                    cmsg["From"] = f"Weber's Tank Testing <{smtp_user}>"
                    cmsg["To"] = cust_em
                    cmsg.set_content(
                        f"Thank you for checking in with Weber's Tank Testing (3086 Legionville Rd, Baden, PA).\n\n"
                        f"Ticket #: {t_no}\nCylinders: {cnt} ({svc})\n"
                        f"Estimated Ready: {ticket_info.get('estimated_ready') or 'Pending Schedule'}\n\n"
                        f"Track Status: {ticket_info.get('track_url', 'https://weberstanktesting.com/#tracker')}\n"
                        f"Ticket PDF: {ticket_info.get('pdf_url', '')}\n"
                        f"Questions? Call or text 724-266-8008."
                    )
                    s.send_message(cmsg)
            res_info["sent_smtp"] = True
        except Exception as exc:
            res_info["detail"] = f"Gmail SMTP error: {exc}"

    # Channel 2: Google Sheets Apps Script Webhook
    if webhook_url:
        try:
            enriched_ticket = {**ticket_info, "shop_alert_email": alert_to, "alert_subject": subj, "alert_body": body_txt}
            payload_bytes = json.dumps({
                "action": "test_alert_email" if force_test else "dropoff_receipt_email",
                "shop_alert_email": alert_to,
                "subject": subj,
                "body": body_txt,
                "ticket": enriched_ticket,
            }).encode("utf-8")
            req = urllib.request.Request(
                webhook_url,
                data=payload_bytes,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            ctx = ssl.create_default_context()
            with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
                _ = resp.read()
            res_info["sent_webhook"] = True
        except Exception as exc:
            if not res_info["detail"]:
                res_info["detail"] = f"Webhook error: {exc}"

    if not res_info["sent_smtp"] and not res_info["sent_webhook"] and not res_info["detail"]:
        res_info["detail"] = "Enter Ryan's Alert Email + either a Google Sheets Webhook URL or Sender Gmail + App Password first."
    return res_info


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


AUTH_COOKIE = "webers_ops_auth"


AUTH_SECRET = (os.getenv("WEBERS_AUTH_SECRET") or "webers-rin-b386-midnight-auth-key").encode("utf-8")


def seconds_until_midnight_eastern() -> int:
    now_e = now_eastern()
    tomorrow_midnight = (now_e + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(60, int((tomorrow_midnight - now_e).total_seconds()))


def make_user_auth_token(user_id: int, initials: str, role: str, pin: str, auth_date: str) -> str:
    msg = f"B386:{user_id}:{initials}:{role}:{pin}:{auth_date}".encode("utf-8")
    sig = hmac.new(AUTH_SECRET, msg, hashlib.sha256).hexdigest()
    return f"{user_id}|{auth_date}|{sig}"


def make_tv_kiosk_token(pin_seed: str) -> str:
    return hmac.new(AUTH_SECRET, f"B386-TV:{pin_seed}".encode("utf-8"), hashlib.sha256).hexdigest()


def default_day_plan_for_date(d: date, default_cap: int = 24) -> tuple[str, int]:
    if d.weekday() >= 5:
        return ("BACKROOM_ONLY", 0)
    return ("FULL_HYDRO", int(default_cap or 24))


def get_date_jacket_ceilings(conn, work_date_iso: str) -> dict:
    out = {}
    for j_name in ("Short Jacket", "Tall Jacket"):
        row = conn.execute(
            "SELECT COALESCE(MAX(target_pressure_psi), 0) AS mx FROM daily_verifications WHERE test_date=? AND jacket_type=? AND passed=1;",
            (work_date_iso, j_name),
        ).fetchone()
        mx = int(row["mx"] or 0) if row else 0
        out[j_name] = {
            "max_calibrated_psi": mx,
            "max_allowed_test_psi": dot_max_allowed_test_psi(mx) if mx > 0 else 0,
        }
    return out


def get_current_session_user(request: Request) -> Optional[dict]:
    """Validates daily individual PIN session cookie — strictly resets at 12:00 AM Eastern."""
    raw = request.cookies.get(AUTH_COOKIE, "")
    today_iso = today_eastern()
    if raw and "|" in raw:
        parts = raw.split("|")
        if len(parts) == 3:
            uid_str, c_date, sig = parts
            if c_date == today_iso and uid_str.isdigit():
                uid = int(uid_str)
                conn = get_conn()
                u = conn.execute("SELECT * FROM crew_users WHERE id=? AND active=1;", (uid,)).fetchone()
                conn.close()
                if u:
                    expected = make_user_auth_token(u["id"], u["initials"], u["role"], u["pin"], today_iso).split("|")[-1]
                    if hmac.compare_digest(sig, expected):
                        return {
                            "id": u["id"],
                            "name": u["name"],
                            "initials": u["initials"],
                            "role": u["role"],
                            "can_sign_dot": bool(u["can_sign_dot"] and u["role"] == "OWNER"),
                        }
    return None


def is_tv_authorized(request: Request) -> bool:
    if get_current_session_user(request):
        return True
    tv_tok = request.cookies.get("webers_tv_auth", "")
    if not tv_tok:
        return False
    conn = get_conn()
    s_row = conn.execute("SELECT value FROM settings WHERE key IN ('shop_pin', 'crew_pin') LIMIT 1;").fetchone()
    conn.close()
    seed = s_row["value"] if s_row else "0386"
    return hmac.compare_digest(tv_tok, make_tv_kiosk_token(seed))


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
    cust_clean = payload.customer_name.strip() or "Walk-In"
    phone_clean = clean_phone(payload.customer_phone)
    spec_clean = payload.dot_spec.strip().upper().replace("SP-", "SP")
    if not serial_clean or not spec_clean or payload.service_psi <= 0:
        raise HTTPException(400, "Serial #, DOT Spec, and Service Pressure are required.")

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
            ree_limit, service_mode, star_10yr, shop_stage, intake_by, operator_name, operator_initials, notes)
           VALUES (?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'IN_SHOP', ?, 'Ryan Pearce', 'RP', ?);""",
        (
            w_date, intake_ts, cust_clean, phone_clean,
            payload.dropoff_id, payload.schedule_job_id, pri, rfee, prom_date, zone,
            serial_clean, v_tag, payload.mfg_id.strip().upper(), payload.mfg_date.strip(),
            gas_clean, dia, length, phys_size,
            spec_clean, payload.service_psi, calc_test_psi,
            ree, payload.service_mode, (1 if (payload.star_10yr and spec_clean in ('3A', '3AA')) else 0), payload.intake_by.strip().upper()[:4] or "RP", payload.notes,
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
        scanned_cnt = conn.execute(
            "SELECT COUNT(*) FROM cylinder_records WHERE schedule_job_id=?;",
            (payload.schedule_job_id,),
        ).fetchone()[0]
        conn.execute(
            """UPDATE schedule_jobs
               SET prep_status = CASE WHEN ? >= COALESCE(bottle_count, 1) THEN 'DEVALVED_TAGGED' ELSE 'IN_PREP' END,
                   holding_zone = 'ZONE_C_READY_RACK',
                   status = CASE WHEN ? >= COALESCE(bottle_count, 1) THEN 'PREPPED' ELSE status END,
                   synced = 0
               WHERE id = ?;""",
            (scanned_cnt, scanned_cnt, payload.schedule_job_id),
        )

    bump_db_version(conn)
    conn.commit()
    row = dict(conn.execute("SELECT * FROM cylinder_records WHERE id=?;", (rid,)).fetchone())
    conn.close()
    return row


def _notify_ryan_low_supply_bg(item_row: dict) -> None:
    def _send():
        conn = get_conn()
        r = conn.execute("SELECT value FROM settings WHERE key='alert_email';").fetchone()
        conn.close()
        to_em = (os.getenv("WEBER_ALERT_EMAIL") or (r["value"] if r else "") or "").strip()
        if not to_em:
            return
        subj = f"[WEBER'S LOW SUPPLY] {item_row['item_name']} is down to {item_row['on_hand_qty']} {item_row.get('unit_label') or 'left'}"
        body = (
            f"Shop Supply Replacement Alert — Weber's Tank Testing\n"
            f"----------------------------------------------------\n"
            f"Item:             {item_row['item_name']}\n"
            f"Category:         {item_row.get('category', '')}\n"
            f"Current On-Hand:  {item_row['on_hand_qty']} {item_row.get('unit_label') or 'units'}\n"
            f"Alert Limit:      <= {item_row['reorder_threshold']} {item_row.get('unit_label') or 'units'}\n"
            f"Spec / Note:      {item_row.get('spec_note') or '—'}\n\n"
            f"View Supplies in Office: https://ops.weberstanktesting.com/office?tab=supplies"
        )
        send_raw_email_resend_or_smtp(to_em, subj, body)

    try:
        asyncio.get_running_loop().create_task(asyncio.to_thread(_send))
    except RuntimeError:
        pass


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
        row = conn.execute("SELECT * FROM shop_day_plans WHERE work_date=?;", (iso,)).fetchone()
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

__all__ = ['ROOT_DIR', 'STATIC_DIR', 'SHEETS_SYNC_LOCK', 'NO_CACHE_HEADERS', 'SHIFT_MODES', 'HOLDING_ZONES', 'GAS_CODE_MAP', 'MAX_RUSH_BOTTLES', 'DEPT_KEYWORDS', 'now_eastern', 'today_eastern', 'parse_iso_date', 'days_since', 'days_until', 'normalize_gas_service', 'clean_phone', 'split_mailing_address', 'upsert_rolodex_from_activity', 'get_compliance_alerts', 'evaluate_composite_life', 'dot_max_allowed_test_psi', 'queue_sort_key', 'enrich_record_meta', 'SHOP_LAT', 'SHOP_LON', 'SHOP_ORIGIN_ADDR', 'LOCAL_PA_COORDS', 'build_shop_maps_url', 'estimate_route_from_shop', '_fmt_12h', 'get_operating_schedule_bundle', '_fast_local_eta', '_compute_leave_by_time', 'enrich_schedule_job_view', 'resolve_day_plan', 'build_month_calendar_grid', 'compute_recall_list', 'get_binder_status', 'calculate_turnaround_quote', 'check_duplicate_active_valve_tag', 'check_duplicate_serial_queued', 'push_unsynced_to_sheets', 'run_guarded_sheets_sync', 'queue_customer_sms_outbox', 'send_raw_email_resend_or_smtp', 'send_ticket_email_via_webhook', 'trigger_ticket_email_bg', 'trigger_sheets_sync_bg', '_quiet_bg_sync', 'wipe_sheets_remote', 'background_sheets_sync_loop', 'AUTH_COOKIE', 'AUTH_SECRET', 'seconds_until_midnight_eastern', 'make_user_auth_token', 'make_tv_kiosk_token', 'default_day_plan_for_date', 'get_date_jacket_ceilings', 'get_current_session_user', 'is_tv_authorized', 'lookup_spec_defaults', 'insert_cylinder_record', '_notify_ryan_low_supply_bg', 'require_owner_or_admin', 'run_smart_autopace']
