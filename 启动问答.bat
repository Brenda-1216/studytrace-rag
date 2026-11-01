@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Python environment not found. Please follow README.md to create .venv first.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -u run.py
if errorlevel 1 pause
endlocal
