#!/usr/bin/env python3
"""
JARVIS Real-Time Interactive Live Microphone & Voice Tester
Features:
- Live bouncing ASCII VU meter updating in real-time.
- Instant feedback if hardware microphone is muted (Fn+F4 on Lenovo).
- Automatic speech segmentation and Google STT recognition.
- Wake word validation ('Hey Jarvis', 'Hey Jarvius', 'ஜார்விஸ்').
- Audio speech confirmation back via speakers.
"""

import sys
import os
import time
import json
from pathlib import Path

# Ensure UTF-8 console output on Windows
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

from core.stt import parse_wake_word, select_bilingual_result

def speak_offline(text: str):
    """Audible response via pyttsx3 SAPI5."""
    try:
        import pyttsx3
        engine = pyttsx3.init()
        engine.setProperty("rate", 185)
        engine.say(text)
        engine.runAndWait()
    except Exception:
        pass

def get_best_input_device():
    """Find the best input device: prioritize active Windows default recording device, then USB, then Bluetooth."""
    devices = sd.query_devices()
    default_in = None
    try:
        d_in = sd.default.device[0]
        if d_in is not None and d_in >= 0:
            default_in = int(d_in)
    except Exception:
        pass

    ranked = []
    for i, d in enumerate(devices):
        if d.get("max_input_channels", 0) > 0:
            name = d.get("name", "").lower()
            if "bthhfenum" in name or "system32" in name:
                continue
            score = 0
            if default_in is not None and i == default_in:
                score += 100
            if any(k in name for k in ("headset", "hands-free", "m19", "p47", "boult", "airbass", "zyio", "bluetooth")):
                score += 25
            elif "usb" in name:
                score += 35
            elif "microphone array" in name or "array" in name:
                score += 30
            elif "mic" in name:
                score += 15
            if "mapper" in name or "primary" in name:
                score -= 20

            api_name = sd.query_hostapis(d.get("hostapi", 0)).get("name", "").lower()
            if "wasapi" in api_name:
                score += 15
            elif "mme" in api_name:
                score += 10
            elif "directsound" in api_name:
                score += 5

            ranked.append((score, i, d["name"]))

    ranked.sort(key=lambda x: x[0], reverse=True)
    if ranked and ranked[0][0] > 0:
        return ranked[0][1], ranked[0][2]

    default_idx = sd.default.device[0]
    if default_idx is not None and default_idx >= 0:
        return default_idx, devices[default_idx]["name"]
    return 1, "Microphone Array"

def main():
    print("=" * 75)
    print("      🎙️   JARVIS REAL-TIME MICROPHONE & VOICE LIVE TESTER   🎙️")
    print("=" * 75)

    dev_idx, dev_name = get_best_input_device()
    sample_rate = 16000
    chunk_size = 1024

    print(f"  • Selected Device: [{dev_idx}] {dev_name}")
    print(f"  • Sample Rate:     {sample_rate} Hz Mono")
    print(f"  • Wake Words:      'Hey Jarvis', 'Hey Jarvius', 'Jarvis', 'ஜார்விஸ்'")
    print("=" * 75)
    print("\n[INSTRUCTIONS]")
    print("  1. Watch the live audio meter [■■■■■■░░░░] below.")
    print("  2. Speak into your microphone (e.g. 'Hey Jarvis test voice').")
    print("  3. If meter stays at 0, check [Fn + F4] on your Lenovo keyboard to unmute.")
    print("  4. Press Ctrl+C anytime to exit.\n")

    r = sr.Recognizer()

    try:
        stream = sd.InputStream(
            device=dev_idx,
            channels=1,
            samplerate=sample_rate,
            dtype="int16",
            blocksize=chunk_size
        )
        stream.start()
    except Exception as e:
        print(f"[!] Error opening audio device {dev_idx}: {e}")
        try:
            stream = sd.InputStream(
                channels=1,
                samplerate=sample_rate,
                dtype="int16",
                blocksize=chunk_size
            )
            stream.start()
            print("  -> Fallen back to system default input stream.")
        except Exception as e2:
            print(f"[FATAL] Could not open audio input: {e2}")
            return

    speech_buffer = []
    is_speaking = False
    silence_chunks = 0
    max_silence_chunks = int(0.7 * (sample_rate / chunk_size))  # ~0.7s silence to finish phrase
    min_speech_chunks = int(0.4 * (sample_rate / chunk_size))   # min ~0.4s to qualify as phrase

    SPEECH_THRESHOLD = 200  # RMS threshold for speech trigger
    muted_warned = False
    zero_count = 0

    try:
        while True:
            data, overflowed = stream.read(chunk_size)
            audio_np = data.flatten()
            peak = int(np.max(np.abs(audio_np)))
            rms = float(np.sqrt(np.mean(audio_np.astype(np.float32)**2)))

            # Track zero signal
            if peak <= 1:
                zero_count += 1
            else:
                zero_count = 0
                muted_warned = False

            # Draw visual meter
            bars = "■" * min(25, int((rms / 500) * 25))
            status = "🟢 SPEAKING" if is_speaking else ("👂 LISTENING" if peak > 5 else "🔴 ZERO/MUTED")
            meter_line = f"\r[Signal: {bars:25s}] Peak:{peak:5d} | RMS:{int(rms):4d} | Status: {status}   "
            sys.stdout.write(meter_line)
            sys.stdout.flush()

            # Hardware mute alert if flatline zeros for 3 seconds
            if zero_count > 45 and not muted_warned:
                muted_warned = True
                print("\n\n" + "-" * 75)
                print("  ⚠️  ATTENTION: Microphone signal is strictly ZERO (Flatline)!")
                print("  Your Lenovo laptop microphone is currently HARDWARE MUTED.")
                print("  👉 Look at your keyboard: Press [Fn + F4] (or [F4]) to toggle the mic!")
                print("  👉 Make sure the orange light on the F4 key is OFF.")
                print("  👉 Or check the 'Recording' sound window on your screen.")
                print("-" * 75 + "\n")

            # Speech start detection
            if rms >= SPEECH_THRESHOLD:
                if not is_speaking:
                    is_speaking = True
                    speech_buffer = []
                speech_buffer.append(audio_np.tobytes())
                silence_chunks = 0
            elif is_speaking:
                speech_buffer.append(audio_np.tobytes())
                silence_chunks += 1

                if silence_chunks >= max_silence_chunks:
                    # Phrase finished!
                    is_speaking = False
                    if len(speech_buffer) >= min_speech_chunks:
                        raw_bytes = b"".join(speech_buffer)
                        speech_buffer = []
                        print("\n\n" + "=" * 60)
                        print(f"  🎙️ Captured phrase ({len(raw_bytes):,} bytes). Transcribing...")
                        print("=" * 60)

                        audio_data = sr.AudioData(raw_bytes, sample_rate, 2)
                        text = None
                        try:
                            import concurrent.futures

                            def _rec(lang: str):
                                try:
                                    return r.recognize_google(audio_data, language=lang).strip()
                                except Exception:
                                    return None

                            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                                f_ta = executor.submit(_rec, "ta-IN")
                                f_en = executor.submit(_rec, "en-IN")
                                res_ta = f_ta.result(timeout=10.0)
                                res_en = f_en.result(timeout=10.0)

                            text = select_bilingual_result(res_ta, res_en)
                        except Exception as err:
                            print(f"  [STT Network error]: {err}")

                        if text:
                            print(f"\n  🗣 HEARD: \"{text}\"")
                            is_wake, cmd, wake_word = parse_wake_word(text)
                            if is_wake:
                                print(f"  ⚡ WAKE WORD: '{wake_word}' (MATCHED!)")
                                print(f"  🚀 COMMAND:   '{cmd}'")
                                ans = f"Yes Sir, I heard {text}."
                            else:
                                print(f"  ℹ️ (No wake word detected. Say 'Hey Jarvis' or 'Hey Jarvius')")
                                ans = f"I heard: {text}"
                            print(f"  🔊 Speaking: \"{ans}\"")
                            speak_offline(ans)
                        else:
                            print("  ⚪ Sound detected, but words were not clear. Try speaking louder or closer.")

                        print("\n" + "-" * 60)
                        print(" [READY] Listening again... Speak anytime!")
                        print("-" * 60 + "\n")

    except KeyboardInterrupt:
        print("\n\n[Exited] Live mic tester stopped.")
    finally:
        try:
            stream.stop()
            stream.close()
        except Exception:
            pass

if __name__ == "__main__":
    main()
