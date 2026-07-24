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
    "Подтверждено ЧЗ",
]

SETTINGS_PATH = Path(__file__).parent.parent / "instance" / "settings.json"

PRODUCT_GROUPS = [
    (1, "lp", "Лёгкая промышленность"),
    (5, "tires", "Шины и покрышки пневматические резиновые новые"),
    (6, "electronics", "Фотокамеры, фотовспышки и лампы-вспышки"),
    (9, "bicycle", "Велосипеды и велосипедные рамы"),
    (19, "antiseptic", "Антисептики и дезинфицирующие средства"),
    (27, "toys", "Игры и игрушки для детей"),
    (28, "radio", "Радиоэлектронная продукция"),
    (31, "titan", "Титановая металлопродукция"),
    (34, "opticfiber", "Оптоволокно и оптоволоконная продукция"),
    (35, "chemistry", "Косметика, бытовая химия и товары личной гигиены"),
    (36, "books", "Печатная продукция"),
    (39, "construction", "Строительные материалы"),
    (40, "fire", "Пиротехника и огнетушащее оборудование"),
    (41, "heater", "Отопительные приборы"),
    (42, "cableraw", "Кабельно-проводниковая продукция"),
    (43, "autofluids", "Моторные масла"),
    (44, "polymer", "Полимерные трубы"),
    (48, "carparts", "Автозапчасти и комплектующие транспортных средств"),
    (51, "gadgets", "Ноутбуки и смартфоны"),
    (53, "fertilizers", "Удобрения в потребительской упаковке"),
    (54, "homeware", "Товары для дома и интерьера"),
    (59, "pyrotechnics", "Пиротехнические изделия"),
]

DEFAULT_PRODUCT_GROUP = "27"


def get_product_group_code(numeric_id) -> str:
    """Получить строковый код товарной группы по числовому ID."""
    for pg_id, code, _ in PRODUCT_GROUPS:
        if str(pg_id) == str(numeric_id):
            return code
    return ""


def get_product_group_name(numeric_id) -> str:
    """Получить название товарной группы по числовому ID."""
    for pg_id, _, name in PRODUCT_GROUPS:
        if str(pg_id) == str(numeric_id):
            return name
    return f"Группа {numeric_id}"


def load_settings() -> dict:
    if SETTINGS_PATH.exists():
        return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    return {}


def save_settings(data: dict):
    SETTINGS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_cz(text: str) -> str:
    code = text.strip().strip('"')
    code = code.replace('""', '"').replace('\\"', '"')
    # Экранированные последовательности → реальные символы
    code = code.replace("\\u001d", GS).replace("\\u001D", GS)
    code = code.replace("\\x1d", GS).replace("\\X1D", GS)
    code = code.replace("\\u00e8", FNC1).replace("\\u00E8", FNC1)
    code = code.replace("\\xe8", FNC1).replace("\\xE8", FNC1)
    code = code.replace("\u241d", GS)
    code = code.replace("\ufffd", FNC1)
    # Текстовые литералы → реальные символы (порядок важен: сначала较长шие)
    code = code.replace("FNC1", FNC1)
    code = code.replace("\\GS\\", GS).replace("\\gs\\", GS)
    # Заменяем текстовый "GS" между AI на настоящий GS-символ
    # Ищем "GS" перед известными AI (01, 21, 91, 92)
    import re
    code = re.sub(r'(?<=\d)GS(?=01|21|91|92)', GS, code)
    code = re.sub(r'(?<=[A-Za-z0-9+/=])GS(?=01|21|91|92)', GS, code)
    # Общая замена оставшихся "GS" которые стоят перед AI
    code = re.sub(r'GS(?=\d{2})', GS, code)
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


def extract_gtin_from_cz(cz_code: str) -> str:
    """Извлечь GTIN-14 из кода ЧЗ (AI 01)."""
    if not cz_code:
        return ""
    code = normalize_cz(cz_code)
    code_body = code.replace(FNC1, "")
    idx = code_body.find("01")
    if idx == 0 and len(code_body) >= 16:
        gtin = code_body[2:16]
        if len(gtin) == 14 and gtin.isdigit():
            return gtin
    return ""


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


def validate_cz_code(cz_code: str, sku_gtin14: str = None) -> dict:
    """
    Валидация кода ЧЗ двумя методами:
    1. Проверка структуры (длины частей)
    2. Проверка криптохвоста (формат)
    + Сравнение GTIN КМ с GTIN карточки SKU
    Возвращает {"valid": bool, "warnings": list[str], "parts": dict}
    """
    warnings = []
    parts = {}

    if not cz_code:
        return {"valid": False, "warnings": ["Код ЧЗ пуст"], "parts": parts}

    code = normalize_cz(cz_code)
    code_body = code.replace(FNC1, "")

    # --- Метод 1: Проверка структуры кода ---

    # Извлекаем AI 01 (GTIN)
    idx_01 = code_body.find("01")
    if idx_01 != 0:
        warnings.append("Код должен начинаться с AI 01 (GTIN)")
    else:
        gtin_part = code_body[2:16] if len(code_body) >= 16 else ""
        parts["gtin"] = gtin_part
        if len(gtin_part) != 14:
            warnings.append(f"GTIN (AI 01): ожидается 14 цифр, получено {len(gtin_part)}")
        elif not gtin_part.isdigit():
            warnings.append(f"GTIN (AI 01): должен содержать только цифры")
        # Сравнение GTIN КМ с GTIN карточки SKU
        if sku_gtin14 and gtin_part and len(gtin_part) == 14:
            sku_gtin_clean = sku_gtin14.strip()
            if gtin_part != sku_gtin_clean:
                warnings.append(f"GTIN не совпадает с карточкой товара: в коде {gtin_part}, в карточке {sku_gtin_clean}")

    # Извлекаем AI 21 (серийный номер) — стандартный тип: ровно 13 символов
    # Ищем "21" только после GTIN (позиция 16+), чтобы не найти "21" внутри GTIN
    idx_21 = code_body.find("21", 16) if len(code_body) > 16 else -1
    if idx_21 < 0:
        warnings.append("Отсутствует AI 21 (серийный номер)")
    else:
        serial_start = idx_21 + 2
        serial_end = len(code_body)
        gs_pos = code_body.find(GS, serial_start)
        if gs_pos > 0:
            serial_end = gs_pos
        else:
            for ai in ("91", "92"):
                idx_ai = code_body.find(ai, serial_start)
                if idx_ai > 0 and idx_ai + 2 <= len(code_body):
                    serial_end = min(serial_end, idx_ai)
        serial_part = code_body[serial_start:serial_end]
        parts["serial"] = serial_part
        if len(serial_part) < 1:
            warnings.append("Серийный номер (AI 21): пустой")
        elif len(serial_part) != 13:
            warnings.append(f"Серийный номер (AI 21): ожидается 13 символов (стандартный тип), получено {len(serial_part)}")
        # Проверка на подозрительные символы в серийном номере
        import string as _str
        allowed_serial = set(_str.ascii_letters + _str.digits + "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~ ")
        invalid_serial = set(serial_part) - allowed_serial
        if invalid_serial:
            warnings.append(f"Серийный номер (AI 21): недопустимые символы: {''.join(sorted(invalid_serial))}")
        if "//" in serial_part or "\\\\" in serial_part:
            warnings.append(f"Серийный номер (AI 21): содержит подозрительные символы (// или \\\\)")
        if any(ord(c) >= 0x0400 and ord(c) <= 0x04FF for c in serial_part):
            warnings.append(f"Серийный номер (AI 21): содержит кириллические символы")

    # Извлекаем AI 91 (ключ проверки) — ищем после серийного номера
    # Определяем позицию окончания серийного номера
    serial_end_pos = len(code_body)
    if "serial" in parts and parts["serial"]:
        idx_21 = code_body.find("21", 16) if len(code_body) > 16 else -1
        if idx_21 >= 0:
            serial_end_pos = idx_21 + 2 + len(parts["serial"])

    idx_91 = code_body.find("91", serial_end_pos)
    if idx_91 >= 0 and (idx_91 == serial_end_pos or (idx_91 > 0 and code_body[idx_91 - 1] == GS)):
        key_start = idx_91 + 2
        key_end = len(code_body)
        # Ищем GS после ключа или начало AI 92
        idx_92 = code_body.find("92", key_start)
        if idx_92 > 0:
            # Ключ заканчивается перед GS или перед "92"
            if idx_92 > 1 and code_body[idx_92 - 1] == GS:
                key_end = idx_92 - 1  # Исключаем GS
            else:
                key_end = idx_92  # Исключаем "92"
        key_part = code_body[key_start:key_end]
        parts["key"] = key_part
        if len(key_part) != 4:
            warnings.append(f"Ключ проверки (AI 91): ожидается 4 символа, получено {len(key_part)}")

        # Извлекаем AI 92 (криптохвост) — ищем "92" после ключа
        idx_92_start = code_body.find("92", key_end)
        if idx_92_start >= 0 and idx_92_start <= key_end + 2:
            crypto_part = code_body[idx_92_start + 2:]
            parts["crypto"] = crypto_part
            if len(crypto_part) != 44:
                warnings.append(f"Криптохвост (AI 92): ожидается 44 символа, получено {len(crypto_part)}")
            import string
            valid_chars = set(string.ascii_letters + string.digits + "+/=")
            invalid_chars = set(crypto_part) - valid_chars
            if invalid_chars:
                warnings.append(f"Криптохвост (AI 92): недопустимые символы: {''.join(sorted(invalid_chars))}")
        else:
            warnings.append("Отсутствует AI 92 (криптохвост)")
    else:
        warnings.append("Отсутствует AI 91 (ключ проверки)")
        # Пробуем найти AI 92 без ключа
        idx_92_direct = code_body.find("92", serial_end_pos)
        if idx_92_direct >= 0 and (idx_92_direct == serial_end_pos or (idx_92_direct > 0 and code_body[idx_92_direct - 1] == GS)):
            crypto_part = code_body[idx_92_direct + 2:]
            parts["crypto"] = crypto_part
            if len(crypto_part) != 44:
                warnings.append(f"Криптохвост (AI 92): ожидается 44 символа, получено {len(crypto_part)}")

    valid = len(warnings) == 0
    return {"valid": valid, "warnings": warnings, "parts": parts}


def find_first_unmarked_unit(sku_id: int, warehouse_id: int = None) -> Unit:
    q = Unit.query.filter(
        Unit.sku_id == sku_id,
        or_(Unit.cz_code == None, Unit.cz_code == '')
    ).order_by(Unit.id.asc())
    if warehouse_id:
        q = q.filter(Unit.warehouse_id == warehouse_id)
    return q.first()



