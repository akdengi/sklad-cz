import sys
import os
import requests
from app.utils import load_settings
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
        b64 = base64.b64encode(data_to_sign.encode("ascii")).decode("ascii")
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
        idx91 = clean.find("91")
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
