import magscope.ui
from magscope.camera import CameraBase, CameraManager
from magscope.datatypes import MatrixBuffer
from magscope.hardware import FocusMotorBase, HardwareManagerBase
from magscope.ui import ControlPanelBase, TimeSeriesPlotBase, UIManager
from magscope.ipc import CommandRegistry, Delivery, register_ipc_command
from magscope.ipc_commands import Command
from magscope.processes import ManagerProcessBase
from magscope.scope import MagScope
from magscope.scripting import Script
from magscope.utils import (AcquisitionMode, PoolVideoFlag, Units, check_cupy, crop_stack_to_rois,
                             date_timestamp_str, numpy_type_to_qt_image_type, register_script_command)
from magscope.zlut_generation import ZLUTGenerationManager
from importlib import import_module

_SUBMODULES = {
    'camera',
    'datatypes',
    'hardware',
    'ipc',
    'ipc_commands',
    'processes',
    'scope',
    'scripting',
    'ui',
    'utils',
}

__all__ = [
    'AcquisitionMode',
    'CameraBase',
    'CameraManager',
    'Command',
    'CommandRegistry',
    'ControlPanelBase',
    'Delivery',
    'HardwareManagerBase',
    'MagScope',
    'ManagerProcessBase',
    'MatrixBuffer',
    'PoolVideoFlag',
    'Script',
    'TimeSeriesPlotBase',
    'UIManager',
    'Units',
    'check_cupy',
    'crop_stack_to_rois',
    'date_timestamp_str',
    'numpy_type_to_qt_image_type',
    'register_ipc_command',
    'register_script_command',
    *_SUBMODULES,
]

_EXPORTS = {
    'AcquisitionMode': ('magscope.utils', 'AcquisitionMode'),
    'CameraBase': ('magscope.camera', 'CameraBase'),
    'CameraManager': ('magscope.camera', 'CameraManager'),
    'Command': ('magscope.ipc_commands', 'Command'),
    'CommandRegistry': ('magscope.ipc', 'CommandRegistry'),
    'ControlPanelBase': ('magscope.ui', 'ControlPanelBase'),
    'Delivery': ('magscope.ipc', 'Delivery'),
    'HardwareManagerBase': ('magscope.hardware', 'HardwareManagerBase'),
    'MagScope': ('magscope.scope', 'MagScope'),
    'ManagerProcessBase': ('magscope.processes', 'ManagerProcessBase'),
    'MatrixBuffer': ('magscope.datatypes', 'MatrixBuffer'),
    'PoolVideoFlag': ('magscope.utils', 'PoolVideoFlag'),
    'Script': ('magscope.scripting', 'Script'),
    'TimeSeriesPlotBase': ('magscope.ui', 'TimeSeriesPlotBase'),
    'UIManager': ('magscope.ui', 'UIManager'),
    'Units': ('magscope.utils', 'Units'),
    'check_cupy': ('magscope.utils', 'check_cupy'),
    'crop_stack_to_rois': ('magscope.utils', 'crop_stack_to_rois'),
    'date_timestamp_str': ('magscope.utils', 'date_timestamp_str'),
    'numpy_type_to_qt_image_type': ('magscope.utils', 'numpy_type_to_qt_image_type'),
    'register_ipc_command': ('magscope.ipc', 'register_ipc_command'),
    'register_script_command': ('magscope.utils', 'register_script_command'),
}

for name in _SUBMODULES | set(_EXPORTS):
    globals().pop(name, None)


def __getattr__(name: str):
    if name in _SUBMODULES:
        value = import_module(f'{__name__}.{name}')
        globals()[name] = value
        return value

    if name not in _EXPORTS:
        raise AttributeError(f'module {__name__!r} has no attribute {name!r}')

    module_name, attribute_name = _EXPORTS[name]
    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
