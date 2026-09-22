import unittest
import sys
import os
import time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.stt import LiveSpeechListener


class TestLiveSpeechListener(unittest.TestCase):
    def test_listener_init(self):
        listener = LiveSpeechListener(
            device_index=None,
            language="en-IN",
            pause_threshold=0.8,
            phrase_time_limit=12.0,
            acoustic_cooldown=0.6,
            continuous_mode=True,
            on_command=lambda text, spk: None,
            is_speaking_fn=lambda: False,
            is_muted_fn=lambda: False,
        )
        self.assertEqual(listener.language, "en-IN")
        self.assertEqual(listener.pause_threshold, 0.8)
        self.assertEqual(listener.phrase_time_limit, 12.0)
        self.assertEqual(listener.acoustic_cooldown, 0.6)
        self.assertTrue(listener.continuous_mode)
        self.assertTrue(listener.recognizer.dynamic_energy_threshold)
        self.assertEqual(listener.recognizer.pause_threshold, 0.8)
        self.assertEqual(listener.recognizer.dynamic_energy_adjustment_damping, 0.15)
        self.assertEqual(listener.recognizer.dynamic_energy_ratio, 1.5)

    def test_echo_suppression_and_mute_checks(self):
        is_speaking = False
        is_muted = False

        listener = LiveSpeechListener(
            device_index=None,
            is_speaking_fn=lambda: is_speaking,
            is_muted_fn=lambda: is_muted,
        )

        self.assertFalse(listener.is_speaking_fn())
        self.assertFalse(listener.is_muted_fn())

        is_speaking = True
        self.assertTrue(listener.is_speaking_fn())

        is_speaking = False
        is_muted = True
        self.assertTrue(listener.is_muted_fn())

    def test_acoustic_cooldown_triggering(self):
        listener = LiveSpeechListener(
            device_index=None,
            acoustic_cooldown=0.5,
        )
        self.assertEqual(listener._cooldown_until, 0.0)

        now = time.time()
        listener.trigger_cooldown()
        self.assertGreaterEqual(listener._cooldown_until, now + 0.49)
        self.assertLessEqual(listener._cooldown_until, now + 0.6)

    def test_custom_pause_threshold_and_cooldown(self):
        listener = LiveSpeechListener(
            pause_threshold=1.2,
            acoustic_cooldown=0.8,
            phrase_time_limit=20.0,
        )
        self.assertEqual(listener.pause_threshold, 1.2)
        self.assertEqual(listener.recognizer.pause_threshold, 1.2)
        self.assertEqual(listener.acoustic_cooldown, 0.8)
        self.assertEqual(listener.phrase_time_limit, 20.0)

    def test_barge_in_and_echo_filtering(self):
        listener = LiveSpeechListener(voice_barge_in=True)
        self.assertTrue(listener.voice_barge_in)

        # Self-echo matching
        speaking_text = "The weather in Chennai today is sunny and thirty degrees Celsius."
        echo_input = "thirty degrees Celsius"
        self.assertTrue(listener._is_echo(echo_input, speaking_text))

        # Sir's new command/interruption (not an echo)
        sir_input = "Wait stop what about tomorrow"
        self.assertFalse(listener._is_echo(sir_input, speaking_text))

        sir_interrupt = "stop"
        self.assertFalse(listener._is_echo(sir_interrupt, speaking_text))

    def test_interrupt_keywords(self):
        listener = LiveSpeechListener()
        self.assertIn("stop", listener.INTERRUPT_KEYWORDS)
        self.assertIn("wait", listener.INTERRUPT_KEYWORDS)
        self.assertIn("cancel", listener.INTERRUPT_KEYWORDS)
        self.assertIn("niruthu", listener.INTERRUPT_KEYWORDS)


if __name__ == "__main__":
    unittest.main()


