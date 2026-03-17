from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from magscope.settings import MagScopeSettings


ROOT_DIR = Path(__file__).resolve().parent
CORE_SETTINGS_PATH = ROOT_DIR / "settings.yaml"
MOTOR_SETTINGS_PATH = ROOT_DIR / "motors_settings.yaml"


def load_core_settings(path: str | Path | None = None) -> MagScopeSettings | None:
    target = Path(path) if path is not None else CORE_SETTINGS_PATH
    if not target.exists():
        return None
    return MagScopeSettings.from_yaml(str(target))


def load_motors_settings(path: str | Path | None = None) -> dict[str, Any]:
    target = Path(path) if path is not None else MOTOR_SETTINGS_PATH
    if not target.exists():
        return {}
    with target.open("r", encoding="utf-8") as stream:
        data = yaml.safe_load(stream)

    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"Motor settings file {target} must be a YAML mapping")

    motors_block = data.get("motors")
    if isinstance(motors_block, dict):
        # Backward-compatibility shim for old wrapper style.
        return dict(motors_block)
    return dict(data)
