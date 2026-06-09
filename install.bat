@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [Token Status] Creating virtualenv...
python -m venv .venv
if errorlevel 1 (
    echo [ERROR] Python 3.10+ required. Make sure python is on PATH.
    pause
    exit /b 1
)
echo [Token Status] Installing dependencies...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
echo.
echo [Token Status] Install complete. Run run.bat to start.
pause
