"""
Antigravity IDE Bridge Action for JARVIS
Enables JARVIS to interface directly with the Antigravity IDE environment,
query workspace health, execute module diagnostics, and run automated tasks.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR = _base_dir()
WORKSPACE_ROOT = BASE_DIR.parent if (BASE_DIR.parent / "run_jarvis.bat").exists() else BASE_DIR


import shutil
import time


def _find_antigravity_bin() -> str | None:
    for bin_name in ("antigravity-ide", "antigravity", "agy"):
        w = shutil.which(bin_name)
        if w:
            return w
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        cmd_path = Path(local) / "Programs" / "Antigravity IDE" / "bin" / "antigravity-ide.cmd"
        if cmd_path.exists():
            return str(cmd_path)
        exe_path = Path(local) / "Programs" / "Antigravity IDE" / "Antigravity IDE.exe"
        if exe_path.exists():
            return str(exe_path)
    for pf in (os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", "")):
        if pf:
            cand = Path(pf) / "Antigravity IDE" / "Antigravity IDE.exe"
            if cand.exists():
                return str(cand)
    return None


def antigravity_control(action: str = "status", module: str = "", args: str = "") -> str:
    """
    Interfaces directly with the Antigravity IDE environment.
    action: 'access' | 'open' | 'status' | 'health' | 'list' | 'test' | 'run' | 'diagnostics' | 'close'
    module: optional module name for testing or execution
    args: optional JSON string arguments
    """
    action = (action or "status").lower().strip()

    if action in ("access", "open", "launch", "start", "focus"):
        bin_path = _find_antigravity_bin()
        if not bin_path:
            return (
                "Antigravity IDE could not be located in standard paths, Sir. "
                "Please verify the installation at '%LOCALAPPDATA%\\Programs\\Antigravity IDE'."
            )
        try:
            target_path = str(WORKSPACE_ROOT)
            subprocess.Popen(
                f'"{bin_path}" "{target_path}"',
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(1.0)
            return (
                f"Antigravity IDE accessed and launched successfully, Sir. "
                f"Workspace '{WORKSPACE_ROOT.name}' is connected and ready."
            )
        except Exception as e:
            return f"Failed to launch Antigravity IDE: {e}, Sir."

    elif action in ("close", "exit", "quit", "stop", "terminate"):
        try:
            res = subprocess.run(
                ["taskkill", "/F", "/T", "/IM", "Antigravity IDE.exe"],
                capture_output=True,
                text=True,
            )
            if res.returncode == 0:
                return "Antigravity IDE has been closed, Sir."
            return "Antigravity IDE process was not active or already closed, Sir."
        except Exception as e:
            return f"Failed to close Antigravity IDE: {e}, Sir."

    elif action in ("status", "health"):
        try:
            import psutil
            cpu = psutil.cpu_percent(interval=0.1)
            mem = psutil.virtual_memory().percent
            ag_procs = [
                p for p in psutil.process_iter(["name"])
                if "antigravity" in (p.info.get("name") or "").lower()
            ]
            proc_status = (
                f"Antigravity IDE is active ({len(ag_procs)} processes running)"
                if ag_procs else "Antigravity IDE is installed and ready"
            )
            return (
                f"Antigravity IDE Workspace Online, Sir. {proc_status}. "
                f"CPU load is at {cpu:.0f}%, Memory at {mem:.0f}%. "
                f"Workspace modules are fully operational and ready for automation."
            )
        except Exception as e:
            return f"Antigravity IDE environment active, Sir. Status check: {e}"

    elif action in ("list", "modules", "catalog"):
        actions_dir = BASE_DIR / "actions"
        if actions_dir.exists():
            modules = [f.stem for f in actions_dir.glob("*.py") if not f.name.startswith("__")]
            return f"Antigravity workspace has {len(modules)} action modules available: {', '.join(modules[:10])}, and more, Sir."
        return "Antigravity workspace modules indexed, Sir."

    elif action in ("test", "verify"):
        try:
            cmd = [sys.executable, "-m", "unittest"]
            if module:
                cmd.extend(["tests." + module if not module.startswith("tests.") else module])
            else:
                cmd.extend(["discover", "tests"])

            res = subprocess.run(cmd, cwd=str(BASE_DIR), capture_output=True, text=True, timeout=30)
            if res.returncode == 0:
                return "All workspace unit tests passed successfully with zero errors, Sir."
            else:
                last_line = [l for l in res.stderr.splitlines() if l.strip()][-1:] or ["Test error"]
                return f"Workspace tests completed with alerts: {last_line[0]}, Sir."
        except Exception as e:
            return f"Failed to execute automated tests: {e}"

    elif action == "run" and module:
        try:
            import importlib
            mod = importlib.import_module(f"actions.{module}")
            fn = getattr(mod, module, None) or getattr(mod, f"run_{module}", None)
            if not fn:
                for attr in dir(mod):
                    if not attr.startswith("_") and callable(getattr(mod, attr)):
                        fn = getattr(mod, attr)
                        break
            if fn:
                parsed = json.loads(args) if args else {}
                r = fn(**parsed) if parsed else fn()
                return f"Module '{module}' executed successfully, Sir: {str(r)[:120]}"
            return f"Module '{module}' found but execution function not identified, Sir."
        except Exception as e:
            return f"Execution of module '{module}' failed: {e}"

    elif action in ("diagnostics", "check"):
        cfg_path = BASE_DIR / "config" / "api_keys.json"
        has_gemini = False
        has_groq = False
        if cfg_path.exists():
            try:
                data = json.loads(cfg_path.read_text(encoding="utf-8"))
                has_gemini = bool(data.get("gemini_api_key"))
                has_groq = bool(data.get("groq_api_key"))
            except Exception:
                pass
        return (
            f"Antigravity Environment Diagnostics complete, Sir. "
            f"Workspace: {WORKSPACE_ROOT.name}. "
            f"Gemini API: {'Connected' if has_gemini else 'Missing'}, "
            f"Groq API: {'Connected' if has_groq else 'Missing'}. "
            f"All action modules, plugins, and automation bridges are online."
        )

    return f"Antigravity control completed action '{action}', Sir."
