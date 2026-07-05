@echo off
chcp 65001 >nul 2>&1
set /p VER=<VERSION
echo ============================================
echo   Товароучет + Честный Знак v%VER%
echo ============================================
echo.

python run.py

pause
