import time
import subprocess
import platform
import shutil

try:
    import psutil
    _PSUTIL = True
except ImportError:
    _PSUTIL = False

_SYSTEM = platform.system()

_APP_ALIASES: dict[str, dict[str, str]] = {

    "chrome":             {"Windows": "chrome",                  "Darwin": "Google Chrome",        "Linux": "google-chrome"},
    "google chrome":      {"Windows": "chrome",                  "Darwin": "Google Chrome",        "Linux": "google-chrome"},
    "firefox":            {"Windows": "firefox",                 "Darwin": "Firefox",              "Linux": "firefox"},
    "edge":               {"Windows": "msedge",                  "Darwin": "Microsoft Edge",       "Linux": "microsoft-edge"},
    "brave":              {"Windows": "brave",                   "Darwin": "Brave Browser",        "Linux": "brave-browser"},
    "safari":             {"Windows": "msedge",                  "Darwin": "Safari",               "Linux": "firefox"},
    "opera":              {"Windows": "opera",                   "Darwin": "Opera",                "Linux": "opera"},
    "whatsapp":           {"Windows": "WhatsApp",                "Darwin": "WhatsApp",             "Linux": "whatsapp"},
    "telegram":           {"Windows": "Telegram",                "Darwin": "Telegram",             "Linux": "telegram"},
    "discord":            {"Windows": "Discord",                 "Darwin": "Discord",              "Linux": "discord"},
    "slack":              {"Windows": "Slack",                   "Darwin": "Slack",                "Linux": "slack"},
    "zoom":               {"Windows": "Zoom",                    "Darwin": "zoom.us",              "Linux": "zoom"},
    "teams":              {"Windows": "msteams",                 "Darwin": "Microsoft Teams",      "Linux": "teams"},
    "skype":              {"Windows": "skype",                   "Darwin": "Skype",                "Linux": "skype"},
    "signal":             {"Windows": "signal",                  "Darwin": "Signal",               "Linux": "signal"},
    "spotify":            {"Windows": "Spotify",                 "Darwin": "Spotify",              "Linux": "spotify"},
    "vlc":                {"Windows": "vlc",                     "Darwin": "VLC",                  "Linux": "vlc"},
    "netflix":            {"Windows": "Netflix",                 "Darwin": "Netflix",              "Linux": "firefox"},
    "vscode":             {"Windows": "code",                    "Darwin": "Visual Studio Code",   "Linux": "code"},
    "visual studio code": {"Windows": "code",                    "Darwin": "Visual Studio Code",   "Linux": "code"},
    "code":               {"Windows": "code",                    "Darwin": "Visual Studio Code",   "Linux": "code"},
    "terminal":           {"Windows": "wt",                      "Darwin": "Terminal",             "Linux": "x-terminal-emulator"},
    "cmd":                {"Windows": "cmd.exe",                 "Darwin": "Terminal",             "Linux": "bash"},
    "powershell":         {"Windows": "powershell.exe",          "Darwin": "Terminal",             "Linux": "bash"},
    "postman":            {"Windows": "Postman",                 "Darwin": "Postman",              "Linux": "postman"},
    "git":                {"Windows": "git-bash",                "Darwin": "Terminal",             "Linux": "bash"},
    "figma":              {"Windows": "Figma",                   "Darwin": "Figma",                "Linux": "figma"},
    "blender":            {"Windows": "blender",                 "Darwin": "Blender",              "Linux": "blender"},
    "word":               {"Windows": "winword",                 "Darwin": "Microsoft Word",       "Linux": "libreoffice --writer"},
    "excel":              {"Windows": "excel",                   "Darwin": "Microsoft Excel",      "Linux": "libreoffice --calc"},
    "powerpoint":         {"Windows": "powerpnt",                "Darwin": "Microsoft PowerPoint", "Linux": "libreoffice --impress"},
    "libreoffice":        {"Windows": "soffice",                 "Darwin": "LibreOffice",          "Linux": "libreoffice"},
    "notepad":            {"Windows": "notepad.exe",             "Darwin": "TextEdit",             "Linux": "gedit"},
    "textedit":           {"Windows": "notepad.exe",             "Darwin": "TextEdit",             "Linux": "gedit"},
    "explorer":           {"Windows": "explorer.exe",            "Darwin": "Finder",               "Linux": "nautilus"},
    "file explorer":      {"Windows": "explorer.exe",            "Darwin": "Finder",               "Linux": "nautilus"},
    "finder":             {"Windows": "explorer.exe",            "Darwin": "Finder",               "Linux": "nautilus"},
    "task manager":       {"Windows": "taskmgr.exe",             "Darwin": "Activity Monitor",     "Linux": "gnome-system-monitor"},
    "settings":           {"Windows": "ms-settings:",            "Darwin": "System Preferences",   "Linux": "gnome-control-center"},
    "calculator":         {"Windows": "calc.exe",                "Darwin": "Calculator",           "Linux": "gnome-calculator"},
    "paint":              {"Windows": "mspaint.exe",             "Darwin": "Preview",              "Linux": "gimp"},
    "instagram":          {"Windows": "Instagram",               "Darwin": "Instagram",            "Linux": "firefox"},
    "tiktok":             {"Windows": "TikTok",                  "Darwin": "TikTok",               "Linux": "firefox"},
    "notion":             {"Windows": "Notion",                  "Darwin": "Notion",               "Linux": "notion"},
    "obsidian":           {"Windows": "Obsidian",                "Darwin": "Obsidian",             "Linux": "obsidian"},
    "capcut":             {"Windows": "CapCut",                  "Darwin": "CapCut",               "Linux": "capcut"},
    "steam":              {"Windows": "steam",                   "Darwin": "Steam",                "Linux": "steam"},
    "epic":               {"Windows": "EpicGamesLauncher",       "Darwin": "Epic Games Launcher",  "Linux": "legendary"},
    "epic games":         {"Windows": "EpicGamesLauncher",       "Darwin": "Epic Games Launcher",  "Linux": "legendary"},
    "youtube":            {"Windows": "https://www.youtube.com", "Darwin": "https://www.youtube.com", "Linux": "https://www.youtube.com"},
    # Antigravity IDE Aliases
    "antigravity":        {"Windows": "antigravity-ide",         "Darwin": "Antigravity",          "Linux": "antigravity"},
    "antigravity ide":    {"Windows": "antigravity-ide",         "Darwin": "Antigravity",          "Linux": "antigravity"},
    "antigravity-ide":    {"Windows": "antigravity-ide",         "Darwin": "Antigravity",          "Linux": "antigravity"},
    "agy":                {"Windows": "antigravity-ide",         "Darwin": "Antigravity",          "Linux": "antigravity"},
    # Tamil App Aliases
    "குரோம்":             {"Windows": "chrome",                  "Darwin": "Google Chrome",        "Linux": "google-chrome"},
    "கூகுள் குரோம்":      {"Windows": "chrome",                  "Darwin": "Google Chrome",        "Linux": "google-chrome"},
    "யூடியூப்":           {"Windows": "https://www.youtube.com", "Darwin": "https://www.youtube.com", "Linux": "https://www.youtube.com"},
    "வாட்ஸ்அப்":          {"Windows": "WhatsApp",                "Darwin": "WhatsApp",             "Linux": "whatsapp"},
    "நோட்பேட்":           {"Windows": "notepad.exe",             "Darwin": "TextEdit",             "Linux": "gedit"},
    "கால்குலேட்டர்":       {"Windows": "calc.exe",                "Darwin": "Calculator",           "Linux": "gnome-calculator"},
    "ஸ்பாட்டிஃபை":        {"Windows": "Spotify",                 "Darwin": "Spotify",              "Linux": "spotify"},
    "டெர்மினல்":          {"Windows": "wt",                      "Darwin": "Terminal",             "Linux": "x-terminal-emulator"},
    "கேமரா":              {"Windows": "microsoft.windows.camera:", "Darwin": "Photo Booth",       "Linux": "cheese"},
    "ஆன்டிகிரேவிட்டி":     {"Windows": "antigravity-ide",         "Darwin": "Antigravity",          "Linux": "antigravity"},
}


def _normalize(raw: str) -> str:
    key = raw.lower().strip()

    if key in _APP_ALIASES:
        return _APP_ALIASES[key].get(_SYSTEM, raw)

    for alias_key, os_map in _APP_ALIASES.items():
        if alias_key in key or key in alias_key:
            return os_map.get(_SYSTEM, raw)

    return raw  

def _launch_windows(app_name: str) -> bool:
    if app_name.startswith("http://") or app_name.startswith("https://"):
        try:
            import webbrowser
            webbrowser.open(app_name)
            time.sleep(1.0)
            return True
        except Exception:
            pass

    # Antigravity IDE direct detection
    if app_name.lower() in ("antigravity", "antigravity ide", "antigravity-ide", "agy"):
        ag_cmd = shutil.which("antigravity-ide")
        if not ag_cmd:
            import os
            from pathlib import Path
            local_app = os.environ.get("LOCALAPPDATA", "")
            if local_app:
                c1 = Path(local_app) / "Programs" / "Antigravity IDE" / "bin" / "antigravity-ide.cmd"
                c2 = Path(local_app) / "Programs" / "Antigravity IDE" / "Antigravity IDE.exe"
                if c1.exists():
                    ag_cmd = str(c1)
                elif c2.exists():
                    ag_cmd = str(c2)
        if ag_cmd:
            try:
                subprocess.Popen(f'"{ag_cmd}"', shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                time.sleep(1.2)
                return True
            except Exception as e:
                print(f"[open_app] Antigravity launch failed: {e}")

    if shutil.which(app_name) or shutil.which(app_name.split(".")[0]):
        try:
            subprocess.Popen(
                app_name,
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(1.5)
            return True
        except Exception as e:
            print(f"[open_app] subprocess failed: {e}")

    if ":" in app_name:
        try:
            subprocess.Popen(f"start {app_name}", shell=True)
            time.sleep(1.0)
            return True
        except Exception:
            pass

    try:
        import pyautogui
        pyautogui.PAUSE = 0.1
        pyautogui.press("win")
        time.sleep(0.7)
        pyautogui.write(app_name, interval=0.05)
        time.sleep(0.9)
        pyautogui.press("enter")
        time.sleep(2.5)
        return True
    except Exception as e:
        print(f"[open_app] Start Menu search failed: {e}")

    return False


def _launch_macos(app_name: str) -> bool:

    try:
        result = subprocess.run(
            ["open", "-a", app_name],
            capture_output=True, timeout=8
        )
        if result.returncode == 0:
            time.sleep(1.0)
            return True
    except Exception:
        pass

    try:
        result = subprocess.run(
            ["open", "-a", f"{app_name}.app"],
            capture_output=True, timeout=8
        )
        if result.returncode == 0:
            time.sleep(1.0)
            return True
    except Exception:
        pass

    binary = shutil.which(app_name) or shutil.which(app_name.lower())
    if binary:
        try:
            subprocess.Popen(
                [binary],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            time.sleep(1.0)
            return True
        except Exception:
            pass

    try:
        import pyautogui
        pyautogui.hotkey("command", "space")
        time.sleep(0.6)
        pyautogui.write(app_name, interval=0.05)
        time.sleep(0.8)
        pyautogui.press("enter")
        time.sleep(1.5)
        return True
    except Exception as e:
        print(f"[open_app] Spotlight failed: {e}")

    return False


_LINUX_TERMINAL_FALLBACKS = [
    "x-terminal-emulator", "gnome-terminal", "konsole", "xfce4-terminal",
    "xterm", "lxterminal", "mate-terminal", "tilix", "alacritty", "kitty",
]

def _launch_linux(app_name: str) -> bool:

    # terminal emulators: try common ones in order
    if app_name in ("x-terminal-emulator", "gnome-terminal", "terminal"):
        for term in _LINUX_TERMINAL_FALLBACKS:
            if shutil.which(term):
                try:
                    subprocess.Popen([term], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    time.sleep(1.0)
                    return True
                except Exception:
                    continue

    binary = (
        shutil.which(app_name) or
        shutil.which(app_name.lower()) or
        shutil.which(app_name.lower().replace(" ", "-")) or
        shutil.which(app_name.lower().replace(" ", "_"))
    )
    if binary:
        try:
            subprocess.Popen(
                [binary],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            time.sleep(1.0)
            return True
        except Exception:
            pass

    try:
        subprocess.run(
            ["xdg-open", app_name],
            capture_output=True, timeout=5
        )
        return True
    except Exception:
        pass

    for desktop_name in [
        app_name.lower(),
        app_name.lower().replace(" ", "-"),
        app_name.lower().replace(" ", ""),
    ]:
        try:
            result = subprocess.run(
                ["gtk-launch", desktop_name],
                capture_output=True, timeout=5
            )
            if result.returncode == 0:
                return True
        except Exception:
            pass

    return False


_OS_LAUNCHERS = {
    "Windows": _launch_windows,
    "Darwin":  _launch_macos,
    "Linux":   _launch_linux,
}

def open_app(
    parameters=None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    app_name = (parameters or {}).get("app_name", "").strip()

    if not app_name:
        return "No application name provided."

    launcher = _OS_LAUNCHERS.get(_SYSTEM)
    if launcher is None:
        return f"Unsupported operating system: {_SYSTEM}"

    normalized = _normalize(app_name)
    print(f"[open_app] Launching: '{app_name}' -> '{normalized}' ({_SYSTEM})")

    if player:
        player.write_log(f"[open_app] {app_name}")

    try:
        if launcher(normalized):
            return f"Opened {app_name}."
        if normalized.lower() != app_name.lower():
            if launcher(app_name):
                return f"Opened {app_name}."
        return (
            f"Could not confirm that {app_name} launched. "
            f"It may still be loading, or it might not be installed."
        )
    except Exception as e:
        print(f"[open_app] Error: {e}")
        return f"Failed to open {app_name}: {e}"


def _close_active_window() -> str:
    try:
        import pyautogui
        if _SYSTEM == "Darwin":
            pyautogui.hotkey("command", "w")
        else:
            pyautogui.hotkey("alt", "f4")
        return "Closed active window, Sir."
    except Exception as e:
        return f"Failed to close active window: {e}"


def _minimize_active_window() -> str:
    try:
        import pyautogui
        if _SYSTEM == "Darwin":
            pyautogui.hotkey("command", "m")
        else:
            pyautogui.hotkey("win", "down")
        return "Minimized active window, Sir."
    except Exception as e:
        return f"Failed to minimize active window: {e}"


def _minimize_all_windows() -> str:
    try:
        if _SYSTEM == "Windows":
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", "(New-Object -ComObject Shell.Application).MinimizeAll()"],
                capture_output=True, timeout=5
            )
            return "Minimized all windows, Sir."
        import pyautogui
        if _SYSTEM == "Darwin":
            pyautogui.hotkey("command", "option", "h")
        else:
            pyautogui.hotkey("ctrl", "alt", "d")
        return "Minimized all windows, Sir."
    except Exception as e:
        return f"Failed to minimize all windows: {e}"


def _close_windows_app(raw_name: str, normalized: str) -> bool:
    """Close a Windows app by name — 4-tier strategy, never fails silently.

    Tier 1: psutil process scan — case-insensitive stem match → taskkill /F /IM
    Tier 2: PowerShell AppActivate by window title → Alt+F4
    Tier 3: Get-Process MainWindowTitle fuzzy match → Stop-Process
    Tier 4: Nuclear taskkill /F /T on any partial match remaining
    """
    raw_lower   = raw_name.lower().strip()
    norm_lower  = normalized.lower().strip()
    # Build search stems: raw, normalized, both without .exe
    stems = {
        raw_lower,
        norm_lower,
        raw_lower.replace(".exe", ""),
        norm_lower.replace(".exe", ""),
        raw_lower.replace(" ", ""),
        norm_lower.replace(" ", ""),
    }
    # Also add exe variants
    exe_names = {s + ".exe" if not s.endswith(".exe") else s for s in stems}
    all_targets = stems | exe_names

    closed = False

    # ── Tier 1: psutil scan + taskkill /F (force, no dialog) ─────────────────
    if _PSUTIL:
        killed: list[str] = []
        for p in psutil.process_iter(["pid", "name"]):
            try:
                pname = (p.info["name"] or "").lower()
                pname_stem = pname.replace(".exe", "")
                if any(
                    pname == t or pname_stem == t.replace(".exe", "") or
                    t.replace(".exe", "") in pname_stem
                    for t in all_targets
                ):
                    result = subprocess.run(
                        ["taskkill", "/F", "/T", "/IM", p.info["name"]],
                        capture_output=True
                    )
                    if result.returncode == 0:
                        killed.append(p.info["name"])
                        closed = True
            except (psutil.NoSuchProcess, psutil.AccessDenied, Exception):
                pass

        if closed:
            time.sleep(0.5)
            return True

    # ── Tier 2: PowerShell AppActivate → Alt+F4 ───────────────────────────────
    # Try multiple title variants so it catches "Google Chrome", "chrome", etc.
    title_variants = [
        raw_name, normalized,
        raw_name.replace(".exe", ""), normalized.replace(".exe", ""),
        raw_name.title(), normalized.title(),
    ]
    for title in dict.fromkeys(title_variants):   # deduplicate, preserve order
        clean = title.replace("'", "").replace('"', "").replace(".exe", "").strip()
        if not clean:
            continue
        ps_script = (
            f"$ws = New-Object -ComObject WScript.Shell; "
            f"if ($ws.AppActivate('{clean}')) {{ "
            f"Start-Sleep -Milliseconds 200; "
            f"$ws.SendKeys('%{{F4}}'); exit 0 }} exit 1"
        )
        res = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True
        )
        if res.returncode == 0:
            time.sleep(0.3)
            return True

    # ── Tier 3: Get-Process MainWindowTitle fuzzy → Stop-Process ─────────────
    for stem in {s.replace(".exe", "") for s in stems}:
        if not stem:
            continue
        ps_script = (
            f"$procs = Get-Process | Where-Object {{ "
            f"  $_.MainWindowTitle -like '*{stem}*' -or "
            f"  $_.Name -like '*{stem}*' }}; "
            f"if ($procs) {{ $procs | Stop-Process -Force; exit 0 }} exit 1"
        )
        res = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True
        )
        if res.returncode == 0:
            return True

    # ── Tier 4: Nuclear taskkill /F /T on any remaining partial match ─────────
    if _PSUTIL:
        for p in psutil.process_iter(["pid", "name"]):
            try:
                pname = (p.info["name"] or "").lower()
                if any(s.replace(".exe", "") in pname for s in stems if len(s.replace(".exe", "")) >= 3):
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/IM", p.info["name"]],
                        capture_output=True
                    )
                    closed = True
            except Exception:
                pass

    return closed




def _minimize_windows_app(raw_name: str, normalized: str) -> bool:
    for target in [raw_name, normalized]:
        clean_target = target.replace("'", "").replace('"', '').replace('.exe', '')
        ps_script = f"""
        $ws = New-Object -ComObject WScript.Shell
        if ($ws.AppActivate('{clean_target}')) {{
            Start-Sleep -Milliseconds 150
            $ws.SendKeys('% n')
            exit 0
        }}
        exit 1
        """
        res = subprocess.run(["powershell", "-NoProfile", "-Command", ps_script], capture_output=True)
        if res.returncode == 0:
            return True

    # Fallback: if focused, send Win+Down
    try:
        import pyautogui
        pyautogui.hotkey("win", "down")
        return True
    except Exception:
        return False


def _close_macos_app(app_name: str) -> bool:
    try:
        script = f'tell application "{app_name}" to quit'
        res = subprocess.run(["osascript", "-e", script], capture_output=True)
        return res.returncode == 0
    except Exception:
        return False


def _minimize_macos_app(app_name: str) -> bool:
    try:
        script = (
            f'tell application "System Events" to '
            f'set miniaturized of window 1 of (first process whose name contains "{app_name}") to true'
        )
        res = subprocess.run(["osascript", "-e", script], capture_output=True)
        return res.returncode == 0
    except Exception:
        return False


def _close_linux_app(app_name: str) -> bool:
    try:
        res = subprocess.run(["wmctrl", "-c", app_name], capture_output=True)
        if res.returncode == 0:
            return True
        subprocess.run(["pkill", "-f", app_name], capture_output=True)
        return True
    except Exception:
        return False


def _minimize_linux_app(app_name: str) -> bool:
    try:
        res = subprocess.run(["xdotool", "search", "--name", app_name, "windowminimize"], capture_output=True)
        return res.returncode == 0
    except Exception:
        return False


def close_app(
    parameters=None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    """
    Closes any running application or active window.
    parameters: {"app_name": "chrome" | "notepad" | "active" | "all" | ...}
    """
    app_name = (parameters or {}).get("app_name", "").strip()

    if player:
        player.write_log(f"[close_app] Closing: '{app_name or 'active window'}'")

    if not app_name or app_name.lower() in ("active", "current", "this", "window", "active window", "current window"):
        return _close_active_window()

    if app_name.lower() in ("jarvis", "yourself", "this assistant", "jarvis assistant"):
        if player and hasattr(player, "prompt_close"):
            player.prompt_close()
            return "Confirmation requested to close JARVIS, Sir. Please confirm."
        return "Confirmation requested to close JARVIS, Sir."

    normalized = _normalize(app_name)
    print(f"[close_app] Closing: '{app_name}' -> '{normalized}' ({_SYSTEM})")

    if _SYSTEM == "Windows":
        success = _close_windows_app(app_name, normalized)
    elif _SYSTEM == "Darwin":
        success = _close_macos_app(app_name) or _close_macos_app(normalized)
    else:
        success = _close_linux_app(app_name) or _close_linux_app(normalized)

    if success:
        return f"Closed {app_name}, Sir."
    return f"Attempted to close {app_name}, but it may not be currently running, Sir."


def minimize_app(
    parameters=None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    """
    Minimizes any running application window, active window, or all windows.
    parameters: {"app_name": "chrome" | "notepad" | "active" | "all" | ...}
    """
    app_name = (parameters or {}).get("app_name", "").strip()

    if player:
        player.write_log(f"[minimize_app] Minimizing: '{app_name or 'active window'}'")

    if app_name.lower() in ("jarvis", "yourself", "this assistant", "jarvis assistant"):
        if player and hasattr(player, "minimize"):
            player.minimize()
            return "JARVIS window minimized, Sir."
        return "JARVIS window minimized, Sir."

    if app_name.lower() in ("all", "all windows", "all apps", "desktop", "everything"):
        return _minimize_all_windows()

    if not app_name or app_name.lower() in ("active", "current", "this", "window", "active window", "current window"):
        return _minimize_active_window()

    normalized = _normalize(app_name)
    print(f"[minimize_app] Minimizing: '{app_name}' -> '{normalized}' ({_SYSTEM})")

    if _SYSTEM == "Windows":
        success = _minimize_windows_app(app_name, normalized)
    elif _SYSTEM == "Darwin":
        success = _minimize_macos_app(app_name) or _minimize_macos_app(normalized)
    else:
        success = _minimize_linux_app(app_name) or _minimize_linux_app(normalized)

    if success:
        return f"Minimized {app_name}, Sir."
    return f"Attempted to minimize {app_name}, Sir."