"""
Speech-to-Text engines for MARK XL.

Whisper  – offline transcription via faster-whisper (VAD-buffered)
Vosk     – offline streaming transcription (lighter)
"""
import json
import os
import re
import sys
import numpy as np
import speech_recognition as sr

# Safe PyAudio stream cleanup patch for Windows
try:
    _orig_mic_exit = sr.Microphone.__exit__
    def _safe_mic_exit(self, exc_type, exc_value, traceback):
        if getattr(self, "stream", None) is not None:
            try:
                self.stream.close()
            except Exception:
                pass
        self.stream = None
    sr.Microphone.__exit__ = _safe_mic_exit
except Exception:
    pass

# Ensure UTF-8 console output for Tamil and Indic Unicode on Windows
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

_orig_print = print
def print(*args, **kwargs):
    """Safe print wrapper preventing UnicodeEncodeError on Windows codepages."""
    try:
        _orig_print(*args, **kwargs)
    except UnicodeEncodeError:
        try:
            cleaned = [str(a).encode("ascii", errors="replace").decode("ascii") for a in args]
            _orig_print(*cleaned, **kwargs)
        except Exception:
            pass
    except Exception:
        pass


def _setup_cuda_dll_paths() -> None:
    """Ensure CUDA and cuDNN DLLs in Python site-packages and PATH are discoverable on Windows."""
    if sys.platform != "win32":
        return

    import site

    candidate_dirs = set()

    site_dirs = []
    try:
        site_dirs.extend(site.getsitepackages())
    except Exception:
        pass
    try:
        user_site = site.getusersitepackages()
        if user_site:
            site_dirs.append(user_site)
    except Exception:
        pass

    for s_dir in site_dirs:
        nvidia_dir = os.path.join(s_dir, "nvidia")
        if os.path.isdir(nvidia_dir):
            for root, dirs, files in os.walk(nvidia_dir):
                if "bin" in dirs:
                    candidate_dirs.add(os.path.join(root, "bin"))
        torch_lib = os.path.join(s_dir, "torch", "lib")
        if os.path.isdir(torch_lib):
            candidate_dirs.add(torch_lib)

    path_entries = os.environ.get("PATH", "").split(os.pathsep)
    for c_dir in sorted(candidate_dirs):
        if os.path.isdir(c_dir):
            try:
                os.add_dll_directory(c_dir)
            except Exception:
                pass
            if c_dir not in path_entries:
                path_entries.insert(0, c_dir)
    os.environ["PATH"] = os.pathsep.join(path_entries)


class WhisperSTT:
    """Offline transcription using faster-whisper with robust CUDA initialization and CPU fallback."""

    def __init__(self, model_name: str = "base", language: str | None = None):
        _setup_cuda_dll_paths()

        print(f"[STT] Loading Whisper '{model_name}'...")
        cuda_avail = False
        try:
            import torch
            cuda_avail = torch.cuda.is_available()
        except Exception:
            cuda_avail = False

        print(f"[STT] CUDA available: {cuda_avail}")

        self._model_name = model_name
        self._language = None if (not language or language.strip().lower() == "auto") else language.strip().lower()
        self._device = "cpu"
        self._model = None

        if cuda_avail:
            print("[STT] Initializing faster-whisper on CUDA...")
            try:
                model = self._load_model(model_name, device="cuda", compute_type="float16")
                # Probe CUDA execution to catch missing DLLs or driver errors before live audio
                probe_audio = np.zeros(1600, dtype=np.float32)
                _ = list(model.transcribe(probe_audio, language="en", beam_size=1)[0])
                self._model = model
                self._device = "cuda"
                print(f"[STT] Whisper '{model_name}' ready (cuda)")
            except Exception as cuda_err:
                print(f"[STT] CUDA initialization failed: {cuda_err}")
                print("[STT] Falling back to CPU int8 so the call remains alive.")
                self._model = self._load_model(model_name, device="cpu", compute_type="int8")
                self._device = "cpu"
                print(f"[STT] Whisper '{model_name}' ready (cpu)")
        else:
            self._model = self._load_model(model_name, device="cpu", compute_type="int8")
            self._device = "cpu"
            print(f"[STT] Whisper '{model_name}' ready (cpu)")

    def _load_model(self, model_name: str, device: str, compute_type: str):
        from faster_whisper import WhisperModel
        try:
            return WhisperModel(model_name, device=device, compute_type=compute_type)
        except Exception as _first_err:
            _e = str(_first_err).lower()
            _offline_keywords = (
                "offline", "not found", "cache", "localentry",
                "does not exist", "outgoing", "local_files_only",
            )
            if any(k in _e for k in _offline_keywords):
                print(f"[STT] Whisper '{model_name}' not in local cache — downloading (one-time, internet required)…")
                os.environ.pop("HF_HUB_OFFLINE", None)
                os.environ.pop("TRANSFORMERS_OFFLINE", None)
                os.environ.pop("HF_DATASETS_OFFLINE", None)
                try:
                    return WhisperModel(model_name, device=device, compute_type=compute_type)
                except Exception as _dl_err:
                    raise RuntimeError(
                        f"Whisper '{model_name}' model download failed.\n"
                        f"Internet access is required the first time to download the speech model (~75–290 MB).\n"
                        f"After the first download it runs fully offline.\n"
                        f"Details: {_dl_err}"
                    ) from _dl_err
            raise

    def transcribe(self, audio: np.ndarray) -> str:
        """Transcribe a float32 mono 16 kHz numpy array. Returns transcript string."""
        try:
            segments, _ = self._model.transcribe(
                audio,
                language=self._language,
                beam_size=1,                       # greedy — 2-3x faster
                best_of=1,
                condition_on_previous_text=False,  # no hallucinations, faster
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 300},
            )
            return " ".join(s.text for s in segments).strip()
        except Exception as e:
            err_str = str(e).lower()
            if self._device == "cuda" and any(k in err_str for k in ("cublas", "cuda", "cudnn", "driver", "failed to load")):
                print(f"[STT] CUDA transcription error: {e}")
                print("[STT] Falling back to CPU int8 so the call remains alive.")
                try:
                    self._model = self._load_model(self._model_name, device="cpu", compute_type="int8")
                    self._device = "cpu"
                    print(f"[STT] Whisper '{self._model_name}' ready (cpu)")
                    segments, _ = self._model.transcribe(
                        audio,
                        language=self._language,
                        beam_size=1,
                        best_of=1,
                        condition_on_previous_text=False,
                        vad_filter=True,
                        vad_parameters={"min_silence_duration_ms": 300},
                    )
                    return " ".join(s.text for s in segments).strip()
                except Exception as fallback_err:
                    print(f"[STT] CPU transcription fallback error: {fallback_err}")
                    raise
            print(f"[STT] Transcription error: {e}")
            raise


class VoskSTT:
    """Streaming transcription using Vosk."""

    def __init__(self, model_path: str | None = None, language: str = "en-us"):
        from vosk import Model, KaldiRecognizer
        print("[STT] Loading Vosk model…")
        if model_path:
            model = Model(model_path)
        else:
            lang  = language.strip().lower() if language and language.strip().lower() != "auto" else "en-us"
            model = Model(lang=lang)
        self._rec = KaldiRecognizer(model, 16000)
        print("[STT] Vosk ready.")

    def process_chunk(self, audio_bytes: bytes) -> tuple[str, bool]:
        """Feed raw int16 LE PCM bytes. Returns (text, is_final)."""
        if self._rec.AcceptWaveform(audio_bytes):
            result = json.loads(self._rec.Result())
            return result.get("text", ""), True
        partial = json.loads(self._rec.PartialResult())
        return partial.get("partial", ""), False


class GroqWhisperSTT:
    """
    Ultra-fast cloud Speech-to-Text transcription powered by Groq's Whisper API.
    Transcribes float32 mono 16 kHz numpy audio arrays in ~150-300ms using whisper-large-v3-turbo.
    """

    def __init__(self, api_key: str | None = None, model: str = "whisper-large-v3-turbo", language: str | None = None):
        self._api_key = api_key or os.environ.get("GROQ_API_KEY", "")
        if not self._api_key:
            try:
                from pathlib import Path
                base_dir = Path(__file__).resolve().parent.parent
                cfg_path = base_dir / "config" / "api_keys.json"
                if cfg_path.exists():
                    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                    self._api_key = cfg.get("groq_api_key", "")
            except Exception:
                pass
        self._model = model
        self._language = None if (not language or language.strip().lower() == "auto") else language.strip().lower()
        print(f"[STT] GroqWhisperSTT initialized (model: {self._model})")

    def transcribe(self, audio: np.ndarray) -> str:
        """Transcribe a float32 mono 16 kHz numpy array using Groq Whisper API."""
        if not self._api_key:
            raise RuntimeError("Groq API key not configured for GroqWhisperSTT.")

        import io
        import wave
        import requests

        int16_audio = np.clip(audio * 32767.0, -32768, 32767).astype(np.int16)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(int16_audio.tobytes())
        buf.seek(0)

        headers = {"Authorization": f"Bearer {self._api_key}"}
        files = {"file": ("audio.wav", buf.read(), "audio/wav")}
        data = {"model": self._model}
        if self._language:
            data["language"] = self._language

        try:
            resp = requests.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers=headers,
                files=files,
                data=data,
                timeout=15,
            )
            resp.raise_for_status()
            return (resp.json().get("text") or "").strip()
        except Exception as e:
            print(f"[STT] Groq Whisper error: {e}")
            raise


DEFAULT_WAKE_WORDS = [
    "hey jarvis",
    "jarvis",
    "hey jarvius",
    "jarvius",
    "hi jarvis",
    "hello jarvis",
    "ok jarvis",
    "okay jarvis",
    "a jarvis",
    "eh jarvis",
    "jarviz",
    "jarves",
    "jaavis",
    "charvis",
    # Tamil wake words
    "ஜார்விஸ்",
    "ஹேய் ஜார்விஸ்",
    "ஹே ஜார்விஸ்",
    "ஏய் ஜார்விஸ்",
    "ஹாய் ஜார்விஸ்",
    "ஹலோ ஜார்விஸ்",
    "வணக்கம் ஜார்விஸ்",
    "சார்விஸ்",
    "ஜார்விசு",
]

_WAKE_PFX = r"(?:hey|hi|hello|ok|okay|yo|a|eh|ஹேய்|ஹே|ஏய்|ஹாய்|ஹலோ|வணக்கம்|சரி)"
_WAKE_NAMES = r"(?:jarvis|jarvius|jarviz|jarves|jaavis|jarwiss?|charvis|ஜார்விஸ்|சார்விஸ்|ஜார்விசு)"

_WAKE_REGEX = re.compile(
    rf"^\s*(?:{_WAKE_PFX}\s+)?{_WAKE_NAMES}(?:\b|\s|[\s,:\-–]|$)+(.*)$",
    re.IGNORECASE | re.UNICODE
)

_EMBEDDED_WAKE_REGEX = re.compile(
    rf"(?:(?:\b|^)(?:{_WAKE_PFX})\s+)?{_WAKE_NAMES}(?:\b|\s|[\s,:\-–]|$)",
    re.IGNORECASE | re.UNICODE
)

TAMIL_CHAR_RE = re.compile(r"[\u0B80-\u0BFF]")


def select_bilingual_result(
    res_ta: str | None,
    res_en: str | None,
    custom_wake_words: list[str] | None = None
) -> str | None:
    """
    Intelligently select the most accurate transcription between Tamil (ta-IN)
    and English (en-IN) recognition candidates for Tanglish and bilingual voice commands.
    """
    if not res_ta and not res_en:
        return None
    if not res_ta:
        return res_en
    if not res_en:
        return res_ta

    # 1. Wake word match preference
    is_wake_ta, _, _ = parse_wake_word(res_ta, custom_wake_words)
    is_wake_en, _, _ = parse_wake_word(res_en, custom_wake_words)

    if is_wake_ta and not is_wake_en:
        return res_ta
    if is_wake_en and not is_wake_ta:
        return res_en

    # 2. Check for Tamil script
    has_tamil = bool(TAMIL_CHAR_RE.search(res_ta))

    # 3. Check for standard English query words
    common_english = {
        "what", "who", "where", "how", "when", "why", "is", "the", "time", "date",
        "open", "close", "play", "pause", "stop", "next", "volume", "system", "battery",
        "weather", "search", "mute", "unmute"
    }
    en_words = set(re.findall(r"\w+", res_en.lower()))
    en_match_count = len(en_words.intersection(common_english))

    # If pure English standard command and not Tanglish particles:
    if en_match_count >= 2 and not any(k in res_en.lower() for k in ("pannu", "pannunga", "enna", "epdi", "irukka", "vanakkam")):
        return res_en

    # If Tamil script present, prefer Tamil
    if has_tamil:
        return res_ta

    return res_en or res_ta


def parse_wake_word(text: str, custom_wake_words: list[str] | None = None) -> tuple[bool, str, str]:
    """
    Check if speech input begins with or contains a wake word (e.g. 'Hey Jarvis', 'Jarvis', 'Hey Jarvius', 'ஜார்விஸ்').
    Returns:
        (is_wake_detected: bool, remaining_command: str, matched_wake_phrase: str)
    """
    if not text:
        return False, "", ""

    t_clean = text.strip()

    # 1. Custom wake words if supplied
    if custom_wake_words:
        t_lower = t_clean.lower()
        sorted_words = sorted([w.strip().lower() for w in custom_wake_words if w.strip()], key=len, reverse=True)
        for w in sorted_words:
            if t_lower.startswith(w):
                remainder = t_clean[len(w):].strip(" ,:.-–!?;")
                return True, remainder, w

    # 2. Fast regex match at start of speech (e.g., "Hey Jarvis, open chrome" -> "open chrome")
    m = _WAKE_REGEX.match(t_clean)
    if m:
        prefix_len = len(t_clean) - len(m.group(1)) if m.group(1) is not None else len(t_clean)
        matched_wake = t_clean[:prefix_len].strip(" ,:.-–!?;")
        remainder = (m.group(1) or "").strip(" ,:.-–!?;")
        return True, remainder, matched_wake.lower()

    # 3. Check for wake word embedded anywhere in the command (e.g., "what time is it jarvis")
    m_emb = _EMBEDDED_WAKE_REGEX.search(t_clean)
    if m_emb:
        matched_wake = m_emb.group(0).lower()
        remainder = (t_clean[:m_emb.start()] + " " + t_clean[m_emb.end():]).strip(" ,:.-–!?;")
        remainder = re.sub(r"\s+", " ", remainder).strip()
        return True, remainder, matched_wake

    return False, t_clean, ""


class LiveSpeechListener:
    """
    Continuous background Speech-to-Text listener using SpeechRecognition.
    Automatically detects when the user speaks and stops speaking (silence pause),
    converts voice to text, and invokes `on_command(text, speaker_info)`.
    Supports 'Hey Jarvis' / 'Jarvis' / 'Hey Jarvius' wake-word activation,
    active post-wake listening window, passive ambient noise filtering,
    maintains a persistent microphone stream to eliminate device lag,
    enforces acoustic cooldown after JARVIS speaks to eliminate speaker echo loops,
    supports voice barge-in (stopping JARVIS speech when Sir speaks),
    and performs speaker biometric verification.
    """

    INTERRUPT_KEYWORDS = {
        "stop", "wait", "hold on", "cancel", "pause", "enough", "jarvis stop",
        "stop jarvis", "quiet", "be quiet", "shut up", "hush", "stop talking",
        "nillu", "niruthu", "pothum"
    }

    @staticmethod
    def get_preferred_mic_index() -> int | None:
        """
        Find the most suitable working microphone index on Windows.
        Prioritizes the Windows default recording device (which reflects user's
        Sound Settings selection), then Bluetooth/headset devices, then arrays.
        Skips raw driver-path-only bthhfenum entries (no clean device name).

        Bluetooth coherence: when the TTS output is already routed to a BT
        device (headphones/headset), the matching BT input device gets a large
        coherence bonus so it beats the laptop's default internal mic.
        """
        try:
            import sounddevice as sd
            import speech_recognition as sr

            default_dev = None
            try:
                d_in = sd.default.device[0]
                if d_in is not None and d_in >= 0:
                    default_dev = int(d_in)
            except Exception:
                pass

            # ── Detect active BT output device name for coherence bonus ──
            active_bt_out_name = ""
            try:
                d_out_idx = sd.default.device[1]
                if d_out_idx is not None and d_out_idx >= 0:
                    d_out = sd.query_devices(int(d_out_idx))
                    out_name = d_out.get("name", "").lower()
                    if any(k in out_name for k in (
                        "headphones", "headset", "bluetooth", "p47", "m19",
                        "boult", "airbass", "zyio"
                    )):
                        active_bt_out_name = out_name
                        print(f"[STT] BT output active: '{d_out.get('name', '')}' — will boost matching BT mic")
            except Exception:
                pass

            devices = sd.query_devices()
            candidates = []
            for i, d in enumerate(devices):
                if d.get("max_input_channels", 0) <= 0:
                    continue

                raw_name = d.get("name", "")
                name = raw_name.lower()

                # Skip entries whose name IS the raw BT driver path (no friendly name)
                # e.g. "@System32\drivers\bthhfenum.sys,#2;%1 Hands-Free%0\n;(P47)"
                if "system32" in name and "bthhfenum" in name:
                    continue

                api_name = ""
                try:
                    api_name = sd.query_hostapis(d.get("hostapi", 0)).get("name", "").lower()
                except Exception:
                    pass

                # WDM-KS causes static on BT — skip entirely
                if "wdm-ks" in api_name:
                    continue

                score = 0

                # ── Highest priority: Windows default recording device ──
                if default_dev is not None and i == default_dev:
                    score += 100

                # ── Device type scoring ──
                is_bt_headset = any(k in name for k in (
                    "headset", "hands-free", "p47", "m19",
                    "boult", "airbass", "zyio", "bluetooth"
                ))
                if is_bt_headset:
                    score += 30   # Bluetooth headsets get strong bonus
                elif "usb" in name:
                    score += 35
                elif "microphone array" in name or "array" in name:
                    score += 28
                elif "mic" in name:
                    score += 15

                # ── Bluetooth coherence bonus ──
                # When TTS output is on a BT device, give BT mic a large bonus
                # so it wins over the default internal laptop mic.
                if is_bt_headset and active_bt_out_name:
                    # Extra boost if device name tokens overlap (same headset model).
                    # Must be large enough to beat default laptop mic (default+array+mme = 138).
                    out_tokens = set(active_bt_out_name.split())
                    in_tokens  = set(name.split())
                    if out_tokens & in_tokens:
                        score += 120  # same model — strong coherence match (beats default mic)
                    else:
                        score += 80   # different BT device but still BT coherence

                # Penalise generic mapper/primary aliases (they shadow real devices)
                if "mapper" in name or "primary" in name:
                    score -= 20

                # ── Audio API quality scoring ──
                if "wasapi" in api_name:
                    score += 15
                elif "mme" in api_name:
                    score += 10
                elif "directsound" in api_name:
                    score += 5

                candidates.append((score, i, raw_name))

            candidates.sort(key=lambda x: x[0], reverse=True)

            for score, idx, name in candidates:
                if score <= 0:
                    continue
                # Verify SpeechRecognition can actually open this device
                try:
                    probe = sr.Microphone(device_index=idx)
                    with probe as src:
                        if src and getattr(src, "stream", None) is not None:
                            print(f"[STT] Auto-selected preferred audio device [{idx}]: {name}")
                            return idx
                except Exception:
                    continue
        except Exception:
            pass
        return None

    def __init__(
        self,
        device_index: int | str | None = None,
        language: str = "bilingual",
        pause_threshold: float = 1.0,
        phrase_time_limit: float = 15.0,
        acoustic_cooldown: float = 0.6,
        continuous_mode: bool = True,
        voice_barge_in: bool = True,
        wake_word_enabled: bool = True,
        wake_timeout: float = 10.0,
        wake_words: list[str] | None = None,
        on_wake = None,
        on_command = None,
        on_interrupt = None,
        get_speaking_text_fn = None,
        is_speaking_fn = None,
        is_muted_fn = None,
        voice_manager = None,
        log_fn = None,
    ):
        import speech_recognition as sr
        self.sr = sr
        # Auto-resolve preferred microphone index if none or 'auto' provided
        if device_index is None or str(device_index).lower() in ("auto", "none", "null", ""):
            self.device_index = self.get_preferred_mic_index()
        else:
            try:
                import sounddevice as sd
                idx_int = int(device_index)
                d_info = sd.query_devices(idx_int)
                if d_info.get("max_input_channels", 0) > 0:
                    self.device_index = idx_int
                else:
                    self.device_index = self.get_preferred_mic_index()
            except Exception:
                self.device_index = self.get_preferred_mic_index()
        self.language = language or "bilingual"
        self.pause_threshold = pause_threshold
        self.phrase_time_limit = phrase_time_limit
        self.acoustic_cooldown = acoustic_cooldown
        self.continuous_mode = continuous_mode
        self.voice_barge_in = voice_barge_in
        self.wake_word_enabled = wake_word_enabled
        self.wake_timeout = wake_timeout
        self.wake_words = wake_words or DEFAULT_WAKE_WORDS
        self.on_wake = on_wake
        self.on_command = on_command
        self.on_interrupt = on_interrupt
        self.get_speaking_text_fn = get_speaking_text_fn
        self.is_speaking_fn = is_speaking_fn
        self.is_muted_fn = is_muted_fn
        self.voice_manager = voice_manager
        self.log_fn = log_fn

        self.recognizer = sr.Recognizer()
        self.recognizer.dynamic_energy_threshold = True
        self.recognizer.dynamic_energy_adjustment_damping = 0.15
        self.recognizer.dynamic_energy_ratio = 1.5
        self.recognizer.pause_threshold = self.pause_threshold
        self.recognizer.phrase_threshold = min(0.15, self.pause_threshold)
        self.recognizer.non_speaking_duration = min(0.3, self.pause_threshold)
        self.recognizer.energy_threshold = 250.0

        self.microphone = None
        self._thread = None
        self._running = False
        self._cooldown_until = 0.0
        self._was_speaking = False
        self._wake_active_until = 0.0

    def is_awake(self) -> bool:
        """Return True if listener is currently in the active post-wake window."""
        import time
        return time.time() < self._wake_active_until

    def wake_up(self, duration: float | None = None) -> None:
        """Explicitly set active wake window."""
        import time
        t = duration if duration is not None else self.wake_timeout
        self._wake_active_until = time.time() + t

    def sleep(self) -> None:
        """Put listener back to sleep / passive listening mode."""
        self._wake_active_until = 0.0

    def trigger_cooldown(self, extra_seconds: float = 0.0) -> None:
        """Enforce acoustic cooldown after JARVIS finishes speaking to drop speaker echo."""
        import time
        self._cooldown_until = time.time() + self.acoustic_cooldown + extra_seconds

    def _is_echo(self, heard: str, speaking: str) -> bool:
        """Return True if heard text is acoustic echo of what JARVIS is speaking."""
        if not speaking:
            return False
        h = heard.strip().lower()
        s = speaking.strip().lower()
        if not h or not s:
            return False
        if len(h) >= 6 and h in s:
            return True
        h_words = set(h.split())
        s_words = set(s.split())
        if len(h_words) >= 3:
            overlap = len(h_words.intersection(s_words)) / len(h_words)
            if overlap >= 0.65:
                return True
        return False

    def transcribe_audio(self, audio) -> str | None:
        """
        Transcribe audio using Google STT with bilingual Tanglish and Tamil support.
        In 'bilingual' or 'auto' mode, queries ta-IN and en-IN concurrently and
        picks the best candidate without delay.
        """
        lang = (self.language or "bilingual").strip().lower()

        if lang in ("bilingual", "auto", "ta+en", "en+ta", "tanglish"):
            import concurrent.futures

            def _try(l: str) -> str | None:
                try:
                    return self.recognizer.recognize_google(audio, language=l).strip()
                except Exception:
                    return None

            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                    f_ta = executor.submit(_try, "ta-IN")
                    f_en = executor.submit(_try, "en-IN")
                    res_ta = f_ta.result(timeout=10.0)
                    res_en = f_en.result(timeout=10.0)
                return select_bilingual_result(res_ta, res_en, self.wake_words)
            except Exception:
                res_ta = _try("ta-IN")
                res_en = _try("en-IN")
                return select_bilingual_result(res_ta, res_en, self.wake_words)

        elif lang == "ta-in" or "ta" in lang:
            try:
                return self.recognizer.recognize_google(audio, language="ta-IN").strip()
            except Exception:
                try:
                    return self.recognizer.recognize_google(audio, language="en-IN").strip()
                except Exception:
                    return None
        else:
            try:
                return self.recognizer.recognize_google(audio, language=self.language).strip()
            except Exception:
                try:
                    return self.recognizer.recognize_google(audio, language="ta-IN").strip()
                except Exception:
                    return None

    def start(self):
        """Initialize microphone and start background listening thread."""
        if self._running:
            return
        self._running = True

        import threading
        self._thread = threading.Thread(target=self._run_loop, name="LiveSpeechListener", daemon=True)
        self._thread.start()

    def stop(self):
        """Stop listening thread."""
        self._running = False

    def _run_loop(self):
        import time

        _broken_devices: dict[int | None, float] = {}   # device_index -> blacklist_until timestamp
        _BLACKLIST_SECS = 60.0

        def _get_working_device_order() -> list[int | None]:
            """Return candidate device indices to try, prioritized by system default and quality."""
            candidates: list[int | None] = []
            # Always try the explicitly configured device first (if not blacklisted)
            if self.device_index is not None and self.device_index not in _broken_devices:
                candidates.append(self.device_index)

            # System default recording device
            default_dev = None
            try:
                import sounddevice as sd
                d_in = sd.default.device[0]
                if d_in is not None and d_in >= 0 and d_in not in _broken_devices:
                    default_dev = int(d_in)
                    if default_dev not in candidates:
                        candidates.append(default_dev)
            except Exception:
                pass

            if None not in candidates and None not in _broken_devices:
                candidates.append(None)

            # Add all other available input devices ranked by reliability
            try:
                import sounddevice as sd
                devices = sd.query_devices()

                # Detect active BT output for coherence scoring
                _active_bt_out = ""
                try:
                    d_out_idx = sd.default.device[1]
                    if d_out_idx is not None and d_out_idx >= 0:
                        _d_out = sd.query_devices(int(d_out_idx))
                        _out_name = _d_out.get("name", "").lower()
                        if any(k in _out_name for k in (
                            "headphones", "headset", "bluetooth", "p47", "m19",
                            "boult", "airbass", "zyio"
                        )):
                            _active_bt_out = _out_name
                except Exception:
                    pass

                ranked = []
                for i, d in enumerate(devices):
                    if d.get("max_input_channels", 0) > 0:
                        name = d.get("name", "").lower()
                        if "bthhfenum" in name or "system32" in name:
                            continue
                        score = 0
                        if default_dev is not None and i == default_dev:
                            score += 100
                        is_bt = any(k in name for k in ("headset", "hands-free", "m19", "p47", "boult", "airbass", "zyio", "bluetooth"))
                        if is_bt:
                            score += 25
                        elif "usb" in name:
                            score += 35
                        elif "microphone array" in name or "array" in name:
                            score += 30
                        elif "mic" in name:
                            score += 15
                        if "mapper" in name or "primary" in name:
                            score -= 20

                        # BT coherence bonus in reconnect loop
                        if is_bt and _active_bt_out:
                            out_tokens = set(_active_bt_out.split())
                            in_tokens  = set(name.split())
                            if out_tokens & in_tokens:
                                score += 120  # same model wins over default laptop mic
                            else:
                                score += 80

                        api_name = sd.query_hostapis(d.get("hostapi", 0)).get("name", "").lower()
                        if "wdm-ks" in api_name:
                            continue
                        if "wasapi" in api_name:
                            score += 15
                        elif "mme" in api_name:
                            score += 10
                        elif "directsound" in api_name:
                            score += 5

                        ranked.append((score, i))
                ranked.sort(key=lambda x: x[0], reverse=True)
                for _, idx in ranked:
                    if idx not in candidates:
                        candidates.append(idx)
            except Exception:
                pass
            return candidates

        def _probe_device(idx: int | None) -> bool:
            """Return True only if the device opens cleanly and its stream is not None."""
            now = time.time()
            if idx in _broken_devices and now < _broken_devices[idx]:
                return False   # still blacklisted
            try:
                probe_mic = self.sr.Microphone(device_index=idx)
                with probe_mic as probe_src:
                    if probe_src is None or getattr(probe_src, "stream", None) is None:
                        _broken_devices[idx] = now + _BLACKLIST_SECS
                        return False
                return True
            except Exception:
                _broken_devices[idx] = now + _BLACKLIST_SECS
                return False

        # Outer reconnection loop for device resilience
        while self._running:
            # Find first working device
            _SENTINEL = object()
            working_device = _SENTINEL  # will be replaced with int | None once a working device is found
            for candidate in _get_working_device_order():
                if _probe_device(candidate):
                    working_device = candidate
                    break

            if working_device is _SENTINEL:
                print("[STT] No working microphone found. Retrying in 3s…")
                if self.log_fn:
                    self.log_fn("WARN: No working microphone detected. Retrying in 3s…")
                time.sleep(3.0)
                continue

            names = []
            try:
                names = self.sr.Microphone.list_microphone_names()
            except Exception:
                pass
            dev_name = names[working_device] if (working_device is not None and names and working_device < len(names)) else "default"

            if working_device != self.device_index:
                print(f"[STT] Falling back to device [{working_device}]: {dev_name}")
                if self.log_fn:
                    self.log_fn(f"SYS: STT using fallback mic [{working_device}]: {dev_name}")

            try:
                mic = self.sr.Microphone(device_index=working_device)
            except Exception as e:
                print(f"[STT] Failed to create microphone (device={working_device}): {e}")
                _broken_devices[working_device] = time.time() + _BLACKLIST_SECS
                time.sleep(1.0)
                continue

            try:
                with mic as source:
                    # Guard: stream must not be None
                    if source is None or getattr(source, "stream", None) is None:
                        print(f"[STT] Device [{working_device}] opened but stream is None — blacklisting.")
                        _broken_devices[working_device] = time.time() + _BLACKLIST_SECS
                        time.sleep(1.0)
                        continue

                    self.microphone = source
                    print(f"[STT] Calibrating microphone for ambient noise (device={working_device})...")
                    # Clear any stream startup click/pop before calibration
                    try:
                        source.stream.read(source.CHUNK)
                    except Exception:
                        pass
                    self.recognizer.adjust_for_ambient_noise(source, duration=0.35)
                    raw_thresh = self.recognizer.energy_threshold
                    is_bt_mic = any(k in str(dev_name).lower() for k in ("headset", "bluetooth", "p47", "m19", "hands-free", "boult", "airbass"))
                    if is_bt_mic:
                        safe_threshold = max(45.0, min(raw_thresh * 1.1 + 8.0, 240.0))
                    else:
                        safe_threshold = max(65.0, min(raw_thresh * 1.15 + 10.0, 360.0))
                    self.recognizer.energy_threshold = safe_threshold
                    self.recognizer.dynamic_energy_threshold = True
                    self.recognizer.dynamic_energy_adjustment_damping = 0.12
                    self.recognizer.dynamic_energy_ratio = 1.25
                    self.recognizer.pause_threshold = min(self.pause_threshold, 0.45)
                    self.recognizer.phrase_threshold = 0.1
                    self.recognizer.non_speaking_duration = 0.25
                    print(f"[STT] Mic calibrated ({'Bluetooth' if is_bt_mic else 'Standard'}). Raw: {raw_thresh:.1f} -> Fast threshold: {self.recognizer.energy_threshold:.1f}")
                    if self.log_fn:
                        self.log_fn(f"SYS: Voice-to-Text mic calibrated (energy threshold: {self.recognizer.energy_threshold:.0f}, pause: {self.recognizer.pause_threshold:.2f}s).")

                    # Inner continuous capture loop with persistent stream
                    while self._running:
                        # Validate stream still alive
                        if getattr(source, "stream", None) is None:
                            print("[STT] Stream became None mid-session — reconnecting.")
                            break

                        # Keep energy_threshold bounded near the calibrated floor so mic never becomes deaf
                        max_allowed_threshold = max(safe_threshold * 1.8, 420.0)
                        if self.recognizer.energy_threshold > max_allowed_threshold:
                            self.recognizer.energy_threshold = safe_threshold
                        elif self.recognizer.energy_threshold < safe_threshold:
                            self.recognizer.energy_threshold = safe_threshold
                        speaking = bool(self.is_speaking_fn and self.is_speaking_fn())
                        muted = bool(self.is_muted_fn and self.is_muted_fn())

                        # Detect transition from speaking -> finished speaking
                        if self._was_speaking and not speaking:
                            self._cooldown_until = time.time() + self.acoustic_cooldown
                        self._was_speaking = speaking

                        # Skip if muted
                        if muted:
                            time.sleep(0.08)
                            continue

                        # Voice barge-in: If JARVIS is speaking and barge-in is enabled, listen for Sir's speech
                        if speaking:
                            if self.voice_barge_in:
                                try:
                                    audio = self.recognizer.listen(
                                        source,
                                        timeout=0.5,
                                        phrase_time_limit=8.0
                                    )
                                except self.sr.WaitTimeoutError:
                                    continue
                                except Exception:
                                    time.sleep(0.05)
                                    continue

                                text = self.transcribe_audio(audio)
                                if not text:
                                    continue

                                # Filter out self-hearing echo through speakers
                                speaking_text = (self.get_speaking_text_fn() if self.get_speaking_text_fn else "")
                                if self._is_echo(text, speaking_text):
                                    continue

                                # Voice barge-in confirmed!
                                print(f"[STT] ✋ Voice Barge-in triggered by Sir: '{text}'")
                                if self.log_fn:
                                    self.log_fn(f"SYS: Voice interruption triggered ('{text}').")

                                if self.on_interrupt:
                                    try:
                                        self.on_interrupt()
                                    except Exception as err:
                                        print(f"[STT] Error invoking on_interrupt: {err}")

                                t_clean = text.lower().strip().rstrip(".!?")
                                if t_clean in self.INTERRUPT_KEYWORDS:
                                    continue

                                if self.on_command:
                                    try:
                                        self.on_command(text, {"speaker": "Sir", "confidence": 1.0, "full_text": text})
                                    except Exception as err:
                                        print(f"[STT] Error executing barge-in command: {err}")
                            else:
                                time.sleep(0.08)
                            continue

                        if time.time() < self._cooldown_until:
                            time.sleep(0.05)
                            continue

                        try:
                            # Listen on the open stream with 1.5s timeout to remain responsive to mute/speaking
                            audio = self.recognizer.listen(
                                source,
                                timeout=1.5,
                                phrase_time_limit=self.phrase_time_limit
                            )
                        except self.sr.WaitTimeoutError:
                            continue
                        except Exception:
                            if not self._running:
                                break
                            time.sleep(0.1)
                            continue

                        # Double check state after phrase capture completes
                        speaking = bool(self.is_speaking_fn and self.is_speaking_fn())
                        muted = bool(self.is_muted_fn and self.is_muted_fn())
                        if muted or speaking or time.time() < self._cooldown_until:
                            continue

                        print(f"[STT] 🎙️ Speech captured ({len(audio.frame_data)} bytes), recognizing...")

                        # Voice Biometrics verification if enrolled
                        speaker_name = None
                        confidence = 0.0
                        if self.voice_manager and getattr(self.voice_manager, "is_enrolled", lambda: False)():
                            try:
                                raw_pcm = audio.get_raw_data(convert_rate=16000, convert_width=2)
                                audio_np = np.frombuffer(raw_pcm, dtype=np.int16)
                                is_match, conf, spk = self.voice_manager.verify_audio(audio_np)
                                if is_match:
                                    speaker_name = spk
                                    confidence = conf
                                elif spk not in ("silent_or_ambient", "unknown_unregistered"):
                                    speaker_name = "Guest"
                                    confidence = conf
                            except Exception:
                                pass

                        # Convert speech to text with bilingual Tanglish and Tamil support
                        text = self.transcribe_audio(audio)
                        # Reset energy threshold to calibrated baseline to prevent drift
                        self.recognizer.energy_threshold = safe_threshold
                        if not text:
                            print("[STT] 👂 Sound detected, but words were not clear (ambient sound or whisper).")
                            continue

                        if text:
                            print(f"[STT] 🗣 Converted speech to text: '{text}' (speaker: {speaker_name})")

                            is_wake, cmd_part, matched_wake = parse_wake_word(text, self.wake_words)
                            now = time.time()
                            awake = (now < self._wake_active_until)

                            if is_wake:
                                # Wake word detected ('Hey Jarvis', 'Jarvis', 'Hey Jarvius')
                                self._wake_active_until = now + self.wake_timeout
                                print(f"[STT] ⚡ Wake word triggered: '{matched_wake}' (awake for {self.wake_timeout:.1f}s)")
                                if self.on_wake:
                                    try:
                                        self.on_wake(matched_wake, bool(cmd_part), text)
                                    except TypeError:
                                        try:
                                            self.on_wake(matched_wake, bool(cmd_part))
                                        except TypeError:
                                            try:
                                                self.on_wake(matched_wake)
                                            except Exception as err:
                                                print(f"[STT] Error invoking on_wake: {err}")
                                        except Exception as err:
                                            print(f"[STT] Error invoking on_wake: {err}")
                                    except Exception as err:
                                        print(f"[STT] Error invoking on_wake: {err}")

                                if cmd_part:
                                    print(f"[STT] 🚀 Inlined voice command: '{cmd_part}'")
                                    if self.on_command:
                                        try:
                                            self.on_command(cmd_part, {
                                                "speaker": speaker_name,
                                                "confidence": confidence,
                                                "wake_word": matched_wake,
                                                "inlined": True,
                                                "full_text": text,
                                            })
                                        except Exception as err:
                                            print(f"[STT] Error invoking on_command callback: {err}")
                                else:
                                    # User just spoke the wake word ('Hey Jarvis')
                                    if self.log_fn:
                                        self.log_fn(f"SYS: Wake word '{matched_wake}' detected. Listening for command, Sir...")
                            elif awake:
                                # Already awake from previous wake word trigger
                                self._wake_active_until = now + self.wake_timeout
                                print(f"[STT] 🗣 Follow-up command during active window: '{text}'")
                                if self.on_command:
                                    try:
                                        self.on_command(text, {
                                            "speaker": speaker_name,
                                            "confidence": confidence,
                                            "follow_up": True,
                                            "full_text": text,
                                        })
                                    except Exception as err:
                                        print(f"[STT] Error invoking on_command callback: {err}")
                            else:
                                # Not awake and no wake word detected
                                if self.wake_word_enabled and not self.continuous_mode:
                                    print(f"[STT] 💤 Passive mode: ignored '{text}' (say 'Hey Jarvis' to wake)")
                                    continue
                                else:
                                    # Continuous mode without wake requirement
                                    if self.on_command:
                                        try:
                                            self.on_command(text, {
                                                "speaker": speaker_name,
                                                "confidence": confidence
                                            })
                                        except Exception as err:
                                            print(f"[STT] Error invoking on_command callback: {err}")

            except Exception as e:
                if not self._running:
                    break
                err_str = str(e)
                # Suppress the noisy NoneType stream error — it's a known Bluetooth device disconnect
                if "NoneType" in err_str and "close" in err_str:
                    _broken_devices[working_device] = time.time() + _BLACKLIST_SECS
                    print(f"[STT] Device [{working_device}] stream closed unexpectedly — blacklisted for {int(_BLACKLIST_SECS)}s, switching device.")
                else:
                    print(f"[STT] Microphone device stream error: {e}. Reconnecting in 1s...")
                    if self.log_fn:
                        self.log_fn(f"WARN: Mic stream disconnected ({e}), re-opening in 1s...")
                time.sleep(1.0)
