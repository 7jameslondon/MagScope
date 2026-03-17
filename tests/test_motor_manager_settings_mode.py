import unittest

try:
    from magscope_motors.manager import MotorManager
except Exception:  # pragma: no cover - optional dependency path for CI environments
    MotorManager = None


def _clear_singletons() -> None:
    if MotorManager is not None:
        type(MotorManager)._instances.pop(MotorManager, None)


@unittest.skipIf(MotorManager is None, "magscope dependencies not available")
class TestMotorManagerSettingsMode(unittest.TestCase):
    def setUp(self):
        _clear_singletons()

    def tearDown(self):
        _clear_singletons()

    def test_test_mode_defaults_to_true_when_missing(self):
        manager = MotorManager()
        manager.set_external_motors_settings({"enabled": True})  # noqa: SLF001 - focused unit test
        manager._load_motor_settings()  # noqa: SLF001 - focused unit test
        self.assertTrue(manager._test_mode)  # noqa: SLF001 - focused unit test

    def test_test_mode_honors_explicit_false(self):
        manager = MotorManager()
        manager.set_external_motors_settings({"enabled": True, "test_mode": False})  # noqa: SLF001 - focused unit test
        manager._load_motor_settings()  # noqa: SLF001 - focused unit test
        self.assertFalse(manager._test_mode)  # noqa: SLF001 - focused unit test

    def test_legacy_settings_fallback_still_supported(self):
        manager = MotorManager()
        manager.settings = {"motors": {"enabled": True, "test_mode": False}}  # noqa: SLF001 - compatibility check
        manager._load_motor_settings()  # noqa: SLF001 - focused unit test
        self.assertFalse(manager._test_mode)  # noqa: SLF001 - focused unit test


if __name__ == "__main__":
    unittest.main()
