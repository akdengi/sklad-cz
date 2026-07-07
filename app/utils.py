import json
from pathlib import Path
from sqlalchemy import or_
from app import db
from app.models import Unit

GS = "\u001d"
FNC1 = "\xe8"

STATUSES = [
    "— не указан —", "Эмитирован", "Нанесен",
    "В обороте", "Продан", "Выбыл"
]

DISPOSAL_TYPES = {
    "shipment": "Перед отгрузкой",
    "return": "При возврате товара",
}

DISPOSAL_REASONS = {
    "remote_sale": "Дистанционная продажа",
    "remote_sale_return": "Возврат при дистанционном способе продажи",
}

DISPOSAL_DOC_TYPES = [
    "прочее",
    "товарная накладная",
    "акт приема-передачи",
    "кассовый чек",
    "УПД",
]

DISPOSAL_STATUSES = [
    "Не начато",
    "Готов к отправке",
    "Отправлено в ЧЗ",
    "Подтверждено ЧЗ",
]

SETTINGS_PATH = Path(__file__).parent.parent / "instance" / "settings.json"


def load_settings() -> dict:
    if SETTINGS_PATH.exists():
        return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    return {}


def save_settings(data: dict):
    SETTINGS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_cz(text: str) -> str:
    code = text.strip().strip('"')
    code = code.replace('""', '"').replace('\\"', '"')
    code = code.replace("\\u001d", GS).replace("\\u001D", GS)
    code = code.replace("\\x1d", GS).replace("\\X1D", GS)
    code = code.replace("\\u00e8", FNC1).replace("\\u00E8", FNC1)
    code = code.replace("\\xe8", FNC1).replace("\\xE8", FNC1)
    code = code.replace("\u241d", GS)
    code = code.replace("\ufffd", FNC1)
    code = code.strip()
    if not code:
        return code
    if code[0] not in (FNC1, GS):
        code = FNC1 + code
    elif code[0] == GS:
        code = FNC1 + code[1:]
    code = FNC1 + code[1:].replace(FNC1, GS)
    for ai in ("91", "92"):
        idx = code.find(ai)
        if idx > 0 and code[idx - 1] != GS:
            code = code[:idx] + GS + code[idx:]
    return code


def cz_search_prefix(cz_code: str) -> str:
    if not cz_code:
        return cz_code
    idx = cz_code.find("91")
    if idx > 0:
        return cz_code[:idx]
    return cz_code


def cz_to_gs1_parenthesized(cz_code: str) -> str:
    code = normalize_cz(cz_code)
    code_body = code.replace(FNC1, '')
    parts = code_body.split(GS)
    result = ''
    for part in parts:
        if part.startswith('01') and len(part) >= 16:
            result += f'(01){part[2:16]}'
            tail = part[16:]
            if tail.startswith('21'):
                result += f'(21){tail[2:].replace(")", "~)")}'
        elif part.startswith('21'):
            result += f'(21){part[2:].replace(")", "~)")}'
        elif part.startswith('91'):
            result += f'(91){part[2:].replace(")", "~)")}'
        elif part.startswith('92'):
            result += f'(92){part[2:].replace(")", "~)")}'
        else:
            result += part
    return result


def cz_to_gs1_raw(cz_code: str) -> str:
    code = normalize_cz(cz_code)
    code_body = code.replace(FNC1, '')
    return code_body


def cz_to_datamatrix_data(cz_code: str) -> str:
    code = normalize_cz(cz_code)
    code = code.replace(FNC1, '^FNC1')
    return code


def parse_range(range_str: str) -> list[int]:
    result = []
    for part in range_str.split(","):
        part = part.strip().lstrip("#")
        if "-" in part:
            a, b = part.split("-", 1)
            a = a.strip().lstrip("#")
            b = b.strip().lstrip("#")
            result.extend(range(int(a), int(b) + 1))
        elif part:
            result.append(int(part))
    return result


def fmt_date_ru(d: str) -> str:
    if not d:
        return ""
    try:
        y, m, day = d.split("-")
        return f"{day}.{m}.{y}"
    except Exception:
        return d


def find_duplicate_unit(cz_code: str, exclude_unit_id: int = None) -> Unit:
    if not cz_code:
        return None
    normalized = normalize_cz(cz_code)
    prefix = cz_search_prefix(normalized)
    search = prefix if prefix.startswith(FNC1) else FNC1 + prefix
    q = Unit.query.filter(
        Unit.cz_code.like(f"{search}%"),
        Unit.cz_code != '',
    )
    if exclude_unit_id:
        q = q.filter(Unit.id != exclude_unit_id)
    candidates = q.all()
    for c in candidates:
        if normalize_cz(c.cz_code) == normalized:
            return c
    return None


def find_first_unmarked_unit(sku_id: int, warehouse_id: int = None) -> Unit:
    q = Unit.query.filter(
        Unit.sku_id == sku_id,
        or_(Unit.cz_code == None, Unit.cz_code == '')
    ).order_by(Unit.id.asc())
    if warehouse_id:
        q = q.filter(Unit.warehouse_id == warehouse_id)
    return q.first()



