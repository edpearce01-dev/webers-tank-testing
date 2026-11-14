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
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from database import bump_db_version, get_conn, init_db
from pdf_generator import (
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


def get_lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "localhost"


def dot_max_allowed_test_psi(max_cal_psi: int) -> int:
    if max_cal_psi <= 0:
        return 0
    return max_cal_psi + 500 if max_cal_psi >= 3000 else int(round(max_cal_psi * 1.10))


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


def check_duplicate_valve_tag(
    conn, work_date: str, valve_tag: str, exclude_id: Optional[int] = None
) -> Optional[dict]:
    clean = (valve_tag or "").strip()
    if not clean:
        return None
    sql = """SELECT id, serial_number, customer_name FROM cylinder_records
             WHERE work_date=? AND UPPER(valve_tag)=UPPER(?) AND valve_reinstalled=0"""
    params: list = [work_date, clean]
    if exclude_id is not None:
        sql += " AND id != ?"
        params.append(exclude_id)
    row = conn.execute(sql + " LIMIT 1;", tuple(params)).fetchone()
    return dict(row) if row else None


def push_unsynced_to_sheets() -> dict:
    conn = get_conn()
    webhook_row = conn.execute(
        "SELECT value FROM settings WHERE key='sheets_webhook_url';"
    ).fetchone()
    webhook_url = (webhook_row["value"] if webhook_row else "").strip()
    if not webhook_url:
        conn.close()
        return {"synced": False, "reason": "No Google Sheets Webhook URL set yet."}

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
    rates = [dict(r) for r in conn.execute("SELECT * FROM pricing_rates;").fetchall()]
    conn.close()

    if not cals and not recs and not invs:
        return {"synced": True, "pushed": 0}

    for r in recs:
        if r.get("percent_permanent") is not None:
            r["percent_permanent"] = round(float(r["percent_permanent"]), 1)
        vtag = (r.get("valve_tag") or "").strip()
        raw_notes = (r.get("notes") or "").strip()
        if vtag and f"Valve Tag #{vtag}" not in raw_notes:
            r["notes"] = f"[Valve Tag #{vtag}] {raw_notes}".strip()

    payload_bytes = json.dumps(
        {"calibrations": cals, "records": recs, "invoices": invs, "pricing_rates": rates}
    ).encode("utf-8")

    req = urllib.request.Request(
        webhook_url,
        data=payload_bytes,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=18, context=ctx) as resp:
        _ = resp.read()

    conn = get_conn()
    if cals:
        conn.executemany("UPDATE daily_verifications SET synced=1 WHERE id=?;", [(c["id"],) for c in cals])
    if recs:
        conn.executemany("UPDATE cylinder_records SET synced=1 WHERE id=?;", [(r["id"],) for r in recs])
    if invs:
        conn.executemany("UPDATE invoices SET synced=1 WHERE id=?;", [(iv["id"],) for iv in invs])
    bump_db_version(conn)
    conn.commit()
    conn.close()
    return {"synced": True, "pushed": len(cals) + len(recs) + len(invs)}


async def background_sheets_sync_loop():
    while True:
        await asyncio.sleep(15)
        try:
            await asyncio.to_thread(push_unsynced_to_sheets)
        except Exception:
            pass


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
    public_paths = {"/", "/manifest.json", "/sw.js", "/icon.svg", "/api/auth/status", "/api/auth/login", "/api/auth/logout"}
    if path in public_paths or path.startswith("/static"):
        return await call_next(request)
    if not is_authenticated(request):
        return JSONResponse({"detail": "Crew PIN login required."}, status_code=401)
    return await call_next(request)


class LoginInput(BaseModel):
    pin: str


class LadderStep(BaseModel):
    pressure_psi: int
    actual_total_cc: float


class VerificationLadderInput(BaseModel):
    test_date: Optional[str] = None
    jacket_type: str
    max_target_psi: int
    steps: list[LadderStep]
    permanent_cc: float = 0.0
    operator_name: str = "Ryan Pearce"
    operator_initials: str = "RP"


class SettingsInput(BaseModel):
    calibrated_serial: Optional[str] = None
    starting_sheet_no: Optional[int] = None
    gemini_api_key: Optional[str] = None
    sheets_webhook_url: Optional[str] = None
    shop_pin: Optional[str] = None


class CrewInput(BaseModel):
    full_name: str
    initials: str
    role: str = "Visual Inspector"


class IntakeInput(BaseModel):
    work_date: Optional[str] = None
    customer_name: str
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
    intake_by: str = "EP"
    notes: str = ""


class ValveTagAssignInput(BaseModel):
    record_id: Optional[int] = None
    serial_number: Optional[str] = None
    work_date: Optional[str] = None
    valve_tag: Optional[str] = None
    valve_reinstalled: Optional[bool] = None


class ScanBottleJsonInput(BaseModel):
    images_b64: list[str]
    work_date: Optional[str] = None
    customer_name: str = ""
    valve_tag: str = ""
    service_mode: str = "HYDRO_AND_VISUAL"
    gas_service: str = "AIR"
    outside_dia: str = ""
    length_in: str = ""
    intake_by: str = "EP"
    auto_import: bool = True


class TestCompleteInput(BaseModel):
    jacket_type: str = "Tall Jacket"
    total_cc: Optional[float] = None
    permanent_cc: Optional[float] = None
    max_percent_perm: float = 10.0
    valve_tag: Optional[str] = None
    valve_reinstalled: bool = False
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
    operator_name: str = "Ryan Pearce"
    operator_initials: str = "RP"
    notes: str = ""


class PricingRateUpdate(BaseModel):
    code: str
    unit_price: float


class InvoiceLineInput(BaseModel):
    record_id: Optional[int] = None
    description: str
    qty: float = 1.0
    unit_price: float = 0.0


class InvoiceCreateInput(BaseModel):
    work_date: str
    customer_name: str
    tax_rate: float = 0.0
    status: str = "DRAFT"
    created_by: str = "AP"
    notes: str = ""
    items: list[InvoiceLineInput]


class InvoiceStatusInput(BaseModel):
    status: str


# --- PWA & AUTH ROUTES ---
@app.get("/")
def serve_ui():
    return FileResponse(STATIC_DIR / "index.html")


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
        "name": "Weber's Tank Testing (DOT B386)",
        "short_name": "Weber's Hydro",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#1b4332",
        "theme_color": "#1b4332",
        "icons": [{"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any maskable"}],
    }


@app.get("/sw.js")
def serve_sw():
    js = "self.addEventListener('install', e => self.skipWaiting()); self.addEventListener('activate', e => self.clients.claim()); self.addEventListener('fetch', () => {});"
    return Response(content=js, media_type="application/javascript")


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


# --- CORE OPERATIONS ---
@app.get("/api/ping")
def ping_version():
    conn = get_conn()
    ver = int(conn.execute("SELECT value FROM settings WHERE key='db_version';").fetchone()["value"])
    unsynced = conn.execute(
        "SELECT (SELECT COUNT(*) FROM daily_verifications WHERE synced=0) + "
        "(SELECT COUNT(*) FROM cylinder_records WHERE synced=0 AND status='COMPLETED') + "
        "(SELECT COUNT(*) FROM invoices WHERE synced=0);"
    ).fetchone()[0]
    conn.close()
    return {"db_version": ver, "unsynced_count": unsynced}


@app.get("/api/bootstrap")
def bootstrap(work_date: Optional[str] = None):
    target_date = work_date or date.today().isoformat()
    conn = get_conn()
    s_map = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings;").fetchall()}
    cal_serial = s_map.get("calibrated_serial", "1SF11904")
    has_key = bool(os.getenv("GEMINI_API_KEY") or s_map.get("gemini_api_key"))
    has_webhook = bool(s_map.get("sheets_webhook_url"))

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

    specs = [dict(r) for r in conn.execute("SELECT * FROM cga_specs;").fetchall()]
    records = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM cylinder_records WHERE work_date=? ORDER BY id ASC;",
            (target_date,),
        ).fetchall()
    ]
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
                      COUNT(*) AS total_bottles,
                      MAX(work_date) AS last_date,
                      SUM(CASE WHEN status='COMPLETED' AND invoice_id IS NULL THEN 1 ELSE 0 END) AS unbilled_count
               FROM cylinder_records
               GROUP BY customer_name
               ORDER BY last_date DESC, customer_name ASC;"""
        ).fetchall()
    ]
    rates = [dict(r) for r in conn.execute("SELECT * FROM pricing_rates ORDER BY code;").fetchall()]
    invoices = [
        dict(r)
        for r in conn.execute("SELECT * FROM invoices ORDER BY id DESC LIMIT 50;").fetchall()
    ]
    unsynced = conn.execute(
        "SELECT (SELECT COUNT(*) FROM daily_verifications WHERE synced=0) + "
        "(SELECT COUNT(*) FROM cylinder_records WHERE synced=0 AND status='COMPLETED') + "
        "(SELECT COUNT(*) FROM invoices WHERE synced=0);"
    ).fetchone()[0]
    conn.close()
    ip = get_lan_ip()
    return {
        "selected_date": target_date,
        "today": date.today().isoformat(),
        "db_version": int(s_map.get("db_version", "1")),
        "dot_rin": s_map.get("dot_rin", "B386"),
        "starting_sheet_no": int(s_map.get("starting_sheet_no", "8095")),
        "calibrated_serial": cal_serial,
        "has_gemini_key": has_key,
        "has_sheets_webhook": has_webhook,
        "sheets_webhook_url": s_map.get("sheets_webhook_url", ""),
        "lan_https_url": f"https://{ip}:8443",
        "crew": crew,
        "calibration_targets": targets,
        "verifications": verifications,
        "jacket_ceilings": jacket_ceilings,
        "cga_specs": specs,
        "records": records,
        "dates_summary": dates_summary,
        "customers_summary": customers_summary,
        "pricing_rates": rates,
        "invoices": invoices,
        "unsynced_count": unsynced,
    }


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
    if payload.gemini_api_key is not None and payload.gemini_api_key.strip():
        CACHED_VISION_MODEL = None
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('gemini_api_key', ?);", (payload.gemini_api_key.strip(),))
    if payload.sheets_webhook_url is not None:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('sheets_webhook_url', ?);", (payload.sheets_webhook_url.strip(),))
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
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO crew_members (full_name, initials, role, active) VALUES (?, ?, ?, 1);",
        (payload.full_name.strip(), payload.initials.strip().upper()[:4], payload.role.strip()),
    )
    bump_db_version(conn)
    conn.commit()
    conn.close()
    return {"added": True}


@app.post("/api/sync-now")
async def manual_sync_now():
    try:
        return await asyncio.to_thread(push_unsynced_to_sheets)
    except Exception as exc:
        raise HTTPException(502, f"Google Sheets sync error: {exc}")


@app.post("/api/verify-ladder")
def log_verification_ladder(payload: VerificationLadderInput):
    t_date = payload.test_date or date.today().isoformat()
    t_time = datetime.now().strftime("%I:%M %p")
    run_id = f"{t_date}-{payload.jacket_type[:5]}-{datetime.now().strftime('%H%M%S')}"

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
                elastic_cc, percent_deviation, passed, operator_name, operator_initials)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);""",
            (
                run_id, t_date, t_time, payload.jacket_type, cal_serial,
                payload.max_target_psi, exp_psi, expected_a, actual_b,
                perm, elastic, pct_dev, step_passed, payload.operator_name, payload.operator_initials,
            ),
        )
    bump_db_version(conn)
    conn.commit()
    conn.close()
    return {
        "passed": bool(run_passed),
        "max_calibrated_psi": payload.max_target_psi if run_passed else 0,
        "max_allowed_test_psi": dot_max_allowed_test_psi(payload.max_target_psi) if run_passed else 0,
    }


def insert_cylinder_record(payload: IntakeInput) -> dict:
    w_date = payload.work_date or date.today().isoformat()
    v_tag = payload.valve_tag.strip()
    conn = get_conn()

    dup = check_duplicate_valve_tag(conn, w_date, v_tag)
    if dup:
        conn.close()
        raise HTTPException(
            409,
            f"Valve Tag #{v_tag} is already in use on SN {dup['serial_number']} ({dup['customer_name']}). Pick an unused tag # or mark that valve reinstalled.",
        )

    base_sheet = int(conn.execute("SELECT value FROM settings WHERE key='starting_sheet_no';").fetchone()["value"])
    prior_hydro_count = conn.execute(
        "SELECT COUNT(*) FROM cylinder_records WHERE service_mode='HYDRO_AND_VISUAL';"
    ).fetchone()[0]
    assigned_sheet_no = base_sheet + (prior_hydro_count // 12)

    dia = payload.outside_dia.strip()
    length = payload.length_in.strip()
    ree = payload.ree_limit
    calc_test_psi = payload.test_psi

    cga_match = conn.execute(
        """SELECT test_psi, outside_dia, length_in, ree_limit FROM cga_specs
           WHERE UPPER(dot_spec)=? AND service_psi=? LIMIT 1;""",
        (payload.dot_spec.strip().upper(), payload.service_psi),
    ).fetchone()

    if not calc_test_psi:
        calc_test_psi = int(cga_match["test_psi"]) if cga_match else round((payload.service_psi * 5) / 3)
    if cga_match and ree is None:
        ree = cga_match["ree_limit"]

    phys_size = f"{dia}x{length}".strip("x") if (dia or length) else ""
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO cylinder_records
           (work_date, sheet_no, customer_name, serial_number, valve_tag, mfg_id, mfg_date, gas_service,
            outside_dia, length_in, physical_size, dot_spec, service_psi, test_psi,
            ree_limit, service_mode, intake_by, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);""",
        (
            w_date, assigned_sheet_no, payload.customer_name.strip(), payload.serial_number.strip().upper(),
            v_tag, payload.mfg_id.strip().upper(), payload.mfg_date.strip(),
            payload.gas_service.strip().upper(), dia, length, phys_size,
            payload.dot_spec.strip().upper(), payload.service_psi, calc_test_psi,
            ree, payload.service_mode, payload.intake_by.strip().upper(), payload.notes,
        ),
    )
    bump_db_version(conn)
    conn.commit()
    rid = cur.lastrowid
    row = dict(conn.execute("SELECT * FROM cylinder_records WHERE id=?;", (rid,)).fetchone())
    conn.close()
    return row


@app.post("/api/intake")
def add_intake(payload: IntakeInput):
    return insert_cylinder_record(payload)


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

    new_tag = rec["valve_tag"] if payload.valve_tag is None else payload.valve_tag.strip()
    new_reinst = (
        int(rec["valve_reinstalled"] or 0)
        if payload.valve_reinstalled is None
        else (1 if payload.valve_reinstalled else 0)
    )

    if new_tag and not new_reinst:
        dup = check_duplicate_valve_tag(conn, rec["work_date"], new_tag, exclude_id=rec["id"])
        if dup:
            conn.close()
            raise HTTPException(
                409,
                f"Valve Tag #{new_tag} is already assigned to SN {dup['serial_number']} ({dup['customer_name']}) on {rec['work_date']}.",
            )

    conn.execute(
        "UPDATE cylinder_records SET valve_tag=?, valve_reinstalled=?, synced=0 WHERE id=?;",
        (new_tag, new_reinst, rec["id"]),
    )
    bump_db_version(conn)
    conn.commit()
    updated = dict(conn.execute("SELECT * FROM cylinder_records WHERE id=?;", (rec["id"],)).fetchone())
    conn.close()
    return updated


@app.delete("/api/records/{record_id}")
def delete_queued_record(record_id: int):
    conn = get_conn()
    conn.execute("DELETE FROM cylinder_records WHERE id=? AND status='QUEUED';", (record_id,))
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

    skip_tokens = ("tts", "embedding", "imagen", "veo", "audio", "robotics", "aqa", "learnlm", "image", "computer-use", "live", "2.5-flash", "2.0-flash", "1.5-")
    scored = []
    for m in data.get("models", []):
        if "generateContent" not in m.get("supportedGenerationMethods", []):
            continue
        short = m.get("name", "").replace("models/", "")
        low = short.lower()
        if not low.startswith("gemini-") or any(tok in low for tok in skip_tokens):
            continue
        ver = re.search(r"gemini-(\d+)(?:\.(\d+))?", low)
        major = int(ver.group(1)) if ver else 0
        minor = int(ver.group(2) or 0) if ver else 0
        is_flash = 1 if ("flash" in low and "lite" not in low) else 0
        is_stable = 0 if ("preview" in low or "exp" in low) else 1
        scored.append(((is_flash, major, minor, is_stable), short))
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
    for fb in ("gemini-3.8-flash", "gemini-3.0-flash", "gemini-flash-latest"):
        if fb not in candidates:
            candidates.append(fb)

    parts = [{"inline_data": {"mime_type": "image/jpeg", "data": b64}} for b64 in images_b64]
    parts.append({"text": prompt})
    body_bytes = json.dumps(
        {
            "contents": [{"parts": parts}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": {
                    "type": "OBJECT",
                    "properties": {
                        "detected_customer": {"type": "STRING", "nullable": True},
                        "serial_number": {"type": "STRING", "nullable": True},
                        "mfg_id": {"type": "STRING", "nullable": True},
                        "mfg_date": {"type": "STRING", "nullable": True},
                        "dot_spec": {"type": "STRING", "nullable": True},
                        "service_psi": {"type": "INTEGER", "nullable": True},
                        "ree_limit": {"type": "NUMBER", "nullable": True},
                        "gas_service": {"type": "STRING", "nullable": True},
                    },
                },
            },
        }
    ).encode("utf-8")

    ctx = ssl.create_default_context()
    last_error = "No compatible Gemini model responded."
    idx = 0
    while idx < len(candidates) and idx < 6:
        model_name = candidates[idx]
        idx += 1
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        req = urllib.request.Request(url, data=body_bytes, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=25, context=ctx) as resp:
                resp_json = json.loads(resp.read().decode("utf-8"))
                raw_text = resp_json["candidates"][0]["content"]["parts"][0]["text"].strip()
                if raw_text.startswith("```"):
                    raw_text = re.sub(r"^```(?:json)?|```$", "", raw_text, flags=re.MULTILINE).strip()
                parsed = json.loads(raw_text)
                CACHED_VISION_MODEL = model_name
                return parsed
        except urllib.error.HTTPError as http_err:
            err_detail = http_err.read().decode("utf-8", errors="ignore")
            if http_err.code == 404:
                for s in re.findall(r"models/(gemini-[a-zA-Z0-9.\-]+)", err_detail):
                    if s != model_name and s not in candidates:
                        candidates.insert(idx, s)
            last_error = f"Gemini HTTP {http_err.code} ({model_name}): {err_detail[:200]}"
    raise RuntimeError(last_error)


@app.post("/api/scan-bottle")
async def scan_and_import_bottle(payload: ScanBottleJsonInput):
    conn = get_conn()
    saved_key = conn.execute("SELECT value FROM settings WHERE key='gemini_api_key';").fetchone()["value"]
    conn.close()

    api_key = (os.getenv("GEMINI_API_KEY") or saved_key or "").strip()
    if not api_key:
        raise HTTPException(503, "Gemini API Key not set. Paste it in '? Guide & Settings'.")

    prompt = (
        "Read 1 to 6 photos around the curved shoulder/neck of the SAME compressed gas cylinder and combine all stamps:\n"
        "- serial_number: Cylinder serial without spaces (e.g. 'B6680'). Ignore 2x2 RIN stamps like B3/68.\n"
        "- dot_spec: Spec code only (e.g. 'ICC3A1800'->'3A', '3AA2015'->'3AA', '3AL3000'->'3AL', 'SP14387-4500'->'SP14387').\n"
        "- service_psi: Integer service pressure (e.g. 1800, 2015, 2250, 3000, 3442, 4500).\n"
        "- mfg_id: Manufacturer code/symbol (e.g. 'LUXFER', 'WK', 'M4625', 'S O CO', 'PST', 'TW').\n"
        "- mfg_date: Original manufacture date M-YY or MM-YY (ignore later retest dates).\n"
        "- gas_service: Gas from sticker/stencil (e.g. 'CO2', 'AIR', 'O2', else null).\n"
        "- detected_customer: Owner/Distributor name on sticker or stencil (else null).\n"
        "- ree_limit: Float REE value if printed on label, else null."
    )

    try:
        extracted = await asyncio.wait_for(
            asyncio.to_thread(call_gemini_vision_rest, api_key, payload.images_b64[:6], prompt),
            timeout=30.0,
        )
    except Exception as exc:
        raise HTTPException(502, str(exc))

    final_customer = payload.customer_name.strip() or (extracted.get("detected_customer") or "").strip()
    serial_clean = (extracted.get("serial_number") or "").replace(" ", "").strip()
    if serial_clean:
        extracted["serial_number"] = serial_clean

    missing = []
    if not final_customer:
        missing.append("Customer Name")
    if not serial_clean:
        missing.append("Serial Number")
    if not extracted.get("dot_spec"):
        missing.append("DOT Spec")
    if not extracted.get("service_psi"):
        missing.append("Service Pressure")

    if payload.auto_import and not missing:
        rec = insert_cylinder_record(
            IntakeInput(
                work_date=payload.work_date,
                customer_name=final_customer,
                serial_number=serial_clean,
                valve_tag=payload.valve_tag,
                mfg_id=extracted.get("mfg_id") or "",
                mfg_date=extracted.get("mfg_date") or "",
                gas_service=extracted.get("gas_service") or payload.gas_service or "AIR",
                outside_dia=payload.outside_dia,
                length_in=payload.length_in,
                dot_spec=extracted["dot_spec"],
                service_psi=int(extracted["service_psi"]),
                ree_limit=extracted.get("ree_limit"),
                service_mode=payload.service_mode,
                intake_by=payload.intake_by,
            )
        )
        return {"imported": True, "record": rec, "extracted": extracted}
    return {"imported": False, "extracted": extracted, "missing": missing}


@app.post("/api/records/{record_id}/complete")
def complete_record(record_id: int, payload: TestCompleteInput):
    conn = get_conn()
    rec = conn.execute("SELECT * FROM cylinder_records WHERE id=?;", (record_id,)).fetchone()
    if not rec:
        conn.close()
        raise HTTPException(404, "Cylinder record not found.")

    final_vtag = rec["valve_tag"] if payload.valve_tag is None else payload.valve_tag.strip()
    final_reinst = 1 if payload.valve_reinstalled else int(rec["valve_reinstalled"] or 0)
    if final_vtag and not final_reinst:
        dup = check_duplicate_valve_tag(conn, rec["work_date"], final_vtag, exclude_id=record_id)
        if dup:
            conn.close()
            raise HTTPException(
                409,
                f"Valve Tag #{final_vtag} is already assigned to SN {dup['serial_number']} ({dup['customer_name']}).",
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
            (rec["work_date"], payload.jacket_type),
        ).fetchone()
        if not ver_row:
            conn.close()
            raise HTTPException(400, f"Calibrate {payload.jacket_type} on Step 1 first.")
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
        ree_failed = rec["ree_limit"] is not None and elastic > float(rec["ree_limit"])
        perm_failed = pct_perm > payload.max_percent_perm
        if not vis_pass or ree_failed or perm_failed:
            hydro_disp = "D"

    conn.execute(
        """UPDATE cylinder_records SET
           status='COMPLETED', verification_id=?, jacket_type=?,
           valve_tag=?, valve_reinstalled=?,
           total_cc=?, permanent_cc=?, elastic_cc=?, percent_permanent=?,
           coating_type=?, coating_condition=?,
           chk_corrosion=?, chk_dents_dings=?, chk_cuts_gouges=?, chk_leaks=?,
           chk_fire_damage=?, chk_bulges=?, chk_neck_defects=?,
           relief_valve_changed=?, visual_pass=?, hydro_disposition=?, visual_disposition=?,
           operator_name=?, operator_initials=?, notes=?, synced=0
           WHERE id=?;""",
        (
            ver_id, payload.jacket_type if rec["service_mode"] == "HYDRO_AND_VISUAL" else "Road/Visual",
            final_vtag, final_reinst,
            payload.total_cc, payload.permanent_cc, elastic, pct_perm,
            payload.coating_type, payload.coating_condition,
            payload.chk_corrosion, payload.chk_dents_dings, payload.chk_cuts_gouges,
            payload.chk_leaks, payload.chk_fire_damage, payload.chk_bulges, payload.chk_neck_defects,
            payload.relief_valve_changed, vis_pass, hydro_disp, vis_disp,
            payload.operator_name, payload.operator_initials.strip().upper(), payload.notes, record_id,
        ),
    )
    bump_db_version(conn)
    conn.commit()
    conn.close()
    return {"id": record_id, "hydro_disposition": hydro_disp, "visual_disposition": vis_disp, "percent_permanent": pct_perm}


@app.get("/api/search")
def search_records(q: str = "", customer: str = ""):
    conn = get_conn()
    if customer:
        rows = conn.execute(
            "SELECT * FROM cylinder_records WHERE customer_name=? ORDER BY work_date DESC, id ASC;",
            (customer,),
        ).fetchall()
    else:
        raw_q = q.strip()
        like = f"%{raw_q}%"
        rows = conn.execute(
            """SELECT * FROM cylinder_records
               WHERE customer_name LIKE ?
                  OR serial_number LIKE ?
                  OR dot_spec LIKE ?
                  OR work_date LIKE ?
                  OR valve_tag LIKE ?
               ORDER BY work_date DESC, id DESC LIMIT 150;""",
            (like, like, like, like, like),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.post("/api/pricing")
def update_pricing_rates(rates: list[PricingRateUpdate]):
    conn = get_conn()
    for r in rates:
        conn.execute("UPDATE pricing_rates SET unit_price=? WHERE code=?;", (r.unit_price, r.code))
    bump_db_version(conn)
    conn.commit()
    conn.close()
    return {"updated": True}


@app.get("/api/unbilled")
def get_unbilled(work_date: str, customer_name: str):
    conn = get_conn()
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
    conn = get_conn()
    nxt_num = conn.execute("SELECT COUNT(*) + 1 FROM invoices;").fetchone()[0]
    inv_no = f"INV-{date.today().year}-{nxt_num:04d}"

    subtotal = round(sum(it.qty * it.unit_price for it in payload.items), 2)
    tax_amt = round(subtotal * (payload.tax_rate / 100.0), 2)
    total = round(subtotal + tax_amt, 2)

    cur = conn.cursor()
    cur.execute(
        """INSERT INTO invoices
           (invoice_no, invoice_date, work_date, customer_name, subtotal, tax_rate, tax_amount, total_amount, status, created_by, notes, synced)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0);""",
        (
            inv_no, date.today().isoformat(), payload.work_date, payload.customer_name,
            subtotal, payload.tax_rate, tax_amt, total, payload.status, payload.created_by, payload.notes,
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
    return {"id": inv_id, "invoice_no": inv_no, "total_amount": total}


@app.post("/api/invoices/{inv_id}/status")
def set_invoice_status(inv_id: int, payload: InvoiceStatusInput):
    conn = get_conn()
    conn.execute("UPDATE invoices SET status=?, synced=0 WHERE id=?;", (payload.status, inv_id))
    bump_db_version(conn)
    conn.commit()
    conn.close()
    return {"updated": True}


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
        manager_name=s_map.get("manager", "Ryan Pearce"),
        base_sheet_no=sheet_no or int(s_map.get("starting_sheet_no", "8095")),
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
        operator=s_map.get("operator", "Ryan Pearce"),
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
        manager=s_map.get("manager", "Ryan Pearce"),
    )
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="DOT_Visual_Log_{work_date}.pdf"'},
    )


@app.get("/api/pdf/stickers")
def download_stickers_pdf(work_date: str, count: int = 30, skip: int = 0):
    conn = get_conn()
    rin = conn.execute("SELECT value FROM settings WHERE key='dot_rin';").fetchone()["value"]
    conn.close()
    pdf_bytes = build_rin_stickers_pdf(work_date=work_date, count=count, dot_rin=rin, skip_slots=skip)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="B386_Stickers_{work_date}.pdf"'},
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
            "SELECT * FROM cylinder_records WHERE work_date=? ORDER BY id ASC;",
            (work_date,),
        ).fetchall()
    conn.close()
    pdf_bytes = build_valve_tags_pdf(work_date=work_date, records=[dict(r) for r in rows], dot_rin=rin)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="Valve_Tag_Sheet_{work_date}.pdf"'},
    )


@app.get("/api/pdf/invoice/{inv_id}")
def download_invoice_pdf(inv_id: int):
    conn = get_conn()
    inv = conn.execute("SELECT * FROM invoices WHERE id=?;", (inv_id,)).fetchone()
    if not inv:
        conn.close()
        raise HTTPException(404, "Invoice not found.")
    items = [dict(r) for r in conn.execute("SELECT * FROM invoice_items WHERE invoice_id=? ORDER BY id;", (inv_id,)).fetchall()]
    conn.close()
    pdf_bytes = build_invoice_pdf(dict(inv), items)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{inv["invoice_no"]}.pdf"'},
    )


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")