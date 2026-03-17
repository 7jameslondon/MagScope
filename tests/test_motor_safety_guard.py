import unittest


try:
    from magscope_motors.safety import FaultCode, SafetyGuard
except Exception:  # pragma: no cover - optional dependency path for CI environments
    FaultCode = None
    SafetyGuard = None


@unittest.skipIf(SafetyGuard is None, "magscope dependencies not available")
class TestSafetyGuard(unittest.TestCase):
    def setUp(self):
        settings = {
            "require_arm": True,
            "objective": {"min_nm": -10, "max_nm": 10},
            "linear": {"min_mm": -1, "max_mm": 1},
            "rotary": {"min_turns": -1, "max_turns": 1},
            "test_caps": {
                "linear_max_speed_mm_s": 0.5,
                "rotary_max_speed_turns_s": 0.5,
            },
            "session_window": {
                "enabled": True,
                "objective_nm": 5,
                "linear_mm": 1,
                "rotary_turns": 1,
            },
        }
        self.guard = SafetyGuard(settings)
        self.guard.set_session_origin("objective", 0)
        self.guard.set_session_origin("linear", 0)
        self.guard.set_session_origin("rotary", 0)

    def test_move_blocked_when_not_armed(self):
        decision = self.guard.validate(axis="objective", current=0, target=1, speed=None)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.fault_code, FaultCode.NOT_ARMED)

    def test_move_stays_allowed_until_explicit_disarm(self):
        import time

        self.guard.arm()
        time.sleep(0.02)
        decision = self.guard.validate(axis="objective", current=0, target=1, speed=None)
        self.assertTrue(decision.allowed)

        self.guard.disarm()
        decision_after_disarm = self.guard.validate(axis="objective", current=0, target=1, speed=None)
        self.assertFalse(decision_after_disarm.allowed)
        self.assertEqual(decision_after_disarm.fault_code, FaultCode.NOT_ARMED)

    def test_hard_limit_blocked(self):
        self.guard.arm()
        decision = self.guard.validate(axis="objective", current=0, target=11, speed=None)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.fault_code, FaultCode.HARD_LIMIT)

    def test_objective_min_limit_blocked(self):
        self.guard.arm()
        decision = self.guard.validate(axis="objective", current=0, target=-11, speed=None)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.fault_code, FaultCode.HARD_LIMIT)

    def test_linear_min_limit_blocked(self):
        self.guard.arm()
        decision = self.guard.validate(axis="linear", current=0.2, target=-0.1, speed=0.1)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.fault_code, FaultCode.HARD_LIMIT)

    def test_linear_max_limit_blocked(self):
        self.guard.arm()
        decision = self.guard.validate(axis="linear", current=0.8, target=1.2, speed=0.1)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.fault_code, FaultCode.HARD_LIMIT)

    def test_rotary_not_hard_limited(self):
        settings = {
            "require_arm": True,
            "objective": {"min_nm": -10, "max_nm": 10},
            "linear": {"min_mm": -1, "max_mm": 1},
            "rotary": {"min_turns": -1, "max_turns": 1},
            "test_caps": {
                "linear_max_speed_mm_s": 0.5,
                "rotary_max_speed_turns_s": 0.5,
            },
            "session_window": {
                "enabled": False,
                "objective_nm": 5,
                "linear_mm": 1,
                "rotary_turns": 1,
            },
        }
        guard = SafetyGuard(settings)
        guard.set_session_origin("rotary", 0.0)
        guard.arm()
        decision = guard.validate(axis="rotary", current=0.0, target=5.0, speed=0.1)
        self.assertTrue(decision.allowed)

    def test_session_window_blocked(self):
        self.guard.arm()
        decision = self.guard.validate(axis="objective", current=0, target=6, speed=None)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.fault_code, FaultCode.SESSION_WINDOW)

    def test_large_step_allowed_without_step_cap(self):
        self.guard.arm()
        decision = self.guard.validate(axis="objective", current=0.0, target=4.0, speed=None)
        self.assertTrue(decision.allowed)

    def test_high_speed_not_blocked_without_speed_caps(self):
        self.guard.arm()
        decision = self.guard.validate(axis="linear", current=0.0, target=0.1, speed=0.7)
        self.assertTrue(decision.allowed)

    def test_valid_move_allowed(self):
        self.guard.arm()
        decision = self.guard.validate(axis="linear", current=0.0, target=0.1, speed=0.4)
        self.assertTrue(decision.allowed)

    def test_linear_user_cap_34_5mm_enforced(self):
        settings = {
            "require_arm": True,
            "objective": {"min_nm": -10, "max_nm": 10},
            "linear": {"min_mm": -10, "max_mm": 100},
            "rotary": {"min_turns": -1, "max_turns": 1},
            "test_caps": {
                "linear_max_speed_mm_s": 10,
                "rotary_max_speed_turns_s": 0.5,
            },
            "session_window": {
                "enabled": True,
                "objective_nm": 5,
                "linear_mm": 100,
                "rotary_turns": 1,
            },
        }
        guard = SafetyGuard(settings)
        guard.set_session_origin("linear", 0)
        guard.arm()
        decision = guard.validate(axis="linear", current=34.0, target=35.0, speed=0.5)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.fault_code, FaultCode.HARD_LIMIT)


if __name__ == "__main__":
    unittest.main()
