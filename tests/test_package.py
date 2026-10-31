from kipu import __version__


def test_package_exposes_version() -> None:
    assert __version__
