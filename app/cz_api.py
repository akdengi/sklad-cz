import sys
import os
import requests
import base64
import json
import logging
from datetime import datetime
from app.utils import load_settings, get_product_group_code
from app.config import CZ_API_URL

_uuid_token = None

CADES_BES = 0x20
CADESCOM_CURRENT_USER_STORE = 1
CADESCOM_LOCAL_MACHINE_STORE = 2
CAPICOM_STORE_OPEN_READ_ONLY = 0

_platform = sys.platform


def _is_windows():
    return _platform == "win32"


def _com_init():
    if _is_windows():
        import pythoncom
        try:
            pythoncom.CoInitialize()
        except Exception:
            pass


def _com_uninit():
    if _is_windows():
        import pythoncom
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass


def _get_base_url():
    s = load_settings()
    return s.get("cz_api_url", CZ_API_URL).rstrip("/")


# ===== Windows COM (win32com) =====

def _get_com_store(location=CADESCOM_LOCAL_MACHINE_STORE):
    import win32com.client
    store = win32com.client.Dispatch("CAdESCOM.Store")
    store.Open(location, "My", CAPICOM_STORE_OPEN_READ_ONLY)
    return store


def _get_cert_by_thumbprint_win(thumbprint):
    import win32com.client
    _com_init()
    try:
        target = thumbprint.upper()
        for loc in [CADESCOM_LOCAL_MACHINE_STORE, CADESCOM_CURRENT_USER_STORE]:
            try:
                store = win32com.client.Dispatch("CAdESCOM.Store")
                store.Open(loc, "My", CAPICOM_STORE_OPEN_READ_ONLY)
                certs = store.Certificates
                for i in range(1, certs.Count + 1):
                    cert = certs.Item(i)
                    tp = cert.Thumbprint.upper()
                    if tp == target:
                        return cert
                store.Close()
            except Exception:
                continue
        return None
    finally:
        _com_uninit()


def _sign_data_win(data_to_sign: str, thumbprint: str) -> str:
    import win32com.client
    import base64
    _com_init()
    try:
        target = thumbprint.upper()
        cert = None
        for loc in [CADESCOM_LOCAL_MACHINE_STORE, CADESCOM_CURRENT_USER_STORE]:
            try:
                store = win32com.client.Dispatch("CAdESCOM.Store")
                store.Open(loc, "My", CAPICOM_STORE_OPEN_READ_ONLY)
                certs = store.Certificates
                for i in range(1, certs.Count + 1):
                    c = certs.Item(i)
                    if c.Thumbprint.upper() == target:
                        cert = c
                        break
                store.Close()
                if cert:
                    break
            except Exception:
                continue
        if not cert:
            available = []
            for loc in [CADESCOM_LOCAL_MACHINE_STORE, CADESCOM_CURRENT_USER_STORE]:
                try:
                    store = win32com.client.Dispatch("CAdESCOM.Store")
                    store.Open(loc, "My", CAPICOM_STORE_OPEN_READ_ONLY)
                    for i in range(1, store.Certificates.Count + 1):
                        available.append(store.Certificates.Item(i).Thumbprint.upper())
                    store.Close()
                except Exception:
                    pass
            raise Exception(f"Certificate {thumbprint} not found! Available: {available[:5]}")

        signer = win32com.client.Dispatch("CAdESCOM.CPSigner")
        signer.Certificate = cert
        signer.CheckCertificate = True

        sd = win32com.client.Dispatch("CAdESCOM.CadesSignedData")
        sd.ContentEncoding = 1
        b64 = base64.b64encode(data_to_sign.encode("utf-8")).decode("ascii")
        sd.Content = b64

        signature = sd.SignCades(signer, 1, False, 0)
        return signature.replace("\r\n", "").replace("\n", "")
    finally:
        _com_uninit()


def _list_certs_win() -> list:
    import win32com.client
    _com_init()
    try:
        s = load_settings()
        target_inn = s.get("cz_inn", "")
        results = []
        stores = [
            (CADESCOM_LOCAL_MACHINE_STORE, "Local Machine"),
            (CADESCOM_CURRENT_USER_STORE, "Current User"),
        ]
        seen = set()
        for location, store_label in stores:
            try:
                store = win32com.client.Dispatch("CAdESCOM.Store")
                store.Open(location, "My", CAPICOM_STORE_OPEN_READ_ONLY)
                certs = store.Certificates
                for i in range(1, certs.Count + 1):
                    cert = certs.Item(i)
                    thumbprint = cert.Thumbprint.upper()
                    if thumbprint in seen:
                        continue
                    seen.add(thumbprint)
                    try:
                        has_priv = cert.HasPrivateKey()
                    except Exception:
                        has_priv = False
                    if not has_priv:
                        continue
                    try:
                        subject = cert.SubjectName
                    except Exception:
                        subject = "Unknown"
                    try:
                        issuer = cert.IssuerName
                    except Exception:
                        issuer = "Unknown"
                    if target_inn and target_inn not in subject:
                        continue
                    results.append({
                        "thumbprint": thumbprint,
                        "subject": subject,
                        "issuer": issuer,
                        "has_private_key": True,
                        "store": store_label,
                    })
                store.Close()
            except Exception:
                continue
        return results
    finally:
        _com_uninit()


# ===== Linux (pycades) =====

def _sign_data_linux(data_to_sign: str, thumbprint: str) -> str:
    import pycades
    store = pycades.Store()
    store.Open(pycades.CADESCOM_CURRENT_USER_STORE, pycades.CAPICOM_MY_STORE, pycades.CAPICOM_STORE_OPEN_READ_ONLY)
    certs = store.Certificates.Find(pycades.CAPICOM_CERTIFICATE_FIND_SHA1_HASH, thumbprint)
    if certs.Count == 0:
        raise Exception(f"Certificate {thumbprint} not found!")
    cert = certs.Item(1)
    signer = pycades.Signer()
    signer.Certificate = cert
    signer.CheckCertificate = True
    signed_data = pycades.SignedData()
    signed_data.Content = data_to_sign
    signature_base64 = signed_data.SignCades(signer, pycades.CADESCOM_CADES_BES, True)
    return signature_base64.replace("\r", "").replace("\n", "")


def _list_certs_linux() -> list:
    import pycades
    results = []
    stores = [
        (pycades.CADESCOM_CURRENT_USER_STORE, "Current User"),
        (pycades.CADESCOM_LOCAL_MACHINE_STORE, "Local Machine"),
    ]
    seen = set()
    for store_location, store_label in stores:
        try:
            store = pycades.Store()
            store.Open(store_location, pycades.CAPICOM_MY_STORE, pycades.CAPICOM_STORE_OPEN_READ_ONLY)
            certs = store.Certificates
            for i in range(1, certs.Count + 1):
                cert = certs.Item(i)
                thumbprint = cert.Thumbprint()
                if thumbprint in seen:
                    continue
                seen.add(thumbprint)
                try:
                    subject = cert.SubjectName()
                except Exception:
                    subject = "Unknown"
                try:
                    issuer = cert.IssuerName()
                except Exception:
                    issuer = "Unknown"
                try:
                    has_priv = cert.HasPrivateKey()
                except Exception:
                    has_priv = False
                results.append({
                    "thumbprint": thumbprint,
                    "subject": subject,
                    "issuer": issuer,
                    "has_private_key": bool(has_priv),
                    "store": store_label,
                })
        except Exception:
            continue
    return results


# ===== Unified interface =====

def _sign_data(data_to_sign: str, thumbprint: str) -> str:
    if _is_windows():
        return _sign_data_win(data_to_sign, thumbprint)
    return _sign_data_linux(data_to_sign, thumbprint)


def list_certificates() -> list:
    if _is_windows():
        return _list_certs_win()
    return _list_certs_linux()


# ===== CrPT API =====

def get_uuid_token(thumbprint: str = None) -> str:
    global _uuid_token
    if _uuid_token:
        return _uuid_token

    s = load_settings()

    if not thumbprint:
        thumbprint = s.get("cz_cert_thumbprint", "")
    if not thumbprint:
        raise Exception("Certificate thumbprint not set. Configure in Settings > Chestny Znak.")

    base = _get_base_url()
    key_url = f"{base}/auth/key"
    response = requests.get(key_url, headers={"accept": "application/json"}, timeout=15)
    response.raise_for_status()
    auth_data = response.json()

    signature = _sign_data(auth_data["data"], thumbprint)

    inn = s.get("cz_inn", "")
    signin_url = f"{base}/auth/simpleSignIn"
    payload = {
        "uuid": auth_data["uuid"],
        "data": signature,
        "unitedToken": True,
    }
    if inn:
        payload["inn"] = inn
    headers = {"Content-Type": "application/json", "accept": "application/json"}
    token_response = requests.post(signin_url, json=payload, headers=headers, timeout=15)

    if token_response.status_code == 403:
        try:
            err = token_response.json().get("error_message", "")
        except Exception:
            err = token_response.text
        if "Подпись невалидна" in err or "nevalidna" in err.lower():
            raise Exception(
                f"Signature invalid (error 4).\n"
                f"csptest produces PKCS#7 but API expects CAdES-BES.\n"
                f"Install csptestf or use CAdESCOM with PIN.\n"
                f"Server: {err}"
            )
        if "Отсутствует доступ" in err or "access" in err.lower():
            raise Exception(
                f"403 Access denied: {err}\n"
                f"Certificate is not registered as API user on markirovka.crpt.ru."
            )
        raise Exception(f"Auth failed (403): {err}")

    token_response.raise_for_status()
    resp = token_response.json()
    _uuid_token = resp.get("token") or resp.get("uuidToken", "")
    return _uuid_token


def reset_token():
    global _uuid_token
    _uuid_token = None


def get_cis_info(token: str, codes_list: list) -> list:
    base = _get_base_url()
    info_url = f"{base}/cises/info"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
        "accept": "application/json",
    }
    if len(codes_list) > 1000:
        raise ValueError(f"Too many codes: {len(codes_list)}. Max 1000 per request.")
    response = requests.post(info_url, json=codes_list, headers=headers, timeout=30)
    if response.status_code in (200, 404):
        return response.json()
    response.raise_for_status()
    return response.json()


def check_cz_status(cz_codes: list, thumbprint: str = None) -> dict:
    if not thumbprint:
        s = load_settings()
        thumbprint = s.get("cz_cert_thumbprint", "")
    if not thumbprint:
        raise Exception("Certificate thumbprint not set. Configure in Settings > Chestny Znak.")

    codes_clean = []
    for code in cz_codes:
        clean = code.strip()
        if not clean or len(clean) < 18:
            continue
        clean = clean.replace("\xe8", "").replace("\u001d", "")
        # Ищем начало зашифрованных данных AI 91 (91EE12),
        # чтобы не найти "91" внутри GTIN или значения AI 92
        idx91 = clean.find("91EE12")
        if idx91 > 0:
            clean = clean[:idx91]
        clean = clean.strip()
        if clean and len(clean) >= 18:
            codes_clean.append(clean)
    if not codes_clean:
        return {"results": []}

    try:
        token = get_uuid_token(thumbprint)
    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 401:
            reset_token()
            token = get_uuid_token(thumbprint)
        else:
            raise

    all_results = []
    BATCH = 900
    for i in range(0, len(codes_clean), BATCH):
        batch = codes_clean[i:i + BATCH]
        try:
            results = get_cis_info(token, batch)
            all_results.extend(results)
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 401:
                reset_token()
                token = get_uuid_token(thumbprint)
                results = get_cis_info(token, batch)
                all_results.extend(results)
            else:
                raise

    return {"results": all_results}


def check_document_status_by_id(
    doc_id: str,
    thumbprint: str = None,
    pg: str = None
) -> dict:
    """
    Получает информацию о документе из ГИС МТ по его ID.
    
    Args:
        doc_id: Идентификатор документа (номер как в ответе создания)
        thumbprint: Отпечаток сертификата ЭЦП
        pg: Код товарной группы
        
    Returns:
        dict: Результат с информацией о документе или ошибкой
    """
    s = load_settings()
    if not thumbprint:
        thumbprint = s.get("cz_cert_thumbprint", "")
    if not thumbprint:
        raise Exception("Certificate thumbprint not set")
    
    # Получаем код товарной группы
    if not pg:
        product_group_id = s.get("product_group", "27")
        pg = get_product_group_code(product_group_id)
        if not pg:
            raise Exception(f"Could not determine product group code for ID {product_group_id}")
    
    try:
        token = get_uuid_token(thumbprint)
    except:
        reset_token()
        token = get_uuid_token(thumbprint)
    
    base_url = _get_base_url()
    
    headers = {
        "accept": "application/json",
        "Authorization": f"Bearer {token}",
    }
    
    url = f"{base_url}/doc/list"
    
    params = {
        "pg": pg,
        "number": doc_id,
        "documentStatus": "",  # Получаем все статусы
    }
    
    try:
        r = requests.get(url, params=params, headers=headers, timeout=30)
        
        if r.status_code == 401:
            reset_token()
            token = get_uuid_token(thumbprint)
            headers["Authorization"] = f"Bearer {token}"
            r = requests.get(url, params=params, headers=headers, timeout=30)
        
        if r.status_code >= 400:
            return {
                "success": False,
                "error_code": r.status_code,
                "error_message": f"HTTP {r.status_code}: {r.text[:800]}"
            }
        
        try:
            result = r.json()
            # Возвращаем первую запись из списка
            documents = result.get("results", [])
            if documents:
                return {"success": True, "data": documents[0]}
            else:
                return {"success": False, "error_message": "Document not found"}
        except Exception as e:
            return {
                "success": False,
                "error_code": 0,
                "error_message": f"Non-JSON response: {r.text[:500]}"
            }
            
    except requests.exceptions.RequestException as e:
        return {"success": False, "error_code": 0, "error_message": str(e)}


# ===== Код для создания документов (LK_RECEIPT) =====

# Типы документов для ввода/вывода по справочнику ЧЗ
DOC_TYPE_LK_RECEIPT = 54       # LK_RECEIPT - Вывод из оборота (JSON)
DOC_TYPE_LK_RECEIPT_XML = 49   # LK_RECEIPT_XML - Вывод из оборота (XML)
DOC_TYPE_LK_RECEIPT_CSV = 52   # LK_RECEIPT_CSV - Вывод из оборота (CSV)
DOC_TYPE_LK_RECEIPT_CANCEL = 236  # LK_RECEIPT_CANCEL - Отмена вывода из оборота

# Типы универсальных документов
DOC_TYPE_UNIVERSAL_TRANSFER_DOCUMENT = 1      # УПД
DOC_TYPE_UNIVERSAL_CORRECTION_DOCUMENT = 7    # УКД
DOC_TYPE_WRITE_OFF = 9                        # Списание
DOC_TYPE_AGGREGATION = 2                      # Формирование упаковки

# Доступные для разных товарных групп
# АЛКОГОЛЬ и ПИВО: FIXATION (239), UNIVERSAL_TRANSFER_DOCUMENT (1, 10), 
#                  UNIVERSAL_CORRECTION_DOCUMENT (7, 11)
# ВЕТЕРИНАРНЫЕ ПРЕПАРАТЫ: UNIVERSAL_TRANSFER_DOCUMENT (1, 10),
#                         UNIVERSAL_CORRECTION_DOCUMENT (7, 11)

DOCUMENT_TYPE_CODES = {
    "LK_RECEIPT": DOC_TYPE_LK_RECEIPT,
    "LK_RECEIPT_XML": DOC_TYPE_LK_RECEIPT_XML,
    "LK_RECEIPT_CSV": DOC_TYPE_LK_RECEIPT_CSV,
    "LK_RECEIPT_CANCEL": DOC_TYPE_LK_RECEIPT_CANCEL,
}


def get_document_type_code(type_name: str) -> int:
    """Получить числовой код типа документа по имени."""
    return DOCUMENT_TYPE_CODES.get(type_name, 0)


def export_units_for_disposal(units: list) -> dict:
    """
    Генерирует CSV-файл для экспорта данных о единицах, готовых к выводу из оборота.
    
    Args:
        units: Список словарей с данными единиц (из Unit.to_dict())
        
    Returns:
        dict: Результат с CSV-данными
    """
    import io
    import csv
    
    buf = io.StringIO()
    buf.write("\ufeff")
    
    w = csv.writer(buf, delimiter=";", lineterminator="\n")
    w.writerow([
        "ID", "Код ЧЗ (полный)", "Код для ввода в оборот", "SKU",
        "Тип операции", "Причина выбытия",
        "Вид первичного документа", "Наименование документа",
        "Номер документа", "Дата документа",
        "Адрес места выбытия", "Цена за единицу"
    ])
    
    for u in units:
        full = u.get("cz_code", "") or ""
        turnover = full.split("\u001d")[0] if full else ""
        
        w.writerow([
            u.get("id", ""),
            full,
            turnover,
            u.get("sku_name", ""),
            u.get("disposal_type", ""),
            u.get("disposal_reason", ""),
            u.get("disposal_doc_type", ""),
            u.get("disposal_doc_name", ""),
            u.get("disposal_doc_number", ""),
            u.get("disposal_doc_date", ""),
            u.get("disposal_address", ""),
            u.get("disposal_price", "")
        ])
    
    return {
        "csv": buf.getvalue(),
        "count": len(units)
    }


def create_receipt_document(
    cz_codes: list,
    thumbprint: str = None,
    pg: str = None,
    document_format: str = "MANUAL",
    unit_data: dict = None
) -> dict:
    """
    Создает документ LK_RECEIPT (вывод из оборота) для указанных кодов ЧЗ.
    
    Args:
        cz_codes: Список кодов ЧЗ для вывода из оборота
        thumbprint: Отпечаток сертификата ЭЦП
        pg: Код товарной группы (например, 'toys', 'lp')
        document_format: Формат документа ('MANUAL')
        unit_data: Данные единицы для заполнения документа:
            {
                "disposal_type": "shipment",
                "disposal_reason": "remote_sale",
                "disposal_doc_type": "other",
                "disposal_doc_name": "Заказ ...",
                "disposal_doc_number": "...",
                "disposal_doc_date": "2024-01-15",
                "disposal_address": "...",
                "disposal_fias_id": "...",
                "disposal_price": 5504.0
            }
        
    Returns:
        dict: Результат с id документа或 сообщением об ошибке
    """
    s = load_settings()
    if not thumbprint:
        thumbprint = s.get("cz_cert_thumbprint", "")
    if not thumbprint:
        raise Exception("Certificate thumbprint not set")
    
    # Получаем код товарной группы
    if not pg:
        product_group_id = s.get("product_group", "27")
        pg = get_product_group_code(product_group_id)
        if not pg:
            raise Exception(f"Could not determine product group code for ID {product_group_id}")
    
    token = get_uuid_token(thumbprint)
    base_url = _get_base_url()
    
    # Формируем JSON документ (LK_RECEIPT)
    inn = s.get("cz_inn", "")
    fias_id = s.get("default_disposal_fias_id", "")
    
    action = "DISTANCE"
    withdrawal_type_other = ""
    document_type = "OTHER"
    
    logging.debug(f"Creating LK_RECEIPT with INN: {inn}, FIAS: {fias_id}")
    
    # Генерируем номер документа
    doc_number = f"ВЫВОД-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
    
    from app.utils import GS, FNC1
    
    # Извлекаем КИ (без криптохвоста) из полных кодов ЧЗ
    cis_codes = []
    for code in cz_codes:
        parts = code.split(GS)
        first_part = parts[0] if parts else ""
        cis_code = first_part.lstrip(FNC1)
        cis_codes.append(cis_code)
    
    product_data = {
        "inn": inn,
        "action": action,
        "action_date": datetime.utcnow().strftime("%Y-%m-%d"),
        "withdrawal_type_other": withdrawal_type_other,
        "document_type": document_type or (unit_data.get("disposal_doc_type") if unit_data else "OTHER"),
        "document_number": (unit_data.get("disposal_doc_number") or "") if unit_data else doc_number,
        "document_date": (unit_data.get("disposal_doc_date") or datetime.utcnow().strftime("%Y-%m-%d")) if unit_data else datetime.utcnow().strftime("%Y-%m-%d"),
        "primary_document_custom_name": (unit_data.get("disposal_doc_name") or "") if unit_data else "",
        "products": [
            {"cis": code, "unit_price_in_kopeks": 0} for code in cis_codes
        ]
    }
    
    # Применяем данные из unit_data если есть
    if unit_data:
        doc_number_from_unit = unit_data.get("disposal_doc_number")
        if doc_number_from_unit:
            product_data["document_number"] = doc_number_from_unit
        
        doc_date_from_unit = unit_data.get("disposal_doc_date")
        if doc_date_from_unit:
            product_data["document_date"] = doc_date_from_unit
        
        doc_name_from_unit = unit_data.get("disposal_doc_name")
        if doc_name_from_unit:
            product_data["primary_document_custom_name"] = doc_name_from_unit
    
    # Добавляем fias_id если есть вunit_data или в настройках
    fias_id_to_use = (unit_data or {}).get("disposal_fias_id") or fias_id
    if fias_id_to_use:
        product_data["fias_id"] = fias_id_to_use

    # Добавляем ИНН покупателя если есть
    buyer_inn = (unit_data or {}).get("buyer_inn")
    if buyer_inn:
        product_data["buyer_inn"] = buyer_inn
    
    # Определяем цену - берем из unit_data или используем 0
    price_kopeks = 0
    if unit_data:
        disposal_price = unit_data.get("disposal_price", 0)
        if disposal_price and isinstance(disposal_price, (int, float)):
            price_kopeks = int(disposal_price * 100)  # рубли в копейки
    
    # Обновляем products с ценой и другими полями
    product_data["products"] = [
        {
            "cis": code,
            "product_cost": price_kopeks,
            "primary_document_type": document_type or (unit_data.get("disposal_doc_type") if unit_data else "OTHER"),
            "primary_document_number": (unit_data or {}).get("disposal_doc_number") or "",
            "primary_document_date": (unit_data or {}).get("disposal_doc_date") or datetime.utcnow().strftime("%Y-%m-%d"),
            "primary_document_custom_name": (unit_data or {}).get("disposal_doc_name") or ""
        } for code in cis_codes
    ]
    
    doc_json = json.dumps(product_data, ensure_ascii=False, separators=(",", ":"))
    
    # Кодируем в base64 (product_document)
    product_document_b64 = base64.b64encode(doc_json.encode("utf-8")).decode("ascii")
    
    # Подписываем исходный JSON документа
    signature = _sign_data(doc_json, thumbprint)
    
    payload = {
        "document_format": "MANUAL",
        "product_document": product_document_b64,
        "type": "LK_RECEIPT",
        "signature": signature,
    }
    
    url = f"{base_url}/lk/documents/create"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
        "accept": "application/json",
    }
    
    # Отправляем запрос с pg как query параметром
    r = requests.post(
        url,
        params={"pg": pg},
        json=payload,
        headers=headers,
        timeout=60,
    )
    
    if r.status_code == 401:
        reset_token()
        token = get_uuid_token(thumbprint)
        headers["Authorization"] = f"Bearer {token}"
        r = requests.post(
            url,
            params={"pg": pg},
            json=payload,
            headers=headers,
            timeout=60,
        )
    
    if r.status_code >= 400:
        return {
            "success": False,
            "error_code": r.status_code,
            "error_message": f"HTTP {r.status_code}: {r.text[:800]}"
        }
    
    # Если ответ JSON, возвращаем данные
    try:
        result = r.json()
        logging.debug(f"[receipt] API response: {result}")
        return {"success": True, "data": result}
    except Exception as e:
        # Ответ не JSON (например, просто UUID документа) - это может быть успех
        msg = f"Non-JSON response (possibly document ID): {r.text[:200]}"
        logging.debug(msg) if logging.root.level <= logging.DEBUG else None
        if r.status_code in (200, 201):
            return {
                "success": True,
                "data": {"document_id": r.text.strip()},
                "raw_response": r.text[:500]
            }
        else:
            return {
                "success": False,
                "error_code": r.status_code,
                "error_message": f"HTTP {r.status_code}: {r.text[:500]}"
            }


def cancel_receipt_document(
    document_id: str,
    thumbprint: str = None,
    pg: str = None
) -> dict:
    """
    Отменяет документ LK_RECEIPT (вывод из оборота).
    
    Args:
        document_id: ID документа, который нужно отменить
        thumbprint: Отпечаток сертификата ЭЦП
        pg: Код товарной группы
        
    Returns:
        dict: Результат операции
    """
    if not thumbprint:
        s = load_settings()
        thumbprint = s.get("cz_cert_thumbprint", "")
    if not thumbprint:
        raise Exception("Certificate thumbprint not set. Configure in Settings > Chestny Znak.")
    
    # Получаем код товарной группы
    if not pg:
        s = load_settings()
        product_group_id = s.get("product_group", "27")
        pg = get_product_group_code(product_group_id)
        if not pg:
            raise Exception(f"Could not determine product group code for ID {product_group_id}")
    
    base_url = _get_base_url()
    url = f"{base_url}/lk/documents/cancel?pg={pg}"
    
    headers = {
        "Content-Type": "application/json",
        "accept": "application/json",
    }
    
    try:
        # Получаем токен
        token = get_uuid_token(thumbprint)
        headers["Authorization"] = f"Bearer {token}"
        
        # Подписываем document_id
        product_json = json.dumps({"documentId": document_id}, ensure_ascii=False)
        product_b64 = base64.b64encode(product_json.encode("utf-8")).decode("ascii")
        signature = _sign_data(product_json, thumbprint)
        
        payload = {
            "document_format": "MANUAL",
            "product_document": product_b64,
            "type": get_document_type_code("LK_RECEIPT_CANCEL"),
            "signature": signature,
        }
        
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        
        if response.status_code in (200, 201):
            result = response.json()
            return {"success": True, "data": result}
        else:
            error_msg = response.text
            try:
                err_json = response.json()
                error_msg = err_json.get("error_message", error_msg)
            except Exception:
                pass
            return {
                "success": False,
                "error_code": response.status_code,
                "error_message": error_msg
            }

    except requests.exceptions.RequestException as e:
        return {"success": False, "error_code": 0, "error_message": str(e)}


# ===== Ввод в оборот =====

INTRODUCTION_TYPES = {
    "production": {"doc_type": "LP_INTRODUCE_GOODS", "label": "Производство в РФ"},
    "remains":    {"doc_type": "LP_INTRODUCE_OST",   "label": "Остатки"},
    "contract":   {"doc_type": "LK_CONTRACT_COMMISSIONING", "label": "Контрактное производство"},
    "import_fts": {"doc_type": "LP_FTS_INTRODUCE",   "label": "Импорт с ФТС"},
}


def _extract_cis_code(cz_code: str) -> str:
    """Извлечь КИ (до первого GS) из полного кода ЧЗ."""
    from app.utils import GS, FNC1
    parts = cz_code.split(GS)
    first_part = parts[0] if parts else ""
    return first_part.lstrip(FNC1)


def create_introduction_document(
    introduction_type: str,
    unit_ids: list,
    thumbprint: str = None,
    form_data: dict = None,
) -> dict:
    """
    Создаёт документ ввода в оборот.

    introduction_type: 'production' | 'remains' | 'contract' | 'import_fts'
    unit_ids:          список ID единиц (Unit.id)
    form_data:         данные из формы (depending on type):
        - production:  {production_date, producer_inn?, owner_inn?, certificate_data?}
        - remains:     {country, declaration_number, declaration_date, certificate_data?}
        - contract:    {production_date, producer_inn?, owner_inn?, certificate_data?}
        - import_fts:  {declaration_number, declaration_date, production_date?}
    """
    s = load_settings()
    if not thumbprint:
        thumbprint = s.get("cz_cert_thumbprint", "")
    if not thumbprint:
        raise Exception("Certificate thumbprint not set. Configure in Settings > Chestny Znak.")

    info = INTRODUCTION_TYPES.get(introduction_type)
    if not info:
        raise Exception(f"Unknown introduction type: {introduction_type}")

    form_data = form_data or {}

    product_group_id = s.get("product_group", "27")
    pg = get_product_group_code(product_group_id)
    if not pg:
        raise Exception(f"Could not determine product group code for ID {product_group_id}")

    from app.models import Unit, db
    units = Unit.query.filter(Unit.id.in_(unit_ids)).all()
    if not units:
        raise Exception("No units found")

    inn = s.get("cz_inn", "")

    if introduction_type == "production":
        producer_inn = form_data.get("producer_inn") or inn
        owner_inn = form_data.get("owner_inn") or inn
        production_date = form_data.get("production_date") or datetime.utcnow().strftime("%Y-%m-%d")
        products = []
        for u in units:
            cis = _extract_cis_code(u.cz_code or "")
            if not cis:
                continue
            product = {"uit_code": cis, "tnved_code": u.sku.tnved_code or "0000000000"}
            cert_data = _build_certificate_data(form_data, u)
            if cert_data:
                product["certificate_document_data"] = cert_data
            products.append(product)
        doc_body = {
            "participant_inn": inn,
            "producer_inn": producer_inn,
            "owner_inn": owner_inn,
            "production_date": production_date,
            "production_type": "OWN_PRODUCTION",
            "products": products,
        }

    elif introduction_type == "remains":
        trade_inn = inn  # всегда ИНН собственника из настроек
        country = form_data.get("country") or "643"
        declaration_number = form_data.get("declaration_number") or ""
        declaration_date = form_data.get("declaration_date") or ""
        products = []
        for u in units:
            cis = _extract_cis_code(u.cz_code or "")
            if not cis:
                continue
            product = {"ki": cis, "country": country}
            if declaration_number:
                product["declaration_number"] = declaration_number
            if declaration_date:
                product["declaration_date"] = declaration_date
            cert_data = _build_certificate_data(form_data, u)
            if cert_data:
                product["certificate_document_data"] = cert_data
            products.append(product)
        doc_body = {
            "trade_participant_inn": trade_inn,
            "products_list": products,
        }

    elif introduction_type == "contract":
        producer_inn = form_data.get("producer_inn") or inn
        owner_inn = form_data.get("owner_inn") or inn
        production_date = form_data.get("production_date") or datetime.utcnow().strftime("%Y-%m-%d")
        products = []
        for u in units:
            cis = _extract_cis_code(u.cz_code or "")
            if not cis:
                continue
            product = {"uit": cis, "tnved_code": u.sku.tnved_code or "0000000000"}
            cert_data = _build_certificate_data(form_data, u)
            if cert_data:
                product["certificate_document_data"] = cert_data
            products.append(product)
        doc_body = {
            "producer_inn": producer_inn,
            "owner_inn": owner_inn,
            "production_date": production_date,
            "production_order": "CONTRACT_PRODUCTION",
            "products_list": products,
        }

    elif introduction_type == "import_fts":
        declaration_number = form_data.get("declaration_number") or ""
        declaration_date = form_data.get("declaration_date") or ""
        production_date = form_data.get("production_date") or ""
        products = []
        for u in units:
            cis = _extract_cis_code(u.cz_code or "")
            if not cis:
                continue
            product = {"ki": cis}
            if production_date:
                product["production_date"] = production_date
            products.append(product)
        doc_body = {
            "trade_participant_inn": inn,
            "declaration_number": declaration_number,
            "declaration_date": declaration_date,
            "products_list": products,
        }
        if production_date:
            doc_body["production_date"] = production_date

    else:
        raise Exception(f"Unhandled introduction type: {introduction_type}")

    doc_json = json.dumps(doc_body, ensure_ascii=False, separators=(",", ":"))
    product_document_b64 = base64.b64encode(doc_json.encode("utf-8")).decode("ascii")
    signature = _sign_data(doc_json, thumbprint)

    payload = {
        "document_format": "MANUAL",
        "product_document": product_document_b64,
        "type": info["doc_type"],
        "signature": signature,
    }

    logging.debug(f"[introduce] type={info['doc_type']} pg={pg}")
    logging.debug(f"[introduce] doc_body={doc_json[:500]}")

    token = get_uuid_token(thumbprint)
    base_url = _get_base_url()
    url = f"{base_url}/lk/documents/create"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
        "accept": "application/json",
    }

    r = requests.post(url, params={"pg": pg}, json=payload, headers=headers, timeout=60)

    logging.debug(f"[introduce] response status={r.status_code} body={r.text[:500]}")

    if r.status_code == 401:
        reset_token()
        token = get_uuid_token(thumbprint)
        headers["Authorization"] = f"Bearer {token}"
        r = requests.post(url, params={"pg": pg}, json=payload, headers=headers, timeout=60)

    logging.debug(f"[introduce] final status={r.status_code} body={r.text[:800]}")

    if r.status_code >= 400:
        return {"success": False, "error_code": r.status_code, "error_message": f"HTTP {r.status_code}: {r.text[:800]}"}

    try:
        result = r.json()
        # Проверяем ошибки внутри JSON (API может вернуть 200 с ошибкой)
        if isinstance(result, dict):
            err_code = result.get("error_code") or result.get("code")
            err_msg = result.get("error_message") or result.get("message") or result.get("description")
            if err_code and str(err_code) != "0":
                logging.warning(f"[introduce] API error in response: {err_code} {err_msg}")
                return {"success": False, "error_code": err_code, "error_message": f"{err_msg} (код {err_code})"}

        # Проверяем статус созданного документа
        doc_id = None
        if isinstance(result, dict):
            doc_id = result.get("value") or result.get("document_id") or result.get("id")
        elif isinstance(result, str):
            doc_id = result.strip()

        if doc_id:
            import time
            time.sleep(2)
            try:
                status_result = check_document_status_by_id(str(doc_id), thumbprint=thumbprint, pg=pg)
                if status_result.get("success"):
                    doc_data = status_result.get("data", {})
                    doc_status = doc_data.get("status") or doc_data.get("documentStatus") or ""
                    if doc_status in ("FAILED", "REJECTED", "ERROR"):
                        error_detail = doc_data.get("error") or doc_data.get("errorText") or doc_data.get("errorMessage") or str(doc_data)
                        logging.warning(f"[introduce] Document {doc_id} rejected: {error_detail}")
                        return {"success": False, "error_code": -1, "error_message": f"Документ отклонён ЧЗ: {error_detail}"}
                    logging.info(f"[introduce] Document {doc_id} status: {doc_status}")
            except Exception as e:
                logging.warning(f"[introduce] Could not check doc status: {e}")

        return {"success": True, "data": result, "doc_type": info["doc_type"]}
    except Exception:
        if r.status_code in (200, 201):
            return {"success": True, "data": {"document_id": r.text.strip()}, "doc_type": info["doc_type"]}
        return {"success": False, "error_code": r.status_code, "error_message": f"HTTP {r.status_code}: {r.text[:500]}"}


def _build_certificate_data(form_data: dict, unit) -> list:
    """Построить certificate_document_data из данных формы и карточки товара."""
    # 1. Приоритет — данные из формы (ручной ввод)
    cert_type = form_data.get("certificate_type") or ""
    cert_number = form_data.get("certificate_number") or ""
    cert_date = form_data.get("certificate_date") or ""
    if cert_type and cert_number and cert_date:
        return [{"certificate_type": cert_type, "certificate_number": cert_number, "certificate_date": cert_date}]
    # 2. Структурированные поля из карточки SKU
    if unit.sku:
        sku_type = (unit.sku.cert_type or "").strip()
        sku_number = (unit.sku.cert_number or "").strip()
        sku_date = (unit.sku.cert_date or "").strip()
        if sku_type and sku_number:
            return [{"certificate_type": sku_type, "certificate_number": sku_number, "certificate_date": sku_date or cert_date or datetime.utcnow().strftime("%Y-%m-%d")}]
    return []
