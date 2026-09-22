#!/usr/bin/env python3
"""
JARVIS Interactive Voice Learning & Speaker Enrollment Wizard
Project VoicePrint - Calibrates and trains JARVIS to recognize your unique voice.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# Safe streams on Windows consoles
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import numpy as np
import sounddevice as sd

from core.voice_profile import VoiceProfileManager

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"
ROOT_CONFIG_PATH = BASE_DIR.parent / "config" / "api_keys.json"
SAMPLE_RATE = 16000
CHANNELS = 1


def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")


def print_banner():
    banner = r"""
==============================================================================
   ___  ___  ______ _   _ _____ _____   _   _ _____ _____ _____ _____ 
  |_  |/ _ \ | ___ \ | | |_   _/  ___| | | | |  _  |_   _/  __ \  ___|
    | / /_\ \| |_/ / | | | | | \ `--.  | | | | | | | | | | /  \/ |__  
    | |  _  ||    /| | | | | |  `--. \ | | | | | | | | | | |   |  __| 
/\__/ / | | || |\ \\ \_/ /_| |_/\__/ / \ \_/ /\ \_/ /_| |_| \__/\ |___ 
\____/\_| |_/\_| \_|\___/ \___/\____/   \___/  \___/ \___/ \____/\____/
                     PROJECT VOICEPRINT - BIOMETRICS                     
==============================================================================
"""
    print(banner)


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_config(cfg: dict) -> None:
    txt = json.dumps(cfg, indent=4)
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(txt, encoding="utf-8")
    except Exception as e:
        print(f"[WARN] Could not update local config: {e}")

    if ROOT_CONFIG_PATH.parent.exists():
        try:
            ROOT_CONFIG_PATH.write_text(txt, encoding="utf-8")
        except Exception:
            pass


def record_audio(duration_s: float, prompt_text: str = "", device = None) -> np.ndarray:
    """Record audio from specified or default input device for duration_s seconds."""
    if prompt_text:
        print(f"\n>> {prompt_text}")
    print(f"[*] Recording in: 3...", end="", flush=True)
    time.sleep(0.6)
    print(" 2...", end="", flush=True)
    time.sleep(0.6)
    print(" 1...", end="", flush=True)
    time.sleep(0.6)
    print(" [SPEAK NOW!]", flush=True)

    num_samples = int(duration_s * SAMPLE_RATE)
    recording = sd.rec(num_samples, samplerate=SAMPLE_RATE, channels=CHANNELS, dtype="float32", device=device)

    # Render a progress bar while recording
    start = time.time()
    while time.time() - start < duration_s:
        elapsed = time.time() - start
        pct = min(1.0, elapsed / duration_s)
        bar_len = 30
        filled = int(round(pct * bar_len))
        bar = "#" * filled + "-" * (bar_len - filled)
        sys.stdout.write(f"\r    Recording: [{bar}] {elapsed:.1f}s / {duration_s:.1f}s")
        sys.stdout.flush()
        time.sleep(0.08)

    sd.wait()
    sys.stdout.write(f"\r    Recording: [{'#' * 30}] Done!                         \n")
    sys.stdout.flush()

    return recording.flatten()


def main():
    if "--test" in sys.argv:
        print("[TEST MODE] Validating Voice Learning Wizard pipeline...")
        sr = 16000
        t = np.linspace(0, 2.0, int(sr * 2.0), endpoint=False)
        def _synth(f0, formants):
            s = np.zeros_like(t)
            for h in range(1, 15):
                freq = f0 * h
                if freq < sr / 2:
                    w = sum(np.exp(-((freq - f) ** 2) / (2 * 120 ** 2)) for f in formants) + 0.1
                    s += (w / h) * np.sin(2 * np.pi * freq * t)
            env = np.sin(2 * np.pi * 1.5 * t) ** 2
            return (s * env / (np.max(np.abs(s)) + 1e-12) * 0.7).astype(np.float32)

        speaker_name = "Sir"
        samples = [
            _synth(125.0, [550.0, 1600.0, 2600.0]),
            _synth(127.0, [560.0, 1580.0, 2620.0]),
            _synth(124.0, [540.0, 1620.0, 2590.0]),
        ]
        mgr = VoiceProfileManager()
        ok = mgr.enroll_speaker(speaker_name, samples, threshold=0.72)
        test_aud = _synth(126.0, [555.0, 1590.0, 2610.0])
        match, conf, spk = mgr.verify_audio(test_aud, speaker_name)
        print(f"[TEST SUCCESS] Enrolled={ok}, Match={match}, Conf={conf*100:.1f}%, Speaker={spk}")
        return

    clear_screen()
    print_banner()

    cfg = load_config()
    default_name = cfg.get("user_name", "Sir")

    print("[*] Welcome to the JARVIS Voice Learning Calibration Wizard.")
    print("[*] JARVIS will extract your acoustic voiceprint so it can recognize your voice.")
    print("------------------------------------------------------------------------------")

    # Speaker Name Prompt
    try:
        user_input = input(f"Enter your preferred callsign/name (default: '{default_name}'): ").strip()
        speaker_name = user_input if user_input else default_name
    except (KeyboardInterrupt, EOFError):
        print("\nOperation cancelled.")
        return

    print(f"\n[+] Enrolling Voice Profile for: [ {speaker_name} ]")

    # Check Audio Input
    mic_device = cfg.get("mic_device_index", None)
    try:
        dev_info = sd.query_devices(mic_device) if mic_device is not None else sd.query_devices(kind="input")
        print(f"[+] Audio Capture Device: {dev_info['name']} (index={mic_device})")
    except Exception as e:
        print(f"[ERR] No audio input device detected: {e}")
        return

    # Phase 1: Ambient Noise Baseline
    print("\n------------------------------------------------------------------------------")
    print("PHASE 1: Ambient Background Noise Baseline")
    print("Please remain completely silent for 2.5 seconds to measure room acoustics...")
    noise_sample = record_audio(2.5, "Measuring room silence...", device=mic_device)
    rms_noise = np.sqrt(np.mean(noise_sample ** 2) + 1e-12)
    print(f"[*] Ambient Noise Floor RMS: {rms_noise:.5f} (Normal)")

    # Phase 2: Voice Training Phrases
    print("\n------------------------------------------------------------------------------")
    print("PHASE 2: Voice Calibration Phrases")
    print("Read each phrase clearly in your normal speaking tone and distance from mic.")

    phrases = [
        f"JARVIS, this is {speaker_name}. Authorize voice protocols.",
        "Run diagnostic check and prepare my daily briefing.",
        "JARVIS, are you online and listening?",
        "Security override, confirm speaker identification.",
    ]

    recorded_samples = []
    manager = VoiceProfileManager()

    for idx, phrase in enumerate(phrases, 1):
        print(f"\n[Step {idx}/{len(phrases)}]")
        print(f'Say: "{phrase}"')
        audio = record_audio(4.0, device=mic_device)

        # Check energy
        rms = np.sqrt(np.mean(audio ** 2) + 1e-12)
        if rms < rms_noise * 1.3:
            print("  [WARN] Very low voice volume detected. Retrying this phrase...")
            audio = record_audio(4.0, "Please speak louder and closer to the mic:", device=mic_device)

        vec, frames = manager.extractor.extract_voiceprint_vector(audio)
        if vec is not None and frames >= 5:
            recorded_samples.append(audio)
            print(f"  [OK] Sample {idx} captured successfully ({frames} vocal frames).")
        else:
            print("  [WARN] Insufficient voice characteristics detected. Trying one more time...")
            audio = record_audio(4.0, f'Repeat: "{phrase}"', device=mic_device)
            vec, frames = manager.extractor.extract_voiceprint_vector(audio)
            if vec is not None:
                recorded_samples.append(audio)
                print(f"  [OK] Sample {idx} captured successfully.")

    if len(recorded_samples) < 2:
        print("\n[ERR] Not enough clear voice samples were gathered. Please run calibration again in a quieter environment.")
        return

    # Phase 3: Building Profile
    print("\n------------------------------------------------------------------------------")
    print("PHASE 3: Computing Acoustic Voiceprint Vectors...")
    success = manager.enroll_speaker(speaker_name, recorded_samples, threshold=0.72)

    if not success:
        print("[ERR] Voice enrollment failed. Please try again.")
        return

    # Phase 4: Live Verification Test
    print("\n------------------------------------------------------------------------------")
    print("PHASE 4: Live Voiceprint Verification Test")
    print(f"Say anything to test if JARVIS recognizes you (e.g., 'Hello JARVIS, do you know who I am?'):")
    test_audio = record_audio(3.5, device=mic_device)

    is_match, conf, matched_speaker = manager.verify_audio(test_audio, speaker_name)
    conf_pct = conf * 100.0

    print("\n[VERIFICATION RESULTS]")
    print(f"  - Speaker Identified: {matched_speaker}")
    print(f"  - Match Confidence:   {conf_pct:.1f}%")
    print(f"  - Verification Status: {'[VERIFIED - OWNER ACCEPTED]' if is_match else '[UNRECOGNIZED - GUEST]'}")

    # Phase 5: Update Configuration
    cfg["user_name"] = speaker_name
    cfg["voice_recognition_enabled"] = True
    cfg["voice_security_mode"] = "smart"  # 'smart' identifies & personalizes; 'strict' locks down
    cfg["voice_match_threshold"] = 0.72
    save_config(cfg)

    print("\n------------------------------------------------------------------------------")
    print(f"[SUCCESS] Voice profile for '{speaker_name}' is saved and active!")
    print("[*] JARVIS will now recognize your voice whenever you speak.")
    print("------------------------------------------------------------------------------\n")
    try:
        input("Press ENTER to exit...")
    except Exception:
        pass


if __name__ == "__main__":
    main()
