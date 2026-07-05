from flask import Blueprint, request, jsonify, abort
from app import db
from app.models import SKU

skus_bp = Blueprint("skus", __name__)


@skus_bp.route("", methods=["GET"])
def get_skus():
    return jsonify([s.to_dict() for s in SKU.query.order_by(SKU.id.desc()).all()])


@skus_bp.route("", methods=["POST"])
def create_sku():
    data = request.json or {}
    name = (data.get("name") or "").strip()
    gtin14 = (data.get("gtin14") or "").strip()
    has_marking = bool(data.get("has_marking", True))
    if not name:
        abort(400, "Название обязательно")
    if has_marking and not gtin14:
        abort(400, "Для товара с маркировкой GTIN обязателен")
    ean13 = (data.get("ean13") or "").strip() or (gtin14[1:] if gtin14 and gtin14.startswith("0") else None)
    sku = SKU(
        name=name,
        article=(data.get("article") or "").strip() or None,
        gtin14=gtin14,
        ean13=ean13,
        production_date=data.get("production_date"),
        permit_doc=data.get("permit_doc"),
        tnved_code=(data.get("tnved_code") or "").strip() or None,
        total_quantity=int(data.get("total_quantity") or 0),
        has_marking=bool(data.get("has_marking", True)),
    )
    db.session.add(sku)
    db.session.commit()
    return jsonify(sku.to_dict()), 201


@skus_bp.route("/<int:sid>", methods=["PUT"])
def update_sku(sid):
    sku = SKU.query.get_or_404(sid)
    data = request.json or {}
    sku.name = (data.get("name") or sku.name).strip()
    sku.article = (data.get("article") or "").strip() or None
    sku.gtin14 = (data.get("gtin14") or sku.gtin14).strip()
    ean13 = data.get("ean13")
    sku.ean13 = ean13.strip() if ean13 else (sku.gtin14[1:] if sku.gtin14.startswith("0") else None)
    sku.production_date = data.get("production_date", sku.production_date)
    sku.permit_doc = data.get("permit_doc", sku.permit_doc)
    tnved = data.get("tnved_code")
    sku.tnved_code = tnved.strip() if tnved else None
    sku.total_quantity = int(data.get("total_quantity") or 0)
    sku.has_marking = bool(data.get("has_marking", sku.has_marking))
    db.session.commit()
    return jsonify(sku.to_dict())


@skus_bp.route("/<int:sid>", methods=["DELETE"])
def delete_sku(sid):
    sku = SKU.query.get_or_404(sid)
    db.session.delete(sku)
    db.session.commit()
    return jsonify({"ok": True})
