import sys
import os
import io
import json
import re
from datetime import datetime
import logging

from flask import Blueprint, request, jsonify, send_file, abort
from sqlalchemy import or_
from sqlalchemy.orm import joinedload

from app import db
from app.models import Warehouse, SKU, Unit
from app.utils import GS, STATUSES, DISPOSAL_TYPES, DISPOSAL_REASONS, DISPOSAL_STATUSES, normalize_cz, find_duplicate_unit, find_first_unmarked_unit, validate_cz_code, cz_to_gs1_parenthesized, extract_gtin_from_cz

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

    sku = SKU.query.get(sku_id)
    sku_gtin14 = sku.gtin14 if sku else None

    added = 0
    assigned = 0
    duplicates = 0
    errors = 0
    import_errors = []  # [{code, reason, sku_name?}]
    added_units = []    # units successfully added, for post-import CZ check

    for cz in unique_codes:
        # 1. Дубликат
        if find_duplicate_unit(cz):
            duplicates += 1
            import_errors.append({
                "code": cz_to_gs1_parenthesized(cz),
                "raw_code": cz,
                "reason": "Дубликат",
                "reason_detail": "Код уже существует в базе",
            })
            continue

        # 2. Валидация структуры
        validation = validate_cz_code(cz, sku_gtin14=sku_gtin14)
        if not validation["valid"]:
            import_errors.append({
                "code": cz_to_gs1_parenthesized(cz),
                "raw_code": cz,
                "reason": "Ошибка структуры КМ",
                "reason_detail": "; ".join(validation["warnings"]),
            })
            # Продолжаем — код может быть привязан даже с предупреждениями

        # 3. Проверка GTIN: извлекаем GTIN из КМ и определяем целевой SKU
        cz_gtin = extract_gtin_from_cz(cz)
        target_sku_id = sku_id
        target_sku = sku

        if cz_gtin:
            if sku_gtin14 and cz_gtin != sku_gtin14.strip():
                # GTIN не совпадает с выбранным SKU
                found_sku = SKU.query.filter(SKU.gtin14 == cz_gtin).first()
                if found_sku:
                    target_sku_id = found_sku.id
                    target_sku = found_sku
                else:
                    import_errors.append({
                        "code": cz_to_gs1_parenthesized(cz),
                        "raw_code": cz,
                        "reason": "Нет SKU для GTIN",
                        "reason_detail": f"КМ относится к товару с GTIN {cz_gtin}, но такого SKU нет в базе",
                    })
                    errors += 1
                    continue
        else:
            # Не удалось извлечь GTIN
            if sku and sku.has_marking:
                import_errors.append({
                    "code": cz_to_gs1_parenthesized(cz),
                    "raw_code": cz,
                    "reason": "Нет GTIN",
                    "reason_detail": "Не удалось извлечь GTIN (AI 01) из кода маркировки",
                })

        # 4. Добавление
        unit = find_first_unmarked_unit(target_sku_id, warehouse_id)
        if not unit:
            unit = find_first_unmarked_unit(target_sku_id)
        if unit:
            unit.cz_code = cz
            unit.status = status
            unit.cz_offline_valid = validation["valid"]
            unit.updated_at = datetime.utcnow()
            db.session.flush()
            assigned += 1
            added_units.append(unit)
        else:
            try:
                new_unit = Unit(
                    sku_id=target_sku_id, cz_code=cz, status=status, warehouse_id=warehouse_id,
                    cz_offline_valid=validation["valid"]
                )
                db.session.add(new_unit)
                db.session.flush()
                added += 1
                added_units.append(new_unit)
            except Exception:
                import_errors.append({
                    "code": cz_to_gs1_parenthesized(cz),
                    "raw_code": cz,
                    "reason": "Ошибка добавления",
                    "reason_detail": "Не удалось создать единицу товара",
                })
                errors += 1

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        import_errors.append({
            "code": "—",
            "reason": "Ошибка сохранения",
            "reason_detail": "Не удалось сохранить изменения в базу данных",
        })

    # 5. Проверка статуса ЧЗ онлайн для добавленных кодов
    cz_check_errors = []
    if added_units:
        try:
            from app.cz_api import check_cz_status
            _CZ_TO_UNIT_STATUS = {
                'EMITTED': 1, 'APPLIED': 2, 'INTRODUCED': 3, 'INTRODUCED_RETURNED': 3,
                'RETIRED': 5, 'WITHDRAWN': 5, 'WRITTEN_OFF': 5,
            }
            codes_to_check = [u.cz_code for u in added_units if u.cz_code]
            if codes_to_check:
                cz_result = check_cz_status(codes_to_check)
                cz_results_list = cz_result.get("results", [])
                checked_units = {u.cz_code: u for u in added_units}

                for entry in cz_results_list:
                    # Определяем код для поиска
                    cis_code = entry.get("cisCode", "")
                    info = entry.get("cisInfo", entry)
                    error_code = entry.get("errorCode", "")
                    error_msg = entry.get("errorMessage", "")

                    # Ищем соответствующую единицу
                    unit = None
                    for cz_key in checked_units:
                        if cis_code and cis_code in cz_key:
                            unit = checked_units[cz_key]
                            break
                    if not unit:
                        continue

                    if error_code and error_code != "0":
                        unit.cz_check_date = datetime.utcnow().strftime("%Y-%m-%d %H:%M")
                        unit.cz_status = None
                        cz_check_errors.append({
                            "code": cz_to_gs1_parenthesized(unit.cz_code),
                            "raw_code": unit.cz_code,
                            "reason": "Ошибка проверки ЧЗ",
                            "reason_detail": f"{error_msg} (код {error_code})",
                        })
                    else:
                        cz_status_raw = info.get("status") or info.get("cisStatus") or ""
                        unit.cz_status = cz_status_raw
                        unit.cz_check_date = datetime.utcnow().strftime("%Y-%m-%d %H:%M")
                        new_status_val = _CZ_TO_UNIT_STATUS.get(cz_status_raw)
                        if new_status_val is not None and new_status_val > unit.status:
                            unit.status = new_status_val
                        if cz_status_raw in ('RETIRED', 'WITHDRAWN', 'WRITTEN_OFF'):
                            unit.disposal_status = 1

                        # Проверка статуса: если КМ не в допустимом статусе
                        allowed_statuses = ('EMITTED', 'APPLIED', 'INTRODUCED', 'INTRODUCED_RETURNED', 'REAPPLY')
                        if cz_status_raw not in allowed_statuses and cz_status_raw:
                            cz_check_errors.append({
                                "code": cz_to_gs1_parenthesized(unit.cz_code),
                                "raw_code": unit.cz_code,
                                "reason": "Недопустимый статус ЧЗ",
                                "reason_detail": f"Статус: {cz_status_raw}. Допустимые: Эмитирован, Нанесён, В обороте",
                            })

                try:
                    from app import db as _db
                    _db.session.commit()
                except Exception:
                    pass
        except Exception as e:
            cz_check_errors.append({
                "code": "—",
                "reason": "Ошибка проверки ЧЗ",
                "reason_detail": f"Не удалось проверить статусы ЧЗ: {str(e)}",
            })

    all_errors = import_errors + cz_check_errors
    return jsonify({
        "added": added,
        "assigned": assigned,
        "duplicates": duplicates,
        "errors": errors,
        "codes": unique_codes[:50],
        "import_errors": all_errors[:500],
        "total_errors": len(all_errors),
    })


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
        validation = validate_cz_code(cz, sku_gtin14=sku.gtin14 if sku else None)
        try:
            db.session.add(Unit(
                sku_id=sku_id, cz_code=cz, status=status, warehouse_id=warehouse_id,
                cz_offline_valid=validation["valid"]
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
                "Тип выбытия", "Причина", "ИНН Покупателя", "Вид док.", "Номер док.", "Дата док.", "Адрес", "Цена", "Статус отчета"])
    for u in Unit.query.options(joinedload(Unit.sku), joinedload(Unit.warehouse)).order_by(Unit.id):
        w.writerow([
            u.id, u.sku.name, u.sku.article or "", u.sku.gtin14, u.sku.ean13 or "",
            u.cz_code or "", STATUSES[u.status], u.warehouse.name,
            u.order_number or "", u.sold_date or "",
            DISPOSAL_TYPES.get(u.disposal_type, '') or '',
            DISPOSAL_REASONS.get(u.disposal_reason, '') or '',
            u.buyer_inn or '',
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
        "ИНН Покупателя",
        "Вид первичного документа", "Наименование документа",
        "Номер документа", "Дата документа",
        "Адрес места выбытия", "Цена за единицу",
        "Статус отчета", "Номер заказа", "Дата продажи"
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
            u.buyer_inn or '',
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
            cert_type=s.get("cert_type"),
            cert_number=s.get("cert_number"),
            cert_date=s.get("cert_date"),
            tnved_code=s.get("tnved_code"),
            contractor_inn=s.get("contractor_inn"),
            total_quantity=s.get("total_quantity", 0),
        ))
    for u in data["units"]:
        cz_code = u.get("cz_code")
        cz_offline_valid = None
        if cz_code:
            validation = validate_cz_code(cz_code)
            cz_offline_valid = validation["valid"]
        db.session.add(Unit(
            id=u["id"], sku_id=u["sku_id"], cz_code=cz_code,
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
            cz_offline_valid=cz_offline_valid,
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


@import_export_bp.route("/cz/documents/create", methods=["POST"])
def create_cz_document():
    """
    Создает документ в системе Честный Знак через API.
    
    Тело запроса:
    {
        "document_type": "FIXATION",
        "cz_codes": ["...коды чз..."],
        "disposal_type": "shipment" или "return",
        "disposal_reason": "remote_sale" или "remote_sale_return",
        "disposal_doc_type": "other", "upd", "nakladnaya", "aktt", "check"
    }
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "Нет данных в запросе"}), 400
    
    from app.cz_api import create_fixation_document, reset_token
    from sqlalchemy.orm import joinedload
    
    cz_codes = data.get("cz_codes", [])
    if not cz_codes or len(cz_codes) == 0:
        return jsonify({"error": "Не указаны коды ЧЗ"}), 400
    
    # Подготавливаем данные о единицах
    unit_ids = data.get("unit_ids", None)
    
    disposal_type = data.get("disposal_type")
    disposal_reason = data.get("disposal_reason")
    disposal_doc_type = data.get("disposal_doc_type") or "other"
    disposal_doc_name = data.get("disposal_doc_name", "")
    disposal_doc_number = data.get("disposal_doc_number", "")
    disposal_doc_date = data.get("disposal_doc_date", "")
    disposal_address = data.get("disposal_address", "")
    disposal_fias_id = data.get("disposal_fias_id", "")
    disposal_price = data.get("disposal_price")
    
    s = load_settings()
    
    # Получаем данные единиц для заполнения документа
    units_query = Unit.query.filter(Unit.cz_code.in_(cz_codes))
    if unit_ids:
        units_query = units_query.filter(Unit.id.in_(unit_ids))
    
    units_list = []
    for u in units_query.options(joinedload(Unit.sku), joinedload(Unit.warehouse)):
        unit_data = u.to_dict()
        # Добавляем код ЧЗ
        unit_data["cz_code"] = u.cz_code
        units_list.append(unit_data)
    
    if not units_list:
        return jsonify({"error": "Нет найденных единиц с указанными кодами"}), 404
    
    try:
        result = create_fixation_document(
            cz_codes=cz_codes,
            disposal_type=disposal_type or "shipment",
            disposal_reason=disposal_reason or "remote_sale",
            disposal_doc_type=DISPOSAL_DOC_TYPES.index(disposal_doc_type) if disposal_doc_type in DISPOSAL_DOC_TYPES else 0,
            disposal_doc_name=disposal_doc_name or s.get("default_disposal_doc_name", ""),
            disposal_doc_number=disposal_doc_number,
            disposal_doc_date=disposal_doc_date or datetime.utcnow().strftime("%Y-%m-%d"),
            disposal_address=disposal_address or s.get("default_disposal_address", ""),
            disposal_fias_id=disposal_fias_id or s.get("default_disposal_fias_id", ""),
            disposal_price=disposal_price,
        )
        
        if result.get("success"):
            # Обновляем статус отчета для единиц
            for u in units_list:
                unit = Unit.query.get(u["id"])
                if unit:
                    unit.disposal_status = 1  # Подтверждено ЧЗ
            try:
                db.session.commit()
            except Exception:
                pass
            
            return jsonify({
                "success": True,
                "document_id": result.get("data", {}),
                "units_processed": len(units_list)
            })
        else:
            reset_token()  # Сбрасываем токен при ошибке
            return jsonify({
                "success": False,
                "error_code": result.get("error_code"),
                "error_message": result.get("error_message")
            }), 400
            
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@import_export_bp.route("/cz/documents/status", methods=["GET"])
def check_document_status():
    """
    Проверяет статус созданного документа.
    """
    doc_id = request.args.get("doc_id")
    if not doc_id:
        return jsonify({"error": "Не указан ID документа"}), 400
    
    from app.cz_api import get_uuid_token, reset_token
    
    s = load_settings()
    base_url = s.get("cz_api_url", "https://markirovka.crpt.ru/api/v3/true-api").rstrip("/")
    
    url = f"{base_url}/lk/documents/status?documentId={doc_id}"
    
    try:
        token = get_uuid_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "accept": "application/json"
        }
        
        response = requests.get(url, headers=headers, timeout=15)
        
        if response.status_code == 200:
            return jsonify(response.json())
        else:
            reset_token()
            return jsonify({
                "error": f"Ошибка API: {response.status_code}",
                "message": response.text
            }), 400
            
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@import_export_bp.route("/cz/receipt/create", methods=["POST"])
def create_receipt_document_route():
    """
    Создает документ LK_RECEIPT (вывод из оборота) для выбранных единиц через API.
    
    Тело запроса:
    {
        "unit_ids": [1, 2, 3],
        "document_format": "MANUAL"
    }
    
    Returns:
    {
        "success": true,
        "document_id": "...",
        "units_processed": 3
    }
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "Нет данных в запросе"}), 400
    
    from app.cz_api import create_receipt_document, reset_token
    from sqlalchemy.orm import joinedload
    
    unit_ids = data.get("unit_ids", [])
    if not unit_ids or len(unit_ids) == 0:
        return jsonify({"error": "Не указаны ID единиц"}), 400
    
    document_format = data.get("document_format", "MANUAL").upper()
    
    # Получаем коды ЧЗ и данные из выбранных единиц
    units_query = Unit.query.options(joinedload(Unit.sku)).filter(
        Unit.id.in_(unit_ids)
    )
    
    cz_codes = []
    unit_data_list = []  # Список данных по каждой единице
    
    for u in units_query:
        if u.cz_code:
            cz_codes.append(u.cz_code)
        # Данные для документа
        unit_doc_data = {
            "disposal_type": u.disposal_type,
            "disposal_reason": u.disposal_reason,
            "disposal_doc_type": u.disposal_doc_type or "other",
            "disposal_doc_name": u.disposal_doc_name or "",
            "disposal_doc_number": u.disposal_doc_number or "",
            "disposal_doc_date": u.disposal_doc_date or "",
            "disposal_address": u.disposal_address or "",
            "disposal_fias_id": u.disposal_fias_id or "",
            "disposal_price": u.disposal_price,
            "buyer_inn": u.buyer_inn or "",
        }
        unit_data_list.append(unit_doc_data)
    
    if not cz_codes:
        return jsonify({"error": "У выбранных единиц нет кодов ЧЗ"}), 400
    
    try:
        # Передаем данные любой единицы (все должны быть аналогичны для одного заказа)
        unit_data = unit_data_list[0] if unit_data_list else None
        
        result = create_receipt_document(
            cz_codes=cz_codes,
            document_format=document_format,
            unit_data=unit_data
        )
        
        if result.get("success"):
            # Обновляем статус отчета для единиц - сначала "Отправлено в ЧЗ" (2)
            for u in units_query:
                u.disposal_status = 2  # Отправлено в ЧЗ
            try:
                db.session.commit()
            except Exception:
                pass
            
            # Пытаемся получить статус документа из ГИС МТ
            doc_id = None
            if isinstance(result.get("data"), dict):
                doc_id = result["data"].get("documentId") or result["data"].get("number")
            elif isinstance(result.get("data"), str):
                doc_id = result["data"]
            
            document_status_info = None
            if doc_id:
                try:
                    from app.cz_api import check_document_status_by_id
                    status_result = check_document_status_by_id(doc_id, pg=pg)
                    if status_result.get("success"):
                        document_status_info = {
                            "status": status_result["data"].get("status"),
                            "errors": status_result["data"].get("errors"),
                            "eliminationReason": status_result["data"].get("eliminationReason")
                        }
                except Exception as e:
                    logging.debug(f"Failed to check doc status: {e}")
            
            return jsonify({
                "success": True,
                "document_id": result.get("data", {}),
                "cz_codes": cz_codes[:50],
                "units_processed": len(cz_codes),
                "document_status_info": document_status_info
            })
        else:
            reset_token()  # Сбрасываем токен при ошибке
            return jsonify({
                "success": False,
                "error_code": result.get("error_code"),
                "error_message": result.get("error_message")
            }), 400
            
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@import_export_bp.route("/cz/export/disposal-csv", methods=["GET"])
def export_disposal_csv_api():
    """
    Экспортирует данные для выбытия в CSV через API.
    
    Query params:
        - unit_ids: coma-separated list of Unit IDs
        - all_sold: if true, exports all sold units without disposal report
    """
    from sqlalchemy import or_
    from app.cz_api import reset_token
    
    s = load_settings()
    
    # Получаем код товарной группы
    product_group_id = s.get("product_group", "27")
    pg_code = get_product_group_code(product_group_id)
    
    unit_ids_param = request.args.get("unit_ids")
    all_sold = request.args.get("all_sold") == "true"
    
    # Получаем единицы для экспорта
    if unit_ids_param:
        unit_ids = [int(x.strip()) for x in unit_ids_param.split(",") if x.strip()]
        q = Unit.query.options(joinedload(Unit.sku), joinedload(Unit.warehouse)).filter(
            Unit.id.in_(unit_ids)
        ).order_by(Unit.id)
    elif all_sold:
        # Все проданные товары (статус 4 или 5, но не отчитались yet)
        q = Unit.query.options(joinedload(Unit.sku), joinedload(Unit.warehouse)).filter(
            or_(Unit.disposal_type != None, Unit.disposal_status != 0),
            Unit.status.in_([4, 5])
        ).order_by(Unit.id.asc())
    else:
        # Единицы с незаполненным отчетом
        q = Unit.query.options(joinedload(Unit.sku), joinedload(Unit.warehouse)).filter(
            or_(Unit.disposal_type != None, Unit.disposal_status == 0),
            Unit.status.in_([4, 5])
        ).order_by(Unit.id.asc())
    
    units_list = []
    for u in q:
        unit_data = u.to_dict()
        if not u.cz_code:
            continue
        units_list.append(unit_data)
    
    from app.cz_api import export_units_for_disposal
    result = export_units_for_disposal(units_list)
    
    bio = io.BytesIO(result["csv"].encode("utf-8"))
    return send_file(
        bio,
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"disposal_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
    )


@import_export_bp.route("/cz/disposal/confirm", methods=["POST"])
def confirm_disposal():
    """
    Подтверждает вывод из оборота для выбранных единиц.
    
    Тело запроса:
    {
        "unit_ids": [1, 2, 3],
        "disposal_type": "shipment" или "return",
        "cz_status": "RETIRED", "WITHDRAWN" или "WRITTEN_OFF"
    }
    
    Returns:
    {
        "success": true,
        "units_updated": 5
    }
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "Нет данных в запросе"}), 400
    
    unit_ids = data.get("unit_ids", [])
    if not unit_ids or len(unit_ids) == 0:
        return jsonify({"error": "Не указаны ID единиц"}), 400
    
    cz_status = data.get("cz_status", "").upper()
    
    # Валидные статусы ЧЗ для выбытия
    VALID_CZ_STATUSES = {"RETIRED", "WITHDRAWN", "WRITTEN_OFF"}
    
    if not cz_status or cz_status not in VALID_CZ_STATUSES:
        return jsonify({
            "error": f"Недопустимый статус ЧЗ. Должен быть один из: {', '.join(VALID_CZ_STATUSES)}"
        }), 400
    
    # Получаем единицы
    updated_units = []
    try:
        for unit_id in unit_ids:
            unit = Unit.query.get(unit_id)
            if not unit:
                continue
            
            # Обновляем статус
            unit.status = 5  # Выбыл
            unit.cz_status = cz_status
            unit.disposal_status = 1  # Подтверждено ЧЗ
            unit.updated_at = datetime.utcnow()
            
            updated_units.append(unit_id)
        
        db.session.commit()
        
        return jsonify({
            "success": True,
            "units_updated": len(updated_units),
            "unit_ids": updated_units
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@import_export_bp.route("/cz/disposal/bulk", methods=["POST"])
def bulk_disposal():
    """
    Массовое подтверждение вывода из оборота для всех проданных единиц с конкретным статусом ЧЗ.
    
    Тело запроса:
    {
        "cz_status": "RETIRED"
    }
    """
    data = request.get_json() or {}
    
    cz_status = data.get("cz_status", "").upper()
    VALID_CZ_STATUSES = {"RETIRED", "WITHDRAWN", "WRITTEN_OFF"}
    
    if not cz_status or cz_status not in VALID_CZ_STATUSES:
        return jsonify({
            "error": f"Недопустимый статус ЧЗ. Должен быть один из: {', '.join(VALID_CZ_STATUSES)}"
        }), 400
    
    try:
        # Находим все проданные единицы (статус = 4) и обновляем их
        updated_count = Unit.query.filter(
            Unit.status == 4,  # Продан
            Unit.cz_status == cz_status
        ).update({
            "status": 5,  # Выбыл
            "disposal_status": 1,
            "updated_at": datetime.utcnow()
        })
        
        db.session.commit()
        
        return jsonify({
            "success": True,
            "units_updated": updated_count
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@import_export_bp.route("/cz/disposal-by-ids", methods=["GET"])
def disposal_by_ids():
    """
    Получает данные единиц для предпросмотра перед отправкой вывода из оборота.
    
    Query params:
        - ids: comma-separated list of Unit IDs
    """
    ids_str = request.args.get("ids", "")
    if not ids_str:
        return jsonify({"error": "Не указаны ID"}), 400
    
    try:
        unit_ids = [int(x.strip()) for x in ids_str.split(",") if x.strip()]
    except Exception:
        return jsonify({"error": "Неверный формат IDs"}), 400
    
    from sqlalchemy.orm import joinedload
    units_query = Unit.query.options(joinedload(Unit.sku)).filter(
        Unit.id.in_(unit_ids)
    )
    
    result = []
    for u in units_query:
        result.append({
            "id": u.id,
            "cz_code": u.cz_code or "",
            "disposal_price": u.disposal_price or 0.0,
            "disposal_address": u.disposal_address or "",
            "disposal_doc_number": u.disposal_doc_number or "",
            "disposal_doc_date": u.disposal_doc_date or "",
            "disposal_doc_name": u.disposal_doc_name or "",
            "disposal_reason": u.disposal_reason or "",
            "buyer_inn": u.buyer_inn or "",
        })
    
    return jsonify({"units": result})
