from pathlib import Path
import sys
from typing import Any

from PyQt6.QtCore import QSettings
import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from magscope import settings as settings_module
from magscope.settings import (
    DEFAULT_GUI_ACCENT_COLOR,
    GUI_ACCENT_COLOR_SETTING,
    GUI_LIVE_PLOT_PROGRESS_BAR_SETTING,
    MagScopeSettings,
    PREFERENCES_BUNDLE_VERSION,
    SAVE_TRACKING_ROI_POSITIONS_SETTING,
    TRACKING_DATA_FILE_ROTATION_ENABLED_SETTING,
    TRACKING_DATA_FILE_ROTATION_INTERVAL_MINUTES_SETTING,
    TRACKING_OPTIONS_QSETTINGS_GROUP,
    TRACKING_OPTIONS_QSETTINGS_KEY,
    SettingSpec,
    build_preferences_bundle,
    default_tracking_options,
    import_preferences_bundle,
    load_preferences_bundle_mapping,
    tracking_options_from_qsettings,
    tracking_options_from_mapping,
)


class FakeQSettings:
    store: dict[str, dict[str, Any]] = {}
    writable = True
    sync_status = QSettings.Status.NoError

    def __init__(self, organization: str, application: str):
        self.organization = organization
        self.application = application
        self._group: str | None = None
        self._status = QSettings.Status.NoError

    def beginGroup(self, group: str) -> None:
        self._group = group

    def endGroup(self) -> None:
        self._group = None

    def contains(self, key: str) -> bool:
        return key in self._group_store()

    def value(self, key: str, default: Any = None, type: type | None = None) -> Any:
        value = self._group_store().get(key, default)
        if type is not None and value is not None:
            return type(value)
        return value

    def setValue(self, key: str, value: Any) -> None:
        if not type(self).writable:
            self._status = QSettings.Status.AccessError
            return
        self._group_store()[key] = value

    def remove(self, key: str) -> None:
        if not type(self).writable:
            self._status = QSettings.Status.AccessError
            return
        if key == "":
            self._group_store().clear()
            return
        self._group_store().pop(key, None)

    def sync(self) -> None:
        if not type(self).writable:
            self._status = QSettings.Status.AccessError
            return
        self._status = type(self).sync_status

    def status(self) -> QSettings.Status:
        return self._status

    def isWritable(self) -> bool:
        return type(self).writable

    def _group_store(self) -> dict[str, Any]:
        if self._group is None:
            raise RuntimeError("Group must be set before using FakeQSettings")
        return type(self).store.setdefault(self._group, {})


@pytest.fixture
def fake_qsettings(monkeypatch):
    FakeQSettings.store = {}
    FakeQSettings.writable = True
    FakeQSettings.sync_status = QSettings.Status.NoError
    monkeypatch.setattr(MagScopeSettings, "_qsettings", classmethod(lambda cls: FakeQSettings("MagScope", "MagScope")))
    return FakeQSettings


def test_settings_clone_and_reset():
    settings = MagScopeSettings()
    settings["magnification"] = 2.5

    clone = settings.clone()
    settings["magnification"] = 3.0

    assert clone["magnification"] == 2.5

    settings.reset_to_defaults()
    defaults = MagScopeSettings()
    assert settings["magnification"] == defaults["magnification"]


def test_settings_yaml_import_export_round_trip():
    settings = MagScopeSettings()
    settings["video processors n"] = 4
    settings["video buffer n stacks"] = 6
    settings[SAVE_TRACKING_ROI_POSITIONS_SETTING] = True
    settings[TRACKING_DATA_FILE_ROTATION_ENABLED_SETTING] = False
    settings[TRACKING_DATA_FILE_ROTATION_INTERVAL_MINUTES_SETTING] = 15

    path = Path("settings-round-trip-test.yaml")
    try:
        settings.export_yaml(path)
        loaded = MagScopeSettings.import_yaml(path)
        assert loaded["video processors n"] == 4
        assert loaded["video buffer n stacks"] == 6
        assert loaded[SAVE_TRACKING_ROI_POSITIONS_SETTING] is True
        assert loaded[TRACKING_DATA_FILE_ROTATION_ENABLED_SETTING] is False
        assert loaded[TRACKING_DATA_FILE_ROTATION_INTERVAL_MINUTES_SETTING] == 15
    finally:
        path.unlink(missing_ok=True)


def test_settings_validation_and_coercion():
    settings = MagScopeSettings()

    settings["video processors n"] = "3"
    assert settings["video processors n"] == 3

    with pytest.raises(ValueError):
        settings["video buffer n stacks"] = 0

    with pytest.raises(KeyError):
        settings["unknown"] = 1


def test_gui_accent_color_setting_validates_hex_color():
    settings = MagScopeSettings()

    assert settings[GUI_ACCENT_COLOR_SETTING] == DEFAULT_GUI_ACCENT_COLOR

    settings[GUI_ACCENT_COLOR_SETTING] = ' #AABBCC '
    assert settings[GUI_ACCENT_COLOR_SETTING] == '#aabbcc'

    with pytest.raises(ValueError):
        settings[GUI_ACCENT_COLOR_SETTING] = 'blue'

    with pytest.raises(ValueError):
        settings[GUI_ACCENT_COLOR_SETTING] = '#abcd'


def test_gui_live_plot_progress_indicator_setting_coerces_boolean_strings():
    settings = MagScopeSettings()

    assert settings[GUI_LIVE_PLOT_PROGRESS_BAR_SETTING] is True
    assert GUI_LIVE_PLOT_PROGRESS_BAR_SETTING not in list(MagScopeSettings.magscope_panel_keys())

    settings[GUI_LIVE_PLOT_PROGRESS_BAR_SETTING] = 'false'
    assert settings[GUI_LIVE_PLOT_PROGRESS_BAR_SETTING] is False

    settings[GUI_LIVE_PLOT_PROGRESS_BAR_SETTING] = 'yes'
    assert settings[GUI_LIVE_PLOT_PROGRESS_BAR_SETTING] is True

    with pytest.raises(ValueError):
        settings[GUI_LIVE_PLOT_PROGRESS_BAR_SETTING] = 'sometimes'


def test_save_tracking_roi_positions_setting_defaults_to_false_and_is_in_preferences():
    settings = MagScopeSettings()

    assert settings[SAVE_TRACKING_ROI_POSITIONS_SETTING] is False
    assert SAVE_TRACKING_ROI_POSITIONS_SETTING in list(MagScopeSettings.magscope_panel_keys())

    settings[SAVE_TRACKING_ROI_POSITIONS_SETTING] = 'yes'
    assert settings[SAVE_TRACKING_ROI_POSITIONS_SETTING] is True


def test_tracking_data_file_rotation_settings_defaults_and_validation():
    settings = MagScopeSettings()

    assert settings[TRACKING_DATA_FILE_ROTATION_ENABLED_SETTING] is True
    assert settings[TRACKING_DATA_FILE_ROTATION_INTERVAL_MINUTES_SETTING] == 60
    assert TRACKING_DATA_FILE_ROTATION_ENABLED_SETTING in list(
        MagScopeSettings.magscope_panel_keys()
    )
    assert TRACKING_DATA_FILE_ROTATION_INTERVAL_MINUTES_SETTING in list(
        MagScopeSettings.magscope_panel_keys()
    )

    settings[TRACKING_DATA_FILE_ROTATION_ENABLED_SETTING] = 'false'
    settings[TRACKING_DATA_FILE_ROTATION_INTERVAL_MINUTES_SETTING] = '15'

    assert settings[TRACKING_DATA_FILE_ROTATION_ENABLED_SETTING] is False
    assert settings[TRACKING_DATA_FILE_ROTATION_INTERVAL_MINUTES_SETTING] == 15

    with pytest.raises(ValueError):
        settings[TRACKING_DATA_FILE_ROTATION_INTERVAL_MINUTES_SETTING] = 0


def test_roi_must_be_even():
    settings = MagScopeSettings()

    settings["ROI"] = 8
    assert settings["ROI"] == 8

    with pytest.raises(ValueError):
        settings["ROI"] = 3

    with pytest.raises(ValueError):
        settings["ROI"] = "5"


def test_clone_does_not_write_persisted_state(fake_qsettings):
    fake_qsettings.store["MagScopeSettings"] = {"magnification": 2.0}

    settings = MagScopeSettings.from_qsettings()
    clone = settings.clone()
    clone["magnification"] = 4.2

    reloaded = MagScopeSettings.from_qsettings()

    assert settings["magnification"] == 2.0
    assert reloaded["magnification"] == 2.0


def test_settings_persist_between_instances(fake_qsettings):
    settings = MagScopeSettings.from_qsettings()
    settings["magnification"] = 4.2
    settings["video buffer n images"] = 7
    settings[SAVE_TRACKING_ROI_POSITIONS_SETTING] = True
    settings[TRACKING_DATA_FILE_ROTATION_ENABLED_SETTING] = False
    settings[TRACKING_DATA_FILE_ROTATION_INTERVAL_MINUTES_SETTING] = 20
    settings.save_to_qsettings()

    reloaded = MagScopeSettings.from_qsettings()

    assert reloaded["magnification"] == 4.2
    assert reloaded["video buffer n images"] == 7
    assert reloaded[SAVE_TRACKING_ROI_POSITIONS_SETTING] is True
    assert reloaded[TRACKING_DATA_FILE_ROTATION_ENABLED_SETTING] is False
    assert reloaded[TRACKING_DATA_FILE_ROTATION_INTERVAL_MINUTES_SETTING] == 20


def test_save_failure_marks_persistence_unavailable_and_keeps_memory_values(fake_qsettings):
    settings = MagScopeSettings.from_qsettings()
    failures: list[bool] = []
    settings.add_persistence_listener(lambda current: failures.append(current.persistence_available))

    settings["magnification"] = 4.2
    settings["video buffer n images"] = 7
    fake_qsettings.writable = False
    settings.save_to_qsettings()

    reloaded = MagScopeSettings.from_qsettings()

    assert settings["magnification"] == 4.2
    assert settings["video buffer n images"] == 7
    assert settings.persistence_available is False
    assert failures == [False]
    assert reloaded["magnification"] == 1
    assert reloaded["video buffer n images"] == 40


def test_from_qsettings_reports_unavailable_persistence_when_backend_is_not_writable(fake_qsettings):
    fake_qsettings.store["MagScopeSettings"] = {"magnification": 3.4}
    fake_qsettings.writable = False

    settings = MagScopeSettings.from_qsettings()

    assert settings["magnification"] == 3.4
    assert settings.persistence_available is False


def test_settings_respect_maximum_values():
    spec = SettingSpec("test", (int, float), minimum=0, maximum=10)

    assert spec.coerce(5) == 5
    assert spec.coerce(10.0) == 10.0

    with pytest.raises(ValueError):
        spec.coerce(11)

    with pytest.raises(ValueError):
        spec.coerce(11.5)


def test_settingspec_display_label_defaults_to_key():
    spec_with_display = SettingSpec("test", int, display_name="Custom label")
    assert spec_with_display.label == "Custom label"

    spec_without_display = SettingSpec("fallback", int)
    assert spec_without_display.label == "fallback"


def test_tracking_options_validation_coerces_values():
    options = tracking_options_from_mapping(
        {
            'center_of_mass': {'background': 'mean'},
            'n auto_conv_multiline_sub_pixel': '6',
            'auto_conv_multiline_sub_pixel': {'line_ratio': '0.25', 'n_local': '4'},
            'use fft_profile': 'yes',
            'fft_profile': {'oversample': '8', 'rmin': '0.1', 'rmax': '0.9'},
            'lookup_z': {'n_local': 6},
        }
    )

    assert options['center_of_mass']['background'] == 'mean'
    assert options['n auto_conv_multiline_sub_pixel'] == 6
    assert options['auto_conv_multiline_sub_pixel']['line_ratio'] == 0.25
    assert options['auto_conv_multiline_sub_pixel']['n_local'] == 5
    assert options['use fft_profile'] is True
    assert options['fft_profile']['oversample'] == 8
    assert options['fft_profile']['rmin'] == 0.1
    assert options['fft_profile']['rmax'] == 0.9
    assert options['fft_profile']['gaus_factor'] == default_tracking_options()['fft_profile']['gaus_factor']
    assert options['lookup_z']['n_local'] == 7


def test_tracking_options_validation_rejects_invalid_values():
    with pytest.raises(ValueError):
        tracking_options_from_mapping({'center_of_mass': {'background': 'mode'}})

    with pytest.raises(ValueError):
        tracking_options_from_mapping({'fft_profile': {'oversample': 0}})


def test_tracking_options_from_qsettings_falls_back_on_malformed_yaml(fake_qsettings, monkeypatch):
    fake_qsettings.store[TRACKING_OPTIONS_QSETTINGS_GROUP] = {
        TRACKING_OPTIONS_QSETTINGS_KEY: 'tracking: [',
    }
    monkeypatch.setattr(settings_module, 'QSettings', FakeQSettings)

    assert tracking_options_from_qsettings() == default_tracking_options()


def test_preferences_bundle_import_export_round_trip(tmp_path):
    settings = MagScopeSettings()
    settings['magnification'] = 3.5
    tracking = default_tracking_options()
    tracking['use fft_profile'] = True
    tracking['fft_profile']['oversample'] = 9
    appearance_layout = {
        'controls': {
            'workflow_columns': [['Run', 'Custom'], ['Analysis', 'Locking']],
            'panel_collapsed': {'CameraPanel': True},
        },
        'splitter_sizes': {'Main Grip Splitter Sizes': [100, 200]},
    }

    bundle = build_preferences_bundle(
        magscope_settings=settings,
        tracking_options=tracking,
        appearance_layout=appearance_layout,
    )
    path = tmp_path / 'magscope-preferences.yaml'
    with open(path, 'w', encoding='utf-8') as file:
        yaml.safe_dump(bundle, file)

    loaded = import_preferences_bundle(path)

    assert loaded['version'] == PREFERENCES_BUNDLE_VERSION
    assert loaded['magscope']['magnification'] == 3.5
    assert loaded['tracking']['use fft_profile'] is True
    assert loaded['tracking']['fft_profile']['oversample'] == 9
    assert loaded['appearance_layout'] == appearance_layout


def test_preferences_bundle_validation_rejects_missing_sections():
    with pytest.raises(ValueError):
        load_preferences_bundle_mapping({'version': PREFERENCES_BUNDLE_VERSION})

    with pytest.raises(ValueError):
        load_preferences_bundle_mapping(
            {
                'version': PREFERENCES_BUNDLE_VERSION,
                'magscope': {},
                'tracking': {},
                'appearance_layout': [],
            }
        )


def test_preferences_bundle_validation_rejects_unknown_magscope_settings():
    with pytest.raises(ValueError, match='Unknown setting'):
        load_preferences_bundle_mapping(
            {
                'version': PREFERENCES_BUNDLE_VERSION,
                'magscope': {'unknown setting': 1},
                'tracking': {},
            }
        )


def test_preferences_bundle_import_reports_malformed_yaml(tmp_path):
    path = tmp_path / 'magscope-preferences.yaml'
    path.write_text('preferences: [', encoding='utf-8')

    with pytest.raises(ValueError, match='Invalid preferences YAML'):
        import_preferences_bundle(path)


def test_tracking_options_from_mapping_rejects_none():
    from magscope.settings import tracking_options_from_mapping
    with pytest.raises(ValueError, match='empty'):
        tracking_options_from_mapping(None)


def test_tracking_options_from_mapping_rejects_non_mapping():
    from magscope.settings import tracking_options_from_mapping
    with pytest.raises(ValueError, match='YAML mapping'):
        tracking_options_from_mapping("not a mapping")


def test_tracking_options_from_mapping_rejects_invalid_background():
    from magscope.settings import tracking_options_from_mapping
    with pytest.raises(ValueError, match='background'):
        tracking_options_from_mapping({'center_of_mass': {'background': 'invalid'}})


def test_build_preferences_bundle_includes_tracking_options():
    from magscope.settings import build_preferences_bundle, MagScopeSettings, default_tracking_options, PREFERENCES_BUNDLE_VERSION
    settings = MagScopeSettings()
    bundle = build_preferences_bundle(
        magscope_settings=settings,
        tracking_options=default_tracking_options(),
    )
    assert 'tracking' in bundle
    assert 'magscope' in bundle
    assert 'version' in bundle
    assert bundle['version'] == PREFERENCES_BUNDLE_VERSION


def test_coerce_tracking_int_value_non_numeric():
    from magscope.settings import _coerce_tracking_int_value
    with pytest.raises(ValueError, match="must be an integer"):
        _coerce_tracking_int_value("abc", name="test", fallback=5)


def test_coerce_tracking_float_value_non_numeric():
    from magscope.settings import _coerce_tracking_float_value
    with pytest.raises(ValueError, match="must be a number"):
        _coerce_tracking_float_value("abc", name="test", fallback=1.0)


def test_coerce_tracking_float_value_below_minimum():
    from magscope.settings import _coerce_tracking_float_value
    with pytest.raises(ValueError, match="must be at least"):
        _coerce_tracking_float_value(0.1, name="test", fallback=1.0, minimum=1.0)


def test_coerce_tracking_bool_value_numeric():
    from magscope.settings import _coerce_tracking_bool_value
    assert _coerce_tracking_bool_value(1, fallback=False) is True
    assert _coerce_tracking_bool_value(0, fallback=True) is False
    assert _coerce_tracking_bool_value(1.0, fallback=False) is True
    with pytest.raises(ValueError, match="must be a boolean"):
        _coerce_tracking_bool_value([], fallback=False)


def test_tracking_options_mapping_rejects_non_mapping_center_of_mass():
    from magscope.settings import tracking_options_from_mapping
    with pytest.raises(ValueError, match="center_of_mass"):
        tracking_options_from_mapping({'center_of_mass': 'not_a_dict'})


def test_tracking_options_mapping_rejects_non_mapping_auto_conv():
    from magscope.settings import tracking_options_from_mapping
    with pytest.raises(ValueError, match="auto_conv_multiline_sub_pixel"):
        tracking_options_from_mapping({'auto_conv_multiline_sub_pixel': 'not_a_dict'})


def test_tracking_options_mapping_rejects_non_mapping_fft():
    from magscope.settings import tracking_options_from_mapping
    with pytest.raises(ValueError, match="fft_profile"):
        tracking_options_from_mapping({'fft_profile': 'not_a_dict'})


def test_tracking_options_mapping_rejects_non_mapping_radial():
    from magscope.settings import tracking_options_from_mapping
    with pytest.raises(ValueError, match="radial_profile"):
        tracking_options_from_mapping({'radial_profile': 'not_a_dict'})


def test_tracking_options_mapping_rejects_non_mapping_lookup_z():
    from magscope.settings import tracking_options_from_mapping
    with pytest.raises(ValueError, match="lookup_z"):
        tracking_options_from_mapping({'lookup_z': 'not_a_dict'})


def test_load_preferences_bundle_validation_errors():
    from magscope.settings import load_preferences_bundle_mapping, PREFERENCES_BUNDLE_VERSION
    with pytest.raises(ValueError, match="empty"):
        load_preferences_bundle_mapping(None)
    with pytest.raises(ValueError, match="YAML mapping"):
        load_preferences_bundle_mapping([1, 2, 3])
    with pytest.raises(ValueError, match="Unsupported preferences"):
        load_preferences_bundle_mapping({'version': '0', 'magscope': {}, 'tracking': {}})
    with pytest.raises(ValueError, match="tracking"): 
        load_preferences_bundle_mapping({'version': PREFERENCES_BUNDLE_VERSION, 'magscope': {}, 'tracking': 'not_a_dict'})


def test_magscope_settings_iter_and_len():
    from magscope.settings import MagScopeSettings
    s = MagScopeSettings()
    keys = list(s)
    assert len(keys) > 0
    assert len(s) == len(keys)


def test_magscope_settings_persistent_copy():
    from magscope.settings import MagScopeSettings
    s = MagScopeSettings()
    copy = s.persistent_copy()
    assert copy['ROI'] == s['ROI']


def test_magscope_settings_update_iterable():
    from magscope.settings import MagScopeSettings
    s = MagScopeSettings()
    s.update([('ROI', 32)])
    assert s['ROI'] == 32
