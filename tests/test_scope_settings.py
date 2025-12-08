from pathlib import Path
import sys

from PyQt6.QtCore import QSettings
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from magscope.settings import MagScopeSettings


@pytest.fixture(autouse=True)
def clear_qsettings():
    settings = QSettings("MagScope", "MagScope")
    settings.beginGroup("MagScopeSettings")
    settings.remove("")
    settings.endGroup()
    settings.sync()
    yield
    settings.beginGroup("MagScopeSettings")
    settings.remove("")
    settings.endGroup()
    settings.sync()


def test_settings_clone_and_reset():
    settings = MagScopeSettings()
    settings['magnification'] = 2.5

    clone = settings.clone()
    settings['magnification'] = 3.0

    assert clone['magnification'] == 2.5

    settings.reset_to_defaults()
    defaults = MagScopeSettings()
    assert settings['magnification'] == defaults['magnification']


def test_settings_round_trip(tmp_path):
    settings = MagScopeSettings()
    settings['video processors n'] = 4
    settings['video buffer n stacks'] = 6

    path = tmp_path / "settings.yaml"
    settings.save(path)

    loaded = MagScopeSettings.from_yaml(path)

    assert loaded['video processors n'] == 4
    assert loaded['video buffer n stacks'] == 6


def test_settings_validation_and_coercion():
    settings = MagScopeSettings()

    settings['video processors n'] = "3"
    assert settings['video processors n'] == 3

    with pytest.raises(ValueError):
        settings['video buffer n stacks'] = 0

    with pytest.raises(KeyError):
        settings['unknown'] = 1


def test_roi_must_be_even():
    settings = MagScopeSettings()

    settings['ROI'] = 2
    assert settings['ROI'] == 2

    with pytest.raises(ValueError):
        settings['ROI'] = 3

    with pytest.raises(ValueError):
        settings['ROI'] = "5"


def test_settings_persist_between_instances():
    settings = MagScopeSettings()
    settings['magnification'] = 4.2
    settings['video buffer n images'] = 7

    reloaded = MagScopeSettings()

    assert reloaded['magnification'] == 4.2
    assert reloaded['video buffer n images'] == 7
