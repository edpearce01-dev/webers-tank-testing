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
from services.core_ops import STATIC_DIR, today_eastern


CACHED_VISION_MODEL: Optional[str] = None


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


SHOP_HELP_SYSTEM_GUIDE = """You are the built-in Shop Operations & Troubleshooting AI Assistant for Weber's Tank Testing (U.S. D.O.T. RIN B386) at 3086 Legionville Rd, Baden, PA 15005 (Phone: 724-266-8008).
You assist Facility Manager Ryan Pearce (RP), Kenny, and the shop crew. Keep answers straight to the point, practical, friendly, and brief (bullet points or 2-4 short steps).

EXACT SYSTEM MAP & RULES:
1. PUBLIC SITE (weberstanktesting.com):
   - Has 3 buttons under #dropoff: '📥 BACKROOM DROP-OFF' (captures Name, Contact, Phone, Email, Billing/Mailing Address, Bottle Count & Types), '✅ BACKROOM PICK-UP', and '🚚 TRUCK PICKUP & DELIVERY'.
   - Issues a digital BR-XXXX ticket card + downloadable PDF receipt + live 5-step tracker. Every address & email auto-saves to the Customer Rolodex for billing, newsletters, and 5-year re-cert notices.
   - TESTING FACILITY ONLY: Weber's NEVER refills cylinders. Passing tanks return clean, thoroughly dried, revalved, and depressurized.
2. SHOP OPS (/ops — 4 Stations on Left Menu):
   - '1. Start & Cal': Shows active 12-row Form 393-001 Retest Sheet # (assigns ONLY when a hydro test completes, never when queued). Calibrates Short Jacket (usually 3000 PSI -> covers test PSI up to 3500#) and Tall Jacket (Method B +-1.0% with Master Cyl 1SF11904). Official DOT PDFs always sign off under Ryan Pearce (RP).
   - '2. Prep & Scan': Import waiting 24/7 Backroom Door batches or Scheduled jobs via 'Lock & Scan Batch'. Turn ON 'Lock Batch Specs' for multi-bottle batches so specs stay locked while Serial # and random Valve Tag # reset blank after each bottle.
   - RANDOM VALVE TAGS: Tags (#47, #12, etc.) are grabbed randomly from the bin. If blocked by 'Valve Tag #X is currently active on SN...', go to '4. Finish & Shelf' -> 'Active Valve & Regulator Tags' and click 'Reinstall & Free #X' (or pick another tag).
   - '3. Hydro Stand': Tap any queued cylinder pill at top -> enter Total Expansion (cc) & Permanent Expansion (cc). If >10% perm, >REE, or visual fail, it flips to Code D Condemned and asks for PHMSA legal disposition.
   - PHYSICAL SHOP LAYOUT: Customers use the right-side Loading Dock / Ramp -> 24/7 Back Room (Drop-Off & Pickup). Inside the Test Room are the Vice, Stamp & Valve Bench, 2 Water Test Jackets (Short & Tall), and Pump/Test Desk. Outside in the Fire Truck Bay is the Drying Rack.
   - '4. Finish & Shelf': Passing tanks dry on the Main Bay Drying Rack and CANNOT move to Back Room Pickup until marked 'Stamped' or 'Stickered' (B386) at the Stamp & Valve Bench. Tap '+ Neck O-Ring', '+ Burst Disc', or '+ Tumble/Clean' here so parts auto-add to the invoice. Click 'Claim Slip' to print the Zone E slip (QR code auto-opens PICK-UP sign-out).
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


def _easter_sunday(year: int) -> date:
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    nxt = date(year, month + 1, 1) if month < 12 else date(year + 1, 1, 1)
    last = nxt - timedelta(days=1)
    offset = (last.weekday() - weekday) % 7
    return last - timedelta(days=offset)


HOLIDAY_CATALOG = {
    "NEW_YEAR": {
        "name": "New Year's",
        "icon": "🎆",
        "effect": "fireworks",
        "garland": "gold_lights",
        "dog_image": "dogs-newyear.jpg",
        "accent": "#d97706",
        "dog_msg": "🐾 Happy New Year from Pongo, Zeke & Pierce! Starting the year fresh — drop off 24/7 at our 24/7 Loading Dock!",
    },
    "VALENTINES": {
        "name": "Valentine's Day",
        "icon": "❤️",
        "effect": "hearts",
        "garland": "warm_pink_lights",
        "dog_image": "dogs-valentines.jpg",
        "accent": "#e11d48",
        "dog_msg": "🐾 Sending puppy love from Pongo, Zeke & Pierce! Show your cylinders some love — 24/7 Loading Dock drop-off is always open!",
    },
    "ST_PATRICKS": {
        "name": "St. Patrick's Day",
        "icon": "🍀",
        "effect": "shamrocks",
        "garland": "emerald_lights",
        "dog_image": "dogs-stpatricks.jpg",
        "accent": "#16a34a",
        "dog_msg": "🐾 Don't rely on luck with high-pressure tanks! Pongo, Zeke & Pierce have our 24/7 Baden Loading Dock ready for your drop-offs!",
    },
    "EASTER": {
        "name": "Easter",
        "icon": "🐰",
        "effect": "spring",
        "garland": "pastel_lights",
        "dog_image": "dogs-easter.jpg",
        "accent": "#8b5cf6",
        "dog_msg": "🐾 Happy Spring & Easter from Pongo, Zeke & Pierce! Getting scuba, paintball, or fire bottles ready for spring? Drop off 24/7!",
    },
    "MEMORIAL_DAY": {
        "name": "Memorial Day",
        "icon": "🇺🇸",
        "effect": "patriotic",
        "garland": "patriotic_lights",
        "dog_image": "dogs-patriotic.jpg",
        "accent": "#dc2626",
        "dog_msg": "🐾 Remembering & honoring those who served. From Pongo, Zeke, Pierce & the Weber's crew — 24/7 Loading Dock drop-off stays open all weekend!",
    },
    "JULY_4TH": {
        "name": "4th of July",
        "icon": "🎇",
        "effect": "patriotic",
        "garland": "patriotic_lights",
        "dog_image": "dogs-july4.jpg",
        "accent": "#2563eb",
        "dog_msg": "🐾 Happy 4th of July from Pongo, Zeke & Pierce! Keep the fireworks in the sky, not in expired tanks — drop off 24/7 at our Loading Dock!",
    },
    "LABOR_DAY": {
        "name": "Labor Day",
        "icon": "🛠️",
        "effect": "patriotic",
        "garland": "patriotic_lights",
        "dog_image": "dogs-laborday.jpg",
        "accent": "#0284c7",
        "dog_msg": "🐾 Happy Labor Day to our hard-working Beaver County crews & first responders! Pongo, Zeke & Pierce are holding down our 24/7 Loading Dock!",
    },
    "HALLOWEEN": {
        "name": "Halloween",
        "icon": "🎃",
        "effect": "halloween",
        "garland": "spooky_lights",
        "dog_image": "dogs-halloween.jpg",
        "accent": "#ea580c",
        "dog_msg": "🐾 No tricks, just certified treats (especially for us)! Pongo, Zeke & Pierce welcome your 24/7 Loading Dock drop-offs!",
    },
    "VETERANS_DAY": {
        "name": "Veterans Day",
        "icon": "🎖️",
        "effect": "patriotic",
        "garland": "patriotic_lights",
        "dog_image": "dogs-veterans.jpg",
        "accent": "#b91c1c",
        "dog_msg": "🐾 Thank you to all Veterans from Pongo, Zeke, Pierce & the Weber's family! Proudly serving our region's departments & fleets.",
    },
    "THANKSGIVING": {
        "name": "Thanksgiving",
        "icon": "🦃",
        "effect": "autumn_leaves",
        "garland": "autumn_lights",
        "dog_image": "dogs-thanksgiving.jpg",
        "accent": "#d97706",
        "dog_msg": "🐾 Grateful for all our customers & local departments! Pongo, Zeke & Pierce say save us some turkey — 24/7 Loading Dock drop-off is open all weekend!",
    },
    "CHRISTMAS": {
        "name": "Christmas",
        "icon": "🎄",
        "effect": "snow",
        "garland": "christmas_lights",
        "dog_image": "dogs-christmas.jpg",
        "accent": "#dc2626",
        "dog_msg": "🐾 Merry Christmas & Happy Holidays from Pongo, Zeke, and Pierce! Drop off 24/7 at our 24/7 Loading Dock — we'll get your tanks ready in a jiffy!",
    },
}


def _holiday_dates_for_year(y: int) -> list[tuple[str, date, int]]:
    return [
        ("CHRISTMAS", date(y, 12, 25), 1),
        ("THANKSGIVING", _nth_weekday(y, 11, 3, 4), 2),
        ("JULY_4TH", date(y, 7, 4), 3),
        ("HALLOWEEN", date(y, 10, 31), 4),
        ("NEW_YEAR", date(y, 1, 1), 5),
        ("MEMORIAL_DAY", _last_weekday(y, 5, 0), 6),
        ("VETERANS_DAY", date(y, 11, 11), 7),
        ("EASTER", _easter_sunday(y), 8),
        ("ST_PATRICKS", date(y, 3, 17), 9),
        ("LABOR_DAY", _nth_weekday(y, 9, 0, 1), 10),
        ("VALENTINES", date(y, 2, 14), 11),
    ]


def compute_active_theme(conn) -> dict:
    s_map = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings;").fetchall()}
    mode = (s_map.get("theme_mode") or "AUTO").strip().upper()
    en_pub = s_map.get("theme_enable_public", "1") != "0"
    en_tv = s_map.get("theme_enable_tv", "1") != "0"
    en_ops = s_map.get("theme_enable_ops", "1") != "0"
    custom_msg = (s_map.get("theme_custom_dog_msg") or "").strip()

    today_d = date.fromisoformat(today_eastern())
    chosen_key = None
    chosen_date = None

    if mode == "OFF":
        chosen_key = None
    elif mode in HOLIDAY_CATALOG:
        chosen_key = mode
        all_d = _holiday_dates_for_year(today_d.year) + _holiday_dates_for_year(today_d.year + 1)
        matches = [hd for k, hd, _ in all_d if k == chosen_key and (hd - today_d).days >= -2]
        chosen_date = matches[0] if matches else today_d
    else:
        candidates = []
        for y in (today_d.year - 1, today_d.year, today_d.year + 1):
            for key, hdate, prio in _holiday_dates_for_year(y):
                diff = (hdate - today_d).days
                if -2 <= diff <= 15:
                    if key == "VETERANS_DAY" and diff < 0:
                        continue
                    if key == "NEW_YEAR" and today_d.month == 12 and today_d.day <= 27:
                        continue
                    candidates.append((prio, abs(diff), key, hdate))
        if candidates:
            candidates.sort(key=lambda x: (x[0], x[1]))
            _, _, chosen_key, chosen_date = candidates[0]

    default_dog_msg = "🐾 Welcome to Weber's Tank Testing from Pongo, Zeke & Pierce! Pull up to our Loading Dock for 24/7 Loading Dock drop-off & pickup anytime!"
    if not chosen_key:
        return {
            "active": False,
            "mode": mode,
            "key": "NONE",
            "name": "Classic Year-Round",
            "icon": "🐾",
            "days_until": None,
            "countdown_label": "",
            "effect": "none",
            "garland": "none",
            "accent": "#f59e0b",
            "dog_image": "dogs-classic.png",
            "dog_message": custom_msg or default_dog_msg,
            "enable_public": en_pub,
            "enable_tv": en_tv,
            "enable_ops": en_ops,
            "custom_dog_message": custom_msg,
            "available_holidays": [{"key": k, "name": v["name"], "icon": v["icon"]} for k, v in HOLIDAY_CATALOG.items()],
        }

    meta = HOLIDAY_CATALOG[chosen_key]
    diff_days = (chosen_date - today_d).days if chosen_date else 0
    if diff_days > 1:
        cd_lbl = f"{meta['icon']} {diff_days} DAYS TILL {meta['name'].upper()}!"
    elif diff_days == 1:
        cd_lbl = f"{meta['icon']} TOMORROW IS {meta['name'].upper()}!"
    elif diff_days == 0:
        cd_lbl = f"{meta['icon']} HAPPY {meta['name'].upper()}!"
    else:
        cd_lbl = f"{meta['icon']} HAPPY {meta['name'].upper()} WEEKEND!"

    return {
        "active": True,
        "mode": mode,
        "key": chosen_key,
        "name": meta["name"],
        "icon": meta["icon"],
        "days_until": diff_days,
        "countdown_label": cd_lbl,
        "effect": meta["effect"],
        "garland": meta["garland"],
        "accent": meta["accent"],
        "dog_image": (meta["dog_image"].rsplit(".", 1)[0] + ".png") if (STATIC_DIR / "images" / (meta["dog_image"].rsplit(".", 1)[0] + ".png")).exists() else meta["dog_image"],
        "dog_message": custom_msg or meta["dog_msg"],
        "enable_public": en_pub,
        "enable_tv": en_tv,
        "enable_ops": en_ops,
        "custom_dog_message": custom_msg,
        "available_holidays": [{"key": k, "name": v["name"], "icon": v["icon"]} for k, v in HOLIDAY_CATALOG.items()],
    }


_PWA_ICON_CACHE: dict[tuple, bytes] = {}


def _build_dynamic_pwa_icon_png(theme: dict, size: int = 512, maskable: bool = False) -> bytes:
    """Composites Warm Cream + Active Holiday Wreath + Original Circular Crest + Shop Dogs Out in Front."""
    t_key = theme.get("key", "NONE") if (theme.get("active") and (theme.get("enable_public") or theme.get("enable_ops"))) else "NONE"
    dog_file = theme.get("dog_image", "dogs-classic.png") if t_key != "NONE" else "dogs-classic.png"
    cache_key = ("v12_1", t_key, dog_file, size, maskable)
    if cache_key in _PWA_ICON_CACHE:
        return _PWA_ICON_CACHE[cache_key]

    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return b""

    S = 512
    canvas_im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas_im)

    if maskable:
        draw.rectangle((0, 0, S, S), fill=(245, 233, 206, 255))
        draw.ellipse((20, 16, S - 20, S - 16), fill=(253, 248, 238, 255))
        crest_d, crest_cy = 276, 204
        dogs_max_w, dogs_max_h, dogs_bottom_y = 374, 202, 456
    else:
        draw.rounded_rectangle((6, 6, S - 7, S - 7), radius=104, fill=(12, 38, 27, 255))
        draw.rounded_rectangle((16, 16, S - 17, S - 17), radius=94, fill=(217, 155, 38, 255))
        draw.rounded_rectangle((24, 24, S - 25, S - 25), radius=86, fill=(253, 248, 238, 255))
        crest_d, crest_cy = 316, 202
        dogs_max_w, dogs_max_h, dogs_bottom_y = 440, 236, 488

    img_dir = STATIC_DIR / "images"

    if t_key != "NONE":
        arch_map = {"THANKSGIVING": "arch-thanksgiving.png", "HALLOWEEN": "arch-thanksgiving.png", "CHRISTMAS": "arch-christmas.png", "NEW_YEAR": "arch-newyear.png"}
        arch_p = img_dir / arch_map.get(t_key, "arch-thanksgiving.png")
        if arch_p.exists():
            with Image.open(arch_p) as a_im:
                arch_d = int(crest_d * 1.34)
                a_rgba = a_im.convert("RGBA").resize((arch_d, arch_d), Image.Resampling.LANCZOS)
                canvas_im.alpha_composite(a_rgba, ((S - arch_d) // 2, crest_cy - arch_d // 2))

    crest_p = img_dir / "logo-crest.png"
    if crest_p.exists():
        with Image.open(crest_p) as c_im:
            c_rgba = c_im.convert("RGBA").resize((crest_d, crest_d), Image.Resampling.LANCZOS)
            canvas_im.alpha_composite(c_rgba, ((S - crest_d) // 2, crest_cy - crest_d // 2))

    stem = dog_file.rsplit(".", 1)[0]
    dog_candidates = [img_dir / f"{stem}.png", img_dir / dog_file, img_dir / "dogs-classic.png", img_dir / "dogs-thanksgiving.png"]
    chosen_dog = next((p for p in dog_candidates if p.exists()), None)
    if chosen_dog:
        with Image.open(chosen_dog) as d_im:
            d_rgba = d_im.convert("RGBA")
            d_rgba.thumbnail((dogs_max_w, dogs_max_h), Image.Resampling.LANCZOS)
            canvas_im.alpha_composite(d_rgba, ((S - d_rgba.width) // 2, dogs_bottom_y - d_rgba.height))

    if size != S:
        canvas_im = canvas_im.resize((size, size), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    canvas_im.save(out, format="PNG", optimize=True)
    png_bytes = out.getvalue()
    if len(_PWA_ICON_CACHE) > 32:
        _PWA_ICON_CACHE.clear()
    _PWA_ICON_CACHE[cache_key] = png_bytes
    return png_bytes


def clear_vision_cache() -> None:
    global CACHED_VISION_MODEL
    CACHED_VISION_MODEL = None

__all__ = ['CACHED_VISION_MODEL', 'discover_vision_models', 'call_gemini_vision_rest', 'SHOP_HELP_SYSTEM_GUIDE', 'call_gemini_help_assistant', '_easter_sunday', '_nth_weekday', '_last_weekday', 'HOLIDAY_CATALOG', '_holiday_dates_for_year', 'compute_active_theme', '_PWA_ICON_CACHE', '_build_dynamic_pwa_icon_png', 'clear_vision_cache']
