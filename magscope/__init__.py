from importlib import import_module

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


def __getattr__(name: str):
    if name not in _EXPORTS:
        raise AttributeError(f'module {__name__!r} has no attribute {name!r}')

    module_name, attribute_name = _EXPORTS[name]
    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
