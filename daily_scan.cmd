@echo off
rem Dnevni sken i osvezavanje izvestaja. Pokrece ga Task Scheduler; moze i rucno.
cd /d "%~dp0"
set LOG=data\daily_scan.log
echo. >> %LOG%
echo ===== %date% %time% ===== >> %LOG%
.venv\Scripts\python.exe main.py scan --top 20 >> %LOG% 2>&1
set SCAN_RESULT=%errorlevel%
.venv\Scripts\python.exe main.py foreign >> %LOG% 2>&1
.venv\Scripts\python.exe main.py report >> %LOG% 2>&1
rem Sličice za artifakt na telefonu: preuzima samo nove slike, pa ponovo pravi izveštaj.
.venv\Scripts\python.exe main.py thumbs >> %LOG% 2>&1
exit /b %SCAN_RESULT%
