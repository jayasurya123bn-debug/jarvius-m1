#!/usr/bin/env python3
"""
JARVIS Live Voice & Microphone Diagnostics Tool
Tests microphone input levels in real-time, verifies biometrics,
and performs an end-to-end conversation turn with Gemini Live.
"""

import os
import sys
import time
import json
import asyncio
from pathlib import Path

# Fix Windows console utf-8
for s in (sys.stdout, sys.stderr):
    try:
        s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import numpy as np
import sounddevice as sd

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"
ROOT_CONFIG_PATH = BASE_DIR.parent / "config" / "api_keys.json"

def load_config() -> dict:
    for p in (CONFIG_PATH, ROOT_CONFIG_PATH):
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
    return {}

def save_config(cfg: dict):
    txt = json.dumps(cfg, indent=4)
    for p in (CONFIG_PATH, ROOT_CONFIG_PATH):
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(txt, encoding="utf-8")
        except Exception:
            pass

def list_input_devices():
    devices = sd.query_devices()
    input_devs = []
    for i, d in enumerate(devices):
        if d['max_input_channels'] > 0:
            host_api = sd.query_hostapis(d['hostapi'])['name']
            input_devs.append((i, d['name'], host_api))
    return input_devs

async def main():
    print("=" * 70)
    print("      JARVIS LIVE VOICE & MICROPHONE DIAGNOSTICS")
    print("=" * 70)

    cfg = load_config()
    api_key = cfg.get("gemini_api_key", "")
    if not api_key:
        print("[ERR] No gemini_api_key found in config/api_keys.json.")
        return

    print("\n[Step 1] Checking Gemini Live Cloud Connection...")
    from google import genai
    from google.genai import types

    try:
        client = genai.Client(api_key=api_key, http_options={"api_version": "v1alpha"})
        connect_cfg = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            output_audio_transcription={},
            input_audio_transcription={},
            system_instruction="You are JARVIS. When the user speaks, respond warmly and concisely in one sentence."
        )
        print("  [OK] Gemini API Client initialized successfully.")
    except Exception as e:
        print(f"  [ERR] Failed to initialize Gemini Client: {e}")
        return

    print("\n[Step 2] Available Audio Input Devices on Your PC:")
    input_devs = list_input_devices()
    default_in = sd.query_devices(kind="input")
    current_saved_mic = cfg.get("mic_device_index", None)

    for idx, name, api in input_devs:
        marker = " (Current JARVIS setting)" if idx == current_saved_mic else ""
        if current_saved_mic is None and name == default_in['name']:
            marker = " (Windows System Default)"
        print(f"  [{idx:2d}] {name}  [{api}]{marker}")

    print("\nSelect the microphone you want to use:")
    print("  - Type device number (e.g. 1 for Laptop Realtek, 14 for M19 Headset WASAPI)")
    print("  - Or press ENTER to use default")
    try:
        user_sel = input("Enter device number [default]: ").strip()
        if user_sel and user_sel.isdigit():
            selected_device = int(user_sel)
        elif current_saved_mic is not None:
            selected_device = current_saved_mic
        else:
            selected_device = None
    except (KeyboardInterrupt, EOFError):
        return

    chosen_name = sd.query_devices(selected_device)['name'] if selected_device is not None else default_in['name']
    print(f"\n[+] Testing Microphone: {chosen_name} (device={selected_device})")

    # Step 3: Live Volume VU Meter
    print("\n[Step 3] Real-Time Voice Volume Test (5 seconds)")
    print("Speak out loud right now! Say: 'Hello JARVIS, are you listening?'")
    print("-" * 70)

    audio_chunks = []
    max_peak = 0

    def meter_callback(indata, frames, time_info, status):
        nonlocal max_peak
        peak = int(np.max(np.abs(indata)))
        if peak > max_peak:
            max_peak = peak
        audio_chunks.append(indata.copy())
        
        # Scale to 0-30 bars (32767 is max int16)
        bars = int(min(30, (peak / 15000.0) * 30))
        bar_str = "■" * bars + " " * (30 - bars)
        level_pct = min(100, int((peak / 20000.0) * 100))
        status_txt = "VOICE DETECTED!" if peak > 1200 else "Too quiet / Silence"
        sys.stdout.write(f"\r  Mic Level: [{bar_str}] {level_pct:3d}%  {status_txt}    ")
        sys.stdout.flush()

    try:
        with sd.InputStream(device=selected_device, samplerate=16000, channels=1, dtype="int16", blocksize=1024, callback=meter_callback):
            time.sleep(5.0)
    except Exception as e:
        print(f"\n[ERR] Failed to open microphone stream: {e}")
        return

    print("\n" + "-" * 70)
    print(f"[*] Max audio peak recorded: {max_peak} / 32767")

    if max_peak < 800:
        print("\n[WARNING] Microphone level is extremely low or silent!")
        print("  - If using laptop mic: check if physical Fn+F4 mic mute is ON, or speak closer.")
        print("  - If using M19 headset: try device [14] (WASAPI) or [8] (DirectSound).")
        print("  - Increase microphone volume in Windows Sound Settings to 100%.")
    else:
        print("\n[SUCCESS] Strong voice audio received from your microphone!")

    # Step 4: Transmit audio to Gemini Live session
    if audio_chunks:
        full_audio_int16 = np.concatenate(audio_chunks, axis=0)
        raw_pcm_bytes = full_audio_int16.tobytes()

        print("\n[Step 4] Sending your voice recording to JARVIS Live AI...")
        try:
            async with client.aio.live.connect(model="models/gemini-2.5-flash-native-audio-latest", config=connect_cfg) as session:
                # Stream the recorded PCM in slices of 2048 bytes
                _slice = 2048
                for i in range(0, len(raw_pcm_bytes), _slice):
                    chunk = raw_pcm_bytes[i:i + _slice]
                    await session.send_realtime_input(media={"data": chunk, "mime_type": "audio/pcm;rate=16000"})
                    await asyncio.sleep(0.01)

                print("  [*] Audio sent. Waiting for JARVIS to answer...")
                received_response = False
                response_audio_chunks = []

                async def recv_timeout():
                    nonlocal received_response
                    async for resp in session.receive():
                        if resp.data:
                            response_audio_chunks.append(resp.data)
                        if resp.server_content:
                            sc = resp.server_content
                            if sc.input_transcription and sc.input_transcription.text:
                                print(f"\n  [JARVIS HEARD YOU SAY]: \"{sc.input_transcription.text}\"")
                            if sc.output_transcription and sc.output_transcription.text:
                                print(f"  [JARVIS SPOKE]:         \"{sc.output_transcription.text}\"")
                                received_response = True
                            if sc.turn_complete:
                                break

                try:
                    await asyncio.wait_for(recv_timeout(), timeout=8.0)
                except asyncio.TimeoutError:
                    pass

                if received_response:
                    print("\n[PERFECT!] JARVIS successfully heard your voice and responded!")
                    if response_audio_chunks:
                        print("  [*] Playing JARVIS voice response through your speakers/headphones...")
                        try:
                            resp_pcm = b"".join(response_audio_chunks)
                            resp_np = np.frombuffer(resp_pcm, dtype=np.int16)
                            sd.play(resp_np, samplerate=24000)
                            sd.wait()
                        except Exception as e:
                            print(f"  [WARN] Audio playback error: {e}")
                else:
                    print("\n[INFO] Gemini received the audio but did not transcribe speech.")
                    print("  This happens when volume is too low for the Voice Activity Detector (VAD).")

        except Exception as e:
            print(f"  [ERR] Gemini Live transmission error: {e}")

    # Step 5: Save preference
    if selected_device is not None:
        try:
            ans = input(f"\nDo you want to save device [{selected_device}] ({chosen_name}) as JARVIS's permanent microphone? (y/N): ").strip().lower()
            if ans == 'y':
                cfg["mic_device_index"] = selected_device
                save_config(cfg)
                print(f"[OK] Saved mic_device_index={selected_device} to config/api_keys.json!")
        except Exception:
            pass

    print("\nDiagnostics complete. Press Enter to exit.")
    try:
        input()
    except Exception:
        pass

if __name__ == "__main__":
    asyncio.run(main())
