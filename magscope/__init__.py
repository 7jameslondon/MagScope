from importlib import import_module

_SUBMODULES = {
    'camera',
    'datatypes',
    'hardware',
    'ipc',
    'ipc_commands',
    'python_microscope',
    'processes',
    'scope',
    'scripting',
    'ui',
    'utils',
    'zlut_generation',
}

__all__ = [
    'AcquisitionMode',
    'CameraBase',
    'CameraManager',
    'Command',
    'CommandRegistry',
    'ControlPanelBase',
    'Delivery',
    'FocusMotorBase',
    'HardwareManagerBase',
    'MagScope',
    'ManagerProcessBase',
    'MatrixBuffer',
    'PoolVideoFlag',
    'PythonMicroscopeCamera',
    'PythonMicroscopeFocusMotor',
    'PythonMicroscopeHardwareManager',
    'PythonMicroscopeHardwareManagerBase',
    'Script',
    'TimeSeriesPlotBase',
    'UIManager',
    'Units',
    'ZLUTGenerationManager',
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
    'FocusMotorBase': ('magscope.hardware', 'FocusMotorBase'),
    'HardwareManagerBase': ('magscope.hardware', 'HardwareManagerBase'),
    'MagScope': ('magscope.scope', 'MagScope'),
    'ManagerProcessBase': ('magscope.processes', 'ManagerProcessBase'),
    'MatrixBuffer': ('magscope.datatypes', 'MatrixBuffer'),
    'PoolVideoFlag': ('magscope.utils', 'PoolVideoFlag'),
    'PythonMicroscopeCamera': ('magscope.python_microscope', 'PythonMicroscopeCamera'),
    'PythonMicroscopeFocusMotor': (
        'magscope.python_microscope',
        'PythonMicroscopeFocusMotor',
    ),
    'PythonMicroscopeHardwareManager': (
        'magscope.python_microscope',
        'PythonMicroscopeHardwareManager',
    ),
    'PythonMicroscopeHardwareManagerBase': (
        'magscope.python_microscope',
        'PythonMicroscopeHardwareManagerBase',
    ),
    'Script': ('magscope.scripting', 'Script'),
    'TimeSeriesPlotBase': ('magscope.ui', 'TimeSeriesPlotBase'),
    'UIManager': ('magscope.ui', 'UIManager'),
    'Units': ('magscope.utils', 'Units'),
    'ZLUTGenerationManager': ('magscope.zlut_generation', 'ZLUTGenerationManager'),
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
