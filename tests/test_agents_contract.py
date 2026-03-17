from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parent.parent
AGENTS_PATH = ROOT / "AGENTS.md"


def _agents_text() -> str:
    return AGENTS_PATH.read_text(encoding="utf-8")


def _section(text: str, title: str) -> str:
    marker = f"## {title}"
    start = text.find(marker)
    assert start != -1, f"Missing section: {title}"
    next_start = text.find("\n## ", start + len(marker))
    if next_start == -1:
        return text[start:]
    return text[start:next_start]


class TestAgentsContract(unittest.TestCase):
    def test_agents_file_exists(self):
        self.assertTrue(AGENTS_PATH.exists(), "AGENTS.md must exist at repository root")


    def test_required_top_level_sections_present(self):
        text = _agents_text()
        required = [
            "Project",
            "Goal",
            "Locked Safety Rules (Do Not Relax)",
            "Required Package Layout",
            "Required Interfaces",
            "GUI Requirements (Motor Panels)",
            "Required Commands (commands.py)",
            "Settings Contract (settings.yaml + motors_settings.yaml)",
            "Implementation Order",
            "Plot Integration Plan",
            "Required Test Scenarios",
            "Definition of Done",
            "Deferred Work",
        ]
        for title in required:
            self.assertIn(f"## {title}", text, f"Missing section header: {title}")


    def test_locked_safety_rules_remain_enforced(self):
        text = _section(_agents_text(), "Locked Safety Rules (Do Not Relax)")
        expected_phrases = [
            "No auto-home and no auto-zero on startup.",
            "rejected unless motors are armed",
            "Arming remains active until explicitly disarmed.",
            "objective/linear hard limits",
            "rotary hard-limit check disabled",
            "must pass all safety checks",
            "Stop-all must be available",
            "Safety failures must not touch hardware",
            "`test_mode` defaults to `true`",
        ]
        for phrase in expected_phrases:
            self.assertIn(phrase, text, f"Safety rule missing phrase: {phrase}")


    def test_required_motor_files_listed(self):
        text = _section(_agents_text(), "Required Package Layout")
        required_files = [
            "magscope_motors/",
            "manager.py",
            "commands.py",
            "control_panel.py",
            "beadlock_ext.py",
            "adapters/pi_objective.py",
            "adapters/zaber_linear.py",
            "adapters/zaber_rotary.py",
        ]
        for file_name in required_files:
            self.assertIn(file_name, text, f"Missing package entry: {file_name}")


    def test_settings_contract_contains_required_motor_keys(self):
        text = _section(_agents_text(), "Settings Contract (settings.yaml + motors_settings.yaml)")
        expected_keys = [
            "settings.yaml",
            "motors_settings.yaml",
            "ROI:",
            "enabled:",
            "require_arm:",
            "discovery_mode:",
            "objective:",
            "linear:",
            "rotary:",
            "min_turns:",
            "max_turns:",
            "test_mode:",
            "test_caps:",
            "session_window:",
        ]
        for key in expected_keys:
            self.assertIn(key, text, f"Missing settings contract key: {key}")


    def test_gui_requirements_cover_three_panels_and_two_positions(self):
        text = _section(_agents_text(), "GUI Requirements (Motor Panels)")
        for phrase in ["objective panel", "linear panel", "rotary panel"]:
            self.assertIn(phrase, text, f"Missing GUI panel requirement: {phrase}")
        self.assertIn("actual_position", text)
        self.assertIn("target_position", text)


    def test_plot_plan_has_three_motors_and_two_traces(self):
        text = _section(_agents_text(), "Plot Integration Plan")
        for phrase in ["objective, linear, rotary", "actual current position", "commanded target position"]:
            self.assertIn(phrase, text, f"Missing plot plan requirement: {phrase}")
        for unit in ["objective: nm", "linear: mm", "rotary: turns"]:
            self.assertIn(unit, text, f"Missing unit requirement: {unit}")


    def test_required_test_scenarios_include_gui_and_plot_checks(self):
        text = _section(_agents_text(), "Required Test Scenarios")
        expected = [
            "GUI shows 3 motor panels",
            "GUI exposes all prior motor controls",
            "Each motor plot shows both actual and target traces",
            "Plot timestamps are monotonic",
        ]
        for phrase in expected:
            self.assertIn(phrase, text, f"Missing required test scenario phrase: {phrase}")


    def test_definition_of_done_includes_ui_and_plot_completion(self):
        text = _section(_agents_text(), "Definition of Done")
        expected = [
            "UI includes 3 styled motor panels",
            "Plotting includes both actual and target position traces",
            "integration works with motors disabled/enabled",
        ]
        for phrase in expected:
            self.assertIn(phrase, text, f"Missing DoD criterion phrase: {phrase}")


if __name__ == "__main__":
    unittest.main()
