import io
import json
import re
from datetime import datetime

from flask import Blueprint, request, jsonify, send_file, abort
from sqlalchemy import or_
from sqlalchemy.orm import joinedload

from app import db
from app.models import Warehouse, SKU, Unit
from app.utils import GS, STATUSES, DISPOSAL_TYPES, DISPOSAL_REASONS, DISPOSAL_STATUSES, normalize_cz, find_duplicate_unit, find_first_unmarked_unit

import_export_bp = Blueprint("import_export", __name__)


@import_export_bp.route("/import/csv", methods=["POST"])
def import_csv():
    if "file" not in request.files:
        abort(400, "Нет файла")
    f = request.files["file"]
    sku_id = int(request.form.get("sku_id", 0))
    warehouse_id = int(request.form.get("warehouse_id", 0))
    status = int(request.form.get("status", 1))
    if not sku_id or not warehouse_id:
        abort(400, "SKU и склад обязательны")

    text = f.read().decode("utf-8", errors="ignore")
    text = text.replace("\ufeff", "")
    codes = []
    for line in text.splitlines():
        cleaned = line.strip().strip('"').strip(",").strip('"').strip()
        if len(cleaned) > 20 and re.search(r"\d", cleaned):
            codes.append(normalize_cz(cleaned))

    seen_in_file = set()
    unique_codes = []
    for c in codes:
        if c not in seen_in_file:
            seen_in_file.add(c)
            unique_codes.append(c)

    added = 0
    assigned = 0
    duplicates = 0
    errors = 0
    for cz in unique_codes:
        if find_duplicate_unit(cz):
            duplicates += 1
            continue
        unit = find_first_unmarked_unit(sku_id, warehouse_id)
        if not unit:
            unit = find_first_unmarked_unit(sku_id)
        if unit:
            unit.cz_code = cz
            unit.status = status
            unit.updated_at = datetime.utcnow()
            db.session.flush()
            assigned += 1
        else:
            try:
                db.session.add(Unit(
                    sku_id=sku_id, cz_code=cz, status=status, warehouse_id=warehouse_id
                ))
                db.session.flush()
                added += 1
            except Exception:
                errors += 1
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
    return jsonify({"added": added, "assigned": assigned, "duplicates": duplicates, "errors": errors, "codes": unique_codes[:50]})


@import_export_bp.route("/import/pdf", methods=["POST"])
def import_pdf():
    if "file" not in request.files:
        abort(400, "Нет файла")
    f = request.files["file"]
    sku_id = int(request.form.get("sku_id", 0))
    warehouse_id = int(request.form.get("warehouse_id", 0))
    status = int(request.form.get("status", 1))
    if not sku_id or not warehouse_id:
        abort(400, "SKU и склад обязательны")

    try:
        import pdfplumber
    except ImportError:
        abort(500, "pdfplumber не установлен")

    codes = []
    with pdfplumber.open(f) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            matches = re.findall(
                r"01\d{14}[\x1d\s\-;:,]21[A-Za-z0-9]+", text, re.IGNORECASE
            )
            for m in matches:
                normalized = normalize_cz(re.sub(r"[\s\-;:,]", GS, m))
                if normalized not in codes:
                    codes.append(normalized)

    added = 0
    duplicates = 0
    errors = 0
    for cz in codes:
        if find_duplicate_unit(cz):
            duplicates += 1
            continue
        try:
            db.session.add(Unit(
                sku_id=sku_id, cz_code=cz, status=status, warehouse_id=warehouse_id
            ))
            added += 1
        except Exception:
            errors += 1
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
    return jsonify({"added": added, "duplicates": duplicates, "errors": errors, "codes": codes[:50]})


@import_export_bp.route("/export/json", methods=["GET"])
def export_json():
    data = {
        "warehouses": [w.to_dict() for w in Warehouse.query.all()],
        "skus": [s.to_dict() for s in SKU.query.all()],
        "units": [u.to_dict() for u in Unit.query.all()],
        "exported_at": datetime.utcnow().isoformat(),
    }
    buf = io.BytesIO(json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))
    return send_file(buf, mimetype="application/json", as_attachment=True,
                     download_name=f"inventory_{datetime.utcnow().strftime('%Y%m%d')}.json")


@import_export_bp.route("/export/csv", methods=["GET"])
def export_csv_units():
    import csv
    buf = io.StringIO()
    buf.write("\ufeff")
    w = csv.writer(buf, delimiter=";")
    w.writerow(["ID", "SKU", "Артикул", "GTIN-14", "EAN-13", "Код ЧЗ", "Статус", "Склад", "Номер заказа", "Дата продажи",
                "Тип выбытия", "Причина", "Вид док.", "Номер док.", "Дата док.", "Адрес", "Цена", "Статус отчёта"])
    for u in Unit.query.options(joinedload(Unit.sku), joinedload(Unit.warehouse)).order_by(Unit.id):
        w.writerow([
            u.id, u.sku.name, u.sku.article or "", u.sku.gtin14, u.sku.ean13 or "",
            u.cz_code or "", STATUSES[u.status], u.warehouse.name,
            u.order_number or "", u.sold_date or "",
            DISPOSAL_TYPES.get(u.disposal_type, '') or '',
            DISPOSAL_REASONS.get(u.disposal_reason, '') or '',
            u.disposal_doc_type or '', u.disposal_doc_number or '',
            u.disposal_doc_date or '', u.disposal_address or '',
            u.disposal_price or '', DISPOSAL_STATUSES[u.disposal_status] if u.disposal_status else '',
        ])
    bio = io.BytesIO(buf.getvalue().encode("utf-8"))
    return send_file(bio, mimetype="text/csv", as_attachment=True,
                     download_name=f"units_{datetime.utcnow().strftime('%Y%m%d')}.csv")


@import_export_bp.route("/export/disposal-csv", methods=["GET"])
def export_disposal_csv():
    import csv
    buf = io.StringIO()
    buf.write("\ufeff")
    w = csv.writer(buf, delimiter=";")
    w.writerow([
        "ID", "Код ЧЗ (полный)", "Код для ввода в оборот", "Код для Ozon",
        "SKU", "Артикул", "GTIN-14", "EAN-13",
        "Тип операции", "Причина выбытия",
        "Вид первичного документа", "Наименование документа",
        "Номер документа", "Дата документа",
        "Адрес места выбытия", "Цена за единицу",
        "Статус отчёта", "Номер заказа", "Дата продажи"
    ])
    q = Unit.query.options(joinedload(Unit.sku), joinedload(Unit.warehouse)).filter(
        or_(Unit.disposal_type != None, Unit.disposal_status != 0)
    ).order_by(Unit.disposal_status.asc(), Unit.id)

    for u in q:
        full = u.cz_code or ''
        turnover = full.split(GS)[0] if full else ''
        ozon = full.replace(GS, '\\u001d') if full else ''
        w.writerow([
            u.id, full, turnover, ozon,
            u.sku.name, u.sku.article or '', u.sku.gtin14, u.sku.ean13 or '',
            DISPOSAL_TYPES.get(u.disposal_type, ''),
            DISPOSAL_REASONS.get(u.disposal_reason, ''),
            u.disposal_doc_type or '', u.disposal_doc_name or '',
            u.disposal_doc_number or '', u.disposal_doc_date or '',
            u.disposal_address or '', u.disposal_price or '',
            DISPOSAL_STATUSES[u.disposal_status] if u.disposal_status else '',
            u.order_number or '', u.sold_date or '',
        ])
    bio = io.BytesIO(buf.getvalue().encode("utf-8"))
    return send_file(bio, mimetype="text/csv", as_attachment=True,
                     download_name=f"disposal_{datetime.utcnow().strftime('%Y%m%d')}.csv")


@import_export_bp.route("/import/json", methods=["POST"])
def import_json():
    if "file" not in request.files:
        abort(400, "Нет файла")
    f = request.files["file"]
    data = json.loads(f.read().decode("utf-8"))
    if "skus" not in data or "units" not in data:
        abort(400, "Неверный формат")

    Unit.query.delete()
    SKU.query.delete()
    Warehouse.query.delete()

    for w in data.get("warehouses", []):
        db.session.add(Warehouse(id=w["id"], name=w["name"]))
    for s in data["skus"]:
        db.session.add(SKU(
            id=s["id"], name=s["name"], article=s.get("article"),
            gtin14=s["gtin14"], ean13=s.get("ean13"),
            production_date=s.get("production_date"),
            permit_doc=s.get("permit_doc"),
            total_quantity=s.get("total_quantity", 0),
        ))
    for u in data["units"]:
        db.session.add(Unit(
            id=u["id"], sku_id=u["sku_id"], cz_code=u.get("cz_code"),
            status=u["status"], warehouse_id=u["warehouse_id"],
            order_number=u.get("order_number"), sold_date=u.get("sold_date"),
            disposal_type=u.get("disposal_type"),
            disposal_reason=u.get("disposal_reason"),
            disposal_doc_type=u.get("disposal_doc_type"),
            disposal_doc_name=u.get("disposal_doc_name"),
            disposal_doc_number=u.get("disposal_doc_number"),
            disposal_doc_date=u.get("disposal_doc_date"),
            disposal_address=u.get("disposal_address"),
            disposal_price=u.get("disposal_price"),
            disposal_status=u.get("disposal_status", 0),
        ))
    db.session.commit()
    return jsonify({"ok": True})


@import_export_bp.route("/reset", methods=["POST"])
def reset():
    Unit.query.delete()
    SKU.query.delete()
    Warehouse.query.delete()
    db.session.commit()
    from app import init_db
    init_db(db.get_app())
    return jsonify({"ok": True})
