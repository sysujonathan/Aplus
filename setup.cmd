@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" py -3 -m venv .venv
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install -r requirements-lock.txt
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pytest -q
if errorlevel 1 goto failed
echo Setup and checks complete. Open the A launcher.
pause
exit /b 0
:failed
echo Setup failed. Read the message above. Your data was not deleted.
pause
exit /b 1
