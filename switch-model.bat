@echo off
rem Double-click entry for model-channel switching.
rem Kept pure ASCII on purpose: cmd.exe reads .bat with the system codepage,
rem so Chinese here would turn into mojibake. The menu itself is printed by
rem Python (UTF-8), which the console renders correctly after chcp 65001.
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
".venv\Scripts\python.exe" scripts\llm_mode.py
echo.
pause
