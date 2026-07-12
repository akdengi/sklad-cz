@echo off
chcp 65001 >nul 2>&1
setlocal

set "VENV_DIR=%~dp0venv"
set "PYTHON=%VENV_DIR%\Scripts\python.exe"
set "PIP=%VENV_DIR%\Scripts\pip.exe"

echo ============================================
echo   Creating virtual environment...
echo ============================================

if exist "%PYTHON%" (
    echo   venv already exists: %VENV_DIR%
) else (
    python -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo   [!] Failed to create venv. Make sure Python is installed and in PATH.
        pause
        exit /b 1
    )
    echo   venv created: %VENV_DIR%
)

echo.
echo ============================================
echo   Installing dependencies...
echo ============================================

"%PIP%" install --upgrade pip >nul 2>&1
"%PIP%" install -r "%~dp0requirements.txt"

if errorlevel 1 (
    echo.
    echo   [!] Failed to install dependencies.
    pause
    exit /b 1
)

echo.
echo ============================================
echo   Done! Run start.bat to launch.
echo ============================================

pause
