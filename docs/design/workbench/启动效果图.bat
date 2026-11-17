@echo off
rem ===========================================================================
rem  One-click launcher for the design workbench (docs/design/workbench).
rem
rem  WHY A .BAT EXISTS AT ALL:
rem    "Interactive preview" loads the real App (ES modules + /health, /items,
rem    /outfits ...). A browser refuses to load any of that from file://, so
rem    double-clicking index.html only ever shows screenshots. This script
rem    starts serve.py and opens the http address where the buttons work.
rem
rem  WHY THIS FILE IS PURE ASCII:
rem    cmd.exe reads .bat files using the system codepage. Chinese text stored
rem    here would render as mojibake on a GBK console, so all messages stay
rem    English - serve.py prints its own Chinese banner.
rem
rem  WHY THE PATH IS RELATIVE:
rem    "%~dp0" is the folder holding this file, so the project can be moved or
rem    renamed without editing this script. The project folder name contains
rem    Chinese, so hard-coding it here would hit the same codepage trap.
rem ===========================================================================

cd /d "%~dp0"

set "PY=%~dp0..\..\..\.venv\Scripts\python.exe"

if not exist "%PY%" (
  echo.
  echo   [x] Not found: .venv\Scripts\python.exe
  echo.
  echo   Create the environment once in the project root, then run this again:
  echo       python -m venv .venv
  echo       .venv\Scripts\python.exe -m pip --isolated install -r requirements.txt
  echo.
  pause
  exit /b 1
)

echo.
echo   Design workbench - http://127.0.0.1:8798/
echo   Keep this window open while you review. Close it to stop the workbench.
echo.

"%PY%" serve.py --open

echo.
echo   Workbench stopped. Press any key to close this window.
pause
exit /b 0
