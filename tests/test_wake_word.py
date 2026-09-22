import unittest
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.stt import parse_wake_word, DEFAULT_WAKE_WORDS, LiveSpeechListener


class TestWakeWord(unittest.TestCase):

    def test_parse_wake_word_phrases(self):
        # 1. Direct wake words alone
        is_wake, cmd, wake = parse_wake_word("Hey Jarvis")
        self.assertTrue(is_wake)
        self.assertEqual(cmd, "")
        self.assertIn("jarvis", wake.lower())

        # User's exact phonetic spelling: "hey jarvius"
        is_wake, cmd, wake = parse_wake_word("hey jarvius")
        self.assertTrue(is_wake)
        self.assertEqual(cmd, "")
        self.assertIn("jarvius", wake.lower())

        # Just "jarvis"
        is_wake, cmd, wake = parse_wake_word("jarvis")
        self.assertTrue(is_wake)
        self.assertEqual(cmd, "")

        # "hi jarvis", "hello jarvis", "ok jarvis"
        for phrase in ("hi jarvis", "hello jarvis", "ok jarvis", "okay jarvis"):
            is_wake, cmd, wake = parse_wake_word(phrase)
            self.assertTrue(is_wake, f"Failed for {phrase}")
            self.assertEqual(cmd, "")

    def test_parse_wake_word_with_commands(self):
        # "Hey Jarvis open chrome" -> command: "open chrome"
        is_wake, cmd, wake = parse_wake_word("Hey Jarvis open chrome")
        self.assertTrue(is_wake)
        self.assertEqual(cmd, "open chrome")

        # "hey jarvius nu sona voice cmd work agunum"
        is_wake, cmd, wake = parse_wake_word("hey jarvius nu sona voice cmd work agunum")
        self.assertTrue(is_wake)
        self.assertEqual(cmd, "nu sona voice cmd work agunum")

        # Punctuation after wake word: "Hey Jarvis, what is the weather?"
        is_wake, cmd, wake = parse_wake_word("Hey Jarvis, what is the weather?")
        self.assertTrue(is_wake)
        self.assertEqual(cmd, "what is the weather")

        # Inlined with "Jarvis:"
        is_wake, cmd, wake = parse_wake_word("Jarvis: mute volume")
        self.assertTrue(is_wake)
        self.assertEqual(cmd, "mute volume")

        # Embedded at the end: "what time is it jarvis"
        is_wake, cmd, wake = parse_wake_word("what time is it jarvis")
        self.assertTrue(is_wake)
        self.assertEqual(cmd, "what time is it")

    def test_non_wake_words(self):
        # Normal room speech without wake word
        for phrase in (
            "hello everyone in the room",
            "the weather looks nice today",
            "can we start the meeting",
            "turning on the television",
        ):
            is_wake, cmd, wake = parse_wake_word(phrase)
            self.assertFalse(is_wake, f"False positive on: {phrase}")
            self.assertEqual(cmd, phrase)
            self.assertEqual(wake, "")

    def test_live_speech_listener_wake_states(self):
        wake_events = []
        commands = []

        listener = LiveSpeechListener(
            continuous_mode=False,
            wake_word_enabled=True,
            wake_timeout=2.0,
            on_wake=lambda w, has_cmd: wake_events.append((w, has_cmd)),
            on_command=lambda cmd, info: commands.append((cmd, info)),
        )

        # Initially sleeping
        self.assertFalse(listener.is_awake())

        # Test manual wake
        listener.wake_up(duration=1.0)
        self.assertTrue(listener.is_awake())
        time.sleep(1.05)
        self.assertFalse(listener.is_awake())

        # Test sleep()
        listener.wake_up(duration=5.0)
        self.assertTrue(listener.is_awake())
        listener.sleep()
        self.assertFalse(listener.is_awake())


if __name__ == "__main__":
    unittest.main()
