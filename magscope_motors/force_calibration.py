from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

try:
    from scipy.interpolate import PchipInterpolator
except Exception:  # pragma: no cover - optional dependency at runtime
    PchipInterpolator = None  # type: ignore[assignment]


class ForceCalibrantError(RuntimeError):
    """Raised when force calibrant data is missing or invalid."""


@dataclass(frozen=True)
class ForceRampProfile:
    forces_pn: np.ndarray
    positions_mm: np.ndarray
    velocities_mm_s: np.ndarray
    dt_s: float


class ForceCalibrantModel:
    """Load and convert force calibrants using v2-compatible PCHIP interpolation."""

    min_rows = 10

    def __init__(self) -> None:
        self.path: str | None = None
        self.data: np.ndarray | None = None
        self._motor2force = None
        self._force2motor = None

    def unload(self) -> None:
        self.path = None
        self.data = None
        self._motor2force = None
        self._force2motor = None

    def is_loaded(self) -> bool:
        return self.data is not None and self._motor2force is not None and self._force2motor is not None

    def load(self, path: str) -> None:
        if PchipInterpolator is None:
            raise ForceCalibrantError("scipy is required for force calibration interpolation")

        source = Path(path).expanduser()
        rows = self._read_rows(source)
        if rows.shape[0] < self.min_rows:
            raise ForceCalibrantError(f"force calibrant must contain at least {self.min_rows} rows")

        # Sort by motor position for forward interpolation.
        rows = rows[np.argsort(rows[:, 0], kind="mergesort")]
        linear_mm = rows[:, 0]
        force_pn = rows[:, 1]

        if not np.all(np.isfinite(rows)):
            raise ForceCalibrantError("force calibrant contains non-finite values")
        if np.any(np.diff(linear_mm) <= 0):
            raise ForceCalibrantError("linear positions must be strictly increasing")

        # For inverse interpolation, force values must be unique and sortable.
        inv_rows = rows[np.argsort(force_pn, kind="mergesort")]
        force_sorted = inv_rows[:, 1]
        motor_sorted = inv_rows[:, 0]
        if np.any(np.diff(force_sorted) <= 0):
            raise ForceCalibrantError("force values must be unique for inverse interpolation")

        self._motor2force = PchipInterpolator(linear_mm, force_pn, extrapolate=False)
        self._force2motor = PchipInterpolator(force_sorted, motor_sorted, extrapolate=False)
        self.path = str(source)
        self.data = rows

    def force_to_motor(self, force_pn: float) -> float | None:
        if not self.is_loaded():
            return None
        values = np.asarray(self._force2motor(np.array([float(force_pn)], dtype=np.float64)), dtype=np.float64)
        if values.size != 1 or not np.isfinite(values[0]):
            return None
        return float(values[0])

    def motor_to_force(self, linear_mm: float) -> float | None:
        if not self.is_loaded():
            return None
        values = np.asarray(self._motor2force(np.array([float(linear_mm)], dtype=np.float64)), dtype=np.float64)
        if values.size != 1 or not np.isfinite(values[0]):
            return None
        return float(values[0])

    def motor_array_to_force(self, linear_mm: np.ndarray) -> np.ndarray:
        values = np.asarray(linear_mm, dtype=np.float64)
        if not self.is_loaded():
            return np.full(values.shape, np.nan, dtype=np.float64)

        converted = np.asarray(self._motor2force(values), dtype=np.float64)
        if converted.shape != values.shape:
            converted = np.reshape(converted, values.shape)
        converted[~np.isfinite(converted)] = np.nan
        return converted

    def build_force_ramp(
        self,
        *,
        start_pn: float,
        stop_pn: float,
        rate_pn_s: float,
        points: int = 100,
    ) -> ForceRampProfile:
        if not self.is_loaded():
            raise ForceCalibrantError("force calibrant is not loaded")

        rate = float(rate_pn_s)
        if rate <= 0:
            raise ForceCalibrantError("rate must be > 0 pN/s")

        n_points = max(int(points), 2)
        forces = np.linspace(float(start_pn), float(stop_pn), num=n_points, dtype=np.float64)
        positions = np.asarray(self._force2motor(forces), dtype=np.float64)
        if positions.shape != forces.shape or not np.all(np.isfinite(positions)):
            raise ForceCalibrantError("requested force ramp exceeds calibrant range")

        dt = abs(float(stop_pn) - float(start_pn)) / rate / float(n_points - 1)
        if dt <= 0:
            velocities = np.zeros_like(positions, dtype=np.float64)
            dt = 0.0
        else:
            velocities = np.diff(positions) / dt
            velocities = np.insert(velocities, 0, 0.0)

        return ForceRampProfile(
            forces_pn=forces,
            positions_mm=positions,
            velocities_mm_s=np.asarray(velocities, dtype=np.float64),
            dt_s=float(dt),
        )

    def plot(self) -> None:
        if not self.is_loaded():
            raise ForceCalibrantError("force calibrant is not loaded")

        import matplotlib.pyplot as plt

        assert self.data is not None
        linear_mm = self.data[:, 0]
        force_pn = self.data[:, 1]
        x = np.linspace(float(np.min(linear_mm)), float(np.max(linear_mm)), num=2000, dtype=np.float64)
        y = np.asarray(self._motor2force(x), dtype=np.float64)

        with plt.style.context("default"):
            plt.plot(x, y, linewidth=4.0)
            plt.plot(linear_mm, force_pn, "x", ms=1)
            plt.xlabel("Motor Position (mm)")
            plt.ylabel("Force (pN)")
            plt.legend(["Interpolated", "Data"])
            plt.show()

    @staticmethod
    def _read_rows(path: Path) -> np.ndarray:
        if not path.exists():
            raise ForceCalibrantError(f"force calibrant file does not exist: {path}")

        rows: list[tuple[float, float]] = []
        for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [part for part in re.split(r"[\s,]+", line) if part]
            if len(parts) != 2:
                raise ForceCalibrantError(
                    f"invalid force calibrant format at line {line_number}: expected two numeric columns"
                )
            try:
                motor_mm = float(parts[0])
                force_pn = float(parts[1])
            except ValueError as exc:
                raise ForceCalibrantError(
                    f"invalid numeric value at line {line_number}: {raw_line}"
                ) from exc
            rows.append((motor_mm, force_pn))

        if not rows:
            raise ForceCalibrantError("force calibrant file is empty")

        return np.asarray(rows, dtype=np.float64)
