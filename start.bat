@echo off
echo Starting XAUUSD Paper Trading Bot...
echo Dashboard: http://localhost:8080
echo Press Ctrl+C to stop
echo.
cd /d "%USERPROFILE%\xau_bot"
python server.py
pause
