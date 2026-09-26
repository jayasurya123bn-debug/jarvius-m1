"""
tests/test_youtube_and_auto_answer.py
Unit tests for:
1. YouTube next song shortcut (Shift + N)
2. Auto-Answer & Playwright form/quiz solver with permission gating
"""

import os
import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path

from actions.youtube_video import youtube_video, _handle_next_song
from actions.auto_answer import (
    compose_best_answer,
    _append_to_scratchpad,
    auto_answer,
    SCRATCHPAD_PATH,
    ROOT_SCRATCHPAD_PATH
)


class TestYouTubeNextSong(unittest.TestCase):
    """Verifies that YouTube next song triggers Shift + N shortcut."""

    @patch("actions.youtube_video.is_windows", return_value=True)
    @patch("ctypes.windll")
    def test_youtube_next_song_windows_keybd(self, mock_windll, mock_is_win):
        mock_user32 = MagicMock()
        mock_windll.user32 = mock_user32

        result = youtube_video({"action": "next"})
        self.assertIn("Shift + N", result)

        # Ensure keybd_event was called with VK_SHIFT (0x10) and VK_N (0x4E)
        call_args_list = [c[0] for c in mock_user32.keybd_event.call_args_list]
        keys_pressed = [args[0] for args in call_args_list]
        self.assertIn(0x10, keys_pressed)  # VK_SHIFT
        self.assertIn(0x4E, keys_pressed)  # VK_N

    def test_youtube_action_alias(self):
        res1 = youtube_video({"action": "next_song"})
        self.assertIn("Shift + N", res1)
        res2 = youtube_video({"action": "next_video"})
        self.assertIn("Shift + N", res2)


class TestAutoAnswer(unittest.TestCase):
    """Verifies Auto-Answer composition, scratchpad logging, and permission gating."""

    def test_compose_best_answer_options(self):
        question = "What is the capital of Tamil Nadu?"
        options = ["Chennai", "Madurai", "Coimbatore"]
        ans = compose_best_answer(question, options=options)
        self.assertIn(ans, options)

    def test_scratchpad_append(self):
        test_entry = "| 2026-09-27 | Q: Test Question | A: Test Answer | Status: FILLED |"
        _append_to_scratchpad(test_entry)
        
        # Verify content exists in scratchpad file
        if SCRATCHPAD_PATH.exists():
            content = SCRATCHPAD_PATH.read_text(encoding="utf-8")
            self.assertIn("Test Question", content)

    @patch("actions.auto_answer.run_auto_answer_workflow")
    def test_auto_answer_permission_gated(self, mock_workflow):
        mock_workflow.return_value = {
            "status": "success",
            "url": "https://example.com/quiz",
            "questions_found": 5,
            "fields_filled": 5,
            "screenshot": "automation/screenshots/verify.png",
            "submitted": False,
            "permission_required": True,
            "permission_prompt": "⚠️ Permission kekuren: Submit form / quiz on https://example.com/quiz. Reason: All 5 fields filled. Allow? (yes / no / always)"
        }

        res = auto_answer({"url": "https://example.com/quiz", "allow_submit": False})
        self.assertIn("⚠️ Permission kekuren", res)
        self.assertIn("Allow? (yes / no / always)", res)


if __name__ == "__main__":
    unittest.main()
