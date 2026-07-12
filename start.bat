@echo off
chcp 65001 >nul 2>&1
setlocal

set "VENV_DIR=%~dp0venv"
set "PYTHON=%VENV_DIR%\Scripts\python.exe"

if not exist "%PYTHON%" (
    echo   [!] Virtual environment not found.
    echo   Run setup.bat first to create venv and install dependencies.
    pause
    exit /b 1
)

set /p VER=<"%~dp0VERSION"
echo ============================================
echo   Sklad v%VER%
echo ============================================
echo.

"%PYTHON%" "%~dp0run.py"

pause
