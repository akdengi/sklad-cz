"""Скрипт повышения версии приложения.

Использование:
    python bump_version.py patch    # 0.8.0 -> 0.8.1  (исправления)
    python bump_version.py minor    # 0.8.1 -> 0.9.0  (новые функции)
    python bump_version.py major    # 0.9.0 -> 1.0.0  (значительные изменения)
"""

import sys
from pathlib import Path

VERSION_FILE = Path(__file__).parent / "VERSION"


def bump(part="patch"):
    current = VERSION_FILE.read_text().strip()
    major, minor, patch = [int(x) for x in current.split(".")]

    if part == "major":
        major += 1
        minor = 0
        patch = 0
    elif part == "minor":
        minor += 1
        patch = 0
    else:
        patch += 1

    new_ver = f"{major}.{minor}.{patch}"
    VERSION_FILE.write_text(new_ver)
    print(f"{current} -> {new_ver}")
    return new_ver


if __name__ == "__main__":
    part = sys.argv[1] if len(sys.argv) > 1 else "patch"
    if part not in ("major", "minor", "patch"):
        print("Использование: python bump_version.py [major|minor|patch]")
        sys.exit(1)
    bump(part)
