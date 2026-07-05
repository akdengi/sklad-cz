import sys
import os
import glob as glob_mod
from app import create_app, init_db
from app.config import APP_HOST, APP_PORT


def find_ghostscript():
    if os.name != 'nt':
        import shutil
        if shutil.which('gs'):
            return
        print("  [!] Ghostscript не найден. Установите: sudo apt install ghostscript")
        return
    for pattern in [
        r"C:\Program Files\gs\gs*\bin\gswin64c.exe",
        r"C:\Program Files\gs\gs*\bin\gswin32c.exe",
        r"C:\Program Files (x86)\gs\gs*\bin\gswin32c.exe",
    ]:
        matches = glob_mod.glob(pattern)
        if matches:
            gs_bin = os.path.dirname(matches[-1])
            if gs_bin not in os.environ.get('PATH', ''):
                os.environ['PATH'] = gs_bin + ';' + os.environ.get('PATH', '')
            return
    print("  [!] Ghostscript не найден. Скачайте: https://github.com/ArtifexSoftware/ghostpdl-downloads/releases")


def check_dependencies():
    missing = []
    try:
        import reportlab
    except ImportError:
        missing.append("reportlab")
    try:
        import pdfplumber
    except ImportError:
        missing.append("pdfplumber")
    try:
        import treepoem
    except ImportError:
        missing.append("treepoem")
    if missing:
        print("=" * 60)
        print(f"  ОТСУТСТВУЮТ ЗАВИСИМОСТИ: {', '.join(missing)}")
        print(f"  Python: {sys.executable}")
        print(f"  Запустите: {sys.executable} -m pip install {' '.join(missing)}")
        print("=" * 60)
        sys.exit(1)

    print("  Все зависимости установлены")


if __name__ == "__main__":
    find_ghostscript()
    check_dependencies()
    app = create_app()
    init_db(app)
    print("=" * 60)
    print("  Товароучёт + Честный Знак v10")
    print(f"  Python: {sys.executable}")
    print(f"  Откройте: http://{APP_HOST}:{APP_PORT}")
    print("=" * 60)
    app.run(host=APP_HOST, port=APP_PORT, debug=False)
