from __future__ import annotations

import csv
from collections.abc import Mapping
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from time import sleep, time

import numpy as np

from magscope import Delivery, HardwareManagerBase, register_ipc_command
from magscope.ipc_commands import SetSettingsCommand, ShowMessageCommand
from magscope.utils import register_script_command

from .adapters.pi_objective import PIObjectiveAdapter
from .adapters.zaber_linear import ZaberLinearAdapter
from .adapters.zaber_rotary import ZaberRotaryAdapter
from .commands import (
    ConnectMotorsCommand,
    DisconnectMotorsCommand,
    LoadForceCalibrantCommand,
    MoveLinearToForceCommand,
    MoveLinearAbsoluteCommand,
    MoveLinearRelativeCommand,
    MoveObjectiveAbsoluteCommand,
    MoveObjectiveRelativeCommand,
    MoveRotaryAbsoluteCommand,
    MoveRotaryRelativeCommand,
    RunLinearForceRampCommand,
    SetMotorArmedCommand,
    SetLinearUiSpeedCommand,
    SetSessionSafetyWindowCommand,
    StopAllMotorsCommand,
    UnloadForceCalibrantCommand,
    UpdateForceCalibrantStatusCommand,
    UpdateMotorFaultCommand,
    UpdateMotorStatusCommand,
)
from .force_calibration import ForceCalibrantError, ForceCalibrantModel
from .safety import FAULT_MESSAGES, FaultCode, SafetyGuard


AXIS_ORDER = ("objective", "linear", "rotary")
AXIS_INDEX = {"objective": 0.0, "linear": 1.0, "rotary": 2.0}
BUFFER_COLUMNS = 8
BUFFER_SHAPE = (8192, BUFFER_COLUMNS)


@dataclass
class AxisState:
    actual_position: float = 0.0
    target_position: float = 0.0
    velocity: float = 0.0
    connected: bool = False
    fault_code: int = int(FaultCode.NONE)
    fault_text: str = ""
    timestamp: float = 0.0


class MotorManager(HardwareManagerBase):
    """Single manager process controlling objective, linear, and rotary motors."""

    def __init__(self, motors_settings: Mapping[str, object] | None = None):
        super().__init__()
        self.buffer_shape = BUFFER_SHAPE
        self._state = {axis: AxisState() for axis in AXIS_ORDER}
        self._adapters = {
            "objective": PIObjectiveAdapter(),
            "linear": ZaberLinearAdapter(),
            "rotary": ZaberRotaryAdapter(),
        }
        self._motors_settings: dict = {}
        self._safety_guard = SafetyGuard({})
        self._enabled = True
        self._test_mode = True
        self._discovery_mode = "settings_then_fallback_scan"
        self._fault_debounce: dict[str, tuple[int, str]] = {}
        self._telemetry_history: deque[tuple[float, ...]] = deque(maxlen=20000)
        self._telemetry_dump_enabled = False
        self._telemetry_file: Path | None = None
        self._default_move_speed = {"objective": None, "linear": 0.2, "rotary": 0.1}
        self._linear_ui_speed_mm_s: float | None = None
        self._force_calibrant = ForceCalibrantModel()
        self._active_force_ramp: dict[str, object] | None = None
        self._last_force_calibrant_status: UpdateForceCalibrantStatusCommand | None = None
        self._external_motors_settings: dict[str, object] | None = None
        self.set_external_motors_settings(motors_settings)

    def setup(self):
        super().setup()
        self._load_motor_settings()
        if self._enabled:
            self.connect()
        for axis in AXIS_ORDER:
            self._write_axis_status(axis)

    def close(self):
        self.disconnect()
        close = getattr(super(), "close", None)
        if callable(close):
            close()

    def cleanup(self):
        self.disconnect()
        cleanup = getattr(super(), "cleanup", None)
        if callable(cleanup):
            cleanup()

    def teardown(self):
        self.disconnect()
        teardown = getattr(super(), "teardown", None)
        if callable(teardown):
            teardown()

    def shutdown(self):
        self.disconnect()
        shutdown = getattr(super(), "shutdown", None)
        if callable(shutdown):
            shutdown()

    def connect(self):
        if not self._enabled:
            for axis in AXIS_ORDER:
                self._emit_fault(axis, FaultCode.DISABLED, FAULT_MESSAGES[FaultCode.DISABLED])
            return

        connected_any = False
        for axis in AXIS_ORDER:
            config = self._motors_settings.get(axis, {})
            adapter = self._adapters[axis]
            try:
                try:
                    adapter.disconnect()
                except Exception:
                    pass
                self._connect_axis_with_fallback(axis, adapter, config)
                status = adapter.get_status()
                state = self._state[axis]
                state.connected = bool(status.get("connected", True))
                state.actual_position = float(status.get("actual_position", state.actual_position))
                state.target_position = float(status.get("target_position", state.actual_position))
                state.velocity = float(status.get("velocity", state.velocity))
                state.fault_code = int(FaultCode.NONE)
                state.fault_text = ""
                self._safety_guard.set_session_origin(axis, state.actual_position)
                self._write_axis_status(axis)
                connected_any = connected_any or state.connected
            except Exception as exc:
                self._emit_fault(axis, FaultCode.ADAPTER_ERROR, str(exc), notify=False)

        if connected_any:
            # Keep the safety rule intact while making connected motors immediately responsive.
            self._safety_guard.arm()

    @register_ipc_command(DisconnectMotorsCommand)
    @register_script_command(DisconnectMotorsCommand)
    def disconnect(self):
        self._cancel_force_ramp()
        self._safety_guard.disarm()
        for axis in AXIS_ORDER:
            adapter = self._adapters[axis]
            try:
                adapter.disconnect()
            except Exception:
                pass
            state = self._state[axis]
            state.connected = False
            state.velocity = 0.0
            self._write_axis_status(axis)

    def fetch(self):
        self._advance_force_ramp()
        for axis in AXIS_ORDER:
            self._refresh_axis(axis)
            self._write_axis_status(axis)
        sleep(0.05)

    @register_ipc_command(ConnectMotorsCommand)
    @register_script_command(ConnectMotorsCommand)
    def connect_motors(self):
        self.connect()

    @register_ipc_command(SetMotorArmedCommand)
    @register_script_command(SetMotorArmedCommand)
    def set_motor_armed(self, value: bool):
        if value:
            self._safety_guard.arm()
        else:
            self._safety_guard.disarm()
        for axis in AXIS_ORDER:
            self._write_axis_status(axis)

    @register_ipc_command(StopAllMotorsCommand)
    @register_script_command(StopAllMotorsCommand)
    def stop_all_motors(self):
        self._cancel_force_ramp()
        for axis in AXIS_ORDER:
            adapter = self._adapters[axis]
            try:
                adapter.stop()
            except Exception as exc:
                self._emit_fault(axis, FaultCode.ADAPTER_ERROR, f"stop failed: {exc}")
        for axis in AXIS_ORDER:
            self._refresh_axis(axis)
            self._write_axis_status(axis)

    @register_ipc_command(MoveObjectiveRelativeCommand)
    @register_script_command(MoveObjectiveRelativeCommand)
    def move_objective_relative(self, delta_nm: float, speed_nm_s: float | None = None, source: str = "ui"):
        self._dispatch_move("objective", relative_delta=float(delta_nm), speed=speed_nm_s, source=source)

    @register_ipc_command(MoveObjectiveAbsoluteCommand)
    @register_script_command(MoveObjectiveAbsoluteCommand)
    def move_objective_absolute(self, position_nm: float, speed_nm_s: float | None = None, source: str = "ui"):
        self._dispatch_move("objective", absolute_target=float(position_nm), speed=speed_nm_s, source=source)

    @register_ipc_command(MoveLinearRelativeCommand)
    @register_script_command(MoveLinearRelativeCommand)
    def move_linear_relative(self, delta_mm: float, speed_mm_s: float | None = None, source: str = "ui"):
        self._dispatch_move("linear", relative_delta=float(delta_mm), speed=speed_mm_s, source=source)

    @register_ipc_command(MoveLinearAbsoluteCommand)
    @register_script_command(MoveLinearAbsoluteCommand)
    def move_linear_absolute(self, position_mm: float, speed_mm_s: float | None = None, source: str = "ui"):
        self._dispatch_move("linear", absolute_target=float(position_mm), speed=speed_mm_s, source=source)

    @register_ipc_command(MoveRotaryRelativeCommand)
    @register_script_command(MoveRotaryRelativeCommand)
    def move_rotary_relative(
        self,
        delta_turns: float,
        speed_turns_s: float | None = None,
        source: str = "ui",
    ):
        self._dispatch_move("rotary", relative_delta=float(delta_turns), speed=speed_turns_s, source=source)

    @register_ipc_command(MoveRotaryAbsoluteCommand)
    @register_script_command(MoveRotaryAbsoluteCommand)
    def move_rotary_absolute(
        self,
        position_turns: float,
        speed_turns_s: float | None = None,
        source: str = "ui",
    ):
        self._dispatch_move("rotary", absolute_target=float(position_turns), speed=speed_turns_s, source=source)

    @register_ipc_command(SetSessionSafetyWindowCommand)
    @register_script_command(SetSessionSafetyWindowCommand)
    def set_session_safety_window(
        self,
        objective_nm: float | None = None,
        linear_mm: float | None = None,
        rotary_turns: float | None = None,
        enabled: bool | None = None,
    ):
        self._safety_guard.update_session_window(
            objective_nm=objective_nm,
            linear_mm=linear_mm,
            rotary_turns=rotary_turns,
            enabled=enabled,
        )

    @register_ipc_command(LoadForceCalibrantCommand)
    @register_script_command(LoadForceCalibrantCommand)
    def load_force_calibrant(self, path: str, source: str = "ui"):
        try:
            self._force_calibrant.load(path)
        except ForceCalibrantError as exc:
            self._force_calibrant.unload()
            message = f"force calibrant load failed: {exc}"
            self._emit_force_calibrant_status(loaded=False, path=None, message=str(exc))
            self._emit_fault("linear", FaultCode.ADAPTER_ERROR, message, notify=(source == "ui"))
            return

        data = self._force_calibrant.data
        force_min = None
        force_max = None
        if data is not None and data.size > 0:
            force_min = float(np.min(data[:, 1]))
            force_max = float(np.max(data[:, 1]))
        self._emit_force_calibrant_status(
            loaded=True,
            path=self._force_calibrant.path,
            message="force calibrant loaded",
            force_min_pn=force_min,
            force_max_pn=force_max,
        )

    @register_ipc_command(UnloadForceCalibrantCommand)
    @register_script_command(UnloadForceCalibrantCommand)
    def unload_force_calibrant(self, source: str = "ui"):
        del source
        self._force_calibrant.unload()
        self._cancel_force_ramp()
        self._emit_force_calibrant_status(loaded=False, path=None, message="force calibrant unloaded")

    @register_ipc_command(SetLinearUiSpeedCommand)
    @register_script_command(SetLinearUiSpeedCommand)
    def set_linear_ui_speed(self, speed_mm_s: float | None = None):
        if speed_mm_s is None:
            self._linear_ui_speed_mm_s = None
            return
        try:
            speed = float(speed_mm_s)
        except (TypeError, ValueError):
            return
        if speed <= 0:
            return
        self._linear_ui_speed_mm_s = speed

    @register_ipc_command(MoveLinearToForceCommand)
    @register_script_command(MoveLinearToForceCommand)
    def move_linear_to_force(self, force_pn: float, speed_mm_s: float | None = None, source: str = "ui"):
        if not self._force_calibrant.is_loaded():
            self._emit_fault(
                "linear",
                FaultCode.ADAPTER_ERROR,
                "force calibrant not loaded",
                notify=False,
            )
            if source == "ui":
                try:
                    self.send_ipc(ShowMessageCommand(text="Warning", details="Force Calibrant not Loaded"))
                except Exception:
                    pass
            return

        target_mm = self._force_calibrant.force_to_motor(float(force_pn))
        if target_mm is None:
            self._emit_fault(
                "linear",
                FaultCode.ADAPTER_ERROR,
                f"target force {float(force_pn)} pN is outside calibrant range",
                notify=(source == "ui"),
            )
            return

        speed = self._resolve_linear_force_speed(speed_mm_s)
        self._dispatch_move("linear", absolute_target=float(target_mm), speed=speed, source=source)

    @register_ipc_command(RunLinearForceRampCommand)
    @register_script_command(RunLinearForceRampCommand)
    def run_linear_force_ramp(
        self,
        start_pn: float,
        stop_pn: float,
        rate_pn_s: float,
        speed_mm_s: float | None = None,
        source: str = "ui",
    ):
        if not self._force_calibrant.is_loaded():
            self._emit_fault(
                "linear",
                FaultCode.ADAPTER_ERROR,
                "force calibrant not loaded",
                notify=(source == "ui"),
            )
            return

        try:
            profile = self._force_calibrant.build_force_ramp(
                start_pn=float(start_pn),
                stop_pn=float(stop_pn),
                rate_pn_s=float(rate_pn_s),
                points=100,
            )
        except ForceCalibrantError as exc:
            self._emit_fault(
                "linear",
                FaultCode.ADAPTER_ERROR,
                f"force ramp rejected: {exc}",
                notify=(source == "ui"),
            )
            return

        positions = np.asarray(profile.positions_mm, dtype=np.float64)
        velocities = np.asarray(profile.velocities_mm_s, dtype=np.float64)
        if positions.size == 0 or not np.all(np.isfinite(positions)):
            self._emit_fault(
                "linear",
                FaultCode.ADAPTER_ERROR,
                "force ramp produced invalid motor positions",
                notify=(source == "ui"),
            )
            return

        speed = self._resolve_linear_force_speed(speed_mm_s)
        if not self._prevalidate_linear_force_ramp(positions=positions, speed=speed, source=source):
            return

        if self._start_linear_force_ramp_stream(
            positions=positions,
            velocities=velocities,
            dt_s=float(profile.dt_s),
            speed=speed,
            source=source,
        ):
            return

        self._start_linear_force_ramp_segmented(
            positions=positions,
            dt_s=float(profile.dt_s),
            speed=speed,
            source=source,
        )

    @register_ipc_command(UpdateMotorStatusCommand)
    def update_motor_status(
        self,
        axis: str,
        timestamp: float,
        actual_position: float,
        target_position: float,
        velocity: float | None = None,
        connected: bool = False,
        armed: bool = False,
    ):
        if axis not in self._state:
            return
        state = self._state[axis]
        state.timestamp = float(timestamp)
        state.actual_position = float(actual_position)
        state.target_position = float(target_position)
        if velocity is not None:
            state.velocity = float(velocity)
        state.connected = bool(connected)
        if armed:
            self._safety_guard.arm()
        self._write_axis_status(axis)

    @register_ipc_command(UpdateMotorFaultCommand)
    def update_motor_fault(self, axis: str, timestamp: float, reason: str, requested_target: float | None = None):
        if axis not in self._state:
            return
        self._state[axis].timestamp = float(timestamp)
        if requested_target is not None:
            self._state[axis].target_position = float(requested_target)
        self._emit_fault(axis, FaultCode.ADAPTER_ERROR, reason)

    @register_ipc_command(UpdateForceCalibrantStatusCommand)
    def update_force_calibrant_status(
        self,
        loaded: bool,
        path: str | None = None,
        message: str = "",
        force_min_pn: float | None = None,
        force_max_pn: float | None = None,
    ):
        self._last_force_calibrant_status = UpdateForceCalibrantStatusCommand(
            loaded=bool(loaded),
            path=path,
            message=str(message),
            force_min_pn=None if force_min_pn is None else float(force_min_pn),
            force_max_pn=None if force_max_pn is None else float(force_max_pn),
        )

    @register_ipc_command(SetSettingsCommand, delivery=Delivery.BROADCAST, target="ManagerProcessBase")
    def set_settings(self, settings):
        super().set_settings(settings)
        self._load_motor_settings()

    def set_external_motors_settings(self, motors_settings: Mapping[str, object] | None) -> None:
        if motors_settings is None:
            self._external_motors_settings = None
            return
        self._external_motors_settings = dict(motors_settings)

    @staticmethod
    def _coerce_legacy_motors_settings(settings_obj) -> dict[str, object]:
        if settings_obj is None:
            return {}
        getter = getattr(settings_obj, "get", None)
        if not callable(getter):
            return {}
        legacy = getter("motors", {})
        if isinstance(legacy, Mapping):
            return dict(legacy)
        return {}

    def _load_motor_settings(self) -> None:
        if self._external_motors_settings:
            self._motors_settings = dict(self._external_motors_settings)
        else:
            self._motors_settings = self._coerce_legacy_motors_settings(self.settings)
        self._enabled = bool(self._motors_settings.get("enabled", True))
        self._test_mode = bool(self._motors_settings.get("test_mode", True))
        self._discovery_mode = str(self._motors_settings.get("discovery_mode", "settings_then_fallback_scan"))
        self._safety_guard = SafetyGuard(self._motors_settings)
        linear_settings = self._motors_settings.get("linear", {})
        rotary_settings = self._motors_settings.get("rotary", {})
        self._default_move_speed = {
            "objective": None,
            "linear": self._coerce_positive_speed(linear_settings.get("default_speed_mm_s"), 0.2),
            "rotary": self._coerce_positive_speed(rotary_settings.get("default_speed_turns_s"), 0.1),
        }
        self._telemetry_dump_enabled = bool(self._motors_settings.get("telemetry_dump_enabled", False))
        self._telemetry_file = None
        self._linear_ui_speed_mm_s = None
        self._cancel_force_ramp()

    def _resolve_linear_force_speed(self, requested_speed: float | None) -> float | None:
        if requested_speed is not None:
            return float(requested_speed)
        if self._linear_ui_speed_mm_s is not None:
            return float(self._linear_ui_speed_mm_s)
        return self._default_move_speed.get("linear")

    def _prevalidate_linear_force_ramp(self, *, positions: np.ndarray, speed: float | None, source: str) -> bool:
        self._refresh_axis("linear")
        state = self._state["linear"]
        if not state.connected:
            self._emit_fault(
                "linear",
                FaultCode.NOT_CONNECTED,
                FAULT_MESSAGES[FaultCode.NOT_CONNECTED],
                notify=(source == "ui"),
            )
            return False

        current = float(state.actual_position)
        for target in positions:
            decision = self._safety_guard.validate(axis="linear", current=current, target=float(target), speed=speed)
            if not decision.allowed:
                self._emit_fault(
                    "linear",
                    decision.fault_code,
                    f"force ramp blocked: {decision.reason}",
                    requested_target=float(target),
                    notify=(source == "ui"),
                )
                return False
            current = float(target)
        return True

    def _start_linear_force_ramp_stream(
        self,
        *,
        positions: np.ndarray,
        velocities: np.ndarray,
        dt_s: float,
        speed: float | None,
        source: str,
    ) -> bool:
        adapter = self._adapters["linear"]
        execute = getattr(adapter, "execute_force_ramp_stream", None)
        supports = getattr(adapter, "supports_force_ramp_stream", None)
        if not callable(execute):
            return False
        if callable(supports):
            try:
                if not bool(supports()):
                    return False
            except Exception:
                return False

        try:
            started = bool(
                execute(
                    positions_mm=positions,
                    velocities_mm_s=velocities,
                    dt_s=float(dt_s),
                    speed_cap_mm_s=speed,
                )
            )
        except Exception:
            return False

        if not started:
            return False

        self._cancel_force_ramp()
        state = self._state["linear"]
        state.target_position = float(positions[-1])
        state.fault_code = int(FaultCode.NONE)
        state.fault_text = ""
        state.timestamp = time()
        self._write_axis_status("linear")
        return True

    def _start_linear_force_ramp_segmented(
        self,
        *,
        positions: np.ndarray,
        dt_s: float,
        speed: float | None,
        source: str,
    ) -> None:
        self._active_force_ramp = {
            "positions": [float(v) for v in positions],
            "index": 0,
            "next_time": time(),
            "dt_s": max(float(dt_s), 0.0),
            "speed": speed,
            "source": source,
        }

    def _advance_force_ramp(self) -> None:
        ramp = self._active_force_ramp
        if not ramp:
            return

        positions = ramp["positions"]
        index = int(ramp["index"])
        next_time = float(ramp["next_time"])
        dt_s = float(ramp["dt_s"])
        speed = ramp["speed"]
        source = str(ramp["source"])

        if index >= len(positions):
            self._cancel_force_ramp()
            return

        now = time()
        if now < next_time:
            return

        # Keep the fetch loop responsive even when dt is small.
        max_steps_this_cycle = 8
        adapter = self._adapters["linear"]
        state = self._state["linear"]
        current = float(state.actual_position)

        while index < len(positions) and now >= next_time and max_steps_this_cycle > 0:
            target = float(positions[index])
            decision = self._safety_guard.validate(axis="linear", current=current, target=target, speed=speed)
            if not decision.allowed:
                self._emit_fault(
                    "linear",
                    decision.fault_code,
                    f"force ramp blocked: {decision.reason}",
                    requested_target=target,
                    notify=(source == "ui"),
                )
                self._cancel_force_ramp()
                return
            try:
                adapter.move_absolute(target, speed=speed)
            except Exception as exc:
                self._emit_fault(
                    "linear",
                    FaultCode.ADAPTER_ERROR,
                    f"force ramp step failed: {exc}",
                    requested_target=target,
                    notify=(source == "ui"),
                )
                self._cancel_force_ramp()
                return

            state.target_position = target
            state.fault_code = int(FaultCode.NONE)
            state.fault_text = ""
            state.timestamp = now
            self._write_axis_status("linear")

            current = target
            index += 1
            max_steps_this_cycle -= 1
            if dt_s > 0:
                next_time += dt_s
            else:
                next_time = now
            now = time()

        if index >= len(positions):
            self._cancel_force_ramp()
            return

        ramp["index"] = index
        ramp["next_time"] = next_time

    def _cancel_force_ramp(self) -> None:
        self._active_force_ramp = None

    def _emit_force_calibrant_status(
        self,
        *,
        loaded: bool,
        path: str | None,
        message: str,
        force_min_pn: float | None = None,
        force_max_pn: float | None = None,
    ) -> None:
        command = UpdateForceCalibrantStatusCommand(
            loaded=bool(loaded),
            path=path,
            message=str(message),
            force_min_pn=force_min_pn,
            force_max_pn=force_max_pn,
        )
        self._last_force_calibrant_status = command
        try:
            self.send_ipc(command)
        except Exception:
            pass

    @staticmethod
    def _coerce_positive_speed(value: object, default: float) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return float(default)
        if parsed <= 0:
            return float(default)
        return parsed

    def _connect_axis_with_fallback(self, axis: str, adapter, config: dict) -> None:
        try:
            adapter.connect(config, test_mode=self._test_mode)
            return
        except Exception as first_error:
            if self._test_mode:
                raise first_error
            if axis == "objective" and self._discovery_mode == "settings_then_fallback_scan":
                configured_serial = str(config.get("serial_number") or "").strip()
                if configured_serial:
                    fallback_config = dict(config)
                    fallback_config["serial_number"] = None
                    try:
                        adapter.connect(fallback_config, test_mode=False)
                        return
                    except Exception:
                        pass
                raise first_error
            if axis not in {"linear", "rotary"}:
                raise first_error
            if self._discovery_mode != "settings_then_fallback_scan":
                raise first_error

            configured_port = str(config.get("port") or "").upper()
            for fallback_port in self._serial_port_scan_candidates(exclude=configured_port):
                fallback_config = dict(config)
                fallback_config["port"] = fallback_port
                try:
                    adapter.connect(fallback_config, test_mode=False)
                    return
                except Exception:
                    continue
            raise first_error

    @staticmethod
    def _serial_port_scan_candidates(exclude: str = "") -> list[str]:
        exclude = exclude.upper()
        try:
            from serial.tools import list_ports

            ports = [p.device for p in list_ports.comports() if p.device.upper() != exclude]
            if ports:
                return ports
        except Exception:
            pass
        return [f"COM{i}" for i in range(1, 33) if f"COM{i}" != exclude]

    def _dispatch_move(
        self,
        axis: str,
        *,
        relative_delta: float | None = None,
        absolute_target: float | None = None,
        speed: float | None = None,
        source: str = "ui",
    ) -> None:
        if axis not in self._state:
            self._emit_fault(axis, FaultCode.BAD_AXIS, FAULT_MESSAGES[FaultCode.BAD_AXIS], notify=False)
            return

        if axis == "linear" and self._active_force_ramp is not None and source != "force_ramp":
            self._cancel_force_ramp()

        if not self._enabled:
            self._emit_fault(
                axis,
                FaultCode.DISABLED,
                FAULT_MESSAGES[FaultCode.DISABLED],
                requested_target=None,
                notify=(source == "ui"),
            )
            return

        self._refresh_axis(axis)
        state = self._state[axis]
        if not state.connected:
            self._emit_fault(
                axis,
                FaultCode.NOT_CONNECTED,
                FAULT_MESSAGES[FaultCode.NOT_CONNECTED],
                notify=(source == "ui"),
            )
            return

        if relative_delta is not None:
            target = state.actual_position + float(relative_delta)
        elif absolute_target is not None:
            target = float(absolute_target)
        else:
            return

        state.target_position = target
        effective_speed = speed
        if effective_speed is None:
            effective_speed = self._default_move_speed.get(axis)

        decision = self._safety_guard.validate(
            axis=axis,
            current=state.actual_position,
            target=target,
            speed=effective_speed,
        )
        if not decision.allowed:
            self._emit_fault(
                axis,
                decision.fault_code,
                decision.reason,
                requested_target=target,
                notify=(source == "ui"),
            )
            return

        # Emit the commanded target immediately so the target trace reflects the
        # requested endpoint even while actual position is still in motion.
        state.fault_code = int(FaultCode.NONE)
        state.fault_text = ""
        state.timestamp = time()
        self._write_axis_status(axis)

        adapter = self._adapters[axis]
        try:
            if relative_delta is not None:
                adapter.move_relative(float(relative_delta), speed=effective_speed)
            else:
                adapter.move_absolute(target, speed=effective_speed)
        except Exception as exc:
            self._emit_fault(
                axis,
                FaultCode.ADAPTER_ERROR,
                f"{source} move failed: {exc}",
                requested_target=target,
                notify=(source == "ui"),
            )
            return

        state.fault_code = int(FaultCode.NONE)
        state.fault_text = ""
        self._refresh_axis(axis)
        self._write_axis_status(axis)

    def _refresh_axis(self, axis: str) -> None:
        state = self._state[axis]
        adapter = self._adapters[axis]
        try:
            status = adapter.get_status()
        except Exception as exc:
            self._emit_fault(axis, FaultCode.ADAPTER_ERROR, f"status read failed: {exc}")
            return

        state.connected = bool(status.get("connected", state.connected))
        state.actual_position = float(status.get("actual_position", state.actual_position))
        state.target_position = float(status.get("target_position", state.target_position))
        state.velocity = float(status.get("velocity", state.velocity))
        state.timestamp = time()

    def _write_axis_status(self, axis: str) -> None:
        if self._buffer is None:
            return
        state = self._state[axis]
        row = np.array(
            [
                [
                    time(),
                    AXIS_INDEX[axis],
                    state.actual_position,
                    state.target_position,
                    state.velocity,
                    1.0 if state.connected else 0.0,
                    1.0 if self._safety_guard.is_armed() else 0.0,
                    float(state.fault_code),
                ]
            ],
            dtype=np.float64,
        )
        self._buffer.write(row)
        row_tuple = tuple(float(v) for v in row[0])
        self._telemetry_history.append(row_tuple)
        self._maybe_dump_telemetry_row(axis, row_tuple, state.fault_text)

    def _emit_fault(
        self,
        axis: str,
        fault_code: FaultCode,
        reason: str,
        requested_target: float | None = None,
        notify: bool = True,
    ) -> None:
        if axis in self._state:
            state = self._state[axis]
            if requested_target is not None:
                state.target_position = float(requested_target)
            state.fault_code = int(fault_code)
            state.fault_text = reason or FAULT_MESSAGES.get(fault_code, "")
            self._write_axis_status(axis)

        prev = self._fault_debounce.get(axis)
        current = (int(fault_code), reason)
        if prev == current:
            return
        self._fault_debounce[axis] = current

        if notify:
            text = f"{axis.title()} motor blocked"
            details = f"{FAULT_MESSAGES.get(fault_code, 'fault')}: {reason}"
            try:
                self.send_ipc(ShowMessageCommand(text=text, details=details))
            except Exception:
                pass

    def _maybe_dump_telemetry_row(self, axis: str, row: tuple[float, ...], fault_text: str) -> None:
        if not self._telemetry_dump_enabled:
            return
        if not self._acquisition_on or not self._acquisition_dir_on or not self._acquisition_dir:
            self._telemetry_file = None
            return

        output_dir = Path(self._acquisition_dir)
        if not output_dir.exists():
            return

        if self._telemetry_file is None:
            self._telemetry_file = output_dir / "motor_telemetry.csv"
            write_header = not self._telemetry_file.exists()
        else:
            write_header = False

        with self._telemetry_file.open("a", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            if write_header:
                writer.writerow(
                    [
                        "timestamp",
                        "axis",
                        "axis_index",
                        "actual_position",
                        "target_position",
                        "velocity",
                        "connected",
                        "armed",
                        "fault_code",
                        "fault_text",
                    ]
                )
            writer.writerow(
                [
                    row[0],
                    axis,
                    int(row[1]),
                    row[2],
                    row[3],
                    row[4],
                    int(row[5]),
                    int(row[6]),
                    int(row[7]),
                    fault_text,
                ]
            )
