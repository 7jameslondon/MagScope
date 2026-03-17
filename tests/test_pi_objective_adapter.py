import unittest

from magscope_motors.adapters.pi_objective import PIObjectiveAdapter


class _FakeUsbDevice:
    def __init__(self, *, enumerated: list[str] | None = None, allowed: set[str] | None = None, allow_default: bool = False):
        self._enumerated = list(enumerated or [])
        self._allowed = set(allowed or set())
        self._allow_default = bool(allow_default)
        self.calls: list[str | None] = []

    def ConnectUSB(self, serialnum: str | None = None):
        self.calls.append(serialnum)
        if serialnum is None:
            if self._allow_default:
                return
            raise RuntimeError("default connect failed")
        if str(serialnum) in self._allowed:
            return
        raise RuntimeError(f"serial {serialnum} failed")

    def EnumerateUSB(self):
        return list(self._enumerated)


class _FakeSpeedDevice:
    def __init__(self):
        self.qvls_calls: list[object] = []

    def qVLS(self, axis):
        self.qvls_calls.append(axis)
        return {axis: 12.5}


class _FakeAxisDevice:
    axes = None

    @staticmethod
    def qSAI():
        return "A B"


class _FakePositionDevice:
    def __init__(self):
        self.calls = 0
        self.qerr_calls = 0

    def qPOS(self, axis):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("Position out of limits 7")
        return {axis: 9.0}

    def qERR(self):
        self.qerr_calls += 1
        return 0


class _FakeMoveDevice:
    def __init__(self):
        self.mov_calls: list[tuple[str, float]] = []
        self.vel_calls: list[tuple[str, float]] = []

    def MOV(self, axis, target):
        self.mov_calls.append((axis, float(target)))

    def VEL(self, axis, speed):
        self.vel_calls.append((axis, float(speed)))


class TestPIObjectiveAdapter(unittest.TestCase):
    def test_connect_in_test_mode_sets_connected(self):
        adapter = PIObjectiveAdapter()
        adapter.connect({}, test_mode=True)
        status = adapter.get_status()
        self.assertTrue(status["connected"])

    def test_connect_usb_fallback_uses_enumerated_when_configured_serial_fails(self):
        adapter = PIObjectiveAdapter()
        device = _FakeUsbDevice(enumerated=["111", "222"], allowed={"222"})
        adapter._connect_usb_with_fallback(device, serial_number="bad")  # noqa: SLF001 - private-path behavior
        self.assertEqual(device.calls, ["bad", "111", "222"])

    def test_connect_usb_fallback_raises_clear_error_when_no_controllers_found(self):
        adapter = PIObjectiveAdapter()
        device = _FakeUsbDevice(enumerated=[], allowed=set(), allow_default=False)
        with self.assertRaises(RuntimeError) as context:
            adapter._connect_usb_with_fallback(device, serial_number=None)  # noqa: SLF001 - private-path behavior
        self.assertIn("No PI USB controllers found", str(context.exception))

    def test_resolve_primary_axis_falls_back_to_qsai(self):
        axis = PIObjectiveAdapter._resolve_primary_axis(_FakeAxisDevice())  # noqa: SLF001 - private-path behavior
        self.assertEqual(axis, "A")

    def test_hardware_max_speed_uses_axis_query_first(self):
        adapter = PIObjectiveAdapter()
        adapter._test_mode = False  # noqa: SLF001 - private-path behavior
        adapter._connected = True  # noqa: SLF001 - private-path behavior
        adapter._axis = "A"  # noqa: SLF001 - private-path behavior
        adapter._device = _FakeSpeedDevice()  # noqa: SLF001 - private-path behavior

        speed = adapter.get_hardware_max_speed_nm_s()

        self.assertEqual(speed, 12.5)
        self.assertEqual(adapter._device.qvls_calls, ["A"])  # noqa: SLF001 - private-path behavior

    def test_parse_nm_per_controller_unit_defaults_to_microns(self):
        adapter = PIObjectiveAdapter()
        value = adapter._parse_nm_per_controller_unit({})  # noqa: SLF001 - private-path behavior
        self.assertEqual(value, 1000.0)

    def test_parse_nm_per_controller_unit_accepts_explicit_units(self):
        adapter = PIObjectiveAdapter()
        self.assertEqual(  # noqa: PT009 - direct assertion improves readability
            adapter._parse_nm_per_controller_unit({"controller_units": "nm"}),  # noqa: SLF001 - private-path behavior
            1.0,
        )
        self.assertEqual(  # noqa: PT009 - direct assertion improves readability
            adapter._parse_nm_per_controller_unit({"controller_units": "um"}),  # noqa: SLF001 - private-path behavior
            1000.0,
        )

    def test_query_position_retries_after_controller_error(self):
        adapter = PIObjectiveAdapter()
        adapter._test_mode = False  # noqa: SLF001 - private-path behavior
        adapter._connected = True  # noqa: SLF001 - private-path behavior
        adapter._axis = "A"  # noqa: SLF001 - private-path behavior
        adapter._nm_per_controller_unit = 1000.0  # noqa: SLF001 - private-path behavior
        adapter._device = _FakePositionDevice()  # noqa: SLF001 - private-path behavior

        position_nm = adapter._query_position_nm()  # noqa: SLF001 - private-path behavior

        self.assertEqual(position_nm, 9000.0)
        self.assertEqual(adapter._device.qerr_calls, 1)  # noqa: SLF001 - private-path behavior

    def test_move_absolute_converts_nm_to_controller_units(self):
        adapter = PIObjectiveAdapter()
        adapter._test_mode = False  # noqa: SLF001 - private-path behavior
        adapter._connected = True  # noqa: SLF001 - private-path behavior
        adapter._axis = "A"  # noqa: SLF001 - private-path behavior
        adapter._nm_per_controller_unit = 1000.0  # noqa: SLF001 - private-path behavior
        adapter._device = _FakeMoveDevice()  # noqa: SLF001 - private-path behavior

        adapter.move_absolute(5000.0, speed=2000.0)

        self.assertEqual(adapter._device.mov_calls, [("A", 5.0)])  # noqa: SLF001 - private-path behavior
        self.assertEqual(adapter._device.vel_calls, [("A", 2.0)])  # noqa: SLF001 - private-path behavior


if __name__ == "__main__":
    unittest.main()
