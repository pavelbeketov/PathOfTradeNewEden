@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
title EVE Trader
cls
echo EVE Trader: market scan, route calculation, HTML report.
echo This can take a few minutes. Keep this window open.
python -u Run_trade.py
if errorlevel 1 goto failed
echo.
echo Opening the finished route report...
start "" "%~dp0results\arbitrage_report.html"
echo Done.
exit /b 0
:failed
echo.
echo The run failed. Read the error above; no new report was created.
pause
exit /b 1