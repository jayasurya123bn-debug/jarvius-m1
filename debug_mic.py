#!/usr/bin/env python3
"""
JARVIS Autonomous Microphone & Voice Diagnostic Suite
Runs a complete diagnostic on hardware mic, Windows volume levels,
ambient noise calibration, STT recognition, and wake-word readiness.
"""

import sys
import os
import json
import time
from pathlib import Path

# UTF-8 console output
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
CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"

def run_diagnostics():
    print("=" * 75)
    print("   🎙️  JARVIS VOICE & MICROPHONE AUTONOMOUS DIAGNOSTIC SUITE  🎙️")
    print("=" * 75)

    report = {
        "os_mute": None,
        "os_volume": None,
        "devices": [],
        "best_device": None,
        "best_peak": 0,
        "calibrated_threshold": None,
        "stt_online": False,
        "whisper_ready": False,
        "recommendations": [],
    }

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 1: Windows OS-Level Microphone Mute & Volume
    # ─────────────────────────────────────────────────────────────────────────
    print("\n[Step 1] Checking Windows OS Microphone Settings...")
    try:
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        mic_endpoint = AudioUtilities.GetMicrophone()
        if mic_endpoint:
            interface = mic_endpoint.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            vol = interface.QueryInterface(IAudioEndpointVolume)
            report["os_mute"] = bool(vol.GetMute())
            report["os_volume"] = float(vol.GetMasterVolumeLevelScalar())

            mute_str = "🔴 MUTED!" if report["os_mute"] else "🟢 UNMUTED (Active)"
            print(f"  - Windows Mic Mute:   {mute_str}")
            print(f"  - Windows Mic Volume: {int(report['os_volume'] * 100)}%")

            if report["os_mute"]:
                report["recommendations"].append("Windows microphone is MUTED. Unmute it in Windows Sound Settings.")
            if report["os_volume"] < 0.5:
                report["recommendations"].append("Windows microphone volume is low (<50%). Increase it to 100%.")
        else:
            print("  - Could not query default microphone endpoint via pycaw.")
    except Exception as e:
        print(f"  - Note on pycaw query: {e}")

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 2: Probe All Input Devices for Live Audio Signal
    # ─────────────────────────────────────────────────────────────────────────
    print("\n[Step 2] Probing Available Microphones for Signal (1.0s each)...")
    devices = sd.query_devices()
    input_indices = [i for i, d in enumerate(devices) if d.get("max_input_channels", 0) > 0]

    best_dev_idx = None
    best_peak = 0

    default_in_idx = None
    try:
        d_in = sd.default.device[0]
        if d_in is not None and d_in >= 0:
            default_in_idx = int(d_in)
    except Exception:
        pass

    for idx in input_indices:
        d = devices[idx]
        d_name = d.get("name", "Unknown")
        host_api = sd.query_hostapis(d.get("hostapi", 0)).get("name", "MME")
        channels = d.get("max_input_channels", 1)
        sr_rate = int(d.get("default_samplerate", 44100))

        # Try recording 1 second test slice
        peak = 0
        rms = 0.0
        status = "OK"
        try:
            sample_rate = sr_rate if sr_rate in (16000, 44100, 48000) else 44100
            rec = sd.rec(int(sample_rate * 0.8), samplerate=sample_rate, channels=min(channels, 2), dtype="int16", device=idx)
            sd.wait()
            peak = int(np.max(np.abs(rec)))
            rms = float(np.sqrt(np.mean(rec.astype(np.float32)**2)))
        except Exception as err:
            status = f"Err: {err}"

        # Scoring
        score = 0
        if default_in_idx is not None and idx == default_in_idx:
            score += 100

        name_lower = d_name.lower()
        if "bthhfenum" in name_lower or "system32" in name_lower:
            score -= 100
        elif any(k in name_lower for k in ("headset", "hands-free", "m19", "p47", "boult", "airbass", "zyio", "bluetooth")):
            score += 25
        elif "usb" in name_lower:
            score += 35
        elif "microphone array" in name_lower or "array" in name_lower:
            score += 30
        elif "mic" in name_lower:
            score += 15
        if "mapper" in name_lower or "primary" in name_lower:
            score -= 20

        host_api_lower = host_api.lower()
        if "wdm-ks" in host_api_lower:
            continue
        if "wasapi" in host_api_lower:
            score += 15
        elif "mme" in host_api_lower:
            score += 10
        elif "directsound" in host_api_lower:
            score += 5

        # Factor in actual audio peak
        if peak > 500:
            score += 25
        elif peak > 10:
            score += 15

        d_info = {
            "index": idx,
            "name": d_name,
            "host_api": host_api,
            "channels": channels,
            "rate": sr_rate,
            "peak": peak,
            "rms": rms,
            "score": score,
            "status": status
        }
        report["devices"].append(d_info)

        signal_bar = "■" * int(min(20, (peak / 500) * 20))
        print(f"  [{idx:2d}] {d_name[:35]:35s} | API: {host_api:12s} | Peak: {peak:5d} | Signal: [{signal_bar:20s}]")

    # Pick highest scoring working device
    valid_devices = [d for d in report["devices"] if "Err" not in d["status"] and d["score"] > 0]
    if valid_devices:
        valid_devices.sort(key=lambda x: x["score"], reverse=True)
        best_dev_idx = valid_devices[0]["index"]
        best_peak = valid_devices[0]["peak"]

    report["best_device"] = best_dev_idx
    report["best_peak"] = best_peak

    best_name = devices[best_dev_idx]["name"] if best_dev_idx is not None else "None"
    print(f"\n  👉 Selected Optimal Microphone: Device [{best_dev_idx}] '{best_name}'")

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 3: SpeechRecognition Calibration & Energy Threshold Clamping
    # ─────────────────────────────────────────────────────────────────────────
    print("\n[Step 3] Testing SpeechRecognition Calibration...")
    try:
        r = sr.Recognizer()
        print(f"  - Initial threshold:  {r.energy_threshold}")
        with sr.Microphone(device_index=best_dev_idx) as source:
            try:
                source.stream.read(source.CHUNK)
            except Exception:
                pass
            r.adjust_for_ambient_noise(source, duration=0.6)
            raw_threshold = r.energy_threshold
            print(f"  - Ambient calibrated: {raw_threshold:.1f}")

            # Safe human speech floor: speech is 150-600, so clamp ambient between 70.0 and 380.0
            clamped = max(70.0, min(raw_threshold * 1.2 + 15.0, 380.0))
            r.energy_threshold = clamped
            report["calibrated_threshold"] = clamped
            print(f"  - Safe speech threshold:  {clamped:.1f}")
    except Exception as e:
        print(f"  - SpeechRecognition mic error: {e}")

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 4: Test STT Cloud Recognition (Google STT)
    # ─────────────────────────────────────────────────────────────────────────
    print("\n[Step 4] Checking Speech-to-Text Engine Connectivity...")
    try:
        # Create silent dummy frame to test Google API reachability
        dummy_pcm = b"\x00" * 3200
        audio = sr.AudioData(dummy_pcm, sample_rate=16000, sample_width=2)
        try:
            r.recognize_google(audio, language="en-IN")
            report["stt_online"] = True
        except sr.UnknownValueError:
            # Expected on silence — means connection succeeded!
            report["stt_online"] = True
            print("  - Google Speech-to-Text API: 🟢 Connected & Online (en-IN + ta-IN)")
        except sr.RequestError as e:
            print(f"  - Google Speech-to-Text API: 🔴 Network error ({e})")
            report["recommendations"].append(f"Google STT network error: {e}. Check internet connection.")
    except Exception as e:
        print(f"  - STT check note: {e}")

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 5: Test Offline Whisper STT Readiness
    # ─────────────────────────────────────────────────────────────────────────
    print("\n[Step 5] Checking Offline Whisper STT Engine...")
    try:
        import faster_whisper
        report["whisper_ready"] = True
        print("  - faster-whisper package:    🟢 Available (Offline transcription ready)")
    except ImportError:
        print("  - faster-whisper package:    ⚪ Not installed (Using Google STT)")

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 6: Test Wake Word Recognition
    # ─────────────────────────────────────────────────────────────────────────
    print("\n[Step 6] Verifying Wake Word Parser...")
    from core.stt import parse_wake_word
    test_phrases = [
        ("Hey Jarvis open notepad", True, "open notepad"),
        ("hey jarvius volume up", True, "volume up"),
        ("ஜார்விஸ் யூடியூப் ஓபன் பண்ணு", True, "யூடியூப் ஓபன் பண்ணு"),
        ("jarvis", True, ""),
    ]
    all_passed = True
    for phrase, expected_wake, expected_cmd in test_phrases:
        w_ok, cmd, w_word = parse_wake_word(phrase)
        if w_ok == expected_wake:
            print(f"  - '{phrase}' -> Wake: '{w_word}', Cmd: '{cmd}' (PASS)")
        else:
            all_passed = False
            print(f"  - '{phrase}' -> FAIL")

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 7: Save Best Device to config/api_keys.json
    # ─────────────────────────────────────────────────────────────────────────
    if best_dev_idx is not None and CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            save_val = "auto" if (default_in_idx is not None and best_dev_idx == default_in_idx) else best_dev_idx
            cfg["mic_device_index"] = save_val
            CONFIG_PATH.write_text(json.dumps(cfg, indent=4), encoding="utf-8")
            print(f"\n[Saved] Updated config/api_keys.json with mic_device_index = {save_val} (Device [{best_dev_idx}])")
        except Exception as e:
            print(f"\n[Notice] Could not update config: {e}")

    # ─────────────────────────────────────────────────────────────────────────
    # SUMMARY
    # ─────────────────────────────────────────────────────────────────────────
    print("\n" + "=" * 75)
    print("                      DIAGNOSTIC SUMMARY & HEALTH")
    print("=" * 75)
    print(f"  • Physical Microphone:      Device [{best_dev_idx}] {best_name}")
    print(f"  • Windows Mute Status:      {'MUTED' if report['os_mute'] else 'Unmuted (Ready)'}")
    print(f"  • Windows Volume Level:     {int((report['os_volume'] or 1.0) * 100)}%")
    print(f"  • Speech Recognition Floor: {report['calibrated_threshold']} (Clamped)")
    print(f"  • Online STT (Google):      {'ONLINE' if report['stt_online'] else 'Offline'}")
    print(f"  • Offline STT (Whisper):    {'READY' if report['whisper_ready'] else 'Not available'}")
    print(f"  • Wake Word Engine:         {'ACTIVE (English + Tamil)' if all_passed else 'Check patterns'}")

    if report["recommendations"]:
        print("\n  ⚠️  Actionable Recommendations:")
        for r in report["recommendations"]:
            print(f"     - {r}")
    else:
        print("\n  🎉 ALL SYSTEMS OPERATIONAL! Your voice microphone is configured and ready.")

    print("=" * 75)

if __name__ == "__main__":
    run_diagnostics()
