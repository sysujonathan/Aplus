@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"
for /f "tokens=2,*" %%A in ('reg query "HKCU\Environment" /v A_WORKBENCH_HOME 2^>nul') do set "A_WORKBENCH_HOME=%%B"
set "APLUS_PYTHON="
if exist ".git\aplus-update\current-python.txt" set /p "APLUS_PYTHON="<".git\aplus-update\current-python.txt"
if not defined APLUS_PYTHON set "APLUS_PYTHON=%CD%\.venv\Scripts\python.exe"
if not exist "%APLUS_PYTHON%" set "APLUS_PYTHON=%CD%\.venv\Scripts\python.exe"
if exist ".git\aplus-update\active-request.txt" (
  set "APLUS_RECOVERY_REQUEST="
  set /p "APLUS_RECOVERY_REQUEST="<".git\aplus-update\active-request.txt"
  for %%F in ("!APLUS_RECOVERY_REQUEST!") do set "APLUS_RECOVERY_WORKER=%%~dpFworker.py"
  if not exist "!APLUS_PYTHON!" (
    echo An unfinished update was found, but its Python recovery runtime is unavailable.
    pause
    exit /b 20
  )
  if not exist "!APLUS_RECOVERY_WORKER!" (
    echo An unfinished update was found, but its recovery helper is unavailable.
    pause
    exit /b 20
  )
  "!APLUS_PYTHON!" "!APLUS_RECOVERY_WORKER!" --recover "!APLUS_RECOVERY_REQUEST!"
  if errorlevel 1 (
    echo The update is still being confirmed or requires maintenance. Aplus was not started.
    pause
    exit /b 20
  )
  set "APLUS_PYTHON="
  if exist ".git\aplus-update\current-python.txt" set /p "APLUS_PYTHON="<".git\aplus-update\current-python.txt"
  if not defined APLUS_PYTHON set "APLUS_PYTHON=%CD%\.venv\Scripts\python.exe"
)
if not exist "%APLUS_PYTHON%" (
  echo Please run setup.cmd first.
  pause
  exit /b 1
)
"%APLUS_PYTHON%" launch.py
if errorlevel 1 pause
