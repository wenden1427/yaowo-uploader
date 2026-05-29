@echo off
chcp 65001 >nul
set "ROOT=%~dp0"
set "PYTHON=%ROOT%python-portable\python\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"
"%PYTHON%" "%ROOT%更新.py"
