import asyncio
import hashlib
import hmac
import json
import os
import re
import socket
import ssl
import urllib.error
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
    build_invoice_pdf,
    build_retest_datasheet_pdf,
    build_rin_stickers_pdf,
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

# Strictly role-based operational shift labels (zero personal names)
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


def now_eastern() -> datetime:
    """Locks server timestamps and working dates to Pennsylvania Eastern Time."""
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


def evaluate_composite_life(dot_spec: str, mfg_date: str, ref_iso_date: Optional[str] = None) -> dict:
    """Checks 15-year DOT service life on carbon/fiberglass Special Permit composite cylinders."""
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
    """Orders active queue: Paid Rush -> High Priority -> Earliest Deadline -> Oldest Sitting -> ID."""
    pri = (r.get("priority") or "STANDARD").upper()
    is_rush = 0 if (r.get("rush_fee") or pri == "RUSH") else (1 if pri == "HIGH" else 2)
    due = r.get("promised_date") or "9999-12-31"
    arrived = (r.get("intake_at") or r.get("work_date") or "")[:10]
    return (is_rush, due, arrived, r.get("id") or 0)


def enrich_record_meta(r: dict, ref_today: str) -> dict:
    life = evaluate_composite_life(r.get("dot_spec", ""), r.get("mfg_date", ""), ref_today)
    r["composite_warning"] = life.get("warning")
    r["composite_expired"] = life.get("expired", False)
    origin_date = (r.get("intake_at") or r.get("work_date") or "")[:10]
    r["pre_test_days"] = days_since(origin_date, ref_today) if r.get("status") == "QUEUED" else days_since(origin_date, r.get("work_date") or ref_today)
    r["post_test_days"] = days_since(r.get("work_date"), ref_today) if (r.get("status") == "COMPLETED" and r.get("shop_stage") != "PICKED_UP") else 0
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
            "shift_mode": mode,
            "shift_label": SHIFT_MODES[mode]["label"],
            "bottle_capacity": cap,
            "day_notes": saved.get("day_notes") or "",
            "is_custom": True,
        }
    # Default shop schedule: Mon-Fri Full Hydro, Sat-Sun 24/7 Backroom Self-Drop
    is_weekend = d.weekday() >= 5
    mode = "BACKROOM_ONLY" if is_weekend else "FULL_HYDRO"
    cap = 0 if is_weekend else default_cap
    return {
        "work_date": iso,
        "weekday": d.strftime("%a"),
        "shift_mode": mode,
        "shift_label": SHIFT_MODES[mode]["label"],
        "bottle_capacity": cap,
        "day_notes": "",
        "is_custom": False,
    }


def get_binder_status(conn, s_map: dict) -> dict:
    """Computes exact 12-row DOT Retest Sheet continuity from previous sessions."""
    base_sheet = int(s_map.get("starting_sheet_no") or 8095)
    saved_cur = int(s_map.get("current_sheet_no") or base_sheet)
    max_row = conn.execute(
        "SELECT COALESCE(MAX(sheet_no), 0) AS m FROM cylinder_records WHERE service_mode='HYDRO_AND_VISUAL';"
    ).fetchone()
    max_db_sheet = int(max_row["m"] or 0)
    active_sheet = max(base_sheet, saved_cur, max_db_sheet)

    used_on_active = conn.execute(
        "SELECT COUNT(*) AS cnt FROM cylinder_records WHERE service_mode='HYDRO_AND_VISUAL' AND sheet_no=?;",
        (active_sheet,),
    ).fetchone()["cnt"]

    if used_on_active >= 12:
        active_sheet += 1
        used_on_active = 0

    last_rec = conn.execute(
        """SELECT sheet_no, work_date, serial_number, customer_name
           FROM cylinder_records
           WHERE service_mode='HYDRO_AND_VISUAL'
           ORDER BY id DESC LIMIT 1;"""
    ).fetchone()
    last_sheet_count = 0
    if last_rec:
        last_sheet_count = conn.execute(
            "SELECT COUNT(*) FROM cylinder_records WHERE service_mode='HYDRO_AND_VISUAL' AND sheet_no=?;",
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
    """Calculates realistic Standard & Paid Rush completion dates using backlog & hydro shift windows."""
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
           FROM dropoffs WHERE status='IN_BACKROOM' AND entry_type='DROPOFF';"""
    ).fetchone()

    sched_row = conn.execute(
        """SELECT COALESCE(SUM(bottle_count), 0) AS tot,
                  COALESCE(SUM(CASE WHEN rush_fee=1 OR priority='RUSH' THEN bottle_count ELSE 0 END), 0) AS rush_tot
           FROM schedule_jobs WHERE status IN ('OPEN', 'PREPPED') AND job_type IN ('SHOP_BATCH', 'ROAD_PICKUP');"""
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
        # If it is already past 1:00 PM EST today, halve remaining same-day capacity
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

    # Add 1 business day on Standard for hot-air drying, stamping/stickering & revalving
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
        "/", "/ops", "/tv", "/checkin", "/manifest.json", "/sw.js", "/icon.svg",
        "/api/auth/status", "/api/auth/login", "/api/auth/logout",
        "/api/public/dropoff",
    }
    if path in public_paths or path.startswith("/static"):
        return await call_next(request)
    if not is_authenticated(request):
        return JSONResponse({"detail": "Crew PIN login required."}, status_code=401)
    return await call_next(request)


# --- PYDANTIC SCHEMAS ---
class LoginInput(BaseModel):
    pin: str


class PublicDropoffInput(BaseModel):
    entry_type: str = "DROPOFF"
    customer_name: str
    customer_phone: str = ""
    bottle_count: int = 1
    tank_summary: str = ""
    service_requested: str = "Hydro + Visual"
    rush_requested: bool = False
    notes: str = ""
    website_hp: str = ""


class DropoffStatusInput(BaseModel):
    status: Optional[str] = None
    holding_zone: Optional[str] = None
    rush_requested: Optional[bool] = None
    promised_date: Optional[str] = None


class BinderActionInput(BaseModel):
    action: str = "continue"  # 'continue' | 'fresh_next' | 'set_custom'
    sheet_no: Optional[int] = None


class DayPlanInput(BaseModel):
    work_date: str
    shift_mode: str = "FULL_HYDRO"
    bottle_capacity: Optional[int] = None
    day_notes: str = ""


class ScheduleJobInput(BaseModel):
    scheduled_date: str
    due_date: str = ""
    job_type: str = "SHOP_BATCH"  # SHOP_BATCH | ROAD_PICKUP | ROAD_DELIVERY | ONSITE_VISUAL
    customer_name: str
    customer_phone: str = ""
    location_notes: str = ""
    bottle_count: int = 1
    tank_summary: str = ""
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
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    location_notes: Optional[str] = None
    bottle_count: Optional[int] = None
    tank_summary: Optional[str] = None
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


class ValveTagAssignInput(BaseModel):
    record_id: Optional[int] = None
    serial_number: Optional[str] = None
    work_date: Optional[str] = None
    valve_tag: Optional[str] = None
    valve_reinstalled: Optional[bool] = None


class ShopStageInput(BaseModel):
    shop_stage: str
    rin_marked: Optional[bool] = None
    rin_mark_type: Optional[str] = None  # 'STAMPED' | 'STICKERED'
    valve_reinstalled: Optional[bool] = None
    holding_zone: Optional[str] = None
    force: bool = False


class BatchStageInput(BaseModel):
    work_date: Optional[str] = None
    customer_name: str
    shop_stage: str
    rin_marked: Optional[bool] = None
    rin_mark_type: Optional[str] = None
    release_valves: bool = False
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
    chk_corrosion: str = "OK"
    chk_dents_dings: str = "OK"
    chk_cuts_gouges: str = "OK"
    chk_leaks: str = "OK"
    chk_fire_damage: str = "OK"
    chk_bulges: str = "OK"
    chk_neck_defects: str = "OK"
    hydro_disposition_override: Optional[str] = None
    notes: str = ""


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


# --- HOST-AWARE ROUTING ---
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
    if landing_file.exists():
        return FileResponse(landing_file, headers=NO_CACHE_HEADERS)
    return FileResponse(STATIC_DIR / "index.html", headers=NO_CACHE_HEADERS)


@app.get("/ops")
def serve_ops():
    return FileResponse(STATIC_DIR / "index.html", headers=NO_CACHE_HEADERS)


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


# --- PUBLIC BACKROOM 24/7 DROP-OFF & PICKUP CHECK-IN ---
@app.post("/api/public/dropoff")
def submit_public_dropoff(payload: PublicDropoffInput):
    if payload.website_hp.strip():
        return {"logged": True}
    cust = payload.customer_name.strip()[:80]
    if len(cust) < 2:
        raise HTTPException(400, "Please enter your name, fire department, or company name.")
    cnt = max(1, min(250, int(payload.bottle_count or 1)))
    etype = "PICKUP_NOTICE" if payload.entry_type.strip().upper() == "PICKUP_NOTICE" else "DROPOFF"
    init_status = "PICKUP_LOGGED" if etype == "PICKUP_NOTICE" else "IN_BACKROOM"
    svc_txt = payload.service_requested.strip()[:60] or "Hydro + Visual"
    is_rush = 1 if (payload.rush_requested or "RUSH" in svc_txt.upper()) else 0

    est = now_eastern()
    ts = est.strftime("%Y-%m-%d %I:%M %p")
    w_date = est.date().isoformat()

    conn = get_conn()
    quote = calculate_turnaround_quote(conn, cnt)
    promised = quote["rush_promise_date"] if is_rush else quote["standard_promise_date"]
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO dropoffs
           (created_at, work_date, entry_type, customer_name, customer_phone,
            bottle_count, tank_summary, service_requested, rush_requested, promised_date,
            holding_zone, notes, status, synced)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ZONE_A_BACKROOM', ?, ?, 0);""",
        (
            ts, w_date, etype, cust, clean_phone(payload.customer_phone),
            cnt, payload.tank_summary.strip()[:120], svc_txt, is_rush,
            promised if etype == "DROPOFF" else "",
            payload.notes.strip()[:280], init_status,
        ),
    )
    did = cur.lastrowid
    bump_db_version(conn)
    conn.commit()
    conn.close()
    trigger_sheets_sync_bg()
    return {"logged": True, "ticket_no": f"BR-{did:04d}", "created_at": ts, "estimated_ready": promised}


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


# --- BINDER CONTINUITY & SCHEDULE / DISPATCH ROUTES ---
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
    ts = now_eastern().strftime("%Y-%m-%d %I:%M %p")
    pri = "RUSH" if payload.rush_fee else payload.priority.strip().upper()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO schedule_jobs
           (created_at, scheduled_date, due_date, job_type, customer_name, customer_phone,
            location_notes, bottle_count, tank_summary, priority, rush_fee, holding_zone,
            prep_status, status, notes, synced)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0);""",
        (
            ts,
            payload.scheduled_date.strip(),
            payload.due_date.strip() or payload.scheduled_date.strip(),
            payload.job_type.strip().upper(),
            cust,
            clean_phone(payload.customer_phone),
            payload.location_notes.strip()[:140],
            max(1, int(payload.bottle_count or 1)),
            payload.tank_summary.strip()[:140],
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


# --- CORE TELEMETRY, LIVE TV LEADERBOARD & BOOTSTRAP ---
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
        "SELECT COUNT(*) FROM dropoffs WHERE status IN ('IN_BACKROOM', 'PICKUP_LOGGED');"
    ).fetchone()[0]
    conn.close()
    return {"db_version": ver, "unsynced_count": unsynced, "backroom_waiting": backroom_waiting}


@app.get("/api/tv")
def get_tv_leaderboard(work_date: Optional[str] = None):
    est = now_eastern()
    est_today = est.date().isoformat()
    target_date = work_date or est_today
    conn = get_conn()
    s_map = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings;").fetchall()}
    default_cap = int(s_map.get("daily_hydro_capacity") or 24)

    plans_rows = conn.execute(
        "SELECT * FROM shop_day_plans WHERE work_date >= ? LIMIT 21;", (target_date,)
    ).fetchall()
    plans_map = {r["work_date"]: dict(r) for r in plans_rows}
    today_plan = resolve_day_plan(parse_iso_date(target_date) or est.date(), plans_map, default_cap)

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
            "SELECT * FROM dropoffs WHERE status IN ('IN_BACKROOM', 'CHECKED_IN', 'PICKUP_LOGGED') ORDER BY rush_requested DESC, id DESC LIMIT 20;"
        ).fetchall()
    ]

    upcoming_jobs = [
        dict(r)
        for r in conn.execute(
            """SELECT * FROM schedule_jobs
               WHERE status != 'COMPLETED' AND status != 'CANCELLED'
               ORDER BY CASE WHEN rush_fee=1 OR priority='RUSH' THEN 0 ELSE 1 END,
                        scheduled_date ASC, id ASC LIMIT 12;"""
        ).fetchall()
    ]

    needs_stamp_count = conn.execute(
        """SELECT COUNT(*) FROM cylinder_records
           WHERE status='COMPLETED' AND hydro_disposition='A' AND COALESCE(rin_marked,0)=0 AND shop_stage!='PICKED_UP';"""
    ).fetchone()[0]

    ready_shelf_count = conn.execute(
        "SELECT COUNT(*) FROM cylinder_records WHERE status='COMPLETED' AND shop_stage='READY_BACKROOM';"
    ).fetchone()[0]

    batch_rows = [
        dict(r)
        for r in conn.execute(
            """SELECT customer_name,
                      MAX(customer_phone) AS customer_phone,
                      MAX(rush_fee) AS is_rush,
                      MIN(CASE WHEN promised_date != '' THEN promised_date ELSE '9999-12-31' END) AS earliest_due,
                      MIN(COALESCE(NULLIF(SUBSTR(intake_at,1,10),''), work_date)) AS oldest_date,
                      COUNT(*) AS total,
                      SUM(CASE WHEN status='COMPLETED' THEN 1 ELSE 0 END) AS completed,
                      SUM(CASE WHEN status='COMPLETED' AND hydro_disposition='A' THEN 1 ELSE 0 END) AS passed,
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
        b["dwell_days"] = days_since(b.get("oldest_date"), est_today)

    unsynced = conn.execute(
        "SELECT (SELECT COUNT(*) FROM daily_verifications WHERE synced=0) + "
        "(SELECT COUNT(*) FROM cylinder_records WHERE synced=0 AND status='COMPLETED') + "
        "(SELECT COUNT(*) FROM invoices WHERE synced=0);"
    ).fetchone()[0]
    conn.close()

    # Role-based ESPN ticker headlines (zero personal names)
    ticker = [f"📅 TODAY'S MODE: {today_plan['shift_label'].upper()}" + (f" — {today_plan['day_notes']}" if today_plan["day_notes"] else "")]
    for j_name, jc in jacket_ceilings.items():
        if jc["max_allowed_test_psi"]:
            ticker.append(f"🟢 {j_name.upper()}: CERTIFIED ≤ {jc['max_allowed_test_psi']} PSI ({jc['verified_at']})")
        elif today_plan["shift_mode"] in ("FULL_HYDRO", "ROAD_RUN"):
            ticker.append(f"🟡 {j_name.upper()}: AWAITING MORNING CALIBRATION")

    for j in upcoming_jobs[:6]:
        j_icon = "🚚 PICKUP RUN" if j["job_type"] == "ROAD_PICKUP" else ("🚚 DELIVERY RUN" if j["job_type"] == "ROAD_DELIVERY" else "📌 SCHEDULED")
        rush_tag = " [🔥 RUSH]" if (j["rush_fee"] or j["priority"] == "RUSH") else ""
        ticker.append(f"{j_icon} ({j['scheduled_date']}): {j['customer_name']} — {j['bottle_count']} tank(s){rush_tag}")

    for d in dropoffs:
        if d["status"] == "IN_BACKROOM":
            r_lbl = " [🔥 RUSH]" if d.get("rush_requested") else ""
            ticker.append(f"📥 BACKROOM DROP-OFF: {d['customer_name']} — {d['bottle_count']} tank(s){r_lbl}")

    if needs_stamp_count:
        ticker.append(f"🔨 FINISH BENCH: {needs_stamp_count} DRIED CYLINDER(S) WAITING FOR B386 STAMP / STICKER")

    for b in batch_rows:
        if b["dwell_days"] >= 4 and b["completed"] < b["total"]:
            ticker.append(f"⏳ AGING PRE-TEST ({b['dwell_days']}d): {b['customer_name'].upper()} — {b['total'] - b['completed']} BOTTLE(S) WAITING")

    if ready_shelf_count:
        ticker.append(f"📦 PICKUP SHELF: {ready_shelf_count} COMPLETED CYLINDER(S) STAGED IN ZONE E")

    return {
        "db_version": int(s_map.get("db_version", "1")),
        "testing_mode": s_map.get("testing_mode", "1") == "1",
        "dot_rin": s_map.get("dot_rin", "B386"),
        "work_date": target_date,
        "server_time": est.strftime("%I:%M %p"),
        "today_plan": today_plan,
        "unsynced_count": unsynced,
        "has_sheets_webhook": bool(s_map.get("sheets_webhook_url")),
        "jacket_ceilings": jacket_ceilings,
        "on_stand": queued_raw[0] if queued_raw else None,
        "up_next": queued_raw[1:12],
        "recent_completed": list(reversed(completed_today_rows[-10:])),
        "active_valves": active_valves,
        "backroom_dropoffs": dropoffs,
        "upcoming_schedule": upcoming_jobs,
        "batch_leaderboard": batch_rows,
        "scoreboard": {
            "queued_count": len(queued_raw),
            "completed_today": len(completed_today_rows),
            "passed_today": passed_today,
            "condemned_today": condemned_today,
            "active_tags_count": len(active_valves),
            "needs_stamp_count": needs_stamp_count,
            "backroom_waiting_count": sum(1 for d in dropoffs if d["status"] in ("IN_BACKROOM", "PICKUP_LOGGED")),
            "ready_pickup_count": ready_shelf_count,
        },
        "ticker": ticker,
    }


@app.get("/api/bootstrap")
def bootstrap(work_date: Optional[str] = None):
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

    # Build 14-day wall calendar strip around selected date / today
    cal_anchor = parse_iso_date(target_date) or now_eastern().date()
    win_start = cal_anchor - timedelta(days=1)
    plans_rows = conn.execute(
        "SELECT * FROM shop_day_plans WHERE work_date >= ? AND work_date <= ?;",
        (win_start.isoformat(), (win_start + timedelta(days=20)).isoformat()),
    ).fetchall()
    plans_map = {r["work_date"]: dict(r) for r in plans_rows}
    day_plans = [resolve_day_plan(win_start + timedelta(days=i), plans_map, default_cap) for i in range(14)]
    selected_day_plan = resolve_day_plan(cal_anchor, plans_map, default_cap)

    schedule_jobs = [
        dict(r)
        for r in conn.execute(
            """SELECT * FROM schedule_jobs
               WHERE status != 'CANCELLED'
               ORDER BY CASE WHEN status='COMPLETED' THEN 1 ELSE 0 END,
                        CASE WHEN rush_fee=1 OR priority='RUSH' THEN 0 ELSE 1 END,
                        scheduled_date ASC, id ASC LIMIT 80;"""
        ).fetchall()
    ]
    for sj in schedule_jobs:
        sj["dwell_days"] = days_since(sj.get("created_at"), est_today)
        sj["holding_zone_label"] = HOLDING_ZONES.get(sj.get("holding_zone") or "", sj.get("holding_zone") or "")

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

    # Include ALL active QUEUED bottles across carryover prep days + COMPLETED bottles for target_date
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

    # All completed cylinders across any date still in Drying / Stamping / Revalving / Pickup Shelf
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
               WHERE status != 'ARCHIVED'
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
               ORDER BY last_date DESC, customer_name ASC;"""
        ).fetchall()
    ]
    for c in customers_summary:
        c["shelf_dwell_days"] = days_since(c.get("oldest_shelf_date"), est_today) if c.get("oldest_shelf_date") else 0

    max_queued_test_psi = max((int(r.get("test_psi") or 0) for r in queued_part), default=0)
    recommended_cal_psi = 0
    if max_queued_test_psi > 0:
        for t in targets:
            if dot_max_allowed_test_psi(int(t["pressure_psi"])) >= max_queued_test_psi:
                recommended_cal_psi = int(t["pressure_psi"])
                break
        if not recommended_cal_psi and targets:
            recommended_cal_psi = int(targets[-1]["pressure_psi"])

    rates = [dict(r) for r in conn.execute("SELECT * FROM pricing_rates ORDER BY sort_order ASC, code ASC;").fetchall()]
    invoices = [dict(r) for r in conn.execute("SELECT * FROM invoices ORDER BY id DESC LIMIT 50;").fetchall()]
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
        "default_tax_rate": float(s_map.get("default_tax_rate", "0") or 0),
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


# --- CALIBRATION, INTAKE & MULTI-ANGLE VISION ---
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
        prev_ph = conn.execute(
            "SELECT customer_phone FROM cylinder_records WHERE customer_name=? AND customer_phone!='' ORDER BY id DESC LIMIT 1;",
            (cust_clean,),
        ).fetchone()
        if prev_ph:
            phone_clean = prev_ph["customer_phone"]

    # Inherit Rush & Promised Date from linked Drop-Off or Scheduled Batch if present
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

    s_map = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings;").fetchall()}
    binder = get_binder_status(conn, s_map)
    assigned_sheet_no = binder["active_sheet_no"]
    if payload.service_mode == "HYDRO_AND_VISUAL":
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('current_sheet_no', ?);", (str(assigned_sheet_no),))

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
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'IN_SHOP', ?, 'Ryan Pearce', 'RP', ?);""",
        (
            w_date, intake_ts, assigned_sheet_no, cust_clean, phone_clean,
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
                   status = CASE WHEN status='IN_BACKROOM' THEN 'CHECKED_IN' ELSE status END,
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

    # Enforce Stamp / Sticker gate before staging a passed cylinder to the Pickup Shelf
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

    conn.execute(
        """UPDATE cylinder_records
           SET shop_stage=?, rin_marked=?, rin_mark_type=?, valve_reinstalled=?, holding_zone=?, synced=0
           WHERE id=?;""",
        (stage, rin_marked, rin_type, reinst, zone, record_id),
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
                    f"{unmarked} bottle(s) for {payload.customer_name} are not marked Stamped/Stickered yet. Mark them Stamped/Stickered first.",
                )
            conn.execute(
                f"UPDATE cylinder_records SET shop_stage='READY_BACKROOM', valve_reinstalled=1, holding_zone='ZONE_E_PICKUP_SHELF', synced=0 {where_sql};",
                tuple(params),
            )
    elif stage == "PICKED_UP":
        conn.execute(
            f"UPDATE cylinder_records SET shop_stage='PICKED_UP', valve_reinstalled=1, holding_zone='PICKED_UP', synced=0 {where_sql};",
            tuple(params),
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


# --- GEMINI VISION SCANNER ---
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

    if rec["service_mode"] == "HYDRO_AND_VISUAL":
        ver_row = conn.execute(
            """SELECT id, target_pressure_psi FROM daily_verifications
               WHERE test_date=? AND jacket_type=? AND passed=1
               ORDER BY target_pressure_psi DESC, id DESC LIMIT 1;""",
            (eff_test_date, payload.jacket_type),
        ).fetchone()
        if not ver_row:
            conn.close()
            raise HTTPException(400, f"Calibrate {payload.jacket_type} for {eff_test_date} on Tab 1 first.")
        allowed_max = dot_max_allowed_test_psi(int(ver_row["target_pressure_psi"]))
        if int(rec["test_psi"]) > allowed_max:
            conn.close()
            raise HTTPException(
                400,
                f"{payload.jacket_type} calibrated to {ver_row['target_pressure_psi']} PSI (covers ≤ {allowed_max} PSI). Calibrate higher for {rec['test_psi']} PSI.",
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

    tested_by = (payload.tested_by or "RP").strip().upper()[:4] or "RP"
    completed_ts = now_eastern().strftime("%I:%M %p")
    rin_marked = 1 if (payload.rin_marked or payload.rin_mark_type) else 0
    rin_type = payload.rin_mark_type.strip().upper() if payload.rin_mark_type else ("MARKED" if rin_marked else "")

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
           work_date=?, status='COMPLETED', shop_stage=?, holding_zone=?, verification_id=?, jacket_type=?,
           valve_tag=?, valve_reinstalled=?, rin_marked=?, rin_mark_type=?,
           total_cc=?, permanent_cc=?, elastic_cc=?, percent_permanent=?,
           coating_type=?, coating_condition=?,
           chk_corrosion=?, chk_dents_dings=?, chk_cuts_gouges=?, chk_leaks=?,
           chk_fire_damage=?, chk_bulges=?, chk_neck_defects=?,
           relief_valve_changed=?, visual_pass=?, hydro_disposition=?, visual_disposition=?,
           tested_by=?, completed_at=?, operator_name='Ryan Pearce', operator_initials='RP', notes=?, synced=0
           WHERE id=?;""",
        (
            eff_test_date, next_stage, next_zone, ver_id,
            payload.jacket_type if rec["service_mode"] == "HYDRO_AND_VISUAL" else "Road/Visual",
            final_vtag, final_reinst, rin_marked, rin_type,
            payload.total_cc, payload.permanent_cc, elastic, pct_perm,
            payload.coating_type, payload.coating_condition,
            payload.chk_corrosion, payload.chk_dents_dings, payload.chk_cuts_gouges,
            payload.chk_leaks, payload.chk_fire_damage, payload.chk_bulges, payload.chk_neck_defects,
            payload.relief_valve_changed, vis_pass, hydro_disp, vis_disp,
            tested_by, completed_ts, payload.notes, record_id,
        ),
    )
    bump_db_version(conn)
    conn.commit()
    conn.close()
    trigger_sheets_sync_bg()
    return {
        "id": record_id,
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
    phone_clean = clean_phone(payload.customer_phone) or (rec["customer_phone"] or "")
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

    conn.execute(
        """UPDATE cylinder_records SET
           customer_name=?, customer_phone=?, priority=?, rush_fee=?, promised_date=?, holding_zone=?,
           serial_number=?, valve_tag=?, valve_reinstalled=?, rin_marked=?, rin_mark_type=?, shop_stage=?,
           mfg_id=?, mfg_date=?, gas_service=?, outside_dia=?, length_in=?, physical_size=?,
           dot_spec=?, service_psi=?, test_psi=?, ree_limit=?,
           total_cc=?, permanent_cc=?, elastic_cc=?, percent_permanent=?,
           coating_condition=?, relief_valve_changed=?,
           chk_corrosion=?, chk_dents_dings=?, chk_cuts_gouges=?, chk_leaks=?,
           chk_fire_damage=?, chk_bulges=?, chk_neck_defects=?,
           visual_pass=?, hydro_disposition=?, visual_disposition=?,
           operator_name='Ryan Pearce', operator_initials='RP', notes=?, synced=0
           WHERE id=?;""",
        (
            payload.customer_name.strip(), phone_clean, pri, rfee,
            payload.promised_date.strip(), payload.holding_zone or rec["holding_zone"] or "ZONE_C_READY_RACK",
            payload.serial_number.strip().upper().replace(" ", ""),
            vtag, reinst, rin_marked, rin_type, payload.shop_stage or rec["shop_stage"] or "IN_SHOP",
            payload.mfg_id.strip().upper(), payload.mfg_date.strip(), gas_clean,
            dia, length, phys_size, spec_clean, payload.service_psi, payload.test_psi, payload.ree_limit,
            payload.total_cc, payload.permanent_cc, elastic, pct_perm,
            payload.coating_condition, payload.relief_valve_changed,
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
            "SELECT customer_phone FROM cylinder_records WHERE customer_name=? AND customer_phone!='' ORDER BY id DESC LIMIT 1;",
            (payload.customer_name,),
        ).fetchone()
        if ph_row:
            phone_clean = ph_row["customer_phone"]

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
    if customer:
        rows = conn.execute(
            "SELECT * FROM cylinder_records WHERE work_date=? AND customer_name=? AND status='COMPLETED' ORDER BY id ASC;",
            (work_date, customer),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM cylinder_records WHERE work_date=? AND status='COMPLETED' ORDER BY id ASC;",
            (work_date,),
        ).fetchall()
    conn.close()
    pdf_bytes = build_retest_datasheet_pdf(
        work_date=work_date,
        records=[dict(r) for r in rows],
        dot_rin=s_map.get("dot_rin", "B386"),
        tc_reg=s_map.get("tc_reg", ""),
        base_sheet_no=sheet_no or int(s_map.get("current_sheet_no") or s_map.get("starting_sheet_no") or 8095),
    )
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="DOT_Retest_Sheet_{work_date}.pdf"'},
    )


@app.get("/api/pdf/verification")
def download_verification_pdf(work_date: str):
    conn = get_conn()
    s_map = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings;").fetchall()}
    rows = conn.execute("SELECT * FROM daily_verifications WHERE test_date=? ORDER BY id ASC;", (work_date,)).fetchall()
    conn.close()
    pdf_bytes = build_verification_pdf(
        work_date=work_date,
        verifications=[dict(r) for r in rows],
        cal_serial=s_map.get("calibrated_serial", "1SF11904"),
    )
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="DOT_Verification_{work_date}.pdf"'},
    )


@app.get("/api/pdf/visual")
def download_visual_pdf(work_date: str, customer: str = ""):
    conn = get_conn()
    s_map = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings;").fetchall()}
    if customer:
        rows = conn.execute(
            "SELECT * FROM cylinder_records WHERE work_date=? AND customer_name=? AND status='COMPLETED' ORDER BY id ASC;",
            (work_date, customer),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM cylinder_records WHERE work_date=? AND status='COMPLETED' ORDER BY id ASC;",
            (work_date,),
        ).fetchall()
    conn.close()
    pdf_bytes = build_visual_log_pdf(
        work_date=work_date,
        records=[dict(r) for r in rows],
        dot_rin=s_map.get("dot_rin", "B386"),
    )
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="DOT_Visual_Log_{work_date}.pdf"'},
    )


@app.get("/api/pdf/stickers")
def download_stickers_pdf(work_date: str, count: int = 30, skip: int = 0, compact: bool = False):
    conn = get_conn()
    rin = conn.execute("SELECT value FROM settings WHERE key='dot_rin';").fetchone()["value"]
    conn.close()
    pdf_bytes = build_rin_stickers_pdf(
        work_date=work_date, count=count, dot_rin=rin, skip_slots=skip, compact=compact
    )
    suffix = "Mini_HPA" if compact else "Standard"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="B386_Stickers_{suffix}_{work_date}.pdf"'},
    )


@app.get("/api/pdf/valvetags")
def download_valve_tags_pdf(work_date: str, customer: str = ""):
    conn = get_conn()
    rin = conn.execute("SELECT value FROM settings WHERE key='dot_rin';").fetchone()["value"]
    if customer:
        rows = conn.execute(
            "SELECT * FROM cylinder_records WHERE work_date=? AND customer_name=? ORDER BY id ASC;",
            (work_date, customer),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM cylinder_records WHERE valve_tag != '' AND valve_reinstalled = 0 ORDER BY id ASC;"
        ).fetchall()
    conn.close()
    pdf_bytes = build_valve_tags_pdf(work_date=work_date, records=[dict(r) for r in rows], dot_rin=rin)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="Valve_Tag_Sheet_{work_date}.pdf"'},
    )


@app.get("/api/pdf/poster")
def download_backroom_poster_pdf(checkin_url: str = "[https://weberstanktesting.com/#dropoff](https://weberstanktesting.com/#dropoff)"):
    conn = get_conn()
    s_map = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings;").fetchall()}
    conn.close()
    pdf_bytes = build_backroom_poster_pdf(
        checkin_url=checkin_url,
        phone=s_map.get("phone", "724-266-8008"),
        dot_rin=s_map.get("dot_rin", "B386"),
    )
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": 'inline; filename="Webers_Backroom_DropOff_QR_Sign.pdf"'},
    )


@app.get("/api/pdf/invoice/{inv_id}")
def download_invoice_pdf(inv_id: int):
    conn = get_conn()
    inv = conn.execute("SELECT * FROM invoices WHERE id=?;", (inv_id,)).fetchone()
    if not inv:
        conn.close()
        raise HTTPException(404, "Invoice not found.")
    items = [
        dict(r)
        for r in conn.execute("SELECT * FROM invoice_items WHERE invoice_id=? ORDER BY id;", (inv_id,)).fetchall()
    ]
    conn.close()
    pdf_bytes = build_invoice_pdf(dict(inv), items)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{inv["invoice_no"]}.pdf"'},
    )


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")