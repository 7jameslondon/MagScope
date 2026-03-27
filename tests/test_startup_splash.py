from importlib import resources


def test_startup_splash_logo_is_packaged():
    logo_resource = resources.files("magscope").joinpath("assets/logo.png")

    assert logo_resource.is_file()
