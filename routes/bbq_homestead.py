import asyncio
import json
import re
from typing import Optional
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from bbq_db import get_bh_conn
from bbq_pdf import (
    build_bbq_business_cards_pdf,
    build_club_potluck_poster_pdf,
    build_event_drop_flyer_pdf,
    build_homestead_poster_pdf,
    build_pit_prep_sheet_pdf,
)
from services.core_ops import (
    NO_CACHE_HEADERS,
    STATIC_DIR,
    clean_phone,
    get_current_session_user,
    now_eastern,
    send_raw_email_resend_or_smtp,
    today_eastern,
)
from database import get_conn

router = APIRouter()


class BbqMenuUpsert(BaseModel):
    id: Optional[int] = None
    category: str = "SMOKED_MEATS"
    name: str
    description: str = ""
    spice_level: int = 0
    opt1_label: str = "1/2 Pan"
    opt1_price: float = 45.0
    opt2_label: str = ""
    opt2_price: float = 0.0
    image_url: str = ""
    available: bool = True
    sort_order: int = 100


class BbqEventUpsert(BaseModel):
    id: Optional[int] = None
    slug: str
    title: str
    subtitle: str = ""
    event_date: str = ""
    cutoff_note: str = ""
    location: str = ""
    event_mode: str = "BOTH"
    host_pin: str = "1234"
    pit_providing: str = ""
    active: bool = True


class ClubChecklistAdd(BaseModel):
    category: str = "Supplies"
    item_name: str
    qty_needed: int = 1
    host_pin: str = ""


class ClubClaimInput(BaseModel):
    checklist_id: Optional[int] = None
    guest_name: str
    guest_phone: str = ""
    qty: int = 1
    custom_category: str = "Side Dish"
    custom_dish: str = ""
    note: str = ""


class PublicOrderInput(BaseModel):
    source: str = "BBQ"
    event_id: Optional[int] = None
    order_type: str = "PREORDER"
    customer_name: str
    customer_phone: str
    customer_email: str = ""
    pickup_or_event_date: str = ""
    headcount: int = 0
    service_style: str = "PICKUP_PANS"
    items: list[dict] = []
    payment_method: str = "Cash"
    notes: str = ""


class HomesteadItemUpsert(BaseModel):
    id: Optional[int] = None
    category: str = "House BBQ Sauces"
    name: str
    unit_label: str = "16 oz Bottle"
    price: float = 10.0
    stock_qty: int = 8
    description: str = ""
    badge: str = ""
    image_url: str = ""
    active: bool = True
    sort_order: int = 100


def _bh_settings_map(conn) -> dict:
    return {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM bh_settings;").fetchall()}


def _payment_methods_public(s: dict) -> list[dict]:
    out = []
    if s.get("pay_cash", "1") == "1":
        out.append({"code": "Cash", "label": "Cash at Pickup / Delivery", "handle": ""})
    if s.get("pay_venmo", "1") == "1":
        out.append({"code": "Venmo", "label": "Venmo", "handle": s.get("venmo_handle", "")})
    if s.get("pay_cashapp", "1") == "1":
        out.append({"code": "Cash App", "label": "Cash App", "handle": s.get("cashapp_handle", "")})
    if s.get("pay_paypal", "0") == "1":
        out.append({"code": "PayPal", "label": "PayPal", "handle": s.get("paypal_handle", "")})
    if s.get("pay_zelle", "0") == "1":
        out.append({"code": "Zelle", "label": "Zelle", "handle": s.get("zelle_handle", "")})
    return out or [{"code": "Cash", "label": "Cash at Pickup", "handle": ""}]


def _notify_pit_order_bg(order_no: str, source: str, cust: str, ph: str, total: float, summary: str) -> None:
    def _send():
        try:
            dconn = get_conn()
            row = dconn.execute("SELECT value FROM settings WHERE key='alert_email';").fetchone()
            dconn.close()
            to_em = (row["value"] if row else "").strip()
            if to_em and "@" in to_em:
                send_raw_email_resend_or_smtp(
                    to_em,
                    f"[{source} ORDER {order_no}] {cust} — ${total:.2f}",
                    f"New {source} order/request logged:\n\nOrder #: {order_no}\nCustomer: {cust}\nPhone: {ph}\nTotal: ${total:.2f}\nDetails:\n{summary}\n",
                )
        except Exception:
            pass

    try:
        asyncio.get_running_loop().create_task(asyncio.to_thread(_send))
    except RuntimeError:
        pass


# --- HTML PAGE ROUTES (HIDDEN NOINDEX PAGES) ---
@router.get("/bbq")
@router.get("/bbq/{event_slug}")
def serve_bbq_page(event_slug: str = ""):
    return FileResponse(STATIC_DIR / "bbq.html", headers={**NO_CACHE_HEADERS, "X-Robots-Tag": "noindex, nofollow"})


@router.get("/homestead")
def serve_homestead_page():
    f = STATIC_DIR / "homestead.html"
    return FileResponse(f if f.exists() else STATIC_DIR / "bbq.html", headers={**NO_CACHE_HEADERS, "X-Robots-Tag": "noindex, nofollow"})


# --- PUBLIC BBQ & CLUB EVENT ENDPOINTS ---
@router.get("/api/bbq/public/bootstrap")
def bbq_public_bootstrap( request: Request, event: str = ""):
    conn = get_bh_conn()
    s = _bh_settings_map(conn)
    menu = [dict(r) for r in conn.execute("SELECT * FROM bbq_menu_items WHERE available=1 ORDER BY sort_order ASC, id ASC;").fetchall()]
    events = [dict(r) for r in conn.execute("SELECT id, slug, title, subtitle, event_date, cutoff_note, location, event_mode, pit_providing, active FROM bbq_events WHERE active=1 ORDER BY id DESC;").fetchall()]

    sel_ev = None
    slug_q = re.sub(r"[^a-z0-9-]", "", (event or "").strip().lower())
    if slug_q:
        row = conn.execute("SELECT id, slug, title, subtitle, event_date, cutoff_note, location, event_mode, pit_providing, active FROM bbq_events WHERE slug=?;", (slug_q,)).fetchone()
        if row:
            sel_ev = dict(row)
    elif events:
        sel_ev = events[0]

    checklist = []
    if sel_ev:
        for cr in conn.execute("SELECT * FROM bbq_club_checklist WHERE event_id=? ORDER BY is_open_potluck ASC, id ASC;", (sel_ev["id"],)).fetchall():
            d = dict(cr)
            claims = json.loads(d.get("claims_json") or "[]")
            d["claims"] = claims
            d["qty_claimed"] = sum(int(c.get("qty") or 1) for c in claims)
            checklist.append(d)
    conn.close()

    user = get_current_session_user(request)
    return {
        "settings": s,
        "payment_methods": _payment_methods_public(s),
        "menu": menu,
        "events": events,
        "selected_event": sel_ev,
        "club_checklist": checklist,
        "pit_boss_user": user,
    }


@router.post("/api/bbq/public/order")
def submit_bbq_or_homestead_order(payload: PublicOrderInput):
    cname = payload.customer_name.strip()[:80]
    ph = clean_phone(payload.customer_phone)
    if len(cname) < 2 or len(re.sub(r"\D", "", ph)) < 10:
        raise HTTPException(400, "Please enter your name and a 10-digit phone number so we can confirm your order.")

    conn = get_bh_conn()
    ts = now_eastern().strftime("%Y-%m-%d %I:%M %p")
    src = "HOMESTEAD" if payload.source.upper() == "HOMESTEAD" else "BBQ"
    prefix = "HMS" if src == "HOMESTEAD" else "PIT"

    total = 0.0
    clean_items = []
    for it in payload.items:
        qty = max(1, min(99, int(it.get("qty") or 1)))
        price = max(0.0, float(it.get("price") or 0.0))
        if src == "HOMESTEAD" and it.get("id"):
            h_row = conn.execute("SELECT id, name, stock_qty, price, unit_label FROM homestead_items WHERE id=?;", (int(it["id"]),)).fetchone()
            if not h_row:
                conn.close()
                raise HTTPException(404, "Item not found.")
            if int(h_row["stock_qty"]) >= 0 and int(h_row["stock_qty"]) < qty:
                conn.close()
                raise HTTPException(400, f"Only {h_row['stock_qty']} left in stock for {h_row['name']}.")
            price = float(h_row["price"])
            if int(h_row["stock_qty"]) >= 0:
                conn.execute("UPDATE homestead_items SET stock_qty = stock_qty - ? WHERE id=?;", (qty, h_row["id"]))
        total += round(qty * price, 2)
        clean_items.append({
            "id": it.get("id"),
            "name": str(it.get("name") or "")[:80],
            "option": str(it.get("option") or "")[:40],
            "qty": qty,
            "price": price,
        })

    if payload.order_type != "RIG_INQUIRY" and not clean_items:
        conn.close()
        raise HTTPException(400, "Add at least one item to your order first.")

    nxt = conn.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM bbq_orders;").fetchone()[0]
    order_no = f"{prefix}-{nxt:04d}"
    conn.execute(
        """INSERT INTO bbq_orders
           (order_no, created_at, source, event_id, order_type, customer_name, customer_phone, customer_email,
            pickup_or_event_date, headcount, service_style, items_json, total_amount, payment_method, notes, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'NEW');""",
        (
            order_no, ts, src, payload.event_id, payload.order_type, cname, ph, payload.customer_email.strip()[:90],
            payload.pickup_or_event_date.strip()[:30], max(0, int(payload.headcount or 0)),
            payload.service_style.strip()[:30], json.dumps(clean_items), round(total, 2),
            payload.payment_method.strip()[:30], payload.notes.strip()[:350],
        ),
    )
    conn.commit()
    conn.close()

    summary_txt = "\n".join(f"- {x['qty']}x {x['name']} ({x['option']}) @ ${x['price']:.2f}" for x in clean_items) or payload.notes
    _notify_pit_order_bg(order_no, src, cname, ph, total, summary_txt)
    return {"ok": True, "order_no": order_no, "total_amount": round(total, 2)}


@router.post("/api/bbq/public/club/{event_id}/claim")
def claim_or_add_club_potluck(event_id: int, payload: ClubClaimInput):
    gname = payload.guest_name.strip()[:50]
    if len(gname) < 2:
        raise HTTPException(400, "Enter your name so folks know who is bringing it!")
    qty = max(1, min(20, int(payload.qty or 1)))
    conn = get_bh_conn()
    if payload.checklist_id:
        row = conn.execute("SELECT * FROM bbq_club_checklist WHERE id=? AND event_id=?;", (payload.checklist_id, event_id)).fetchone()
        if not row:
            conn.close()
            raise HTTPException(404, "Checklist item not found.")
        claims = json.loads(row["claims_json"] or "[]")
        claims.append({"name": gname, "phone": clean_phone(payload.guest_phone), "qty": qty, "note": payload.note.strip()[:80]})
        conn.execute("UPDATE bbq_club_checklist SET claims_json=? WHERE id=?;", (json.dumps(claims), row["id"]))
    else:
        dish = payload.custom_dish.strip()[:80]
        if len(dish) < 2:
            conn.close()
            raise HTTPException(400, "Enter the dish, dessert, or drinks you're bringing.")
        claims = [{"name": gname, "phone": clean_phone(payload.guest_phone), "qty": 1, "note": payload.note.strip()[:80]}]
        conn.execute(
            "INSERT INTO bbq_club_checklist (event_id, category, item_name, qty_needed, claims_json, is_open_potluck) VALUES (?, ?, ?, 1, ?, 1);",
            (event_id, payload.custom_category.strip()[:24] or "Side Dish", dish, json.dumps(claims)),
        )
    conn.commit()
    conn.close()
    return {"ok": True}


@router.post("/api/bbq/public/club/{event_id}/host-add")
def host_or_crew_add_needed_supply(event_id: int, payload: ClubChecklistAdd, request: Request):
    user = get_current_session_user(request)
    conn = get_bh_conn()
    ev = conn.execute("SELECT * FROM bbq_events WHERE id=?;", (event_id,)).fetchone()
    if not ev:
        conn.close()
        raise HTTPException(404, "Event not found.")
    if not user and payload.host_pin.strip() != (ev["host_pin"] or "").strip():
        conn.close()
        raise HTTPException(403, "Incorrect Event Host Passcode.")
    item_clean = payload.item_name.strip()[:80]
    if len(item_clean) < 2:
        conn.close()
        raise HTTPException(400, "Enter supply or dish needed.")
    conn.execute(
        "INSERT INTO bbq_club_checklist (event_id, category, item_name, qty_needed, claims_json, is_open_potluck) VALUES (?, ?, ?, ?, '[]', 0);",
        (event_id, payload.category.strip()[:24] or "Supplies", item_clean, max(1, int(payload.qty_needed or 1))),
    )
    conn.commit()
    conn.close()
    return {"ok": True}


@router.get("/api/bbq/public/club-poster/{slug}.pdf")
def download_club_potluck_poster(slug: str, request: Request):
    conn = get_bh_conn()
    s = _bh_settings_map(conn)
    ev = conn.execute("SELECT * FROM bbq_events WHERE slug=?;", (slug,)).fetchone()
    if not ev:
        conn.close()
        raise HTTPException(404, "Event not found.")
    chk = [dict(r) for r in conn.execute("SELECT * FROM bbq_club_checklist WHERE event_id=? ORDER BY is_open_potluck ASC, id ASC;", (ev["id"],)).fetchall()]
    conn.close()
    base = str(request.base_url).rstrip("/")
    pdf_b = build_club_potluck_poster_pdf(dict(ev), chk, s, f"{base}/bbq/{ev['slug']}#club")
    return Response(content=pdf_b, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="Club_SignUp_Poster_{slug}.pdf"'})


@router.get("/api/bbq/public/flyer/{slug}.pdf")
def download_event_menu_flyer(slug: str, request: Request):
    conn = get_bh_conn()
    s = _bh_settings_map(conn)
    ev = conn.execute("SELECT * FROM bbq_events WHERE slug=?;", (slug,)).fetchone()
    if not ev:
        conn.close()
        raise HTTPException(404, "Event not found.")
    menu = [dict(r) for r in conn.execute("SELECT * FROM bbq_menu_items WHERE available=1 ORDER BY sort_order ASC, id ASC;").fetchall()]
    conn.close()
    base = str(request.base_url).rstrip("/")
    pdf_b = build_event_drop_flyer_pdf(dict(ev), menu, s, f"{base}/bbq/{ev['slug']}")
    return Response(content=pdf_b, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="BBQ_Menu_Flyer_{slug}.pdf"'})


# --- PUBLIC HOMESTEAD STAND ENDPOINT ---
@router.get("/api/homestead/public/bootstrap")
def homestead_public_bootstrap(request: Request):
    conn = get_bh_conn()
    s = _bh_settings_map(conn)
    items = [dict(r) for r in conn.execute("SELECT * FROM homestead_items WHERE active=1 ORDER BY sort_order ASC, id ASC;").fetchall()]
    conn.close()
    return {
        "settings": s,
        "payment_methods": _payment_methods_public(s),
        "items": items,
        "pit_boss_user": get_current_session_user(request),
    }


# --- PIN-PROTECTED PIT BOSS & HOMESTEAD HQ (KENNY, RYAN & ERIC) ---
@router.get("/api/bbq/admin/state")
def get_pit_boss_hq_state():
    conn = get_bh_conn()
    s = _bh_settings_map(conn)
    menu = [dict(r) for r in conn.execute("SELECT * FROM bbq_menu_items ORDER BY category ASC, sort_order ASC, id ASC;").fetchall()]
    events = [dict(r) for r in conn.execute("SELECT * FROM bbq_events ORDER BY id DESC;").fetchall()]
    orders = [dict(r) for r in conn.execute("SELECT * FROM bbq_orders ORDER BY id DESC LIMIT 150;").fetchall()]
    hs_items = [dict(r) for r in conn.execute("SELECT * FROM homestead_items ORDER BY sort_order ASC, id ASC;").fetchall()]
    conn.close()

    prep_tally: dict[str, int] = {}
    for o in orders:
        if o["status"] in ("CANCELLED", "PAID_PICKED_UP"):
            continue
        for it in json.loads(o.get("items_json") or "[]"):
            lbl = f"{it.get('name')} [{it.get('option') or 'Std'}]"
            prep_tally[lbl] = prep_tally.get(lbl, 0) + int(it.get("qty") or 1)

    return {
        "settings": s,
        "menu": menu,
        "events": events,
        "orders": orders,
        "homestead_items": hs_items,
        "prep_tally": [{"label": k, "qty": v} for k, v in sorted(prep_tally.items())],
    }


@router.post("/api/bbq/admin/settings")
def save_bh_settings(payload: dict):
    conn = get_bh_conn()
    for k, v in payload.items():
        if isinstance(k, str) and len(k) < 50:
            conn.execute("INSERT OR REPLACE INTO bh_settings (key, value) VALUES (?, ?);", (k, str(v)))
    conn.commit()
    conn.close()
    return {"saved": True}


@router.post("/api/bbq/admin/menu")
def upsert_bbq_menu_item(payload: BbqMenuUpsert):
    conn = get_bh_conn()
    avail = 1 if payload.available else 0
    if payload.id:
        conn.execute(
            """UPDATE bbq_menu_items SET category=?, name=?, description=?, spice_level=?,
               opt1_label=?, opt1_price=?, opt2_label=?, opt2_price=?, image_url=?, available=?, sort_order=? WHERE id=?;""",
            (payload.category, payload.name.strip(), payload.description.strip(), payload.spice_level,
             payload.opt1_label.strip(), payload.opt1_price, payload.opt2_label.strip(), payload.opt2_price,
             payload.image_url.strip(), avail, payload.sort_order, payload.id),
        )
    else:
        conn.execute(
            """INSERT INTO bbq_menu_items (category, name, description, spice_level, opt1_label, opt1_price, opt2_label, opt2_price, image_url, available, sort_order)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);""",
            (payload.category, payload.name.strip(), payload.description.strip(), payload.spice_level,
             payload.opt1_label.strip(), payload.opt1_price, payload.opt2_label.strip(), payload.opt2_price,
             payload.image_url.strip(), avail, payload.sort_order),
        )
    conn.commit()
    conn.close()
    return {"saved": True}


@router.delete("/api/bbq/admin/menu/{item_id}")
def delete_bbq_menu_item(item_id: int):
    conn = get_bh_conn()
    conn.execute("DELETE FROM bbq_menu_items WHERE id=?;", (item_id,))
    conn.commit()
    conn.close()
    return {"deleted": item_id}


@router.post("/api/bbq/admin/events")
def upsert_bbq_event(payload: BbqEventUpsert):
    slug = re.sub(r"[^a-z0-9-]", "-", payload.slug.strip().lower()).strip("-") or f"smoke-{today_eastern()}"
    conn = get_bh_conn()
    act = 1 if payload.active else 0
    if payload.id:
        conn.execute(
            """UPDATE bbq_events SET slug=?, title=?, subtitle=?, event_date=?, cutoff_note=?, location=?,
               event_mode=?, host_pin=?, pit_providing=?, active=? WHERE id=?;""",
            (slug, payload.title.strip(), payload.subtitle.strip(), payload.event_date.strip(), payload.cutoff_note.strip(),
             payload.location.strip(), payload.event_mode, payload.host_pin.strip()[:10] or "1234", payload.pit_providing.strip(), act, payload.id),
        )
    else:
        conn.execute(
            """INSERT INTO bbq_events (slug, title, subtitle, event_date, cutoff_note, location, event_mode, host_pin, pit_providing, active, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);""",
            (slug, payload.title.strip(), payload.subtitle.strip(), payload.event_date.strip(), payload.cutoff_note.strip(),
             payload.location.strip(), payload.event_mode, payload.host_pin.strip()[:10] or "1234", payload.pit_providing.strip(), act, today_eastern()),
        )
    conn.commit()
    conn.close()
    return {"saved": True, "slug": slug}


@router.delete("/api/bbq/admin/events/{ev_id}")
def delete_bbq_event(ev_id: int):
    conn = get_bh_conn()
    conn.execute("DELETE FROM bbq_club_checklist WHERE event_id=?;", (ev_id,))
    conn.execute("DELETE FROM bbq_events WHERE id=?;", (ev_id,))
    conn.commit()
    conn.close()
    return {"deleted": ev_id}


@router.post("/api/bbq/admin/orders/{oid}/status")
def update_bbq_order_status(oid: int, payload: dict):
    conn = get_bh_conn()
    conn.execute("UPDATE bbq_orders SET status=? WHERE id=?;", (str(payload.get("status") or "CONFIRMED"), oid))
    conn.commit()
    conn.close()
    return {"updated": oid}


@router.delete("/api/bbq/admin/orders/{oid}")
def delete_bbq_order(oid: int):
    conn = get_bh_conn()
    conn.execute("DELETE FROM bbq_orders WHERE id=?;", (oid,))
    conn.commit()
    conn.close()
    return {"deleted": oid}


@router.post("/api/bbq/admin/homestead")
def upsert_homestead_item(payload: HomesteadItemUpsert):
    conn = get_bh_conn()
    act = 1 if payload.active else 0
    if payload.id:
        conn.execute(
            """UPDATE homestead_items SET category=?, name=?, unit_label=?, price=?, stock_qty=?,
               description=?, badge=?, image_url=?, active=?, sort_order=? WHERE id=?;""",
            (payload.category.strip(), payload.name.strip(), payload.unit_label.strip(), payload.price,
             payload.stock_qty, payload.description.strip(), payload.badge.strip(), payload.image_url.strip(), act, payload.sort_order, payload.id),
        )
    else:
        conn.execute(
            """INSERT INTO homestead_items (category, name, unit_label, price, stock_qty, description, badge, image_url, active, sort_order)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);""",
            (payload.category.strip(), payload.name.strip(), payload.unit_label.strip(), payload.price,
             payload.stock_qty, payload.description.strip(), payload.badge.strip(), payload.image_url.strip(), act, payload.sort_order),
        )
    conn.commit()
    conn.close()
    return {"saved": True}


@router.post("/api/bbq/admin/homestead/{hid}/stock")
def step_homestead_stock(hid: int, payload: dict):
    conn = get_bh_conn()
    if "stock_qty" in payload:
        conn.execute("UPDATE homestead_items SET stock_qty=? WHERE id=?;", (max(-1, int(payload["stock_qty"])), hid))
    elif "delta" in payload:
        conn.execute("UPDATE homestead_items SET stock_qty = MAX(0, stock_qty + ?) WHERE id=?;", (int(payload["delta"]), hid))
    conn.commit()
    row = dict(conn.execute("SELECT * FROM homestead_items WHERE id=?;", (hid,)).fetchone())
    conn.close()
    return row


@router.delete("/api/bbq/admin/homestead/{hid}")
def delete_homestead_item(hid: int):
    conn = get_bh_conn()
    conn.execute("DELETE FROM homestead_items WHERE id=?;", (hid,))
    conn.commit()
    conn.close()
    return {"deleted": hid}


@router.get("/api/bbq/admin/pdf/business-cards")
def pdf_bbq_business_cards(request: Request):
    conn = get_bh_conn()
    s = _bh_settings_map(conn)
    conn.close()
    base = str(request.base_url).rstrip("/")
    pdf_b = build_bbq_business_cards_pdf(s, f"{base}/bbq", f"{base}/homestead")
    return Response(content=pdf_b, media_type="application/pdf", headers={"Content-Disposition": 'inline; filename="BBQ_Pit_Business_Cards_10up.pdf"'})


@router.get("/api/bbq/admin/pdf/prep-sheet")
def pdf_pit_prep_sheet(source: str = "BBQ"):
    conn = get_bh_conn()
    rows = [dict(r) for r in conn.execute("SELECT * FROM bbq_orders WHERE source=? AND status!='PAID_PICKED_UP' ORDER BY id ASC;", (source.upper(),)).fetchall()]
    conn.close()
    pdf_b = build_pit_prep_sheet_pdf(f"{source.upper()} Active Orders", rows)
    return Response(content=pdf_b, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="Pit_Prep_Sheet_{source}.pdf"'})


@router.get("/api/bbq/public/homestead-poster.pdf")
def download_homestead_stand_poster(request: Request):
    conn = get_bh_conn()
    s = _bh_settings_map(conn)
    items = [dict(r) for r in conn.execute("SELECT * FROM homestead_items WHERE active=1 ORDER BY sort_order ASC, id ASC;").fetchall()]
    conn.close()
    base = str(request.base_url).rstrip("/")
    pdf_b = build_homestead_poster_pdf(s, items, f"{base}/homestead")
    return Response(content=pdf_b, media_type="application/pdf", headers={"Content-Disposition": 'inline; filename="Homestead_Stand_QR_Sign.pdf"'})
