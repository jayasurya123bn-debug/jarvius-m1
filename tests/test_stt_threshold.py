import unittest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.stt import LiveSpeechListener, parse_wake_word


class TestSTTThresholdAndMic(unittest.TestCase):

    def test_energy_threshold_floor(self):
        listener = LiveSpeechListener(
            continuous_mode=True,
            wake_word_enabled=True,
        )
        # Verify initial energy threshold is at safe floor >= 250
        self.assertGreaterEqual(listener.recognizer.energy_threshold, 250.0)
        self.assertEqual(listener.recognizer.dynamic_energy_adjustment_damping, 0.15)

    def test_preferred_mic_detection(self):
        # Verify get_preferred_mic_index executes cleanly without exception
        idx = LiveSpeechListener.get_preferred_mic_index()
        if idx is not None:
            self.assertIsInstance(idx, int)
            self.assertGreaterEqual(idx, 0)

    def test_tamil_wake_word(self):
        # Verify Tamil script wake word detection
        is_wake, cmd, wake = parse_wake_word("ஜார்விஸ் யூடியூப் ஓபன் பண்ணு")
        self.assertTrue(is_wake)
        self.assertEqual(cmd, "யூடியூப் ஓபன் பண்ணு")
        self.assertEqual(wake, "ஜார்விஸ்")


if __name__ == "__main__":
    unittest.main()
