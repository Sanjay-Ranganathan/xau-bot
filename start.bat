@echo off
echo ============================================
echo   XAUUSD Bot — Production
echo ============================================
echo.
echo   MT5 Terminal must be running and logged in.
echo   Bot will trade XAUUSD during London session.
echo.
echo   Logs: xau_bot.log
echo   State: bot_state.json
echo   Press Ctrl+C to stop
echo.
cd /d "%~dp0"
python mt5_bot.py
pause
