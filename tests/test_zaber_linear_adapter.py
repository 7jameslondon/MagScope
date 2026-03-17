import unittest

from magscope_motors.adapters.zaber_linear import ZaberLinearAdapter


class _UnitsBritish:
    LENGTH_MILLIMETRES = object()
    VELOCITY_MILLIMETRES_PER_SECOND = object()


class _UnitsAmerican:
    LENGTH_MILLIMETERS = object()
    VELOCITY_MILLIMETERS_PER_SECOND = object()


class _UnitsEmpty:
    pass


class TestZaberLinearAdapterUnits(unittest.TestCase):
    def test_linear_unit_accepts_british_spelling(self):
        adapter = ZaberLinearAdapter()
        adapter._units = _UnitsBritish  # noqa: SLF001 - unit-level behavior test
        self.assertIs(adapter._linear_unit(), _UnitsBritish.LENGTH_MILLIMETRES)

    def test_linear_unit_accepts_american_spelling(self):
        adapter = ZaberLinearAdapter()
        adapter._units = _UnitsAmerican  # noqa: SLF001 - unit-level behavior test
        self.assertIs(adapter._linear_unit(), _UnitsAmerican.LENGTH_MILLIMETERS)

    def test_linear_unit_raises_clear_error_when_missing(self):
        adapter = ZaberLinearAdapter()
        adapter._units = _UnitsEmpty  # noqa: SLF001 - unit-level behavior test
        with self.assertRaises(RuntimeError) as context:
            adapter._linear_unit()
        self.assertIn("LENGTH_MILLIMETRES", str(context.exception))
        self.assertIn("LENGTH_MILLIMETERS", str(context.exception))


if __name__ == "__main__":
    unittest.main()
