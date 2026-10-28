@echo off
cd /d "%~dp0"
for /f "tokens=2,*" %%A in ('reg query "HKCU\Environment" /v A_WORKBENCH_HOME 2^>nul') do set "A_WORKBENCH_HOME=%%B"
set "APLUS_PYTHON="
if exist ".git\aplus-update\current-python.txt" set /p "APLUS_PYTHON="<".git\aplus-update\current-python.txt"
if not defined APLUS_PYTHON set "APLUS_PYTHON=%CD%\.venv\Scripts\python.exe"
if not exist "%APLUS_PYTHON%" set "APLUS_PYTHON=%CD%\.venv\Scripts\python.exe"
if not exist "%APLUS_PYTHON%" (
  echo Please run setup.cmd first.
  pause
  exit /b 1
)
"%APLUS_PYTHON%" launch.py
if errorlevel 1 pause
