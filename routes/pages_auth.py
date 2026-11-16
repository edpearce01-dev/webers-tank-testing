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


@router.get("/")
def serve_root(request: Request):
    host = (request.headers.get("host") or "").split(":")[0].lower()
    landing_file = STATIC_DIR / "landing.html"
    if (host in ("weberstanktesting.com", "www.weberstanktesting.com") or request.query_params.get("view") == "public") and landing_file.exists():
        return FileResponse(landing_file, headers=NO_CACHE_HEADERS)
    return FileResponse(STATIC_DIR / "index.html", headers=NO_CACHE_HEADERS)


@router.get("/checkin")
def serve_public_checkin():
    landing_file = STATIC_DIR / "landing.html"
    return FileResponse(landing_file if landing_file.exists() else STATIC_DIR / "index.html", headers=NO_CACHE_HEADERS)


@router.get("/ops")
def serve_ops():
    return FileResponse(STATIC_DIR / "index.html", headers=NO_CACHE_HEADERS)


@router.get("/schedule")
def serve_schedule_page():
    f = STATIC_DIR / "schedule.html"
    return FileResponse(f if f.exists() else STATIC_DIR / "index.html", headers=NO_CACHE_HEADERS)


@router.get("/office")
def serve_office_page():
    f = STATIC_DIR / "office.html"
    return FileResponse(f if f.exists() else STATIC_DIR / "index.html", headers=NO_CACHE_HEADERS)


@router.get("/print")
def serve_print_vault_page():
    f = STATIC_DIR / "print.html"
    return FileResponse(f if f.exists() else STATIC_DIR / "office.html", headers=NO_CACHE_HEADERS)


@router.get("/owner")
def serve_owner_controls_page():
    f = STATIC_DIR / "owner.html"
    return FileResponse(f if f.exists() else STATIC_DIR / "office.html", headers=NO_CACHE_HEADERS)


@router.get("/tv")
def serve_tv_board():
    tv_file = STATIC_DIR / "tv.html"
    if not tv_file.exists():
        raise HTTPException(404, "tv.html not found in static/ folder.")
    return FileResponse(tv_file, headers=NO_CACHE_HEADERS)


@router.get("/icon.svg")
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


@router.get("/manifest.json")
def serve_manifest():
    conn = get_conn()
    t = compute_active_theme(conn)
    conn.close()
    t_tag = t["key"] if (t.get("active") and (t.get("enable_public") or t.get("enable_ops"))) else "CLASSIC"
    return JSONResponse(
        content={
            "id": "/ops",
            "name": "Weber's Tank Testing — Ops (DOT B386)",
            "short_name": "Weber's Ops",
            "start_url": "/ops",
            "scope": "/",
            "display": "standalone",
            "background_color": "#fdf8ee",
            "theme_color": "#0c261b",
            "icons": [
                {"src": f"/pwa-icon.png?size=192&mode=any&v=12.1&theme={t_tag}", "sizes": "192x192", "type": "image/png", "purpose": "any"},
                {"src": f"/pwa-icon.png?size=512&mode=any&v=12.1&theme={t_tag}", "sizes": "512x512", "type": "image/png", "purpose": "any"},
                {"src": f"/pwa-icon.png?size=192&mode=maskable&v=12.1&theme={t_tag}", "sizes": "192x192", "type": "image/png", "purpose": "maskable"},
                {"src": f"/pwa-icon.png?size=512&mode=maskable&v=12.1&theme={t_tag}", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
            ],
        },
        headers=NO_CACHE_HEADERS,
    )


@router.get("/sw.js")
def serve_sw():
    js = "self.addEventListener('install', e => self.skipWaiting()); self.addEventListener('activate', e => { e.waitUntil(caches.keys().then(ks => Promise.all(ks.map(k => caches.delete(k)))).then(() => self.clients.claim())); });"
    return Response(content=js, media_type="application/javascript", headers=NO_CACHE_HEADERS)


@router.get("/api/auth/status")
def auth_status(request: Request, tv: int = 0):
    user = get_current_session_user(request)
    if tv and not user and is_tv_authorized(request):
        return {"authenticated": True, "today": today_eastern(), "user": {"name": "Live TV Display", "initials": "TV", "role": "TV", "can_sign_dot": False}}
    return {
        "authenticated": bool(user),
        "today": today_eastern(),
        "user": user,
    }


@router.post("/api/auth/login")
def auth_login(payload: LoginInput, response: Response):
    entered = payload.pin.strip()
    if not entered:
        raise HTTPException(401, "Enter your individual PIN.")

    conn = get_conn()
    u = conn.execute("SELECT * FROM crew_users WHERE pin=? AND active=1;", (entered,)).fetchone()
    s_row = conn.execute("SELECT value FROM settings WHERE key IN ('shop_pin', 'crew_pin') LIMIT 1;").fetchone()
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


@router.post("/api/auth/logout")
def auth_logout(response: Response):
    response.delete_cookie(AUTH_COOKIE)
    return {"authenticated": False}


@router.post("/api/crew")
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


@router.delete("/api/crew/{crew_id}")
def deactivate_crew_member(crew_id: int, request: Request):
    require_owner_or_admin(request)
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


@router.get("/api/crew/pins")
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


@router.post("/api/crew/pins")
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


@router.delete("/api/crew/pins/{uid}")
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


@router.get("/pwa-icon.png")
def serve_dynamic_pwa_icon(size: int = 512, mode: str = "any", theme: str = ""):
    sz = max(32, min(1024, int(size or 512)))
    conn = get_conn()
    active_theme = compute_active_theme(conn)
    conn.close()
    png = _build_dynamic_pwa_icon_png(active_theme, size=sz, maskable=(mode == "maskable"))
    if not png:
        raise HTTPException(404, "Icon generator unavailable")
    return Response(content=png, media_type="image/png", headers={"Cache-Control": "public, max-age=3600"})


@router.get("/apple-touch-icon.png")
def serve_apple_touch_icon():
    conn = get_conn()
    active_theme = compute_active_theme(conn)
    conn.close()
    png = _build_dynamic_pwa_icon_png(active_theme, size=180, maskable=True)
    return Response(content=png, media_type="image/png", headers=NO_CACHE_HEADERS)


@router.get("/merch-go")
def redirect_counter_qr_to_store():
    from fastapi.responses import RedirectResponse
    conn = get_conn()
    row = conn.execute("SELECT value FROM settings WHERE key='merch_popup_url';").fetchone()
    conn.close()
    url = (row["value"].strip() if row and row["value"] else "")
    return RedirectResponse(url if url else "/ops", status_code=302)


@router.get("/merch")
def serve_merch_locker():
    return FileResponse(STATIC_DIR / "merch.html", headers=NO_CACHE_HEADERS)
