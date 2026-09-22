import os
import sys
from pathlib import Path
import win32com.client

shell = win32com.client.Dispatch("WScript.Shell")
desktop = Path(shell.SpecialFolders("Desktop"))

python = Path(sys.executable)
pythonw = python.parent / "pythonw.exe"
if not pythonw.exists():
    pythonw = python

base_dir = Path(__file__).resolve().parent
main_script = base_dir / "main.py"
icon_path = base_dir / "config" / "jarvis.ico"

# 1. Main GUI Shortcut (Windowed - launches UI directly, no console window)
sc1 = shell.CreateShortcut(str(desktop / "JARVIS.lnk"))
sc1.TargetPath = str(pythonw)
sc1.Arguments = f'"{main_script}"'
sc1.WorkingDirectory = str(base_dir)
sc1.IconLocation = f"{icon_path},0"
sc1.Description = "JARVIS AI Assistant"
sc1.Save()
print("GUI Shortcut created at:", desktop / "JARVIS.lnk")

# 2. Debug Console Shortcut (Interactive console for live status/logs)
debug_bat = base_dir / "run_jarvis_debug.bat"
if not debug_bat.exists():
    debug_bat = base_dir.parent / "run_jarvis_debug.bat"
sc2 = shell.CreateShortcut(str(desktop / "JARVIS Debug.lnk"))
sc2.TargetPath = str(debug_bat)
sc2.WorkingDirectory = str(base_dir)
sc2.IconLocation = f"{icon_path},0"
sc2.Description = "JARVIS Debug Console"
sc2.Save()
print("Debug Shortcut created at:", desktop / "JARVIS Debug.lnk")

# 3. Create Stop JARVIS shortcut
stop_script = base_dir / "stop_jarvis.bat"
if not stop_script.exists():
    stop_script = base_dir.parent / "stop_jarvis.bat"
sc3 = shell.CreateShortcut(str(desktop / "Stop JARVIS.lnk"))
sc3.TargetPath = str(stop_script)
sc3.WorkingDirectory = str(base_dir)
sc3.IconLocation = f"{icon_path},0"
sc3.Description = "Terminate all JARVIS background processes"
sc3.Save()
print("Stop JARVIS Shortcut created at:", desktop / "Stop JARVIS.lnk")

# 4. Remove obsolete Voice Training shortcut if present
old_vt = desktop / "JARVIS Voice Training.lnk"
if old_vt.exists():
    try:
        old_vt.unlink()
        print("Removed obsolete shortcut:", old_vt)
    except Exception as e:
        print("Could not remove old shortcut:", e)

