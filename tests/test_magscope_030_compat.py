from __future__ import annotations

from pathlib import Path
import unittest

from magscope.settings import MagScopeSettings

import main
from scope_config import load_motors_settings


ROOT = Path(__file__).resolve().parent.parent


class TestMagScope030Compat(unittest.TestCase):
    def test_control_panel_uses_new_ui_widgets_path(self):
        source = (ROOT / "magscope_motors" / "control_panel.py").read_text(encoding="utf-8")
        self.assertIn("from magscope.ui.widgets import LabeledLineEdit", source)
        self.assertNotIn("magscope.gui.widgets", source)

    def test_settings_yaml_is_valid_for_magscope_settings(self):
        settings = MagScopeSettings.from_yaml(str(ROOT / "settings.yaml"))
        self.assertEqual(settings["ROI"], 28)

    def test_motors_sidecar_loads(self):
        motors = load_motors_settings(ROOT / "motors_settings.yaml")
        self.assertIsInstance(motors, dict)
        self.assertTrue(motors.get("enabled", False))
        self.assertIn("objective", motors)
        self.assertIn("linear", motors)
        self.assertIn("rotary", motors)

    def test_shared_memory_recovery_names_include_live_profile_buffer(self):
        class _FakeScope:
            _hardware = {"MotorManager": object()}

        names = main._shared_memory_names_for_scope(_FakeScope())
        self.assertIn("LiveProfileBuffer", names)
        self.assertIn("ProfilesBuffer", names)

    def test_main_and_connect_modules_import(self):
        # Importing here validates the startup modules can be loaded on 0.3.0.
        import connect_motors  # noqa: F401
        import magscope_motors.control_panel  # noqa: F401


if __name__ == "__main__":
    unittest.main()
