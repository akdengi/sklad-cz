#!/usr/bin/env python3
"""
Синхронизация базы данных Sklad с удалённым сервером.

Использование:
    python sync.py push   — загрузить базу на сервер (с бэкапом на сервере)
    python sync.py pull   — скачать базу с сервера (с бэкапом локально)
    python sync.py status — показать статус синхронизации

Настройки сервера задаются в .env файле или через settings.json.
"""

import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

try:
    import paramiko
except ImportError:
    print("Установите paramiko: pip install paramiko")
    sys.exit(1)

from app.config import REMOTE_HOST, REMOTE_USER, REMOTE_PASSWORD, REMOTE_SKLAD_DIR

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "instance" / "inventory.db"
BACKUPS_DIR = BASE_DIR / "backups"
SETTINGS_PATH = BASE_DIR / "instance" / "settings.json"

REMOTE_DB_PATH = f"{REMOTE_SKLAD_DIR}/instance/inventory.db"
REMOTE_BACKUPS_DIR = f"{REMOTE_SKLAD_DIR}/backups"


def parse_backup_date(filename):
    """Парсит дату из имени файла backup_YYYYMMDD_HHMMSS.db"""
    try:
        ts = filename.replace("backup_", "").replace(".db", "")
        return datetime.strptime(ts, "%Y%m%d_%H%M%S")
    except (ValueError, AttributeError):
        return datetime.min


def load_settings():
    if SETTINGS_PATH.exists():
        return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    return {}


def save_settings(data):
    SETTINGS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_sync_settings():
    s = load_settings()
    sync = s.get("sync", {})
    return {
        "host": sync.get("host", REMOTE_HOST),
        "user": sync.get("user", REMOTE_USER),
        "password": sync.get("password", REMOTE_PASSWORD),
        "remote_dir": sync.get("remote_dir", REMOTE_SKLAD_DIR),
    }


def create_ssh_client(host, user, password):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(host, username=user, password=password, timeout=10)
    return client


def remote_exists(sftp, path):
    try:
        sftp.stat(path)
        return True
    except FileNotFoundError:
        return False


def remote_makedirs(sftp, path):
    parts = path.strip("/").split("/")
    current = ""
    for part in parts:
        current += "/" + part
        try:
            sftp.stat(current)
        except FileNotFoundError:
            sftp.mkdir(current)


def create_remote_backup(sftp, remote_db_path, remote_backups_dir):
    remote_makedirs(sftp, remote_backups_dir)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"backup_{ts}.db"
    backup_path = f"{remote_backups_dir}/{backup_name}"
    if remote_exists(sftp, remote_db_path):
        with sftp.open(remote_db_path, "rb") as src:
            with sftp.open(backup_path, "wb") as dst:
                while True:
                    chunk = src.read(65536)
                    if not chunk:
                        break
                    dst.write(chunk)
        # Удалить старые бэкапы (оставить 10)
        backups = []
        for f in sftp.listdir_attr(remote_backups_dir):
            if f.filename.startswith("backup_") and f.filename.endswith(".db"):
                backups.append(f.filename)
        backups.sort()
        while len(backups) > 10:
            old = backups.pop(0)
            sftp.remove(f"{remote_backups_dir}/{old}")
        print(f"  Бэкап на сервере: {backup_name}")
        return backup_name
    return None


def push():
    settings = get_sync_settings()
    print(f"=== Загрузка базы на сервер {settings['host']} ===")

    if not DB_PATH.exists():
        print("Ошибка: локальная база данных не найдена!")
        sys.exit(1)

    # Локальный бэкап перед заливкой
    BACKUPS_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    local_backup = BACKUPS_DIR / f"backup_{ts}.db"
    shutil.copy2(str(DB_PATH), str(local_backup))
    print(f"Локальный бэкап: {local_backup.name}")

    # Подключение к серверу
    try:
        client = create_ssh_client(settings["host"], settings["user"], settings["password"])
    except Exception as e:
        print(f"Ошибка подключения: {e}")
        sys.exit(1)

    sftp = client.open_sftp()
    try:
        # Бэкап на сервере
        remote_instance = f"{settings['remote_dir']}/instance"
        remote_backups = f"{settings['remote_dir']}/backups"
        remote_makedirs(sftp, remote_instance)
        create_remote_backup(sftp, f"{remote_instance}/inventory.db", remote_backups)
        remote_db = f"{remote_instance}/inventory.db"
        remote_bak = f"{remote_db}.bak"
        if remote_exists(sftp, remote_db):
            sftp.rename(remote_db, remote_bak)
            print(f"  Предыдущая база на сервере сохранена как {remote_bak}")

        # Загрузка базы
        sftp.put(str(DB_PATH), f"{remote_instance}/inventory.db")
        print(f"База загружена на {settings['host']}:{remote_instance}/inventory.db")

        # Синхронизация settings.json
        if SETTINGS_PATH.exists():
            remote_settings = f"{remote_instance}/settings.json"
            sftp.put(str(SETTINGS_PATH), remote_settings)
            print("settings.json синхронизирован")

    finally:
        sftp.close()
        client.close()

    print("=== Готово ===")


def pull():
    settings = get_sync_settings()
    print(f"=== Скачивание базы с сервера {settings['host']} ===")

    # Локальный бэкап перед скачиванием
    if DB_PATH.exists():
        BACKUPS_DIR.mkdir(exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        local_backup = BACKUPS_DIR / f"backup_{ts}.db"
        shutil.copy2(str(DB_PATH), str(local_backup))
        print(f"Локальный бэкап: {local_backup.name}")
        bak_path = DB_PATH.with_suffix(".db.bak")
        shutil.copy2(str(DB_PATH), str(bak_path))
        print(f"  Предыдущая база сохранена как {bak_path.name}")

    # Подключение к серверу
    try:
        client = create_ssh_client(settings["host"], settings["user"], settings["password"])
    except Exception as e:
        print(f"Ошибка подключения: {e}")
        sys.exit(1)

    sftp = client.open_sftp()
    try:
        remote_instance = f"{settings['remote_dir']}/instance"
        remote_db = f"{remote_instance}/inventory.db"

        if not remote_exists(sftp, remote_db):
            print(f"Ошибка: база не найдена на сервере: {remote_db}")
            sys.exit(1)

        # Скачивание базы
        DB_PATH.parent.mkdir(exist_ok=True)
        sftp.get(remote_db, str(DB_PATH))
        print(f"База скачана с {settings['host']}:{remote_db}")

        # Синхронизация settings.json
        remote_settings = f"{remote_instance}/settings.json"
        if remote_exists(sftp, remote_settings):
            sftp.get(remote_settings, str(SETTINGS_PATH))
            print("settings.json синхронизирован")

    finally:
        sftp.close()
        client.close()

    print("=== Готово ===")


def status():
    settings = get_sync_settings()
    print(f"=== Статус синхронизации ===")
    print(f"Сервер: {settings['host']}")
    print(f"Локальная база: {DB_PATH} ({'есть' if DB_PATH.exists() else 'нет'})")

    if DB_PATH.exists():
        stat = DB_PATH.stat()
        size_kb = stat.st_size / 1024
        mtime = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        print(f"  Размер: {size_kb:.1f} КБ")
        print(f"  Изменена: {mtime}")

    # Проверка сервера
    try:
        client = create_ssh_client(settings["host"], settings["user"], settings["password"])
        sftp = client.open_sftp()
        remote_db = f"{settings['remote_dir']}/instance/inventory.db"
        if remote_exists(sftp, remote_db):
            stat = sftp.stat(remote_db)
            size_kb = stat.st_size / 1024
            mtime = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            print(f"Серверная база: {remote_db} (есть)")
            print(f"  Размер: {size_kb:.1f} КБ")
            print(f"  Изменена: {mtime}")
        else:
            print(f"Серверная база: {remote_db} (не найдена)")

        remote_backups = f"{settings['remote_dir']}/backups"
        if remote_exists(sftp, remote_backups):
            backups = [f.filename for f in sftp.listdir_attr(remote_backups)
                       if f.filename.startswith("backup_") and f.filename.endswith(".db")]
            print(f"Бэкапов на сервере: {len(backups)}")

        sftp.close()
        client.close()
    except Exception as e:
        print(f"Ошибка подключения к серверу: {e}")

    # Локальные бэкапы
    if BACKUPS_DIR.exists():
        backups = list(BACKUPS_DIR.glob("backup_*.db"))
        print(f"Локальных бэкапов: {len(backups)}")

    print("=== ===")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "push":
        push()
    elif cmd == "pull":
        pull()
    elif cmd == "status":
        status()
    else:
        print(__doc__)
