@echo off
title JARVIS Shortcut Creator
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" create_desktop_shortcut.py
) else (
    python create_desktop_shortcut.py
)
pause
