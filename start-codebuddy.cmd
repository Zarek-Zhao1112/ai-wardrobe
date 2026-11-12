@echo off
setlocal
title CodeBuddy Login
cd /d "%~dp0."
set "NODE_EXE=C:\Program Files\nodejs\node.exe"
set "CLI_ENTRY=.local\tools\workbuddy-cli\node_modules\@tencent-ai\codebuddy-code\bin\codebuddy"
if not exist "%NODE_EXE%" (
  echo [ERROR] node.exe not found: %NODE_EXE%
  pause
  exit /b 1
)
if not exist "%CLI_ENTRY%" (
  echo [ERROR] CLI entry not found: %CLI_ENTRY%
  echo current dir: %CD%
  pause
  exit /b 1
)
echo Starting CodeBuddy CLI...
echo.
echo   Step 1: type  /login   and press Enter
echo   Step 2: finish sign-in in the browser
echo   Step 3: close this window, then tell the assistant you are done
echo.
"%NODE_EXE%" "%CLI_ENTRY%"
echo.
echo CLI exited. Press any key to close.
pause >nul
