import os
import shutil
import threading
import logging
import tempfile
from datetime import datetime
from pathlib import Path
from flask import Blueprint, request, jsonify
from app.utils import load_settings, save_settings
from app.config import MAX_BACKUPS, CZ_API_URL, REMOTE_HOST

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(levelname)s %(message)s"))
    logger.addHandler(_h)

settings_bp = Blueprint("settings", __name__)

BASE_DIR = Path(__file__).parent.parent.parent
DB_PATH = BASE_DIR / "instance" / "inventory.db"
BACKUPS_DIR = BASE_DIR / "backups"

sync_status = {"running": False, "last_result": None, "last_error": None, "direction": None, "restart_required": False}


def _parse_backup_date(filename):
    """Парсит дату из имени файла backup_YYYYMMDD_HHMMSS.db"""
    try:
        ts = filename.replace("backup_", "").replace(".db", "")
        return datetime.strptime(ts, "%Y%m%d_%H%M%S")
    except (ValueError, AttributeError):
        return datetime.min


def _backup_sort_key(p):
    """Ключ сортировки бэкапов по имени файла (lexicographic = chronological)"""
    return p.name


@settings_bp.route("/api/settings/cz", methods=["GET"])
def get_cz_settings():
    s = load_settings()
    return jsonify({
        "cz_api_url": s.get("cz_api_url", CZ_API_URL),
        "cz_api_token": s.get("cz_api_token", ""),
        "cz_inn": s.get("cz_inn", ""),
        "has_token": bool(s.get("cz_api_token")),
        "cz_cert_thumbprint": s.get("cz_cert_thumbprint", ""),
        "default_disposal_address": s.get("default_disposal_address", ""),
        "default_disposal_fias_id": s.get("default_disposal_fias_id", ""),
    })


@settings_bp.route("/api/settings/cz", methods=["POST"])
def set_cz_settings():
    data = request.json or {}
    s = load_settings()
    if "cz_api_url" in data:
        s["cz_api_url"] = data["cz_api_url"]
    if "cz_api_token" in data:
        s["cz_api_token"] = data["cz_api_token"]
    if "cz_inn" in data:
        s["cz_inn"] = data["cz_inn"]
    if "cz_cert_thumbprint" in data:
        s["cz_cert_thumbprint"] = data["cz_cert_thumbprint"]
    if "cz_key_pin" in data:
        s["cz_key_pin"] = data["cz_key_pin"]
    if "default_disposal_address" in data:
        s["default_disposal_address"] = data["default_disposal_address"]
    if "default_disposal_fias_id" in data:
        s["default_disposal_fias_id"] = data["default_disposal_fias_id"]
    save_settings(s)
    return jsonify({"ok": True, "has_token": bool(s.get("cz_api_token"))})


@settings_bp.route("/api/backups", methods=["GET"])
def list_backups():
    BACKUPS_DIR.mkdir(exist_ok=True)
    backups = []
    for f in sorted(BACKUPS_DIR.glob("backup_*.db"), key=_backup_sort_key, reverse=True):
        stat = f.stat()
        dt = _parse_backup_date(f.name)
        backups.append({
            "filename": f.name,
            "size": stat.st_size,
            "created": dt.strftime("%Y-%m-%d %H:%M:%S"),
            "created_ts": dt.timestamp(),
        })
    s = load_settings()
    return jsonify({"backups": backups, "max": MAX_BACKUPS, "remaining": max(0, MAX_BACKUPS - len(backups)), "rotation": s.get("backup_rotation", False)})


@settings_bp.route("/api/backups/create", methods=["POST"])
def create_backup():
    BACKUPS_DIR.mkdir(exist_ok=True)
    if not DB_PATH.exists():
        return jsonify({"error": "База данных не найдена"}), 404
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUPS_DIR / f"backup_{ts}.db"
    shutil.copy2(str(DB_PATH), str(backup_path))
    existing = sorted(BACKUPS_DIR.glob("backup_*.db"), key=_backup_sort_key)
    while len(existing) > MAX_BACKUPS:
        os.remove(str(existing.pop(0)))
    remaining = max(0, MAX_BACKUPS - len(list(BACKUPS_DIR.glob("backup_*.db"))))
    return jsonify({"ok": True, "filename": backup_path.name, "remaining": remaining})


@settings_bp.route("/api/backups/delete", methods=["POST"])
def delete_backup():
    data = request.json or {}
    filename = data.get("filename", "")
    if not filename or "/" in filename or "\\" in filename or ".." in filename:
        return jsonify({"error": "Некорректное имя файла"}), 400
    backup_path = BACKUPS_DIR / filename
    if not backup_path.exists():
        return jsonify({"error": "Бэкап не найден"}), 404
    os.remove(str(backup_path))
    return jsonify({"ok": True})


@settings_bp.route("/api/backups/rotation", methods=["POST"])
def set_rotation():
    data = request.json or {}
    enabled = bool(data.get("enabled"))
    s = load_settings()
    s["backup_rotation"] = enabled
    save_settings(s)
    return jsonify({"ok": True, "rotation": enabled})


@settings_bp.route("/api/backups/restore", methods=["POST"])
def restore_backup():
    data = request.json or {}
    filename = data.get("filename", "")
    if not filename or "/" in filename or "\\" in filename or ".." in filename:
        return jsonify({"error": "Некорректное имя файла"}), 400
    backup_path = BACKUPS_DIR / filename
    if not backup_path.exists():
        return jsonify({"error": "Бэкап не найден"}), 404
    shutil.copy2(str(backup_path), str(DB_PATH))
    return jsonify({"ok": True, "message": "База данных восстановлена. Перезапустите приложение."})


# ===== SYNC =====

def _get_sync_settings():
    s = load_settings()
    sync = s.get("sync", {})
    return {
        "host": sync.get("host", REMOTE_HOST),
        "user": sync.get("user", "root"),
        "password": sync.get("password", ""),
        "remote_dir": sync.get("remote_dir", ""),
    }


def _create_ssh(host, user, password):
    import paramiko
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(host, username=user, password=password, timeout=10)
    return client


def _sftp_stat(sftp, path):
    try:
        sftp.stat(path)
        return True
    except FileNotFoundError:
        return False


def _sftp_makedirs(sftp, path):
    parts = path.strip("/").split("/")
    current = ""
    for part in parts:
        current += "/" + part
        try:
            sftp.stat(current)
        except FileNotFoundError:
            sftp.mkdir(current)


def _remote_backup(sftp, remote_db, remote_backups_dir):
    _sftp_makedirs(sftp, remote_backups_dir)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"backup_{ts}.db"
    backup_path = f"{remote_backups_dir}/{backup_name}"
    if _sftp_stat(sftp, remote_db):
        with sftp.open(remote_db, "rb") as src:
            with sftp.open(backup_path, "wb") as dst:
                while True:
                    chunk = src.read(65536)
                    if not chunk:
                        break
                    dst.write(chunk)
        backups = []
        for f in sftp.listdir_attr(remote_backups_dir):
            if f.filename.startswith("backup_") and f.filename.endswith(".db"):
                backups.append(f.filename)
        backups.sort()
        while len(backups) > 10:
            old = backups.pop(0)
            sftp.remove(f"{remote_backups_dir}/{old}")
        return backup_name
    return None


def _do_sync(direction):
    global sync_status
    sync_status = {"running": True, "last_result": None, "last_error": None, "direction": direction, "restart_required": False}
    try:
        settings = _get_sync_settings()
        logger.info(f"[sync] direction={direction} host={settings['host']} user={settings['user']} remote_dir={settings['remote_dir']} has_password={bool(settings['password'])}")
        if not settings["password"]:
            raise Exception("Пароль не задан")

        logger.info(f"[sync] Подключение к {settings['host']}...")
        client = _create_ssh(settings["host"], settings["user"], settings["password"])
        logger.info(f"[sync] SSH подключён, открываем SFTP...")
        sftp = client.open_sftp()
        logger.info(f"[sync] SFTP открыт")
        try:
            remote_instance = f"{settings['remote_dir']}/instance"
            remote_backups = f"{settings['remote_dir']}/backups"
            remote_db = f"{remote_instance}/inventory.db"

            if direction == "push":
                if not DB_PATH.exists():
                    raise Exception("Локальная база данных не найдена")
                local_size = DB_PATH.stat().st_size
                logger.info(f"[sync] push: локальная база {DB_PATH} ({local_size} байт)")
                _sftp_makedirs(sftp, remote_instance)
                logger.info(f"[sync] push: создание резервной копии на сервере...")
                _remote_backup(sftp, remote_db, remote_backups)
                remote_bak = f"{remote_db}.bak"
                if _sftp_stat(sftp, remote_db):
                    sftp.rename(remote_db, remote_bak)
                    logger.info(f"[sync] push: предыдущая база сохранена как {remote_bak}")
                logger.info(f"[sync] push: загрузка базы на сервер...")
                sftp.put(str(DB_PATH), remote_db)
                remote_size = sftp.stat(remote_db).st_size
                logger.info(f"[sync] push: база загружена, размер на сервере {remote_size} байт")
                if (BASE_DIR / "instance" / "settings.json").exists():
                    sftp.put(str(BASE_DIR / "instance" / "settings.json"), f"{remote_instance}/settings.json")
                    logger.info(f"[sync] push: settings.json синхронизирован")
                sync_status["last_result"] = f"Загружено на {settings['host']}"
                sync_status["restart_required"] = False

            elif direction == "pull":
                if not _sftp_stat(sftp, remote_db):
                    raise Exception(f"База не найдена на сервере: {remote_db}")
                remote_size = sftp.stat(remote_db).st_size
                logger.info(f"[sync] pull: серверная база {remote_db} ({remote_size} байт)")
                if DB_PATH.exists():
                    BACKUPS_DIR.mkdir(exist_ok=True)
                    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                    shutil.copy2(str(DB_PATH), str(BACKUPS_DIR / f"backup_{ts}.db"))
                    logger.info(f"[sync] pull: локальный бэкап создан")
                    bak_path = DB_PATH.with_suffix(".db.bak")
                    shutil.copy2(str(DB_PATH), str(bak_path))
                    logger.info(f"[sync] pull: предыдущая база сохранена как {bak_path.name}")
                DB_PATH.parent.mkdir(exist_ok=True)
                logger.info(f"[sync] pull: скачивание базы с сервера...")
                sftp.get(remote_db, str(DB_PATH))
                local_size = DB_PATH.stat().st_size
                logger.info(f"[sync] pull: база скачана, локальный размер {local_size} байт")
                remote_settings = f"{remote_instance}/settings.json"
                if _sftp_stat(sftp, remote_settings):
                    sftp.get(remote_settings, str(BASE_DIR / "instance" / "settings.json"))
                    logger.info(f"[sync] pull: settings.json синхронизирован")
                sync_status["last_result"] = f"Скачано с {settings['host']}"
                sync_status["restart_required"] = True
        finally:
            sftp.close()
            client.close()
            logger.info(f"[sync] Соединение закрыто")

    except Exception as e:
        logger.error(f"[sync] Ошибка: {e}")
        sync_status["last_error"] = str(e)
    finally:
        sync_status["running"] = False


@settings_bp.route("/api/sync/settings", methods=["GET"])
def get_sync_settings_api():
    s = load_settings().get("sync", {})
    return jsonify({
        "host": s.get("host", REMOTE_HOST),
        "user": s.get("user", "root"),
        "remote_dir": s.get("remote_dir", ""),
        "password": s.get("password", ""),
        "has_password": bool(s.get("password")),
    })


@settings_bp.route("/api/sync/settings", methods=["POST"])
def set_sync_settings_api():
    data = request.json or {}
    s = load_settings()
    if "sync" not in s:
        s["sync"] = {}
    if "host" in data:
        s["sync"]["host"] = data["host"]
    if "user" in data:
        s["sync"]["user"] = data["user"]
    if "remote_dir" in data:
        s["sync"]["remote_dir"] = data["remote_dir"]
    if "password" in data:
        s["sync"]["password"] = data["password"]
    save_settings(s)
    return jsonify({"ok": True, "has_password": bool(s["sync"].get("password"))})


@settings_bp.route("/api/sync/push", methods=["POST"])
def sync_push():
    if sync_status["running"]:
        return jsonify({"error": "Синхронизация уже выполняется"}), 409
    thread = threading.Thread(target=_do_sync, args=("push",), daemon=True)
    thread.start()
    return jsonify({"ok": True, "direction": "push"})


@settings_bp.route("/api/sync/pull", methods=["POST"])
def sync_pull():
    if sync_status["running"]:
        return jsonify({"error": "Синхронизация уже выполняется"}), 409
    thread = threading.Thread(target=_do_sync, args=("pull",), daemon=True)
    thread.start()
    return jsonify({"ok": True, "direction": "pull"})


@settings_bp.route("/api/sync/status", methods=["GET"])
def sync_status_api():
    return jsonify(sync_status)


# ===== CZ STATUS CHECK =====

@settings_bp.route("/api/cz/list-certs", methods=["GET"])
def cz_list_certs():
    try:
        from app.cz_api import list_certificates
        certs = list_certificates()
        return jsonify({"ok": True, "certs": certs})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@settings_bp.route("/api/cz/test-auth", methods=["POST"])
def cz_test_auth():
    data = request.json or {}
    thumbprint = data.get("thumbprint", "")
    if not thumbprint:
        from app.utils import load_settings
        s = load_settings()
        thumbprint = s.get("cz_cert_thumbprint", "")
    if not thumbprint:
        return jsonify({"ok": False, "error": "Отпечаток не задан"})
    try:
        from app.cz_api import get_uuid_token, reset_token
        reset_token()
        token = get_uuid_token(thumbprint)
        return jsonify({"ok": True, "message": "Авторизация прошла успешно. Токен получен."})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@settings_bp.route("/api/cz/diagnose", methods=["POST"])
def cz_diagnose():
    steps = []
    s = load_settings()

    thumbprint = s.get("cz_cert_thumbprint", "")
    if not thumbprint:
        steps.append({"step": "certificate", "ok": False, "detail": "Thumbprint not set"})
        return jsonify({"ok": False, "steps": steps})
    steps.append({"step": "certificate", "ok": True, "detail": f"Thumbprint: {thumbprint[:16]}..."})

    pin = s.get("cz_key_pin", "")
    steps.append({"step": "pin", "ok": bool(pin), "detail": "Set" if pin else "Not set"})

    try:
        from app.cz_api import _sign_data
        sig = _sign_data("test_diagnostic_12345", thumbprint)
        steps.append({"step": "signing", "ok": True, "detail": f"csptest OK ({len(sig)} chars)"})
    except Exception as e:
        steps.append({"step": "signing", "ok": False, "detail": str(e)[:200]})
        return jsonify({"ok": False, "steps": steps})

    import requests as req
    base = s.get("cz_api_url", CZ_API_URL)
    try:
        r = req.get(f"{base}/auth/key", headers={"accept": "application/json"}, timeout=15)
        auth = r.json()
        steps.append({"step": "auth_key", "ok": True, "detail": f"uuid={auth['uuid'][:16]}..., data={auth['data'][:16]}..."})
    except Exception as e:
        steps.append({"step": "auth_key", "ok": False, "detail": str(e)[:200]})
        return jsonify({"ok": False, "steps": steps})

    try:
        sig = _sign_data(auth["data"], thumbprint)
        inn = s.get("cz_inn", "")
        payload = {"uuid": auth["uuid"], "data": sig, "unitedToken": True}
        if inn:
            payload["inn"] = inn
        r2 = req.post(f"{base}/auth/simpleSignIn", json=payload,
                      headers={"Content-Type": "application/json", "accept": "application/json"},
                      timeout=15)
        if r2.status_code == 200:
            token = r2.json().get("token", "")
            steps.append({"step": "simpleSignIn", "ok": True, "detail": f"Token received ({len(token)} chars)"})
        else:
            err = r2.text[:300]
            steps.append({"step": "simpleSignIn", "ok": False, "detail": f"HTTP {r2.status_code}: {err}"})
    except Exception as e:
        steps.append({"step": "simpleSignIn", "ok": False, "detail": str(e)[:200]})

    all_ok = all(s["ok"] for s in steps)
    return jsonify({"ok": all_ok, "steps": steps})


CZ_TO_UNIT_STATUS = {
    'EMITTED': 1, 'APPLIED': 2, 'INTRODUCED': 3, 'INTRODUCED_RETURNED': 3,
    'RETIRED': 5, 'WITHDRAWN': 5, 'WRITTEN_OFF': 5,
}


@settings_bp.route("/api/cz/check-single", methods=["POST"])
def cz_check_single():
    from app.models import Unit
    data = request.json or {}
    unit_id = data.get("unit_id")
    if not unit_id:
        abort(400, "unit_id обязателен")
    unit = Unit.query.get_or_404(unit_id)
    if not unit.cz_code:
        abort(400, "У единицы нет кода ЧЗ")
    try:
        from app.cz_api import check_cz_status
        from datetime import datetime
        result = check_cz_status([unit.cz_code])
        cz_results = result.get("results", [])
        if cz_results:
            entry = cz_results[0]
            info = entry.get("cisInfo", entry)
            error_code = entry.get("errorCode", "")
            error_msg = entry.get("errorMessage", "")
            if error_code and error_code != "0":
                return jsonify({
                    "ok": False,
                    "error": f"{error_msg} (код {error_code})",
                    "raw_keys": list(entry.keys()),
                })
            cz_status_raw = info.get("status") or info.get("cisStatus") or ""
            unit.cz_status = cz_status_raw
            unit.cz_check_date = datetime.utcnow().strftime("%Y-%m-%d %H:%M")
            new_status = CZ_TO_UNIT_STATUS.get(cz_status_raw)
            if new_status is not None and new_status > unit.status:
                unit.status = new_status
            from app import db
            db.session.commit()
            return jsonify({
                "ok": True,
                "cz_status": cz_status_raw,
                "cz_check_date": unit.cz_check_date,
                "unit_status": unit.status,
            })
        return jsonify({"ok": False, "error": "Код не найден в ЧЗ"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


cz_check_status = {"running": False, "last_result": None, "last_error": None, "checked": 0, "total": 0}


def _do_cz_check_all():
    global cz_check_status
    from app import create_app, db
    from app.models import Unit
    from datetime import datetime
    app = create_app()
    with app.app_context():
        cz_check_status = {"running": True, "last_result": None, "last_error": None, "checked": 0, "total": 0}
        try:
            from app.cz_api import check_cz_status
            units = Unit.query.join(SKU).filter(
                Unit.cz_code != None, Unit.cz_code != '',
                SKU.has_marking == True,
            ).all()
            cz_check_status["total"] = len(units)

            if not units:
                cz_check_status["last_result"] = "Нет кодов для проверки"
                cz_check_status["running"] = False
                return

            now = datetime.utcnow().strftime("%Y-%m-%d %H:%M")
            BATCH = 900
            unit_by_code = {}
            for u in units:
                clean = u.cz_code.replace("\xe8", "").replace("\u001d", "").strip() if u.cz_code else ""
                idx91 = clean.find("91")
                if idx91 > 0:
                    clean = clean[:idx91]
                clean = clean.strip()
                if clean and len(clean) >= 18:
                    unit_by_code[clean] = u

            all_codes = list(unit_by_code.keys())
            for i in range(0, len(all_codes), BATCH):
                batch = all_codes[i:i + BATCH]
                try:
                    result = check_cz_status(batch)
                    results = result.get("results", [])
                    for entry in results:
                        error_code = entry.get("errorCode", "")
                        if error_code and error_code != "0":
                            continue
                        info = entry.get("cisInfo", entry)
                        cis = info.get("cis", "") or info.get("requestedCis", "")
                        status = info.get("status") or info.get("cisStatus") or ""
                        if cis and status and cis in unit_by_code:
                            unit_by_code[cis].cz_status = status
                            unit_by_code[cis].cz_check_date = now
                            new_status = CZ_TO_UNIT_STATUS.get(status)
                            if new_status is not None and new_status > unit_by_code[cis].status:
                                unit_by_code[cis].status = new_status
                    cz_check_status["checked"] = min(i + BATCH, len(all_codes))
                except Exception:
                    pass

            db.session.commit()
            cz_check_status["last_result"] = f"Проверено: {len(all_codes)} кодов"
        except Exception as e:
            cz_check_status["last_error"] = str(e)
        finally:
            cz_check_status["running"] = False


@settings_bp.route("/api/cz/check-all", methods=["POST"])
def cz_check_all():
    if cz_check_status["running"]:
        return jsonify({"error": "Проверка уже выполняется"}), 409
    thread = threading.Thread(target=_do_cz_check_all, daemon=True)
    thread.start()
    return jsonify({"ok": True})


@settings_bp.route("/api/cz/check-status", methods=["GET"])
def cz_check_status_api():
    return jsonify(cz_check_status)


@settings_bp.route("/api/cz/debug", methods=["POST"])
def cz_debug():
    from app.models import Unit
    from app.cz_api import get_uuid_token, get_cis_info, reset_token
    data = request.json or {}
    unit_id = data.get("unit_id")
    cz_code_raw = data.get("cz_code")
    debug = {}

    if unit_id:
        unit = Unit.query.get_or_404(unit_id)
        debug["unit_id"] = unit.id
        debug["cz_code_raw"] = repr(unit.cz_code)
        cz_code = unit.cz_code
    elif cz_code_raw:
        cz_code = cz_code_raw
        debug["cz_code_raw"] = repr(cz_code)
    else:
        return jsonify({"error": "unit_id или cz_code обязателен"}), 400

    debug["cz_code_len"] = len(cz_code) if cz_code else 0
    debug["cz_code_hex"] = cz_code.encode("utf-8").hex() if cz_code else ""

    if not cz_code:
        debug["error"] = "Нет кода ЧЗ"
    return jsonify(debug)

    try:
        s = load_settings()
        thumbprint = s.get("cz_cert_thumbprint", "")
        debug["thumbprint"] = thumbprint[:8] + "..." if thumbprint else "(пусто)"
        debug["api_url"] = s.get("cz_api_url", "")

        token = get_uuid_token(thumbprint)
        debug["token_prefix"] = token[:16] + "..." if token else "(пусто)"

        clean = cz_code.replace("\xe8", "").replace("\u001d", "")
        idx91 = clean.find("91")
        if idx91 > 0:
            clean = clean[:idx91]
        clean = clean.strip()
        debug["clean_code"] = clean
        debug["clean_len"] = len(clean)

        results = get_cis_info(token, [clean])
        debug["raw_results"] = results

        if results:
            entry = results[0]
            debug["all_keys"] = list(entry.keys())
            debug["errorCode"] = entry.get("errorCode", "")
            debug["errorMessage"] = entry.get("errorMessage", "")
            info = entry.get("cisInfo", entry)
            debug["cisInfo_keys"] = list(info.keys()) if isinstance(info, dict) else str(type(info))
            debug["cis"] = info.get("cis", "")
            debug["cisStatus"] = info.get("cisStatus", "")
            debug["status"] = info.get("status", "")
            debug["productGroup"] = info.get("productGroup", "")
        else:
            debug["error"] = "Пустой ответ от API"

    except Exception as e:
        debug["error"] = str(e)
        try:
            reset_token()
        except Exception:
            pass

    return jsonify(debug)
