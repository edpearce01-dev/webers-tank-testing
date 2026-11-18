import io
import json
from reportlab.graphics import renderPDF
from reportlab.graphics.barcode import qr
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas


def _draw_qr(c: canvas.Canvas, url: str, x: float, y: float, size: float) -> None:
    w = qr.QrCodeWidget(url)
    b = w.getBounds()
    d = Drawing(size, size, transform=[size / (b[2] - b[0]), 0, 0, size / (b[3] - b[1]), 0, 0])
    d.add(w)
    renderPDF.draw(d, c, x, y)


def build_club_potluck_poster_pdf(event: dict, checklist: list[dict], settings: dict, event_url: str) -> bytes:
    """8.5x11 Printable Club / Hall Wall Sign with QR Code to Claim Needed Supplies or Sign Up a Dish."""
    buf = io.BytesIO()
    W, H = letter
    c = canvas.Canvas(buf, pagesize=letter)
    brand = f"{settings.get('brand_top', 'BACKROAD')} {settings.get('brand_bottom', 'PIT & SMOKE')}".strip()
    c.setTitle(f"{event.get('title', 'Club Event')} - Bring a Dish & Supply Sign-Up")

    # Dark Smokehouse Header Banner
    c.setFillColor(colors.HexColor("#110d0b"))
    c.rect(0, H - 122, W, 122, fill=1, stroke=0)
    c.setFillColor(colors.HexColor("#ea580c"))
    c.rect(0, H - 128, W, 6, fill=1, stroke=0)

    c.setFillColor(colors.HexColor("#fbbf24"))
    c.setFont("Helvetica-Bold", 10.5)
    c.drawCentredString(W / 2, H - 34, f"WOOD-FIRED CATERING BY {brand.upper()}  •  EVENT SIGN-UP SHEET")
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 23)
    c.drawCentredString(W / 2, H - 64, (event.get("title") or "CLUB SMOKE & POTLUCK")[:42].upper())
    c.setFillColor(colors.HexColor("#e5e7eb"))
    c.setFont("Helvetica-Bold", 11)
    meta_line = "  |  ".join(filter(None, [f"Date: {event.get('event_date')}" if event.get("event_date") else "", event.get("location") or ""]))
    c.drawCentredString(W / 2, H - 88, meta_line[:75])
    if event.get("pit_providing"):
        c.setFillColor(colors.HexColor("#fdba74"))
        c.setFont("Helvetica-Bold", 9.6)
        c.drawCentredString(W / 2, H - 108, f"OFF THE SMOKER: {event['pit_providing'][:78]}")

    # Left: QR Box | Right: How to Claim
    qy = H - 302
    c.setStrokeColor(colors.HexColor("#ea580c"))
    c.setLineWidth(2)
    c.roundRect(40, qy, 155, 155, 10, fill=0, stroke=1)
    _draw_qr(c, event_url, 50, qy + 10, 135)

    c.setFillColor(colors.HexColor("#111827"))
    c.setFont("Helvetica-Bold", 15.5)
    c.drawString(212, qy + 130, "BRINGING A DISH, DESSERT, DRINKS OR SUPPLIES?")
    c.setFont("Helvetica", 10.5)
    c.setFillColor(colors.HexColor("#374151"))
    c.drawString(212, qy + 108, "1. Scan the QR code with your phone camera (no app or login needed).")
    c.drawString(212, qy + 90, "2. Tap 'Claim' next to any supply needed below so nobody doubles up.")
    c.drawString(212, qy + 72, "3. Bringing a favorite side, dessert, or cooler of drinks? Post it live!")
    c.setFillColor(colors.HexColor("#9a3412"))
    c.setFont("Helvetica-Bold", 9.8)
    c.drawString(212, qy + 46, f"Link: {event_url[:62]}")
    c.setFillColor(colors.HexColor("#4b5563"))
    c.setFont("Helvetica", 9.4)
    c.drawString(212, qy + 26, f"Questions or catering orders? Call/Text: {settings.get('contact_phone', '724-266-8008')}")

    # Checklist Table
    ty = qy - 18
    c.setFillColor(colors.HexColor("#1f2937"))
    c.rect(40, ty - 22, W - 80, 22, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 9.2)
    c.drawString(48, ty - 15, "CATEGORY")
    c.drawString(138, ty - 15, "ITEM / DISH NEEDED")
    c.drawCentredString(385, ty - 15, "NEEDED")
    c.drawString(430, ty - 15, "CLAIMED BY / WRITE-IN NAME")

    cy = ty - 22
    c.setStrokeColor(colors.HexColor("#cbd5e1"))
    c.setLineWidth(0.7)
    for item in checklist[:13]:
        cy -= 22
        c.rect(40, cy, W - 80, 22, fill=0, stroke=1)
        claims = json.loads(item.get("claims_json") or "[]")
        claimed_qty = sum(int(x.get("qty") or 1) for x in claims)
        names = ", ".join(f"{x.get('name')} ({x.get('qty',1)})" for x in claims)
        c.setFillColor(colors.HexColor("#9a3412"))
        c.setFont("Helvetica-Bold", 8.5)
        c.drawString(48, cy + 7, str(item.get("category") or "Item")[:14].upper())
        c.setFillColor(colors.black)
        c.setFont("Helvetica-Bold", 9.5)
        c.drawString(138, cy + 7, str(item.get("item_name") or "")[:36])
        c.setFont("Helvetica", 9.2)
        c.drawCentredString(385, cy + 7, "Potluck" if item.get("is_open_potluck") else f"{claimed_qty} / {item.get('qty_needed', 1)}")
        c.drawString(430, cy + 7, names[:26] if names else "_______________________")

    # Extra blank pen write-in lines for club bulletin board
    if cy > 110:
        c.setFillColor(colors.HexColor("#475569"))
        c.setFont("Helvetica-Bold", 8.8)
        c.drawString(40, cy - 16, "EXTRA WRITE-IN DISHES / DESSERTS (OR SCAN QR ABOVE SO EVERYONE SEES IT):")
        wy = cy - 22
        for _ in range(min(4, int((wy - 48) // 22))):
            wy -= 22
            c.rect(40, wy, W - 80, 22, fill=0, stroke=1)
            c.setFont("Helvetica", 8.5)
            c.drawString(48, wy + 7, "Dish / Item: ____________________________________   Brought By: ________________________")

    c.setFillColor(colors.HexColor("#64748b"))
    c.setFont("Helvetica-Bold", 8.5)
    c.drawCentredString(W / 2, 24, f"{brand}  •  Hickory & Fruitwood Smoked Catering  •  {settings.get('contact_phone', '724-266-8008')}")
    c.save()
    return buf.getvalue()


def build_event_drop_flyer_pdf(event: dict, menu_items: list[dict], settings: dict, event_url: str) -> bytes:
    """8.5x11 Printable Pre-Order Menu Flyer with QR Code."""
    buf = io.BytesIO()
    W, H = letter
    c = canvas.Canvas(buf, pagesize=letter)
    brand = f"{settings.get('brand_top', 'BACKROAD')} {settings.get('brand_bottom', 'PIT & SMOKE')}".strip()
    c.setFillColor(colors.HexColor("#110d0b"))
    c.rect(0, H - 118, W, 118, fill=1, stroke=0)
    c.setFillColor(colors.HexColor("#ea580c"))
    c.rect(0, H - 124, W, 6, fill=1, stroke=0)

    c.setFillColor(colors.HexColor("#fbbf24"))
    c.setFont("Helvetica-Bold", 11)
    c.drawCentredString(W / 2, H - 36, brand.upper())
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 22)
    c.drawCentredString(W / 2, H - 64, (event.get("title") or "SMOKED BBQ PRE-ORDER MENU")[:40].upper())
    c.setFont("Helvetica", 10.5)
    c.setFillColor(colors.HexColor("#fde68a"))
    c.drawCentredString(W / 2, H - 88, f"Pickup / Event Date: {event.get('event_date') or 'See Online'}   •   {event.get('cutoff_note') or 'Pre-Order Before Sold Out'}")

    cy = H - 150
    for it in menu_items[:14]:
        if cy < 195:
            break
        c.setFillColor(colors.HexColor("#111827"))
        c.setFont("Helvetica-Bold", 10.5)
        c.drawString(45, cy, str(it["name"]).upper()[:44])
        p_str = f"{it['opt1_label']}: ${float(it['opt1_price']):.2f}"
        if float(it.get("opt2_price") or 0) > 0:
            p_str += f"  |  {it['opt2_label']}: ${float(it['opt2_price']):.2f}"
        c.setFillColor(colors.HexColor("#c2410c"))
        c.drawRightString(W - 45, cy, p_str)
        c.setFillColor(colors.HexColor("#4b5563"))
        c.setFont("Helvetica", 8.6)
        c.drawString(45, cy - 12, str(it.get("description") or "")[:95])
        c.setStrokeColor(colors.HexColor("#e5e7eb"))
        c.line(45, cy - 18, W - 45, cy - 18)
        cy -= 31

    c.setFillColor(colors.HexColor("#fff7ed"))
    c.setStrokeColor(colors.HexColor("#ea580c"))
    c.roundRect(45, 42, W - 90, 128, 10, fill=1, stroke=1)
    _draw_qr(c, event_url, 58, 53, 106)
    c.setFillColor(colors.HexColor("#111827"))
    c.setFont("Helvetica-Bold", 14)
    c.drawString(180, 142, "SCAN TO LOCK IN YOUR PANS & MEATS")
    c.setFont("Helvetica", 10)
    c.drawString(180, 122, "Pay Cash, Venmo, or Cash App — zero online card fees.")
    c.drawString(180, 104, f"Order Link: {event_url[:56]}")
    c.setFont("Helvetica-Bold", 10.5)
    c.setFillColor(colors.HexColor("#9a3412"))
    c.drawString(180, 80, f"Questions or Custom Orders? Call/Text {settings.get('contact_phone', '724-266-8008')}")
    c.setFillColor(colors.HexColor("#374151"))
    c.setFont("Helvetica", 9)
    c.drawString(180, 60, "Also check out our Homestead Stand for House Sauces, Honey, Maple Syrup & Eggs!")
    c.save()
    return buf.getvalue()


def build_bbq_business_cards_pdf(settings: dict, bbq_url: str, homestead_url: str) -> bytes:
    """10-Up Printable Business Cards (Standard 3.5 x 2 inch Avery 5371 Grid)."""
    buf = io.BytesIO()
    W, H = letter
    c = canvas.Canvas(buf, pagesize=letter)
    brand_t = settings.get("brand_top", "BACKROAD")
    brand_b = settings.get("brand_bottom", "PIT & SMOKE")
    phone = settings.get("contact_phone", "724-266-8008")

    cw, ch = 3.5 * 72, 2.0 * 72
    mx, my = 0.75 * 72, 0.5 * 72
    for row in range(5):
        for col in range(2):
            x = mx + col * cw
            y = H - my - (row + 1) * ch
            c.setFillColor(colors.HexColor("#110d0b"))
            c.rect(x + 2, y + 2, cw - 4, ch - 4, fill=1, stroke=0)
            c.setStrokeColor(colors.HexColor("#ea580c"))
            c.setLineWidth(1.2)
            c.rect(x + 7, y + 7, cw - 14, ch - 14, fill=0, stroke=1)

            c.setFillColor(colors.white)
            c.setFont("Helvetica-Bold", 13)
            c.drawString(x + 16, y + ch - 32, brand_t.upper()[:18])
            c.setFillColor(colors.HexColor("#ea580c"))
            c.setFont("Helvetica-Bold", 13)
            c.drawString(x + 16, y + ch - 47, brand_b.upper()[:18])

            c.setFillColor(colors.HexColor("#fde68a"))
            c.setFont("Helvetica-Bold", 7.3)
            c.drawString(x + 16, y + ch - 63, "SMOKED MEATS • PARTY PANS • TRAILER RIG")
            c.setFillColor(colors.HexColor("#d1d5db"))
            c.setFont("Helvetica", 7.2)
            c.drawString(x + 16, y + ch - 77, "Brisket • Pulled Pork • Smoked Mac • Wings")
            c.drawString(x + 16, y + ch - 89, "House Sauces • Raw Honey • Syrup • Eggs")

            c.setFillColor(colors.white)
            c.setFont("Helvetica-Bold", 9.5)
            c.drawString(x + 16, y + 20, f"Call / Text: {phone}")

            c.setFillColor(colors.white)
            c.roundRect(x + cw - 76, y + 36, 60, 60, 5, fill=1, stroke=0)
            _draw_qr(c, bbq_url, x + cw - 73, y + 39, 54)
            c.setFillColor(colors.HexColor("#fdba74"))
            c.setFont("Helvetica-Bold", 6.3)
            c.drawCentredString(x + cw - 46, y + 24, "SCAN MENU")
    c.save()
    return buf.getvalue()


def build_pit_prep_sheet_pdf(title: str, orders: list[dict]) -> bytes:
    """Auto-Tallied Pitmaster Cook Sheet + Customer Pickup Checklist."""
    buf = io.BytesIO()
    W, H = letter
    c = canvas.Canvas(buf, pagesize=letter)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(40, H - 42, f"PIT BOSS PREP & PICKUP SHEET — {title[:36].upper()}")

    totals: dict[str, int] = {}
    grand_rev = 0.0
    for o in orders:
        if o.get("status") == "CANCELLED":
            continue
        grand_rev += float(o.get("total_amount") or 0)
        for it in json.loads(o.get("items_json") or "[]"):
            k = f"{it.get('name')} ({it.get('option') or 'Item'})"
            totals[k] = totals.get(k, 0) + int(it.get("qty") or 1)

    c.setFont("Helvetica-Bold", 10.5)
    c.drawString(40, H - 62, f"Active Orders: {len(orders)}   |   Expected Total: ${grand_rev:.2f}")

    cy = H - 86
    c.setFillColor(colors.HexColor("#fef3c7"))
    c.rect(40, cy - 18, W - 80, 18, fill=1, stroke=1)
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 9.5)
    c.drawString(48, cy - 13, "AUTO-TALLIED COOK / PAN TOTALS (EVERYTHING TO MAKE)")
    c.drawRightString(W - 48, cy - 13, "TOTAL QTY")
    cy -= 18

    for k, qty in sorted(totals.items()):
        cy -= 18
        c.line(40, cy, W - 40, cy)
        c.setFont("Helvetica-Bold", 9.5)
        c.drawString(48, cy + 5, k[:68])
        c.drawRightString(W - 48, cy + 5, f"x {qty}")

    cy -= 28
    c.setFillColor(colors.HexColor("#e2e8f0"))
    c.rect(40, cy - 18, W - 80, 18, fill=1, stroke=1)
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(46, cy - 13, "DONE   CUSTOMER & PHONE")
    c.drawString(235, cy - 13, "ITEMS ORDERED")
    c.drawRightString(W - 46, cy - 13, "TOTAL / PAY")
    cy -= 18

    for o in orders:
        if o.get("status") == "CANCELLED":
            continue
        if cy < 65:
            c.showPage()
            cy = H - 50
        cy -= 24
        c.line(40, cy, W - 40, cy)
        c.setFont("Helvetica-Bold", 9)
        c.drawString(46, cy + 8, f"[   ]  {o['customer_name'][:18]} ({o.get('customer_phone','')})")
        items_str = ", ".join(f"{x.get('qty',1)}x {x.get('name')} [{x.get('option','')}]" for x in json.loads(o.get("items_json") or "[]"))
        c.setFont("Helvetica", 8.2)
        c.drawString(235, cy + 8, (items_str or o.get("notes") or "Catering Inquiry")[:52])
        c.setFont("Helvetica-Bold", 8.8)
        c.drawRightString(W - 46, cy + 8, f"${float(o.get('total_amount') or 0):.2f} ({o.get('payment_method','Cash')})")
    c.save()
    return buf.getvalue()


def build_homestead_poster_pdf(settings: dict, items: list[dict], homestead_url: str) -> bytes:
    """8.5x11 Printable Porch / Counter QR Sign for Homestead Sauces, Honey, Maple Syrup & Eggs."""
    buf = io.BytesIO()
    W, H = letter
    c = canvas.Canvas(buf, pagesize=letter)
    title = (settings.get("homestead_name") or "Backroad Homestead & Sauce Stand").strip()
    phone = settings.get("contact_phone", "724-266-8008")
    c.setTitle(f"{title} - QR Stand Sign")

    c.setFillColor(colors.HexColor("#1f3a2c"))
    c.rect(0, H - 122, W, 122, fill=1, stroke=0)
    c.setFillColor(colors.HexColor("#d97706"))
    c.rect(0, H - 128, W, 6, fill=1, stroke=0)

    c.setFillColor(colors.HexColor("#fde68a"))
    c.setFont("Helvetica-Bold", 10.5)
    c.drawCentredString(W / 2, H - 36, "SMALL-BATCH HOMESTEAD PROVISIONS & HOUSE BBQ SAUCES")
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 22)
    c.drawCentredString(W / 2, H - 66, title[:42].upper())
    c.setFillColor(colors.HexColor("#e2e8f0"))
    c.setFont("Helvetica", 10.2)
    c.drawCentredString(W / 2, H - 92, "Bottled BBQ Sauces  •  Raw Hive Honey  •  Wood-Fired PA Maple Syrup  •  Fresh Duck & Chicken Eggs")

    c.setFillColor(colors.HexColor("#1c1917"))
    c.setFont("Helvetica-Bold", 18)
    c.drawCentredString(W / 2, H - 164, "SCAN TO CHECK LIVE STOCK & RESERVE YOURS")

    qr_sz = 210
    qx = (W - qr_sz) / 2
    qy = H - 394
    c.setStrokeColor(colors.HexColor("#b45309"))
    c.setLineWidth(2.5)
    c.setFillColor(colors.white)
    c.roundRect(qx, qy, qr_sz, qr_sz, 14, fill=1, stroke=1)
    _draw_qr(c, homestead_url, qx + 15, qy + 15, qr_sz - 30)

    c.setFillColor(colors.HexColor("#92400e"))
    c.setFont("Helvetica-Bold", 11)
    c.drawCentredString(W / 2, qy - 18, homestead_url[:64])

    cy = qy - 50
    c.setFillColor(colors.HexColor("#faf6ee"))
    c.setStrokeColor(colors.HexColor("#d6cfc2"))
    c.setLineWidth(1)
    c.roundRect(46, 74, W - 92, cy - 64, 10, fill=1, stroke=1)

    c.setFillColor(colors.HexColor("#1f3a2c"))
    c.setFont("Helvetica-Bold", 11.5)
    c.drawCentredString(W / 2, cy - 10, "WHAT WE MAKE & GATHER IN SMALL BATCHES:")
    ry = cy - 32
    for it in items[:8]:
        if ry < 92:
            break
        c.setFillColor(colors.HexColor("#1c1917"))
        c.setFont("Helvetica-Bold", 10)
        c.drawString(64, ry, f"• {str(it.get('name') or '')[:42]} ({it.get('unit_label') or 'Each'})")
        c.setFillColor(colors.HexColor("#b45309"))
        c.drawRightString(W - 64, ry, f"${float(it.get('price') or 0):.2f}")
        ry -= 20

    c.setFillColor(colors.HexColor("#57534e"))
    c.setFont("Helvetica-Bold", 9.5)
    c.drawCentredString(W / 2, 42, f"Pay Cash, Venmo, or Cash App at Pickup  •  Questions? Call/Text {phone}")
    c.save()
    return buf.getvalue()
