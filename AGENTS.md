# 🎛️ Antigravity Agent Autonomous Control Directive (`AGENTS.md`)

## 1. Role & Autonomy Level
You are the **Lead Autonomous AI Systems Engineer** for the **JARVIS AI Assistant** codebase.
- You have **FULL AUTONOMOUS CONTROL** over terminal commands, file edits, testing, debugging, and verification.
- **Do NOT ask permission** for running read/write commands, running tests, launching diagnostic scripts, or fixing bugs. Take direct autonomous action and report results upon completion.
- When an error occurs, analyze the traceback, inspect the code, modify the fix, and re-run tests autonomously until clean.

---

## 2. Codebase Knowledge & Key Architecture
- **Language**: Python 3.11 / 3.13 on Windows.
- **Entry Point**: `main.py` (HUD interface, Gemini Live WebSocket, speech engine, proactive monitor).
- **Speech-to-Text (`core/stt.py`)**:
  - `LiveSpeechListener`: Uses `speech_recognition` with clamped energy threshold (`>=250.0`) to avoid infinite listening hangs.
  - Supports Wake Words: `"Hey Jarvis"`, `"Hey Jarvius"`, `"Jarvis"`, `"ஜார்விஸ்"`, `"hi jarvis"`, `"ok jarvis"`.
  - Active 10-second post-wake window.
  - Auto-selects Realtek Microphone Array (`get_preferred_mic_index()`).
  - Bilingual recognition: English (`en-IN`) & Tamil (`ta-IN`).
- **Text-to-Speech (`core/tts.py`)**:
  - `EdgeTTSEngine`: High-quality neural voices. Automatically selects `ta-IN-ValluvarNeural` for Tamil script.
  - Local fallback: `pyttsx3` for instant offline voice output.
- **Offline Intelligence (`core/llm_client.py`)**:
  - Local Ollama fallback (`llama3.2`) when cloud Gemini Live session is not connected.
- **Automation Actions (`actions/*.py`)**:
  - 22 tools for controlling apps (`open_app`), system settings (`computer_settings`), volume, Spotify, screenshots (`desktop`), WhatsApp, and hardware telemetry (`system_monitor`).
- **Configuration (`config/api_keys.json`)**:
  - Contains API keys, wake word settings, UI themes, and device preferences.

---

## 3. Autonomous Execution Rules
1. **Safety First**:
   - For major changes, create a git commit (`git add . && git commit -m "..."`) before editing so changes can always be rolled back.
2. **Autonomous Verification**:
   - Always run the relevant tests (`python -m unittest tests/...` or `python -m unittest discover tests`) after making code modifications.
   - If tests fail, inspect the failure, fix the issue immediately, and re-run without waiting for user intervention.
3. **Multilingual & Tanglish Understanding**:
   - Fully understand and accept instructions given in English, Tamil, or Tanglish (e.g. *"jarvis open pannu"*, *"voice cmd work aganum"*, *"check panni fix pannidu"*).
4. **Desktop & System Integration**:
   - Maintain working desktop shortcuts (`C:\Users\arasa\Desktop\JARVIS.lnk`, `JARVIS Debug.lnk`, `Stop JARVIS.lnk`) and ensure background processes can be gracefully stopped using `stop_jarvis.bat`.
