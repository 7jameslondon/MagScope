import os
import types
import unittest
import warnings
from unittest import mock

import egrabber_camera_loader


class TestEGrabberCameraLoader(unittest.TestCase):
    def test_returns_class_when_module_has_egrabber_camera(self):
        fake_module = types.SimpleNamespace(EGrabberCamera=type("EGrabberCamera", (), {}))

        with mock.patch.object(egrabber_camera_loader, "import_module", return_value=fake_module) as mocked:
            camera_cls = egrabber_camera_loader.load_egrabber_camera_class()

        self.assertIs(camera_cls, fake_module.EGrabberCamera)
        mocked.assert_called_once_with("camera_egrabber")

    def test_returns_none_when_optional_modules_are_missing(self):
        with mock.patch.object(
            egrabber_camera_loader,
            "import_module",
            side_effect=ImportError("module not found"),
        ):
            camera_cls = egrabber_camera_loader.load_egrabber_camera_class()

        self.assertIsNone(camera_cls)

    def test_debug_warnings_are_opt_in(self):
        with mock.patch.object(
            egrabber_camera_loader,
            "import_module",
            side_effect=ImportError("module not found"),
        ):
            with mock.patch.dict(os.environ, {"MAGSCOPE_EGRABBER_DEBUG": "1"}, clear=False):
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter("always")
                    camera_cls = egrabber_camera_loader.load_egrabber_camera_class()

        self.assertIsNone(camera_cls)
        self.assertTrue(any("Skipping optional camera module" in str(w.message) for w in caught))


if __name__ == "__main__":
    unittest.main()
