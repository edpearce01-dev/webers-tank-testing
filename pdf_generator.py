from pathlib import Path
import io
from datetime import datetime
from reportlab.graphics import renderPDF
from reportlab.graphics.barcode import qr
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas

PRIMARY_MANAGER = "Ryan Pearce"
PRIMARY_INITIALS = "RP"
PDF_AUTHOR = "Weber's Tank Testing (DOT RIN B386)"
SHOP_ADDRESS = "3086 Legionville Rd, Baden, PA 15005"
SHOP_HOURS = "Mon & Fri 9AM-4PM  |  Tue-Thu 9AM-2PM  |  24/7 Loading Dock Drop-Off"

CONDEMN_LABELS = {
    "STAMPED_CONDEMNED": "CONDEMNED stamped over DOT marks",
    "DEVALVED_UNSERVICEABLE": "Returned devalved / threads rendered unserviceable",
    "EXPIRED_15YR": "Retired: 15-yr composite service life exceeded",
    "CUSTOMER_SCRAP": "Condemned & retained for scrap per owner",
}



def _stamp_pdf_crest(c: canvas.Canvas, x: float, y: float, size: float, watermark_page: tuple | None = None) -> None:
    """Draws crisp header crest badge in dedicated non-overlapping margin space."""
    logo_p = Path(__file__).parent / "static" / "images" / "logo-crest.png"
    if not logo_p.exists() or size <= 0:
        return
    try:
        c.drawImage(str(logo_p), x, y, width=size, height=size, preserveAspectRatio=True, mask="auto")
    except Exception:
        pass


def _set_pdf_meta(c: canvas.Canvas, title: str) -> None:
    c.setTitle(title)
    c.setAuthor(PDF_AUTHOR)
    c.setSubject(title)


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


def _clean_ree_for_print(rec: dict):
    spec = (rec.get("dot_spec") or "").strip().upper()
    ree = rec.get("ree_limit")
    if spec in ("3A", "3AA", "3AL") and ree == 7.2:
        return None
    return ree


def _format_dot_row_notes(rec: dict) -> str:
    parts = []
    c_act = (rec.get("condemn_action") or "").strip().upper()
    if c_act and (rec.get("hydro_disposition") == "D" or not rec.get("visual_pass", 1)):
        parts.append(f"[CONDEMNED: {CONDEMN_LABELS.get(c_act, c_act)}]")
    if rec.get("star_10yr") and rec.get("hydro_disposition") == "A":
        parts.append("10-Yr Star (★)")
    addons = []
    if rec.get("addon_oring"):
        addons.append("Neck O-Ring")
    if rec.get("addon_burst_disc") or rec.get("relief_valve_changed") == "Yes":
        addons.append("Burst Disc/Relief")
    if rec.get("addon_clean"):
        addons.append("Internal Clean")
    if addons:
        parts.append("Svc: " + ", ".join(addons))
    raw_note = str(rec.get("notes") or "").strip()
    if raw_note:
        parts.append(raw_note)
    return " | ".join(parts)


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
    _set_pdf_meta(c, f"DOT Retest Data Sheet - {work_date} (RIN {dot_rin})")

    hydro_only = [r for r in records if r.get("service_mode") == "HYDRO_AND_VISUAL"]
    if not hydro_only:
        chunks = [[None]]
    else:
        chunks = []
        by_sheet: dict[int, list[dict]] = {}
        for r in hydro_only:
            sn = int(r.get("sheet_no") or base_sheet_no)
            by_sheet.setdefault(sn, []).append(r)
        for sn in sorted(by_sheet.keys()):
            s_rows = by_sheet[sn]
            for i in range(0, len(s_rows), 12):
                chunks.append(s_rows[i : i + 12])
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
                c.drawCentredString((col_x[4] + col_x[5]) / 2, y_val, (rec.get("mfg_date") or "").lstrip("'")[:8])
                c.drawCentredString((col_x[5] + col_x[6]) / 2, y_val, (rec.get("gas_service") or "")[:6])
                phys = (rec.get("physical_size") or "").replace(" x ", "x")
                c.drawCentredString((col_x[6] + col_x[7]) / 2, y_val, phys[:12])
                spec_str = _fmt_spec_psi(rec.get("dot_spec", ""), int(rec.get("service_psi") or 0)) + (" ★" if (rec.get("star_10yr") and rec.get("hydro_disposition") == "A") else "")
                c.drawCentredString((col_x[7] + col_x[8]) / 2, y_val, spec_str[:15])
                c.drawCentredString((col_x[8] + col_x[9]) / 2, y_val, str(rec.get("test_psi") or ""))
                c.drawCentredString((col_x[9] + col_x[10]) / 2, y_val, _fmt_num(rec.get("total_cc")))
                c.drawCentredString((col_x[10] + col_x[11]) / 2, y_val, _fmt_num(rec.get("permanent_cc")))
                c.drawCentredString((col_x[11] + col_x[12]) / 2, y_val, _fmt_num(rec.get("elastic_cc")))
                c.drawCentredString((col_x[12] + col_x[13]) / 2, y_val, _fmt_pct(rec.get("percent_permanent")))
                c.drawCentredString((col_x[13] + col_x[14]) / 2, y_val, _fmt_num(_clean_ree_for_print(rec)))

                vis_ok = bool(rec.get("visual_pass", 1))
                chk_col = 14 if vis_ok else 15
                cx_chk = (col_x[chk_col] + col_x[chk_col + 1]) / 2
                c.setLineWidth(1.1)
                c.line(cx_chk - 3.5, y_val + 2, cx_chk - 1, y_val - 1)
                c.line(cx_chk - 1, y_val - 1, cx_chk + 4, y_val + 6)
                c.setLineWidth(0.35)

                c.drawCentredString((col_x[16] + col_x[17]) / 2, y_val, rec.get("hydro_disposition") or "A")
                op_cell = "ADM*" if "ADMIN" in str(rec.get("operator_initials", "")) or "ADMIN" in str(rec.get("tested_by", "")) else PRIMARY_INITIALS
                c.drawCentredString((col_x[17] + col_x[18]) / 2, y_val, op_cell)

                row_note = _format_dot_row_notes(rec)
                if row_note:
                    c.setFont("Helvetica", 7.1)
                    c.drawString(col_x[1] + 28, r_bot + 4, row_note[:115])

            cur_top = r_bot

        fy = cur_top - 15
        c.setFont("Times-Roman", 7.4)
        c.drawString(left_x + 8, fy, "I hereby certify that all of the above tests were made under my supervision and")
        c.drawString(left_x + 8, fy - 9, "in accordance with DOT and/or TC regulations.")

        real_rows = [r for r in chunk if r]
        all_adm = bool(real_rows) and all("ADMIN" in str(r.get("operator_initials", "")) or "ADMIN" in str(r.get("tested_by", "")) for r in real_rows)
        c.setFont("Times-Italic", 10)
        c.drawString(left_x + 20, fy - 25, "ADMIN DIAGNOSTIC (NOT DOT SIGNED)" if all_adm else PRIMARY_MANAGER)
        c.setLineWidth(0.6)
        c.line(left_x + 8, fy - 28, left_x + 178, fy - 28)
        c.setFont("Times-Roman", 7.4)
        c.drawString(left_x + 182, fy - 27, "(Facility Manager)")

        dx = left_x + 295
        c.setFont("Times-Bold", 7.1)
        c.drawString(dx, fy, "(*) Disposition Codes:")
        codes = [
            "A - Return to Service",
            "B - Hold for Retest",
            "C - Rejected",
            "D - Condemned",
            "E - Other (specify in notes)",
        ]
        c.setFont("Times-Roman", 6.7)
        for idx, txt in enumerate(codes):
            c.drawString(dx + 80, fy - (idx * 8.0), txt)

        c.setFont("Times-Roman", 6.4)
        c.drawRightString(right_x, fy - 4, "This form meets the requirements for recording of")
        c.drawRightString(right_x, fy - 12, "cylinder re-qualification under CFR Title 49 §180.215")
        c.setFont("Times-Bold", 6.5)
        c.drawRightString(right_x, fy - 22, f"Weber's Tank Testing  |  RIN {dot_rin}")
        c.setFont("Times-Roman", 6.2)
        c.drawRightString(right_x, fy - 30, SHOP_ADDRESS)

    c.save()
    return buf.getvalue()


def build_verification_pdf(work_date: str, verifications: list[dict], cal_serial: str = "1SF11904") -> bytes:
    """Back-of-form Daily Verification Data for Water Jacket Testing (Method B)."""
    buf = io.BytesIO()
    W, H = landscape(letter)
    c = canvas.Canvas(buf, pagesize=(W, H))
    _set_pdf_meta(c, f"DOT Daily Verification Sheet - {work_date} ({cal_serial})")

    headers = [
        "Time", "Water Jacket", "Gauge Pressure (PSI)", "Expected Chart (cc) (A)",
        "Actual Burette (cc) (B)", "Permanent Exp (cc)", "Elastic Exp (cc)",
        "% Deviation ((A-B)/A)", "Within ±1%?",
    ]
    widths = [64, 86, 86, 92, 92, 78, 74, 88, 52]
    x0 = 40
    xs = [x0]
    for w in widths:
        xs.append(xs[-1] + w)

    rows_per_page = 16
    chunks = [verifications[i : i + rows_per_page] for i in range(0, max(1, len(verifications)), rows_per_page)]

    for p_idx, chunk in enumerate(chunks):
        if p_idx > 0:
            c.showPage()

        c.setFont("Times-Bold", 15)
        c.drawCentredString(W / 2, H - 36, "VERIFICATION DATA FOR WATER JACKET TESTING")
        c.setFont("Times-Bold", 9.5)
        c.drawString(40, H - 56, f"Date: {work_date}")
        c.drawString(190, H - 56, f"Serial No. of Calibrated Cylinder: {cal_serial}")
        c.drawString(490, H - 56, f"Operator's Name: {PRIMARY_MANAGER} ({PRIMARY_INITIALS})")

        c.setFont("Times-Bold", 10.2)
        c.drawString(
            40, H - 76,
            "Method B - Verification procedure using Pressure Indicating Device (PID) against Expansion Indicating Device (EID)",
        )

        y = H - 90
        c.setFillColor(colors.Color(0.94, 0.94, 0.94))
        c.rect(x0, y - 24, xs[-1] - x0, 24, fill=1, stroke=1)
        c.setFillColor(colors.black)
        c.setFont("Times-Bold", 7.4)
        for i, h_txt in enumerate(headers):
            c.drawCentredString((xs[i] + xs[i + 1]) / 2, y - 15, h_txt)
            c.line(xs[i], y, xs[i], y - 24)
        c.line(xs[-1], y, xs[-1], y - 24)

        cur_y = y - 24
        row_h = 22
        for idx in range(rows_per_page):
            v = chunk[idx] if idx < len(chunk) else None
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

        c.setFont("Times-Roman", 7.8)
        c.drawString(40, cur_y - 16, "Example (Method B):   Pressure: 3000 PSI  |  Expected (A): 54.0  |  Actual (B): 53.8  |  Perm: 0  |  Elastic: 53.8  |  % Dev: 0.37%")
        c.setFont("Times-Bold", 7.6)
        c.drawString(
            40, cur_y - 27,
            "Formula for percentage:  (A - B) ÷ A × 100 = % Deviation (±1.0% max)    |    Formula for elastic expansion:  Actual (B) - Permanent = Elastic",
        )
        all_v_adm = bool(chunk) and all("ADMIN" in str(v.get("operator_initials", "")) or "ADMIN" in str(v.get("logged_by", "")) for v in chunk if v)
        c.setFont("Times-Italic", 10)
        c.drawString(48, cur_y - 45, "ADMIN DIAGNOSTIC (NOT DOT SIGNED)" if all_v_adm else PRIMARY_MANAGER)
        c.line(40, cur_y - 48, 215, cur_y - 48)
        c.setFont("Times-Roman", 7.6)
        c.drawString(220, cur_y - 47, "(Verified By - Facility Manager)")
        c.drawRightString(xs[-1], cur_y - 40, "CFR Title 49 §180.215  |  Form 393-001")
        c.drawRightString(xs[-1], cur_y - 49, f"Weber's Tank Testing  |  {SHOP_ADDRESS}")

    c.save()
    return buf.getvalue()


def build_visual_log_pdf(
    work_date: str,
    records: list[dict],
    dot_rin: str = "B386",
    visual_rin: str = "V143159",
) -> bytes:
    """D.O.T./I.C.C. Cylinder Visual Inspection and Requalification Log (Exact replica of physical shop log)."""
    buf = io.BytesIO()
    W, H = landscape(letter)
    c = canvas.Canvas(buf, pagesize=(W, H))
    _set_pdf_meta(c, f"DOT Cylinder Visual Inspection & Requalification Log - {work_date} ({dot_rin} / {visual_rin})")

    chunks = [records[i : i + 14] for i in range(0, max(1, len(records)), 14)]
    headers = [
        "Serial No.", "Date of Mfg.", "ICC/DOT Spec.", "Name of Mfg.", "Owner / Symbol", "Size",
        "Type", "Condition",
        "Corrosion", "Dents/Dings", "Cuts/Gouges", "Leaks", "Fire Dmg", "Bulges", "Neck Def",
        "Disp.", "Date Insp.", "Relief Chg", "Init.",
    ]
    widths = [62, 38, 52, 38, 76, 36, 34, 40, 36, 38, 42, 30, 32, 30, 32, 28, 40, 36, 26]
    x0 = 23
    xs = [x0]
    for w in widths:
        xs.append(xs[-1] + w)

    try:
        dt_vis = datetime.strptime(work_date, "%Y-%m-%d")
        insp_my = f"{dt_vis.month:02d}/{str(dt_vis.year)[-2:]}"
    except Exception:
        insp_my = work_date[:7]

    for p_idx, chunk in enumerate(chunks):
        if p_idx > 0:
            c.showPage()
        c.setFont("Times-Bold", 13.5)
        c.drawCentredString(W / 2, H - 30, "D.O.T./I.C.C. CYLINDER VISUAL INSPECTION AND REQUALIFICATION LOG")

        c.setFont("Times-Roman", 8.5)
        c.drawString(x0, H - 46, "Company:")
        c.setFont("Helvetica-Bold", 8.8)
        c.drawString(x0 + 44, H - 46, "Weber's Tank Testing")
        c.line(x0 + 42, H - 48, x0 + 195, H - 48)

        c.setFont("Times-Roman", 8.5)
        c.drawString(x0 + 235, H - 46, "Service Unit #:")
        c.setFont("Helvetica", 8.5)
        c.drawString(x0 + 296, H - 46, "Baden Shop")
        c.line(x0 + 294, H - 48, x0 + 410, H - 48)

        has_propane = any("PROPANE" in str(r.get("notes", "")).upper() or str(r.get("dot_spec", "")).upper().startswith("4B") or str(r.get("gas_service", "")).upper() == "LPG" for r in chunk if r)
        rin_display = f"{visual_rin} (Propane/Visual) / {dot_rin}" if has_propane else f"{dot_rin}  |  Visual ID: {visual_rin}"
        c.setFont("Times-Roman", 8.5)
        c.drawString(x0 + 445, H - 46, "RIN:")
        c.setFont("Helvetica-Bold", 8.8)
        c.drawString(x0 + 468, H - 46, rin_display)
        c.line(x0 + 466, H - 48, xs[-1], H - 48)

        c.setFont("Times-Roman", 8.5)
        c.drawString(x0, H - 61, "Plant or City/State:")
        c.setFont("Helvetica", 8.5)
        c.drawString(x0 + 76, H - 61, SHOP_ADDRESS)
        c.line(x0 + 74, H - 63, x0 + 280, H - 63)

        all_adm = bool(chunk) and all("ADMIN" in str(r.get("operator_initials", "")) or "ADMIN" in str(r.get("tested_by", "")) for r in chunk if r)
        mgr_txt = "ADMIN DIAGNOSTIC" if all_adm else PRIMARY_MANAGER
        c.setFont("Times-Roman", 8.5)
        c.drawString(x0 + 400, H - 61, "Responsible Manager:")
        c.setFont("Times-Italic", 9.5)
        c.drawString(x0 + 488, H - 61, mgr_txt)
        c.line(x0 + 484, H - 63, xs[-1], H - 63)

        grp_top = H - 72
        hdr_top = grp_top - 13
        hdr_bot = hdr_top - 22

        c.setLineWidth(0.65)
        c.setFillColor(colors.Color(0.95, 0.95, 0.95))
        c.rect(xs[0], grp_top - 13, xs[15] - xs[0], 13, fill=1, stroke=1)
        c.rect(xs[0], hdr_bot, xs[-1] - xs[0], hdr_top - hdr_bot, fill=1, stroke=1)
        c.setFillColor(colors.black)

        c.line(xs[6], grp_top, xs[6], hdr_top)
        c.line(xs[8], grp_top, xs[8], hdr_top)
        c.line(xs[15], grp_top, xs[15], hdr_top)
        c.setFont("Times-Bold", 6.8)
        c.drawCentredString((xs[0] + xs[6]) / 2, hdr_top + 3.5, "CYLINDER IDENTIFICATION")
        c.drawCentredString((xs[6] + xs[8]) / 2, hdr_top + 3.5, "PROTECTIVE COATING")
        c.drawCentredString((xs[8] + xs[15]) / 2, hdr_top + 3.5, "CYLINDER CHECKED FOR")

        c.setFont("Times-Bold", 5.8)
        for i, h_txt in enumerate(headers):
            cx = (xs[i] + xs[i + 1]) / 2
            parts = h_txt.split(" ")
            if len(parts) >= 2 and len(h_txt) > 7:
                mid = len(parts) // 2
                c.drawCentredString(cx, hdr_bot + 12, " ".join(parts[:mid]))
                c.drawCentredString(cx, hdr_bot + 4.5, " ".join(parts[mid:]))
            else:
                c.drawCentredString(cx, hdr_bot + 8, h_txt)
            c.line(xs[i], hdr_top, xs[i], hdr_bot)
        c.line(xs[-1], hdr_top, xs[-1], hdr_bot)

        cy = hdr_bot
        rh = 23.5
        for r_i in range(14):
            r = chunk[r_i] if r_i < len(chunk) else None
            c.setLineWidth(0.5)
            c.rect(x0, cy - rh, xs[-1] - x0, rh, fill=0, stroke=1)
            for xl in xs[1:-1]:
                c.line(xl, cy, xl, cy - rh)
            if r:
                c.setFont("Helvetica", 6.9)
                r_my = insp_my
                if r.get("work_date") and "-" in str(r.get("work_date")):
                    wp = str(r["work_date"]).split("-")
                    if len(wp) == 3:
                        r_my = f"{wp[1]}/{wp[0][-2:]}"
                vals = [
                    (r.get("serial_number") or "")[:12],
                    (r.get("mfg_date") or "").lstrip("'")[:7],
                    _fmt_spec_psi(r.get("dot_spec", ""), int(r.get("service_psi") or 0))[:10],
                    (r.get("mfg_id") or "")[:7],
                    (r.get("customer_name") or "")[:15],
                    (r.get("physical_size") or "")[:8],
                    (r.get("coating_type") or "Painted")[:7],
                    (r.get("coating_condition") or "Good")[:8],
                    r.get("chk_corrosion", "OK"),
                    r.get("chk_dents_dings", "OK"),
                    r.get("chk_cuts_gouges", "OK"),
                    r.get("chk_leaks", "OK"),
                    r.get("chk_fire_damage", "OK"),
                    r.get("chk_bulges", "OK"),
                    r.get("chk_neck_defects", "OK"),
                    "SC" if r.get("hydro_disposition") == "D" else r.get("visual_disposition", "OK"),
                    r_my,
                    "Yes" if (r.get("relief_valve_changed") == "Yes" or r.get("addon_burst_disc")) else "No",
                    "ADM*" if "ADMIN" in str(r.get("operator_initials", "")) else PRIMARY_INITIALS,
                ]
                for i, v in enumerate(vals):
                    c.drawCentredString((xs[i] + xs[i + 1]) / 2, cy - 15, str(v))
            cy -= rh

        c.setFont("Times-Bold", 7.2)
        c.drawString(
            x0, cy - 13,
            "Original - Retain in Service Unit files for five years after last inspection date listed     Disposition Code:   OK - Return to Service     SC - Scrap (Condemned)     RM - Removed from Service",
        )

    c.save()
    return buf.getvalue()
def _draw_mini_rin_badge(
    c: canvas.Canvas,
    bx: float,
    by: float,
    bw: float,
    bh: float,
    month_str: str,
    year_str: str,
    rin_chars: tuple[str, str, str, str],
) -> None:
    """Draws a compact 49 CFR §180.213 RIN badge (ideal for 2-up Paintball / Pony HPA bottles)."""
    r_tl, r_tr, r_br, r_bl = rin_chars
    c.setLineWidth(1.0)
    c.roundRect(bx, by, bw, bh, 4.5, stroke=1, fill=0)

    side_w = bw * 0.245
    side_h = 23.0
    side_bot = by + 19.0

    mid_w = bw * 0.34
    mid_h = 34.0
    mid_bot = by + 13.5

    m_x = bx + bw * 0.05
    rin_x = bx + (bw - mid_w) / 2.0
    y_x = bx + bw * 0.95 - side_w

    c.setLineWidth(0.8)
    c.rect(m_x, side_bot, side_w, side_h, stroke=1, fill=0)
    c.rect(rin_x, mid_bot, mid_w, mid_h, stroke=1, fill=0)
    c.rect(y_x, side_bot, side_w, side_h, stroke=1, fill=0)

    c.setFont("Helvetica-Bold", 10.5)
    c.drawCentredString(m_x + side_w / 2.0, side_bot + side_h / 2.0 - 3.8, month_str)
    c.drawCentredString(y_x + side_w / 2.0, side_bot + side_h / 2.0 - 3.8, year_str)

    c.setFont("Helvetica-Bold", 9.6)
    cx_rin = rin_x + mid_w / 2.0
    cy_rin = mid_bot + mid_h / 2.0
    c.drawCentredString(cx_rin - 6.6, cy_rin + 2.8, r_tl)
    c.drawCentredString(cx_rin + 6.6, cy_rin + 2.8, r_tr)
    c.drawCentredString(cx_rin - 6.6, cy_rin - 9.8, r_bl)
    c.drawCentredString(cx_rin + 6.6, cy_rin - 9.8, r_br)

    c.setFont("Helvetica-Bold", 4.7)
    c.drawCentredString(m_x + side_w / 2.0, by + 11.5, "MONTH")
    c.drawCentredString(y_x + side_w / 2.0, by + 11.5, "YEAR")
    c.drawCentredString(cx_rin, by + 5.0, "RIN / LIC.")


def _draw_webers_shop_sticker(
    c: canvas.Canvas,
    lx: float,
    ly: float,
    lw: float,
    lh: float,
    month_str: str,
    year_str: str,
    license_code: str,
    is_propane_visual: bool = False,
) -> None:
    """
    Draws Ryan Pearce's exact physical shop retest sticker layout:
      - Hydro Retest ('B386' + LICENSE NUMBER + MM  YY + REQUALIFICATION DATE)
      - Propane Visual ('V143159' + MM  YY  E + REQUALIFICATION DATE)
    Scales cleanly to 15-up (3x5, 2.0" tall), 18-up (3x6, 1.67" tall), or 30-up (3x10, 1.0" tall).
    """
    cx = lx + lw / 2.0
    # Reference height is 2.0" (144 pt) for 15-up; clamp font scaling gently so 30-up stays super crisp
    sy = lh / 144.0
    fs = max(0.54, min(1.05, sy ** 0.88))

    c.setFillColor(colors.black)
    c.setStrokeColor(colors.black)

    # Vertical anchor positions (from top of label downward, proportional to label height lh)
    top_y = ly + lh
    c.setFont("Helvetica-Bold", 9.8 * fs)
    c.drawCentredString(cx, top_y - 17.5 * sy, "WEBER'S TANK TESTING")

    c.setFont("Helvetica-Bold", 6.8 * fs)
    c.drawCentredString(cx, top_y - 28.5 * sy, "3086 LEGIONVILLE ROAD BADEN, PA")
    c.drawCentredString(cx, top_y - 37.5 * sy, "15005")
    c.setFont("Helvetica-Bold", 7.3 * fs)
    c.drawCentredString(cx, top_y - 47.0 * sy, "(724)-266-8008")

    if is_propane_visual:
        # Propane Visual Requalification Sticker: V143159 + MM  YY  E
        c.setFont("Helvetica-Bold", 15.8 * fs)
        c.drawCentredString(cx, top_y - 67.5 * sy, license_code or "V143159")

        m_cx = cx - lw * 0.21
        y_cx = cx + lw * 0.03
        e_cx = cx + lw * 0.26

        c.setFont("Helvetica-Bold", 13.5 * fs)
        c.drawCentredString(m_cx, top_y - 90.0 * sy, month_str)
        c.drawCentredString(y_cx, top_y - 90.0 * sy, year_str)

        c.setFont("Helvetica-Bold", 5.2 * fs)
        c.drawCentredString(m_cx, top_y - 101.5 * sy, "MONTH")
        c.drawCentredString(y_cx, top_y - 101.5 * sy, "YEAR")

        c.setFont("Helvetica-Bold", 23.0 * fs)
        c.drawCentredString(e_cx, top_y - 99.5 * sy, "E")

        rule_y = top_y - 106.5 * sy
        c.setLineWidth(max(0.8, 1.15 * fs))
        c.line(lx + lw * 0.11, rule_y, lx + lw * 0.89, rule_y)

        c.setFont("Helvetica-Bold", 7.2 * fs)
        c.drawCentredString(cx, top_y - 118.5 * sy, "REQUALIFICATION DATE")
    else:
        # Hydrostatic Retest Sticker: B386 + LICENSE NUMBER + MM  YY
        c.setFont("Helvetica-Bold", 17.5 * fs)
        c.drawCentredString(cx, top_y - 66.5 * sy, license_code or "B386")

        c.setFont("Helvetica-Bold", 5.3 * fs)
        c.drawCentredString(cx, top_y - 74.5 * sy, "LICENSE NUMBER")

        m_cx = cx - lw * 0.165
        y_cx = cx + lw * 0.165

        c.setFont("Helvetica-Bold", 14.2 * fs)
        c.drawCentredString(m_cx, top_y - 93.0 * sy, month_str)
        c.drawCentredString(y_cx, top_y - 93.0 * sy, year_str)

        c.setFont("Helvetica-Bold", 5.3 * fs)
        c.drawCentredString(m_cx, top_y - 103.5 * sy, "MONTH")
        c.drawCentredString(y_cx, top_y - 103.5 * sy, "YEAR")

        rule_y = top_y - 108.5 * sy
        c.setLineWidth(max(0.8, 1.15 * fs))
        c.line(lx + lw * 0.11, rule_y, lx + lw * 0.89, rule_y)

        c.setFont("Helvetica-Bold", 7.3 * fs)
        c.drawCentredString(cx, top_y - 120.5 * sy, "REQUALIFICATION DATE")


def build_rin_stickers_pdf(
    work_date: str,
    count: int = 15,
    dot_rin: str = "B386",
    visual_rin: str = "V143159",
    cols: int = 3,
    rows: int = 5,
    skip_slots: int = 0,
    compact: bool = False,
    sticker_type: str = "",
    layout: str = "15_up",
    month_override: str = "",
    year_override: str = "",
) -> bytes:
    """
    Printable Retest & Visual Requalification Sticker Sheets:
      - sticker_type='hydro': Weber's B386 Hydro Retest Stickers (matches Ryan's sample hydro sheet)
      - sticker_type='propane': Weber's V143159 + 'E' Propane Visual Stickers (matches Ryan's sample propane sheet)
      - sticker_type='mini_hpa' (or compact=True): 2-up Mini Paintball HPA Collar Badges on Avery 5160
      - layout: '15_up' (3x5, 2" tall), '18_up' (3x6, 1.67" tall), or '30_up' (3x10 Avery 5160, 1" tall)
    """
    eff_type = (sticker_type or ("mini_hpa" if compact else "hydro")).strip().lower()
    eff_layout = (layout or "15_up").strip().lower()
    if eff_type == "mini_hpa":
        cols, rows = 3, 10
    elif eff_layout == "30_up":
        cols, rows = 3, 10
    elif eff_layout == "18_up":
        cols, rows = 3, 6
    elif eff_layout == "10_up":
        cols, rows = 2, 5
    else:
        cols, rows = 3, 5

    buf = io.BytesIO()
    W, H = letter
    c = canvas.Canvas(buf, pagesize=letter)
    title_kind = (
        f"Propane Visual ({visual_rin} E)"
        if eff_type == "propane"
        else ("Mini Paintball HPA (2-Up)" if eff_type == "mini_hpa" else f"Hydro Retest ({dot_rin})")
    )
    _set_pdf_meta(c, f"Weber's Tank Testing {title_kind} Stickers - {work_date}")

    try:
        dt = datetime.strptime(work_date, "%Y-%m-%d")
        month_str = f"{dt.month:02d}"
        year_str = dt.strftime("%y")
    except Exception:
        month_str, year_str = "11", "26"

    if month_override.strip():
        m_raw = month_override.strip()
        month_str = f"{int(m_raw):02d}" if m_raw.isdigit() and 1 <= int(m_raw) <= 12 else m_raw[:2]
    if year_override.strip():
        y_raw = year_override.strip()
        year_str = y_raw[-2:] if len(y_raw) >= 2 else y_raw

    rin = (dot_rin or "B386").ljust(4)
    rin_chars = (rin[0], rin[1], rin[2], rin[3])

    if eff_type == "mini_hpa":
        margin_x = 0.1875 * inch
        margin_y = 0.5 * inch
        pitch_x = 2.75 * inch
        label_w = 2.625 * inch
        label_h = (H - 2 * margin_y) / 10.0
        per_page = 30
        needed = max(1, count)
        skipped = max(0, skip_slots)
        physical_slots_needed = (needed + 1) // 2
        total_slots = skipped + physical_slots_needed
        drawn = 0

        for slot in range(total_slots):
            if slot > 0 and slot % per_page == 0:
                c.showPage()
            if slot < skipped:
                continue
            local = slot % per_page
            r_idx = local // 3
            c_idx = local % 3
            lx = margin_x + c_idx * pitch_x
            ly = H - margin_y - (r_idx + 1) * label_h
            half_w = label_w / 2.0
            pad_x, pad_y = 3.5, 4.5
            bw, bh = half_w - 2 * pad_x, label_h - 2 * pad_y

            if drawn < needed:
                _draw_mini_rin_badge(c, lx + pad_x, ly + pad_y, bw, bh, month_str, year_str, rin_chars)
                drawn += 1
            c.saveState()
            c.setDash(2, 2)
            c.setStrokeColor(colors.Color(0.65, 0.65, 0.65))
            c.setLineWidth(0.5)
            c.line(lx + half_w, ly + 3, lx + half_w, ly + label_h - 3)
            c.restoreState()
            if drawn < needed:
                _draw_mini_rin_badge(c, lx + half_w + pad_x, ly + pad_y, bw, bh, month_str, year_str, rin_chars)
                drawn += 1

        c.save()
        return buf.getvalue()

    # Sheet geometry for 10-up (2x5), 15-up (3x5), 18-up (3x6), and 30-up (3x10)
    if cols == 2:
        margin_x = 0.15625 * inch
        margin_y = 0.5 * inch
        label_w = 4.0 * inch
        pitch_x = 4.1875 * inch
    elif rows == 10:
        margin_x = 0.1875 * inch
        margin_y = 0.5 * inch
        pitch_x = 2.75 * inch
        label_w = 2.625 * inch
    else:
        # Butt-cut 3-column 2.625" wide retest sticker stock (matches Ryan's sample sheets)
        margin_x = 0.3125 * inch
        margin_y = 0.5 * inch
        label_w = (W - 2 * margin_x) / cols
        pitch_x = label_w

    label_h = (H - 2 * margin_y) / max(1, rows)
    per_page = cols * rows
    needed = max(1, count)
    skipped = max(0, skip_slots)
    total_slots = skipped + needed
    is_prop = eff_type == "propane"
    lic = (visual_rin or "V143159") if is_prop else (dot_rin or "B386")

    for slot in range(total_slots):
        if slot > 0 and slot % per_page == 0:
            c.showPage()
        if slot < skipped:
            continue
        local = slot % per_page
        r_idx = local // cols
        c_idx = local % cols
        lx = margin_x + c_idx * pitch_x
        ly = H - margin_y - (r_idx + 1) * label_h
        _draw_webers_shop_sticker(c, lx, ly, label_w, label_h, month_str, year_str, lic, is_propane_visual=is_prop)

    c.save()
    return buf.getvalue()


def build_valve_tags_pdf(work_date: str, records: list[dict], dot_rin: str = "B386") -> bytes:
    """Full-Page Shop Clipboard Match Sheet for Active Reusable Valve Tags."""
    buf = io.BytesIO()
    W, H = letter
    c = canvas.Canvas(buf, pagesize=letter)
    _set_pdf_meta(c, f"Active Valve Tag Clipboard Sheet - {work_date}")

    headers = ["Tag #", "Cylinder Serial #", "Customer / Owner", "Spec & PSI", "Size", "Parts / Relief", "Reinstalled"]
    widths = [54, 104, 142, 76, 56, 68, 58]
    x0 = 26
    xs = [x0]
    for w in widths:
        xs.append(xs[-1] + w)

    active_rows = [r for r in records if r.get("valve_tag") and not r.get("valve_reinstalled")]
    rows_per_page = 25
    total_slots = max(rows_per_page, ((len(active_rows) + rows_per_page - 1) // rows_per_page) * rows_per_page)
    padded_rows = active_rows + [None] * (total_slots - len(active_rows))

    for p_idx in range(0, total_slots, rows_per_page):
        if p_idx > 0:
            c.showPage()

        c.setFont("Helvetica-Bold", 14.0)
        c.drawString(x0, H - 40, "WEBER'S TANK TESTING - ACTIVE VALVE TAG SHEET")
        c.setFont("Helvetica", 9.0)
        c.drawString(
            x0, H - 55,
            f"Work Date: {work_date}  |  DOT RIN: {dot_rin}  |  Sign-Off: {PRIMARY_MANAGER} ({PRIMARY_INITIALS})  |  Active Tags: {len(active_rows)}",
        )

        y = H - 68
        c.setFillColor(colors.Color(0.93, 0.95, 0.94))
        c.rect(x0, y - 22, xs[-1] - x0, 22, fill=1, stroke=1)
        c.setFillColor(colors.black)
        c.setFont("Helvetica-Bold", 8.2)
        for i, h_txt in enumerate(headers):
            c.drawCentredString((xs[i] + xs[i + 1]) / 2, y - 15, h_txt)
            c.line(xs[i], y, xs[i], y - 22)
        c.line(xs[-1], y, xs[-1], y - 22)

        cy = y - 22
        rh = 24.5
        chunk = padded_rows[p_idx : p_idx + rows_per_page]
        for r in chunk:
            c.setLineWidth(0.55)
            c.rect(x0, cy - rh, xs[-1] - x0, rh, fill=0, stroke=1)
            for xl in xs[1:-1]:
                c.line(xl, cy, xl, cy - rh)
            if r:
                c.setFont("Helvetica-Bold", 9.5)
                c.drawCentredString((xs[0] + xs[1]) / 2, cy - 16, f"#{r.get('valve_tag')}")
                c.drawCentredString((xs[1] + xs[2]) / 2, cy - 16, str(r.get("serial_number") or "")[:15])
                c.setFont("Helvetica", 8.5)
                c.drawString(xs[2] + 4, cy - 16, str(r.get("customer_name") or "")[:23])
                c.drawCentredString(
                    (xs[3] + xs[4]) / 2, cy - 16,
                    _fmt_spec_psi(r.get("dot_spec", ""), int(r.get("service_psi") or 0))[:12],
                )
                c.drawCentredString((xs[4] + xs[5]) / 2, cy - 16, str(r.get("physical_size") or "")[:9])
                p_list = []
                if r.get("addon_oring"):
                    p_list.append("O-Ring")
                if r.get("addon_burst_disc") or r.get("relief_valve_changed") == "Yes":
                    p_list.append("Burst")
                c.drawCentredString((xs[5] + xs[6]) / 2, cy - 16, ",".join(p_list) if p_list else "Std")
            c.setFont("Helvetica", 8.5)
            c.drawCentredString((xs[6] + xs[7]) / 2, cy - 16, "[     ]")
            cy -= rh

        c.setFont("Helvetica-Oblique", 7.8)
        c.drawString(
            x0, cy - 14,
            "Mark [ X ] when the matching valve is reinstalled onto its cylinder so the tag returns to the bench pool.",
        )

    c.save()
    return buf.getvalue()


def build_backroom_poster_pdf(
    checkin_url: str = "https://weberstanktesting.com/#dropoff",
    phone: str = "724-266-8008",
    dot_rin: str = "B386",
) -> bytes:
    """Printable 8.5x11 Laminated Wall Sign for 24/7 Backroom Cylinder Drop-Off & Pickup."""
    buf = io.BytesIO()
    W, H = letter
    c = canvas.Canvas(buf, pagesize=letter)
    _set_pdf_meta(c, "Weber's Tank Testing - 24/7 Loading Dock Drop-Off & Pickup QR Sign")

    c.setFillColor(colors.HexColor("#0f291e"))
    c.rect(0, H - 126, W, 126, fill=1, stroke=0)
    c.setFillColor(colors.HexColor("#f59e0b"))
    c.rect(0, H - 132, W, 6, fill=1, stroke=0)

    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 24)
    _stamp_pdf_crest(c, 26, H - 111, 90)
    hdr_cx = 356
    c.drawCentredString(hdr_cx, H - 46, "WEBER'S TANK TESTING")
    c.setFont("Helvetica-Bold", 10.6)
    c.setFillColor(colors.HexColor("#fde68a"))
    c.drawCentredString(hdr_cx, H - 67, f"{SHOP_ADDRESS.upper()}  |  U.S. D.O.T. CERTIFIED FACILITY")
    c.setFillColor(colors.HexColor("#e2e8f0"))
    c.setFont("Helvetica-Bold", 8.8)
    c.drawCentredString(hdr_cx, H - 86, f"Hours: {SHOP_HOURS}")
    c.setFont("Helvetica", 9.0)
    c.drawCentredString(hdr_cx, H - 105, "SCBA  |  EMS Oxygen  |  Propane Visual  |  Paintball HPA  |  Industrial Gas  |  CO2  |  Scuba")

    c.setFillColor(colors.HexColor("#0f172a"))
    c.setFont("Helvetica-Bold", 21)
    c.drawCentredString(W / 2, H - 166, "24/7 LOADING DOCK DROP-OFF & PICKUP")
    c.setFont("Helvetica", 11.5)
    c.setFillColor(colors.HexColor("#475569"))
    c.drawCentredString(W / 2, H - 186, "Dropping off tanks or picking up a completed batch? Scan below in 20 seconds:")

    qr_box_size = 232
    qr_x = (W - qr_box_size) / 2
    qr_y = H - 440
    c.setStrokeColor(colors.HexColor("#1b4332"))
    c.setLineWidth(2.5)
    c.setFillColor(colors.white)
    c.roundRect(qr_x, qr_y, qr_box_size, qr_box_size, 14, fill=1, stroke=1)

    qr_widget = qr.QrCodeWidget(checkin_url)
    bounds = qr_widget.getBounds()
    qw = bounds[2] - bounds[0]
    qh = bounds[3] - bounds[1]
    draw_sz = 204.0
    d = Drawing(draw_sz, draw_sz, transform=[draw_sz / qw, 0, 0, draw_sz / qh, 0, 0])
    d.add(qr_widget)
    renderPDF.draw(d, c, qr_x + (qr_box_size - draw_sz) / 2, qr_y + (qr_box_size - draw_sz) / 2)

    c.setFillColor(colors.HexColor("#1b4332"))
    c.setFont("Helvetica-Bold", 12)
    c.drawCentredString(W / 2, qr_y - 18, "weberstanktesting.com")

    box_x = 48
    box_w = W - 96
    box_y = 130
    box_h = 178
    c.setFillColor(colors.HexColor("#f8fafc"))
    c.setStrokeColor(colors.HexColor("#cbd5e1"))
    c.setLineWidth(1.2)
    c.roundRect(box_x, box_y, box_w, box_h, 10, fill=1, stroke=1)

    steps = [
        ("1. SCAN QR CODE WITH YOUR PHONE CAMERA", "Opens our instant check-in & verified pickup form - no app download needed."),
        ("2. ENTER NAME / DEPT, CELL #, & TANK DETAILS", "Receive a live Ticket # (BR-XXXX) and get texted when your batch is staged at our Loading Dock."),
        ("3. PLACE CYLINDERS NEATLY ON LOADING DOCK", "Every removed valve is numbered & matched back to its cylinder serial #."),
    ]
    sy = box_y + box_h - 32
    for title_txt, sub_txt in steps:
        c.setFillColor(colors.HexColor("#0f291e"))
        c.setFont("Helvetica-Bold", 11.5)
        c.drawString(box_x + 18, sy, title_txt)
        c.setFillColor(colors.HexColor("#334155"))
        c.setFont("Helvetica", 10.2)
        c.drawString(box_x + 18, sy - 15, sub_txt)
        sy -= 52

    c.setFillColor(colors.HexColor("#fffbeb"))
    c.setStrokeColor(colors.HexColor("#f59e0b"))
    c.roundRect(box_x, 66, box_w, 48, 8, fill=1, stroke=1)
    c.setFillColor(colors.HexColor("#92400e"))
    c.setFont("Helvetica-Bold", 10.2)
    c.drawCentredString(W / 2, 93, "SAFETY NOTE: Never leave actively leaking cylinders on the Loading Dock.")
    c.setFont("Helvetica", 9.8)
    c.drawCentredString(W / 2, 77, f"Questions, department batches, or payment info? Call or Text Shop: {phone}")

    c.setFillColor(colors.HexColor("#64748b"))
    c.setFont("Helvetica-Bold", 9.2)
    c.drawCentredString(W / 2, 36, f"Weber's Tank Testing  |  {SHOP_ADDRESS}  |  Manager: {PRIMARY_MANAGER}  |  {phone}")

    c.save()
    return buf.getvalue()


def build_invoice_pdf(invoice: dict, items: list[dict], phone: str = "724-266-8008", dot_rin: str = "B386", customer_profile: dict | None = None) -> bytes:
    """Clean Printable Customer Invoice PDF with Address, Hours, Multi-Page & Payment Method Support."""
    buf = io.BytesIO()
    W, H = letter
    c = canvas.Canvas(buf, pagesize=letter)
    inv_no = invoice.get("invoice_no") or "INVOICE"
    _set_pdf_meta(c, f"{inv_no} - {invoice.get('customer_name', '')} | Weber's Tank Testing")
    _stamp_pdf_crest(c, 40, H - 96, 58)

    def draw_table_header(top_y: float) -> float:
        c.setFillColor(colors.Color(0.93, 0.95, 0.94))
        c.rect(45, top_y - 20, W - 90, 20, fill=1, stroke=1)
        c.setFillColor(colors.black)
        c.setFont("Helvetica-Bold", 9.5)
        c.drawString(52, top_y - 14, "Description / Cylinder Serial & Service")
        c.drawCentredString(W - 185, top_y - 14, "Qty")
        c.drawRightString(W - 115, top_y - 14, "Unit Price")
        c.drawRightString(W - 52, top_y - 14, "Line Total")
        return top_y - 20

    c.setFont("Helvetica-Bold", 17.5)
    c.drawString(106, H - 48, "WEBER'S TANK TESTING")
    c.setFont("Helvetica-Bold", 9.2)
    c.drawString(106, H - 63, f"{SHOP_ADDRESS}  |  Phone: {phone}")
    c.setFont("Helvetica", 8.8)
    c.drawString(106, H - 76, f"U.S. D.O.T. Certified Cylinder Testing  |  Manager: {PRIMARY_MANAGER}")
    c.setFillColor(colors.HexColor("#475569"))
    c.setFont("Helvetica", 8.1)
    c.drawString(106, H - 89, f"Hours: {SHOP_HOURS}")
    c.setFillColor(colors.black)

    c.setFont("Helvetica-Bold", 16)
    c.drawRightString(W - 45, H - 48, "INVOICE")
    c.setFont("Helvetica", 10)
    c.drawRightString(W - 45, H - 64, f"Invoice #: {inv_no}")
    c.drawRightString(W - 45, H - 78, f"Invoice Date: {invoice.get('invoice_date')}")
    status_str = str(invoice.get("status") or "DRAFT")
    pay_method = str(invoice.get("payment_method") or "").strip()
    c.drawRightString(W - 45, H - 92, f"Status: {status_str}" + (f" ({pay_method})" if pay_method else ""))

    c.line(45, H - 106, W - 45, H - 106)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(45, H - 121, f"Bill To: {invoice.get('customer_name')}")
    prof = customer_profile or {}
    if prof.get("payment_terms") == "NET_30":
        c.setFont("Helvetica-Bold", 9.5)
        c.drawRightString(W - 45, H - 121, "Terms: Net-30 Dept / Fleet")
    c.setFont("Helvetica", 9.4)
    addr_bits = []
    if prof.get("contact_person"):
        addr_bits.append(f"Attn: {prof['contact_person']}")
    has_real_loc = bool((prof.get("street_address") or "").strip() or (prof.get("city") or "").strip())
    if has_real_loc:
        city_st = " ".join(filter(None, [prof.get("city"), prof.get("state"), prof.get("zip_code")])).strip()
        full_addr = ", ".join(filter(None, [prof.get("street_address"), city_st])).strip()
        if full_addr:
            addr_bits.append(full_addr)
    if prof.get("email"):
        addr_bits.append(str(prof["email"]))
    y_svc = H - 136
    if addr_bits:
        c.drawString(45, H - 135, "   |   ".join(addr_bits)[:92])
        y_svc = H - 149
    cust_ph = str(invoice.get("customer_phone") or prof.get("phone") or "").strip()
    svc_line = f"Service / Test Date: {invoice.get('work_date')}" + (f"   |   Phone: {cust_ph}" if cust_ph else "")
    c.drawString(45, y_svc, svc_line)

    cy = draw_table_header(H - 166)
    c.setFont("Helvetica", 9.5)
    for it in items:
        if cy - 20 < 120:
            c.showPage()
            c.setFont("Helvetica-Bold", 11)
            c.drawString(45, H - 42, f"WEBER'S TANK TESTING - {inv_no} (Continued)")
            cy = draw_table_header(H - 56)
            c.setFont("Helvetica", 9.5)
        cy -= 20
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
    tax_lbl = "Tax (Exempt):" if float(invoice.get("tax_rate", 0)) == 0 else f"Tax ({invoice.get('tax_rate', 0)}%):"
    c.drawRightString(W - 125, cy, tax_lbl)
    c.drawRightString(W - 52, cy, f"${float(invoice.get('tax_amount', 0)):.2f}")
    cy -= 20
    c.setFont("Helvetica-Bold", 12.5)
    lbl_due = "Total Paid:" if status_str == "PAID" else "Total Due:"
    c.drawRightString(W - 125, cy, lbl_due)
    c.drawRightString(W - 52, cy, f"${float(invoice.get('total_amount', 0)):.2f}")

    if invoice.get("paid_at"):
        c.setFont("Helvetica-Bold", 9.5)
        c.setFillColor(colors.HexColor("#15803d"))
        c.drawString(45, cy, f"PAID IN FULL - {invoice['paid_at']}" + (f" via {pay_method}" if pay_method else ""))
        c.setFillColor(colors.black)

    if invoice.get("notes"):
        c.setFont("Helvetica", 9.5)
        c.drawString(45, cy - 26, f"Notes: {invoice['notes']}")

    c.setFillColor(colors.HexColor("#64748b"))
    c.setFont("Helvetica", 8.5)
    c.drawCentredString(W / 2, 34, f"Thank you for trusting Weber's Tank Testing  |  {SHOP_ADDRESS}  |  {phone}")

    c.save()
    return buf.getvalue()


def build_shelf_claim_slip_pdf(
    customer_name: str,
    customer_phone: str,
    records: list[dict],
    invoice: dict | None = None,
    payment_terms: str = "DUE_AT_PICKUP",
    phone: str = "724-266-8008",
    dot_rin: str = "B386",
    pickup_url: str = "https://weberstanktesting.com/#dropoff",
) -> bytes:
    """Printable Zone E Backroom Pickup Shelf Claim Slip & Batch Test Summary."""
    buf = io.BytesIO()
    W, H = letter
    c = canvas.Canvas(buf, pagesize=letter)
    _set_pdf_meta(c, f"Loading Dock Pickup Claim Slip - {customer_name}")

    total_cnt = len(records)
    pass_cnt = sum(1 for r in records if r.get("hydro_disposition") == "A")
    fail_cnt = total_cnt - pass_cnt

    # Bold Zone E Header Banner (easy to read across the backroom)
    c.setFillColor(colors.HexColor("#0f291e"))
    c.rect(36, H - 108, W - 72, 72, fill=1, stroke=0)
    _stamp_pdf_crest(c, W - 104, H - 103, 60)
    c.setFillColor(colors.HexColor("#fbbf24"))
    c.setFont("Helvetica-Bold", 10)
    c.drawString(50, H - 54, "24/7 LOADING DOCK PICKUP  •  WEBER'S TANK TESTING")
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 22)
    c.drawString(50, H - 80, (customer_name or "CUSTOMER BATCH")[:32].upper())
    c.setFont("Helvetica", 9.5)
    c.setFillColor(colors.HexColor("#cbd5e1"))
    c.drawString(50, H - 97, f"Phone: {customer_phone or 'On File'}   |   Shop: {phone}   |   {SHOP_ADDRESS}")

    # Summary KPI Boxes + Payment Status
    y_box = H - 170
    c.setStrokeColor(colors.HexColor("#cbd5e1"))
    c.setFillColor(colors.HexColor("#f8fafc"))
    c.roundRect(36, y_box, 290, 50, 8, fill=1, stroke=1)
    c.setFillColor(colors.HexColor("#0f172a"))
    c.setFont("Helvetica-Bold", 11)
    c.drawString(48, y_box + 31, f"BATCH TOTAL: {total_cnt} CYLINDER(S)")
    c.setFont("Helvetica-Bold", 9.5)
    c.setFillColor(colors.HexColor("#15803d"))
    c.drawString(48, y_box + 13, f"PASSED (Code A): {pass_cnt}")
    if fail_cnt > 0:
        c.setFillColor(colors.HexColor("#b91c1c"))
        c.drawString(180, y_box + 13, f"CONDEMNED (Code D): {fail_cnt}")

    # Billing Box
    c.setFillColor(colors.HexColor("#ecfdf5") if (invoice and invoice.get("status") == "PAID") else colors.HexColor("#fffbeb"))
    c.setStrokeColor(colors.HexColor("#10b981") if (invoice and invoice.get("status") == "PAID") else colors.HexColor("#f59e0b"))
    c.roundRect(338, y_box, W - 374, 50, 8, fill=1, stroke=1)
    c.setFillColor(colors.HexColor("#0f172a"))
    c.setFont("Helvetica-Bold", 10.5)
    if invoice:
        inv_st = str(invoice.get("status") or "DRAFT").upper()
        amt = float(invoice.get("total_amount") or 0.0)
        c.drawString(350, y_box + 31, f"INVOICE {invoice.get('invoice_no')}: {'PAID IN FULL' if inv_st == 'PAID' else f'DUE ${amt:.2f}'}")
        c.setFont("Helvetica", 9)
        c.drawString(350, y_box + 13, f"Status: {inv_st}" + (f" ({invoice.get('payment_method')})" if invoice.get("payment_method") else ""))
    else:
        terms_txt = "NET-30 DEPT / FLEET ACCOUNT" if payment_terms == "NET_30" else "INVOICE PENDING / PAY AT PICKUP"
        c.drawString(350, y_box + 31, terms_txt)
        c.setFont("Helvetica", 9)
        c.drawString(350, y_box + 13, f"Questions on balance? Text/Call {phone}")

    # Cylinder Serial Breakdown Table
    ty = y_box - 18
    c.setFillColor(colors.HexColor("#e2e8f0"))
    c.rect(36, ty - 20, W - 72, 20, fill=1, stroke=1)
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 8.5)
    c.drawString(44, ty - 14, "Serial Number")
    c.drawString(165, ty - 14, "DOT Spec & PSI")
    c.drawString(265, ty - 14, "Test Date")
    c.drawString(340, ty - 14, "Result & Mark")
    c.drawString(445, ty - 14, "Service / Disposition Notes")

    cy = ty - 20
    c.setFont("Helvetica", 8.6)
    for r in records[:20]:
        cy -= 19
        c.setStrokeColor(colors.HexColor("#cbd5e1"))
        c.line(36, cy, W - 36, cy)
        c.setFillColor(colors.black)
        c.setFont("Helvetica-Bold", 8.8)
        c.drawString(44, cy + 5, str(r.get("serial_number") or "")[:18])
        c.setFont("Helvetica", 8.5)
        c.drawString(165, cy + 5, _fmt_spec_psi(r.get("dot_spec", ""), int(r.get("service_psi") or 0))[:15])
        c.drawString(265, cy + 5, str(r.get("work_date") or "")[:10])
        is_pass = r.get("hydro_disposition") == "A"
        c.setFillColor(colors.HexColor("#15803d") if is_pass else colors.HexColor("#b91c1c"))
        c.setFont("Helvetica-Bold", 8.5)
        mark_txt = ("PASS (★ 10-YR)" if r.get("star_10yr") else "PASSED") if is_pass else f"CONDEMN ({r.get('hydro_disposition')})"
        c.drawString(340, cy + 5, mark_txt)
        c.setFillColor(colors.HexColor("#334155"))
        c.setFont("Helvetica", 7.8)
        note_bits = []
        if r.get("addon_oring"):
            note_bits.append("New O-Ring")
        if r.get("addon_burst_disc") or r.get("relief_valve_changed") == "Yes":
            note_bits.append("New Burst Disc")
        if r.get("addon_clean"):
            note_bits.append("Cleaned")
        if r.get("condemn_action"):
            note_bits.append(CONDEMN_LABELS.get(r["condemn_action"], r["condemn_action"]))
        c.drawString(445, cy + 5, (", ".join(note_bits) or "Hydro + 7-Pt Visual")[:28])

    if len(records) > 20:
        cy -= 18
        c.setFont("Helvetica-Oblique", 8.2)
        c.drawString(44, cy + 4, f"+ {len(records) - 20} additional cylinder(s) included in this batch.")

    # Bottom QR Check-Out Box
    foot_y = 48
    c.setFillColor(colors.HexColor("#f8fafc"))
    c.setStrokeColor(colors.HexColor("#94a3b8"))
    c.roundRect(36, foot_y, W - 72, 86, 9, fill=1, stroke=1)

    qr_widget = qr.QrCodeWidget(pickup_url)
    bounds = qr_widget.getBounds()
    qw = bounds[2] - bounds[0]
    qh = bounds[3] - bounds[1]
    d = Drawing(68, 68, transform=[68 / qw, 0, 0, 68 / qh, 0, 0])
    d.add(qr_widget)
    renderPDF.draw(d, c, 48, foot_y + 9)

    c.setFillColor(colors.HexColor("#0f291e"))
    c.setFont("Helvetica-Bold", 10.5)
    c.drawString(128, foot_y + 62, "SELF-SERVE 24/7 PICKUP CHECK-OUT (TAKES 10 SECONDS)")
    c.setFillColor(colors.HexColor("#334155"))
    c.setFont("Helvetica", 9.0)
    c.drawString(128, foot_y + 45, "1. Verify cylinder count above matches your batch staged at our 24/7 Loading Dock.")
    c.drawString(128, foot_y + 30, "2. Scan QR code -> tap 'LOADING DOCK PICK-UP' -> enter your name to confirm pickup.")
    c.setFont("Helvetica-Bold", 8.5)
    c.drawString(128, foot_y + 13, f"NOTE: Testing facility only - cylinders are returned clean, dry & depressurized.  |  {phone}")

    c.save()
    return buf.getvalue()


def build_dropoff_receipt_pdf(
    dropoff: dict,
    phone: str = "724-266-8008",
    dot_rin: str = "B386",
    track_url: str = "https://weberstanktesting.com/#tracker",
) -> bytes:
    """Generates a clean mobile-friendly 1-page Customer Ticket PDF with QR tracker."""
    buf = io.BytesIO()
    W, H = letter
    c = canvas.Canvas(buf, pagesize=letter)

    ticket_no = f"BR-{int(dropoff.get('id') or 0):04d}"
    etype = (dropoff.get("entry_type") or "DROPOFF").upper()
    is_pickup = (etype == "PICKUP_NOTICE")
    is_truck = (etype == "TRUCK_REQUEST")

    c.setFillColorRGB(0.04, 0.13, 0.09)
    c.rect(36, H - 122, W - 72, 86, fill=1, stroke=0)
    _stamp_pdf_crest(c, W - 116, H - 115, 72)
    c.setFillColorRGB(0.98, 0.75, 0.14)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(54, H - 62, "U.S. D.O.T. CERTIFIED TESTING FACILITY")
    c.setFillColorRGB(1, 1, 1)
    c.setFont("Helvetica-Bold", 21)
    c.drawString(54, H - 88, "WEBER'S TANK TESTING")
    c.setFont("Helvetica", 9.5)
    c.drawString(54, H - 106, f"3086 Legionville Rd, Baden, PA 15005  •  Call/Text: {phone}")

    c.setFillColorRGB(0.94, 0.99, 0.96)
    c.setStrokeColorRGB(0.09, 0.64, 0.29)
    c.setLineWidth(2)
    c.roundRect(36, H - 252, W - 72, 114, 10, fill=1, stroke=1)

    c.setFillColorRGB(0.06, 0.37, 0.24)
    c.setFont("Helvetica-Bold", 11)
    lbl = (
        "TRUCK PICKUP & DELIVERY REQUEST TICKET #"
        if is_truck
        else ("LOADING DOCK PICK-UP CONFIRMATION #" if is_pickup else "24/7 LOADING DOCK DROP-OFF TICKET #")
    )
    c.drawCentredString(W / 2, H - 164, lbl)

    c.setFillColorRGB(0.04, 0.13, 0.09)
    c.setFont("Helvetica-Bold", 42)
    c.drawCentredString(W / 2, H - 208, ticket_no)

    est_ready = dropoff.get("promised_date") or "Pending Shop Schedule"
    c.setFillColorRGB(0.09, 0.40, 0.20)
    c.setFont("Helvetica-Bold", 12)
    sub_line = (
        f"Signed Out By: {dropoff.get('picked_up_by') or dropoff.get('customer_name')}"
        if is_pickup
        else f"Estimated Ready / Return Date:  {est_ready}" + ("  (PAID RUSH)" if dropoff.get("rush_requested") else "")
    )
    c.drawCentredString(W / 2, H - 234, sub_line)

    y = H - 288
    c.setFillColorRGB(0.06, 0.09, 0.16)
    rows = [
        ("Logged Date / Time:", str(dropoff.get("created_at") or "")),
        ("Customer / Department:", str(dropoff.get("customer_name") or "")),
    ]
    if dropoff.get("contact_person"):
        rows.append(("Contact / Requested By:", str(dropoff.get("contact_person"))))
    if dropoff.get("pickup_address"):
        rows.append(("Pickup / Delivery Address:", str(dropoff.get("pickup_address"))))
    if dropoff.get("preferred_date"):
        rows.append(("Requested Stop Date:", str(dropoff.get("preferred_date"))))
    if dropoff.get("po_station"):
        rows.append(("Station / PO #:", str(dropoff.get("po_station"))))
    rows.extend([
        ("Contact Phone:", str(dropoff.get("customer_phone") or "")),
        ("Service / Dispatch Mode:", str(dropoff.get("service_requested") or "Hydro + Visual")),
        ("Cylinder Count:", f"{dropoff.get('bottle_count') or 1} Cylinder(s)"),
        ("Cylinder Types / Notes:", str(dropoff.get("tank_summary") or "Standard Hydro + Visual")),
    ])

    for k, v in rows:
        c.setFont("Helvetica-Bold", 10)
        c.drawString(48, y, k)
        c.setFont("Helvetica", 10)
        c.drawString(205, y, v[:68])
        c.setStrokeColorRGB(0.88, 0.91, 0.94)
        c.setLineWidth(0.7)
        c.line(48, y - 6, W - 48, y - 6)
        y -= 23

    qr_y = 78
    c.setFillColorRGB(0.97, 0.98, 0.99)
    c.setStrokeColorRGB(0.75, 0.80, 0.86)
    c.setLineWidth(1)
    c.roundRect(36, qr_y, W - 72, 114, 8, fill=1, stroke=1)

    qr_widget = qr.QrCodeWidget(track_url)
    bounds = qr_widget.getBounds()
    qw = bounds[2] - bounds[0]
    qh = bounds[3] - bounds[1]
    d = Drawing(90, 90, transform=[90.0 / qw, 0, 0, 90.0 / qh, 0, 0])
    d.add(qr_widget)
    renderPDF.draw(d, c, 50, qr_y + 12)

    c.setFillColorRGB(0.06, 0.09, 0.16)
    c.setFont("Helvetica-Bold", 11.5)
    c.drawString(154, qr_y + 84, "Track Live Shop Status Anytime:  weberstanktesting.com")
    c.setFont("Helvetica", 9.6)
    c.drawString(154, qr_y + 65, f"1. Scan QR code (or visit weberstanktesting.com) and enter Ticket {ticket_no}.")
    c.drawString(154, qr_y + 48, "2. We will text/call your phone to confirm ready status or truck ETA.")
    c.setFont("Helvetica-Bold", 9.0)
    c.setFillColorRGB(0.55, 0.25, 0.04)
    c.drawString(154, qr_y + 26, "TESTING FACILITY ONLY: Weber's does not refill cylinders. Tanks return clean, dry & empty.")

    c.showPage()
    c.save()
    return buf.getvalue()
