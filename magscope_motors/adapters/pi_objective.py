from __future__ import annotations

from contextlib import suppress
from typing import Any


class PIObjectiveAdapter:
    """PI objective adapter with an in-memory simulation fallback.

    `pipython` is required only when `test_mode=False`.
    """

    def __init__(self) -> None:
        self._connected = False
        self._test_mode = True
        self._actual_nm = 0.0
        self._target_nm = 0.0
        self._velocity_nm_s = 0.0
        self._nm_per_controller_unit = 1.0
        self._device: Any = None
        self._axis: Any = None

    def connect(self, config: dict, *, test_mode: bool = True) -> None:
        self.disconnect()
        self._test_mode = bool(test_mode)
        self._nm_per_controller_unit = self._parse_nm_per_controller_unit(config)
        if self._test_mode:
            self._connected = True
            return

        try:
            from pipython import GCSDevice
        except ImportError as exc:
            raise RuntimeError("pipython is required for PI objective control") from exc

        model = str(config.get("model") or "E-709")
        serial = config.get("serial_number")

        dev = GCSDevice(model)
        try:
            self._connect_usb_with_fallback(dev, serial_number=serial)
        except Exception as exc:
            with suppress(Exception):
                dev.CloseConnection()
            raise RuntimeError(f"Failed to connect PI objective ({model}): {exc}") from exc

        self._axis = self._resolve_primary_axis(dev)
        self._device = dev
        self._connected = True
        self._actual_nm = self._query_position_nm()
        self._target_nm = self._actual_nm
        max_speed = self.get_hardware_max_speed_nm_s()
        if max_speed is not None:
            self._velocity_nm_s = float(max_speed)
            self._apply_velocity_nm_s(float(max_speed))

    def disconnect(self) -> None:
        if self._device is not None:
            try:
                self._device.CloseConnection()
            except Exception:
                pass
        self._device = None
        self._axis = None
        self._connected = False

    def stop(self) -> None:
        if not self._connected:
            return
        if self._test_mode:
            self._velocity_nm_s = 0.0
            return
        try:
            self._device.STP(self._axis)
            return
        except Exception:
            pass
        try:
            self._device.STP()
        except Exception:
            pass

    def move_relative(self, delta_nm: float, *, speed: float | None = None) -> None:
        target_nm = self._actual_nm + float(delta_nm)
        self.move_absolute(target_nm, speed=speed)

    def move_absolute(self, target_nm: float, *, speed: float | None = None) -> None:
        if not self._connected:
            raise RuntimeError("PI objective is not connected")

        self._target_nm = float(target_nm)
        if speed is not None:
            self._velocity_nm_s = float(speed)

        if self._test_mode:
            self._actual_nm = self._target_nm
            return

        if speed is not None:
            self._apply_velocity_nm_s(float(speed))
        target_controller_units = self._nm_to_controller_units(self._target_nm)
        try:
            self._device.MOV(self._axis, target_controller_units)
        except Exception as exc:
            self._clear_error_state()
            raise RuntimeError(str(exc)) from exc

    def get_status(self) -> dict[str, float | bool]:
        if self._connected and not self._test_mode:
            self._actual_nm = self._query_position_nm()
        return {
            "connected": self._connected,
            "actual_position": float(self._actual_nm),
            "target_position": float(self._target_nm),
            "velocity": float(self._velocity_nm_s),
        }

    def _query_position_nm(self) -> float:
        if self._device is None:
            return float(self._actual_nm)
        try:
            pos = self._device.qPOS(self._axis)
            value = self._extract_axis_value(pos, self._axis)
            return self._controller_units_to_nm(value)
        except Exception:
            self._clear_error_state()
        try:
            pos = self._device.qPOS(self._axis)
            value = self._extract_axis_value(pos, self._axis)
            return self._controller_units_to_nm(value)
        except Exception:
            return float(self._actual_nm)

    def _apply_velocity_nm_s(self, speed_nm_s: float) -> None:
        if self._test_mode or self._device is None:
            return
        speed_controller_units_s = self._nm_to_controller_units(float(speed_nm_s))
        try:
            self._device.VEL(self._axis, speed_controller_units_s)
        except Exception:
            pass

    def get_hardware_max_speed_nm_s(self) -> float | None:
        if self._test_mode or not self._connected or self._device is None:
            return None

        # Prefer controller-reported velocity limit when available.
        for call in (lambda: self._device.qVLS(self._axis), lambda: self._device.qVLS()):
            try:
                value = self._extract_first_numeric(call())
            except Exception:
                continue
            if value is not None and value > 0:
                return self._controller_units_to_nm(value)

        # E-709 exposes axis velocity limits through SPA parameter 0x7000201.
        try:
            result = self._device.qSPA(self._axis, 0x7000201)
            value = self._extract_first_numeric(result)
            if value is not None and value > 0:
                return self._controller_units_to_nm(value)
        except Exception:
            pass

        # Last resort: use the currently configured velocity.
        try:
            result = self._device.qVEL(self._axis)
            value = self._extract_first_numeric(result)
            if value is not None and value > 0:
                return self._controller_units_to_nm(value)
        except Exception:
            pass
        return None

    def _connect_usb_with_fallback(self, device: Any, serial_number: object | None) -> None:
        serial = str(serial_number).strip() if serial_number is not None else ""
        if serial.lower() in {"none", "null"}:
            serial = ""

        if serial:
            try:
                device.ConnectUSB(serialnum=serial)
                return
            except Exception:
                pass

        enumerated = self._enumerate_usb_serials(device)
        for candidate in enumerated:
            if serial and candidate == serial:
                continue
            try:
                device.ConnectUSB(serialnum=candidate)
                return
            except Exception:
                continue

        try:
            device.ConnectUSB()
            return
        except TypeError:
            pass
        except Exception:
            pass

        if serial:
            raise RuntimeError(f"PI USB controller {serial} not found")
        raise RuntimeError("No PI USB controllers found")

    @staticmethod
    def _enumerate_usb_serials(device: Any) -> list[str]:
        enumerate_usb = getattr(device, "EnumerateUSB", None)
        if not callable(enumerate_usb):
            return []

        try:
            raw = enumerate_usb()
        except TypeError:
            raw = enumerate_usb("")
        except Exception:
            return []

        if raw is None:
            return []
        if isinstance(raw, str):
            candidates = [raw]
        else:
            try:
                candidates = list(raw)
            except Exception:
                candidates = [raw]

        serials: list[str] = []
        for candidate in candidates:
            text = str(candidate).strip()
            if text:
                serials.append(text)
        return serials

    @staticmethod
    def _parse_nm_per_controller_unit(config: dict) -> float:
        objective_units = str(config.get("controller_units", "")).strip().lower()
        if objective_units in {"um", "micrometer", "micrometers", "micrometre", "micrometres"}:
            return 1000.0
        if objective_units in {"nm", "nanometer", "nanometers", "nanometre", "nanometres"}:
            return 1.0
        value = config.get("nm_per_controller_unit", None)
        if value is not None:
            try:
                parsed = float(value)
                if parsed > 0:
                    return parsed
            except Exception:
                pass
        # E-709 objective position is typically reported in micrometers.
        return 1000.0

    def _nm_to_controller_units(self, value_nm: float) -> float:
        return float(value_nm) / float(self._nm_per_controller_unit)

    def _controller_units_to_nm(self, value_controller_units: float) -> float:
        return float(value_controller_units) * float(self._nm_per_controller_unit)

    @staticmethod
    def _resolve_primary_axis(device: Any) -> Any:
        axes_attr = getattr(device, "axes", None)
        axes = PIObjectiveAdapter._normalize_axes(axes_attr)
        if axes:
            return axes[0]

        query_axis_names = getattr(device, "qSAI", None)
        if callable(query_axis_names):
            try:
                axes = PIObjectiveAdapter._normalize_axes(query_axis_names())
                if axes:
                    return axes[0]
            except Exception:
                pass

        return "1"

    @staticmethod
    def _extract_axis_value(value: Any, axis: Any) -> float:
        if isinstance(value, dict):
            if axis in value:
                return float(value[axis])
            axis_text = str(axis)
            if axis_text in value:
                return float(value[axis_text])
            return float(next(iter(value.values())))
        return float(value)

    def _clear_error_state(self) -> None:
        if self._device is None:
            return
        try:
            self._device.qERR()
        except Exception:
            pass

    @staticmethod
    def _normalize_axes(value: Any) -> list[Any]:
        if value is None:
            return []
        if isinstance(value, dict):
            keys = [key for key in value.keys() if str(key).strip()]
            if keys:
                return keys
            return [item for item in value.values() if str(item).strip()]
        if isinstance(value, str):
            parts = [part for part in value.replace(",", " ").split() if part.strip()]
            return parts if parts else [value]
        try:
            sequence = list(value)
        except Exception:
            text = str(value).strip()
            return [text] if text else []
        normalized: list[Any] = []
        for item in sequence:
            text = str(item).strip()
            if text:
                normalized.append(item)
        return normalized

    @staticmethod
    def _extract_first_numeric(value: Any) -> float | None:
        if isinstance(value, dict):
            for item in value.values():
                nested = PIObjectiveAdapter._extract_first_numeric(item)
                if nested is not None:
                    return nested
            return None
        try:
            return float(value)
        except Exception:
            return None
