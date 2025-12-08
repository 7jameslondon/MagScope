from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, MutableMapping

from PyQt6.QtCore import QSettings
import yaml


@dataclass(frozen=True)
class SettingSpec:
    key: str
    value_type: type | tuple[type, ...]
    default: Any | None = None
    display_name: str | None = None
    minimum: float | None = None
    maximum: float | None = None
    must_be_even: bool = False

    def coerce(self, value: Any) -> Any:
        if isinstance(value, str):
            value = value.strip()
            if value == "":
                raise ValueError(f"Setting '{self.key}' cannot be empty")
            try:
                if float in self._candidate_types:
                    coerced = float(value)
                else:
                    coerced = int(value)
            except (TypeError, ValueError):
                coerced = value
        else:
            coerced = value

        if not isinstance(coerced, self.value_type):
            raise ValueError(
                f"Setting '{self.key}' must be of type {self.value_type}, got {type(coerced)}."
            )

        if self.minimum is not None and isinstance(coerced, (int, float)):
            if coerced < self.minimum:
                raise ValueError(
                    f"Setting '{self.key}' must be at least {self.minimum}, got {coerced}."
                )

        if self.maximum is not None and isinstance(coerced, (int, float)):
            if coerced > self.maximum:
                raise ValueError(
                    f"Setting '{self.key}' must be at most {self.maximum}, got {coerced}."
                )

        if self.must_be_even and isinstance(coerced, int):
            if coerced % 2 != 0:
                raise ValueError(
                    f"Setting '{self.key}' must be an even integer, got {coerced}."
                )

        return coerced

    def default_value(self) -> Any:
        return copy.deepcopy(self.default)

    @property
    def _candidate_types(self) -> tuple[type, ...]:
        if isinstance(self.value_type, tuple):
            return self.value_type
        return (self.value_type,)

    @property
    def label(self) -> str:
        return self.display_name or self.key


class MagScopeSettings(MutableMapping[str, Any]):
    _QSETTINGS_ORGANIZATION = "MagScope"
    _QSETTINGS_APPLICATION = "MagScope"
    _QSETTINGS_GROUP = "MagScopeSettings"

    _SETTING_SPECS: dict[str, SettingSpec] = {
        "ROI": SettingSpec(
            "ROI",
            value_type=int,
            default=50,
            display_name="ROI",
            minimum=8,
            must_be_even=True,
        ),
        "magnification": SettingSpec(
            "magnification",
            value_type=(int, float),
            default=1,
            display_name="Magnification",
            minimum=0.0001,
        ),
        "tracks max datapoints": SettingSpec(
            "tracks max datapoints",
            value_type=int,
            default=1_000_000,
            display_name="Tracks max datapoints",
            minimum=1,
        ),
        "video buffer n images": SettingSpec(
            "video buffer n images",
            value_type=int,
            default=40,
            display_name="Video buffer n images",
            minimum=1,
        ),
        "video buffer n stacks": SettingSpec(
            "video buffer n stacks",
            value_type=int,
            default=5,
            display_name="Video buffer n stacks",
            minimum=1,
        ),
        "video processors n": SettingSpec(
            "video processors n",
            value_type=int,
            default=3,
            display_name="Video processors n",
            minimum=1,
        ),
        "xy-lock default interval": SettingSpec(
            "xy-lock default interval",
            value_type=(int, float),
            default=10,
            display_name="XY-lock default interval",
            minimum=0,
        ),
        "xy-lock default max": SettingSpec(
            "xy-lock default max",
            value_type=(int, float),
            default=10,
            display_name="XY-lock default max",
            minimum=0,
        ),
        "xy-lock default window": SettingSpec(
            "xy-lock default window",
            value_type=int,
            default=10,
            display_name="XY-lock default window",
            minimum=1,
        ),
        "z-lock default interval": SettingSpec(
            "z-lock default interval",
            value_type=(int, float),
            default=10,
            display_name="Z-lock default interval",
            minimum=0,
        ),
        "z-lock default max": SettingSpec(
            "z-lock default max",
            value_type=(int, float),
            default=1_000,
            display_name="Z-lock default max",
            minimum=0,
        ),
    }

    def __init__(self, values: Mapping[str, Any] | None = None):
        self._persist_changes = False
        self._values: dict[str, Any] = {}
        self.update(self._load_defaults())
        self.update(self._load_qsettings_values())
        if values:
            self.update(values)
        self._persist_changes = True
        self._write_to_qsettings()

    @classmethod
    def _load_defaults(cls) -> dict[str, Any]:
        return {key: spec.default_value() for key, spec in cls._SETTING_SPECS.items()}

    @classmethod
    def _qsettings(cls) -> QSettings:
        return QSettings(cls._QSETTINGS_ORGANIZATION, cls._QSETTINGS_APPLICATION)

    def _load_qsettings_values(self) -> dict[str, Any]:
        settings = self._qsettings()
        settings.beginGroup(self._QSETTINGS_GROUP)
        loaded: dict[str, Any] = {}
        for key, spec in self._SETTING_SPECS.items():
            if not settings.contains(key):
                continue
            raw_value = settings.value(key)
            try:
                loaded[key] = spec.coerce(raw_value)
            except ValueError:
                continue
        settings.endGroup()
        return loaded

    def _write_to_qsettings(self) -> None:
        if not self._persist_changes:
            return
        settings = self._qsettings()
        settings.beginGroup(self._QSETTINGS_GROUP)
        for key, value in self._values.items():
            settings.setValue(key, value)
        settings.endGroup()
        settings.sync()

    def reset_to_defaults(self) -> None:
        self._persist_changes = False
        self._values = {}
        self.update(self._load_defaults())
        self._persist_changes = True
        self._write_to_qsettings()

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self._values)

    def clone(self) -> "MagScopeSettings":
        return MagScopeSettings(self._values)

    def _coerce_setting(self, key: str, value: Any) -> Any:
        if key not in self._SETTING_SPECS:
            raise KeyError(f"Unknown setting '{key}'.")
        spec = self._SETTING_SPECS[key]
        return spec.coerce(value)

    def update(self, mapping: Mapping[str, Any] | Iterable[tuple[str, Any]] = (), **kwargs: Any) -> None:  # type: ignore[override]
        items: Iterable[tuple[str, Any]]
        if isinstance(mapping, Mapping):
            items = mapping.items()
        else:
            items = mapping
        for key, value in items:
            self[key] = value
        for key, value in kwargs.items():
            self[key] = value

    def __getitem__(self, key: str) -> Any:
        return self._values[key]

    def __setitem__(self, key: str, value: Any) -> None:
        coerced = self._coerce_setting(key, value)
        self._values[key] = coerced
        self._write_to_qsettings()

    def __delitem__(self, key: str) -> None:
        raise TypeError("MagScopeSettings does not support deleting settings")

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as file:
            yaml.safe_dump(self._values, file)

    @classmethod
    def from_yaml(cls, path: str) -> "MagScopeSettings":
        with open(path, "r", encoding="utf-8") as file:
            data = yaml.safe_load(file)
        if data is None:
            raise ValueError(f"Settings file {path} is empty")
        if not isinstance(data, dict):
            raise ValueError(f"Settings file {path} must be a YAML mapping")
        return cls(data)

    @classmethod
    def spec_for(cls, key: str) -> SettingSpec:
        return cls._SETTING_SPECS[key]

    @classmethod
    def defined_keys(cls) -> Iterable[str]:
        return cls._SETTING_SPECS.keys()
