from __future__ import annotations

import copy
import os
import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, MutableMapping

from PyQt6.QtCore import QSettings
import yaml


DEFAULT_GUI_ACCENT_COLOR = '#78c7ff'
GUI_ACCENT_COLOR_SETTING = 'gui accent color'
GUI_LIVE_PLOT_PROGRESS_BAR_SETTING = 'gui live plot progress bar'
PREFERENCES_BUNDLE_VERSION = 1
TRACKING_OPTIONS_QSETTINGS_GROUP = 'TrackingOptions'
TRACKING_OPTIONS_QSETTINGS_KEY = 'options_yaml'
_HEX_COLOR_RE = re.compile(r'^#[0-9a-fA-F]{6}$')


DEFAULT_TRACKING_OPTIONS: dict[str, Any] = {
    'center_of_mass': {'background': 'median'},
    'n auto_conv_multiline_sub_pixel': 5,
    'auto_conv_multiline_sub_pixel': {'line_ratio': 0.1, 'n_local': 5},
    'use fft_profile': False,
    'fft_profile': {'oversample': 4, 'rmin': 0.0, 'rmax': 0.5, 'gaus_factor': 6.0},
    'radial_profile': {'oversample': 1},
    'lookup_z': {'n_local': 7},
}


def normalize_hex_color(value: str) -> str:
    value = value.strip()
    if not _HEX_COLOR_RE.fullmatch(value):
        raise ValueError("Accent color must use #RRGGBB hex format.")
    return value.lower()


def default_tracking_options() -> dict[str, Any]:
    return copy.deepcopy(DEFAULT_TRACKING_OPTIONS)


def _coerce_tracking_int_value(
    raw: Any,
    *,
    name: str,
    fallback: int,
    minimum: int | None = None,
    enforce_odd: bool = False,
) -> int:
    if raw is None:
        return fallback
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValueError(f'{name} must be an integer')
    if minimum is not None and value < minimum:
        raise ValueError(f'{name} must be at least {minimum}')
    if enforce_odd and value % 2 == 0:
        value += 1
    return value


def _coerce_tracking_float_value(
    raw: Any,
    *,
    name: str,
    fallback: float,
    minimum: float | None = None,
) -> float:
    if raw is None:
        return fallback
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise ValueError(f'{name} must be a number')
    if minimum is not None and value < minimum:
        raise ValueError(f'{name} must be at least {minimum}')
    return value


def _coerce_tracking_bool_value(raw: Any, *, fallback: bool) -> bool:
    if raw is None:
        return fallback
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        normalized = raw.strip().lower()
        if normalized in {'true', '1', 'yes'}:
            return True
        if normalized in {'false', '0', 'no'}:
            return False
    if isinstance(raw, (int, float)):
        return bool(raw)
    raise ValueError('use fft_profile must be a boolean')


def tracking_options_from_mapping(loaded: Any) -> dict[str, Any]:
    if loaded is None:
        raise ValueError('Tracking options file is empty')
    if not isinstance(loaded, Mapping):
        raise ValueError('Tracking options file must be a YAML mapping')

    options = default_tracking_options()

    center_of_mass = loaded.get('center_of_mass')
    if center_of_mass is not None:
        if not isinstance(center_of_mass, Mapping):
            raise ValueError('center_of_mass must be a mapping')
        background = center_of_mass.get('background', options['center_of_mass']['background'])
        if background not in {'none', 'mean', 'median'}:
            raise ValueError('center_of_mass.background must be one of none, mean, median')
        options['center_of_mass']['background'] = background

    options['n auto_conv_multiline_sub_pixel'] = _coerce_tracking_int_value(
        loaded.get('n auto_conv_multiline_sub_pixel'),
        name='n auto_conv_multiline_sub_pixel',
        fallback=options['n auto_conv_multiline_sub_pixel'],
        minimum=1,
    )

    auto_conv_multiline = loaded.get('auto_conv_multiline_sub_pixel')
    if auto_conv_multiline is not None:
        if not isinstance(auto_conv_multiline, Mapping):
            raise ValueError('auto_conv_multiline_sub_pixel must be a mapping')
        options['auto_conv_multiline_sub_pixel']['line_ratio'] = _coerce_tracking_float_value(
            auto_conv_multiline.get('line_ratio'),
            name='auto_conv_multiline_sub_pixel.line_ratio',
            fallback=options['auto_conv_multiline_sub_pixel']['line_ratio'],
            minimum=0.0,
        )
        options['auto_conv_multiline_sub_pixel']['n_local'] = _coerce_tracking_int_value(
            auto_conv_multiline.get('n_local'),
            name='auto_conv_multiline_sub_pixel.n_local',
            fallback=options['auto_conv_multiline_sub_pixel']['n_local'],
            minimum=3,
            enforce_odd=True,
        )

    options['use fft_profile'] = _coerce_tracking_bool_value(
        loaded.get('use fft_profile'),
        fallback=options['use fft_profile'],
    )

    fft_profile = loaded.get('fft_profile')
    if fft_profile is not None:
        if not isinstance(fft_profile, Mapping):
            raise ValueError('fft_profile must be a mapping')
        options['fft_profile']['oversample'] = _coerce_tracking_int_value(
            fft_profile.get('oversample'),
            name='fft_profile.oversample',
            fallback=options['fft_profile']['oversample'],
            minimum=1,
        )
        options['fft_profile']['rmin'] = _coerce_tracking_float_value(
            fft_profile.get('rmin'),
            name='fft_profile.rmin',
            fallback=options['fft_profile']['rmin'],
            minimum=0.0,
        )
        options['fft_profile']['rmax'] = _coerce_tracking_float_value(
            fft_profile.get('rmax'),
            name='fft_profile.rmax',
            fallback=options['fft_profile']['rmax'],
            minimum=0.0,
        )
        options['fft_profile']['gaus_factor'] = _coerce_tracking_float_value(
            fft_profile.get('gaus_factor'),
            name='fft_profile.gaus_factor',
            fallback=options['fft_profile']['gaus_factor'],
            minimum=0.0,
        )

    radial_profile = loaded.get('radial_profile')
    if radial_profile is not None:
        if not isinstance(radial_profile, Mapping):
            raise ValueError('radial_profile must be a mapping')
        options['radial_profile']['oversample'] = _coerce_tracking_int_value(
            radial_profile.get('oversample'),
            name='radial_profile.oversample',
            fallback=options['radial_profile']['oversample'],
            minimum=1,
        )

    lookup_z = loaded.get('lookup_z')
    if lookup_z is not None:
        if not isinstance(lookup_z, Mapping):
            raise ValueError('lookup_z must be a mapping')
        options['lookup_z']['n_local'] = _coerce_tracking_int_value(
            lookup_z.get('n_local'),
            name='lookup_z.n_local',
            fallback=options['lookup_z']['n_local'],
            minimum=3,
            enforce_odd=True,
        )

    return options


def tracking_options_from_qsettings() -> dict[str, Any]:
    settings = QSettings('MagScope', 'MagScope')
    settings.beginGroup(TRACKING_OPTIONS_QSETTINGS_GROUP)
    raw_value = settings.value(TRACKING_OPTIONS_QSETTINGS_KEY, '', type=str)
    settings.endGroup()
    if not raw_value:
        return default_tracking_options()
    try:
        loaded = yaml.safe_load(raw_value)
        return tracking_options_from_mapping(loaded)
    except (ValueError, yaml.YAMLError):
        return default_tracking_options()


def save_tracking_options_to_qsettings(options: Mapping[str, Any]) -> None:
    validated = tracking_options_from_mapping(options)
    settings = QSettings('MagScope', 'MagScope')
    settings.beginGroup(TRACKING_OPTIONS_QSETTINGS_GROUP)
    settings.setValue(TRACKING_OPTIONS_QSETTINGS_KEY, yaml.safe_dump(validated))
    settings.endGroup()
    settings.sync()


def build_preferences_bundle(
    *,
    magscope_settings: MagScopeSettings,
    tracking_options: Mapping[str, Any],
    appearance_layout: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        'version': PREFERENCES_BUNDLE_VERSION,
        'magscope': magscope_settings.to_dict(),
        'tracking': tracking_options_from_mapping(tracking_options),
        'appearance_layout': copy.deepcopy(dict(appearance_layout or {})),
    }


def load_preferences_bundle_mapping(data: Any) -> dict[str, Any]:
    if data is None:
        raise ValueError('Preferences file is empty')
    if not isinstance(data, Mapping):
        raise ValueError('Preferences file must be a YAML mapping')
    version = data.get('version')
    if version != PREFERENCES_BUNDLE_VERSION:
        raise ValueError(f'Unsupported preferences file version: {version!r}')

    magscope = data.get('magscope')
    if not isinstance(magscope, Mapping):
        raise ValueError('Preferences file must include a magscope mapping')

    tracking = data.get('tracking')
    if not isinstance(tracking, Mapping):
        raise ValueError('Preferences file must include a tracking mapping')

    appearance_layout = data.get('appearance_layout', {})
    if not isinstance(appearance_layout, Mapping):
        raise ValueError('appearance_layout must be a mapping')

    try:
        magscope_settings = MagScopeSettings(magscope)
    except KeyError as exc:
        raise ValueError(exc.args[0]) from exc

    return {
        'version': PREFERENCES_BUNDLE_VERSION,
        'magscope': magscope_settings,
        'tracking': tracking_options_from_mapping(tracking),
        'appearance_layout': copy.deepcopy(dict(appearance_layout)),
    }


def export_preferences_bundle(
    path: str | os.PathLike[str],
    *,
    magscope_settings: MagScopeSettings,
    tracking_options: Mapping[str, Any],
    appearance_layout: Mapping[str, Any] | None = None,
) -> None:
    bundle = build_preferences_bundle(
        magscope_settings=magscope_settings,
        tracking_options=tracking_options,
        appearance_layout=appearance_layout,
    )
    with open(path, 'w', encoding='utf-8') as file:
        yaml.safe_dump(bundle, file)


def import_preferences_bundle(path: str | os.PathLike[str]) -> dict[str, Any]:
    with open(path, 'r', encoding='utf-8') as file:
        try:
            data = yaml.safe_load(file)
        except yaml.YAMLError as exc:
            raise ValueError(f'Invalid preferences YAML: {exc}') from exc
    return load_preferences_bundle_mapping(data)


@dataclass(frozen=True)
class SettingSpec:
    key: str
    value_type: type | tuple[type, ...]
    default: Any | None = None
    display_name: str | None = None
    minimum: float | None = None
    maximum: float | None = None
    must_be_even: bool = False
    validator: Callable[[Any], Any] | None = None

    def coerce(self, value: Any) -> Any:
        if isinstance(value, str):
            value = value.strip()
            if value == "":
                raise ValueError(f"Setting '{self.key}' cannot be empty")
            if bool in self._candidate_types:
                normalized = value.lower()
                if normalized in {'true', '1', 'yes'}:
                    coerced = True
                elif normalized in {'false', '0', 'no'}:
                    coerced = False
                else:
                    coerced = value
            else:
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
                f"Setting '{self.key}' must be of type {self.value_type}, not {type(coerced)}."
            )

        if self.minimum is not None and isinstance(coerced, (int, float)):
            if coerced < self.minimum:
                raise ValueError(
                    f"Setting '{self.key}' must be at least {self.minimum}, not {coerced}."
                )

        if self.maximum is not None and isinstance(coerced, (int, float)):
            if coerced > self.maximum:
                raise ValueError(
                    f"Setting '{self.key}' must be at most {self.maximum}, not {coerced}."
                )

        if self.must_be_even and isinstance(coerced, int):
            if coerced % 2 != 0:
                raise ValueError(
                    f"Setting '{self.key}' must be an even integer, not {coerced}."
                )

        if self.validator is not None:
            coerced = self.validator(coerced)

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
    _MAG_SCOPE_PANEL_EXCLUDED_KEYS = {
        GUI_ACCENT_COLOR_SETTING,
        GUI_LIVE_PLOT_PROGRESS_BAR_SETTING,
    }

    _SETTING_SPECS: dict[str, SettingSpec] = {
        "ROI": SettingSpec(
            "ROI",
            value_type=int,
            default=50,
            display_name="ROI (pixels)",
            minimum=8,
            maximum=256,
            must_be_even=True,
        ),
        "magnification": SettingSpec(
            "magnification",
            value_type=(int, float),
            default=1,
            display_name="Magnification (x)",
            minimum=1,
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
        "z-lock default window": SettingSpec(
            "z-lock default window",
            value_type=int,
            default=10,
            display_name="Z-lock default window",
            minimum=1,
        ),
        GUI_ACCENT_COLOR_SETTING: SettingSpec(
            GUI_ACCENT_COLOR_SETTING,
            value_type=str,
            default=DEFAULT_GUI_ACCENT_COLOR,
            display_name="Accent color",
            validator=normalize_hex_color,
        ),
        GUI_LIVE_PLOT_PROGRESS_BAR_SETTING: SettingSpec(
            GUI_LIVE_PLOT_PROGRESS_BAR_SETTING,
            value_type=bool,
            default=True,
            display_name="Show live plot loading indicator",
        ),
    }

    def __init__(
        self,
        values: Mapping[str, Any] | None = None,
        *,
        persistence_available: bool = True,
        persistence_enabled: bool = False,
    ):
        self._persistence_enabled = persistence_enabled
        self._persistence_listeners: list[Callable[["MagScopeSettings"], None]] = []
        self.persistence_available = persistence_available
        self._values: dict[str, Any] = {}
        self.update(self._load_defaults())
        if values:
            self.update(values)

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
        self._update_persistence_availability(
            settings.isWritable() and settings.status() == QSettings.Status.NoError
        )
        return loaded

    def _update_persistence_availability(self, available: bool) -> None:
        was_available = self.persistence_available
        self.persistence_available = available
        if was_available and not available:
            for listener in list(self._persistence_listeners):
                listener(self)

    def add_persistence_listener(
        self, callback: Callable[["MagScopeSettings"], None]
    ) -> None:
        self._persistence_listeners.append(callback)

    @classmethod
    def from_qsettings(
        cls, values: Mapping[str, Any] | None = None
    ) -> "MagScopeSettings":
        settings = cls(persistence_enabled=True)
        settings.update(settings._load_qsettings_values())
        if values:
            settings.update(values)
        return settings

    def save_to_qsettings(self) -> None:
        if not self._persistence_enabled:
            return
        settings = self._qsettings()
        settings.beginGroup(self._QSETTINGS_GROUP)
        settings.remove("")
        for key, value in self._values.items():
            settings.setValue(key, value)
        settings.endGroup()
        settings.sync()
        self._update_persistence_availability(
            settings.isWritable() and settings.status() == QSettings.Status.NoError
        )

    def reset_to_defaults(self) -> None:
        self._values = {}
        self.update(self._load_defaults())

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self._values)

    def clone(self) -> "MagScopeSettings":
        return MagScopeSettings(
            self._values,
            persistence_available=self.persistence_available,
        )

    def persistent_copy(self) -> "MagScopeSettings":
        return MagScopeSettings(
            self._values,
            persistence_available=self.persistence_available,
            persistence_enabled=True,
        )

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

    def __delitem__(self, key: str) -> None:
        raise TypeError("MagScopeSettings does not support deleting settings")

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def export_yaml(self, path: str | os.PathLike[str]) -> None:
        with open(path, "w", encoding="utf-8") as file:
            yaml.safe_dump(self._values, file)

    @classmethod
    def import_yaml(cls, path: str | os.PathLike[str]) -> "MagScopeSettings":
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

    @classmethod
    def magscope_panel_keys(cls) -> Iterable[str]:
        return (
            key for key in cls._SETTING_SPECS.keys()
            if key not in cls._MAG_SCOPE_PANEL_EXCLUDED_KEYS
        )
