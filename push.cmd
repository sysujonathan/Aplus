@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

cd /d "%~dp0"

set "PROXY=http://127.0.0.1:7890"

echo.
echo === A project backup to GitHub ===
echo Dir: %CD%
echo.

if not exist ".git" (
    echo [ERROR] Not a git repo. Put this file in the A project root.
    pause
    exit /b 1
)

git status --porcelain > "%TEMP%\a_status.tmp" 2>nul
set CHANGES=0
for /f "usebackq" %%A in ("%TEMP%\a_status.tmp") do set /a CHANGES+=1
del "%TEMP%\a_status.tmp" >nul 2>&1

if !CHANGES! EQU 0 (
    echo [INFO] No changes detected. Nothing to commit.
    pause
    exit /b 0
)

echo [1/4] Found !CHANGES! changed item^(s^):
git status --short
echo.

rem Commit message: use argument if given, otherwise auto timestamp
set "MSG=%~1"
if "!MSG!"=="" (
    for /f "tokens=1-3 delims=/- " %%a in ("%date%") do set "D=%%a-%%b-%%c"
    for /f "tokens=1-2 delims=:." %%a in ("%time%") do set "T=%%a:%%b"
    set "MSG=backup !D! !T!"
)
echo [2/4] Commit message: !MSG!
echo.

echo [3/4] Committing...
git add -A
git commit -q -m "!MSG!"
if errorlevel 1 (
    echo [ERROR] Commit failed. See output above.
    pause
    exit /b 1
)

echo [4/4] Pushing to GitHub...
set "http_proxy=%PROXY%"
set "https_proxy=%PROXY%"
git push origin main
if errorlevel 1 (
    echo.
    echo [FAILED] Push not completed. Local commit is saved.
    echo   Re-run this script after fixing the cause.
    echo   - Auth prompt: username sysujonathan, password = token
    echo   - workflow scope error: add workflow scope at github.com/settings/tokens
    echo   - timeout: check proxy port 7890
    echo.
    pause
    exit /b 1
)

echo.
echo === Done! Pushed to GitHub ===
echo.
pause
endlocal
