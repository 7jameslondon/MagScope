import importlib
import unittest


class TestMagtrackImports(unittest.TestCase):
    def test_scope_is_exposed(self):
        module = importlib.import_module("magscope")
        self.assertIsNotNone(getattr(module, "scope", None))


if __name__ == "__main__":
    unittest.main()