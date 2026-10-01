@echo off
cd /d "%~dp0"
for /f "tokens=2,*" %%A in ('reg query "HKCU\Environment" /v A_WORKBENCH_HOME 2^>nul') do set "A_WORKBENCH_HOME=%%B"
if not exist ".venv\Scripts\python.exe" (
  echo Please run setup.cmd first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" launch.py
if errorlevel 1 pause
