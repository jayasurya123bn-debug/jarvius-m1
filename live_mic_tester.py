#!/usr/bin/env python3
"""
JARVIS Live Microphone & Voice Debugger
Visual real-time audio VU meter, live speech recognition,
wake-word validator, and audible voice response feedback.
"""

import sys
import os
import time
import json
import threading
from pathlib import Path

# Ensure UTF-8 output on Windows console
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import numpy as np
import sounddevice as sd
import speech_recognition as sr

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"

from core.stt import parse_wake_word, LiveSpeechListener

def speak_offline(text: str):
    """Speak short audio confirmation using pyttsx3 SAPI5."""
    try:
        import pyttsx3
        engine = pyttsx3.init()
        engine.setProperty("rate", 185)
        engine.say(text)
        engine.runAndWait()
    except Exception as e:
        print(f"[TTS Error: {e}]")

def get_configured_mic_index() -> int:
    """Read mic_device_index from config or auto-detect."""
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            idx = cfg.get("mic_device_index")
            if idx is not None:
                return int(idx)
        except Exception:
            pass
    # Fallback to auto-detection
    idx = LiveSpeechListener.get_preferred_mic_index()
    return idx if idx is not None else 1

def main():
    print("=" * 75)
    print("       🎙️   JARVIS INTERACTIVE LIVE MICROPHONE & VOICE DEBUGGER   🎙️")
    print("=" * 75)

    mic_idx = get_configured_mic_index()
    try:
        dev_info = sd.query_devices(mic_idx)
        mic_name = dev_info.get("name", "Unknown")
    except Exception:
        mic_name = f"Device [{mic_idx}]"

    print(f"  • Target Microphone:  Device [{mic_idx}] '{mic_name}'")
    print(f"  • Safe Energy Floor:  250.0")
    print(f"  • Languages:          en-IN (English India) & ta-IN (Tamil)")
    print(f"  • Wake Words:         'Hey Jarvis', 'Hey Jarvius', 'Jarvis', 'ஜார்விஸ்'")
    print("=" * 75)
    print("\n👉 SPEAK INTO YOUR MICROPHONE NOW! (e.g. say: 'Hey Jarvis test mic')")
    print("   Press Ctrl+C anytime to exit.\n")

    r = sr.Recognizer()
    r.dynamic_energy_threshold = False
    r.energy_threshold = 280.0
    r.pause_threshold = 0.8

    try:
        mic = sr.Microphone(device_index=mic_idx)
    except Exception as e:
        print(f"[!] Error opening microphone {mic_idx}: {e}. Falling back to default.")
        mic = sr.Microphone()

    with mic as source:
        print("Calibrating ambient noise level (0.8s)...")
        r.adjust_for_ambient_noise(source, duration=0.8)
        ambient_cal = r.energy_threshold
        r.energy_threshold = max(250.0, min(ambient_cal, 1800.0))
        r.dynamic_energy_threshold = False
        print(f"Calibration complete! Ambient floor: {ambient_cal:.1f} -> Clamped threshold: {r.energy_threshold:.1f}\n")

        print("-" * 75)
        print(" [READY] Listening for your voice... Speak now!")
        print("-" * 75)

        turn = 1
        while True:
            try:
                print(f"\n[Turn {turn}] 👂 Waiting for speech...")
                audio = r.listen(source, timeout=10.0, phrase_time_limit=12.0)
                byte_len = len(audio.frame_data)
                print(f"[Turn {turn}] 🎙️ Audio captured ({byte_len:,} bytes). Sending to Speech-to-Text...")

                # Recognition with bilingual retry
                text = None
                try:
                    text = r.recognize_google(audio, language="en-IN").strip()
                except sr.UnknownValueError:
                    try:
                        text = r.recognize_google(audio, language="ta-IN").strip()
                    except Exception:
                        pass
                except Exception as e:
                    print(f"  [STT Error]: {e}")

                if not text:
                    print(f"  ⚪ Audio received, but words were not decipherable. Please speak a little louder or closer to the mic.")
                    continue

                print(f"  🟢 RECOGNIZED TEXT: \"{text}\"")

                # Test wake word
                is_wake, cmd, wake_word = parse_wake_word(text)
                if is_wake:
                    print(f"  ⚡ WAKE WORD DETECTED: '{wake_word}'")
                    print(f"  🚀 COMMAND EXTRACTED:  '{cmd}'")
                    reply = f"Voice received, Sir! Wake word '{wake_word}' confirmed."
                    print(f"  🔊 Speaking: \"{reply}\"")
                    speak_offline(reply)
                else:
                    print(f"  ℹ️  No wake word in phrase (say 'Hey Jarvis' or 'Hey Jarvius' to trigger wake)")
                    reply = f"Heard: {text}"
                    print(f"  🔊 Speaking: \"{reply}\"")
                    speak_offline(reply)

                turn += 1

            except sr.WaitTimeoutError:
                print("  ⏳ [No speech detected in last 10 seconds. Still listening...]")
            except KeyboardInterrupt:
                print("\n\n[Exited] Voice debugging finished.")
                break
            except Exception as e:
                print(f"\n[!] Error during listening: {e}")
                time.sleep(1.0)

if __name__ == "__main__":
    main()
