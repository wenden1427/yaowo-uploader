@echo off

echo ============================================
echo   YaoWo Uploader v2.0 - Debug Start
echo ============================================
echo.
echo Current dir: %cd%
echo Script dir: %~dp0
echo.

:: Find Python
set "PYTHON="

if exist "E:\ai-test\python-portable\python\python.exe" (
    set "PYTHON=E:\ai-test\python-portable\python\python.exe"
    echo [1] Found: E:\ai-test\python-portable
    goto :found
)

if exist "%~dp0..\python-portable\python\python.exe" (
    set "PYTHON=%~dp0..\python-portable\python\python.exe"
    echo [2] Found: parent\python-portable
    goto :found
)

if exist "%~dp0..\..\python-portable\python\python.exe" (
    set "PYTHON=%~dp0..\..\python-portable\python\python.exe"
    echo [3] Found: grandparent\python-portable
    goto :found
)

echo [ERROR] python-portable NOT FOUND
echo   Checked:
echo     E:\ai-test\python-portable\
echo     %~dp0..\python-portable\
echo     %~dp0..\..\python-portable\
pause
exit /b 1

:found
echo.
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

:: Test import
echo.
echo Testing import...
set "SDIR=%~dp0"
set "SDIR=%SDIR:~0,-1%"
"%PYTHON%" -c "import sys; sys.path.insert(0, r'%SDIR%'); from main import main; print('Import OK')"
if errorlevel 1 (
    echo [ERROR] Import failed
    pause
    exit /b 1
)

:: Launch GUI
echo.
echo Launching GUI...
echo Window should appear now. If not, check taskbar and alt-tab.
cd /d "%~dp0"
start "YaoWo Uploader" "%PYTHON%" "%~dp0main.py"
echo.
echo If window did not appear, try: start "%PYTHON%" "%~dp0main.py"
echo.
pause
