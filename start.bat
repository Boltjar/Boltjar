@echo off
title Boltjar  -  port 8770
cd /d "%~dp0"

echo ==================================================
echo   BOLTJAR
echo   http://127.0.0.1:8770
echo   (close this window to stop the server)
echo ==================================================
echo.

echo [1/2] Building the editor (latest source)...
call npm run build --prefix editor
echo.

echo [2/2] Starting the server...
echo       A browser tab opens in a few seconds.
start "" powershell -NoProfile -Command "Start-Sleep 3; Start-Process 'http://127.0.0.1:8770'"

".venv\Scripts\python.exe" -m uvicorn boltjar.server:app --port 8770

echo.
echo Server stopped. Press any key to close.
pause >nul
