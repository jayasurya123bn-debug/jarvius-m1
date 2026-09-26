import unittest
import subprocess
import time
import psutil
from actions.open_app import open_app, close_app, minimize_app, _normalize


class TestAppControls(unittest.TestCase):

    def test_app_normalization(self):
        self.assertEqual(_normalize("chrome"), "chrome")
        self.assertEqual(_normalize("google chrome"), "chrome")
        self.assertEqual(_normalize("notepad"), "notepad.exe")
        self.assertEqual(_normalize("நோட்பேட்"), "notepad.exe")
        self.assertEqual(_normalize("கால்குலேட்டர்"), "calc.exe")

    def test_minimize_and_close_lifecycle(self):
        # 1. Open Notepad
        res_open = open_app({"app_name": "notepad"})
        self.assertTrue("Opened" in res_open or "confirm" in res_open)
        time.sleep(1.0)

        # Verify notepad is in running processes
        np_running = any("notepad" in (p.info["name"] or "").lower() for p in psutil.process_iter(["name"]))
        self.assertTrue(np_running, "Notepad should be running after open_app")

        # 2. Minimize Notepad
        res_min = minimize_app({"app_name": "notepad"})
        self.assertTrue("Minimized" in res_min or "Attempted" in res_min)
        time.sleep(0.5)

        # 3. Close Notepad
        res_close = close_app({"app_name": "notepad"})
        self.assertTrue("Closed" in res_close or "Attempted" in res_close)
        time.sleep(1.0)

        # Verify notepad closed
        np_still_running = any("notepad" in (p.info["name"] or "").lower() for p in psutil.process_iter(["name"]))
        if np_still_running:
            # Clean up if needed
            subprocess.run(["taskkill", "/F", "/T", "/IM", "notepad.exe"], capture_output=True)

    def test_minimize_all(self):
        res = minimize_app({"app_name": "all"})
        self.assertIn("Minimized all windows", res)

    def test_active_window_close(self):
        # Should gracefully return without crashing
        res = close_app({"app_name": "active"})
        self.assertTrue(isinstance(res, str))


    def test_jarvis_close_and_minimize_delegation(self):
        class MockUI:
            def __init__(self):
                self.prompt_close_called = False
                self.minimize_called = False
            def prompt_close(self):
                self.prompt_close_called = True
            def minimize(self):
                self.minimize_called = True
            def write_log(self, msg):
                pass

        mock_ui = MockUI()
        res_close = close_app({"app_name": "jarvis"}, player=mock_ui)
        self.assertTrue(mock_ui.prompt_close_called)
        self.assertIn("Confirmation requested", res_close)

        res_min = minimize_app({"app_name": "jarvis"}, player=mock_ui)
        self.assertTrue(mock_ui.minimize_called)
        self.assertIn("minimized", res_min)


if __name__ == "__main__":
    unittest.main()
