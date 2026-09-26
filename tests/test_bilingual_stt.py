import unittest
import sys
import os

# Ensure UTF-8 console output for Tamil
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.stt import parse_wake_word, select_bilingual_result, DEFAULT_WAKE_WORDS, LiveSpeechListener
from actions.open_app import _normalize


class TestBilingualSTTAndCommands(unittest.TestCase):

    def test_wake_words_tamil_and_tanglish(self):
        cases = [
            ("ஹேய் ஜார்விஸ் ஓப்பன் குரோம் பண்ணு", True, "ஓப்பன் குரோம் பண்ணு", "ஹேய் ஜார்விஸ்"),
            ("வணக்கம் ஜார்விஸ் எப்படி இருக்கீங்க", True, "எப்படி இருக்கீங்க", "வணக்கம் ஜார்விஸ்"),
            ("ஜார்விஸ் ஓப்பன் குரோம் பண்ணு", True, "ஓப்பன் குரோம் பண்ணு", "ஜார்விஸ்"),
            ("Hey Jarvis open chrome", True, "open chrome", "hey jarvis"),
            ("Hey Jarvius open chrome", True, "open chrome", "hey jarvius"),
            ("jarvis open pannu", True, "open pannu", "jarvis"),
            ("ஏய் ஜார்விஸ் என்ன பண்ற", True, "என்ன பண்ற", "ஏய் ஜார்விஸ்"),
            ("சார்விஸ் குரோம் திற", True, "குரோம் திற", "சார்விஸ்"),
        ]

        for text, exp_wake, exp_cmd, exp_phrase in cases:
            is_wake, cmd, matched = parse_wake_word(text)
            self.assertEqual(is_wake, exp_wake, f"Failed wake detection on '{text}'")
            self.assertEqual(cmd, exp_cmd, f"Failed command extraction on '{text}'")
            self.assertEqual(matched.lower(), exp_phrase.lower(), f"Failed wake phrase on '{text}'")

    def test_bilingual_selection(self):
        # 1. Tamil speech: ta-IN is clean Tamil, en-IN is phonetic distortion
        res = select_bilingual_result(
            res_ta="வணக்கம் ஜார்விஸ் எப்படி இருக்கீங்க",
            res_en="Vanakkam Jarvis Eppadi irukkum"
        )
        self.assertEqual(res, "வணக்கம் ஜார்விஸ் எப்படி இருக்கீங்க")

        # 2. Tanglish command: ta-IN produces Tamil script, en-IN has Tanglish
        res = select_bilingual_result(
            res_ta="ஹேய் ஜார்விஸ் ஓப்பன் குரோம் பண்ணு",
            res_en="hey Jarvis open Chrome Pannu"
        )
        self.assertIn(res, ["ஹேய் ஜார்விஸ் ஓப்பன் குரோம் பண்ணு", "hey Jarvis open Chrome Pannu"])

        # 3. Pure English query: en-IN has clean English, ta-IN has transliterated Tamil
        res = select_bilingual_result(
            res_ta="வாட் இஸ் த டைம் நவ்",
            res_en="what is the time now"
        )
        self.assertEqual(res, "what is the time now")

        # 4. English command: en-IN matches wake word cleanly
        res = select_bilingual_result(
            res_ta="ஹீட் ஆப் இன் குரோம்",
            res_en="hey Jarvis open Chrome"
        )
        self.assertEqual(res, "hey Jarvis open Chrome")

        # 5. One candidate failed: returns the valid one
        self.assertEqual(select_bilingual_result(None, "open chrome"), "open chrome")
        self.assertEqual(select_bilingual_result("நிறுத்து", None), "நிறுத்து")

    def test_tamil_app_aliases(self):
        self.assertEqual(_normalize("குரோம்"), "chrome")
        self.assertEqual(_normalize("கூகுள் குரோம்"), "chrome")
        self.assertEqual(_normalize("யூடியூப்"), "https://www.youtube.com")
        self.assertEqual(_normalize("youtube"), "https://www.youtube.com")
        self.assertEqual(_normalize("வாட்ஸ்அப்"), "WhatsApp")
        self.assertEqual(_normalize("நோட்பேட்"), "notepad.exe")
        self.assertEqual(_normalize("கால்குலேட்டர்"), "calc.exe")
        self.assertEqual(_normalize("ஸ்பாட்டிஃபை"), "Spotify")

    def test_live_speech_listener_defaults(self):
        listener = LiveSpeechListener()
        self.assertEqual(listener.language, "bilingual")
        self.assertEqual(listener.pause_threshold, 1.0)
        self.assertGreaterEqual(listener.recognizer.energy_threshold, 200.0)


if __name__ == "__main__":
    unittest.main()
