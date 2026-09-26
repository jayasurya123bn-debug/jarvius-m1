import sys
import unittest
from unittest.mock import patch, MagicMock

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from actions.send_message import (
    clean_contact_name_for_search,
    strip_tamil_dative_suffix,
    transliterate_tamil,
    parse_whatsapp_voice_command,
    send_message,
)

class TestWhatsAppMessaging(unittest.TestCase):
    """Unit tests for WhatsApp English contact search and Tanglish message preservation."""

    def test_clean_contact_name_english_and_tanglish(self):
        self.assertEqual(clean_contact_name_for_search("arun"), "Arun")
        self.assertEqual(clean_contact_name_for_search("arun ku"), "Arun")
        self.assertEqual(clean_contact_name_for_search("rahul-ku"), "Rahul")
        self.assertEqual(clean_contact_name_for_search("Mom kitta"), "Mom")
        self.assertEqual(clean_contact_name_for_search("karthik kooda"), "Karthik")
        self.assertEqual(clean_contact_name_for_search("to Arun"), "Arun")
        self.assertEqual(clean_contact_name_for_search("contact Rahul"), "Rahul")
        self.assertEqual(clean_contact_name_for_search("Arun in whatsapp"), "Arun")

    def test_clean_contact_name_tamil_script(self):
        # Known Tamil names
        self.assertEqual(clean_contact_name_for_search("அம்மா"), "Amma")
        self.assertEqual(clean_contact_name_for_search("அப்பா"), "Appa")
        self.assertEqual(clean_contact_name_for_search("அருண்"), "Arun")
        self.assertEqual(clean_contact_name_for_search("ராகுல்"), "Rahul")

        # Tamil names with dative suffix
        self.assertEqual(clean_contact_name_for_search("அருணுக்கு"), "Arun")
        self.assertEqual(clean_contact_name_for_search("அம்மாவுக்கு"), "Amma")
        self.assertEqual(clean_contact_name_for_search("ராகுலுக்கு"), "Rahul")
        self.assertEqual(clean_contact_name_for_search("கார்த்திக்குக்கு"), "Karthik")

    def test_strip_tamil_dative_suffix(self):
        self.assertEqual(strip_tamil_dative_suffix("அருணுக்கு"), "அருண்")
        self.assertEqual(strip_tamil_dative_suffix("ராகுலுக்கு"), "ராகுல்")
        self.assertEqual(strip_tamil_dative_suffix("அம்மாவுக்கு"), "அம்மா")

    def test_transliterate_tamil_fallback(self):
        self.assertTrue(len(transliterate_tamil("ரவி")) > 0)
        self.assertTrue(len(transliterate_tamil("கவிதா")) > 0)

    def test_parse_whatsapp_voice_command(self):
        # Pattern 1: Tanglish with 'nu message anupu/podu'
        c1, m1 = parse_whatsapp_voice_command("whatsapp la arun ku naan varren nu message anupu")
        self.assertEqual(c1, "Arun")
        self.assertEqual(m1, "naan varren")

        c1b, m1b = parse_whatsapp_voice_command("whatsapp la arun ku naan reach aagiten nu message podu")
        self.assertEqual(c1b, "Arun")
        self.assertEqual(m1b, "naan reach aagiten")

        # Pattern 2: Tanglish with colon or message action
        c2, m2 = parse_whatsapp_voice_command("whatsapp la rahul ku message pannu: enna pandra")
        self.assertEqual(c2, "Rahul")
        self.assertEqual(m2, "enna pandra")

        # Pattern 0A: Contact first
        c0a, m0a = parse_whatsapp_voice_command("arun ku whatsapp la naan varren nu message anupu")
        self.assertEqual(c0a, "Arun")
        self.assertEqual(m0a, "naan varren")

        # Pattern 0B: Contact first + direct message
        c0b, m0b = parse_whatsapp_voice_command("rahul ku whatsapp la message pannu: enna pandra")
        self.assertEqual(c0b, "Rahul")
        self.assertEqual(m0b, "enna pandra")

        # Pattern 3: English format
        c3, m3 = parse_whatsapp_voice_command("send whatsapp message to Mom: reach aagiten")
        self.assertEqual(c3, "Mom")
        self.assertEqual(m3, "reach aagiten")

        # Pattern 4: Tamil script command
        c4, m4 = parse_whatsapp_voice_command("வாட்ஸ்அப்ல அருணுக்கு நான் வரேன் என்று மெசேஜ் அனுப்பு")
        self.assertEqual(c4, "Arun")
        self.assertEqual(m4, "நான் வரேன்")

        # Non-message commands should NOT match
        self.assertEqual(parse_whatsapp_voice_command("whatsapp open pannu"), (None, None))
        self.assertEqual(parse_whatsapp_voice_command("whatsapp la anna ku call pannu nu sollu"), (None, None))

    @patch("actions.send_message._desktop_send")
    def test_send_message_sanitizes_contact_and_preserves_tanglish(self, mock_desktop):
        mock_desktop.return_value = "Message sent to Arun via WhatsApp."
        
        tanglish_msg = "naan 10 mins la reach aagiduven machan"
        res = send_message(
            parameters={
                "platform": "whatsapp",
                "receiver": "arun ku",
                "message_text": tanglish_msg,
            }
        )
        
        # Verify contact was cleaned to clean English 'Arun' for WhatsApp search
        mock_desktop.assert_called_once_with("WhatsApp", "Arun", tanglish_msg)
        self.assertIn("Message sent to Arun via WhatsApp.", res)

if __name__ == "__main__":
    unittest.main()
