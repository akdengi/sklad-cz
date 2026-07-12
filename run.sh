#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/venv"
PYTHON="$VENV_DIR/bin/python"

if [ ! -f "$PYTHON" ]; then
    echo "  [!] Виртуальное окружение не найдено."
    echo "  Запустите ./setup.sh для создания venv и установки зависимостей."
    exit 1
fi

VER=$(cat "$SCRIPT_DIR/VERSION")
echo "============================================"
echo "  Товароучет + Честный Знак v$VER"
echo "============================================"
echo ""

"$PYTHON" "$SCRIPT_DIR/run.py"
