"""
JARVIS Voice Learning & Speaker Identification Engine (Project VoicePrint)
Provides robust acoustic feature extraction (MFCCs, spectral energy, pitch dynamics),
speaker voiceprint modeling, voice enrollment, and real-time speaker verification
using pure NumPy for maximum cross-platform compatibility (Python 3.10-3.13+).
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Any, Tuple, Optional

# Reconfigure stdout/stderr on Windows to avoid UnicodeEncodeError on cp1252
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import numpy as np


class VoiceFeatureExtractor:
    """
    Extracts acoustic feature vectors (MFCCs + energy + dynamics) from 16kHz audio.
    Optimized for speaker recognition and voiceprint matching.
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        frame_len_ms: float = 25.0,
        frame_step_ms: float = 10.0,
        num_filters: int = 26,
        num_ceps: int = 20,
        nfft: int = 512,
        low_freq_hz: float = 50.0,
        high_freq_hz: Optional[float] = None,
        pre_emphasis_coeff: float = 0.97,
    ):
        self.sample_rate = sample_rate
        self.frame_len = int(round(sample_rate * frame_len_ms / 1000.0))
        self.frame_step = int(round(sample_rate * frame_step_ms / 1000.0))
        self.num_filters = num_filters
        self.num_ceps = num_ceps
        self.nfft = nfft
        self.low_freq_hz = low_freq_hz
        self.high_freq_hz = high_freq_hz or (sample_rate / 2.0)
        self.pre_emphasis_coeff = pre_emphasis_coeff

        # Precompute Hamming window
        self._hamming = np.hamming(self.frame_len)

        # Precompute Mel filterbank matrix
        self._filterbank = self._build_mel_filterbank()

        # Precompute DCT-II matrix
        self._dct_basis = self._build_dct_basis()

    def _hz_to_mel(self, hz: float | np.ndarray) -> float | np.ndarray:
        return 2595.0 * np.log10(1.0 + hz / 700.0)

    def _mel_to_hz(self, mel: float | np.ndarray) -> float | np.ndarray:
        return 700.0 * (10.0 ** (mel / 2595.0) - 1.0)

    def _build_mel_filterbank(self) -> np.ndarray:
        """Construct triangular Mel filterbank matrix (num_filters x (nfft // 2 + 1))."""
        low_mel = self._hz_to_mel(self.low_freq_hz)
        high_mel = self._hz_to_mel(self.high_freq_hz)
        mel_points = np.linspace(low_mel, high_mel, self.num_filters + 2)
        hz_points = self._mel_to_hz(mel_points)
        bin_points = np.floor((self.nfft + 1) * hz_points / self.sample_rate).astype(int)

        fb = np.zeros((self.num_filters, self.nfft // 2 + 1), dtype=np.float32)
        for m in range(1, self.num_filters + 1):
            f_m_minus = bin_points[m - 1]
            f_m = bin_points[m]
            f_m_plus = bin_points[m + 1]

            if f_m > f_m_minus:
                fb[m - 1, f_m_minus:f_m] = (
                    np.arange(f_m_minus, f_m) - f_m_minus
                ) / float(f_m - f_m_minus)
            if f_m_plus > f_m:
                fb[m - 1, f_m:f_m_plus] = (
                    f_m_plus - np.arange(f_m, f_m_plus)
                ) / float(f_m_plus - f_m)

        return fb

    def _build_dct_basis(self) -> np.ndarray:
        """Construct Discrete Cosine Transform (DCT-II) matrix (num_ceps x num_filters)."""
        basis = np.zeros((self.num_ceps, self.num_filters), dtype=np.float32)
        factor = np.pi / float(self.num_filters)
        for i in range(self.num_ceps):
            for j in range(self.num_filters):
                basis[i, j] = np.cos((j + 0.5) * i * factor)
        return basis

    def _pre_emphasis(self, signal: np.ndarray) -> np.ndarray:
        if len(signal) == 0:
            return signal
        return np.append(signal[0], signal[1:] - self.pre_emphasis_coeff * signal[:-1])

    def _extract_frames(self, signal: np.ndarray) -> np.ndarray:
        sig_len = len(signal)
        if sig_len < self.frame_len:
            pad = np.zeros(self.frame_len - sig_len, dtype=signal.dtype)
            signal = np.append(signal, pad)
            sig_len = len(signal)

        num_frames = 1 + int(math.floor((sig_len - self.frame_len) / self.frame_step))
        shape = (num_frames, self.frame_len)
        strides = (signal.strides[0] * self.frame_step, signal.strides[0])
        frames = np.lib.stride_tricks.as_strided(signal, shape=shape, strides=strides)
        return frames.copy()

    def compute_mfcc(self, audio: np.ndarray) -> np.ndarray:
        """
        Compute MFCCs for the given audio signal.
        audio: 1D float32 array in [-1.0, 1.0] (or int16 PCM converted).
        Returns: (num_active_frames, num_ceps) 2D array.
        """
        if audio.dtype == np.int16:
            audio = audio.astype(np.float32) / 32768.0
        elif audio.dtype != np.float32 and audio.dtype != np.float64:
            audio = audio.astype(np.float32)

        if len(audio) < self.frame_len:
            return np.zeros((0, self.num_ceps), dtype=np.float32)

        # Pre-emphasis filter
        emphasized = self._pre_emphasis(audio)

        # Frame segmentation
        frames = self._extract_frames(emphasized)

        # Voice Activity Detection (VAD) filter: discard silent / low energy frames
        frame_energies = np.sqrt(np.mean(frames ** 2, axis=1) + 1e-12)
        max_energy = np.max(frame_energies) if len(frame_energies) > 0 else 0.0
        # Dynamic threshold: at least 0.005 and 15% of peak energy
        energy_thresh = max(0.005, max_energy * 0.15)
        active_indices = np.where(frame_energies > energy_thresh)[0]

        if len(active_indices) == 0:
            return np.zeros((0, self.num_ceps), dtype=np.float32)

        active_frames = frames[active_indices]

        # Apply Hamming window
        windowed = active_frames * self._hamming

        # FFT & Power spectrum
        mag_spec = np.abs(np.fft.rfft(windowed, n=self.nfft, axis=1))
        pow_spec = (1.0 / self.nfft) * (mag_spec ** 2)

        # Mel filterbank log energy
        mel_energies = np.dot(pow_spec, self._filterbank.T)
        mel_energies = np.maximum(mel_energies, 1e-10)
        log_mel_energies = np.log(mel_energies)

        # DCT -> MFCC
        mfcc = np.dot(log_mel_energies, self._dct_basis.T)

        # Cepstral Mean Subtraction (CMS) for channel normalization
        mfcc -= np.mean(mfcc, axis=0, keepdims=True)

        return mfcc.astype(np.float32)

    def extract_voiceprint_vector(self, audio: np.ndarray) -> Tuple[Optional[np.ndarray], int]:
        """
        Extracts a compact speaker voiceprint vector (Mean + Std + Delta) from audio.
        Returns: (voiceprint_vector, active_frame_count).
        """
        mfcc = self.compute_mfcc(audio)
        if len(mfcc) < 5:
            return None, len(mfcc)

        # Mean and standard deviation across time
        mean_vec = np.mean(mfcc, axis=0)
        std_vec = np.std(mfcc, axis=0)

        # Deltas (first derivative approximation)
        if len(mfcc) >= 3:
            delta = np.gradient(mfcc, axis=0)
            delta_mean = np.mean(delta, axis=0)
            delta_std = np.std(delta, axis=0)
        else:
            delta_mean = np.zeros_like(mean_vec)
            delta_std = np.zeros_like(std_vec)

        # Concatenated representation
        vector = np.concatenate([mean_vec, std_vec, delta_mean, delta_std])

        # L2-normalize vector for robust cosine similarity
        norm = np.linalg.norm(vector)
        if norm > 1e-10:
            vector = vector / norm

        return vector, len(mfcc)


class VoiceProfileManager:
    """
    Manages speaker profiles, enrollment storage, and real-time verification.
    """

    DEFAULT_THRESHOLD = 0.72

    def __init__(self, profiles_dir: Optional[Path | str] = None):
        if profiles_dir is None:
            # Look relative to workspace
            base_dir = Path(__file__).resolve().parent.parent
            profiles_dir = base_dir / "memory" / "voice_profiles"
        self.profiles_dir = Path(profiles_dir)
        self.profiles_dir.mkdir(parents=True, exist_ok=True)

        self.extractor = VoiceFeatureExtractor()
        self.enrolled_profiles: dict[str, dict[str, Any]] = {}
        self.active_speaker: Optional[str] = None
        self.load_all_profiles()

    def _get_profile_path(self, speaker_name: str) -> Path:
        safe_name = "".join(c for c in speaker_name.lower().replace(" ", "_") if c.isalnum() or c == "_")
        return self.profiles_dir / f"{safe_name}.json"

    def load_all_profiles(self) -> None:
        """Load all saved voice profiles from disk."""
        self.enrolled_profiles.clear()
        for f in self.profiles_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                name = data.get("speaker_name", f.stem)
                vector = np.array(data["vector"], dtype=np.float32)
                # Ensure L2 normalized
                norm = np.linalg.norm(vector)
                if norm > 1e-10:
                    vector = vector / norm
                data["_vector_np"] = vector
                self.enrolled_profiles[name] = data
                if self.active_speaker is None:
                    self.active_speaker = name
            except Exception as e:
                print(f"[VoiceProfile] [WARN] Failed to load {f.name}: {e}")

    def is_enrolled(self, speaker_name: Optional[str] = None) -> bool:
        if speaker_name:
            return speaker_name in self.enrolled_profiles
        return len(self.enrolled_profiles) > 0

    def enroll_speaker(
        self,
        speaker_name: str,
        audio_samples: list[np.ndarray],
        threshold: float = DEFAULT_THRESHOLD,
    ) -> bool:
        """
        Enroll a speaker by aggregating multiple audio recordings into a unified voiceprint.
        """
        vectors = []
        total_frames = 0
        for sample in audio_samples:
            vec, frames = self.extractor.extract_voiceprint_vector(sample)
            if vec is not None and frames >= 5:
                vectors.append(vec)
                total_frames += frames

        if not vectors:
            print(f"[VoiceProfile] [ERR] Insufficient vocal data to enroll '{speaker_name}'.")
            return False

        # Average normalized vectors and re-normalize
        mean_vector = np.mean(vectors, axis=0)
        norm = np.linalg.norm(mean_vector)
        if norm > 1e-10:
            mean_vector = mean_vector / norm

        profile_data = {
            "speaker_name": speaker_name,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "num_samples": len(vectors),
            "total_frames": total_frames,
            "threshold": threshold,
            "vector": mean_vector.tolist(),
        }

        path = self._get_profile_path(speaker_name)
        path.write_text(json.dumps(profile_data, indent=2), encoding="utf-8")

        profile_data["_vector_np"] = mean_vector
        self.enrolled_profiles[speaker_name] = profile_data
        self.active_speaker = speaker_name
        print(f"[VoiceProfile] [OK] Speaker '{speaker_name}' successfully enrolled ({len(vectors)} samples, {total_frames} frames).")
        return True

    def verify_audio(
        self,
        audio: np.ndarray,
        target_speaker: Optional[str] = None,
        threshold_override: Optional[float] = None,
    ) -> Tuple[bool, float, str]:
        """
        Verify if the given audio chunk matches the enrolled speaker voiceprint.
        Returns: (is_match, confidence_score_0_to_1, speaker_identified)
        """
        speaker = target_speaker or self.active_speaker
        if not speaker or speaker not in self.enrolled_profiles:
            return False, 0.0, "unknown_unregistered"

        profile = self.enrolled_profiles[speaker]
        target_vec: np.ndarray = profile["_vector_np"]
        threshold = threshold_override or profile.get("threshold", self.DEFAULT_THRESHOLD)

        sample_vec, frames = self.extractor.extract_voiceprint_vector(audio)
        if sample_vec is None or frames < 5:
            # Not enough speech frames in this chunk to reliably verify
            return False, 0.0, "silent_or_ambient"

        # Cosine similarity between normalized vectors
        cos_sim = float(np.dot(target_vec, sample_vec))
        # Map cosine similarity [-1, 1] to positive confidence [0, 1]
        confidence = max(0.0, min(1.0, (cos_sim + 1.0) / 2.0))

        # Check threshold
        is_match = confidence >= threshold
        matched_name = speaker if is_match else "unrecognized_guest"
        return is_match, confidence, matched_name

    def identify_speaker(self, audio: np.ndarray) -> Tuple[Optional[str], float]:
        """
        Identify which registered speaker is speaking in the audio chunk.
        Returns: (speaker_name_or_None, highest_confidence)
        """
        if not self.enrolled_profiles:
            return None, 0.0

        sample_vec, frames = self.extractor.extract_voiceprint_vector(audio)
        if sample_vec is None or frames < 5:
            return None, 0.0

        best_speaker = None
        best_conf = 0.0

        for name, profile in self.enrolled_profiles.items():
            target_vec = profile["_vector_np"]
            cos_sim = float(np.dot(target_vec, sample_vec))
            conf = max(0.0, min(1.0, (cos_sim + 1.0) / 2.0))
            thresh = profile.get("threshold", self.DEFAULT_THRESHOLD)

            if conf >= thresh and conf > best_conf:
                best_conf = conf
                best_speaker = name

        return best_speaker, best_conf
