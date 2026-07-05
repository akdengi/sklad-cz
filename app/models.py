from datetime import datetime
from sqlalchemy import or_
from app import db


class Warehouse(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    wh_type = db.Column(db.String(20), default="physical")
    color = db.Column(db.String(7), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        from sqlalchemy import func
        in_stock = Unit.query.filter(
            Unit.warehouse_id == self.id, Unit.status.notin_([4, 5])
        ).count()
        sold = Unit.query.filter(
            Unit.warehouse_id == self.id, Unit.status.in_([4, 5])
        ).count()

        sku_rows = db.session.query(
            SKU.id, SKU.name,
            func.sum(func.cast(Unit.status.notin_([4, 5]), db.Integer)).label("in_stock"),
            func.sum(func.cast(Unit.status.in_([4, 5]), db.Integer)).label("sold"),
        ).join(Unit, Unit.sku_id == SKU.id).filter(
            Unit.warehouse_id == self.id
        ).group_by(SKU.id, SKU.name).all()

        sku_breakdown = []
        for row in sku_rows:
            sku_breakdown.append({
                "sku_id": row.id,
                "sku_name": row.name,
                "in_stock": int(row.in_stock or 0),
                "sold": int(row.sold or 0),
            })

        return {
            "id": self.id,
            "name": self.name,
            "wh_type": self.wh_type or "physical",
            "color": self.color or "",
            "in_stock": in_stock,
            "sold": sold,
            "unit_count": in_stock + sold,
            "sku_breakdown": sku_breakdown,
        }


class SKU(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    article = db.Column(db.String(50), nullable=True)
    gtin14 = db.Column(db.String(14), nullable=True)
    ean13 = db.Column(db.String(13), nullable=True)
    production_date = db.Column(db.String(10), nullable=True)
    permit_doc = db.Column(db.Text, nullable=True)
    tnved_code = db.Column(db.String(20), nullable=True)
    total_quantity = db.Column(db.Integer, default=0)
    has_marking = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    units = db.relationship("Unit", backref="sku", lazy="dynamic",
                            cascade="all, delete-orphan")

    def to_dict(self):
        from sqlalchemy import func
        marked = self.units.filter(
            Unit.cz_code != None, Unit.cz_code != ''
        ).count()
        unmarked = self.units.filter(
            or_(Unit.cz_code == None, Unit.cz_code == '')
        ).count()
        total = self.units.filter(Unit.status.notin_([4, 5])).count()
        sold = self.units.filter(Unit.status.in_([4, 5])).count()
        sold_price = db.session.query(
            func.coalesce(func.sum(Unit.disposal_price), 0)
        ).filter(Unit.sku_id == self.id, Unit.status.in_([4, 5])).scalar()
        remaining = max(0, (self.total_quantity or 0) - total) if self.total_quantity else 0
        return {
            "id": self.id,
            "name": self.name,
            "article": self.article or "",
            "gtin14": self.gtin14,
            "ean13": self.ean13,
            "production_date": self.production_date,
            "permit_doc": self.permit_doc or "",
            "tnved_code": self.tnved_code or "",
            "total_quantity": self.total_quantity or 0,
            "has_marking": bool(self.has_marking),
            "marked_count": marked,
            "unmarked_count": unmarked,
            "total_units": total,
            "sold_count": sold,
            "sold_price": sold_price,
            "remaining": remaining,
        }


class Unit(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sku_id = db.Column(db.Integer, db.ForeignKey("sku.id"), nullable=False)
    cz_code = db.Column(db.Text, nullable=True)
    status = db.Column(db.Integer, default=0)
    warehouse_id = db.Column(db.Integer, db.ForeignKey("warehouse.id"), nullable=False)
    order_number = db.Column(db.String(100), nullable=True)
    sold_date = db.Column(db.String(10), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    disposal_type = db.Column(db.String(50), nullable=True)
    disposal_reason = db.Column(db.String(100), nullable=True)
    disposal_doc_type = db.Column(db.String(100), nullable=True)
    disposal_doc_name = db.Column(db.String(200), nullable=True)
    disposal_doc_number = db.Column(db.String(100), nullable=True)
    disposal_doc_date = db.Column(db.String(10), nullable=True)
    disposal_address = db.Column(db.String(500), nullable=True)
    disposal_fias_id = db.Column(db.String(50), nullable=True)
    disposal_price = db.Column(db.Float, nullable=True)
    disposal_status = db.Column(db.Integer, default=0)
    was_returned = db.Column(db.Boolean, default=False)

    cz_status = db.Column(db.String(100), nullable=True)
    cz_check_date = db.Column(db.String(30), nullable=True)

    warehouse = db.relationship("Warehouse", backref="units")

    def to_dict(self):
        return {
            "id": self.id,
            "sku_id": self.sku_id,
            "sku_name": self.sku.name if self.sku else None,
            "sku_article": self.sku.article if self.sku else None,
            "sku_permit_doc": self.sku.permit_doc if self.sku else None,
            "gtin14": self.sku.gtin14 if self.sku else None,
            "ean13": self.sku.ean13 if self.sku else None,
            "cz_code": self.cz_code or "",
            "status": self.status,
            "warehouse_id": self.warehouse_id,
            "warehouse_name": self.warehouse.name if self.warehouse else None,
            "order_number": self.order_number,
            "sold_date": self.sold_date,
            "disposal_type": self.disposal_type,
            "disposal_reason": self.disposal_reason,
            "disposal_doc_type": self.disposal_doc_type,
            "disposal_doc_name": self.disposal_doc_name,
            "disposal_doc_number": self.disposal_doc_number,
            "disposal_doc_date": self.disposal_doc_date,
            "disposal_address": self.disposal_address,
            "disposal_fias_id": self.disposal_fias_id or "",
            "disposal_price": self.disposal_price,
            "disposal_status": self.disposal_status,
            "was_returned": bool(self.was_returned),
            "has_marking": bool(self.sku.has_marking) if self.sku else True,
            "cz_status": self.cz_status or "",
            "cz_check_date": self.cz_check_date or "",
            "updated_at": self.updated_at.isoformat() if self.updated_at else self.created_at.isoformat() if self.created_at else "",
        }
