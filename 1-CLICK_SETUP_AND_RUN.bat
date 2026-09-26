@echo off
setlocal enabledelayedexpansion
title JARVIS System Setup & Launcher
color 0b

echo =====================================================================
echo          STARK INDUSTRIES // JARVIS AUTONOMOUS AI ASSISTANT         
echo                    Universal System Setup & Boot                   
echo =====================================================================
echo.

cd /d "%~dp0"

:: 1. Check Python installation
echo [1/5] Checking Python environment...
where python >nul 2>nul
if %errorlevel% neq 0 (
    where py >nul 2>nul
    if %errorlevel% neq 0 (
        echo [!] Python not detected on this system.
        echo [*] Attempting automated Python 3.11 installation via winget...
        winget install Python.Python.3.11 --silent --accept-source-agreements --accept-package-agreements
        if %errorlevel% neq 0 (
            echo.
            echo [ERROR] Please install Python 3.10 or 3.11 from https://www.python.org/downloads/
            echo         Make sure to check "Add Python to PATH" during installation.
            echo.
            pause
            exit /b 1
        )
    )
)

:: Find python command
set PY_CMD=python
python --version >nul 2>nul
if %errorlevel% neq 0 (
    set PY_CMD=py -3
)

echo [*] Python engine detected: %PY_CMD%

:: 2. Setup isolated virtual environment (.venv)
echo.
echo [2/5] Initializing local virtual environment (.venv)...
if not exist ".venv\Scripts\python.exe" (
    echo [*] Creating isolated virtual environment...
    %PY_CMD% -m venv .venv
    if %errorlevel% neq 0 (
        echo [WARN] venv creation failed; will proceed with system Python.
        set VENV_PY=%PY_CMD%
        set VENV_PYW=pythonw
    ) else (
        echo [OK] Virtual environment created successfully.
        set VENV_PY=.venv\Scripts\python.exe
        set VENV_PYW=.venv\Scripts\pythonw.exe
    )
) else (
    echo [OK] Existing virtual environment detected.
    set VENV_PY=.venv\Scripts\python.exe
    set VENV_PYW=.venv\Scripts\pythonw.exe
)

:: 3. Install & upgrade dependencies
echo.
echo [3/5] Verifying dependencies and requirements...
%VENV_PY% -m pip install --upgrade pip --quiet
%VENV_PY% -m pip install -r requirements.txt --quiet
if %errorlevel% neq 0 (
    echo [WARN] Some packages had warnings; continuing...
)

:: 4. Verify config file exists (create keyless template if missing)
echo.
echo [4/5] Checking configuration...
if not exist "config\api_keys.json" (
    if exist "config\api_keys.json.example" (
        copy /y "config\api_keys.json.example" "config\api_keys.json" >nul
        echo [OK] Clean configuration template initialized.
    )
)

:: 5. Create Desktop Shortcuts
echo.
echo [5/5] Generating Desktop Application shortcuts...
%VENV_PY% create_desktop_shortcut.py >nul 2>nul
echo [OK] Desktop shortcuts created!

echo.
echo =====================================================================
echo  SETUP COMPLETE! Launching JARVIS Neural Interface...
echo  (First-time users: Paste your Gemini API key in the setup window)
echo =====================================================================
echo.

start "" "%VENV_PYW%" main.py
exit
