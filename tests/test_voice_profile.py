"""
Unit tests for JARVIS Voice Learning & Speaker Identification Engine
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

import numpy as np

from core.voice_profile import VoiceFeatureExtractor, VoiceProfileManager


class TestVoiceProfile(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.extractor = VoiceFeatureExtractor(sample_rate=16000)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _generate_synthetic_voice(
        self,
        duration_s: float = 2.0,
        fundamental_hz: float = 130.0,
        formants: list[float] = [500.0, 1500.0, 2500.0],
    ) -> np.ndarray:
        """Synthesize a harmonic, speech-like periodic signal with specific formant resonances."""
        sr = 16000
        t = np.linspace(0, duration_s, int(sr * duration_s), endpoint=False)
        # Glottal pulse train simulation
        signal = np.zeros_like(t)
        for harmonic in range(1, 15):
            freq = fundamental_hz * harmonic
            if freq < sr / 2:
                # Weight by formants
                weight = sum(np.exp(-((freq - f) ** 2) / (2 * 120 ** 2)) for f in formants) + 0.1
                signal += (weight / harmonic) * np.sin(2 * np.pi * freq * t)

        # Apply amplitude envelope (bursts with pauses)
        env = np.sin(2 * np.pi * 1.5 * t) ** 2
        signal = signal * env

        # Normalize
        peak = np.max(np.abs(signal))
        if peak > 0:
            signal = signal / peak * 0.7
        return signal.astype(np.float32)

    def test_feature_extractor_dimensions(self):
        audio = self._generate_synthetic_voice(duration_s=1.5)
        mfcc = self.extractor.compute_mfcc(audio)
        self.assertGreater(len(mfcc), 0, "MFCCs should be extracted from voiced signal")
        self.assertEqual(mfcc.shape[1], 20, "Should have 20 cepstral coefficients")

        vec, frames = self.extractor.extract_voiceprint_vector(audio)
        self.assertIsNotNone(vec)
        self.assertEqual(len(vec), 80, "Voiceprint vector should be 4 x 20 = 80 dimensions")
        # Check normalized
        norm = np.linalg.norm(vec)
        self.assertAlmostEqual(norm, 1.0, places=4)

    def test_voice_enrollment_and_verification(self):
        manager = VoiceProfileManager(profiles_dir=self.temp_dir)
        self.assertFalse(manager.is_enrolled("Sir"))

        # User voice (fundamental ~ 125 Hz, formants [550, 1600, 2600])
        user_samples = [
            self._generate_synthetic_voice(duration_s=2.0, fundamental_hz=125.0, formants=[550.0, 1600.0, 2600.0]),
            self._generate_synthetic_voice(duration_s=2.0, fundamental_hz=127.0, formants=[560.0, 1580.0, 2620.0]),
            self._generate_synthetic_voice(duration_s=2.0, fundamental_hz=124.0, formants=[540.0, 1620.0, 2590.0]),
        ]

        ok = manager.enroll_speaker("Sir", user_samples, threshold=0.70)
        self.assertTrue(ok)
        self.assertTrue(manager.is_enrolled("Sir"))

        # Test with another utterance from the same speaker
        matching_sample = self._generate_synthetic_voice(
            duration_s=2.0, fundamental_hz=126.0, formants=[555.0, 1590.0, 2610.0]
        )
        is_match, conf, name = manager.verify_audio(matching_sample, "Sir")
        self.assertTrue(is_match, f"Expected match, got conf={conf}")
        self.assertEqual(name, "Sir")
        self.assertGreaterEqual(conf, 0.70)

        # Test with a distinctly different speaker (fundamental ~ 220 Hz, higher formants)
        imposter_sample = self._generate_synthetic_voice(
            duration_s=2.0, fundamental_hz=230.0, formants=[800.0, 2100.0, 3200.0]
        )
        is_match_imp, conf_imp, name_imp = manager.verify_audio(imposter_sample, "Sir")
        self.assertLess(conf_imp, conf, "Imposter voice should have lower confidence score than genuine voice")

    def test_empty_or_silence_handling(self):
        manager = VoiceProfileManager(profiles_dir=self.temp_dir)
        silence = np.zeros(16000, dtype=np.float32)
        vec, frames = self.extractor.extract_voiceprint_vector(silence)
        self.assertIsNone(vec, "Silence should not generate a valid voiceprint vector")

        is_match, conf, tag = manager.verify_audio(silence, "Sir")
        self.assertFalse(is_match)
        self.assertEqual(tag, "unknown_unregistered" if not manager.is_enrolled() else "silent_or_ambient")


if __name__ == "__main__":
    unittest.main()
