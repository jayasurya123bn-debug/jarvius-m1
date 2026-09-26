import json
import re
import subprocess
import sys
import time
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

try:
    import pyautogui
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE    = 0.06
    _PYAUTOGUI = True
except ImportError:
    _PYAUTOGUI = False

try:
    import pyperclip
    _PYPERCLIP = True
except ImportError:
    _PYPERCLIP = False

def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

def _get_os() -> str:
    try:
        cfg = json.loads(
            (_base_dir() / "config" / "api_keys.json").read_text(encoding="utf-8")
        )
        return cfg.get("os_system", "windows").lower()
    except Exception:
        return "windows"


def _require_pyautogui():
    if not _PYAUTOGUI:
        raise RuntimeError("PyAutoGUI not installed. Run: pip install pyautogui")


def _paste_text(text: str) -> None:
    _require_pyautogui()

    os_name = _get_os()
    paste_hotkey = ("command", "v") if os_name == "mac" else ("ctrl", "v")

    if _PYPERCLIP:
        pyperclip.copy(text)
        time.sleep(0.15)
        pyautogui.hotkey(*paste_hotkey)
        time.sleep(0.1)
    else:
        pyautogui.write(text, interval=0.03)


def _clear_and_paste(text: str) -> None:
    _require_pyautogui()
    os_name = _get_os()
    select_all = ("command", "a") if os_name == "mac" else ("ctrl", "a")
    pyautogui.hotkey(*select_all)
    time.sleep(0.1)
    pyautogui.press("delete")
    time.sleep(0.1)
    _paste_text(text)

def _open_app(app_name: str) -> bool:
    _require_pyautogui()
    os_name = _get_os()

    try:
        if os_name == "windows":
            pyautogui.press("win")
            time.sleep(0.5)
            _paste_text(app_name)
            time.sleep(0.6)
            pyautogui.press("enter")
            time.sleep(2.5)
            return True

        elif os_name == "mac":
            result = subprocess.run(
                ["open", "-a", app_name],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                result = subprocess.run(
                    ["open", "-a", f"{app_name}.app"],
                    capture_output=True, text=True, timeout=10,
                )
            time.sleep(2.5)
            return result.returncode == 0

        else: 
            launched = False
            for launcher in [
                ["gtk-launch", app_name.lower()],
                [app_name.lower()],
            ]:
                try:
                    subprocess.Popen(
                        launcher,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    launched = True
                    break
                except FileNotFoundError:
                    continue
            time.sleep(2.5)
            return launched

    except Exception as e:
        print(f"[SendMessage] ⚠️ Could not open {app_name}: {e}")
        return False


def _open_browser_url(url: str) -> bool:
    import webbrowser
    try:
        webbrowser.open(url)
        time.sleep(4.0) 
        return True
    except Exception as e:
        print(f"[SendMessage] ⚠️ Could not open browser: {e}")
        return False


# ── Tamil & Tanglish Contact Name Sanitization ────────────────────────────────
TAMIL_COMMON_NAMES = {
    "அம்மா": "Amma",
    "அப்பா": "Appa",
    "அண்ணா": "Anna",
    "அக்கா": "Akka",
    "தம்பி": "Thambi",
    "தங்கச்சி": "Thangachi",
    "அருண்": "Arun",
    "கார்த்திக்": "Karthik",
    "ராகுல்": "Rahul",
    "சுரேஷ்": "Suresh",
    "ரமேஷ்": "Ramesh",
    "விஜய்": "Vijay",
    "அஜித்": "Ajith",
    "பிரவீன்": "Praveen",
    "தினேஷ்": "Dinesh",
    "பிரசாத்": "Prasad",
    "மோகன்": "Mohan",
    "சரவணன்": "Saravanan",
    "மணி": "Mani",
    "சிவா": "Siva",
    "பாலா": "Bala",
    "குமார்": "Kumar",
    "சந்தோஷ்": "Santhosh",
    "ஹரி": "Hari",
    "கணேஷ்": "Ganesh",
}

TAMIL_DATIVE_STEMS = [
    ("ணுக்கு", "ண்"),
    ("லுக்கு", "ல்"),
    ("ருக்கு", "ர்"),
    ("ஷுக்கு", "ஷ்"),
    ("க்குக்கு", "க்"),
    ("வுக்கு", ""),
    ("உக்கு", ""),
    ("க்கு", ""),
    ("ணு", "ண்"),
    ("லு", "ல்"),
    ("ரு", "ர்"),
    ("ஷு", "ஷ்"),
]

VOWELS = {
    'அ': 'A', 'ஆ': 'Aa', 'இ': 'I', 'ஈ': 'Ee', 'உ': 'U', 'ஊ': 'Oo',
    'எ': 'E', 'ஏ': 'Ae', 'ஐ': 'Ai', 'ஒ': 'O', 'ஓ': 'O', 'ஔ': 'Au'
}

CONSONANTS = {
    'க': 'k', 'ங': 'ng', 'ச': 's', 'ஞ': 'nj', 'ட': 'd', 'ண': 'n',
    'த': 'th', 'ந': 'n', 'ப': 'p', 'ம': 'm', 'ய': 'y', 'ர': 'r',
    'ல': 'l', 'வ': 'v', 'ழ': 'zh', 'ள': 'l', 'ற': 'r', 'ன': 'n',
    'ஜ': 'j', 'ஷ': 'sh', 'ஸ': 's', 'ஹ': 'h'
}

VOWEL_SIGNS = {
    'ா': 'aa', 'ி': 'i', 'ீ': 'ee', 'ு': 'u', 'ூ': 'oo',
    'ெ': 'e', 'ே': 'e', 'ை': 'ai', 'ொ': 'o', 'ோ': 'o', 'ௌ': 'au',
    '்': ''
}

def strip_tamil_dative_suffix(name: str) -> str:
    """Strips Tamil dative suffixes and stems inflected consonants (e.g. அருணுக்கு -> அருண்)."""
    for sfx, rep in TAMIL_DATIVE_STEMS:
        if name.endswith(sfx):
            return name[:-len(sfx)] + rep
    return name

def transliterate_tamil(word: str) -> str:
    """Fallback transliterator from Tamil script to Latin English phonetics."""
    clean_w = word.strip()
    if clean_w in TAMIL_COMMON_NAMES:
        return TAMIL_COMMON_NAMES[clean_w]

    out = []
    i = 0
    chars = list(clean_w)
    while i < len(chars):
        c = chars[i]
        if c in VOWELS:
            out.append(VOWELS[c])
            i += 1
        elif c in CONSONANTS:
            base = CONSONANTS[c]
            if i + 1 < len(chars) and chars[i + 1] in VOWEL_SIGNS:
                sign = VOWEL_SIGNS[chars[i + 1]]
                out.append(base + sign)
                i += 2
            else:
                out.append(base + ('a' if i + 1 < len(chars) and chars[i+1] in CONSONANTS else ''))
                i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out).strip().title()

def clean_contact_name_for_search(name: str) -> str:
    """
    Cleans contact names for English search in WhatsApp:
    - Strips prefixes like 'to', 'contact', 'for'
    - Strips Tamil dative suffixes ('-ku', 'ku', 'kitta', 'kooda', 'உக்கு', 'க்கு', 'வுக்கு')
    - Transliterates Tamil script into English Latin name
    - Capitalizes cleanly
    """
    raw = (name or "").strip().strip(" \"':;,.-")
    if not raw:
        return ""

    # Strip English/Tanglish prefixes
    raw = re.sub(r"^(?:to\s+|contact\s+|for\s+)", "", raw, flags=re.IGNORECASE).strip()

    # Strip trailing "in whatsapp" or "on whatsapp" if captured accidentally
    raw = re.sub(r"\s+(?:in|on)\s+whatsapp$", "", raw, flags=re.IGNORECASE).strip()

    # Strip Tamil dative case markers from Tamil script
    raw = strip_tamil_dative_suffix(raw)

    # Check known Tamil names directly
    if raw in TAMIL_COMMON_NAMES:
        return TAMIL_COMMON_NAMES[raw]

    # Strip Latin Tanglish suffixes: "-ku", " ku", " kitta", " kooda", "ukku"
    raw = re.sub(r"(?:-ku|\s+ku|\s+kitta|\s+kooda|ukku|ku)$", "", raw, flags=re.IGNORECASE).strip(" -_")

    if raw in TAMIL_COMMON_NAMES:
        return TAMIL_COMMON_NAMES[raw]

    for tn, en in TAMIL_COMMON_NAMES.items():
        if raw.startswith(tn):
            return en

    # If still contains Tamil script, transliterate phonetically
    if re.search(r"[\u0B80-\u0BFF]", raw):
        return transliterate_tamil(raw)

    return raw.strip().title()

def parse_whatsapp_voice_command(text: str) -> tuple[str | None, str | None]:
    """
    Parse a voice or text command into (receiver, message_text) where:
    - receiver: Contact name sanitized into English for WhatsApp contact search
    - message_text: Tanglish / Tamil / English message to be sent
    Returns (None, None) if not a WhatsApp messaging command.
    """
    if not text:
        return None, None

    t = text.strip()
    t_lower = t.lower()

    if not any(k in t_lower for k in ("whatsapp", "வாட்ஸ்அப்", "வாட்சப்", "வாட்ஸப்")):
        return None, None

    WA_PFX = r"(?:whatsapp(?:\s+la|\s+le|\s+laa|la|le)?|வாட்ஸ்அப்(?:ல|la)?|வாட்சப்(?:ல)?|வாட்ஸப்(?:ல)?)"
    CASE_MARKERS = r"(?:\s+ku|\s+kitta|\s+kooda|\s+க்கு|\s+வுக்கு|-ku|க்கு|வுக்கு)"
    ACTION_VERBS = r"(?:\s+anupu|\s+anupunga|\s+pannu|\s+pannunga|\s+send\s+pannu|\s+send|\s+போடு|\s+அனுப்பு|\s+பண்ணு|\s+podu|\s+podunga|\s+sollu|\s+solunga)"

    # Pattern 0A: Contact first ("Arun ku whatsapp la naan varren nu message anupu")
    m0a = re.search(
        rf"^([a-zA-Z0-9_\u0B80-\u0BFF]+(?:\s+[a-zA-Z0-9_\u0B80-\u0BFF]+)?){CASE_MARKERS}\s+{WA_PFX}\s+(.+?)\s+(?:nu|endru|என்று)\s+(?:message|msg|மெசேஜ்)(?:{ACTION_VERBS})?$",
        t, re.IGNORECASE
    )
    if m0a:
        contact = clean_contact_name_for_search(m0a.group(1))
        msg = m0a.group(2).strip()
        return contact, msg

    # Pattern 0B: Contact first + direct message ("Arun ku whatsapp la message pannu: enna pandra")
    m0b = re.search(
        rf"^([a-zA-Z0-9_\u0B80-\u0BFF]+(?:\s+[a-zA-Z0-9_\u0B80-\u0BFF]+)?){CASE_MARKERS}\s+{WA_PFX}\s+(?:message|msg|மெசேஜ்)(?:{ACTION_VERBS})?\s*[:,-]?\s*(.+)$",
        t, re.IGNORECASE
    )
    if m0b:
        contact = clean_contact_name_for_search(m0b.group(1))
        msg = m0b.group(2).strip().lstrip(":,- ")
        return contact, msg

    # Pattern 1: With explicit Tamil/Tanglish case marker ("whatsapp la Arun ku ... nu message anupu"):
    m1 = re.search(
        rf"^{WA_PFX}\s+([a-zA-Z0-9_\u0B80-\u0BFF]+(?:\s+[a-zA-Z0-9_\u0B80-\u0BFF]+)?){CASE_MARKERS}\s+(.+?)\s+(?:nu|endru|என்று)\s+(?:message|msg|மெசேஜ்)(?:{ACTION_VERBS})?$",
        t, re.IGNORECASE
    )
    if m1:
        contact = clean_contact_name_for_search(m1.group(1))
        msg = m1.group(2).strip()
        return contact, msg

    # Pattern 2: Explicit case marker + message marker ("whatsapp la rahul ku message pannu: enna pandra")
    m2 = re.search(
        rf"^{WA_PFX}\s+([a-zA-Z0-9_\u0B80-\u0BFF]+(?:\s+[a-zA-Z0-9_\u0B80-\u0BFF]+)?){CASE_MARKERS}\s+(?:message|msg|மெசேஜ்)(?:{ACTION_VERBS})?\s*[:,-]?\s*(.+)$",
        t, re.IGNORECASE
    )
    if m2:
        contact = clean_contact_name_for_search(m2.group(1))
        msg = m2.group(2).strip().lstrip(":,- ")
        return contact, msg

    # Pattern 3: English "send [whatsapp] message to <contact> [in/on whatsapp] saying <msg>"
    m3 = re.search(
        r"^(?:send\s+)?(?:a\s+)?(?:whatsapp\s+)?(?:message|msg)\s+(?:on\s+whatsapp\s+|in\s+whatsapp\s+)?to\s+([a-zA-Z0-9_\u0B80-\u0BFF]+(?:\s+[a-zA-Z0-9_\u0B80-\u0BFF]+)?)(?:\s+(?:on|in)\s+whatsapp)?\s*(?:saying|[:,-])\s*(.+)$",
        t, re.IGNORECASE
    )
    if m3:
        contact = clean_contact_name_for_search(m3.group(1))
        msg = m3.group(2).strip().lstrip(":,- ")
        return contact, msg

    # Pattern 4: Direct "whatsapp <contact> message <msg>"
    m4 = re.search(
        rf"^{WA_PFX}\s+([a-zA-Z0-9_\u0B80-\u0BFF]+(?:\s+[a-zA-Z0-9_\u0B80-\u0BFF]+)?)\s+(?:message|msg|மெசேஜ்)\s*[:,-]?\s*(.+)$",
        t, re.IGNORECASE
    )
    if m4:
        contact = clean_contact_name_for_search(m4.group(1))
        msg = m4.group(2).strip().lstrip(":,- ")
        return contact, msg

    return None, None

def _search_in_app(query: str) -> None:
    _require_pyautogui()
    os_name = _get_os()
    search_hotkey = ("command", "f") if os_name == "mac" else ("ctrl", "f")

    pyautogui.hotkey(*search_hotkey)
    time.sleep(0.5)
    _clear_and_paste(query)
    time.sleep(1.0)

def _desktop_send(app_name: str, receiver: str, message: str) -> str:
    if not _open_app(app_name):
        return f"Could not open {app_name}."

    time.sleep(1.0)
    _search_in_app(receiver)
    pyautogui.press("enter")
    time.sleep(0.8)

    _paste_text(message)
    time.sleep(0.2)
    pyautogui.press("enter")
    time.sleep(0.3)
    return f"Message sent to {receiver} via {app_name}."

def _send_whatsapp(receiver: str, message: str) -> str:
    search_name = clean_contact_name_for_search(receiver)
    return _desktop_send("WhatsApp", search_name, message)

def _send_telegram(receiver: str, message: str) -> str:
    return _desktop_send("Telegram", receiver, message)

def _send_signal(receiver: str, message: str) -> str:
    return _desktop_send("Signal", receiver, message)


def _send_discord(receiver: str, message: str) -> str:
    return _desktop_send("Discord", receiver, message)


def _send_instagram(receiver: str, message: str) -> str:
    _require_pyautogui()

    if not _open_browser_url("https://www.instagram.com/direct/new/"):
        return "Could not open Instagram in browser."

    _paste_text(receiver)
    time.sleep(1.5)

    pyautogui.press("down")
    time.sleep(0.3)
    pyautogui.press("enter")   
    time.sleep(0.4)

    for _ in range(4):
        pyautogui.press("tab")
        time.sleep(0.15)
    pyautogui.press("enter")
    time.sleep(2.0)

    _paste_text(message)
    time.sleep(0.2)
    pyautogui.press("enter")
    time.sleep(0.3)

    return f"Message sent to {receiver} via Instagram."


def _send_messenger(receiver: str, message: str) -> str:
    _require_pyautogui()

    if not _open_browser_url("https://www.messenger.com/"):
        return "Could not open Messenger in browser."


    _search_in_app(receiver)
    time.sleep(0.5)
    pyautogui.press("down")
    time.sleep(0.3)
    pyautogui.press("enter")
    time.sleep(1.0)

    _paste_text(message)
    time.sleep(0.2)
    pyautogui.press("enter")
    time.sleep(0.3)

    return f"Message sent to {receiver} via Messenger."

_PLATFORM_MAP = [
    ({"whatsapp", "wp", "wapp"},              _send_whatsapp),
    ({"telegram", "tg"},                      _send_telegram),
    ({"instagram", "ig", "insta"},            _send_instagram),
    ({"signal"},                               _send_signal),
    ({"discord"},                              _send_discord),
    ({"messenger", "facebook", "fb"},         _send_messenger),
]


def _resolve_platform(platform_str: str):
    key = platform_str.lower().strip()
    for keywords, handler in _PLATFORM_MAP:
        if any(k in key for k in keywords):
            return handler
    return lambda r, m: _desktop_send(platform_str.strip().title(), r, m)


def send_message(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params       = parameters or {}
    raw_receiver = params.get("receiver", "").strip()
    receiver     = clean_contact_name_for_search(raw_receiver) or raw_receiver
    message_text = params.get("message_text", "").strip()
    platform     = params.get("platform", "whatsapp").strip()

    if not receiver:
        return "Please specify a recipient."
    if not message_text:
        return "Please specify the message content."
    if not _PYAUTOGUI:
        return "PyAutoGUI is not installed — cannot control the desktop."

    preview = message_text[:50] + ("…" if len(message_text) > 50 else "")
    print(f"[SendMessage] 📨 {platform} → {receiver} (raw: '{raw_receiver}'): {preview}")
    if player:
        player.write_log(f"[msg] {platform} → {receiver}")

    try:
        handler = _resolve_platform(platform)
        result  = handler(receiver, message_text)
    except Exception as e:
        result = f"Could not send message: {e}"

    print(f"[SendMessage] {'✅' if 'sent' in result.lower() else '❌'} {result}")
    if player:
        player.write_log(f"[msg] {result}")

    return result