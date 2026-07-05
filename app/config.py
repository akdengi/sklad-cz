import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent.parent
load_dotenv(BASE_DIR / ".env")

def _load_version():
    vfile = BASE_DIR / "VERSION"
    if vfile.exists():
        return vfile.read_text().strip()
    return "0.0.0"

APP_VERSION = _load_version()

SECRET_KEY = os.getenv("SECRET_KEY", "inventory-secret-change-me")
APP_HOST = os.getenv("APP_HOST", "127.0.0.1")
APP_PORT = int(os.getenv("APP_PORT", "5000"))

CZ_API_URL = os.getenv("CZ_API_URL", "https://markirovka.crpt.ru/api/v3/true-api")

REMOTE_HOST = os.getenv("REMOTE_HOST", "")
REMOTE_USER = os.getenv("REMOTE_USER", "root")
REMOTE_PASSWORD = os.getenv("REMOTE_PASSWORD", "")
REMOTE_SKLAD_DIR = os.getenv("REMOTE_SKLAD_DIR", "")

MAX_BACKUPS = int(os.getenv("MAX_BACKUPS", "8"))
