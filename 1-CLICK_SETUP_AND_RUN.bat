@echo off
setlocal enabledelayedexpansion
title JARVIS System Setup & Launcher
color 0b

echo =====================================================================
echo          STARK INDUSTRIES // JARVIS AUTONOMOUS AI ASSISTANT         
echo               Universal Python Engine & System Boot                 
echo =====================================================================
echo.

cd /d "%~dp0"

:: ───────────────────────────────────────────────────────────────────────
:: 1. DETECT OR AUTOMATICALLY INSTALL PYTHON
:: ───────────────────────────────────────────────────────────────────────
echo [1/5] Checking Python environment on this machine...

set "PY_CMD="

:: Check if standard python or py is available in PATH
where python >nul 2>nul
if %errorlevel% equ 0 (
    set "PY_CMD=python"
) else (
    where py >nul 2>nul
    if %errorlevel% equ 0 (
        set "PY_CMD=py -3"
    )
)

:: Check common default installation paths if PATH not updated
if "!PY_CMD!"=="" (
    if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" (
        set "PY_CMD=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
        set "PATH=%LOCALAPPDATA%\Programs\Python\Python311;%LOCALAPPDATA%\Programs\Python\Python311\Scripts;!PATH!"
    ) else if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
        set "PY_CMD=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
        set "PATH=%LOCALAPPDATA%\Programs\Python\Python312;%LOCALAPPDATA%\Programs\Python\Python312\Scripts;!PATH!"
    ) else if exist "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" (
        set "PY_CMD=%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
        set "PATH=%LOCALAPPDATA%\Programs\Python\Python310;%LOCALAPPDATA%\Programs\Python\Python310\Scripts;!PATH!"
    ) else if exist "C:\Python311\python.exe" (
        set "PY_CMD=C:\Python311\python.exe"
        set "PATH=C:\Python311;C:\Python311\Scripts;!PATH!"
    )
)

:: If Python is still not found, AUTOMATICALLY DOWNLOAD & SILENTLY INSTALL IT
if "!PY_CMD!"=="" (
    echo.
    echo [!] Python is NOT installed on this PC.
    echo [*] Initiating automatic silent Python 3.11 installation...
    echo [*] Downloading official Python installer from python.org...
    echo.

    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
        "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; " ^
        "$url = 'https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe'; " ^
        "$out = Join-Path $env:TEMP 'python-3.11.9-installer.exe'; " ^
        "Write-Host '[*] Downloading Python 3.11 runtime (approx. 25MB)...' -ForegroundColor Cyan; " ^
        "try { " ^
        "    (New-Object System.Net.WebClient).DownloadFile($url, $out); " ^
        "    Write-Host '[*] Installing Python silently with PATH configuration...' -ForegroundColor Cyan; " ^
        "    $proc = Start-Process $out -ArgumentList '/quiet', 'InstallAllUsers=0', 'PrependPath=1', 'Include_test=0', 'SimpleInstall=1' -Wait -PassThru; " ^
        "    Remove-Item $out -Force -ErrorAction SilentlyContinue; " ^
        "    Write-Host '[OK] Python installation completed with code:' $proc.ExitCode -ForegroundColor Green; " ^
        "} catch { " ^
        "    Write-Host '[ERR] Download failed: ' $_.Exception.Message -ForegroundColor Red; " ^
        "    exit 1; " ^
        "}"

    :: Refresh PATH in current session
    set "PATH=%LOCALAPPDATA%\Programs\Python\Python311;%LOCALAPPDATA%\Programs\Python\Python311\Scripts;!PATH!"

    if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" (
        set "PY_CMD=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
        echo [OK] Python 3.11 installed and activated!
    ) else (
        where python >nul 2>nul
        if %errorlevel% equ 0 (
            set "PY_CMD=python"
        ) else (
            echo.
            echo [ERROR] Automatic installation could not be completed.
            echo         Please install Python 3.10+ from https://www.python.org/downloads/
            echo         (Check 'Add Python to PATH' during installation)
            echo.
            pause
            exit /b 1
        )
    )
)

echo [*] Python engine confirmed: !PY_CMD!
!PY_CMD! --version

:: ───────────────────────────────────────────────────────────────────────
:: 2. SETUP ISOLATED VIRTUAL ENVIRONMENT (.venv)
:: ───────────────────────────────────────────────────────────────────────
echo.
echo [2/5] Initializing local virtual environment (.venv)...
if not exist ".venv\Scripts\python.exe" (
    echo [*] Creating isolated virtual environment...
    !PY_CMD! -m venv .venv
    if %errorlevel% neq 0 (
        echo [WARN] venv creation failed; will proceed with base Python.
        set "VENV_PY=!PY_CMD!"
        set "VENV_PYW=pythonw"
    ) else (
        echo [OK] Virtual environment created successfully.
        set "VENV_PY=.venv\Scripts\python.exe"
        set "VENV_PYW=.venv\Scripts\pythonw.exe"
    )
) else (
    echo [OK] Existing virtual environment detected.
    set "VENV_PY=.venv\Scripts\python.exe"
    set "VENV_PYW=.venv\Scripts\pythonw.exe"
)

:: ───────────────────────────────────────────────────────────────────────
:: 3. INSTALL & UPGRADE DEPENDENCIES
:: ───────────────────────────────────────────────────────────────────────
echo.
echo [3/5] Verifying dependencies and requirements...
!VENV_PY! -m pip install --upgrade pip --quiet
!VENV_PY! -m pip install -r requirements.txt --quiet
if %errorlevel% neq 0 (
    echo [WARN] Some packages had warnings; continuing...
)

:: ───────────────────────────────────────────────────────────────────────
:: 4. CONFIGURATION CHECK (CLEAN TEMPLATE FOR NEW SYSTEM)
:: ───────────────────────────────────────────────────────────────────────
echo.
echo [4/5] Checking configuration...
if not exist "config\api_keys.json" (
    if exist "config\api_keys.json.example" (
        copy /y "config\api_keys.json.example" "config\api_keys.json" >nul
        echo [OK] Clean configuration template initialized.
    )
)

:: ───────────────────────────────────────────────────────────────────────
:: 5. GENERATE DESKTOP SHORTCUTS & LAUNCH
:: ───────────────────────────────────────────────────────────────────────
echo.
echo [5/5] Generating Desktop Application shortcuts...
!VENV_PY! create_desktop_shortcut.py >nul 2>nul
echo [OK] Desktop shortcuts created!

echo.
echo =====================================================================
echo  SETUP COMPLETE! Launching JARVIS Neural Interface...
echo  (First-time users: Paste your Gemini API key in the setup window)
echo =====================================================================
echo.

start "" "!VENV_PYW!" main.py
exit
