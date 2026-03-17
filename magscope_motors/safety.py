from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class FaultCode(IntEnum):
    NONE = 0
    NOT_CONNECTED = 1
    NOT_ARMED = 2
    ARM_TIMEOUT = 3  # Legacy code retained for telemetry compatibility; not emitted by SafetyGuard.
    HARD_LIMIT = 4
    SESSION_WINDOW = 5
    STEP_CAP = 6  # Legacy code retained for telemetry compatibility; not emitted by SafetyGuard.
    SPEED_CAP = 7
    ADAPTER_ERROR = 8
    DISABLED = 9
    BAD_AXIS = 10
    PERMISSION_REQUIRED = 11


FAULT_MESSAGES = {
    FaultCode.NONE: "",
    FaultCode.NOT_CONNECTED: "motor not connected",
    FaultCode.NOT_ARMED: "motors are not armed",
    FaultCode.ARM_TIMEOUT: "legacy arm-timeout code (unused)",
    FaultCode.HARD_LIMIT: "hard-limit check failed",
    FaultCode.SESSION_WINDOW: "session-window check failed",
    FaultCode.STEP_CAP: "legacy step-cap code (unused)",
    FaultCode.SPEED_CAP: "speed-cap check failed",
    FaultCode.ADAPTER_ERROR: "hardware adapter error",
    FaultCode.DISABLED: "motors disabled in settings",
    FaultCode.BAD_AXIS: "unknown motor axis",
    FaultCode.PERMISSION_REQUIRED: "operator move permission is required",
}

LINEAR_USER_MIN_MM = 0.0
LINEAR_USER_MAX_MM = 34.5


@dataclass
class SafetyDecision:
    allowed: bool
    fault_code: FaultCode = FaultCode.NONE
    reason: str = ""


class SafetyGuard:
    """Centralized safety checks for all move commands."""

    def __init__(self, motors_settings: dict):
        # Locked safety rule: movement always requires arming.
        self.require_arm = True
        self._armed = False

        linear_settings = motors_settings.get("linear", {})
        linear_min = self._coerce_limit(linear_settings.get("min_mm"), LINEAR_USER_MIN_MM)
        linear_max = self._coerce_limit(linear_settings.get("max_mm"), LINEAR_USER_MAX_MM)
        linear_min = max(linear_min, LINEAR_USER_MIN_MM)
        linear_max = min(linear_max, LINEAR_USER_MAX_MM)
        if linear_max < linear_min:
            linear_min = LINEAR_USER_MIN_MM
            linear_max = LINEAR_USER_MAX_MM

        self.hard_limits = {
            "objective": (
                motors_settings.get("objective", {}).get("min_nm"),
                motors_settings.get("objective", {}).get("max_nm"),
            ),
            "linear": (linear_min, linear_max),
            "rotary": (
                motors_settings.get("rotary", {}).get("min_turns"),
                motors_settings.get("rotary", {}).get("max_turns"),
            ),
        }

        # Speed caps are intentionally disabled.
        self.speed_caps = {"objective": None, "linear": None, "rotary": None}

        session_window = motors_settings.get("session_window", {})
        self.session_window_enabled = bool(session_window.get("enabled", True))
        self.session_window = {
            "objective": session_window.get("objective_nm"),
            "linear": session_window.get("linear_mm"),
            "rotary": session_window.get("rotary_turns"),
        }
        self.session_origin: dict[str, float] = {}

    @staticmethod
    def _coerce_limit(value: object, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return float(default)

    def arm(self) -> None:
        self._armed = True

    def disarm(self) -> None:
        self._armed = False

    def is_armed(self) -> bool:
        if not self.require_arm:
            return True
        return self._armed

    def set_session_origin(self, axis: str, position: float) -> None:
        self.session_origin[axis] = float(position)

    def update_session_window(
        self,
        *,
        objective_nm: float | None = None,
        linear_mm: float | None = None,
        rotary_turns: float | None = None,
        enabled: bool | None = None,
    ) -> None:
        if objective_nm is not None:
            self.session_window["objective"] = float(objective_nm)
        if linear_mm is not None:
            self.session_window["linear"] = float(linear_mm)
        if rotary_turns is not None:
            self.session_window["rotary"] = float(rotary_turns)
        if enabled is not None:
            self.session_window_enabled = bool(enabled)

    def set_speed_cap(self, axis: str, max_speed: float | None) -> None:
        if axis not in self.speed_caps:
            return
        if max_speed is None:
            self.speed_caps[axis] = None
            return
        value = float(max_speed)
        if value <= 0:
            return
        self.speed_caps[axis] = value

    def get_speed_cap(self, axis: str) -> float | None:
        if axis not in self.speed_caps:
            return None
        cap = self.speed_caps.get(axis)
        if cap is None:
            return None
        value = float(cap)
        if value <= 0:
            return None
        return value

    def validate(
        self,
        *,
        axis: str,
        current: float,
        target: float,
        speed: float | None,
    ) -> SafetyDecision:
        if self.require_arm and not self.is_armed():
            code = FaultCode.NOT_ARMED
            return SafetyDecision(allowed=False, fault_code=code, reason=FAULT_MESSAGES[code])

        # Rotary hard-limit gating is intentionally disabled; objective and linear
        # retain strict min/max enforcement.
        if axis != "rotary":
            min_limit, max_limit = self.hard_limits.get(axis, (None, None))
            if min_limit is None or max_limit is None:
                return SafetyDecision(False, FaultCode.HARD_LIMIT, "hard limits are not configured")
            if min_limit is not None and target < float(min_limit):
                return SafetyDecision(False, FaultCode.HARD_LIMIT, f"target {target} below min {min_limit}")
            if max_limit is not None and target > float(max_limit):
                return SafetyDecision(False, FaultCode.HARD_LIMIT, f"target {target} above max {max_limit}")

        if self.session_window_enabled:
            window = self.session_window.get(axis)
            origin = self.session_origin.get(axis)
            if window is None or origin is None:
                return SafetyDecision(False, FaultCode.SESSION_WINDOW, "session window is not configured")
            if window is not None and origin is not None and abs(target - origin) > float(window):
                return SafetyDecision(
                    False,
                    FaultCode.SESSION_WINDOW,
                    f"target {target} exceeds session window {window} from {origin}",
                )

        return SafetyDecision(allowed=True)
