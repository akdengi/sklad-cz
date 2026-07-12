from datetime import datetime
import io
from flask import Blueprint, request, jsonify, abort, send_file
from sqlalchemy import select, or_, exc
from sqlalchemy.orm import joinedload
from app import db
from app.models import Unit, SKU, Warehouse
from app.utils import normalize_cz, find_duplicate_unit, find_first_unmarked_unit, cz_search_prefix, validate_cz_code, cz_to_gs1_parenthesized, FNC1, STATUSES

units_bp = Blueprint("units", __name__)


@units_bp.route("/validate-all", methods=["GET"])
def validate_all_units():
    """Массовая валидация кодов ЧЗ всех единиц на остатках."""
    units = Unit.query.options(joinedload(Unit.sku)).filter(
        or_(Unit.cz_code != None, Unit.cz_code != ''),
        Unit.status.notin_([4, 5]),
    ).all()
    invalid = []
    for u in units:
        if u.cz_code:
            sku_gtin14 = u.sku.gtin14 if u.sku else None
            validation = validate_cz_code(u.cz_code, sku_gtin14=sku_gtin14)
            u.cz_offline_valid = validation["valid"]
            if not validation["valid"]:
                invalid.append({
                    "unit_id": u.id,
                    "sku_name": u.sku.name if u.sku else None,
                    "cz_code": cz_to_gs1_parenthesized(u.cz_code),
                    "warnings": validation["warnings"],
                })
    db.session.commit()
    return jsonify({
        "total": len(units),
        "invalid_count": len(invalid),
        "invalid": invalid[:200],
    })


@units_bp.route("/<int:uid>", methods=["GET"])
def get_unit(uid):
    u = Unit.query.filter(Unit.id == uid).first()
    if not u:
        abort(404)
    _ = u.sku
    _ = u.warehouse
    return jsonify({"unit": u.to_dict()})

SORT_MAP = {
    'id': Unit.id,
    'sku_name': SKU.name,
    'article': SKU.article,
    'warehouse': Warehouse.name,
    'status': Unit.status,
    'cz_code': Unit.cz_code,
}


@units_bp.route("", methods=["GET"])
def get_units():
    q = Unit.query.options(joinedload(Unit.sku), joinedload(Unit.warehouse))
    if request.args.get("warehouse_id"):
        q = q.filter(Unit.warehouse_id == int(request.args["warehouse_id"]))
    if request.args.get("sku_id"):
        q = q.filter(Unit.sku_id == int(request.args["sku_id"]))
    if request.args.get("status") is not None:
        q = q.filter(Unit.status == int(request.args["status"]))
    else:
        q = q.filter(Unit.status.notin_([4, 5]))
    if request.args.get("no_cz"):
        q = q.filter(or_(Unit.cz_code == None, Unit.cz_code == ''))
    if request.args.get("q"):
        raw_q = request.args['q'].lstrip('#').strip()
        term = f"%{raw_q}%"
        norm_q = normalize_cz(raw_q)
        search_prefix = cz_search_prefix(norm_q)
        q = q.join(SKU).filter(
            or_(
                Unit.cz_code.like(f"{FNC1}{search_prefix}%"),
                Unit.cz_code.like(f"{search_prefix}%"),
                Unit.cz_code.like(f"%{raw_q}%"),
                Unit.order_number.ilike(term),
                SKU.name.ilike(term),
                SKU.article.ilike(term),
                Unit.id.cast(db.String).ilike(term),
            )
        )

    sort_field = request.args.get("sort", "id")
    sort_dir = request.args.get("order", "desc").lower()
    sort_col = SORT_MAP.get(sort_field, Unit.id)
    if sort_dir == "asc":
        q = q.order_by(sort_col.asc())
    else:
        q = q.order_by(sort_col.desc())

    total = q.count()
    page = int(request.args.get("page", 1))
    per_page = min(int(request.args.get("per_page", 100)), 500)
    units = q.offset((page - 1) * per_page).limit(per_page).all()

    dirty = False
    for u in units:
        if u.cz_code and u.cz_offline_valid is None:
            sku_gtin14 = u.sku.gtin14 if u.sku else None
            v = validate_cz_code(u.cz_code, sku_gtin14=sku_gtin14)
            u.cz_offline_valid = v["valid"]
            dirty = True
    if dirty:
        db.session.commit()

    return jsonify({
        "units": [u.to_dict() for u in units],
        "total": total,
        "page": page,
        "per_page": per_page,
    })


@units_bp.route("/find-by-code", methods=["GET"])
def find_by_code():
    raw_code = (request.args.get("code") or "").strip()
    if not raw_code:
        return jsonify({"found": False})
    code = normalize_cz(raw_code)
    prefix = cz_search_prefix(code)
    unit = Unit.query.options(
        joinedload(Unit.sku), joinedload(Unit.warehouse)
    ).filter(Unit.cz_code.like(f"{prefix}%")).first()
    if not unit:
        unit = Unit.query.options(
            joinedload(Unit.sku), joinedload(Unit.warehouse)
        ).filter(Unit.cz_code.like(f"%{code}%")).first()
    # Поиск по EAN13 (товары без маркировки)
    if not unit:
        ean = raw_code.strip()
        if len(ean) == 13 and ean.isdigit():
            sku = SKU.query.filter(SKU.ean13 == ean, SKU.has_marking == False).first()
            if sku:
                unit = Unit.query.options(
                    joinedload(Unit.sku), joinedload(Unit.warehouse)
                ).filter(
                    Unit.sku_id == sku.id,
                    Unit.status.notin_([4, 5]),
                ).first()
    # Поиск по артикулу (товары без маркировки)
    if not unit:
        article = raw_code.strip()
        if len(article) >= 2:
            sku = SKU.query.filter(SKU.article == article, SKU.has_marking == False).first()
            if sku:
                unit = Unit.query.options(
                    joinedload(Unit.sku), joinedload(Unit.warehouse)
                ).filter(
                    Unit.sku_id == sku.id,
                    Unit.status.notin_([4, 5]),
                ).first()
    # Поиск по названию SKU (товары без маркировки)
    if not unit:
        name = raw_code.strip()
        if len(name) >= 3:
            sku = SKU.query.filter(SKU.name.ilike(f"%{name}%"), SKU.has_marking == False).first()
            if sku:
                unit = Unit.query.options(
                    joinedload(Unit.sku), joinedload(Unit.warehouse)
                ).filter(
                    Unit.sku_id == sku.id,
                    Unit.status.notin_([4, 5]),
                ).first()
    if not unit:
        return jsonify({"found": False})
    return jsonify({"found": True, "unit": unit.to_dict()})


@units_bp.route("/sold", methods=["GET"])
def get_sold_units():
    q = Unit.query.options(joinedload(Unit.sku), joinedload(Unit.warehouse))
    q = q.filter(Unit.status.in_([4, 5]))
    if request.args.get("sku_id"):
        q = q.filter(Unit.sku_id == int(request.args["sku_id"]))
    if request.args.get("warehouse_id"):
        q = q.filter(Unit.warehouse_id == int(request.args["warehouse_id"]))
    if request.args.get("date_from"):
        q = q.filter(Unit.sold_date >= request.args["date_from"])
    if request.args.get("date_to"):
        q = q.filter(Unit.sold_date <= request.args["date_to"])

    if request.args.get("q"):
        raw_q = request.args['q'].lstrip('#').strip()
        term = f"%{raw_q}%"
        norm_q = normalize_cz(raw_q)
        search_prefix = cz_search_prefix(norm_q)
        q = q.join(SKU).filter(
            or_(
                Unit.cz_code.like(f"{FNC1}{search_prefix}%"),
                Unit.cz_code.like(f"%{raw_q}%"),
                Unit.order_number.ilike(term),
                SKU.name.ilike(term),
                SKU.article.ilike(term),
                Unit.id.cast(db.String).ilike(term),
            )
        )

    sort = request.args.get("sort", "date_desc")
    sort_map = {
        "id_asc": Unit.id.asc(), "id_desc": Unit.id.desc(),
        "sku_asc": SKU.name.asc(), "sku_desc": SKU.name.desc(),
        "warehouse_asc": Warehouse.name.asc(), "warehouse_desc": Warehouse.name.desc(),
        "order_asc": Unit.order_number.asc(), "order_desc": Unit.order_number.desc(),
        "date_asc": Unit.sold_date.asc(), "date_desc": Unit.sold_date.desc(),
        "price_asc": Unit.disposal_price.asc(), "price_desc": Unit.disposal_price.desc(),
        "disposal_asc": Unit.disposal_status.asc(), "disposal_desc": Unit.disposal_status.desc(),
    }
    q = q.order_by(sort_map.get(sort, Unit.sold_date.desc()))

    total = q.count()
    total_price_all = sum(u.disposal_price or 0 for u in q.all())
    page = int(request.args.get("page", 1))
    per_page = min(int(request.args.get("per_page", 100)), 500)
    units = q.offset((page - 1) * per_page).limit(per_page).all()
    total_price_page = sum(u.disposal_price or 0 for u in units)
    return jsonify({
        "units": [u.to_dict() for u in units],
        "count": total,
        "total_price": total_price_all,
        "page": page,
        "per_page": per_page,
    })


@units_bp.route("/<int:uid>/return", methods=["POST"])
def return_unit(uid):
    u = Unit.query.get_or_404(uid)
    if u.status != 5:
        abort(400, f"Возврат возможен только из статуса «Выбыл». Текущий статус: {STATUSES[u.status]}")
    data = request.json or {}
    warehouse_id = data.get("warehouse_id")
    if not warehouse_id:
        abort(400, "Склад обязателен")
    u.warehouse_id = int(warehouse_id)
    u.status = 3
    u.was_returned = True
    u.sold_date = None
    u.order_number = None
    u.disposal_type = None
    u.disposal_reason = None
    u.disposal_doc_type = None
    u.disposal_doc_name = None
    u.disposal_doc_number = None
    u.disposal_doc_date = None
    u.disposal_address = None
    u.disposal_price = None
    u.disposal_status = 0
    u.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify({
        "unit": u.to_dict(),
        "message": "Товар возвращен. Необходимо подать отчет о возврате в ЛК Честный Знак!",
    })


@units_bp.route("/<int:uid>/delete-sale", methods=["POST"])
def delete_sale(uid):
    """Удаление ошибочной продажи — сброс единицы в исходное состояние."""
    u = Unit.query.get_or_404(uid)
    if u.status not in (4, 5):
        abort(400, "Удаление продажи доступно только для проданных/выбывших товаров")
    u.status = 0
    u.sold_date = None
    u.order_number = None
    u.disposal_type = None
    u.disposal_reason = None
    u.disposal_doc_type = None
    u.disposal_doc_name = None
    u.disposal_doc_number = None
    u.disposal_doc_date = None
    u.disposal_address = None
    u.disposal_fias_id = None
    u.disposal_price = None
    u.disposal_status = 0
    u.cz_status = None
    u.cz_check_date = None
    u.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify({
        "unit": u.to_dict(),
        "message": "Продажа удалена. Если отчет о выбытии был подан в ЧЗ — подайте отчет о возврате.",
    })


@units_bp.route("/disposal", methods=["GET"])
def get_disposal_units():
    q = db.session.query(Unit).options(joinedload(Unit.sku), joinedload(Unit.warehouse))
    q = q.join(SKU, Unit.sku_id == SKU.id)
    q = q.filter(
        or_(
            Unit.disposal_type.isnot(None),
            Unit.disposal_status != 0,
        ),
        SKU.has_marking == True,
    )
    if request.args.get("warehouse_id"):
        q = q.filter(Unit.warehouse_id == int(request.args["warehouse_id"]))
    if request.args.get("disposal_status") is not None:
        q = q.filter(Unit.disposal_status == int(request.args["disposal_status"]))
    if request.args.get("date_from"):
        q = q.filter(Unit.sold_date >= request.args["date_from"])
    if request.args.get("date_to"):
        q = q.filter(Unit.sold_date <= request.args["date_to"])
    if request.args.get("q"):
        raw_q = request.args['q'].lstrip('#').strip()
        term = f"%{raw_q}%"
        norm_q = normalize_cz(raw_q)
        search_prefix = cz_search_prefix(norm_q)
        q = q.filter(
            or_(
                Unit.cz_code.like(f"{FNC1}{search_prefix}%"),
                Unit.cz_code.like(f"{search_prefix}%"),
                Unit.cz_code.like(f"%{raw_q}%"),
                Unit.order_number.ilike(term),
                SKU.name.ilike(term),
                SKU.article.ilike(term),
                Unit.id.cast(db.String).ilike(term),
            )
        )
    sort = request.args.get("sort", "date_desc")
    sort_map = {
        "id_asc": Unit.id.asc(), "id_desc": Unit.id.desc(),
        "sku_asc": SKU.name.asc(), "sku_desc": SKU.name.desc(),
        "warehouse_asc": Warehouse.name.asc(), "warehouse_desc": Warehouse.name.desc(),
        "date_asc": Unit.sold_date.asc(), "date_desc": Unit.sold_date.desc(),
        "price_asc": Unit.disposal_price.asc(), "price_desc": Unit.disposal_price.desc(),
    }
    q = q.order_by(sort_map.get(sort, Unit.sold_date.desc()))

    total = q.count()
    page = int(request.args.get("page", 1))
    per_page = min(int(request.args.get("per_page", 100)), 500)
    units = q.offset((page - 1) * per_page).limit(per_page).all()

    return jsonify({
        "units": [u.to_dict() for u in units],
        "total": total,
        "page": page,
        "per_page": per_page,
    })


@units_bp.route("/batch-create", methods=["POST"])
def batch_create_units():
    data = request.json or {}
    sku_id = int(data.get("sku_id") or 0)
    warehouse_id = int(data.get("warehouse_id") or 0)
    count = int(data.get("count") or 0)
    if not sku_id or not warehouse_id or count < 1:
        abort(400, "SKU, склад и количество обязательны")
    if count > 10000:
        abort(400, "Максимум 10000 единиц за раз")

    sku = SKU.query.get_or_404(sku_id)
    if sku.total_quantity and sku.total_quantity > 0:
        existing_count = Unit.query.filter(
            Unit.sku_id == sku_id,
            Unit.status.notin_([4, 5]),
        ).count()
        if existing_count + count > sku.total_quantity:
            abort(400, f"Нельзя создать {count} единиц. Тираж: {sku.total_quantity}, на складе: {existing_count}")

    created = 0
    for _ in range(count):
        db.session.add(Unit(
            sku_id=sku_id,
            cz_code=None,
            status=0,
            warehouse_id=warehouse_id,
        ))
        created += 1
    db.session.commit()
    return jsonify({"created": created})


@units_bp.route("/scan", methods=["POST"])
def scan_unit():
    data = request.json or {}
    cz_code = (data.get("cz_code") or "").strip()
    sku_id = int(data.get("sku_id") or 0)
    warehouse_id = int(data.get("warehouse_id") or 0)
    status = int(data.get("status") or 1)

    if not cz_code:
        abort(400, "Код ЧЗ обязателен")
    if not sku_id:
        abort(400, "SKU обязателен")

    cz_code = normalize_cz(cz_code)

    existing = find_duplicate_unit(cz_code)
    if existing:
        abort(409, f"Код уже существует в единице #{existing.id}")

    # Валидация кода ЧЗ (с проверкой GTIN карточки)
    sku = SKU.query.get(sku_id)
    sku_gtin14 = sku.gtin14 if sku else None
    validation = validate_cz_code(cz_code, sku_gtin14=sku_gtin14)

    unit = find_first_unmarked_unit(sku_id, warehouse_id if warehouse_id else None)
    if not unit:
        if warehouse_id:
            unit = find_first_unmarked_unit(sku_id, None)
        if not unit:
            unit = Unit(
                sku_id=sku_id,
                cz_code=cz_code,
                status=status,
                warehouse_id=warehouse_id or 1,
                cz_offline_valid=validation["valid"],
            )
            db.session.add(unit)
            db.session.commit()
            return jsonify({
                "unit_id": unit.id,
                "sku_name": unit.sku.name if unit.sku else None,
                "created": True,
                "validation": validation,
            })

    unit.cz_code = cz_code
    unit.cz_offline_valid = validation["valid"]
    unit.status = status
    unit.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify({
        "unit_id": unit.id,
        "sku_name": unit.sku.name if unit.sku else None,
        "created": False,
        "validation": validation,
    })


@units_bp.route("/quick-sell", methods=["POST"])
def quick_sell():
    data = request.json or {}
    cz_code = (data.get("cz_code") or "").strip()
    target_warehouse_id = data.get("target_warehouse_id")
    order_number = (data.get("order_number") or "").strip() or None
    disposal_price = data.get("disposal_price")

    if not cz_code:
        abort(400, "Код ЧЗ обязателен")
    cz_code = normalize_cz(cz_code)

    unit = Unit.query.options(
        joinedload(Unit.sku), joinedload(Unit.warehouse)
    ).filter(Unit.cz_code == cz_code).first()
    if not unit:
        abort(404, f"Единица с кодом ЧЗ не найдена")
    if unit.status != 3:
        abort(400, f"Продавать можно только товары в статусе «В обороте». Текущий статус: {STATUSES[unit.status]}")

    source_wh = Warehouse.query.get(unit.warehouse_id)
    transferred = False

    if source_wh and target_warehouse_id:
        target_wh = Warehouse.query.get(target_warehouse_id)
        if target_wh and target_wh.id != source_wh.id and target_wh.wh_type == "virtual":
            existing_on_virtual = Unit.query.filter(
                Unit.sku_id == unit.sku_id,
                Unit.warehouse_id == target_wh.id,
                Unit.status.notin_([4, 5]),
            ).count()

            if existing_on_virtual > 0:
                victim = Unit.query.filter(
                    Unit.sku_id == unit.sku_id,
                    Unit.warehouse_id == target_wh.id,
                    Unit.status.notin_([4, 5]),
                ).order_by(Unit.id.asc()).first()
                if victim:
                    db.session.delete(victim)
                    db.session.flush()

            target_unit = Unit(
                sku_id=unit.sku_id,
                cz_code=unit.cz_code,
                status=3,
                warehouse_id=target_wh.id,
                cz_offline_valid=unit.cz_offline_valid,
            )
            db.session.add(target_unit)
            unit.cz_code = None
            unit.status = 0
            transferred = True
            db.session.flush()
            unit = target_unit

    unit.status = 4
    unit.sold_date = datetime.utcnow().strftime("%Y-%m-%d")
    if order_number:
        unit.order_number = order_number

    unit.disposal_type = "shipment"
    unit.disposal_reason = "remote_sale"
    unit.disposal_doc_type = "прочее"
    unit.disposal_doc_name = f"Заказ {order_number}" if order_number else ""
    unit.disposal_doc_number = order_number or ""
    unit.disposal_doc_date = unit.sold_date
    if disposal_price:
        unit.disposal_price = float(disposal_price)

    from app.utils import load_settings
    settings = load_settings()
    default_address = settings.get("default_disposal_address", "")
    unit.disposal_address = default_address or None
    unit.disposal_fias_id = settings.get("default_disposal_fias_id") or None

    unit.disposal_status = 0
    unit.updated_at = datetime.utcnow()

    db.session.commit()

    final_wh = Warehouse.query.get(unit.warehouse_id)
    return jsonify({
        "unit": unit.to_dict(),
        "transferred": transferred,
        "message": "Товар продан и поставлен на вывод из оборота" if not transferred
                   else f"Код перенесен на склад «{final_wh.name}», продан и поставлен на вывод из оборота",
    })


@units_bp.route("/sell-no-marking", methods=["POST"])
def sell_no_marking():
    """Продажа товара без маркировки по GTIN/EAN13 или SKU ID."""
    data = request.json or {}
    ean13 = (data.get("ean13") or "").strip()
    sku_id = data.get("sku_id")
    target_warehouse_id = data.get("target_warehouse_id")
    order_number = (data.get("order_number") or "").strip() or None
    disposal_price = data.get("disposal_price")

    sku = None
    if sku_id:
        sku = SKU.query.get(int(sku_id))
    elif ean13:
        sku = SKU.query.filter(SKU.ean13 == ean13, SKU.has_marking == False).first()
        if not sku:
            sku = SKU.query.filter(SKU.gtin14.like(f"%{ean13}"), SKU.has_marking == False).first()

    if not sku:
        abort(404, "Товар без маркировки не найден")
    if sku.has_marking:
        abort(400, "Этот товар имеет маркировку — используйте стандартную продажу")

    unit = Unit.query.filter(
        Unit.sku_id == sku.id,
        Unit.status.notin_([4, 5]),
    ).first()

    if not unit:
        unit = Unit(
            sku_id=sku.id,
            status=0,
            warehouse_id=target_warehouse_id or 1,
        )
        db.session.add(unit)
        db.session.flush()

    source_wh = Warehouse.query.get(unit.warehouse_id)
    transferred = False
    if source_wh and target_warehouse_id:
        target_wh = Warehouse.query.get(int(target_warehouse_id))
        if target_wh and target_wh.id != source_wh.id and target_wh.wh_type == "virtual":
            existing = Unit.query.filter(
                Unit.sku_id == sku.id,
                Unit.warehouse_id == target_wh.id,
                Unit.status.notin_([4, 5]),
            ).first()
            if existing:
                db.session.delete(existing)
                db.session.flush()

            target_unit = Unit(
                sku_id=sku.id,
                status=0,
                warehouse_id=target_wh.id,
            )
            db.session.add(target_unit)
            unit.status = 0
            unit.updated_at = datetime.utcnow()
            transferred = True
            db.session.flush()
            unit = target_unit

    unit.status = 4
    unit.sold_date = datetime.utcnow().strftime("%Y-%m-%d")
    unit.order_number = order_number
    if disposal_price:
        unit.disposal_price = float(disposal_price)
    unit.updated_at = datetime.utcnow()
    db.session.commit()

    final_wh = Warehouse.query.get(unit.warehouse_id)
    return jsonify({
        "unit": unit.to_dict(),
        "transferred": transferred,
        "message": "Товар продан" if not transferred
                   else f"Товар перемещен на «{final_wh.name}» и продан",
    })


@units_bp.route("/<int:uid>/sell", methods=["POST"])
def sell_unit(uid):
    u = Unit.query.get_or_404(uid)
    if u.status != 3:
        abort(400, f"Продавать можно только товары в статусе «В обороте». Текущий статус: {STATUSES[u.status]}")
    data = request.json or {}
    u.status = 4
    u.sold_date = datetime.utcnow().strftime("%Y-%m-%d")
    u.order_number = data.get("order_number") or u.order_number
    u.disposal_type = data.get("disposal_type", "shipment")
    u.disposal_reason = data.get("disposal_reason", "remote_sale")
    u.disposal_doc_type = data.get("disposal_doc_type", "прочее")
    u.disposal_doc_name = data.get("disposal_doc_name", "")
    u.disposal_doc_number = data.get("disposal_doc_number", u.order_number or "")
    u.disposal_doc_date = data.get("disposal_doc_date", u.sold_date)
    u.disposal_address = data.get("disposal_address")
    u.disposal_price = data.get("disposal_price")
    u.disposal_status = 0
    u.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify(u.to_dict())


@units_bp.route("/check-duplicate", methods=["GET"])
def check_duplicate():
    cz = (request.args.get("cz") or "").strip()
    exclude_id = request.args.get("exclude_id")
    if not cz:
        return jsonify({"duplicate": False})
    cz = normalize_cz(cz)
    existing = find_duplicate_unit(cz, exclude_unit_id=int(exclude_id) if exclude_id else None)
    if existing:
        return jsonify({
            "duplicate": True,
            "existing_id": existing.id,
            "existing_sku": existing.sku.name if existing.sku else None,
        })
    return jsonify({"duplicate": False})


@units_bp.route("/validate", methods=["GET"])
def validate_code():
    """Валидация кода ЧЗ (структурная + криптохвост + сравнение GTIN)."""
    cz = (request.args.get("cz") or "").strip()
    sku_id = request.args.get("sku_id")
    if not cz:
        return jsonify({"valid": False, "warnings": ["Код не задан"], "parts": {}})
    sku_gtin14 = None
    if sku_id:
        sku = SKU.query.get(int(sku_id))
        if sku:
            sku_gtin14 = sku.gtin14
    validation = validate_cz_code(cz, sku_gtin14=sku_gtin14)
    return jsonify(validation)


@units_bp.route("", methods=["POST"])
def create_unit():
    data = request.json or {}
    if not data.get("sku_id") or not data.get("warehouse_id"):
        abort(400, "SKU и склад обязательны")

    sku = SKU.query.get_or_404(int(data["sku_id"]))
    if sku.total_quantity and sku.total_quantity > 0:
        existing_count = Unit.query.filter(
            Unit.sku_id == sku.id,
            Unit.status.notin_([4, 5]),
        ).count()
        if existing_count >= sku.total_quantity:
            abort(400, f"Достигнут лимит тиража: {sku.total_quantity} единиц")

    cz_code = (data.get("cz_code") or "").strip() or None
    validation = None
    if cz_code:
        cz_code = normalize_cz(cz_code)
        existing = find_duplicate_unit(cz_code)
        if existing:
            abort(409, f"Код ЧЗ уже существует в единице #{existing.id}")
        validation = validate_cz_code(cz_code, sku_gtin14=sku.gtin14)
    u = Unit(
        sku_id=int(data["sku_id"]),
        cz_code=cz_code,
        status=int(data.get("status") or 0),
        warehouse_id=int(data["warehouse_id"]),
        order_number=data.get("order_number"),
        sold_date=data.get("sold_date"),
        disposal_type=data.get("disposal_type"),
        disposal_reason=data.get("disposal_reason"),
        disposal_doc_type=data.get("disposal_doc_type"),
        disposal_doc_name=data.get("disposal_doc_name"),
        disposal_doc_number=data.get("disposal_doc_number"),
        disposal_doc_date=data.get("disposal_doc_date"),
        disposal_address=data.get("disposal_address"),
        disposal_fias_id=data.get("disposal_fias_id"),
        disposal_price=data.get("disposal_price"),
        disposal_status=int(data.get("disposal_status") or 0),
        cz_offline_valid=validation["valid"] if validation else None,
    )
    try:
        db.session.add(u)
        db.session.commit()
    except exc.IntegrityError:
        db.session.rollback()
        abort(409, "Код ЧЗ уже существует")
    result = u.to_dict()
    if validation:
        result["validation"] = validation
    return jsonify(result), 201


@units_bp.route("/<int:uid>", methods=["PUT"])
def update_unit(uid):
    u = Unit.query.get_or_404(uid)
    data = request.json or {}
    u.sku_id = int(data.get("sku_id") or u.sku_id)
    cz_code = data.get("cz_code")
    validation = None
    if cz_code is not None:
        cz_code = (cz_code.strip() or None)
    if cz_code and cz_code != u.cz_code:
        cz_code = normalize_cz(cz_code)
        existing = find_duplicate_unit(cz_code, exclude_unit_id=uid)
        if existing:
            abort(409, f"Код ЧЗ уже существует в единице #{existing.id}")
        sku = SKU.query.get(u.sku_id)
        sku_gtin14 = sku.gtin14 if sku else None
        validation = validate_cz_code(cz_code, sku_gtin14=sku_gtin14)
    u.cz_code = cz_code
    if validation:
        u.cz_offline_valid = validation["valid"]
    new_status = int(data.get("status") or u.status)
    if new_status == 3 and u.status != 3:
        u.was_returned = False
    u.status = new_status
    u.warehouse_id = int(data.get("warehouse_id") or u.warehouse_id)
    u.order_number = data.get("order_number", u.order_number)
    u.sold_date = data.get("sold_date", u.sold_date)
    u.disposal_type = data.get("disposal_type", u.disposal_type)
    u.disposal_reason = data.get("disposal_reason", u.disposal_reason)
    u.disposal_doc_type = data.get("disposal_doc_type", u.disposal_doc_type)
    u.disposal_doc_name = data.get("disposal_doc_name", u.disposal_doc_name)
    u.disposal_doc_number = data.get("disposal_doc_number", u.disposal_doc_number)
    u.disposal_doc_date = data.get("disposal_doc_date", u.disposal_doc_date)
    u.disposal_address = data.get("disposal_address", u.disposal_address)
    u.disposal_fias_id = data.get("disposal_fias_id", u.disposal_fias_id)
    u.disposal_price = data.get("disposal_price", u.disposal_price)
    new_disposal_status = int(data.get("disposal_status") or u.disposal_status)
    if new_disposal_status == 2 and u.cz_status not in ('RETIRED', 'WITHDRAWN', 'WRITTEN_OFF'):
        new_disposal_status = 1
    u.disposal_status = new_disposal_status
    u.updated_at = datetime.utcnow()
    try:
        db.session.commit()
    except exc.IntegrityError:
        db.session.rollback()
        abort(409, "Код ЧЗ уже существует")
    result = u.to_dict()
    if validation:
        result["validation"] = validation
    return jsonify(result)


@units_bp.route("/<int:uid>", methods=["DELETE"])
def delete_unit(uid):
    u = Unit.query.get_or_404(uid)
    db.session.delete(u)
    db.session.commit()
    return jsonify({"ok": True})


@units_bp.route("/<int:uid>/move", methods=["POST"])
def move_unit(uid):
    u = Unit.query.get_or_404(uid)
    data = request.json or {}
    if "warehouse_id" not in data:
        abort(400, "warehouse_id обязателен")
    u.warehouse_id = int(data["warehouse_id"])
    u.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify(u.to_dict())


@units_bp.route("/<int:uid>/dm-image", methods=["GET"])
def unit_dm_image(uid):
    u = Unit.query.get_or_404(uid)
    code = u.cz_code
    if not code:
        abort(400, "У единицы нет кода ЧЗ")
    import treepoem
    from app.utils import cz_to_datamatrix_data
    data = cz_to_datamatrix_data(code)
    image = treepoem.generate_barcode(
        barcode_type='datamatrix',
        data=data,
        options={'parsefnc': True},
    )
    buf = io.BytesIO()
    image.convert('1').save(buf, format='PNG')
    buf.seek(0)
    return send_file(buf, mimetype='image/png')
