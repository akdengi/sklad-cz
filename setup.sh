#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/venv"
PYTHON="$VENV_DIR/bin/python"
PIP="$VENV_DIR/bin/pip"

echo "============================================"
echo "  Создание виртуального окружения..."
echo "============================================"

if [ -f "$PYTHON" ]; then
    echo "  venv уже существует: $VENV_DIR"
else
    python3 -m venv "$VENV_DIR"
    echo "  venv создана: $VENV_DIR"
fi

echo ""
echo "============================================"
echo "  Установка зависимостей..."
echo "============================================"

"$PIP" install --upgrade pip > /dev/null 2>&1
"$PIP" install -r "$SCRIPT_DIR/requirements.txt"

echo ""
echo "============================================"
echo "  Готово! Запустите ./run.sh для запуска."
echo "============================================"
