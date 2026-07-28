@echo off
echo Starting XAUUSD Bot in background...
cd /d "%~dp0"
start /B pythonw mt5_bot.py
echo Bot started! PID: 
tasklist /fi "WINDOWTITLE eq pythonw" 2>nul
echo.
echo Check logs: type xau_bot.log
echo Check status: python -c "import MetaTrader5 as mt5; mt5.initialize(); i=mt5.account_info(); print(f'Balance: ${i.balance:,.2f} Equity: ${i.equity:,.2f}'); mt5.shutdown()"
echo.
echo To stop: taskkill /f /im pythonw.exe
