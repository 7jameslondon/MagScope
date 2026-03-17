from __future__ import annotations

from typing import Any


class ZaberRotaryAdapter:
    """Zaber rotary stage adapter storing positions in turns."""

    def __init__(self) -> None:
        self._connected = False
        self._test_mode = True
        self._actual_turns = 0.0
        self._target_turns = 0.0
        self._velocity_turns_s = 0.0
        self._connection: Any = None
        self._axis: Any = None
        self._units: Any = None

    def connect(self, config: dict, *, test_mode: bool = True) -> None:
        self._test_mode = bool(test_mode)
        if self._test_mode:
            self._connected = True
            return

        try:
            from zaber_motion import Units
            from zaber_motion.ascii import Connection
        except ImportError as exc:
            raise RuntimeError("zaber-motion is required for Zaber rotary control") from exc

        port = str(config.get("port") or "")
        if not port:
            raise RuntimeError("Rotary motor serial port is required")

        connection = Connection.open_serial_port(port)
        devices = connection.detect_devices()
        if not devices:
            connection.close()
            raise RuntimeError(f"No Zaber rotary devices detected on {port}")

        self._connection = connection
        self._axis = devices[0].get_axis(1)
        self._units = Units
        self._connected = True
        self._actual_turns = self._query_position_turns()
        self._target_turns = self._actual_turns
        max_speed = self.get_hardware_max_speed_turns_s()
        if max_speed is not None:
            self._velocity_turns_s = float(max_speed)
            self._set_speed(float(max_speed))

    def disconnect(self) -> None:
        if self._connection is not None:
            try:
                self._connection.close()
            except Exception:
                pass
        self._connection = None
        self._axis = None
        self._units = None
        self._connected = False

    def stop(self) -> None:
        if not self._connected:
            return
        if self._test_mode:
            self._velocity_turns_s = 0.0
            return
        try:
            self._axis.stop()
        except Exception:
            pass

    def move_relative(self, delta_turns: float, *, speed: float | None = None) -> None:
        if not self._connected:
            raise RuntimeError("Rotary motor is not connected")
        delta_turns = float(delta_turns)
        self._target_turns = self._actual_turns + delta_turns
        if speed is not None:
            self._set_speed(float(speed))
        if self._test_mode:
            self._actual_turns = self._target_turns
            return
        self._move_relative_nonblocking(delta_turns)

    def move_absolute(self, target_turns: float, *, speed: float | None = None) -> None:
        if not self._connected:
            raise RuntimeError("Rotary motor is not connected")
        self._target_turns = float(target_turns)
        if speed is not None:
            self._set_speed(float(speed))
        if self._test_mode:
            self._actual_turns = self._target_turns
            return
        self._move_absolute_nonblocking(self._target_turns)

    def get_status(self) -> dict[str, float | bool]:
        if self._connected and not self._test_mode:
            self._actual_turns = self._query_position_turns()
        return {
            "connected": self._connected,
            "actual_position": float(self._actual_turns),
            "target_position": float(self._target_turns),
            "velocity": float(self._velocity_turns_s),
        }

    @staticmethod
    def _to_degrees(turns: float) -> float:
        return float(turns) * 360.0

    @staticmethod
    def _from_degrees(degrees: float) -> float:
        return float(degrees) / 360.0

    def _degree_unit(self) -> Any:
        return getattr(self._units, "ANGLE_DEGREES")

    def _degree_velocity_unit(self) -> Any | None:
        return getattr(self._units, "ANGULAR_VELOCITY_DEGREES_PER_SECOND", None)

    def _set_speed(self, speed_turns_s: float) -> None:
        self._velocity_turns_s = float(speed_turns_s)
        if self._test_mode:
            return
        speed_unit = self._degree_velocity_unit()
        if speed_unit is None:
            return
        try:
            self._axis.settings.set("maxspeed", self._to_degrees(speed_turns_s), speed_unit)
        except Exception:
            pass

    def get_hardware_max_speed_turns_s(self) -> float | None:
        if self._test_mode or not self._connected or self._axis is None:
            return None
        speed_unit = self._degree_velocity_unit()
        if speed_unit is None:
            return None
        for key in ("maxspeed.max", "maxspeed"):
            try:
                degrees_per_s = float(self._axis.settings.get(key, speed_unit))
            except Exception:
                continue
            if degrees_per_s > 0:
                return self._from_degrees(degrees_per_s)
        return None

    def _query_position_turns(self) -> float:
        if self._axis is None:
            return float(self._actual_turns)
        try:
            degrees = float(self._axis.get_position(self._degree_unit()))
            return self._from_degrees(degrees)
        except Exception:
            return float(self._actual_turns)

    def _move_relative_nonblocking(self, delta_turns: float) -> None:
        # Prefer non-blocking motion so manager fetch keeps updating telemetry/plots.
        delta_degrees = self._to_degrees(delta_turns)
        try:
            self._axis.move_relative(delta_degrees, self._degree_unit(), wait_until_idle=False)
            return
        except TypeError:
            # Older APIs may not accept keyword args; try positional wait flag.
            pass
        try:
            self._axis.move_relative(delta_degrees, self._degree_unit(), False)
            return
        except TypeError:
            # Older APIs may not support wait control; fall back to legacy call.
            pass
        self._axis.move_relative(delta_degrees, self._degree_unit())

    def _move_absolute_nonblocking(self, target_turns: float) -> None:
        # Prefer non-blocking motion so manager fetch keeps updating telemetry/plots.
        target_degrees = self._to_degrees(target_turns)
        try:
            self._axis.move_absolute(target_degrees, self._degree_unit(), wait_until_idle=False)
            return
        except TypeError:
            # Older APIs may not accept keyword args; try positional wait flag.
            pass
        try:
            self._axis.move_absolute(target_degrees, self._degree_unit(), False)
            return
        except TypeError:
            # Older APIs may not support wait control; fall back to legacy call.
            pass
        self._axis.move_absolute(target_degrees, self._degree_unit())
