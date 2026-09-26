import platform as _platform
import subprocess as _subprocess
import sys as _sys

# ── Make stdout/stderr UTF-8 tolerant ────────────────────────────────────────
# On non-UTF-8 Windows consoles (cp1254/cp1252/cp936...) any print() containing
# an emoji raises UnicodeEncodeError.  Several of those prints sit inside except
# handlers, so the handler itself would blow up and skip the recovery code that
# follows it — turning a recoverable error into a silent hang.  errors="replace"
# makes every print safe.
for _stream in (_sys.stdout, _sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass   # frozen builds may have no real stream attached

# ── Nuclear: force CREATE_NO_WINDOW on EVERY subprocess call on Windows ───────
# This patches Popen itself, so no per-file flag is needed anywhere.
if _platform.system() == "Windows":
    _OrigPopen = _subprocess.Popen

    class _Popen(_OrigPopen):
        def __init__(self, args, **kw):
            kw["creationflags"] = kw.get("creationflags", 0) | _subprocess.CREATE_NO_WINDOW
            kw.pop("startupinfo", None)   # drop any stale/shared STARTUPINFO
            super().__init__(args, **                       kw)

    _subprocess.Popen = _Popen

# ─────────────────────────────────────────────────────────────────────────────

import asyncio
import re
import random
import threading
import time
import json
import sys
import traceback
from datetime import datetime
from pathlib import Path

import sounddevice as sd
import numpy as np
from google import genai
from google.genai import types
from ui import JarvisUI
from memory.memory_manager import (
    load_memory, update_memory, format_memory_for_prompt,
    save_session_summary, pop_last_session,
)

from actions.file_processor import file_processor
from actions.flight_finder     import flight_finder
from actions.open_app          import open_app, close_app, minimize_app
from actions.weather_report    import weather_action
from actions.send_message      import send_message, parse_whatsapp_voice_command
from actions.reminder          import reminder
from actions.computer_settings import computer_settings
from actions.screen_processor  import _capture_camera, _capture_screen
from actions.youtube_video     import youtube_video
from actions.desktop           import desktop_control
from actions.browser_control   import browser_control
from actions.auto_answer       import auto_answer
from actions.file_controller   import file_controller
from actions.code_helper       import code_helper
from actions.dev_agent         import dev_agent
from actions.web_search        import web_search as web_search_action
from actions.computer_control  import computer_control
from actions.game_updater      import game_updater
from actions.system_monitor    import SystemMonitor, get_system_status
from actions.proactive         import ProactiveEngine
from actions.background_monitor import (
    add_monitor, remove_monitor, list_monitors, check_all as monitor_check_all,
)
from actions.web_search        import _news as _fetch_news_sync
from memory.config_manager     import get_brief_enabled
from core.plugin_loader        import discover_plugins

def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent

BASE_DIR        = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"
PROMPT_PATH     = BASE_DIR / "core" / "prompt.txt"
LIVE_MODELS     = [
    "models/gemini-2.5-flash-native-audio-latest",
    "models/gemini-2.5-flash-native-audio-preview-12-2025",
]
LIVE_MODEL      = LIVE_MODELS[0]
CHANNELS            = 1
SEND_SAMPLE_RATE    = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE          = 1024

def _get_api_key() -> str:
    if not API_CONFIG_PATH.exists():
        return ""
    try:
        with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f).get("gemini_api_key", "").strip()
    except Exception:
        return ""


def _load_system_prompt() -> str:
    try:
        return PROMPT_PATH.read_text(encoding="utf-8")
    except Exception:
        return (
            "You are JARVIS, Tony Stark's AI assistant. "
            "Be concise, direct, and always use the provided tools to complete tasks. "
            "Never simulate or guess results — always call the appropriate tool."
        )

_CTRL_RE = re.compile(r"<ctrl\d+>", re.IGNORECASE)

def _clean_transcript(text: str) -> str:    
    text = _CTRL_RE.sub("", text)
    text = re.sub(r"[\x00-\x08\x0b-\x1f]", "", text)
    return text.strip()

TOOL_DECLARATIONS = [
    {
        "name": "open_app",
        "description": (
            "Opens any application on the computer. "
            "Use this whenever the user asks to open, launch, or start any app, "
            "website, or program. Always call this tool — never just say you opened it."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "app_name": {
                    "type": "STRING",
                    "description": "Exact name of the application (e.g. 'WhatsApp', 'Chrome', 'Spotify')"
                }
            },
            "required": ["app_name"]
        }
    },
    {
        "name": "close_app",
        "description": (
            "Closes ANY running application or window on the computer by name, or closes the active app or all apps. "
            "Use whenever the user asks to close, quit, kill, or terminate any application "
            "(e.g. 'close Chrome', 'close Notepad', 'close Spotify', 'close WhatsApp', 'close active window', 'close all apps')."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "app_name": {
                    "type": "STRING",
                    "description": "Name of the application to close (e.g. 'Chrome', 'Notepad', 'Spotify', 'active', or 'all')"
                }
            },
            "required": ["app_name"]
        }
    },
    {
        "name": "minimize_app",
        "description": (
            "Minimizes ANY running application window on the computer by name, or minimizes all windows or the active app. "
            "Use whenever the user asks to minimize any application or window "
            "(e.g. 'minimize Chrome', 'minimize Notepad', 'minimize Spotify', 'minimize all windows', 'minimize current window')."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "app_name": {
                    "type": "STRING",
                    "description": "Name of the application to minimize (e.g. 'Chrome', 'Notepad', 'all', or 'active')"
                }
            },
            "required": []
        }
    },
    {
        "name": "web_search",
        "description": (
            "Searches the web. Use for ANY question about current facts, events, prices, "
            "or topics — always prefer this over guessing. "
            "Modes: 'search' (default), 'news' (latest headlines on a topic), "
            "'research' (deep comprehensive answer), 'price' (product cost lookup), "
            "'compare' (side-by-side comparison of items)."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query":  {"type": "STRING", "description": "Search query or topic"},
                "mode":   {"type": "STRING", "description": "search | news | research | price | compare"},
                "items":  {"type": "ARRAY",  "items": {"type": "STRING"}, "description": "Items to compare (compare mode)"},
                "aspect": {"type": "STRING", "description": "Comparison aspect: price | specs | reviews | features"},
            },
            "required": ["query"]
        }
    },
    {
        "name": "system_status",
        "description": (
            "Returns real-time system metrics: CPU usage, RAM, GPU load, CPU temperature, "
            "uptime, and process count. Use when the user asks about computer performance, "
            "temperature, memory, or resource usage."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
        }
    },
    {
        "name": "weather_report",
        "description": "Gives the weather report to user",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "city": {"type": "STRING", "description": "City name"}
            },
            "required": ["city"]
        }
    },
    {
        "name": "send_message",
        "description": "Sends a text message via WhatsApp, Telegram, or other messaging platform. 'receiver' must always be the clean English recipient contact name (e.g. 'Arun', 'Rahul', 'Amma') stripped of Tamil/Tanglish suffixes like '-ku', 'kitta'. 'message_text' must preserve the exact Tanglish, Tamil, or English phrasing as requested by Sir.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "receiver":     {"type": "STRING", "description": "Recipient contact name in clean English for search (e.g. 'Arun', 'Rahul', 'Mom')"},
                "message_text": {"type": "STRING", "description": "The exact message to send in Tanglish, Tamil, or English"},
                "platform":     {"type": "STRING", "description": "Platform: WhatsApp, Telegram, etc."}
            },
            "required": ["receiver", "message_text", "platform"]
        }
    },
    {
        "name": "reminder",
        "description": "Sets a timed reminder using Task Scheduler.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "date":    {"type": "STRING", "description": "Date in YYYY-MM-DD format"},
                "time":    {"type": "STRING", "description": "Time in HH:MM format (24h)"},
                "message": {"type": "STRING", "description": "Reminder message text"}
            },
            "required": ["date", "time", "message"]
        }
    },
    {
        "name": "youtube_video",
        "description": (
            "Controls YouTube. Use for: playing videos, skipping to the next song/video (using Shift + N shortcut), "
            "summarizing a video's content, getting video info, or showing trending videos."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "play | next | summarize | get_info | trending (default: play). Use 'next' to play the next song/video via Shift + N shortcut"},
                "query":  {"type": "STRING", "description": "Search query for play action"},
                "save":   {"type": "BOOLEAN", "description": "Save summary to Notepad (summarize only)"},
                "region": {"type": "STRING", "description": "Country code for trending e.g. TR, US"},
                "url":    {"type": "STRING", "description": "Video URL for get_info action"},
            },
            "required": []
        }
    },
    {
        "name": "auto_answer",
        "description": (
            "Autonomous auto-answer and form/quiz solver using Playwright. "
            "Scans web page or quiz for questions, finds best answers from local scratchpad or web, "
            "fills inputs, dropdowns, radios, checkboxes, captures verification screenshots, "
            "and requests explicit permission before submitting."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "url": {"type": "STRING", "description": "Target webpage or form URL (optional, defaults to current active page)"},
                "action": {"type": "STRING", "description": "auto_answer | scan | submit"},
                "answers": {"type": "OBJECT", "description": "Optional mapping of field names/questions to specific custom answers"},
                "allow_submit": {"type": "BOOLEAN", "description": "True only if user explicitly allowed form submission"}
            },
            "required": []
        }
    },
    {
        "name": "screen_process",
        "description": (
            "Captures the screen or webcam image and lets you analyze it. "
            "MUST be called when user asks what is on screen, what you see, "
            "look at camera, analyze my screen, etc. "
            "You have NO visual ability without this tool. "
            "After the image is captured it is sent directly to you — describe what you see and answer the user's question. "
            "When using camera: the live view stays open until user says close it or calls close_camera."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "angle": {"type": "STRING", "description": "'screen' to capture display, 'camera' for webcam. Default: 'screen'"},
                "text":  {"type": "STRING", "description": "The question or instruction about the captured image"}
            },
            "required": ["text"]
        }
    },
    {
        "name": "close_camera",
        "description": (
            "Closes the live camera view shown on screen. "
            "Call when user says: close camera, stop camera, turn off camera, "
            "kamerayı kapat, kapat, creepy, etc."
        ),
        "parameters": {"type": "OBJECT", "properties": {}, "required": []}
    },
    {
        "name": "computer_settings",
        "description": (
            "Controls the computer: volume, brightness, window management, keyboard shortcuts, "
            "typing text on screen, closing apps, fullscreen, dark mode, WiFi, restart, shutdown, "
            "scrolling, tab management, zoom, screenshots, lock screen, refresh/reload page. "
            "Use for ANY single computer control command."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "The action to perform"},
                "description": {"type": "STRING", "description": "Natural language description of what to do"},
                "value":       {"type": "STRING", "description": "Optional value: volume level, text to type, etc."}
            },
            "required": []
        }
    },
    {
        "name": "browser_control",
        "description": (
            "Controls any web browser. Use for: opening websites, searching the web, "
            "clicking elements, filling forms, scrolling, screenshots, navigation, any web-based task. "
            "Simple open/search requests launch the user's own browser normally (their real profile "
            "and logged-in accounts); interactive actions (click, type, fill_form...) attach an "
            "automation browser. "
            "Always pass the 'browser' parameter when the user specifies a browser (e.g. 'open in Edge', "
            "'use Firefox', 'open Chrome'). Multiple browsers can run simultaneously."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "go_to | search | click | type | scroll | fill_form | smart_click | smart_type | get_text | get_url | press | new_tab | close_tab | screenshot | back | forward | reload | switch | list_browsers | close | close_all"},
                "browser":     {"type": "STRING", "description": "Target browser: chrome | edge | firefox | opera | operagx | brave | vivaldi | safari. Omit to use the currently active browser."},
                "url":         {"type": "STRING", "description": "URL for go_to / new_tab action"},
                "query":       {"type": "STRING", "description": "Search query for search action"},
                "engine":      {"type": "STRING", "description": "Search engine: google | bing | duckduckgo | yandex (default: google)"},
                "selector":    {"type": "STRING", "description": "CSS selector for click/type"},
                "text":        {"type": "STRING", "description": "Text to click or type"},
                "description": {"type": "STRING", "description": "Element description for smart_click/smart_type"},
                "direction":   {"type": "STRING", "description": "up | down for scroll"},
                "amount":      {"type": "INTEGER", "description": "Scroll amount in pixels (default: 500)"},
                "key":         {"type": "STRING", "description": "Key name for press action (e.g. Enter, Escape, F5)"},
                "path":        {"type": "STRING", "description": "Save path for screenshot"},
                "incognito":   {"type": "BOOLEAN", "description": "Open in private/incognito mode"},
                "clear_first": {"type": "BOOLEAN", "description": "Clear field before typing (default: true)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "file_controller",
        "description": "Manages files and folders: list, create, delete, move, copy, rename, read, write, find, disk usage.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "list | create_file | create_folder | delete | move | copy | rename | read | write | find | largest | disk_usage | organize_desktop | info"},
                "path":        {"type": "STRING", "description": "File/folder path or shortcut: desktop, downloads, documents, home"},
                "destination": {"type": "STRING", "description": "Destination path for move/copy"},
                "new_name":    {"type": "STRING", "description": "New name for rename"},
                "content":     {"type": "STRING", "description": "Content for create_file/write"},
                "name":        {"type": "STRING", "description": "File name to search for"},
                "extension":   {"type": "STRING", "description": "File extension to search (e.g. .pdf)"},
                "count":       {"type": "INTEGER", "description": "Number of results for largest"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "desktop_control",
        "description": "Controls the desktop: wallpaper, organize, clean, list, stats.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "wallpaper | wallpaper_url | organize | clean | list | stats | task"},
                "path":   {"type": "STRING", "description": "Image path for wallpaper"},
                "url":    {"type": "STRING", "description": "Image URL for wallpaper_url"},
                "mode":   {"type": "STRING", "description": "by_type or by_date for organize"},
                "task":   {"type": "STRING", "description": "Natural language desktop task"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "code_helper",
        "description": "Writes, edits, explains, runs, or builds code files.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "write | edit | explain | run | build | auto (default: auto)"},
                "description": {"type": "STRING", "description": "What the code should do or what change to make"},
                "language":    {"type": "STRING", "description": "Programming language (default: python)"},
                "output_path": {"type": "STRING", "description": "Where to save the file"},
                "file_path":   {"type": "STRING", "description": "Path to existing file for edit/explain/run/build"},
                "code":        {"type": "STRING", "description": "Raw code string for explain"},
                "args":        {"type": "STRING", "description": "CLI arguments for run/build"},
                "timeout":     {"type": "INTEGER", "description": "Execution timeout in seconds (default: 30)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "dev_agent",
        "description": "Builds complete multi-file projects from scratch: plans, writes files, installs deps, opens VSCode, runs and fixes errors.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "description":  {"type": "STRING", "description": "What the project should do"},
                "language":     {"type": "STRING", "description": "Programming language (default: python)"},
                "project_name": {"type": "STRING", "description": "Optional project folder name"},
                "timeout":      {"type": "INTEGER", "description": "Run timeout in seconds (default: 30)"},
            },
            "required": ["description"]
        }
    },
    {
        "name": "computer_control",
        "description": "Direct computer control: type, click, hotkeys, scroll, move mouse, screenshots, find elements on screen.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "type | smart_type | click | double_click | right_click | hotkey | press | scroll | move | copy | paste | screenshot | wait | clear_field | focus_window | screen_find | screen_click | random_data | user_data"},
                "text":        {"type": "STRING", "description": "Text to type or paste"},
                "x":           {"type": "INTEGER", "description": "X coordinate"},
                "y":           {"type": "INTEGER", "description": "Y coordinate"},
                "keys":        {"type": "STRING", "description": "Key combination e.g. 'ctrl+c'"},
                "key":         {"type": "STRING", "description": "Single key e.g. 'enter'"},
                "direction":   {"type": "STRING", "description": "up | down | left | right"},
                "amount":      {"type": "INTEGER", "description": "Scroll amount (default: 3)"},
                "seconds":     {"type": "NUMBER",  "description": "Seconds to wait"},
                "title":       {"type": "STRING",  "description": "Window title for focus_window"},
                "description": {"type": "STRING",  "description": "Element description for screen_find/screen_click"},
                "type":        {"type": "STRING",  "description": "Data type for random_data"},
                "field":       {"type": "STRING",  "description": "Field for user_data: name|email|city"},
                "clear_first": {"type": "BOOLEAN", "description": "Clear field before typing (default: true)"},
                "path":        {"type": "STRING",  "description": "Save path for screenshot"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "game_updater",
        "description": (
            "THE ONLY tool for ANY Steam or Epic Games request. "
            "Use for: installing, downloading, updating games, listing installed games, "
            "checking download status, scheduling updates. "
            "ALWAYS call directly for any Steam/Epic/game request. "
            "NEVER use browser_control or web_search for Steam/Epic."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":    {"type": "STRING",  "description": "update | install | list | download_status | schedule | cancel_schedule | schedule_status (default: update)"},
                "platform":  {"type": "STRING",  "description": "steam | epic | both (default: both)"},
                "game_name": {"type": "STRING",  "description": "Game name (partial match supported)"},
                "app_id":    {"type": "STRING",  "description": "Steam AppID for install (optional)"},
                "hour":      {"type": "INTEGER", "description": "Hour for scheduled update 0-23 (default: 3)"},
                "minute":    {"type": "INTEGER", "description": "Minute for scheduled update 0-59 (default: 0)"},
                "shutdown_when_done": {"type": "BOOLEAN", "description": "Shut down PC when download finishes"},
            },
            "required": []
        }
    },
    {
        "name": "flight_finder",
        "description": "Searches Google Flights and speaks the best options.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "origin":      {"type": "STRING",  "description": "Departure city or airport code"},
                "destination": {"type": "STRING",  "description": "Arrival city or airport code"},
                "date":        {"type": "STRING",  "description": "Departure date (any format)"},
                "return_date": {"type": "STRING",  "description": "Return date for round trips"},
                "passengers":  {"type": "INTEGER", "description": "Number of passengers (default: 1)"},
                "cabin":       {"type": "STRING",  "description": "economy | premium | business | first"},
                "save":        {"type": "BOOLEAN", "description": "Save results to Notepad"},
            },
            "required": ["origin", "destination", "date"]
        }
    },
    {
        "name": "manage_monitor",
        "description": (
            "Add, remove, or list background monitoring topics. "
            "JARVIS checks these topics once a day and alerts the user when there is a new development. "
            "Use 'add' when the user says 'monitor X', 'track X', 'follow X'. "
            "Use 'remove' when the user says 'stop monitoring X'. "
            "Use 'list' when the user asks what is being monitored. "
            "Do NOT add crypto, financial, or trading topics."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type":        "STRING",
                    "description": "add | remove | list",
                },
                "topic": {
                    "type":        "STRING",
                    "description": "Topic to monitor or stop monitoring (e.g. 'space exploration', 'AI news')",
                },
            },
            "required": ["action"],
        },
    },
    {
        "name": "shutdown_jarvis",
        "description": (
            "Shuts down the assistant completely. "
            "Call this ONLY after the user has explicitly confirmed they want to close or shut down Jarvis "
            "(e.g., when asked for confirmation, user replies yes, close it, confirm, proceed, goodbye). "
            "The user can say this in ANY language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
        }
    },
    {
        "name": "minimize_jarvis",
        "description": (
            "Minimizes the Jarvis application window to the taskbar. "
            "Call this when the user asks to minimize Jarvis, hide the window, minimize screen, etc."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
        }
    },
    {
    "name": "file_processor",
    "description": (
        "Processes any file that the user has uploaded or dropped onto the interface. "
        "Use this when the user refers to an uploaded file and wants an action on it. "
        "Supports: images (describe/ocr/resize/compress/convert), "
        "PDFs (summarize/extract_text/to_word), "
        "Word docs & text files (summarize/fix/reformat/translate), "
        "CSV/Excel (analyze/stats/filter/sort/convert), "
        "JSON/XML (validate/format/analyze), "
        "code files (explain/review/fix/optimize/run/document/test), "
        "audio (transcribe/trim/convert/info), "
        "video (trim/extract_audio/extract_frame/compress/transcribe/info), "
        "archives (list/extract), "
        "presentations (summarize/extract_text). "
        "ALWAYS call this tool when a file has been uploaded and the user gives a command about it. "
        "If the user's command is ambiguous, pick the most logical action for that file type."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "file_path": {
                "type": "STRING",
                "description": "Full path to the uploaded file. Leave empty to use the currently uploaded file."
            },
            "action": {
                "type": "STRING",
                "description": (
                    "What to do with the file. Examples by type:\n"
                    "image: describe | ocr | resize | compress | convert | info\n"
                    "pdf: summarize | extract_text | to_word | info\n"
                    "docx/txt: summarize | fix | reformat | translate_hint | word_count | to_bullet\n"
                    "csv/excel: analyze | stats | filter | sort | convert | info\n"
                    "json: validate | format | analyze | to_csv\n"
                    "code: explain | review | fix | optimize | run | document | test\n"
                    "audio: transcribe | trim | convert | info\n"
                    "video: trim | extract_audio | extract_frame | compress | transcribe | info | convert\n"
                    "archive: list | extract\n"
                    "pptx: summarize | extract_text | analyze"
                )
            },
            "instruction": {
                "type": "STRING",
                "description": "Free-form instruction if action doesn't cover it. E.g. 'translate this to Turkish', 'find all email addresses'"
            },
            "format": {
                "type": "STRING",
                "description": "Target format for conversion. E.g. 'mp3', 'pdf', 'csv', 'png'"
            },
            "width":     {"type": "INTEGER", "description": "Target width for image resize"},
            "height":    {"type": "INTEGER", "description": "Target height for image resize"},
            "scale":     {"type": "NUMBER",  "description": "Scale factor for image resize (e.g. 0.5)"},
            "quality":   {"type": "INTEGER", "description": "Quality 1-100 for image/video compress"},
            "start":     {"type": "STRING",  "description": "Start time for trim: seconds or HH:MM:SS"},
            "end":       {"type": "STRING",  "description": "End time for trim: seconds or HH:MM:SS"},
            "timestamp": {"type": "STRING",  "description": "Timestamp for video frame extraction HH:MM:SS"},
            "column":    {"type": "STRING",  "description": "Column name for CSV filter/sort"},
            "value":     {"type": "STRING",  "description": "Filter value for CSV filter"},
            "condition": {"type": "STRING",  "description": "Filter condition: equals|contains|gt|lt"},
            "ascending": {"type": "BOOLEAN", "description": "Sort order for CSV sort (default: true)"},
            "save":      {"type": "BOOLEAN", "description": "Save result to file (default: true)"},
            "destination": {"type": "STRING", "description": "Output folder for archive extract"},
        },
        "required": []
    }
},
    {
        "name": "save_memory",
        "description": (
            "Save an important personal fact about the user to long-term memory. "
            "Call this silently whenever the user reveals something worth remembering: "
            "name, age, city, job, preferences, hobbies, relationships, projects, or future plans. "
            "Do NOT call for: weather, reminders, searches, or one-time commands. "
            "Do NOT announce that you are saving — just call it silently. "
            "Values must be in English regardless of the conversation language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "category": {
                    "type": "STRING",
                    "description": (
                        "identity — name, age, birthday, city, job, language, nationality | "
                        "preferences — favorite food/color/music/film/game/sport, hobbies | "
                        "projects — active projects, goals, things being built | "
                        "relationships — friends, family, partner, colleagues | "
                        "wishes — future plans, things to buy, travel dreams | "
                        "notes — habits, schedule, anything else worth remembering"
                    )
                },
                "key":   {"type": "STRING", "description": "Short snake_case key (e.g. name, favorite_food, sister_name)"},
                "value": {"type": "STRING", "description": "Concise value in English (e.g. Fatih, pizza, older sister)"},
            },
            "required": ["category", "key", "value"]
        }
    },
]

class JarvisLive:

    def __init__(self, ui: JarvisUI):
        self.ui             = ui
        self._asst_name     = "JARVIS"   # updated each session from config
        self.session              = None
        self.audio_in_queue       = None
        self.out_queue            = None
        self._loop                = None
        self._is_speaking         = False
        self._speaking_lock       = threading.Lock()
        self._phone_active        = False   # True while phone mic is streaming; pauses PC mic
        self._pending_vision       = None    # (img_bytes, mime_type, question, angle) to inject after tool response
        self._vision_cam_active    = False   # True if camera was opened for vision → auto-close after response
        self._vision_close_pending = False   # True after vision injected; next turn_complete closes camera
        self._vision_last_time     = 0.0     # monotonic time of last screen_process call (cooldown guard)
        self._vision_busy          = False   # True while a vision capture/inject cycle is in flight
        self._interrupted          = False   # True while draining audio after user interrupt
        self.ui.on_text_command   = self._on_text_command
        self.ui.on_remote_clicked = self._make_remote_key
        self.ui.on_interrupt      = self.interrupt
        self._turn_done_event: asyncio.Event | None = None
        self._dashboard     = None
        self._briefing_sent    = False          # morning briefing fires once per process
        self._sys_monitor      = SystemMonitor()  # persistent cooldown state
        self._proactive        = ProactiveEngine()
        self._last_user_speech = time.monotonic()  # updated on every user utterance
        self._session_log: list[str] = []          # conversation turns for end-of-session summary
        self._model_index = 0
        self._live_speech_listener = None

        # Voice Learning & Speaker Identification (Project VoicePrint)
        try:
            from core.voice_profile import VoiceProfileManager
            self.voice_manager = VoiceProfileManager()
        except Exception as e:
            print(f"[JARVIS] VoiceProfileManager error: {e}")
            self.voice_manager = None
        self._voice_buffer = bytearray()
        self._verified_speaker = None
        self._last_voice_check = time.monotonic()
        self._voice_enabled = True
        self._voice_security_mode = "smart"
        self._voice_threshold = 0.72
        self._wake_word_enabled = True
        self._wake_timeout = 10.0
        self._wake_words = None
        self._tts_lock = threading.Lock()

        self._enhanced_live = True  # affective dialog + proactive audio; auto-disabled if the server rejects them
        _core_names = {t["name"] for t in TOOL_DECLARATIONS}
        self._plugin_registry = discover_plugins(
            plugins_dir=Path(__file__).resolve().parent / "plugins",
            core_tool_names=_core_names,
            logger=lambda msg: (print(f"[Plugins] {msg}"), self.ui.write_log(f"SYS: {msg}")),
        )
        self.ui.get_plugins = self._plugin_registry.list_for_ui
        self.ui.request_say = self.plugin_say   # plugins: mid-task speech channel

    def plugin_say(self, instruction: str) -> None:
        """
        Thread-safe speech channel for plugins: lets a plugin ask JARVIS to
        say something short WHILE its run() is still executing (plugins block
        their executor thread, so they can't speak through the tool response
        until they finish). The instruction is injected into the Live session
        exactly like a proactive check-in; Gemini phrases it naturally in the
        user's language. Silently a no-op when no session is connected.
        """
        loop = getattr(self, "_loop", None)
        if not loop or not self.session:
            return

        async def _say():
            try:
                await self.session.send_client_content(
                    turns={"parts": [{"text": instruction}]},
                    turn_complete=True,
                )
            except Exception as e:
                print(f"[PluginSay] {e}")

        try:
            asyncio.run_coroutine_threadsafe(_say(), loop)
        except Exception as e:
            print(f"[PluginSay] {e}")

    def _make_remote_key(self):
        """Called from Qt main thread when user presses Remote Control."""
        if self._dashboard is None:
            self.ui.write_log(
                "SYS: Dashboard unavailable. "
                "Run: pip install fastapi \"uvicorn[standard]\" cryptography"
            )
            return None
        key    = self._dashboard.new_key()
        url    = self._dashboard.get_url()
        manual = self._dashboard.get_manual_url()
        return url, key, f"{url}/auto-login?key={key}", manual

    def _speak_local(self, text: str) -> None:
        """Speak text immediately using local EdgeTTS (for Tamil/English) or pyttsx3 in a background thread."""
        def _do():
            with self._tts_lock:
                try:
                    self.set_speaking(True)
                    # Try neural EdgeTTS first: high quality, streams directly to Bluetooth/default output via sounddevice
                    try:
                        from core.tts import EdgeTTSEngine
                        engine = EdgeTTSEngine(
                            voice="en-IN-PrabhatNeural",
                            tamil_voice="ta-IN-ValluvarNeural",
                            rate="+20%"
                        )
                        engine.speak(text)
                        return
                    except Exception as edge_err:
                        print(f"[TTS Local] EdgeTTS failed: {edge_err}, falling back to pyttsx3")

                    import pyttsx3
                    engine = pyttsx3.init()
                    try:
                        import win32com.client
                        spk = win32com.client.Dispatch("SAPI.SpVoice")
                        outputs = spk.GetAudioOutputs()
                        for i in range(outputs.Count):
                            desc = outputs.Item(i).GetDescription().lower()
                            if any(k in desc for k in ("headphones", "headset", "p47", "bluetooth")):
                                spk.AudioOutput = outputs.Item(i)
                                break
                    except Exception:
                        pass
                    engine.setProperty("rate", 205)
                    engine.say(text)
                    engine.runAndWait()
                except Exception as e:
                    print(f"[TTS Local] Error: {e}")
                finally:
                    self.set_speaking(False)

        threading.Thread(target=_do, daemon=True).start()

    def _on_wake_detected(self, matched_phrase: str, has_command: bool = False, full_text: str = ""):
        """Called when 'Hey Jarvis' / 'Jarvis' / 'Hey Jarvius' is detected."""
        print(f"[JARVIS] ⚡ Wake word detected: '{matched_phrase}' (has_command={has_command})")
        self.ui.set_state("LISTENING")
        self.ui.set_voice_status("SIR", "Sir", 1.0)
        self.ui.write_log(f"SYS: ⚡ Wake word detected: '{matched_phrase}'.")

        disp = (full_text or matched_phrase).strip()
        self.ui.set_input_text(disp)

        if self._dashboard and self._loop:
            try:
                asyncio.run_coroutine_threadsafe(
                    self._dashboard.broadcast({
                        "type": "wake",
                        "wake_word": matched_phrase,
                        "text": disp,
                        "ts": datetime.now().isoformat(),
                    }),
                    self._loop
                )
            except Exception:
                pass

        if not has_command:
            # User said "Hey Jarvis" alone -> acknowledge immediately!
            self.ui.write_log(f"Sir: {disp}")
            self._session_log.append(f"Sir: {disp}")

            greetings = [
                "Yes Sir?",
                "At your service, Sir.",
                "I am listening, Sir.",
                "Yes Sir, how can I help you?",
            ]
            chosen = random.choice(greetings)
            self.ui.write_log(f"JARVIS: {chosen}")
            self._session_log.append(f"JARVIS: {chosen}")
            if self.session:
                self.speak(chosen)
            else:
                self._speak_local(chosen)

    async def _execute_local_voice_command(self, text: str) -> bool:
        """Execute common desktop and system commands locally without requiring Gemini Live."""
        t = text.lower().strip().rstrip(".!?")

        # 1. Tamil Greetings, Status & Common Phrases
        if any(k in t for k in ("வணக்கம்", "vanakkam", "வாழ்க வளமுடன்", "ஹலோ", "ஹாய்", "hello jarvis", "hi jarvis")):
            msg = "வணக்கம் சார்! நான் நலமாக இருக்கிறேன், சொல்லுங்கள் உங்களுக்கு என்ன உதவி வேண்டும்?"
            self.ui.write_log(f"JARVIS: {msg}")
            self._speak_local(msg)
            return True

        if any(k in t for k in ("எப்படி இருக்க", "eppadi irukka", "how are you")):
            msg = "நான் எப்போதும் போல சிறப்பாக செயல்படுகிறேன் சார். சொல்லுங்கள் என்ன செய்ய வேண்டும்?"
            self.ui.write_log(f"JARVIS: {msg}")
            self._speak_local(msg)
            return True

        if any(k in t for k in ("என்ன பண்ற", "என்ன செய்கிறாய்", "enna panra", "enna seira", "what are you doing")):
            msg = "நான் உங்கள் கட்டளைகளுக்காக காத்திருக்கிறேன் சார். என்ன செய்ய வேண்டும் சொல்லுங்கள்!"
            self.ui.write_log(f"JARVIS: {msg}")
            self._speak_local(msg)
            return True

        # 2. App opening (English & Tamil/Tanglish: "open chrome", "chrome open pannu", "youtube open", "குரோம் ஓபன் பண்ணு", "யூடியூப் திற")
        app_name = None
        # Pattern A: Prefix command: "open chrome", "திற குரோம்", "ஓபன் யூடியூப்", "launch whatsapp"
        m_pfx = re.match(r"^(?:open|launch|start|run|ஓபன்|ஓப்பன்|திற|லான்ச்)\s+(?:the\s+|app\s+|application\s+)?(.+?)(?:\s+pannu|\s+pannunga|\s+பண்ணு|\s+பண்ணுங்க|\s+செய்|\s+செய்யவும்|\s+திறக்கவும்)?$", t)
        if m_pfx:
            app_name = m_pfx.group(1).strip()
        else:
            # Pattern B: Suffix command: "chrome open pannu", "யூடியூப் ஓபன் பண்ணு", "youtube open", "குரோம் திற"
            m_rev = re.search(r"^(.+?)\s+(?:open|launch|start|ஓபன்|ஓப்பன்|திற|லான்ச்)(?:\s+pannu|\s+pannunga|\s+பண்ணு|\s+பண்ணுங்க|\s+செய்|\s+செய்யவும்|\s+திறக்கவும்)?$", t)
            if m_rev:
                app_name = m_rev.group(1).strip()
            else:
                m_rev2 = re.search(r"^(.+?)\s+(?:திறக்கவும்|open\s+pannu|open\s+pannunga|திற|open)$", t)
                if m_rev2:
                    app_name = m_rev2.group(1).strip()

        if app_name:
            self.ui.write_log(f"SYS: Executing local open_app: '{app_name}'")
            try:
                loop = asyncio.get_event_loop()
                from actions.open_app import open_app
                res = await loop.run_in_executor(None, lambda: open_app({"app_name": app_name}, None, self.ui))
                msg = res or f"Opening {app_name}, Sir."
                self.ui.write_log(f"JARVIS: {msg}")
                self._speak_local(msg)
                return True
            except Exception as e:
                print(f"[Local Command] open_app error: {e}")

        # Close App Command: "close chrome", "chrome close pannu", "மூடு குரோம்", "குரோம் மூடு", "close notepad"
        close_target = None
        m_c_pfx = re.match(r"^(?:close|quit|kill|exit|terminate|க்ளோஸ்|மூடு)\s+(?:the\s+|app\s+|application\s+)?(.+?)(?:\s+pannu|\s+pannunga|\s+பண்ணு|\s+பண்ணுங்க|\s+செய்|\s+செய்யவும்|\s+மூடவும்)?$", t)
        if m_c_pfx:
            close_target = m_c_pfx.group(1).strip()
        else:
            m_c_sfx = re.search(r"^(.+?)\s+(?:close|quit|kill|exit|க்ளோஸ்|மூடு)(?:\s+pannu|\s+pannunga|\s+பண்ணு|\s+பண்ணுங்க|\s+செய்|\s+செய்யவும்|\s+மூடவும்)?$", t)
            if m_c_sfx:
                close_target = m_c_sfx.group(1).strip()

        if (
            t in ("close", "close it", "close jarvis", "close app", "close application", "shutdown", "shut down", "exit", "quit", "bye", "மூடு", "க்ளோஸ்", "க்ளோஸ் பண்ணு", "ஜார்விஸ் மூடு")
            or (close_target and close_target in ("jarvis", "yourself", "this conversation", "this app", "this application", "jarvis assistant"))
        ):
            self.ui.prompt_close()
            msg = "Are you sure you want to close JARVIS, Sir? Please confirm."
            self.ui.write_log(f"JARVIS: {msg}")
            self._speak_local(msg)
            return True

        if close_target:
            self.ui.write_log(f"SYS: Executing local close_app: '{close_target}'")
            try:
                loop = asyncio.get_event_loop()
                from actions.open_app import close_app
                res = await loop.run_in_executor(None, lambda: close_app({"app_name": close_target}, None, self.ui))
                msg = res or f"Closed {close_target}, Sir."
                self.ui.write_log(f"JARVIS: {msg}")
                self._speak_local(msg)
                return True
            except Exception as e:
                print(f"[Local Command] close_app error: {e}")

        # Minimize App Command: "minimize chrome", "chrome minimize pannu", "மினிமைஸ் குரோம்", "minimize all"
        min_target = None
        if t in ("minimize all", "minimize everything", "show desktop", "மினிமைஸ் ஆல்"):
            min_target = "all"
        else:
            m_m_pfx = re.match(r"^(?:minimize|hide|மினிமைஸ்)\s+(?:the\s+|app\s+|application\s+)?(.+?)(?:\s+pannu|\s+pannunga|\s+பண்ணு|\s+பண்ணுங்க)?$", t)
            if m_m_pfx:
                min_target = m_m_pfx.group(1).strip()
            else:
                m_m_sfx = re.search(r"^(.+?)\s+(?:minimize|hide|மினிமைஸ்)(?:\s+pannu|\s+pannunga|\s+பண்ணு|\s+பண்ணுங்க)?$", t)
                if m_m_sfx:
                    min_target = m_m_sfx.group(1).strip()

        if (
            t in ("minimize", "minimize it", "minimize jarvis", "hide", "hide jarvis", "hide window", "மினிமைஸ்")
            or (min_target and min_target in ("jarvis", "yourself", "this conversation", "this app", "this window", "jarvis assistant"))
        ):
            self.ui.minimize()
            msg = "Minimizing window, Sir."
            self.ui.write_log(f"SYS: {msg}")
            self._speak_local(msg)
            return True

        if min_target:
            self.ui.write_log(f"SYS: Executing local minimize_app: '{min_target}'")
            try:
                loop = asyncio.get_event_loop()
                from actions.open_app import minimize_app
                res = await loop.run_in_executor(None, lambda: minimize_app({"app_name": min_target}, None, self.ui))
                msg = res or f"Minimized {min_target}, Sir."
                self.ui.write_log(f"JARVIS: {msg}")
                self._speak_local(msg)
                return True
            except Exception as e:
                print(f"[Local Command] minimize_app error: {e}")

        # 3. WhatsApp Messaging (English contact search + Tanglish/Tamil message)
        wa_contact, wa_msg = parse_whatsapp_voice_command(text)
        if wa_contact and wa_msg:
            self.ui.write_log(f"SYS: Dispatching local WhatsApp message to '{wa_contact}': '{wa_msg}'")
            try:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    None,
                    lambda: send_message(
                        parameters={"platform": "whatsapp", "receiver": wa_contact, "message_text": wa_msg},
                        response=None,
                        player=self.ui,
                        session_memory=None,
                    )
                )
                ack = f"WhatsApp-la {wa_contact}-ku '{wa_msg}' nu message anupitten, Sir!"
                self.ui.write_log(f"JARVIS: {ack}")
                self._speak_local(ack)
                return True
            except Exception as e:
                print(f"[Local Command] WhatsApp send error: {e}")

        # YouTube Next Song Command (Shift + N shortcut): "youtube play next", "youtube next song", "youtube la playnext song"
        if ("youtube" in t or "யூடியூப்" in t) and any(k in t for k in ("next", "play next", "playnext", "skip", "அடுத்த", "அடுத்த பாடல்", "அடுத்த பாட்டு")):
            self.ui.write_log("SYS: Dispatching YouTube Next Song (Shift + N)")
            try:
                loop = asyncio.get_event_loop()
                from actions.youtube_video import youtube_video
                res = await loop.run_in_executor(None, lambda: youtube_video({"action": "next"}, None, self.ui, None, self.speak))
                msg = res or "Skipped to next YouTube song (Shift + N), Sir."
                self.ui.write_log(f"JARVIS: {msg}")
                self._speak_local(msg)
                return True
            except Exception as e:
                print(f"[Local Command] YouTube next song error: {e}")

        # Auto-Answer / Quiz & Form filling voice command
        if any(k in t for k in ("auto answer", "answer quiz", "fill form", "form fill", "quiz answer", "பதில் சொல்லு", "குவிஸ்", "படிவம் நிரப்பு")):
            self.ui.write_log(f"SYS: Dispatching auto_answer for command: '{text}'")
            try:
                loop = asyncio.get_event_loop()
                from actions.auto_answer import auto_answer
                res = await loop.run_in_executor(None, lambda: auto_answer({"action": "auto_answer"}, self.ui, self.speak))
                msg = res or "Auto-answer workflow executed, Sir."
                self.ui.write_log(f"JARVIS: {msg}")
                self._speak_local(msg)
                return True
            except Exception as e:
                print(f"[Local Command] auto_answer error: {e}")

        # 4. Spotify and media controls
        if any(k in t for k in ("spotify", "music", "song", "track", "பாடல்", "பாட்டு", "இசை", "மியூசிக்")):
            try:
                loop = asyncio.get_event_loop()
                from actions.desktop import desktop_control
                if any(k in t for k in ("next", "skip", "அடுத்த பாடல்", "மாற்று")):
                    await loop.run_in_executor(None, lambda: desktop_control({"action": "media_next"}, self.ui))
                    msg = "Skipped to next track, Sir."
                elif any(k in t for k in ("previous", "back", "முந்தைய பாடல்")):
                    await loop.run_in_executor(None, lambda: desktop_control({"action": "media_prev"}, self.ui))
                    msg = "Playing previous track, Sir."
                elif any(k in t for k in ("pause", "stop", "நிறுத்து", "பாஸ்")):
                    await loop.run_in_executor(None, lambda: desktop_control({"action": "media_play_pause"}, self.ui))
                    msg = "Music paused, Sir."
                elif any(k in t for k in ("play", "resume", "போடு", "ப்ளே")):
                    match = re.search(r"(?:play|போடு)\s+(.+?)(?:\s+on\s+spotify|\s+பாடல்)?$", t)
                    if match and match.group(1).strip() not in ("music", "song", "பாட்டு", "பாடல்", ""):
                        query = match.group(1).strip()
                        from actions.open_app import open_app
                        await loop.run_in_executor(None, lambda: open_app({"app_name": "spotify"}, None, self.ui))
                        import webbrowser
                        webbrowser.open(f"https://open.spotify.com/search/{query}")
                        msg = f"Playing {query} on Spotify, Sir."
                    else:
                        await loop.run_in_executor(None, lambda: desktop_control({"action": "media_play_pause"}, self.ui))
                        msg = "Resuming music playback, Sir."
                else:
                    msg = "Media command executed, Sir."
                self.ui.write_log(f"JARVIS: {msg}")
                self._speak_local(msg)
                return True
            except Exception as e:
                print(f"[Local Command] spotify error: {e}")

        # 4. Volume and audio controls
        if any(k in t for k in ("volume", "mute", "unmute", "sound", "சத்தம்", "வால்யூம்")):
            act = "toggle_mute"
            val = None
            if ("mute" in t and "unmute" not in t) or "அமைதி" in t or "மியூட்" in t:
                act = "mute"
            elif "unmute" in t or "அன்மியூட்" in t:
                act = "unmute"
            elif any(k in t for k in ("up", "increase", "higher", "அதிகரி", "கூட்டு", "ஏத்து")):
                act = "volume_up"
            elif any(k in t for k in ("down", "decrease", "lower", "குறை", "இறக்கு")):
                act = "volume_down"
            elif "max" in t or "100" in t:
                act = "set_volume"
                val = 100
            elif "half" in t or "50" in t:
                act = "set_volume"
                val = 50

            try:
                loop = asyncio.get_event_loop()
                from actions.computer_settings import computer_settings
                params = {"action": act}
                if val is not None:
                    params["value"] = val
                res = await loop.run_in_executor(None, lambda: computer_settings(params, None, self.ui))
                msg = res or "Volume adjusted, Sir."
                self.ui.write_log(f"JARVIS: {msg}")
                self._speak_local(msg)
                return True
            except Exception as e:
                print(f"[Local Command] volume error: {e}")

        # 5. Time query
        if any(k in t for k in ("what time", "what's the time", "current time", "tell me the time", "time now", "what is the time", "time enna", "மணி என்ன", "நேரம் என்ன")):
            time_now = datetime.now().strftime("%I:%M %p")
            if bool(re.search(r"[\u0B80-\u0BFF]", t)):
                msg = f"இப்போது நேரம் {time_now} சார்."
            else:
                msg = f"It is currently {time_now}, Sir."
            self.ui.write_log(f"JARVIS: {msg}")
            self._speak_local(msg)
            return True

        # 6. Date query
        if any(k in t for k in ("what date", "today's date", "what is the date", "what's today's date", "which day is today", "date enna", "இன்று என்ன தேதி", "இன்னைக்கு என்ன தேதி")):
            date_now = datetime.now().strftime("%A, %B %d, %Y")
            if bool(re.search(r"[\u0B80-\u0BFF]", t)):
                msg = f"இன்று {date_now} சார்."
            else:
                msg = f"Today is {date_now}, Sir."
            self.ui.write_log(f"JARVIS: {msg}")
            self._speak_local(msg)
            return True

        # 7. System status
        if any(k in t for k in ("system status", "battery", "cpu", "ram", "performance", "pc status")):
            try:
                import psutil
                cpu = psutil.cpu_percent(interval=0.2)
                ram = psutil.virtual_memory().percent
                battery = psutil.sensors_battery()
                bat_str = f", Battery is at {battery.percent}%" if battery else ""
                msg = f"System telemetry: CPU is at {cpu:.0f}%, RAM usage is at {ram:.0f}%{bat_str}, Sir."
                self.ui.write_log(f"JARVIS: {msg}")
                self._speak_local(msg)
                return True
            except Exception as e:
                print(f"[Local Command] status error: {e}")

        # 8. Screenshot
        if "screenshot" in t or "screen capture" in t:
            try:
                loop = asyncio.get_event_loop()
                from actions.desktop import desktop_control
                res = await loop.run_in_executor(None, lambda: desktop_control({"action": "screenshot"}, self.ui))
                msg = res or "Screenshot captured, Sir."
                self.ui.write_log(f"JARVIS: {msg}")
                self._speak_local(msg)
                return True
            except Exception as e:
                print(f"[Local Command] screenshot error: {e}")

        # 9. Close window / app
        if t.startswith("close ") or t.startswith("quit ") or t.startswith("exit "):
            target = t.split(" ", 1)[1].strip()
            if target in ("jarvis", "yourself", "this app", "this window", "this application", "jarvis assistant"):
                self.ui.prompt_close()
                msg = "Are you sure you want to close JARVIS, Sir? Please confirm."
                self.ui.write_log(f"JARVIS: {msg}")
                self._speak_local(msg)
                return True
            try:
                loop = asyncio.get_event_loop()
                from actions.open_app import close_app
                res = await loop.run_in_executor(None, lambda: close_app({"app_name": target}, None, self.ui))
                msg = res or f"Closed {target}, Sir."
                self.ui.write_log(f"JARVIS: {msg}")
                self._speak_local(msg)
                return True
            except Exception as e:
                print(f"[Local Command] close error: {e}")

        return False

    def _on_text_command(self, text: str):
        if not self._loop:
            return

        async def _dispatch():
            raw = (text or "").strip()
            if not raw:
                return
            t = raw.lower().rstrip(".!?")

            # Check 1: If confirmation overlay is active, process user confirmation reply immediately
            if self.ui and self.ui.is_confirm_close_visible():
                affirmative = (
                    "yes", "y", "close", "close it", "close jarvis", "confirm", "ok", "okay",
                    "proceed", "shut down", "shutdown", "exit", "quit", "bye", "yes please",
                    "sure", "do it", "ஆம்", "சரி", "க்ளோஸ்", "க்ளோஸ் பண்ணு", "மூடு",
                    "ஆமா", "ஆமாம்", "sari", "aama", "aamam", "confirm close",
                )
                negative = (
                    "no", "n", "cancel", "stop", "abort", "stay", "back", "dont", "don't",
                    "dont close", "don't close", "never mind", "wait", "வேண்டாம்",
                    "இல்லை", "இல்ல", "vendam", "illai", "cancel close",
                )
                if any(t == a or t.startswith(a + " ") for a in affirmative):
                    self.ui.confirm_close()
                    return
                elif any(t == n or t.startswith(n + " ") for n in negative):
                    self.ui.cancel_close()
                    return

            # Check 2: Direct voice/text close command for JARVIS
            close_jarvis_terms = (
                "close", "close it", "close jarvis", "close app", "close application",
                "close window", "close this", "close this window", "shutdown", "shut down",
                "shutdown jarvis", "shut down jarvis", "exit", "exit jarvis", "quit", "quit jarvis",
                "bye", "bye jarvis", "goodbye", "goodbye jarvis", "i close", "close tell", "tell close",
                "மூடு", "க்ளோஸ்", "க்ளோஸ் பண்ணு", "ஜார்விஸ் மூடு", "ஜார்விஸ் க்ளோஸ் பண்ணு",
                "moodu", "close pannu", "jarvis moodu", "jarvis close",
            )
            is_close_cmd = (
                t in close_jarvis_terms
                or t.startswith("close jarvis")
                or t.startswith("shutdown jarvis")
                or t.startswith("exit jarvis")
                or bool(re.search(r"^(?:jarvis\s+)?(?:close|shut\s*down|exit|quit|க்ளோஸ்|மூடு)(?:\s+(?:jarvis|yourself|app|application|window|it|please|pannu|பண்ணு))?$", t))
                or t in ("i close", "close tell", "tell close")
            )
            if is_close_cmd:
                self.ui.prompt_close()
                msg = "Are you sure you want to close JARVIS, Sir? Please confirm."
                self.ui.write_log(f"JARVIS: {msg}")
                self._speak_local(msg)
                return

            # Check 3: Direct voice/text minimize command for JARVIS
            min_jarvis_terms = (
                "minimize", "minimize it", "minimize jarvis", "hide", "hide jarvis",
                "hide window", "minimize window", "minimize this", "மினிமைஸ்",
                "minimize pannu", "மினிமைஸ் பண்ணு",
            )
            is_min_cmd = (
                t in min_jarvis_terms
                or t.startswith("minimize jarvis")
                or bool(re.search(r"^(?:jarvis\s+)?(?:minimize|hide|மினிமைஸ்)(?:\s+(?:jarvis|yourself|app|application|window|it|please|pannu|பண்ணு))?$", t))
            )
            if is_min_cmd:
                self.ui.minimize()
                msg = "Minimizing window, Sir."
                self.ui.write_log(f"SYS: {msg}")
                self._speak_local(msg)
                return

            # Check 4: Fast-path local system & app commands (open app, close app, time, date, screenshot, spotify)
            handled = await self._execute_local_voice_command(raw)
            if handled:
                return

            # Forward to Gemini Live session if connected
            if self.session:
                try:
                    await self.session.send_client_content(
                        turns={"parts": [{"text": text}]},
                        turn_complete=True
                    )
                    return
                except Exception as e:
                    print(f"[JARVIS] Failed to send client content: {e}")

            # Wait briefly for session to connect
            for _ in range(15):
                if self.session:
                    break
                await asyncio.sleep(0.1)

            if self.session:
                try:
                    await self.session.send_client_content(
                        turns={"parts": [{"text": text}]},
                        turn_complete=True
                    )
                    return
                except Exception as e:
                    print(f"[JARVIS] Failed to send client content: {e}")

            # Fallback: Process conversational query via configured LLM (Groq / Ollama)!
            try:
                from core.llm_client import call_llm_stream, get_llm_provider
                provider = get_llm_provider()
                self.ui.write_log(f"SYS: Processing query via {provider.upper()} LLM...")
                llm_resp = ""
                for item in call_llm_stream(text, system_prompt="You are JARVIS, an autonomous assistant. Answer Sir concisely in 1-2 sentences in their language."):
                    if isinstance(item, dict):
                        if item.get("type") == "sentence":
                            llm_resp += item.get("text", "") + " "
                        elif item.get("type") == "done" and not llm_resp.strip():
                            llm_resp = item.get("content", "")
                    elif isinstance(item, str):
                        llm_resp += item
                if llm_resp.strip():
                    ans = llm_resp.strip()
                    self.ui.write_log(f"JARVIS: {ans}")
                    self._session_log.append(f"JARVIS: {ans}")
                    self._speak_local(ans)
                    return
            except Exception as llm_err:
                print(f"[LLM Fallback] Error: {llm_err}")

            self.ui.write_log("SYS: Gemini session connecting, please wait...")
            self._speak_local("Gemini session is connecting, Sir. Command queued.")

        asyncio.run_coroutine_threadsafe(_dispatch(), self._loop)

    def get_current_speaking_text(self) -> str:
        with self._speaking_lock:
            if not self._is_speaking:
                return ""
            return getattr(self, "_current_speech_text", "")

    def set_speaking(self, value: bool):
        with self._speaking_lock:
            was_speaking = self._is_speaking
            self._is_speaking = value
            if not value:
                self._current_speech_text = ""
        if value:
            self.ui.set_state("SPEAKING")
        elif not self.ui.muted:
            self.ui.set_state("LISTENING")
            if was_speaking and self._live_speech_listener:
                self._live_speech_listener.trigger_cooldown()

    def interrupt(self) -> None:
        """Stop JARVIS mid-speech: drain queued audio and open mic immediately."""
        self._interrupted = True
        self._current_speech_text = ""
        q = self.audio_in_queue
        if q:
            drained = 0
            while True:
                try:
                    q.get_nowait()
                    drained += 1
                except Exception:
                    break
            if drained:
                print(f"[JARVIS] ✋ Interrupted — {drained} audio chunks discarded")
        self.set_speaking(False)
        if self._turn_done_event:
            self._turn_done_event.clear()
        self.ui.write_log("SYS: Interrupted — listening...")

    def speak(self, text: str):
        if not self._loop or not self.session:
            return
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"parts": [{"text": text}]},
                turn_complete=True
            ),
            self._loop
        )

    def speak_error(self, tool_name: str, error: str):
        short = str(error)[:120]
        self.ui.write_log(f"ERR: {tool_name} — {short}")
        self.speak(f"Sir, {tool_name} encountered an error. {short}")

    def _build_config(self) -> types.LiveConnectConfig:
        from datetime import datetime

        # Load customization from config
        try:
            _cfg = json.loads(open(API_CONFIG_PATH, encoding="utf-8").read())
            self._asst_name = (_cfg.get("assistant_name") or "JARVIS").strip()
            _user_name = (_cfg.get("user_name") or "").strip()
            self._voice_enabled = bool(_cfg.get("voice_recognition_enabled", True))
            self._voice_security_mode = str(_cfg.get("voice_security_mode", "smart")).lower().strip()
            self._voice_threshold = float(_cfg.get("voice_match_threshold", 0.72))
            self._voice_continuous_listening = bool(_cfg.get("voice_continuous_listening", True))
            self._voice_pause_threshold = float(_cfg.get("voice_pause_threshold", 0.8))
            self._voice_phrase_time_limit = float(_cfg.get("voice_phrase_time_limit", 15.0))
            self._voice_acoustic_cooldown = float(_cfg.get("voice_acoustic_cooldown", 0.6))
            self._voice_barge_in = bool(_cfg.get("voice_barge_in", True))
            self._wake_word_enabled = bool(_cfg.get("wake_word_enabled", True))
            self._wake_timeout = float(_cfg.get("wake_timeout", 10.0))
            _raw_mic = _cfg.get("mic_device_index", None)
            if _raw_mic is None or str(_raw_mic).lower().strip() in ("auto", "none", "null", ""):
                self._mic_device = None
            else:
                try:
                    self._mic_device = int(_raw_mic)
                except (ValueError, TypeError):
                    self._mic_device = None
            self._stt_language = _cfg.get("stt_language", "bilingual")
        except Exception:
            self._asst_name = "JARVIS"
            _user_name = ""
            self._voice_enabled = False
            self._voice_security_mode = "smart"
            self._voice_threshold = 0.72
            self._voice_continuous_listening = True
            self._voice_pause_threshold = 1.0
            self._voice_phrase_time_limit = 15.0
            self._voice_acoustic_cooldown = 0.6
            self._voice_barge_in = True
            self._wake_word_enabled = True
            self._wake_timeout = 10.0
            self._wake_words = None
            self._mic_device = None
            self._stt_language = "bilingual"

        memory     = load_memory()
        mem_str    = format_memory_for_prompt(memory)
        sys_prompt = _load_system_prompt()

        now      = datetime.now()
        time_str = now.strftime("%A, %B %d, %Y — %I:%M %p")
        time_ctx = (
            f"[CURRENT DATE & TIME]\n"
            f"Right now it is: {time_str}\n"
            f"Use this to calculate exact times for reminders.\n\n"
        )

        # Identity injection — overrides any hardcoded name in prompt.txt
        _addr = (
            "CRITICAL ADDRESS PROTOCOL:\n"
            "- Always address the user strictly as 'Sir'.\n"
            "- In every answer, response, confirmation, and greeting, address the user as 'Sir' (e.g. 'Yes, Sir', 'Right away, Sir', 'At your service, Sir').\n"
            "- Never call the user 'ok jarvius', or any name other than 'Sir'.\n"
        )
        identity_ctx = (
            f"[IDENTITY]\n"
            f"Your name is {self._asst_name}. "
            f"Always refer to yourself as {self._asst_name}.\n"
            f"{_addr}\n"
        )

        parts = [time_ctx, identity_ctx]

        # Voice biometrics injection (if enabled and enrolled)
        if getattr(self, "_voice_enabled", False) and self.voice_manager and self.voice_manager.is_enrolled():
            enrolled_user = self.voice_manager.active_speaker or "Sir"
            parts.append(
                f"[VOICE BIOMETRIC IDENTIFICATION]\n"
                f"Your biometric speaker recognition is active for your creator/owner '{enrolled_user}'. "
                f"When you converse with the user, recognize them personally as {enrolled_user}.\n\n"
            )
        else:
            parts.append(
                f"[USER ADDRESS PROTOCOL]\n"
                f"The person speaking to you is 'Sir'. Always acknowledge them and reply to them as 'Sir'.\n\n"
            )

        if mem_str:
            parts.append(mem_str)
        parts.append(sys_prompt)

        cfg = dict(
            response_modalities=["AUDIO"],
            output_audio_transcription={},
            input_audio_transcription={},
            system_instruction="\n".join(parts),
            tools=[{"function_declarations": TOOL_DECLARATIONS + self._plugin_registry.get_tool_declarations()}],
            session_resumption=types.SessionResumptionConfig(),
            # Sliding-window compression: session never dies from a full context
            # window — JARVIS can stay in one conversation for hours
            context_window_compression=types.ContextWindowCompressionConfig(
                sliding_window=types.SlidingWindow(),
            ),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name="Charon"
                    )
                )
            ),
        )
        if self._enhanced_live:
            # Affective dialog: JARVIS hears tone/emotion and adapts its voice.
            cfg["enable_affective_dialog"] = True
            # Proactive audio: off by default so speech is never ignored; toggle via config if desired
            if _cfg.get("proactive_audio", False):
                cfg["proactivity"] = types.ProactivityConfig(proactive_audio=True)
        return types.LiveConnectConfig(**cfg)

    async def _execute_tool(self, fc) -> types.FunctionResponse:
        name = fc.name
        args = dict(fc.args or {})

        print(f"[JARVIS] 🔧 {name}  {args}")
        self.ui.set_state("THINKING")

        if name == "save_memory":
            category = args.get("category", "notes")
            key      = args.get("key", "")
            value    = args.get("value", "")
            if key and value:
                update_memory({category: {key: {"value": value}}})
                print(f"[Memory] 💾 save_memory: {category}/{key} = {value}")
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": "ok", "silent": True}
            )

        loop   = asyncio.get_event_loop()
        result = "Done."

        try:
            if name == "open_app":
                r = await loop.run_in_executor(None, lambda: open_app(parameters=args, response=None, player=self.ui))
                result = r or f"Opened {args.get('app_name')}."

            elif name == "close_app":
                target = (args.get("app_name") or "").lower().strip()
                if target in ("jarvis", "yourself", "this app", "this application", "jarvis assistant"):
                    self.ui.prompt_close()
                    result = "Confirmation requested to close JARVIS, Sir. Please confirm."
                else:
                    r = await loop.run_in_executor(None, lambda: close_app(parameters=args, response=None, player=self.ui))
                    result = r or f"Closed {args.get('app_name', 'application')}."

            elif name == "minimize_app":
                target = (args.get("app_name") or "").lower().strip()
                if target in ("jarvis", "yourself", "this app", "this application", "jarvis assistant", ""):
                    self.ui.minimize()
                    result = "Minimized JARVIS window, Sir."
                else:
                    r = await loop.run_in_executor(None, lambda: minimize_app(parameters=args, response=None, player=self.ui))
                    result = r or f"Minimized {args.get('app_name', 'application')}."

            elif name == "weather_report":
                r = await loop.run_in_executor(None, lambda: weather_action(parameters=args, player=self.ui))
                result = r or "Weather delivered."

            elif name == "browser_control":
                r = await loop.run_in_executor(None, lambda: browser_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "auto_answer":
                r = await loop.run_in_executor(None, lambda: auto_answer(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Auto-answer completed."

            elif name == "file_controller":
                r = await loop.run_in_executor(None, lambda: file_controller(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "send_message":
                r = await loop.run_in_executor(None, lambda: send_message(parameters=args, response=None, player=self.ui, session_memory=None))
                result = r or f"Message sent to {args.get('receiver')}."

            elif name == "reminder":
                r = await loop.run_in_executor(None, lambda: reminder(parameters=args, response=None, player=self.ui))
                result = r or "Reminder set."

            elif name == "youtube_video":
                r = await loop.run_in_executor(None, lambda: youtube_video(parameters=args, response=None, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "screen_process":
                import time as _t_mod
                _now = _t_mod.monotonic()
                _cooldown = 4.0  # seconds — covers echo window after speaking ends
                if self._vision_busy or (_now - self._vision_last_time) < _cooldown:
                    _wait = max(0, _cooldown - (_now - self._vision_last_time))
                    print(f"[Vision] ⏳ Cooldown active ({_wait:.1f}s remaining) — ignoring duplicate call")
                    result = "Vision is still processing the previous request. I will not call this again."
                else:
                    self._vision_busy      = True
                    self._vision_last_time = _now
                    angle     = args.get("angle", "screen").lower()
                    user_text = args.get("text", "What do you see?")
                    if angle == "camera":
                        img_b, mime_t = await loop.run_in_executor(None, _capture_camera)
                        self.ui.start_camera_stream()
                        self._vision_cam_active = True
                        print(f"[Vision] 📷 Camera: {len(img_b):,} bytes")
                        _stall = "camera"
                    else:
                        img_b, mime_t = await loop.run_in_executor(None, _capture_screen)
                        print(f"[Vision] 🖥️  Screen: {len(img_b):,} bytes")
                        _stall = "screen"
                    self._pending_vision = (img_b, mime_t, user_text, angle)
                    result = (
                        f"[VISION_ACTIVE] {_stall.capitalize()} captured. "
                        f"Immediately say ONE short natural sentence in the user's own language, "
                        f"telling them you are looking at their {_stall} right now. "
                        f"Do NOT describe or guess content — the actual image arrives in the NEXT message."
                    )

            elif name == "close_camera":
                self.ui.stop_camera_stream()
                result = "Camera closed."

            elif name == "computer_settings":
                r = await loop.run_in_executor(None, lambda: computer_settings(parameters=args, response=None, player=self.ui))
                result = r or "Done."

            elif name == "desktop_control":
                r = await loop.run_in_executor(None, lambda: desktop_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "code_helper":
                r = await loop.run_in_executor(None, lambda: code_helper(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "dev_agent":
                r = await loop.run_in_executor(None, lambda: dev_agent(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "web_search":
                r = await loop.run_in_executor(None, lambda: web_search_action(parameters=args, player=self.ui))
                result = r or "Done."
                # Mirror results to the on-screen content panel
                _mode = args.get("mode", "search")
                if r and not r.startswith("No results") and not r.startswith("Search failed"):
                    _query = args.get("query") or ", ".join(args.get("items", []))
                    _label = f"{_mode.upper()} — {_query[:38]}" if _query else _mode.upper()
                    self.ui.show_content(_label, r)
            elif name == "file_processor":
                if not args.get("file_path") and self.ui.current_file:
                    args["file_path"] = self.ui.current_file
                r = await loop.run_in_executor(
                    None,
                    lambda: file_processor(parameters=args, player=self.ui, speak=self.speak)
                )
                result = r or "Done."

            elif name == "computer_control":
                r = await loop.run_in_executor(None, lambda: computer_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "game_updater":
                r = await loop.run_in_executor(None, lambda: game_updater(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "flight_finder":
                r = await loop.run_in_executor(None, lambda: flight_finder(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "system_status":
                r = await loop.run_in_executor(None, get_system_status)
                result = str(r)

            elif name == "manage_monitor":
                action = args.get("action", "").lower().strip()
                topic  = args.get("topic", "").strip()
                if action == "add" and topic:
                    result = await asyncio.to_thread(add_monitor, topic)
                elif action == "remove" and topic:
                    result = await asyncio.to_thread(remove_monitor, topic)
                elif action == "list":
                    topics = await asyncio.to_thread(list_monitors)
                    result = ("Monitoring: " + ", ".join(topics)) if topics else "No topics are being monitored."
                else:
                    result = "Specify action (add/remove/list) and a topic."

            elif name == "minimize_jarvis":
                self.ui.write_log("SYS: Window minimized.")
                self.ui.minimize()
                result = "Jarvis window minimized to taskbar, Sir."

            elif name == "shutdown_jarvis":
                self.ui.write_log("SYS: Shutdown requested.")
                async def _do_shutdown():
                    await self._save_session_summary()
                    if self.session:
                        try:
                            await self.session.send_client_content(
                                turns={"parts": [{"text": "Say a brief natural goodbye to the user."}]},
                                turn_complete=True,
                            )
                        except Exception:
                            pass
                    await asyncio.sleep(1.5)
                    import os as _os
                    _os._exit(0)
                asyncio.create_task(_do_shutdown())
                result = "Shutting down, goodbye Sir."

            else:
                if self._plugin_registry.has(name):
                    r = await loop.run_in_executor(
                        None,
                        lambda: self._plugin_registry.run(name, args, player=self.ui, session_memory=None)
                    )
                    result = r or "Done."
                else:
                    result = f"Unknown tool: {name}"

        except Exception as e:
            result = f"Tool '{name}' failed: {e}"
            traceback.print_exc()
            self.speak_error(name, e)

        if not self.ui.muted:
            self.ui.set_state("LISTENING")

        print(f"[JARVIS] 📤 {name} → {str(result)[:80]}")
        return types.FunctionResponse(
            id=fc.id, name=name,
            response={"result": result}
        )

    async def _send_realtime(self):
        while True:
            msg = await self.out_queue.get()
            if isinstance(msg, bytes):
                msg = {"data": msg, "mime_type": "audio/pcm;rate=16000"}
            elif isinstance(msg, dict) and msg.get("mime_type") in ("audio/pcm", None):
                msg["mime_type"] = "audio/pcm;rate=16000"
            await self.session.send_realtime_input(media=msg)

    async def _listen_audio(self):
        print("[JARVIS] 🎤 Voice-to-Text STT engine starting...")
        from core.stt import LiveSpeechListener

        def is_speaking():
            with self._speaking_lock:
                return self._is_speaking

        def is_muted():
            return bool(self.ui.muted or self._phone_active)

        def on_stt_command(text: str, speaker_info: dict):
            speaker_name = speaker_info.get("speaker")
            confidence = speaker_info.get("confidence", 0.0)

            if not getattr(self, "_voice_enabled", False) or not speaker_name:
                speaker_name = "Sir"

            # Strict mode check: ignore guest commands if strict security is enabled
            if getattr(self, "_voice_security_mode", "smart") == "strict" and speaker_name == "Guest":
                self.ui.write_log("SYS: Guest voice command blocked (strict mode active).")
                return

            full_transcript = (speaker_info.get("full_text") or text).strip()
            speaker_lbl = "Sir"
            self.ui.write_log(f"{speaker_lbl}: {full_transcript}")
            self._session_log.append(f"{speaker_lbl}: {full_transcript}")
            self.ui.set_input_text(full_transcript)

            if self._dashboard and self._loop:
                try:
                    asyncio.run_coroutine_threadsafe(
                        self._dashboard.broadcast({
                            "type": "log", "speaker": "user",
                            "text": full_transcript,
                            "ts": datetime.now().isoformat(),
                        }),
                        self._loop
                    )
                except Exception:
                    pass

            # Automatically dispatch as text command to execute through JARVIS
            print(f"[JARVIS] ⚡ Auto-dispatching voice command: '{text}'")
            self._on_text_command(text)

        listener = LiveSpeechListener(
            device_index=self._mic_device,
            language=getattr(self, "_stt_language", "bilingual"),
            pause_threshold=getattr(self, "_voice_pause_threshold", 0.45),
            phrase_time_limit=getattr(self, "_voice_phrase_time_limit", 10.0),
            acoustic_cooldown=getattr(self, "_voice_acoustic_cooldown", 0.3),
            continuous_mode=getattr(self, "_voice_continuous_listening", False),
            voice_barge_in=getattr(self, "_voice_barge_in", True),
            wake_word_enabled=getattr(self, "_wake_word_enabled", True),
            wake_timeout=getattr(self, "_wake_timeout", 10.0),
            wake_words=getattr(self, "_wake_words", None),
            on_wake=self._on_wake_detected,
            on_command=on_stt_command,
            on_interrupt=self.interrupt,
            get_speaking_text_fn=self.get_current_speaking_text,
            is_speaking_fn=is_speaking,
            is_muted_fn=is_muted,
            voice_manager=self.voice_manager if getattr(self, "_voice_enabled", True) else None,
            log_fn=self.ui.write_log,
        )
        self._live_speech_listener = listener

        listener.start()
        try:
            while True:
                await asyncio.sleep(0.5)
        finally:
            listener.stop()
            self._live_speech_listener = None

    async def _receive_audio(self):
        print("[JARVIS] 👂 Recv started")
        out_buf, in_buf = [], []

        try:
            while True:
                async for response in self.session.receive():

                    if response.data:
                        if self._interrupted:
                            pass  # discard: interrupted
                        else:
                            if self._turn_done_event and self._turn_done_event.is_set():
                                self._turn_done_event.clear()
                            # Split into ~50 ms chunks so interrupt() stops audio within 50 ms
                            # (24000 Hz × 2 bytes/sample × 0.05 s = 2400 bytes per slice)
                            _audio_data = response.data
                            _SLICE = 2400
                            for _i in range(0, len(_audio_data), _SLICE):
                                self.audio_in_queue.put_nowait(_audio_data[_i : _i + _SLICE])

                    if response.server_content:
                        sc = response.server_content

                        if sc.output_transcription and sc.output_transcription.text:
                            txt = _clean_transcript(sc.output_transcription.text)
                            if txt and txt != (out_buf[-1] if out_buf else ""):
                                out_buf.append(txt)
                                self._current_speech_text = " ".join(out_buf)
                        elif sc.model_turn:
                            for part in sc.model_turn.parts:
                                if getattr(part, "text", None) and not getattr(part, "thought", False):
                                    txt = _clean_transcript(part.text)
                                    if txt and txt != (out_buf[-1] if out_buf else ""):
                                        out_buf.append(txt)
                                        self._current_speech_text = " ".join(out_buf)

                        if sc.input_transcription and sc.input_transcription.text:
                            txt = _clean_transcript(sc.input_transcription.text)
                            if txt:
                                in_buf.append(txt)
                                self._last_user_speech = time.monotonic()

                        if sc.turn_complete:
                            if self._turn_done_event:
                                self._turn_done_event.set()

                            # If this turn_complete ends an interrupted response, clear the
                            # flag and skip all further processing for that turn.
                            if self._interrupted:
                                self._interrupted = False
                                in_buf  = []
                                out_buf = []
                                continue

                            full_in = " ".join(in_buf).strip()
                            if full_in:
                                speaker_lbl = "Sir"
                                self.ui.write_log(f"{speaker_lbl}: {full_in}")
                                self._session_log.append(f"{speaker_lbl}: {full_in}")
                                self._verified_speaker = None
                                if self._dashboard:
                                    asyncio.create_task(self._dashboard.broadcast({
                                        "type": "log", "speaker": "user",
                                        "text": full_in,
                                        "ts": datetime.now().isoformat(),
                                    }))
                            in_buf = []

                            full_out = " ".join(out_buf).strip()
                            if full_out:
                                self.ui.write_log(f"{self._asst_name}: {full_out}")
                                self._session_log.append(f"{self._asst_name}: {full_out}")
                                if self._dashboard:
                                    asyncio.create_task(self._dashboard.broadcast({
                                        "type": "log", "speaker": "jarvis",
                                        "text": full_out,
                                        "ts": datetime.now().isoformat(),
                                    }))
                            out_buf = []

                            # Vision injection: model finished tool-response turn → now send the image
                            if self._pending_vision and self.session:
                                import base64 as _b64
                                img_b, mime_t, question, angle = self._pending_vision
                                self._pending_vision = None
                                b64 = _b64.b64encode(img_b).decode("ascii")
                                print(f"[Vision] 📤 {len(img_b):,} bytes (angle={angle}) → main session")
                                await self.session.send_client_content(
                                    turns={"parts": [
                                        {"inline_data": {"mime_type": mime_t, "data": b64}},
                                        {"text": question},
                                    ]},
                                    turn_complete=True,
                                )
                                # Mark next turn_complete behaviour depending on angle
                                if self._vision_cam_active:
                                    # Camera: keep busy until JARVIS finishes speaking the answer
                                    self._vision_cam_active    = False
                                    self._vision_close_pending = True
                                else:
                                    # Screen-only: no camera to close; release busy flag now
                                    self._vision_busy = False
                            elif self._vision_close_pending:
                                # This turn_complete IS the vision answer — close camera + release busy flag
                                self._vision_close_pending = False
                                self._vision_busy = False
                                async def _cam_close():
                                    await asyncio.sleep(2.0)
                                    self.ui.stop_camera_stream()
                                asyncio.create_task(_cam_close())

                    if response.tool_call:
                        fn_responses = []
                        for fc in response.tool_call.function_calls:
                            print(f"[JARVIS] 📞 {fc.name}")
                            fr = await self._execute_tool(fc)
                            fn_responses.append(fr)
                        await self.session.send_tool_response(
                            function_responses=fn_responses
                        )
        except Exception as e:
            print(f"[JARVIS] ❌ Recv: {e}")
            traceback.print_exc()
            raise

    async def _play_audio(self):
        print("[JARVIS] 🔊 Play started")
        from core.tts import get_preferred_output_device
        out_dev = get_preferred_output_device()

        stream = sd.RawOutputStream(
            samplerate=RECEIVE_SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
            blocksize=CHUNK_SIZE,
            device=out_dev,
        )
        stream.start()

        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(
                        self.audio_in_queue.get(),
                        timeout=0.1
                    )
                except asyncio.TimeoutError:
                    if (
                        self._turn_done_event
                        and self._turn_done_event.is_set()
                        and self.audio_in_queue.empty()
                    ):
                        self.set_speaking(False)
                        self._turn_done_event.clear()
                    continue

                self.set_speaking(True)

                # Batch all immediately-available chunks into one write to reduce
                # thread-pool round-trips (was one asyncio.to_thread per 50ms slice).
                # Cap at ~200 ms so interrupt() still stops audio within ~200 ms.
                batch = bytearray(chunk)
                while len(batch) < 9600:   # 9600 bytes ≈ 200 ms at 24 kHz / 16-bit mono
                    try:
                        batch.extend(self.audio_in_queue.get_nowait())
                    except asyncio.QueueEmpty:
                        break

                try:
                    await asyncio.to_thread(stream.write, bytes(batch))
                except (RuntimeError, asyncio.CancelledError):
                    break   # executor shutting down — exit cleanly
        except Exception as e:
            print(f"[JARVIS] ❌ Play: {e}")
            raise
        finally:
            self.set_speaking(False)
            stream.stop()
            stream.close()

    # ── Morning briefing ────────────────────────────────────────────────────────

    async def _send_startup_briefing(self) -> None:
        """
        Two-phase briefing optimized for speed:
          Phase 1 — instant greeting (no tools) → speech starts in <1s
          Phase 2 — news pre-fetched in a background thread while Phase 1 plays,
                    delivered as ready text (no Gemini tool-call round-trip) and
                    shown on the UI content panel. Waits for turn_complete event
                    instead of a fixed sleep so there is no unnecessary gap.
        """
        memory   = load_memory()
        identity = memory.get("identity", {})

        def _val(k: str) -> str:
            e = identity.get(k, {})
            return (e.get("value", "") if isinstance(e, dict) else str(e)).strip()

        lang = _val("language")
        name = _val("name")
        time_str = datetime.now().strftime("%H:%M")

        # Start fetching news immediately — runs in parallel while phase 1 plays
        loop = asyncio.get_event_loop()
        news_future = loop.run_in_executor(None, _fetch_news_sync, "top world news today")

        await asyncio.sleep(0.3)
        if not self.session:
            return

        # ── Phase 1: instant greeting ─────────────────────────────────────────
        lang_clause = f" Respond in {lang}." if lang else ""
        name_clause = " Always address the user strictly as 'Sir' (or 'சார்' in Tamil). Never use any other name."

        # Inject last session context if available — pop removes it so it's never repeated
        last = await asyncio.to_thread(pop_last_session)
        session_clause = ""
        if last:
            try:
                _delta = (datetime.now() - datetime.strptime(last["date"], "%Y-%m-%d")).days
                _when  = "earlier today" if _delta == 0 else ("yesterday" if _delta == 1 else f"{_delta} days ago")
            except Exception:
                _when = "last time"
            session_clause = (
                f" Also briefly and naturally mention that {_when}: {last['summary']}"
            )

        p1 = (
            f"Greet Sir warmly (e.g. 'Good morning, Sir' or 'வணக்கம் சார்'), mention it is {time_str}, "
            f"and say you are fetching today's news now.{session_clause} "
            f"Keep it to 2 short sentences max. Always start by addressing the user as 'Sir'. Never call the user anything else. Do not call any tools.{lang_clause}{name_clause}"
        )

        # Clear the turn-done event so we can wait for Phase 1 to finish
        if self._turn_done_event:
            self._turn_done_event.clear()

        await self.session.send_client_content(
            turns={"parts": [{"text": p1}]},
            turn_complete=True,
        )
        self.ui.write_log("SYS: Briefing phase 1 (greeting) sent.")

        # ── Phase 2: fire as soon as Phase 1 audio is done ───────────────────
        async def _deliver_news():
            try:
                lang_str = f" Respond in {lang}." if lang else ""

                # Wait for news fetch (already running) and Phase 1 turn-complete
                # in parallel — whichever takes longer determines the wait time
                news_done   = asyncio.wrap_future(news_future)
                turn_waited = False
                if self._turn_done_event:
                    try:
                        await asyncio.wait_for(self._turn_done_event.wait(), timeout=6.0)
                        turn_waited = True
                    except asyncio.TimeoutError:
                        pass

                # Extra buffer: turn_complete fires when Gemini finishes *generating*
                # Phase 1, but audio may still be playing.  Waiting a beat here
                # prevents Phase 2 audio from arriving while Phase 1 is mid-sentence
                # (which sounds like a "repeated first response" to the user).
                if turn_waited:
                    await asyncio.sleep(0.8)
                else:
                    await asyncio.sleep(1.0)

                try:
                    news_text = await asyncio.wait_for(news_done, timeout=8.0)
                except Exception as e:
                    self.ui.write_log(f"SYS: News fetch timed out/failed: {e!r}")
                    news_text = ""

                if not self.session:
                    return

                failed = (not news_text) or news_text.startswith(
                    ("No news found", "Search failed", "Please provide")
                )
                if not failed:
                    # Show on UI content panel immediately
                    self.ui.show_content("NEWS — top world news today", news_text)

                    p2 = (
                        f"[BRIEFING] Here are today's top news headlines:\n{news_text}\n\n"
                        "Always address the user strictly as 'Sir'. "
                        "Pick ONE headline, summarise it in one sentence, then say the full list "
                        f"is displayed on screen, Sir. Do not call any tools.{lang_str}"
                    )
                else:
                    self.ui.write_log(
                        f"SYS: News unavailable — backend returned: {news_text[:120]!r}"
                    )
                    p2 = (
                        "News headlines could not be fetched right now. "
                        "Always address the user strictly as 'Sir'. "
                        f"Let Sir know briefly.{lang_str}"
                    )

                await self.session.send_client_content(
                    turns={"parts": [{"text": p2}]},
                    turn_complete=True,
                )
                self.ui.write_log("SYS: Briefing phase 2 (news) sent.")
            except Exception as e:
                print(f"[Briefing] Phase 2 error: {e}")
                self.ui.write_log(f"SYS: Briefing phase 2 failed: {e}")

        asyncio.create_task(_deliver_news())

    # ── Session memory ──────────────────────────────────────────────────────────

    async def _save_session_summary(self) -> None:
        """Summarise the current session in 1-2 sentences and save to long_term.json."""
        log = self._session_log
        if len(log) < 3:          # need at least one exchange to be worth saving
            return
        self._session_log = []    # reset immediately so the next session starts clean

        memory = load_memory()
        lang_entry = memory.get("identity", {}).get("language", {})
        lang = (lang_entry.get("value", "") if isinstance(lang_entry, dict) else str(lang_entry)).strip()
        lang = lang or "English"

        convo = "\n".join(log[-40:])   # cap at last 40 turns to stay within token budget
        prompt = (
            f"Summarize this conversation in 1-2 sentences in {lang}. "
            "Focus on what the user accomplished or discussed. "
            "Output ONLY the summary text, nothing else:\n\n" + convo
        )
        try:
            from google import genai as _genai
            client = _genai.Client(api_key=_get_api_key())
            resp   = await asyncio.to_thread(
                client.models.generate_content,
                model="gemini-flash-latest",
                contents=prompt,
            )
            summary = (resp.text or "").strip()
            if summary:
                save_session_summary(summary, lang)
        except Exception as e:
            print(f"[Memory] ⚠️ Session summary failed: {e}")

    # ── System monitor ──────────────────────────────────────────────────────────

    async def _run_system_monitor(self) -> None:
        """Background task: voice alerts when metrics exceed thresholds."""
        while True:
            await asyncio.sleep(10)
            alert = await asyncio.to_thread(self._sys_monitor.check)
            if not alert or not self.session:
                continue
            # Don't interrupt an active conversation
            with self._speaking_lock:
                speaking = self._is_speaking
            if speaking or (time.monotonic() - self._last_user_speech) < 10:
                continue
            try:
                await self.session.send_client_content(
                    turns={"parts": [{"text": alert}]},
                    turn_complete=True,
                )
            except Exception as e:
                print(f"[Monitor] ⚠️ Could not send alert: {e}")

    # ── Background monitor ──────────────────────────────────────────────────────

    async def _run_background_monitor(self) -> None:
        """Check user-configured topics once per day; speak alerts when new headlines appear."""
        await asyncio.sleep(300)          # wait 5 min after startup before first check
        while True:
            if self.session:
                # Don't interrupt if user spoke recently or JARVIS is mid-sentence
                with self._speaking_lock:
                    speaking = self._is_speaking
                recent_speech = (time.monotonic() - self._last_user_speech) < 30
                if not speaking and not recent_speech:
                    try:
                        alerts = await asyncio.to_thread(monitor_check_all)
                        memory = load_memory()
                        lang_e = memory.get("identity", {}).get("language", {})
                        lang   = (lang_e.get("value", "") if isinstance(lang_e, dict) else str(lang_e)).strip() or "English"
                        for alert in alerts:
                            msg = (
                                f"{alert}\n\n"
                                f"Inform the user about this development naturally in {lang}. "
                                "One brief sentence only."
                            )
                            await self.session.send_client_content(
                                turns={"parts": [{"text": msg}]},
                                turn_complete=True,
                            )
                            self.ui.write_log(f"SYS: Monitor alert sent.")
                            await asyncio.sleep(6)   # gap between consecutive alerts
                    except Exception as e:
                        print(f"[Monitor] ⚠️ Background check error: {e}")
            await asyncio.sleep(1800)     # check every 30 minutes

    # ── Proactive mode ──────────────────────────────────────────────────────────

    async def _run_proactive_mode(self) -> None:
        """
        Background task: periodically checks if the user has been silent long enough,
        then hands time + memory context to Gemini so it can decide what (if anything)
        to say proactively. No hardcoded rules — Gemini makes the call.
        """
        while True:
            await asyncio.sleep(60)   # evaluate once per minute

            if not self.session:
                continue

            with self._speaking_lock:
                speaking = self._is_speaking
            if speaking:
                continue

            if not self._proactive.should_trigger(self._last_user_speech):
                continue

            self._proactive.mark_triggered()

            try:
                memory       = await asyncio.to_thread(load_memory)
                monitors     = await asyncio.to_thread(list_monitors)
                recent_turns = self._session_log[-8:] if self._session_log else []
                prompt = self._proactive.build_prompt(
                    memory       = memory,
                    monitors     = monitors or None,
                    recent_turns = recent_turns or None,
                )
                await self.session.send_client_content(
                    turns={"parts": [{"text": prompt}]},
                    turn_complete=True,
                )
                self.ui.write_log("SYS: Proactive check-in.")
            except Exception as e:
                print(f"[Proactive] ⚠️ {e}")

    # ── Phone audio relay ────────────────────────────────────────────────────────

    async def _relay_phone_audio(self) -> None:
        """Forward phone mic PCM chunks from dashboard queue into the Gemini Live session."""
        q = self._dashboard._phone_audio_queue
        while True:
            try:
                chunk = await asyncio.wait_for(q.get(), timeout=1.0)
            except asyncio.TimeoutError:
                # No audio for 1 s → phone mic inactive, give PC mic back
                self._phone_active = False
                continue
            self._phone_active = True   # phone is streaming — silence PC mic
            with self._speaking_lock:
                speaking = self._is_speaking
            if not speaking and not self.ui.muted:
                try:
                    self.out_queue.put_nowait(chunk)
                except asyncio.QueueFull:
                    pass

    def _on_phone_connected(self) -> None:
        self.ui.write_log("SYS: Phone connected via Remote Dashboard.")
        self.ui.notify_phone_connected()

    # ── dashboard command relay ─────────────────────────────────────────────

    async def _process_dashboard_commands(self) -> None:
        while True:
            try:
                text = await asyncio.wait_for(
                    self._dashboard._command_queue.get(), timeout=0.5
                )
                if not text:
                    continue
                # Wait up to 8s for session to become ready after a wake
                for _ in range(80):
                    if self.session:
                        break
                    await asyncio.sleep(0.1)
                if self.session:
                    await self.session.send_client_content(
                        turns={"parts": [{"text": text}]},
                        turn_complete=True,
                    )
                    self.ui.write_log(f"[Web]: {text}")
                else:
                    print(f"[Dashboard] Dropped command (no session): {text}")
            except asyncio.TimeoutError:
                pass
            except Exception as e:
                print(f"[Dashboard] Command error: {e}")
                await asyncio.sleep(0.5)

    # ── main loop ───────────────────────────────────────────────────────────

    async def run(self):
        self._loop = asyncio.get_event_loop()

        # Start dashboard (optional — needs: pip install fastapi "uvicorn[standard]" cryptography)
        try:
            from dashboard.server import DashboardServer
            self._dashboard = DashboardServer()
            self._dashboard.set_connect_callback(self._on_phone_connected)
            self._dashboard.set_wake_callback(lambda: self._on_wake_detected("remote_wake", False))
            asyncio.create_task(self._dashboard.serve())
            # Runs for the whole lifetime, not just inside an active session
            asyncio.create_task(self._process_dashboard_commands())
        except Exception as e:
            print(f"[Dashboard] Disabled: {e}")
            self._dashboard = None

        # Persistent STT listener: runs for the entire lifetime of JARVIS.
        # This guarantees the microphone (Bluetooth or system) is ALWAYS active and
        # never drops voice commands when Gemini Live is reconnecting or offline.
        asyncio.create_task(self._listen_audio())

        while True:
            current_model = LIVE_MODELS[self._model_index % len(LIVE_MODELS)]
            try:
                print(f"[JARVIS] Connecting ({current_model})...")
                self.ui.set_state("THINKING")
                config = self._build_config()

                # Fresh client on every reconnect — avoids stale HTTP session state
                # v1alpha carries the enhanced audio features (affective dialog,
                # proactive audio); if they get rejected we fall back to v1beta.
                client = genai.Client(
                    api_key=_get_api_key(),
                    http_options={"api_version": "v1alpha" if self._enhanced_live else "v1beta"}
                )

                async with (
                    client.aio.live.connect(model=current_model, config=config) as session,
                    asyncio.TaskGroup() as tg,
                ):
                    self.session          = session
                    self.audio_in_queue   = asyncio.Queue()
                    self.out_queue        = asyncio.Queue(maxsize=200)
                    self._turn_done_event = asyncio.Event()

                    # Reset transient state that must not carry over from a previous session
                    self._pending_vision       = None
                    self._vision_cam_active    = False
                    self._vision_close_pending = False
                    self._vision_busy          = False
                    self._vision_last_time     = 0.0
                    self._interrupted          = False

                    print(f"[JARVIS] Connected to {current_model}.")
                    self.ui.set_state("LISTENING")
                    self.ui.write_log(f"SYS: JARVIS online ({current_model.split('/')[-1]}).")

                    # Voice biometrics status indicator
                    if getattr(self, "_voice_enabled", False) and self.voice_manager and self.voice_manager.is_enrolled():
                        active_spk = self.voice_manager.active_speaker or "Sir"
                        self.ui.set_voice_status("IDLE", active_spk)
                        self.ui.write_log(f"SYS: Voice biometrics active for {active_spk}.")
                    else:
                        self.ui.set_voice_status("SIR", "Sir", 1.0)
                        self.ui.write_log("SYS: Continuous voice ready. Serving Sir.")

                    if self._dashboard:
                        await self._dashboard.broadcast({"type": "status", "state": "active"})

                    tg.create_task(self._send_realtime())
                    tg.create_task(self._receive_audio())
                    tg.create_task(self._play_audio())
                    tg.create_task(self._run_system_monitor())
                    tg.create_task(self._run_background_monitor())
                    tg.create_task(self._run_proactive_mode())
                    if self._dashboard:
                        tg.create_task(self._relay_phone_audio())

                    # Morning briefing — fires once per process launch (if enabled)
                    if not self._briefing_sent and get_brief_enabled():
                        self._briefing_sent = True
                        tg.create_task(self._send_startup_briefing())

            except KeyboardInterrupt:
                raise
            except SystemExit:
                raise
            except BaseException as e:
                # Catches both Exception and BaseExceptionGroup (Python 3.11+
                # TaskGroup raises BaseExceptionGroup when tasks are cancelled
                # externally, which `except Exception` would miss, letting the
                # exception escape the while-loop and causing asyncio.run() to
                # start shutdown — resulting in "executor after shutdown" errors).
                err_str = str(e)
                print(f"[JARVIS] Error ({type(e).__name__}): {e}")
                traceback.print_exc()

                # Model quota, deprecation, or not found error — switch to next fallback model
                if any(k in err_str.lower() for k in ("1011", "quota", "not_found", "404", "no longer available", "not available", "deprecated")):
                    prev_model = LIVE_MODELS[self._model_index % len(LIVE_MODELS)]
                    self._model_index += 1
                    next_model = LIVE_MODELS[self._model_index % len(LIVE_MODELS)]
                    self.ui.write_log(f"SYS: {prev_model.split('/')[-1]} quota/unavailable — switching to {next_model.split('/')[-1]}.")
                    await asyncio.sleep(1)
                    continue

                # Enhanced audio features rejected by the server (preview API
                # drift) — drop them and reconnect with the plain config.
                if self._enhanced_live and (
                    "INVALID_ARGUMENT" in err_str
                    or "affective" in err_str.lower()
                    or "proactiv" in err_str.lower()
                    or "Unknown name" in err_str
                    or "unexpected keyword" in err_str
                ):
                    self._enhanced_live = False
                    self.ui.write_log(
                        "SYS: Advanced audio features unavailable — reconnecting without them."
                    )
                    continue

                # Invalid API key — stop hammering the API, prompt re-configuration
                if "API key not valid" in err_str:
                    self.ui.write_log("ERR: API key invalid — please re-enter your key.")
                    self.ui.set_state("SLEEPING")
                    self.ui.prompt_reconfig()
                    while not self.ui._win._ready:
                        await asyncio.sleep(1)
                    print("[JARVIS] New API key saved — reconnecting...")
                    _conn_backoff = 3
                    continue

                # Network / timeout errors — log clearly and back off
                is_net_err = any(k in err_str for k in (
                    "TimeoutError", "timed out", "getaddrinfo", "CancelledError",
                    "ConnectionRefusedError", "OSError", "Cannot connect",
                ))
                if is_net_err:
                    _conn_backoff = min(getattr(self, "_conn_backoff", 3) * 2, 60)
                    self._conn_backoff = _conn_backoff
                    self.ui.write_log(
                        f"NET: Bağlantı kurulamadı — {_conn_backoff}s sonra tekrar deneniyor. "
                        "(VPN gerekiyor olabilir)"
                    )
                else:
                    self._conn_backoff = 3
                    self.ui.write_log(f"ERR: {type(e).__name__} — {err_str[:90]}")
            finally:
                self.session = None
                # Only save if there was a real conversation (≥3 turns)
                if len(self._session_log) >= 3:
                    asyncio.create_task(self._save_session_summary())

            self.set_speaking(False)
            self.ui.set_state("SLEEPING")

            if self._dashboard:
                await self._dashboard.broadcast({"type": "status", "state": "sleeping"})

            delay = getattr(self, "_conn_backoff", 3)
            print(f"[JARVIS] Reconnecting in {delay}s...")
            await asyncio.sleep(delay)

def main():
    ui = JarvisUI("face.png")

    def runner():
        ui.wait_for_api_key()
        jarvis = JarvisLive(ui)
        try:
            asyncio.run(jarvis.run())
        except (KeyboardInterrupt, SystemExit):
            print("\n🔴 Shutting down...")
        finally:
            os._exit(0)

    threading.Thread(target=runner, daemon=True).start()
    try:
        ui.root.mainloop()
    finally:
        os._exit(0)

if __name__ == "__main__":
    main()