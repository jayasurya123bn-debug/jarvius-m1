import unittest
from unittest.mock import MagicMock, patch
import sys
import os
from pathlib import Path

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from actions.antigravity_bridge import antigravity_control
from plugins import antigravity_control as antigravity_plugin
from core.plugin_loader import discover_plugins


class TestAntigravityPlugin(unittest.TestCase):
    def test_plugin_metadata(self):
        self.assertIn("name", antigravity_plugin.PLUGIN)
        self.assertEqual(antigravity_plugin.PLUGIN["name"], "antigravity_control")
        self.assertIn("description", antigravity_plugin.PLUGIN)
        self.assertIn("parameters", antigravity_plugin.PLUGIN)
        self.assertEqual(antigravity_plugin.PLUGIN["parameters"]["type"], "OBJECT")
        self.assertTrue(callable(antigravity_plugin.run))

    def test_antigravity_status(self):
        res = antigravity_plugin.run({"action": "status"})
        self.assertIn("Antigravity IDE Workspace Online", res)
        self.assertIn("CPU load", res)

    def test_antigravity_list(self):
        res = antigravity_plugin.run({"action": "list"})
        self.assertIn("Antigravity workspace has", res)

    def test_antigravity_diagnostics(self):
        res = antigravity_plugin.run({"action": "diagnostics"})
        self.assertIn("Diagnostics complete", res)
        self.assertIn("Gemini API", res)

    def test_antigravity_health_with_player(self):
        mock_player = MagicMock()
        res = antigravity_plugin.run({"action": "health"}, player=mock_player)
        self.assertIn("Online", res)
        mock_player.write_log.assert_called_once()

    def test_plugin_loader_discovery(self):
        plugins_dir = Path(__file__).resolve().parent.parent / "plugins"
        reg = discover_plugins(plugins_dir, core_tool_names={"open_app"})
        self.assertTrue(reg.has("antigravity_control"))
        tool_decls = reg.get_tool_declarations()
        names = [d["name"] for d in tool_decls]
        self.assertIn("antigravity_control", names)

    def test_antigravity_access(self):
        with patch("subprocess.Popen") as mock_popen, \
             patch("actions.antigravity_bridge._find_antigravity_bin", return_value="dummy/antigravity.exe"):
            res = antigravity_plugin.run({"action": "access"})
            self.assertIn("accessed and launched successfully", res)
            mock_popen.assert_called_once()

    def test_antigravity_close(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            res = antigravity_plugin.run({"action": "close"})
            self.assertIn("closed", res)


if __name__ == "__main__":
    unittest.main()

