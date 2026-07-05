"""Модуль загрузки и поиска справочника ТН ВЭД ЕАЭС из CSV (источник: opencustoms)."""

import csv
import io
import sqlite3
from pathlib import Path

import requests

BASE_DIR = Path(__file__).parent.parent
TNVED_DB = BASE_DIR / "instance" / "tnved.db"
TNVED_CSV_URL = (
    "https://raw.githubusercontent.com/infoculture/opencustoms/master/data/tnved.csv"
)


def _ensure_db():
    conn = sqlite3.connect(str(TNVED_DB))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tnved (
            code TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            name_lower TEXT NOT NULL,
            parent_code TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tnved_name ON tnved(name_lower)")
    conn.commit()
    return conn


def _parent_code(code):
    """Вычисляет код родительской позиции: 9504908009 -> 95049080, 95049080 -> 950490."""
    if len(code) <= 2:
        return None
    return code[:-2] if len(code) > 4 else code[:2]


def load_tnved_db():
    """Скачивает CSV ТН ВЭД ЕАЭС и загружает в SQLite. Возвращает кол-во записей."""
    TNVED_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = _ensure_db()

    count = conn.execute("SELECT COUNT(*) FROM tnved").fetchone()[0]
    if count > 0:
        conn.close()
        return count

    print("[tnved] Скачивание ТН ВЭД ЕАЭС с GitHub (opencustoms)...")
    resp = requests.get(TNVED_CSV_URL, timeout=60)
    resp.raise_for_status()

    reader = csv.reader(io.StringIO(resp.text))
    records = []
    for row in reader:
        if len(row) < 2:
            continue
        code = row[0].strip()
        name = row[1].strip()
        if not code or not name or not code[0].isdigit():
            continue
        records.append((code, name, _parent_code(code)))

    if records:
        conn.executemany(
            "INSERT OR REPLACE INTO tnved (code, name, name_lower, parent_code) VALUES (?, ?, ?, ?)",
            [(c, n, n.lower(), p) for c, n, p in records],
        )
        conn.commit()
    conn.close()
    print(f"[tnved] Загружено {len(records)} записей")
    return len(records)


def search_tnved(query, limit=20):
    """Поиск по коду или описанию (регистронезависимый)."""
    conn = _ensure_db()
    q = f"%{query.lower()}%"
    rows = conn.execute(
        """SELECT code, name FROM tnved
           WHERE code LIKE ? OR name_lower LIKE ?
           ORDER BY LENGTH(code) DESC, code
           LIMIT ?""",
        (q, q, limit),
    ).fetchall()
    conn.close()
    return [{"code": r[0], "name": r[1]} for r in rows]


def get_tnved_by_code(code):
    """Точный поиск по коду."""
    conn = _ensure_db()
    row = conn.execute(
        "SELECT code, name FROM tnved WHERE code = ?", (code,)
    ).fetchone()
    conn.close()
    return {"code": row[0], "name": row[1]} if row else None
