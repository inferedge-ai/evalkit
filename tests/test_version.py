import pytest

from evalkit import version


def test_parse_release_build():
    assert version.parse("0.1.0+g6aa10cc") == (0, 1, 0, "g6aa10cc")


def test_parse_dev_build():
    assert version.parse("0.1.0") == (0, 1, 0, None)


@pytest.mark.parametrize(
    ("local", "remote", "expected"),
    [
        ("0.1.0+gaaa", "0.1.0+gaaa", version.VersionComparison.SAME),
        ("0.1.0+gaaa", "0.1.0+gbbb", version.VersionComparison.DIFFERENT),
        ("0.1.0", "0.1.0+gbbb", version.VersionComparison.DIFFERENT),
        ("0.1.0+gaaa", "0.1.3+gbbb", version.VersionComparison.DIFFERENT),
        ("0.1.0+gaaa", "0.2.0+gaaa", version.VersionComparison.INCOMPATIBLE),
        ("0.1.0+gaaa", "1.0.0+gaaa", version.VersionComparison.INCOMPATIBLE),
        ("1.1.0+gaaa", "1.2.0+gaaa", version.VersionComparison.DIFFERENT),
    ],
)
def test_compare(local, remote, expected):
    assert version.compare(local, remote) is expected


def test_version_from_wheel_filename():
    name = "evalkit-0.1.0+g6aa10cc-py3-none-any.whl"
    assert version.version_from_wheel_filename(name) == "0.1.0+g6aa10cc"


def test_current_is_installed_metadata():
    assert version.parse(version.current()).major >= 0
