import io
from datetime import datetime
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas

PRIMARY_MANAGER = "Ryan Pearce"
PRIMARY_INITIALS = "RP"


def _fmt_num(val) -> str:
    if val is None or val == "":
        return ""
    f = float(val)
    if f == 0:
        return "0"
    if f == int(f):
        return str(int(f))
    s = f"{f:.2f}".rstrip("0").rstrip(".")
    return s[1:] if (0 < f < 1 and s.startswith("0.")) else s


def _fmt_pct(val) -> str:
    if val is None or val == "":
        return ""
    f = float(val)
    if f == 0:
        return "0%"
    s = f"{f:.1f}".rstrip("0").rstrip(".")
    if 0 < f < 1 and s.startswith("0."):
        s = s[1:]
    return f"{s}%"


def _fmt_spec_psi(spec: str, psi: int) -> str:
    sp = (spec or "").strip().upper()
    if sp.startswith("SP") or sp.startswith("E-") or "-" in sp:
        clean_sp = sp.replace("SP-", "SP")
        return f"{clean_sp}-{psi}"
    return f"{sp}{psi}"


def _fmt_slash_date(iso_date: str) -> tuple[str, str, str]:
    try:
        dt = datetime.strptime(iso_date, "%Y-%m-%d")
        return str(dt.month), str(dt.day), str(dt.year)
    except Exception:
        return "", "", iso_date


def build_retest_datasheet_pdf(
    work_date: str,
    records: list[dict],
    dot_rin: str = "B386",
    tc_reg: str = "",
    base_sheet_no: int = 8095,
) -> bytes:
    """Exact Landscape Letter replica of HYDROSTATIC WATER JACKET TEST RETEST DATA SHEET (Form 393-001)."""
    buf = io.BytesIO()
    W, H = landscape(letter)
    c = canvas.Canvas(buf, pagesize=(W, H))

    hydro_rows = [r for r in records if r.get("service_mode") == "HYDRO_AND_VISUAL"] or [None]
    chunks = [hydro_rows[i : i + 12] for i in range(0, len(hydro_rows), 12)]
    m_str, d_str, y_str = _fmt_slash_date(work_date)

    left_x = 28
    col_widths = [18, 92, 66, 44, 38, 34, 56, 76, 44, 34, 38, 34, 42, 30, 22, 22, 42, 34]
    total_w = sum(col_widths)
    scale = 736.0 / total_w
    col_w = [w * scale for w in col_widths]
    col_x = [left_x]
    for w in col_w:
        col_x.append(col_x[-1] + w)
    right_x = col_x[-1]

    for page_idx, chunk in enumerate(chunks):
        if page_idx > 0:
            c.showPage()

        sheet_num = (chunk[0].get("sheet_no") if chunk[0] else None) or (base_sheet_no + page_idx)

        c.setFont("Times-Italic", 13)
        c.drawRightString(right_x - 4, H - 28, str(sheet_num))

        c.setFont("Times-Bold", 15)
        c.drawCentredString(W / 2, H - 34, "HYDROSTATIC WATER JACKET TEST RETEST DATA SHEET")

        y_rin = H - 50
        c.setFont("Times-Bold", 8.5)
        c.drawString(left_x + 42, y_rin, "D.O.T. Requalification Identification Number (RIN):")
        c.setFont("Helvetica-Bold", 11)
        c.drawString(left_x + 245, y_rin, dot_rin)
        c.line(left_x + 240, y_rin - 2, left_x + 355, y_rin - 2)

        c.setFont("Times-Bold", 8.5)
        c.drawString(left_x + 382, y_rin, "and/or")
        c.drawString(left_x + 432, y_rin, "Transport Canada (TC) Registration Number:")
        c.setFont("Helvetica", 9)
        c.drawString(left_x + 612, y_rin, tc_reg)
        c.line(left_x + 608, y_rin - 2, right_x - 10, y_rin - 2)

        y_date = H - 64
        c.setFont("Times-Bold", 8.5)
        c.drawString(left_x, y_date, "Date:")
        c.setFont("Helvetica", 10)
        c.drawCentredString(left_x + 36, y_date + 1, m_str)
        c.drawString(left_x + 48, y_date, "/")
        c.drawCentredString(left_x + 60, y_date + 1, d_str)
        c.drawString(left_x + 72, y_date, "/")
        c.drawCentredString(left_x + 94, y_date + 1, y_str)
        c.line(left_x + 24, y_date - 2, left_x + 116, y_date - 2)

        hdr_top = H - 70
        hdr_mid = hdr_top - 11
        hdr_bot = hdr_top - 28
        notice_bot = hdr_bot - 11

        c.setLineWidth(0.7)
        c.setFillColor(colors.Color(0.95, 0.95, 0.95))
        c.rect(left_x, hdr_bot, right_x - left_x, hdr_top - hdr_bot, fill=1, stroke=1)
        c.setFillColor(colors.black)

        c.line(col_x[9], hdr_mid, col_x[16], hdr_mid)
        c.setFont("Times-Bold", 6.8)
        c.drawCentredString((col_x[9] + col_x[14]) / 2, hdr_mid + 2.5, "EXPANSION READINGS (cc's)")
        c.drawCentredString((col_x[14] + col_x[16]) / 2, hdr_mid + 2.5, "Visual Insp.")

        for idx in range(len(col_x)):
            top_y = hdr_mid if idx in (10, 11, 12, 13, 15) else hdr_top
            c.line(col_x[idx], top_y, col_x[idx], hdr_bot)

        single_headers = [
            (0, ["", "#"]),
            (1, ["", "Cylinder Owner"]),
            (2, ["Cylinder Serial", "Number"]),
            (3, ["", "MFG ID"]),
            (4, ["MFG", "Date"]),
            (5, ["Gas", "Service"]),
            (6, ["Physical Size (*)", "(dia. x length)"]),
            (7, ["Specification &", "Service Pressure"]),
            (8, ["Actual Test", "Pressure"]),
            (16, ["Disposition", "Code (*)"]),
            (17, ["Retest", "Operator"]),
        ]
        c.setFont("Times-Bold", 6.5)
        for c_idx, lines_txt in single_headers:
            cx = (col_x[c_idx] + col_x[c_idx + 1]) / 2
            if lines_txt[0]:
                c.drawCentredString(cx, hdr_bot + 13, lines_txt[0])
                c.setFont("Times-Roman" if c_idx == 6 else "Times-Bold", 5.6 if c_idx == 6 else 6.5)
                c.drawCentredString(cx, hdr_bot + 4, lines_txt[1])
                c.setFont("Times-Bold", 6.5)
            else:
                c.drawCentredString(cx, hdr_bot + 8, lines_txt[1])

        sub_headers = [
            (9, ["", "Total"]),
            (10, ["", "Permanent"]),
            (11, ["", "Elastic"]),
            (12, ["%", "Permanent"]),
            (13, ["", "REE"]),
            (14, ["", "Pass"]),
            (15, ["", "Fail"]),
        ]
        c.setFont("Times-Bold", 6.0)
        for c_idx, lines_txt in sub_headers:
            cx = (col_x[c_idx] + col_x[c_idx + 1]) / 2
            if lines_txt[0]:
                c.drawCentredString(cx, hdr_bot + 9.5, lines_txt[0])
                c.drawCentredString(cx, hdr_bot + 3, lines_txt[1])
            else:
                c.drawCentredString(cx, hdr_bot + 5, lines_txt[1])

        c.rect(left_x, notice_bot, right_x - left_x, hdr_bot - notice_bot, fill=0, stroke=1)
        c.line(col_x[1], hdr_bot, col_x[1], notice_bot)
        c.setFont("Times-BoldItalic", 6.5)
        c.drawString(col_x[1] + 3, notice_bot + 3, "Calibrated Cylinder:")
        c.setFont("Times-Italic", 6.5)
        c.drawString(
            col_x[1] + 63,
            notice_bot + 3,
            "Be sure to verify the system accuracy with the calibrated cylinder prior to each day of testing. Use the back of this form to properly record the verification procedure.",
        )

        row_h = 33.5
        data_h = 19.5
        cur_top = notice_bot

        for r_i in range(12):
            rec = chunk[r_i] if r_i < len(chunk) else None
            r_mid = cur_top - data_h
            r_bot = cur_top - row_h

            c.setLineWidth(0.6)
            c.rect(left_x, r_bot, right_x - left_x, row_h, fill=0, stroke=1)
            c.setLineWidth(0.35)
            c.line(col_x[1], r_mid, right_x, r_mid)

            c.line(col_x[1], cur_top, col_x[1], r_bot)
            for idx in range(2, len(col_x) - 1):
                c.line(col_x[idx], cur_top, col_x[idx], r_mid)

            c.setFont("Times-Bold", 7.5)
            c.drawCentredString((col_x[0] + col_x[1]) / 2, cur_top - 12, str(r_i + 1))

            c.setFont("Times-Bold", 6.2)
            c.drawString(col_x[1] + 3, r_bot + 4, "Notes:")

            if rec:
                c.setFont("Helvetica", 8.2)
                y_val = r_mid + 5
                c.drawString(col_x[1] + 3, y_val, (rec.get("customer_name") or "")[:20])
                c.drawCentredString((col_x[2] + col_x[3]) / 2, y_val, (rec.get("serial_number") or "")[:14])
                c.drawCentredString((col_x[3] + col_x[4]) / 2, y_val, (rec.get("mfg_id") or "")[:9])
                c.drawCentredString((col_x[4] + col_x[5]) / 2, y_val, (rec.get("mfg_date") or "")[:8])
                c.drawCentredString((col_x[5] + col_x[6]) / 2, y_val, (rec.get("gas_service") or "")[:6])
                phys = (rec.get("physical_size") or "").replace(" x ", "x")
                c.drawCentredString((col_x[6] + col_x[7]) / 2, y_val, phys[:12])
                spec_str = _fmt_spec_psi(rec.get("dot_spec", ""), int(rec.get("service_psi") or 0))
                c.drawCentredString((col_x[7] + col_x[8]) / 2, y_val, spec_str[:15])
                c.drawCentredString((col_x[8] + col_x[9]) / 2, y_val, str(rec.get("test_psi") or ""))
                c.drawCentredString((col_x[9] + col_x[10]) / 2, y_val, _fmt_num(rec.get("total_cc")))
                c.drawCentredString((col_x[10] + col_x[11]) / 2, y_val, _fmt_num(rec.get("permanent_cc")))
                c.drawCentredString((col_x[11] + col_x[12]) / 2, y_val, _fmt_num(rec.get("elastic_cc")))
                c.drawCentredString((col_x[12] + col_x[13]) / 2, y_val, _fmt_pct(rec.get("percent_permanent")))
                c.drawCentredString((col_x[13] + col_x[14]) / 2, y_val, _fmt_num(rec.get("ree_limit")))

                vis_ok = bool(rec.get("visual_pass", 1))
                chk_col = 14 if vis_ok else 15
                cx_chk = (col_x[chk_col] + col_x[chk_col + 1]) / 2
                c.setLineWidth(1.1)
                c.line(cx_chk - 3.5, y_val + 2, cx_chk - 1, y_val - 1)
                c.line(cx_chk - 1, y_val - 1, cx_chk + 4, y_val + 6)
                c.setLineWidth(0.35)

                c.drawCentredString((col_x[16] + col_x[17]) / 2, y_val, rec.get("hydro_disposition") or "A")
                # Strictly locked to RP for DOT recordkeeping
                c.drawCentredString((col_x[17] + col_x[18]) / 2, y_val, PRIMARY_INITIALS)

                if rec.get("notes"):
                    c.setFont("Helvetica", 7.2)
                    c.drawString(col_x[1] + 28, r_bot + 4, str(rec["notes"])[:110])

            cur_top = r_bot

        fy = cur_top - 16
        c.setFont("Times-Roman", 7.5)
        c.drawString(left_x + 10, fy, "I hereby certify that all of the above test were made under my supervision and")
        c.drawString(left_x + 10, fy - 9, "in accordance with DOT and/or TC regulations.")

        c.setFont("Times-Italic", 10)
        c.drawString(left_x + 24, fy - 25, PRIMARY_MANAGER)
        c.setLineWidth(0.6)
        c.line(left_x + 10, fy - 28, left_x + 185, fy - 28)
        c.setFont("Times-Roman", 7.5)
        c.drawString(left_x + 188, fy - 27, "(Facility Manager)")

        dx = left_x + 410
        c.setFont("Times-Bold", 7.2)
        c.drawString(dx, fy, "(*) Disposition Codes:")
        codes = [
            "A - Return to Service",
            "B - Hold for Retest",
            "C - Rejected",
            "D - Condemned",
            "E - Other (specify in notes)",
        ]
        c.setFont("Times-Roman", 6.8)
        for idx, txt in enumerate(codes):
            c.drawString(dx + 88, fy - (idx * 8.2), txt)

        c.setFont("Times-Roman", 6.2)
        c.drawRightString(right_x, fy - 12, "This form meets the requirements for recording of")
        c.drawRightString(right_x, fy - 20, "cylinder re-qualification under CFR Title49 §180.215")
        c.setFont("Times-Bold", 6.2)
        c.drawRightString(right_x, fy - 30, f" Weber's Tank Testing • RIN {dot_rin}")

    c.save()
    return buf.getvalue()


def build_verification_pdf(work_date: str, verifications: list[dict], cal_serial: str = "1SF11904") -> bytes:
    """Back-of-form Daily Verification Data for Water Jacket Testing (Method B)."""
    buf = io.BytesIO()
    W, H = landscape(letter)
    c = canvas.Canvas(buf, pagesize=(W, H))

    c.setFont("Times-Bold", 15)
    c.drawCentredString(W / 2, H - 36, "VERIFICATION DATA FOR WATER JACKET TESTING")
    c.setFont("Times-Bold", 9.5)
    c.drawString(40, H - 56, f"Date: {work_date}")
    c.drawString(190, H - 56, f"Serial No. of Calibrated Cylinder: {cal_serial}")
    c.drawString(490, H - 56, f"Operator's Name: {PRIMARY_MANAGER}")

    c.setFont("Times-Bold", 11)
    c.drawString(40, H - 78, "Method B — Verification procedure using Pressure Indicating Device (PID) against Expansion Indicating Device (EID)")

    headers = ["Time", "Water Jacket", "Gauge Pressure (PSI)", "Expected Chart (cc) (A)", "Actual Burette (cc) (B)", "Permanent Exp (cc)", "Elastic Exp (cc)", "% Deviation ((A-B)/A)", "Within ±1%?"]
    widths = [64, 86, 86, 92, 92, 78, 74, 88, 52]
    x0 = 40
    xs = [x0]
    for w in widths:
        xs.append(xs[-1] + w)

    y = H - 92
    c.setFillColor(colors.Color(0.94, 0.94, 0.94))
    c.rect(x0, y - 24, xs[-1] - x0, 24, fill=1, stroke=1)
    c.setFillColor(colors.black)
    c.setFont("Times-Bold", 7.5)
    for i, h_txt in enumerate(headers):
        c.drawCentredString((xs[i] + xs[i + 1]) / 2, y - 15, h_txt)
        c.line(xs[i], y, xs[i], y - 24)
    c.line(xs[-1], y, xs[-1], y - 24)

    cur_y = y - 24
    row_h = 22
    for idx in range(16):
        v = verifications[idx] if idx < len(verifications) else None
        c.rect(x0, cur_y - row_h, xs[-1] - x0, row_h, fill=0, stroke=1)
        for x_line in xs[1:-1]:
            c.line(x_line, cur_y, x_line, cur_y - row_h)
        if v:
            c.setFont("Helvetica", 8.5)
            vals = [
                v.get("test_time", ""),
                v.get("jacket_type", ""),
                str(v.get("target_pressure_psi", "")),
                _fmt_num(v.get("expected_cc")),
                _fmt_num(v.get("actual_total_cc")),
                _fmt_num(v.get("permanent_cc")),
                _fmt_num(v.get("elastic_cc")),
                f"{v.get('percent_deviation', 0)}%",
                "PASS" if v.get("passed") else "FAIL",
            ]
            for i, val in enumerate(vals):
                c.drawCentredString((xs[i] + xs[i + 1]) / 2, cur_y - 14, val)
        cur_y -= row_h

    c.setFont("Times-Roman", 8)
    c.drawString(40, cur_y - 20, "CFR Title 49 §180.215(b)(1): Verification must demonstrate ±1% accuracy and permanent expansion returning to zero prior to daily testing.")
    c.save()
    return buf.getvalue()


def build_visual_log_pdf(work_date: str, records: list[dict], dot_rin: str = "B386") -> bytes:
    """D.O.T./I.C.C. Cylinder Visual Inspection and Requalification Log."""
    buf = io.BytesIO()
    W, H = landscape(letter)
    c = canvas.Canvas(buf, pagesize=(W, H))

    chunks = [records[i : i + 15] for i in range(0, max(1, len(records)), 15)]
    headers = [
        "Serial No.", "Mfg Date", "ICC/DOT Spec", "Mfg ID", "Owner / Client", "Size",
        "Coating", "Corrosion", "Dents", "Cuts/Gouges", "Leaks", "Fire", "Bulges", "Neck",
        "Disp", "Relief Chg", "Init",
    ]
    widths = [64, 40, 58, 40, 86, 44, 44, 40, 36, 46, 34, 32, 34, 34, 32, 42, 28]
    x0 = 28
    xs = [x0]
    for w in widths:
        xs.append(xs[-1] + w)

    for p_idx, chunk in enumerate(chunks):
        if p_idx > 0:
            c.showPage()
        c.setFont("Times-Bold", 14)
        c.drawCentredString(W / 2, H - 34, "D.O.T./I.C.C. CYLINDER VISUAL INSPECTION AND REQUALIFICATION LOG")
        c.setFont("Times-Bold", 9)
        c.drawString(x0, H - 52, f"Company: Weber's Tank Testing     Inspection Date: {work_date}     RIN: {dot_rin}     Responsible Manager: {PRIMARY_MANAGER}")

        y = H - 64
        c.setFillColor(colors.Color(0.94, 0.94, 0.94))
        c.rect(x0, y - 20, xs[-1] - x0, 20, fill=1, stroke=1)
        c.setFillColor(colors.black)
        c.setFont("Times-Bold", 6.5)
        for i, h_txt in enumerate(headers):
            c.drawCentredString((xs[i] + xs[i + 1]) / 2, y - 13, h_txt)
            c.line(xs[i], y, xs[i], y - 20)
        c.line(xs[-1], y, xs[-1], y - 20)

        cy = y - 20
        rh = 26
        for r_i in range(15):
            r = chunk[r_i] if r_i < len(chunk) else None
            c.rect(x0, cy - rh, xs[-1] - x0, rh, fill=0, stroke=1)
            for xl in xs[1:-1]:
                c.line(xl, cy, xl, cy - rh)
            if r:
                c.setFont("Helvetica", 7.3)
                vals = [
                    (r.get("serial_number") or "")[:12],
                    (r.get("mfg_date") or "")[:7],
                    _fmt_spec_psi(r.get("dot_spec", ""), int(r.get("service_psi") or 0))[:11],
                    (r.get("mfg_id") or "")[:7],
                    (r.get("customer_name") or "")[:16],
                    (r.get("physical_size") or "")[:8],
                    (r.get("coating_condition") or "Good")[:8],
                    r.get("chk_corrosion", "OK"),
                    r.get("chk_dents_dings", "OK"),
                    r.get("chk_cuts_gouges", "OK"),
                    r.get("chk_leaks", "OK"),
                    r.get("chk_fire_damage", "OK"),
                    r.get("chk_bulges", "OK"),
                    r.get("chk_neck_defects", "OK"),
                    r.get("visual_disposition", "OK"),
                    r.get("relief_valve_changed", "No"),
                    PRIMARY_INITIALS,
                ]
                for i, v in enumerate(vals):
                    c.drawCentredString((xs[i] + xs[i + 1]) / 2, cy - 16, str(v))
            cy -= rh

        c.setFont("Times-Bold", 7.5)
        c.drawString(x0, cy - 16, "Disposition Codes:   OK - Return to Service     SC - Scrap (Condemned)     RM - Removed from Service     (Retain in files 5 years)")

    c.save()
    return buf.getvalue()


def build_rin_stickers_pdf(
    work_date: str,
    count: int = 30,
    dot_rin: str = "B386",
    cols: int = 3,
    rows: int = 10,
    skip_slots: int = 0,
) -> bytes:
    """Laser Mailing Label Sheet (Default 3x10 Avery 5160 2.625x1.0 in) in DOT RIN 2x2 clockwise format."""
    buf = io.BytesIO()
    W, H = letter
    c = canvas.Canvas(buf, pagesize=letter)

    try:
        dt = datetime.strptime(work_date, "%Y-%m-%d")
        month_str = str(dt.month)
        year_str = dt.strftime("%y")
    except Exception:
        month_str, year_str = "11", "26"

    rin = (dot_rin or "B386").ljust(4)
    r_tl, r_tr, r_br, r_bl = rin[0], rin[1], rin[2], rin[3]

    margin_x = 0.19 * inch
    margin_y = 0.5 * inch
    pitch_x = 2.75 * inch
    label_w = 2.625 * inch
    label_h = (H - 2 * margin_y) / max(1, rows)

    per_page = cols * rows
    total_slots = skip_slots + max(1, count)

    for slot in range(total_slots):
        if slot > 0 and slot % per_page == 0:
            c.showPage()
        if slot < skip_slots:
            continue

        local = slot % per_page
        r_idx = local // cols
        c_idx = local % cols

        lx = margin_x + c_idx * pitch_x
        ly = H - margin_y - (r_idx + 1) * label_h

        pad = 4
        bx, by = lx + pad, ly + pad
        bw, bh = label_w - 2 * pad, label_h - 2 * pad
        c.setLineWidth(1.2)
        c.roundRect(bx, by, bw, bh, 6, stroke=1, fill=0)

        box_y = by + 15
        side_w = bw * 0.27
        side_h = bh * 0.44
        mid_w = bw * 0.30
        mid_h = bh * 0.64

        m_x = bx + bw * 0.05
        rin_x = bx + (bw - mid_w) / 2
        y_x = bx + bw * 0.95 - side_w

        c.setLineWidth(1.0)
        c.rect(m_x, box_y + 4, side_w, side_h, stroke=1, fill=0)
        c.rect(rin_x, box_y - 2, mid_w, mid_h, stroke=1, fill=0)
        c.rect(y_x, box_y + 4, side_w, side_h, stroke=1, fill=0)

        c.setFont("Helvetica-Bold", 15)
        c.drawCentredString(m_x + side_w / 2, box_y + 4 + side_h / 2 - 5, month_str)
        c.drawCentredString(y_x + side_w / 2, box_y + 4 + side_h / 2 - 5, year_str)

        c.setFont("Helvetica-Bold", 14)
        cx_rin = rin_x + mid_w / 2
        cy_rin = box_y - 2 + mid_h / 2
        c.drawCentredString(cx_rin - 8, cy_rin + 3, r_tl)
        c.drawCentredString(cx_rin + 8, cy_rin + 3, r_tr)
        c.drawCentredString(cx_rin - 8, cy_rin - 13, r_bl)
        c.drawCentredString(cx_rin + 8, cy_rin - 13, r_br)

        c.setFont("Helvetica-Bold", 6.3)
        c.drawCentredString(m_x + side_w / 2, box_y - 5, "MONTH")
        c.drawCentredString(y_x + side_w / 2, box_y - 5, "YEAR")
        c.drawCentredString(rin_x + mid_w / 2, by + 4, "LICENSE NO.")

    c.save()
    return buf.getvalue()


def build_valve_tags_pdf(work_date: str, records: list[dict], dot_rin: str = "B386") -> bytes:
    """Shop Clipboard Match Sheet for Active Valve Tags."""
    buf = io.BytesIO()
    W, H = letter
    c = canvas.Canvas(buf, pagesize=letter)

    c.setFont("Helvetica-Bold", 15)
    c.drawString(40, H - 44, "WEBER'S TANK TESTING — ACTIVE VALVE TAG MATCH SHEET")
    c.setFont("Helvetica", 9.5)
    c.drawString(40, H - 60, f"Work Date: {work_date}   •   DOT RIN: {dot_rin}   •   Facility Sign-Off: {PRIMARY_MANAGER} ({PRIMARY_INITIALS})")

    headers = ["Valve Tag #", "Cylinder Serial #", "Customer / Owner", "Spec & PSI", "Gas", "Relief Chg?", "Reinstalled"]
    widths = [72, 105, 145, 82, 42, 58, 58]
    x0 = 30
    xs = [x0]
    for w in widths:
        xs.append(xs[-1] + w)

    y = H - 78
    c.setFillColor(colors.Color(0.93, 0.95, 0.94))
    c.rect(x0, y - 22, xs[-1] - x0, 22, fill=1, stroke=1)
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 8.5)
    for i, h_txt in enumerate(headers):
        c.drawCentredString((xs[i] + xs[i + 1]) / 2, y - 15, h_txt)
        c.line(xs[i], y, xs[i], y - 22)
    c.line(xs[-1], y, xs[-1], y - 22)

    cy = y - 22
    rh = 22
    active_rows = [r for r in records if r.get("valve_tag") and not r.get("valve_reinstalled")] or [None]
    for r in active_rows:
        if cy - rh < 45:
            c.showPage()
            cy = H - 50
        c.rect(x0, cy - rh, xs[-1] - x0, rh, fill=0, stroke=1)
        for xl in xs[1:-1]:
            c.line(xl, cy, xl, cy - rh)
        if r:
            c.setFont("Helvetica-Bold", 9.5)
            c.drawCentredString((xs[0] + xs[1]) / 2, cy - 15, str(r.get("valve_tag") or "—"))
            c.drawCentredString((xs[1] + xs[2]) / 2, cy - 15, str(r.get("serial_number") or "")[:15])
            c.setFont("Helvetica", 8.5)
            c.drawString(xs[2] + 4, cy - 15, str(r.get("customer_name") or "")[:24])
            c.drawCentredString((xs[3] + xs[4]) / 2, cy - 15, _fmt_spec_psi(r.get("dot_spec", ""), int(r.get("service_psi") or 0))[:12])
            c.drawCentredString((xs[4] + xs[5]) / 2, cy - 15, str(r.get("gas_service") or "")[:6])
            c.drawCentredString((xs[5] + xs[6]) / 2, cy - 15, str(r.get("relief_valve_changed") or "No"))
            c.drawCentredString((xs[6] + xs[7]) / 2, cy - 15, "[   ]")
        cy -= rh

    c.save()
    return buf.getvalue()


def build_invoice_pdf(invoice: dict, items: list[dict], phone: str = "724-266-8008", dot_rin: str = "B386") -> bytes:
    """Clean Printable Customer Invoice PDF."""
    buf = io.BytesIO()
    W, H = letter
    c = canvas.Canvas(buf, pagesize=letter)

    c.setFont("Helvetica-Bold", 18)
    c.drawString(45, H - 50, "WEBER'S TANK TESTING")
    c.setFont("Helvetica", 10)
    c.drawString(45, H - 65, f"Hydrostatic Air Cylinder Testing & Visual Requalification • DOT RIN {dot_rin}")
    c.drawString(45, H - 79, f"Phone: {phone} • Facility Manager: {PRIMARY_MANAGER}")

    c.setFont("Helvetica-Bold", 16)
    c.drawRightString(W - 45, H - 50, "INVOICE")
    c.setFont("Helvetica", 10)
    c.drawRightString(W - 45, H - 66, f"Invoice #: {invoice.get('invoice_no')}")
    c.drawRightString(W - 45, H - 80, f"Invoice Date: {invoice.get('invoice_date')}")
    c.drawRightString(W - 45, H - 94, f"Status: {invoice.get('status')}")

    c.line(45, H - 108, W - 45, H - 108)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(45, H - 128, f"Bill To: {invoice.get('customer_name')}")
    c.setFont("Helvetica", 10)
    c.drawString(45, H - 143, f"Service / Test Date: {invoice.get('work_date')}")

    y = H - 172
    c.setFillColor(colors.Color(0.93, 0.95, 0.94))
    c.rect(45, y - 20, W - 90, 20, fill=1, stroke=1)
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 9.5)
    c.drawString(52, y - 14, "Description / Cylinder Serial & Service")
    c.drawCentredString(W - 185, y - 14, "Qty")
    c.drawRightString(W - 115, y - 14, "Unit Price")
    c.drawRightString(W - 52, y - 14, "Line Total")

    cy = y - 20
    c.setFont("Helvetica", 9.5)
    for it in items:
        cy -= 20
        if cy < 110:
            c.showPage()
            cy = H - 60
            c.setFont("Helvetica", 9.5)
        c.line(45, cy, W - 45, cy)
        c.drawString(52, cy + 6, str(it.get("description", ""))[:68])
        c.drawCentredString(W - 185, cy + 6, _fmt_num(it.get("qty", 1)))
        c.drawRightString(W - 115, cy + 6, f"${float(it.get('unit_price', 0)):.2f}")
        c.drawRightString(W - 52, cy + 6, f"${float(it.get('line_total', 0)):.2f}")

    cy -= 28
    c.setFont("Helvetica-Bold", 10.5)
    c.drawRightString(W - 125, cy, "Subtotal:")
    c.drawRightString(W - 52, cy, f"${float(invoice.get('subtotal', 0)):.2f}")
    cy -= 16
    c.drawRightString(W - 125, cy, f"Tax ({invoice.get('tax_rate', 0)}%):")
    c.drawRightString(W - 52, cy, f"${float(invoice.get('tax_amount', 0)):.2f}")
    cy -= 20
    c.setFont("Helvetica-Bold", 12.5)
    c.drawRightString(W - 125, cy, "Total Due:")
    c.drawRightString(W - 52, cy, f"${float(invoice.get('total_amount', 0)):.2f}")

    if invoice.get("notes"):
        c.setFont("Helvetica", 9.5)
        c.drawString(45, cy - 30, f"Notes: {invoice['notes']}")

    c.save()
    return buf.getvalue()