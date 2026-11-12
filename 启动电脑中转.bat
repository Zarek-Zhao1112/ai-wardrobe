@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo   ai 衣橱 - 电脑中转（端口 8891）
echo   ----------------------------------------
echo   手机上的所有 AI 请求都走这里，它不开手机就报"连接超时"。
echo.

".venv\Scripts\python.exe" "scripts\bridge\manage.py" start
if errorlevel 1 goto failed

echo.
echo   [OK] 已经启动，窗口可以直接关掉。
echo        中转会在后台继续跑，关窗口不影响。
echo.
timeout /t 6 >nul
exit /b 0

:failed
echo.
echo   [失败] 上面是具体原因。常见两种：
echo     1. 端口被占用 -^> 先重启电脑，或关掉别的窗口再试
echo     2. 本机 CLI 登录过期 -^> 需要重新登录电脑版 WorkBuddy
echo.
pause
exit /b 1
