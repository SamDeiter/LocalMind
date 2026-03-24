@echo off
title LocalMind AI Assistant
echo.
echo  Starting LocalMind...
echo.
cd /d "%~dp0"

:: Open browser after a short delay (gives server time to start)
start "" cmd /c "timeout /t 3 /nobreak >nul & start http://localhost:8001"

:: Launch server on port 8001 (8000 is used by TradeCommander)
python run.py --no-browser --port 8001
pause
