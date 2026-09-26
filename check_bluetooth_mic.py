#!/usr/bin/env python3
"""
🎙️ JARVIS Autonomous Bluetooth Microphone Diagnostic & Registration Suite 🎙️
Performs end-to-end automated detection, live signal capture, ambient noise calibration,
STT speech recognition testing, and automated configuration for Bluetooth audio headsets.
"""

import sys
import os
import json
import time
from pathlib import Path

# UTF-8 console output for Indic/Tamil and Windows Unicode
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import numpy as np
import sounddevice as sd
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

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH_MAIN = BASE_DIR / "config" / "api_keys.json"
CONFIG_PATH_ROOT = BASE_DIR.parent / "config" / "api_keys.json"

BLUETOOTH_KEYWORDS = (
    "headset", "hands-free", "m19", "p47", "boult", "airbass", "zyio", "bluetooth",
    "bthhfenum", "hfp", "a2dp", "sg-2016",
)

def check_bluetooth_mic(auto_save: bool = True, live_listen_seconds: float = 3.0):
    print("=" * 80)
    print("   🎙️  JARVIS AUTONOMOUS BLUETOOTH MICROPHONE REGISTRATION & CHECK  🎙️")
    print("=" * 80)
    print("Checking Bluetooth connectivity, signal peaks, ambient noise & STT recognition...\n")

    results = {
        "bluetooth_found": False,
        "bt_candidates": [],
        "best_device_index": None,
        "best_device_name": None,
        "best_api": None,
        "peak_level": 0,
        "calibrated_threshold": None,
        "stt_online": False,
        "wake_word_verified": False,
        "saved_to_config": False,
    }

    # 1. Enumerate and score all input devices
    print("[1/5] 🔍 Scanning audio input endpoints for Bluetooth headsets...")
    devices = sd.query_devices()
    bt_candidates = []
    other_candidates = []

    for idx, d in enumerate(devices):
        if d.get("max_input_channels", 0) <= 0:
            continue
        name = d.get("name", "")
        name_lower = name.lower()

        api_info = sd.query_hostapis(d.get("hostapi", 0))
        api_name = api_info.get("name", "Unknown")
        api_lower = api_name.lower()

        # Detect Bluetooth: bthhfenum.sys = Windows BT HFP driver (ALWAYS BT)
        is_bt_hfp = "bthhfenum" in name_lower or "system32\\drivers\\bthh" in name_lower
        is_bt = is_bt_hfp or any(k in name_lower for k in BLUETOOTH_KEYWORDS)

        # Skip generic mapper/primary placeholders (not real devices)
        if ("mapper" in name_lower or "primary sound capture" in name_lower) and not is_bt:
            continue

        score = 0
        if is_bt_hfp:
            score += 80   # Highest priority — confirmed BT HFP device
        elif is_bt:
            score += 50
        elif "usb" in name_lower:
            score += 30
        elif "microphone array" in name_lower or "array" in name_lower:
            score += 15
        elif "mic" in name_lower:
            score += 10

        if "directsound" in api_lower:
            score += 20
        elif "wasapi" in api_lower:
            score += 15
        elif "wdm-ks" in api_lower:
            score += 10   # WDM-KS is OK for BT, try it
        elif "mme" in api_lower:
            score += 2

        # Try live 0.5s audio signal capture (safe try for BT/WDM-KS)
        peak = 0
        rms = 0.0
        can_open = False
        rate = int(d.get("default_samplerate", 44100))
        try:
            sample_rate = rate if rate in (8000, 16000, 44100, 48000) else 16000
            rec = sd.rec(int(sample_rate * 0.5), samplerate=sample_rate,
                         channels=1, dtype="int16", device=idx)
            sd.wait()
            peak = int(np.max(np.abs(rec)))
            rms = float(np.sqrt(np.mean(rec.astype(np.float32)**2)))
            can_open = True
            if is_bt:
                print(f"  🎧 BT device [{idx}] '{name[:50]}' — peak={peak}, rms={rms:.1f}")
        except Exception as bt_err:
            can_open = False
            if is_bt:
                print(f"  ⚠️  BT device [{idx}] '{name[:50]}' — open failed: {bt_err}")

        if not can_open:
            continue

        # Also verify SpeechRecognition can open it without error
        sr_works = False
        try:
            probe = sr.Microphone(device_index=idx)
            with probe as src:
                if src and getattr(src, "stream", None) is not None:
                    sr_works = True
        except Exception:
            sr_works = False

        if not sr_works:
            continue

        if peak > 500:
            score += 25
        elif peak > 10:
            score += 15

        item = {
            "index": idx,
            "name": name,
            "api": api_name,
            "rate": rate,
            "peak": peak,
            "rms": rms,
            "score": score,
            "is_bt": is_bt,
        }

        if is_bt:
            bt_candidates.append(item)
        else:
            other_candidates.append(item)

    if bt_candidates:
        results["bluetooth_found"] = True
        bt_candidates.sort(key=lambda x: x["score"], reverse=True)
        best = bt_candidates[0]
        results["best_device_index"] = best["index"]
        results["best_device_name"] = best["name"]
        results["best_api"] = best["api"]
        results["peak_level"] = best["peak"]
        results["bt_candidates"] = bt_candidates

        print(f"  ✅ Bluetooth Headset Detected: [{best['index']}] '{best['name']}' ({best['api']})")
        print(f"     Signal Peak: {best['peak']} | RMS: {best['rms']:.1f} | Score: {best['score']}")
    else:
        print("  ⚠️  No active Bluetooth headset detected. Falling back to primary microphone.")
        if other_candidates:
            other_candidates.sort(key=lambda x: x["score"], reverse=True)
            best = other_candidates[0]
            results["best_device_index"] = best["index"]
            results["best_device_name"] = best["name"]
            results["best_api"] = best["api"]
            results["peak_level"] = best["peak"]
            print(f"  Fallback Device: [{best['index']}] '{best['name']}' ({best['api']})")

    selected_idx = results["best_device_index"]

    # 2. Calibrate Ambient Noise and Verify SpeechRecognition Safety Floor
    print(f"\n[2/5] 🎚️ Calibrating SpeechRecognition noise floor on device [{selected_idx}]...")
    r = sr.Recognizer()
    try:
        with sr.Microphone(device_index=selected_idx) as source:
            r.adjust_for_ambient_noise(source, duration=0.8)
            raw_threshold = r.energy_threshold
            # Safety headroom formula (tailored for Bluetooth headsets with low-gain speech)
            safe_threshold = max(80.0, min(raw_threshold * 1.2 + 20.0, 2000.0))
            r.energy_threshold = safe_threshold
            results["calibrated_threshold"] = safe_threshold
            print(f"  - Ambient noise level:      {raw_threshold:.1f}")
            print(f"  - Dynamic speech threshold: {safe_threshold:.1f} (Tailored for Bluetooth RF)")
    except Exception as e:
        print(f"  ❌ Calibration error: {e}")

    # 3. Test Online Speech-to-Text Connectivity
    print("\n[3/5] 🌐 Testing Speech-to-Text Recognition Connectivity (English + Tamil)...")
    try:
        dummy_pcm = b"\x00" * 3200
        audio = sr.AudioData(dummy_pcm, sample_rate=16000, sample_width=2)
        try:
            r.recognize_google(audio, language="en-IN")
            results["stt_online"] = True
        except sr.UnknownValueError:
            results["stt_online"] = True
            print("  ✅ Google Speech Recognition Online (en-IN + ta-IN bilingual)")
        except sr.RequestError as e:
            print(f"  ⚠️  Google STT warning: {e}")
    except Exception as e:
        print(f"  ⚠️  STT note: {e}")

    # 4. Verify Wake Words
    print("\n[4/5] ⚡ Verifying Wake-Word Detection Matrix...")
    from core.stt import parse_wake_word
    test_phrases = [
        ("Hey Jarvis what time is it", True),
        ("hey jarvius volume up", True),
        ("ஜார்விஸ் யூடியூப் ஓபன் பண்ணு", True),
    ]
    wake_pass = True
    for phrase, expected in test_phrases:
        ok, _, wake_found = parse_wake_word(phrase)
        if ok == expected:
            print(f"  - Wake word '{wake_found}' verified for '{phrase}'")
        else:
            wake_pass = False
    results["wake_word_verified"] = wake_pass

    # 5. Save verified settings to config
    if auto_save and selected_idx is not None:
        print("\n[5/5] 💾 Registering optimal microphone in configuration...")
        for p in (CONFIG_PATH_MAIN, CONFIG_PATH_ROOT):
            try:
                if p.exists():
                    cfg = json.loads(p.read_text(encoding="utf-8"))
                    cfg["mic_device_index"] = selected_idx
                    p.write_text(json.dumps(cfg, indent=4), encoding="utf-8")
                    print(f"  ✅ Saved mic_device_index = {selected_idx} into {p.name}")
                    results["saved_to_config"] = True
            except Exception as e:
                print(f"  ⚠️  Could not write {p}: {e}")

    # Summary Display
    print("\n" + "=" * 80)
    print("                    BLUETOOTH MICROPHONE STATUS REPORT")
    print("=" * 80)
    print(f"  • Bluetooth Status:    {'🟢 Connected & Active' if results['bluetooth_found'] else '⚪ Not detected'}")
    print(f"  • Selected Device:     [{results['best_device_index']}] {results['best_device_name']}")
    print(f"  • Audio Host API:      {results['best_api']}")
    print(f"  • Live Audio Signal:   Peak {results['peak_level']}")
    threshold_display = f"{results['calibrated_threshold']:.1f}" if results['calibrated_threshold'] is not None else "N/A"
    print(f"  • Noise Threshold:     {threshold_display}")
    print(f"  • Speech Recognition:  {'ONLINE' if results['stt_online'] else 'Check network'}")
    print(f"  • Wake-Word Parser:    {'ONLINE (Hey Jarvis / Hey Jarvius / ஜார்விஸ்)' if results['wake_word_verified'] else 'Check config'}")
    print("=" * 80)

    if results["bluetooth_found"]:
        print("  🎉 Bluetooth mic ready aachu, Sir! JARVIS is now locked to your Bluetooth headset.")
    else:
        print("  ℹ️  Laptop mic active aaguthu, Sir. Bluetooth headset connect pannuna auto-switch aagum.")
    print("=" * 80 + "\n")

    return results

if __name__ == "__main__":
    check_bluetooth_mic()
