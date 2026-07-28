@echo off
echo ============================================
echo   XAUUSD Bot - Windows Setup
echo ============================================
echo.

REM Create folder
if not exist "%USERPROFILE%\xau_bot" mkdir "%USERPROFILE%\xau_bot"
cd /d "%USERPROFILE%\xau_bot"

REM Install Python dependencies
echo [1/3] Installing Python packages...
pip install numpy polars flask requests websocket-client gunicorn python-dateutil

REM Install MetaTrader5 package
echo [2/3] Installing MetaTrader5 package...
pip install MetaTrader5

REM Run backtest to verify
echo [3/3] Running backtest to verify...
python main.py backtest

echo.
echo ============================================
echo   Setup Complete!
echo ============================================
echo.
echo   To start paper trading:
echo     cd %USERPROFILE%\xau_bot
echo     python server.py
echo.
echo   Dashboard: http://localhost:8080
echo.
pause
