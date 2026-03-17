from __future__ import annotations

from math import isfinite
from typing import Any


class ZaberLinearAdapter:
    """Zaber linear stage adapter with a safe simulation fallback."""

    def __init__(self) -> None:
        self._connected = False
        self._test_mode = True
        self._actual_mm = 0.0
        self._target_mm = 0.0
        self._velocity_mm_s = 0.0
        self._connection: Any = None
        self._device: Any = None
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
            raise RuntimeError("zaber-motion is required for Zaber linear control") from exc

        port = str(config.get("port") or "")
        if not port:
            raise RuntimeError("Linear motor serial port is required")

        connection = Connection.open_serial_port(port)
        devices = connection.detect_devices()
        if not devices:
            connection.close()
            raise RuntimeError(f"No Zaber linear devices detected on {port}")

        self._connection = connection
        self._device = devices[0]
        self._axis = self._device.get_axis(1)
        self._units = Units
        self._connected = True
        self._actual_mm = self._query_position_mm()
        self._target_mm = self._actual_mm
        max_speed = self.get_hardware_max_speed_mm_s()
        if max_speed is not None:
            self._velocity_mm_s = float(max_speed)
            self._set_speed(float(max_speed))

    def disconnect(self) -> None:
        if self._connection is not None:
            try:
                self._connection.close()
            except Exception:
                pass
        self._connection = None
        self._device = None
        self._axis = None
        self._units = None
        self._connected = False

    def stop(self) -> None:
        if not self._connected:
            return
        if self._test_mode:
            self._velocity_mm_s = 0.0
            return
        try:
            self._axis.stop()
        except Exception:
            pass

    def move_relative(self, delta_mm: float, *, speed: float | None = None) -> None:
        if not self._connected:
            raise RuntimeError("Linear motor is not connected")
        delta_mm = float(delta_mm)
        self._target_mm = self._actual_mm + delta_mm
        if speed is not None:
            self._set_speed(float(speed))
        if self._test_mode:
            self._actual_mm = self._target_mm
            return
        self._move_relative_nonblocking(delta_mm)

    def move_absolute(self, target_mm: float, *, speed: float | None = None) -> None:
        if not self._connected:
            raise RuntimeError("Linear motor is not connected")
        self._target_mm = float(target_mm)
        if speed is not None:
            self._set_speed(float(speed))
        if self._test_mode:
            self._actual_mm = self._target_mm
            return
        self._move_absolute_nonblocking(self._target_mm)

    def get_status(self) -> dict[str, float | bool]:
        if self._connected and not self._test_mode:
            self._actual_mm = self._query_position_mm()
        return {
            "connected": self._connected,
            "actual_position": float(self._actual_mm),
            "target_position": float(self._target_mm),
            "velocity": float(self._velocity_mm_s),
        }

    def supports_force_ramp_stream(self) -> bool:
        if not self._connected:
            return False
        if self._test_mode:
            return True
        if self._device is None:
            return False
        streams = getattr(self._device, "streams", None)
        return streams is not None and hasattr(streams, "get_stream") and hasattr(streams, "get_buffer")

    def execute_force_ramp_stream(
        self,
        *,
        positions_mm: Any,
        velocities_mm_s: Any,
        dt_s: float,
        speed_cap_mm_s: float | None = None,
    ) -> bool:
        del dt_s
        if not self.supports_force_ramp_stream():
            return False

        try:
            positions = [float(value) for value in positions_mm]
            velocities = [float(value) for value in velocities_mm_s]
        except Exception:
            return False
        if len(positions) == 0 or len(positions) != len(velocities):
            return False
        if any(not isfinite(value) for value in positions):
            return False
        if any(not isfinite(value) for value in velocities):
            return False

        if self._test_mode:
            self._target_mm = positions[-1]
            self._actual_mm = self._target_mm
            if speed_cap_mm_s is not None and speed_cap_mm_s > 0:
                self._velocity_mm_s = float(speed_cap_mm_s)
            elif velocities:
                self._velocity_mm_s = max(abs(value) for value in velocities)
            return True

        if self._device is None:
            return False

        try:
            from zaber_motion import Measurement
        except Exception:
            return False

        streams = self._device.streams
        try:
            stream = streams.get_stream(1)
            stream.disable()
            buffer = streams.get_buffer(1)
            buffer.erase()
            stream.setup_store(buffer, 1)
        except Exception:
            return False

        velocity_unit = self._linear_velocity_unit()
        fallback_speed = float(speed_cap_mm_s) if speed_cap_mm_s is not None and speed_cap_mm_s > 0 else 0.1
        if fallback_speed <= 0:
            fallback_speed = 0.1

        try:
            for position_mm, velocity_mm_s in zip(positions, velocities):
                speed = abs(float(velocity_mm_s))
                if speed <= 0:
                    speed = fallback_speed
                if speed_cap_mm_s is not None and speed_cap_mm_s > 0:
                    speed = min(speed, float(speed_cap_mm_s))
                if speed <= 0:
                    speed = fallback_speed
                if velocity_unit is not None:
                    stream.set_max_speed(speed, velocity_unit)
                stream.line_absolute(Measurement(float(position_mm), "mm"))
            stream.disable()
            stream.setup_live(1)
            stream.call(buffer)
        except Exception:
            return False

        self._target_mm = positions[-1]
        self._velocity_mm_s = float(fallback_speed)
        return True

    def _linear_unit(self) -> Any:
        return self._required_unit("LENGTH_MILLIMETRES", "LENGTH_MILLIMETERS")

    def _linear_velocity_unit(self) -> Any | None:
        return self._optional_unit(
            "VELOCITY_MILLIMETRES_PER_SECOND",
            "VELOCITY_MILLIMETERS_PER_SECOND",
        )

    def _required_unit(self, *names: str) -> Any:
        for name in names:
            if hasattr(self._units, name):
                return getattr(self._units, name)
        joined_names = ", ".join(names)
        raise RuntimeError(f"zaber-motion Units missing required unit: {joined_names}")

    def _optional_unit(self, *names: str) -> Any | None:
        for name in names:
            if hasattr(self._units, name):
                return getattr(self._units, name)
        return None

    def _set_speed(self, speed_mm_s: float) -> None:
        self._velocity_mm_s = float(speed_mm_s)
        if self._test_mode:
            return
        speed_unit = self._linear_velocity_unit()
        if speed_unit is None:
            return
        try:
            self._axis.settings.set("maxspeed", self._velocity_mm_s, speed_unit)
        except Exception:
            pass

    def get_hardware_max_speed_mm_s(self) -> float | None:
        if self._test_mode or not self._connected or self._axis is None:
            return None
        speed_unit = self._linear_velocity_unit()
        if speed_unit is None:
            return None
        for key in ("maxspeed.max", "maxspeed"):
            try:
                value = float(self._axis.settings.get(key, speed_unit))
            except Exception:
                continue
            if value > 0:
                return value
        return None

    def _query_position_mm(self) -> float:
        if self._axis is None:
            return float(self._actual_mm)
        try:
            return float(self._axis.get_position(self._linear_unit()))
        except Exception:
            return float(self._actual_mm)

    def _move_relative_nonblocking(self, delta_mm: float) -> None:
        # Prefer non-blocking motion so manager fetch keeps updating telemetry/plots.
        try:
            self._axis.move_relative(delta_mm, self._linear_unit(), wait_until_idle=False)
            return
        except TypeError:
            # Older APIs may not accept keyword args; try positional wait flag.
            pass
        try:
            self._axis.move_relative(delta_mm, self._linear_unit(), False)
            return
        except TypeError:
            # Older APIs may not support wait control; fall back to legacy call.
            pass
        self._axis.move_relative(delta_mm, self._linear_unit())

    def _move_absolute_nonblocking(self, target_mm: float) -> None:
        # Prefer non-blocking motion so manager fetch keeps updating telemetry/plots.
        try:
            self._axis.move_absolute(target_mm, self._linear_unit(), wait_until_idle=False)
            return
        except TypeError:
            # Older APIs may not accept keyword args; try positional wait flag.
            pass
        try:
            self._axis.move_absolute(target_mm, self._linear_unit(), False)
            return
        except TypeError:
            # Older APIs may not support wait control; fall back to legacy call.
            pass
        self._axis.move_absolute(target_mm, self._linear_unit())
