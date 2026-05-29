@echo off
chcp 65001 >nul
set "ROOT=%~dp0"
set "PYTHON=%ROOT%python-portable\python\python.exe"
set "PATH=%ROOT%python-portable\python;%ROOT%python-portable\python\DLLs;%PATH%"

if not exist "%PYTHON%" (
    echo [ERROR] Python not found: %PYTHON%
    pause
    exit /b 1
)

cd /d "%ROOT%"
start "" "%PYTHON%" "%ROOT%main.py"
