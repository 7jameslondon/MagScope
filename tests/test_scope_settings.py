from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from magscope.settings import MagScopeSettings


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
