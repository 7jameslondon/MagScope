import importlib
import sys
import types

import pytest

import magscope


def reload_magscope():
    return importlib.reload(magscope)


def test_top_level_submodule_access_is_lazy_and_cached(monkeypatch):
    package = reload_magscope()
    stub_ui = types.ModuleType('magscope.ui')

    monkeypatch.setitem(sys.modules, 'magscope.ui', stub_ui)

    assert package.ui is stub_ui
    assert package.ui is stub_ui


def test_dir_includes_preserved_submodules():
    package = reload_magscope()

    assert 'ui' in dir(package)
    assert 'camera' in dir(package)


def test_unknown_top_level_attribute_still_raises():
    package = reload_magscope()

    with pytest.raises(AttributeError, match="has no attribute 'not_a_real_export'"):
        package.not_a_real_export
