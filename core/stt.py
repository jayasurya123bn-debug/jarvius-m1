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
]

_WAKE_REGEX = re.compile(
    r"^\s*(?:(?:hey|hi|hello|ok|okay|yo|a|eh)\s+)?(?:jarvis|jarvius|jarviz|jarves|jaavis|jarwiss?|charvis)\b[\s,:\-–]*(.*)$",
    re.IGNORECASE
)

_EMBEDDED_WAKE_REGEX = re.compile(
    r"\b(?:(?:hey|hi|hello|ok|okay|yo|a|eh)\s+)?(?:jarvis|jarvius|jarviz|jarves|jaavis|jarwiss?|charvis)\b",
    re.IGNORECASE
)


def parse_wake_word(text: str, custom_wake_words: list[str] | None = None) -> tuple[bool, str, str]:
    """
    Check if speech input begins with or contains a wake word (e.g. 'Hey Jarvis', 'Jarvis', 'Hey Jarvius').
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

    def __init__(
        self,
        device_index: int | None = None,
        language: str = "en-IN",
        pause_threshold: float = 0.8,
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
        self.device_index = device_index
        self.language = language or "en-IN"
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
        self.recognizer.phrase_threshold = 0.3
        self.recognizer.non_speaking_duration = 0.5

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

        # Outer reconnection loop for device resilience
        while self._running:
            try:
                mic = self.sr.Microphone(device_index=self.device_index)
            except Exception as e:
                print(f"[STT] Failed to create microphone source (device={self.device_index}): {e}")
                if self.log_fn:
                    self.log_fn(f"ERR: Voice-to-Text mic init error: {e}")
                try:
                    mic = self.sr.Microphone()
                except Exception as e2:
                    print(f"[STT] Default microphone also failed: {e2}")
                    time.sleep(2.0)
                    continue

            try:
                with mic as source:
                    self.microphone = source
                    print(f"[STT] Calibrating microphone for ambient noise (device={self.device_index})...")
                    self.recognizer.adjust_for_ambient_noise(source, duration=0.8)
                    print(f"[STT] Mic calibrated. Energy threshold: {self.recognizer.energy_threshold:.1f}")
                    if self.log_fn:
                        self.log_fn("SYS: Voice-to-Text mic calibrated and active.")

                    # Inner continuous capture loop with persistent stream
                    while self._running:
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

                                try:
                                    text = self.recognizer.recognize_google(audio, language=self.language).strip()
                                except Exception:
                                    continue

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
                                        self.on_command(text, {"speaker": "Sir", "confidence": 1.0})
                                    except Exception as err:
                                        print(f"[STT] Error executing barge-in command: {err}")
                            else:
                                time.sleep(0.08)
                            continue

                        if time.time() < self._cooldown_until:
                            time.sleep(0.05)
                            continue

                        try:
                            # Listen on the open stream with 1.0s timeout to remain responsive to mute/speaking
                            audio = self.recognizer.listen(
                                source,
                                timeout=1.0,
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

                        # Convert speech to text
                        text = None
                        try:
                            text = self.recognizer.recognize_google(audio, language=self.language).strip()
                        except self.sr.UnknownValueError:
                            print("[STT] 👂 Sound detected, but words were not clear (ambient sound or whisper).")
                            continue
                        except self.sr.RequestError as e:
                            # Retry with en-US if specific locale failed
                            if self.language != "en-US":
                                try:
                                    text = self.recognizer.recognize_google(audio, language="en-US").strip()
                                except Exception:
                                    text = None
                            if not text:
                                print(f"[STT] Recognition service error: {e}")
                                if self.log_fn:
                                    self.log_fn(f"ERR: STT network error: {e}")
                                continue
                        except Exception as e:
                            print(f"[STT] Speech conversion exception: {e}")
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
                                        self.on_wake(matched_wake, bool(cmd_part))
                                    except TypeError:
                                        try:
                                            self.on_wake(matched_wake)
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
                print(f"[STT] Microphone device stream error: {e}. Reconnecting in 1s...")
                if self.log_fn:
                    self.log_fn(f"WARN: Mic stream disconnected ({e}), re-opening in 1s...")
                time.sleep(1.0)


