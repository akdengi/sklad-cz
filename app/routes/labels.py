import hashlib
import io
import os
import string
from datetime import datetime

from flask import Blueprint, request, send_file, abort, jsonify
from reportlab.lib.units import mm as mm_unit
from reportlab.pdfgen import canvas
from reportlab.graphics import renderPDF

from app.models import SKU
from app.utils import GS, parse_range, fmt_date_ru, FNC1

SERIAL_CHARS = string.ascii_letters + string.digits + '-!"%&\'()*+'

labels_bp = Blueprint("labels", __name__)

LABEL_SIZES = {
    "50x50": (50, 50),
    "58x40": (58, 40),
    "50x60": (50, 60),
    "58x30": (58, 30),
    "50x25": (50, 25),
}

LAYOUTS = {
    "full": "Вся информация",
    "km_only": "Только КМ",
    "barcode_only": "Только штрихкод",
}


def generate_serial(gtin14, unit_num):
    seed = f"{gtin14}:{unit_num}"
    h = hashlib.sha256(seed.encode()).hexdigest()
    return ''.join(SERIAL_CHARS[int(h[i * 2:i * 2 + 2], 16) % len(SERIAL_CHARS)] for i in range(13))


def get_cz_code(sku, unit_num):
    from app.models import Unit
    unit = Unit.query.filter_by(id=unit_num, sku_id=sku.id).first()
    if unit and unit.cz_code:
        return unit.cz_code
    serial = generate_serial(sku.gtin14, unit_num)
    return f"01{sku.gtin14}21{serial}"


@labels_bp.route("/sizes", methods=["GET"])
def get_sizes():
    return jsonify({"sizes": LABEL_SIZES, "layouts": LAYOUTS})


def _load_font():
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    font_paths = [
        r"C:\Windows\Fonts\DejaVuSans.ttf",
        r"C:\Windows\Fonts\Arial.ttf",
        r"/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for fp in font_paths:
        if os.path.exists(fp):
            try:
                pdfmetrics.registerFont(TTFont('CyrillicFont', fp))
                return True
            except Exception:
                continue
    return False


def _make_dm_image(cz_code):
    from reportlab.lib.utils import ImageReader
    import treepoem
    from app.utils import cz_to_datamatrix_data

    data = cz_to_datamatrix_data(cz_code)
    image = treepoem.generate_barcode(
        barcode_type='datamatrix',
        data=data,
        options={'parsefnc': True},
    )
    buf = io.BytesIO()
    image.convert('1').save(buf, format='PNG')
    buf.seek(0)
    return ImageReader(buf)


def _wrap_text(c, text, max_width, font_name, font_size):
    words = text.split()
    lines = []
    current = ""
    for word in words:
        test = f"{current} {word}".strip() if current else word
        tw = c.stringWidth(test, font_name, font_size)
        if tw <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _draw_wrapped_text(c, text, x, y, max_width, font_name, font_size, max_lines=3, leading=None):
    if leading is None:
        leading = font_size * 1.3
    lines = _wrap_text(c, text, max_width, font_name, font_size)
    drawn = 0
    for line in lines[:max_lines]:
        if drawn > 0 and drawn == max_lines - 1 and len(lines) > max_lines:
            line = line[:-2] + "..."
        c.drawString(x, y, line)
        y -= leading
        drawn += 1
    return y


def _draw_ean(c, ean13, x, y, width_mm, height_mm):
    from reportlab.graphics.barcode import createBarcodeDrawing
    desired_w = width_mm * mm_unit
    ean_h = height_mm * mm_unit
    ean_drawing = createBarcodeDrawing(
        'EAN13', value=ean13,
        barWidth=desired_w / 113,
        barHeight=ean_h,
        humanReadable=True
    )
    renderPDF.draw(ean_drawing, c, x, y)


@labels_bp.route("/pdf", methods=["GET"])
def labels_pdf():
    sku_id = int(request.args.get("sku_id", 0))
    range_str = request.args.get("range", "1-1")
    copies = int(request.args.get("copies", 1))
    size_key = request.args.get("size", "50x50")
    layout = request.args.get("layout", "full")
    sku = SKU.query.get_or_404(sku_id)
    numbers = parse_range(range_str)
    if copies > 1:
        expanded = []
        for n in numbers:
            for _ in range(copies):
                expanded.append(n)
        numbers = expanded

    w_mm, h_mm = LABEL_SIZES.get(size_key, (50, 50))
    pad = 2 * mm_unit if w_mm > h_mm else 0
    w = w_mm * mm_unit
    h = h_mm * mm_unit
    is_horizontal = w_mm > h_mm
    is_small = h_mm < 25

    font_ok = _load_font()

    first_cz = get_cz_code(sku, numbers[0]) if numbers else ""
    short_code = first_cz.split(GS)[0].replace(FNC1, "") if GS in first_cz or FNC1 in first_cz else first_cz[:20]

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(w, h))
    c.setTitle(f"Этикетки {sku.name}")

    for idx, n in enumerate(numbers):
        cz = get_cz_code(sku, n) if sku.has_marking else ""

        # Товар без маркировки — этикетка без КМ, только текст + штрихкод
        if not sku.has_marking:
            if is_horizontal:
                text_x = pad + 3 * mm_unit
                text_max_w = w - 6 * mm_unit

                font_name = 'CyrillicFont' if font_ok else "Helvetica-Bold"
                c.setFont(font_name, 7)
                name_y = h - 3 * mm_unit
                name_bottom = _draw_wrapped_text(c, sku.name, text_x, name_y, text_max_w, font_name, 7, max_lines=3)

                font_name2 = 'CyrillicFont' if font_ok else "Helvetica"
                c.setFont(font_name2, 6)
                production_date = fmt_date_ru(sku.production_date)
                article = sku.article or ""
                label_info = f"#{n} - {article} - {production_date}" if production_date else f"#{n} - {article}"
                date_bottom = _draw_wrapped_text(c, label_info, text_x, name_bottom - 1 * mm_unit, text_max_w, font_name2, 6, max_lines=2)

                if sku.ean13:
                    ean_h_mm = min(h_mm * 0.35, 12)
                    ean_h = ean_h_mm * mm_unit
                    ean_y = 2 * mm_unit
                    _draw_ean(c, sku.ean13, text_x, ean_y, w_mm - 6, ean_h_mm)
            else:
                cx = w / 2
                text_max_w = w - 6 * mm_unit
                margin = 3 * mm_unit

                font_name = 'CyrillicFont' if font_ok else "Helvetica-Bold"
                c.setFont(font_name, 7)
                name_y = h - margin - 2 * mm_unit
                name_lines = _wrap_text(c, sku.name, text_max_w, font_name, 7)
                for i, line in enumerate(name_lines[:2]):
                    c.drawCentredString(cx, name_y - i * 7 * 1.3, line)
                name_bottom = name_y - len(name_lines[:2]) * 7 * 1.3

                font_name2 = 'CyrillicFont' if font_ok else "Helvetica"
                c.setFont(font_name2, 5)
                production_date = fmt_date_ru(sku.production_date)
                article = sku.article or ""
                label_info = f"#{n} - {article} - {production_date}" if production_date else f"#{n} - {article}"
                info_lines = _wrap_text(c, label_info, text_max_w, font_name2, 5)
                for i, line in enumerate(info_lines[:2]):
                    c.drawCentredString(cx, name_bottom - 2 * mm_unit - i * 5 * 1.3, line)

                if sku.ean13:
                    ean_h_mm = min(h_mm * 0.3, 12)
                    ean_h = ean_h_mm * mm_unit
                    ean_y = 2 * mm_unit
                    _draw_ean(c, sku.ean13, 3 * mm_unit, ean_y, w_mm - 6, ean_h_mm)

            c.showPage()
            continue

        if layout == "km_only":
            dm_img = _make_dm_image(cz)
            if is_horizontal:
                dm_sz = min(h_mm * 0.92, (w_mm - 4) * 0.48) * mm_unit
                dm_x = pad
                dm_y = (h - dm_sz) / 2
            else:
                dm_sz = min(w_mm * 0.9, h_mm * 0.9) * mm_unit
                dm_x = (w - dm_sz) / 2
                dm_y = (h - dm_sz) / 2
            c.drawImage(dm_img, dm_x, dm_y, width=dm_sz, height=dm_sz, mask='auto')

        elif layout == "barcode_only":
            dm_img = _make_dm_image(cz)
            if is_horizontal:
                left_w = (w_mm - 4) * 0.5
                dm_sz = min(h_mm * 0.95, left_w * 0.98) * mm_unit
                dm_x = pad + (left_w * mm_unit - dm_sz) / 2
                dm_y = (h - dm_sz) / 2
                c.drawImage(dm_img, dm_x, dm_y, width=dm_sz, height=dm_sz, mask='auto')

                if sku.ean13:
                    right_x = pad + left_w * mm_unit + 2 * mm_unit
                    right_w = w_mm - 4 - left_w - 2
                    ean_h_mm = min(h_mm * 0.35, 10)
                    ean_h = ean_h_mm * mm_unit
                    ean_y = (h - ean_h) / 2
                    _draw_ean(c, sku.ean13, right_x, ean_y, right_w, ean_h_mm)
            else:
                if sku.ean13:
                    ean_h_mm = min(h_mm * 0.3, 12)
                    ean_h = ean_h_mm * mm_unit
                    ean_y = 2 * mm_unit
                    _draw_ean(c, sku.ean13, 3 * mm_unit, ean_y, w_mm - 6, ean_h_mm)
                dm_sz = min(w_mm * 0.6, h_mm * 0.4) * mm_unit
                dm_y = h - 2 * mm_unit - dm_sz
                c.drawImage(dm_img, (w - dm_sz) / 2, dm_y, width=dm_sz, height=dm_sz, mask='auto')

        else:
            dm_img = _make_dm_image(cz)
            if is_horizontal:
                left_w = (w_mm - 4) * 0.5
                dm_sz = min(h_mm * 0.95, left_w * 0.98) * mm_unit
                dm_x = pad + (left_w * mm_unit - dm_sz) / 2
                dm_y = (h - dm_sz) / 2
                c.drawImage(dm_img, dm_x, dm_y, width=dm_sz, height=dm_sz, mask='auto')

                right_x = pad + left_w * mm_unit + 2 * mm_unit
                right_w = w_mm - 4 - left_w - 2
                text_x = right_x + 1 * mm_unit
                text_max_w = right_w * mm_unit - 2 * mm_unit

                if is_small:
                    name_size = 5
                    info_size = 4
                else:
                    name_size = 7 if right_w > 20 else 5
                    info_size = 6 if right_w > 20 else 4

                font_name = 'CyrillicFont' if font_ok else "Helvetica-Bold"
                c.setFont(font_name, name_size)
                name_y = h - 3 * mm_unit

                if is_small:
                    name = sku.name[:18] if len(sku.name) > 18 else sku.name
                    c.drawString(text_x, name_y, name)
                    name_bottom = name_y - name_size * 1.3
                else:
                    name_bottom = _draw_wrapped_text(c, sku.name, text_x, name_y, text_max_w, font_name, name_size, max_lines=3)

                font_name2 = 'CyrillicFont' if font_ok else "Helvetica"
                c.setFont(font_name2, info_size)
                production_date = fmt_date_ru(sku.production_date)
                article = sku.article or ""
                label_info = f"#{n} - {article} - {production_date}" if production_date else f"#{n} - {article}"
                if is_small:
                    c.drawString(text_x, name_bottom - 1 * mm_unit, label_info[:30])
                else:
                    date_bottom = _draw_wrapped_text(c, label_info, text_x, name_bottom - 1 * mm_unit, text_max_w, font_name2, info_size, max_lines=2)

                if sku.ean13:
                    ean_h_mm = min(h_mm * 0.28, 10)
                    ean_h = ean_h_mm * mm_unit
                    ean_y = 2 * mm_unit
                    _draw_ean(c, sku.ean13, text_x, ean_y, right_w - 2, ean_h_mm)

            else:
                dm_img = _make_dm_image(cz)
                margin = 3 * mm_unit

                dm_sz = min(w_mm - 6, h_mm * 0.55) * mm_unit
                dm_y = h - margin - dm_sz
                c.drawImage(dm_img, (w - dm_sz) / 2, dm_y, width=dm_sz, height=dm_sz, mask='auto')

                cx = w / 2
                text_max_w = w - 6 * mm_unit

                name_size = 6 if w_mm >= 40 else 5
                info_size = 5 if w_mm >= 40 else 4

                font_name = 'CyrillicFont' if font_ok else "Helvetica-Bold"
                c.setFont(font_name, name_size)
                name_y = dm_y - 3 * mm_unit
                name_lines = _wrap_text(c, sku.name, text_max_w, font_name, name_size)
                for i, line in enumerate(name_lines[:2]):
                    c.drawCentredString(cx, name_y - i * name_size * 1.3, line)
                name_bottom = name_y - len(name_lines[:2]) * name_size * 1.3

                font_name2 = 'CyrillicFont' if font_ok else "Helvetica"
                c.setFont(font_name2, info_size)
                production_date = fmt_date_ru(sku.production_date)
                article = sku.article or ""
                label_info = f"#{n} - {article} - {production_date}" if production_date else f"#{n} - {article}"
                c.drawCentredString(cx, name_bottom - 1 * mm_unit, label_info[:30])

                if sku.ean13:
                    ean_h_mm = min(h_mm * 0.15, 8)
                    ean_h = ean_h_mm * mm_unit
                    ean_y = margin
                    _draw_ean(c, sku.ean13, 3 * mm_unit, ean_y, w_mm - 6, ean_h_mm)

        c.showPage()

    c.save()
    buf.seek(0)
    return send_file(buf, mimetype="application/pdf", as_attachment=True,
                     download_name=f"{short_code}_{size_key}.pdf")


@labels_bp.route("/print", methods=["GET"])
def labels_print():
    sku_id = int(request.args.get("sku_id", 0))
    range_str = request.args.get("range", "1-1")
    copies = int(request.args.get("copies", 1))
    fmt = request.args.get("format", "zpl")
    size_key = request.args.get("size", "50x50")
    layout = request.args.get("layout", "full")
    sku = SKU.query.get_or_404(sku_id)
    numbers = parse_range(range_str)
    if copies > 1:
        expanded = []
        for n in numbers:
            for _ in range(copies):
                expanded.append(n)
        numbers = expanded

    w_mm, h_mm = LABEL_SIZES.get(size_key, (50, 50))
    is_horizontal = w_mm > h_mm
    is_small = h_mm < 25
    zpl_pad = 16 if is_horizontal else 0

    lines = []
    if fmt == "zpl":
        pw = int(w_mm * 8)
        pl = int(h_mm * 8)
        dm_size_zpl = int(min(w_mm, h_mm) * 3)
        for n in numbers:
            cz = get_cz_code(sku, n) if sku.has_marking else ""
            lines.append("^XA")
            lines.append(f"^PW{pw}^LL{pl}")

            # Товар без маркировки — без КМ, только текст + штрихкод
            if not sku.has_marking:
                name_len = 20 if is_horizontal else 25
                font_sz = "20,20"
                if is_horizontal:
                    rx = 20
                    lines.append(f"^FO{rx},20^A0N,{font_sz}^FD{sku.name[:name_len]}^FS")
                    if sku.ean13:
                        lines.append(f"^FO{rx},{pl - 50}^A0N,14,14^FD{sku.ean13}^FS")
                        lines.append(f"^FO{rx},{pl - 75}^BY2,2,40^B3N,N,40,N,N^FD{sku.ean13}^FS")
                else:
                    lines.append(f"^FO20,{pl - 40}^A0N,{font_sz}^FD{sku.name[:name_len]}^FS")
                    if sku.ean13:
                        lines.append(f"^FO20,24^A0N,14,14^FD{sku.ean13}^FS")
                        lines.append(f"^FO20,44^BY2,2,40^B3N,N,40,N,N^FD{sku.ean13}^FS")
                lines.append(f"^FO20,{pl - 20}^A0N,14,14^FD#{n}^FS")
                lines.append("^XZ")
                continue

            if layout == "km_only":
                if is_horizontal:
                    lines.append(f"^FO{10 + zpl_pad},{pl//2 - dm_size_zpl//2}^BY2^BDM,2,2,2,2,N^FD{cz}^FS")
                else:
                    lines.append(f"^FO{pw//2 - dm_size_zpl//2},{pl//2 - dm_size_zpl//2}^BY2^BDM,2,2,2,2,N^FD{cz}^FS")

            elif layout == "barcode_only":
                if is_horizontal:
                    lines.append(f"^FO{10 + zpl_pad},{pl//2 - dm_size_zpl//2}^BY2^BDM,2,2,2,2,N^FD{cz}^FS")
                    if sku.ean13:
                        lines.append(f"^FO{pw//2 + 10 + zpl_pad},{pl//2 - 30}^A0N,16,16^FD{sku.ean13}^FS")
                        lines.append(f"^FO{pw//2 + 10 + zpl_pad},{pl//2 - 55}^BY2,2,50^B3N,N,50,N,N^FD{sku.ean13}^FS")
                else:
                    if sku.ean13:
                        lines.append(f"^FO{pw//2 - 100},{pl//2 - 30}^BY2,2,60^B3N,N,60,N,N^FD{sku.ean13}^FS")
                    lines.append(f"^FO{pw//2 - dm_size_zpl//2},20^BY2^BDM,2,2,2,2,N^FD{cz}^FS")

            else:
                name_len = 12 if is_small else (20 if is_horizontal else 25)
                font_sz = "16,16" if is_small else "20,20"
                if is_horizontal:
                    lines.append(f"^FO{10 + zpl_pad},{pl//2 - dm_size_zpl//2}^BY2^BDM,2,2,2,2,N^FD{cz}^FS")
                    rx = pw // 2 + 10 + zpl_pad
                    lines.append(f"^FO{rx},20^A0N,{font_sz}^FD{sku.name[:name_len]}^FS")
                    if sku.ean13:
                        lines.append(f"^FO{rx},{pl - 50}^A0N,14,14^FD{sku.ean13}^FS")
                        lines.append(f"^FO{rx},{pl - 75}^BY2,2,40^B3N,N,40,N,N^FD{sku.ean13}^FS")
                else:
                    dm_y_zpl = pl - 24 - dm_size_zpl
                    lines.append(f"^FO{pw//2 - dm_size_zpl//2},{dm_y_zpl}^BY2^BDM,2,2,2,2,N^FD{cz}^FS")
                    text_y = dm_y_zpl - 20
                    lines.append(f"^FO20,{text_y}^A0N,{font_sz}^FD{sku.name[:name_len]}^FS")
                    if sku.ean13:
                        lines.append(f"^FO20,24^A0N,14,14^FD{sku.ean13}^FS")
                        lines.append(f"^FO20,44^BY2,2,40^B3N,N,40,N,N^FD{sku.ean13}^FS")

            lines.append(f"^FO20,{pl - 20}^A0N,14,14^FD#{n}^FS")
            lines.append("^XZ")
        content = "\n".join(lines)
        fname = f"labels_{sku.name}_{size_key}.zprn"
    else:
        lines.append(f"SIZE {w_mm} mm,{h_mm} mm")
        lines.append("GAP 3 mm,0 mm")
        lines.append("DIRECTION 1,0")
        lines.append("REFERENCE 0,0")
        lines.append("OFFSET 0 mm")
        lines.append("SET PEEL OFF")
        lines.append("SET CUTTER OFF")
        lines.append("SET PARTIAL_CUTTER OFF")
        lines.append("SET TEAR ON")
        lines.append("CODEPAGE UTF-8")
        lines.append("")
        for n in numbers:
            cz = get_cz_code(sku, n) if sku.has_marking else ""
            lines.append("CLS")

            name_len = 10 if is_small else (15 if is_horizontal else 20)

            dm_params = 'c126,x3,r0,0,0'
            tspl_pad = 2 if is_horizontal else 0

            # Товар без маркировки — без КМ, только текст + штрихкод
            if not sku.has_marking:
                name_len_full = 15 if is_horizontal else 20
                if is_horizontal:
                    lines.append(f'TEXT 10,10,"4",0,1,1,"{sku.name[:name_len_full]}"')
                    if sku.ean13:
                        lines.append(f'TEXT 10,{h_mm - 20},"4",0,1,1,"{sku.ean13}"')
                        lines.append(f'BARCODE 10,{h_mm - 40},"EAN13",60,2,0,2,2,"{sku.ean13}"')
                else:
                    lines.append(f'TEXT 10,{h_mm - 20},"4",0,1,1,"{sku.name[:name_len_full]}"')
                    if sku.ean13:
                        lines.append(f'TEXT 10,15,"4",0,1,1,"{sku.ean13}"')
                        lines.append(f'BARCODE 10,25,"EAN13",60,2,0,2,2,"{sku.ean13}"')
                lines.append(f'TEXT 10,{h_mm - 10},"4",0,1,1,"#{n}"')
                lines.append("PRINT 1")
                continue

            if layout == "km_only":
                if is_horizontal:
                    dm_y_pos = h_mm // 2 - min(w_mm - 4, h_mm) // 4
                    dm_sz = min(w_mm - 4, h_mm) - 10
                    lines.append(f'DMATRIX {5 + tspl_pad},{dm_y_pos},{dm_sz},{dm_sz},{dm_params},"{cz}"')
                else:
                    dm_sz = min(w_mm, h_mm) - 10
                    lines.append(f'DMATRIX {w_mm//2 - dm_sz//2},{h_mm//2 - dm_sz//2},{dm_sz},{dm_sz},{dm_params},"{cz}"')

            elif layout == "barcode_only":
                if is_horizontal:
                    dm_y_pos = h_mm // 2 - min(w_mm - 4, h_mm) // 4
                    dm_sz = min(w_mm - 4, h_mm) - 10
                    lines.append(f'DMATRIX {5 + tspl_pad},{dm_y_pos},{dm_sz},{dm_sz},{dm_params},"{cz}"')
                    if sku.ean13:
                        lines.append(f'TEXT {w_mm//2 + 5 + tspl_pad},{h_mm//2},"4",0,1,1,"{sku.ean13}"')
                        lines.append(f'BARCODE {w_mm//2 + 5 + tspl_pad},{h_mm//2 + 10},"EAN13",60,2,0,2,2,"{sku.ean13}"')
                else:
                    if sku.ean13:
                        lines.append(f'BARCODE {w_mm//2 - 30},{h_mm//2},"EAN13",60,2,0,2,2,"{sku.ean13}"')
                    dm_sz = min(w_mm, h_mm) - 10
                    lines.append(f'DMATRIX {w_mm//2 - dm_sz//2},10,{dm_sz},{dm_sz},{dm_params},"{cz}"')

            else:
                if is_horizontal:
                    dm_y_pos = h_mm // 2 - min(w_mm - 4, h_mm) // 4
                    dm_sz = min(w_mm - 4, h_mm) - 10
                    lines.append(f'DMATRIX {5 + tspl_pad},{dm_y_pos},{dm_sz},{dm_sz},{dm_params},"{cz}"')
                    lines.append(f'TEXT {w_mm//2 + 5 + tspl_pad},10,"4",0,1,1,"{sku.name[:name_len]}"')
                    if sku.ean13:
                        lines.append(f'TEXT {w_mm//2 + 5 + tspl_pad},{h_mm - 20},"4",0,1,1,"{sku.ean13}"')
                        lines.append(f'BARCODE {w_mm//2 + 5 + tspl_pad},{h_mm - 40},"EAN13",60,2,0,2,2,"{sku.ean13}"')
                else:
                    dm_sz = min(w_mm - 6, h_mm - 6)
                    dm_y_tspl = h_mm - 3 - dm_sz
                    lines.append(f'DMATRIX {w_mm//2 - dm_sz//2},{dm_y_tspl},{dm_sz},{dm_sz},{dm_params},"{cz}"')
                    text_y = dm_y_tspl - 8
                    lines.append(f'TEXT 10,{text_y},"4",0,1,1,"{sku.name[:name_len]}"')
                    if sku.ean13:
                        lines.append(f'TEXT 10,15,"4",0,1,1,"{sku.ean13}"')
                        lines.append(f'BARCODE 10,25,"EAN13",60,2,0,2,2,"{sku.ean13}"')

            lines.append(f'TEXT 10,{h_mm - 10},"4",0,1,1,"#{n}"')
            lines.append("PRINT 1")
        content = "\n".join(lines)
        fname = f"labels_{sku.name}_{size_key}.tspl"

    buf = io.BytesIO(content.encode("utf-8"))
    return send_file(buf, mimetype="text/plain", as_attachment=True, download_name=fname)
