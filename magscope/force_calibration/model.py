from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.interpolate import PchipInterpolator


class ForceCalibrantError(RuntimeError):
    """Raised when force calibration data is missing or invalid."""


@dataclass(frozen=True)
class ForceRampProfile:
    forces_pn: np.ndarray
    positions_mm: np.ndarray
    velocities_mm_s: np.ndarray
    dt_s: float


class ForceCalibrantModel:
    """Bidirectional PCHIP interpolation between motor position (mm) and force (pN).

    Calibration data is loaded from a two-column text file: ``position_mm  force_pn``.
    Once loaded, the model can convert motor position to force (and vice versa) and
    build force-ramp profiles that maintain a constant pN/s rate across the non-linear
    force curve.
    """

    MIN_ROWS = 10

    def __init__(self):
        self._force_pchip: PchipInterpolator | None = None
        self._position_pchip: PchipInterpolator | None = None
        self._path: str | None = None
        self._force_min: float | None = None
        self._force_max: float | None = None
        self._position_min: float | None = None
        self._position_max: float | None = None
        self._n_rows: int = 0

    def is_loaded(self) -> bool:
        return self._force_pchip is not None

    def load(self, path: str | Path) -> None:
        path = Path(path)
        if not path.exists():
            raise ForceCalibrantError(f"Force calibrant file not found: {path}")

        data = np.loadtxt(str(path), comments="#")
        if data.ndim != 2 or data.shape[1] < 2:
            raise ForceCalibrantError(
                f"Force calibrant file must have at least 2 columns, got shape {data.shape}"
            )

        data = data[~np.any(np.isnan(data), axis=1)]
        if data.shape[0] < self.MIN_ROWS:
            raise ForceCalibrantError(
                f"Force calibrant file must have at least {self.MIN_ROWS} rows, "
                f"got {data.shape[0]}"
            )

        positions_mm = data[:, 0].astype(float)
        forces_pn = data[:, 1].astype(float)

        sort_idx = np.argsort(positions_mm)
        positions_mm = positions_mm[sort_idx]
        forces_pn = forces_pn[sort_idx]

        if np.any(np.diff(forces_pn) <= 0):
            raise ForceCalibrantError(
                "Force calibrant data must be monotonically increasing in force"
            )

        self._force_pchip = PchipInterpolator(positions_mm, forces_pn)
        self._position_pchip = PchipInterpolator(forces_pn, positions_mm)
        self._path = str(path)
        self._force_min = float(forces_pn[0])
        self._force_max = float(forces_pn[-1])
        self._position_min = float(positions_mm[0])
        self._position_max = float(positions_mm[-1])
        self._n_rows = int(data.shape[0])

    def unload(self) -> None:
        self._force_pchip = None
        self._position_pchip = None
        self._path = None
        self._force_min = None
        self._force_max = None
        self._position_min = None
        self._position_max = None
        self._n_rows = 0

    def motor_to_force(self, position_mm: float) -> float | None:
        if self._force_pchip is None:
            return None
        if position_mm < self._position_min or position_mm > self._position_max:
            return None
        return float(self._force_pchip(position_mm))

    def motor_array_to_force(self, positions_mm: np.ndarray) -> np.ndarray:
        if self._force_pchip is None:
            return np.full_like(positions_mm, np.nan)
        result = self._force_pchip(positions_mm)
        result[(positions_mm < self._position_min) | (positions_mm > self._position_max)] = np.nan
        return result

    def force_to_motor(self, force_pn: float) -> float | None:
        if self._position_pchip is None:
            return None
        if force_pn < self._force_min or force_pn > self._force_max:
            return None
        return float(self._position_pchip(force_pn))

    def force_array_to_motor(self, forces_pn: np.ndarray) -> np.ndarray:
        if self._position_pchip is None:
            return np.full_like(forces_pn, np.nan)
        result = self._position_pchip(forces_pn)
        result[(forces_pn < self._force_min) | (forces_pn > self._force_max)] = np.nan
        return result

    def get_path(self) -> str | None:
        return self._path

    def get_n_rows(self) -> int:
        return self._n_rows

    def get_force_range(self) -> tuple[float, float] | None:
        if self._force_min is None:
            return None
        return (self._force_min, self._force_max)

    def get_position_range(self) -> tuple[float, float] | None:
        if self._position_min is None:
            return None
        return (self._position_min, self._position_max)

    def build_force_ramp(
        self,
        *,
        start_pn: float,
        stop_pn: float,
        rate_pn_s: float,
        points: int = 100,
    ) -> ForceRampProfile | None:
        if self._force_pchip is None or self._position_pchip is None:
            return None
        if rate_pn_s <= 0:
            return None

        if not (self._force_min <= start_pn <= self._force_max):
            return None
        if not (self._force_min <= stop_pn <= self._force_max):
            return None

        forces_pn = np.linspace(start_pn, stop_pn, points)
        positions_mm = self._position_pchip(forces_pn)

        dt_s = abs(forces_pn[1] - forces_pn[0]) / rate_pn_s

        velocities_mm_s = np.zeros_like(positions_mm)
        velocities_mm_s[1:] = np.diff(positions_mm) / dt_s
        velocities_mm_s[0] = 0.0

        return ForceRampProfile(
            forces_pn=forces_pn,
            positions_mm=positions_mm.astype(float),
            velocities_mm_s=velocities_mm_s.astype(float),
            dt_s=dt_s,
        )

    def plot(self, ax=None):
        """Debug helper: plot raw data points and interpolated curve."""
        import matplotlib.pyplot as plt

        if not self.is_loaded():
            return

        data = np.loadtxt(str(self._path), comments="#")
        positions_raw = data[:, 0]
        forces_raw = data[:, 1]

        if ax is None:
            _, ax = plt.subplots()

        ax.scatter(positions_raw, forces_raw, label="Data", s=10)
        x_fine = np.linspace(self._position_min, self._position_max, 500)
        y_fine = self._force_pchip(x_fine)
        ax.plot(x_fine, y_fine, "r-", label="PCHIP interpolation")
        ax.set_xlabel("Position (mm)")
        ax.set_ylabel("Force (pN)")
        ax.legend()
        ax.grid(True)
