@echo off
chcp 65001 >nul
echo ============================================
echo   YaoWo Uploader v2.0 - Debug Start
echo ============================================
echo.

set "ROOT=%~dp0"
set "PYTHON=%ROOT%python-portable\python\python.exe"

if exist "%PYTHON%" (
    echo [OK] Found: python-portable
) else (
    echo [ERROR] python-portable NOT FOUND
    echo   Expected: %PYTHON%
    pause
    exit /b 1
)

echo Python: %PYTHON%
echo.

:: Test Python
echo Testing Python...
"%PYTHON%" --version
if errorlevel 1 (
    echo [ERROR] Python failed to run
    pause
    exit /b 1
)

:: Launch GUI
echo.
echo Launching GUI...
cd /d "%ROOT%"
start "YaoWo Uploader" "%PYTHON%" "%ROOT%main.py"
echo Done
pause
