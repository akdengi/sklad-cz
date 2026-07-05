from flask import Blueprint, request, jsonify, abort
from app import db
from app.models import Warehouse, Unit

warehouses_bp = Blueprint("warehouses", __name__)


@warehouses_bp.route("", methods=["GET"])
def get_warehouses():
    warehouses = Warehouse.query.all()
    return jsonify([w.to_dict() for w in warehouses])


@warehouses_bp.route("", methods=["POST"])
def create_warehouse():
    data = request.json or {}
    name = (data.get("name") or "").strip()
    wh_type = data.get("wh_type", "physical")
    if not name:
        abort(400, "Название обязательно")
    if Warehouse.query.filter_by(name=name).first():
        abort(400, "Такой склад уже есть")
    if wh_type not in ("physical", "virtual"):
        abort(400, "Тип склада: physical или virtual")
    color = (data.get("color") or "").strip()
    w = Warehouse(name=name, wh_type=wh_type, color=color or None)
    db.session.add(w)
    db.session.commit()
    return jsonify(w.to_dict()), 201


@warehouses_bp.route("/<int:wid>", methods=["PUT"])
def update_warehouse(wid):
    w = Warehouse.query.get_or_404(wid)
    data = request.json or {}
    if "name" in data:
        name = data["name"].strip()
        if not name:
            abort(400, "Название не может быть пустым")
        existing = Warehouse.query.filter(Warehouse.name == name, Warehouse.id != wid).first()
        if existing:
            abort(400, "Склад с таким названием уже существует")
        w.name = name
    if "wh_type" in data:
        if data["wh_type"] not in ("physical", "virtual"):
            abort(400, "Тип склада: physical или virtual")
        w.wh_type = data["wh_type"]
    if "color" in data:
        w.color = data["color"].strip() or None
    db.session.commit()
    return jsonify(w.to_dict())


@warehouses_bp.route("/<int:wid>", methods=["DELETE"])
def delete_warehouse(wid):
    w = Warehouse.query.get_or_404(wid)
    if Unit.query.filter_by(warehouse_id=wid).first():
        abort(400, "Нельзя удалить склад с товарами")
    db.session.delete(w)
    db.session.commit()
    return jsonify({"ok": True})
