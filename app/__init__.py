from pathlib import Path
from flask import Flask, render_template, g
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from app.config import SECRET_KEY, APP_VERSION

db = SQLAlchemy()

BASE_DIR = Path(__file__).parent.parent
DB_PATH = BASE_DIR / "instance" / "inventory.db"
DB_PATH.parent.mkdir(exist_ok=True)


def create_app():
    app = Flask(
        __name__,
        template_folder=str(BASE_DIR / "templates"),
        static_folder=str(BASE_DIR / "static"),
    )
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{DB_PATH}"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.secret_key = SECRET_KEY

    db.init_app(app)
    CORS(app)

    from app.routes.dashboard import dashboard_bp
    from app.routes.warehouses import warehouses_bp
    from app.routes.skus import skus_bp
    from app.routes.units import units_bp
    from app.routes.labels import labels_bp
    from app.routes.import_export import import_export_bp
    from app.routes.settings import settings_bp
    from app.routes.tnved import tnved_bp

    app.register_blueprint(dashboard_bp)
    app.register_blueprint(warehouses_bp, url_prefix="/api/warehouses")
    app.register_blueprint(skus_bp, url_prefix="/api/skus")
    app.register_blueprint(units_bp, url_prefix="/api/units")
    app.register_blueprint(labels_bp, url_prefix="/api/labels")
    app.register_blueprint(import_export_bp, url_prefix="/api")
    app.register_blueprint(settings_bp)
    app.register_blueprint(tnved_bp, url_prefix="/api/tnved")

    @app.context_processor
    def inject_version():
        return {"app_version": APP_VERSION}

    @app.route("/")
    def index():
        return render_template("index.html")

    return app


SQL_TYPE_MAP = {
    "INTEGER": "INTEGER",
    "String": "VARCHAR",
    "Text": "TEXT",
    "Float": "FLOAT",
    "Boolean": "BOOLEAN",
    "DateTime": "DATETIME",
}


def sqlalchemy_type_to_sql(col):
    type_obj = col.type
    type_name = type(type_obj).__name__
    sql_type = SQL_TYPE_MAP.get(type_name, "TEXT")
    if type_name == "String" and hasattr(type_obj, "length") and type_obj.length:
        sql_type = f"VARCHAR({type_obj.length})"
    return sql_type


def get_model_columns(model):
    cols = {}
    for name, col in model.__table__.columns.items():
        if col.name == "id":
            continue
        cols[col.name] = col
    return cols


def migrate_table(table_name, model, session):
    from sqlalchemy import inspect as sa_inspect, text
    engine = session.get_bind()
    insp = sa_inspect(engine)
    existing = {c["name"] for c in insp.get_columns(table_name)}
    model_cols = get_model_columns(model)
    added = []
    for name, col in model_cols.items():
        if name not in existing:
            sql_type = sqlalchemy_type_to_sql(col)
            default = ""
            if col.default is not None and col.default.is_scalar:
                default_val = col.default.arg
                if isinstance(default_val, bool):
                    default = f" DEFAULT {int(default_val)}"
                elif default_val is not None:
                    default = f" DEFAULT {repr(default_val)}"
            nullable = "" if col.nullable else " NOT NULL"
            sql = f"ALTER TABLE {table_name} ADD COLUMN {name} {sql_type}{default}{nullable}"
            try:
                session.execute(text(sql))
                session.commit()
                added.append(name)
            except Exception:
                session.rollback()
    if added:
        print(f"  [migration] {table_name}: +{', '.join(added)}")
    return added


def init_db(app):
    with app.app_context():
        from app.models import Warehouse, SKU, Unit

        db.create_all()

        migrate_table("warehouse", Warehouse, db.session)
        migrate_table("sku", SKU, db.session)
        migrate_table("unit", Unit, db.session)

        try:
            from app.tnved import load_tnved_db
            load_tnved_db()
        except Exception as e:
            print(f"[warn] Не удалось загрузить справочник ТН ВЭД: {e}")

        from sqlalchemy import text
        try:
            db.session.execute(text("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_unit_cz_unique
                ON unit(cz_code)
                WHERE cz_code IS NOT NULL AND cz_code != ''
            """))
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f"[warn] Не удалось создать уникальный индекс: {e}")

        if Warehouse.query.count() == 0:
            for name in ["Склад", "Озон", "Яндекс Маркет"]:
                db.session.add(Warehouse(name=name))
            db.session.commit()

        from app.utils import load_settings
        s = load_settings()
        if s.get("backup_rotation"):
            from app.routes.settings import BACKUPS_DIR, DB_PATH as BP_PATH, _backup_sort_key
            from app.config import MAX_BACKUPS
            import shutil, os as _os
            from datetime import datetime as _dt
            BACKUPS_DIR.mkdir(exist_ok=True)
            if BP_PATH.exists():
                ts = _dt.now().strftime("%Y%m%d_%H%M%S")
                bp = BACKUPS_DIR / f"backup_{ts}.db"
                shutil.copy2(str(BP_PATH), str(bp))
                existing = sorted(BACKUPS_DIR.glob("backup_*.db"), key=_backup_sort_key)
                while len(existing) > MAX_BACKUPS:
                    _os.remove(str(existing.pop(0)))
                print(f"  [auto-backup] {bp.name}")
