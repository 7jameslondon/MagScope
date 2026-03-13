from pathlib import Path
import sys
from typing import Any

from PyQt6.QtCore import QSettings
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from magscope.settings import MagScopeSettings, SettingSpec


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

    def value(self, key: str) -> Any:
        return self._group_store()[key]

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


def test_settings_round_trip():
    settings = MagScopeSettings()
    settings["video processors n"] = 4
    settings["video buffer n stacks"] = 6

    path = Path("settings-round-trip-test.yaml")
    try:
        settings.save(path)
        loaded = MagScopeSettings.from_yaml(path)
        assert loaded["video processors n"] == 4
        assert loaded["video buffer n stacks"] == 6
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
    settings.save_to_qsettings()

    reloaded = MagScopeSettings.from_qsettings()

    assert reloaded["magnification"] == 4.2
    assert reloaded["video buffer n images"] == 7


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
