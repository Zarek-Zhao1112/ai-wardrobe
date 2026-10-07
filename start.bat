@echo off
rem ===========================================================================
rem  One-click launcher for the AI Wardrobe project.
rem
rem  WHY THIS FILE IS PURE ASCII:
rem    cmd.exe reads .bat files using the system codepage. Chinese text stored
rem    here would render as mojibake on a GBK console. So every Chinese message
rem    is printed by run.py (Python writes to the Windows console as UTF-16,
rem    which always displays correctly).
rem
rem  WHY THERE IS NO HARD-CODED PATH:
rem    "%~dp0" expands to the folder this .bat lives in, so the project can be
rem    moved or renamed without touching this file. Note the project folder
rem    name itself contains Chinese - hard-coding it here would be the exact
rem    encoding trap described above.
rem ===========================================================================

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo.
  echo   [x] Not found: .venv\Scripts\python.exe
  echo.
  echo   Create the environment once, then run this again:
  echo       python -m venv .venv
  echo       .venv\Scripts\python.exe -m pip --isolated install -r requirements.txt
  echo.
  pause
  exit /b 1
)

rem Tests may pre-set this to 0 to skip opening a browser.
if not defined APP_OPEN_BROWSER set APP_OPEN_BROWSER=1

echo.
echo   AI Wardrobe - starting the local service...
echo   Keep this window open while you use it. Close it to stop the service.
echo.

".venv\Scripts\python.exe" run.py
rem run.py exits with 3 when the port was already taken: nothing was started,
rem so there is no reason to leave this window sitting here.
if errorlevel 3 goto already_running

echo.
echo   Service stopped. Press any key to close this window.
pause
exit /b 0

:already_running
echo   A service is already running - nothing new was started.
echo   Closing this window...
rem 'timeout' needs a real console; if it is missing we just close anyway.
timeout /t 3 /nobreak >nul 2>&1
exit /b 0
