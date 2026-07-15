from flask import Blueprint, jsonify
from datetime import datetime, timedelta
from sqlalchemy import or_
from sqlalchemy.orm import joinedload
from app import db
from app.models import Warehouse, SKU, Unit

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/api/summary", methods=["GET"])
def summary():
    warehouses = Warehouse.query.all()
    by_warehouse = []
    for w in warehouses:
        active_count = Unit.query.filter(
            Unit.warehouse_id == w.id,
            Unit.status.notin_([4, 5]),
        ).count()
        by_warehouse.append({
            "id": w.id,
            "name": w.name,
            "count": active_count,
        })

    total_units = Unit.query.filter(Unit.status.notin_([4, 5])).count()
    marked_units = Unit.query.filter(
        Unit.cz_code != None, Unit.cz_code != '',
        Unit.status.notin_([4, 5]),
    ).count()
    unmarked_units = Unit.query.join(SKU).filter(
        SKU.has_marking == True,
        or_(Unit.cz_code == None, Unit.cz_code == ''),
        Unit.status.notin_([4, 5]),
    ).count()
    returned_units = Unit.query.filter(
        Unit.was_returned == True,
        Unit.status.notin_([4, 5]),
    ).count()

    sold_units = Unit.query.filter(Unit.status.in_([4, 5])).count()
    sold_total_price = db.session.query(
        db.func.coalesce(db.func.sum(Unit.disposal_price), 0)
    ).filter(Unit.status.in_([4, 5])).scalar()

    disposal_ready = Unit.query.filter(
        Unit.disposal_status == 0,
        Unit.sold_date != None,
        Unit.status.in_([4, 5]),
    ).join(SKU).filter(SKU.has_marking == True).count()
    disposal_sent = Unit.query.filter(
        Unit.disposal_status == 1,
    ).count()

    today = datetime.utcnow().date()
    deadline_overdue = []
    deadline_warning = []
    deadline_ok = []
    sold_pending = Unit.query.filter(
        Unit.status.in_([4, 5]),
        Unit.disposal_status == 0,
        Unit.sold_date != None,
    ).all()
    for u in sold_pending:
        try:
            if u.sku and not u.sku.has_marking:
                continue
            sold_dt = datetime.strptime(u.sold_date, "%Y-%m-%d").date()
            days_left = 3 - (today - sold_dt).days
            item = {
                "id": u.id,
                "sku_name": u.sku.name if u.sku else "",
                "sku_article": u.sku.article if u.sku else "",
                "sold_date": u.sold_date,
                "days_left": days_left,
            }
            if days_left < 0:
                deadline_overdue.append(item)
            elif days_left <= 1:
                deadline_warning.append(item)
            else:
                deadline_ok.append(item)
        except Exception:
            pass

    skus = SKU.query.all()
    total_in_batches = sum(s.total_quantity or 0 for s in skus)
    remaining = max(0, total_in_batches - marked_units)

    by_sku = []
    for s in skus:
        total = s.units.filter(Unit.status.notin_([4, 5])).count()
        marked = s.units.filter(
            Unit.cz_code != None, Unit.cz_code != '',
            Unit.status.notin_([4, 5]),
        ).count()
        sold = s.units.filter(Unit.status.in_([4, 5])).count()
        sold_price = db.session.query(
            db.func.coalesce(db.func.sum(Unit.disposal_price), 0)
        ).filter(Unit.sku_id == s.id, Unit.status.in_([4, 5])).scalar()
        in_disposal = s.units.filter(
            Unit.disposal_status == 0,
            Unit.status.in_([4, 5]),
        ).count()
        for w in warehouses:
            wh_count = s.units.filter(
                Unit.warehouse_id == w.id,
                Unit.status.notin_([4, 5]),
            ).count()
            if wh_count > 0:
                by_sku.append({
                    "sku_id": s.id,
                    "sku_name": s.name,
                    "sku_article": s.article or "",
                    "warehouse_name": w.name,
                    "total": wh_count,
                    "marked": s.units.filter(
                        Unit.warehouse_id == w.id,
                        Unit.cz_code != None, Unit.cz_code != '',
                        Unit.status.notin_([4, 5]),
                    ).count(),
                })
        if not warehouses:
            by_sku.append({
                "sku_id": s.id,
                "sku_name": s.name,
                "sku_article": s.article or "",
                "warehouse_name": "—",
                "total": total,
                "marked": marked,
            })
        by_sku.append({
            "sku_id": s.id,
            "sku_name": s.name,
            "sku_article": s.article or "",
            "warehouse_name": "_TOTAL_",
            "total": total,
            "marked": marked,
            "sold": sold,
            "sold_price": sold_price,
            "in_disposal": in_disposal,
            "edition_total": s.total_quantity or 0,
            "has_marking": bool(s.has_marking),
        })

    recent = (
        Unit.query.options(joinedload(Unit.sku), joinedload(Unit.warehouse))
        .order_by(Unit.updated_at.desc().nullslast(), Unit.id.desc())
        .limit(10)
        .all()
    )
    return jsonify({
        "skus": SKU.query.count(),
        "units": total_units,
        "marked": marked_units,
        "unmarked": unmarked_units,
        "returned": returned_units,
        "total_in_batches": total_in_batches,
        "remaining": remaining,
        "sold_units": sold_units,
        "sold_total_price": sold_total_price,
        "disposal_ready": disposal_ready,
        "disposal_sent": disposal_sent,
        "deadline_overdue": deadline_overdue,
        "deadline_warning": deadline_warning,
        "deadline_ok": deadline_ok,
        "warehouses": len(warehouses),
        "by_warehouse": by_warehouse,
        "by_sku": by_sku,
        "recent": [u.to_dict() for u in recent],
    })
