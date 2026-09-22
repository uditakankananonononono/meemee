import re
from pathlib import Path

from meemee import __version__


def test_core_sdk_and_documented_versions_match():
    root=Path(__file__).parents[1]
    sdk_version=re.search(r'^version = "([^"]+)"', (root/"sdk/pyproject.toml").read_text(),re.MULTILINE).group(1)
    imported=re.search(r'__version__ = "([^"]+)"',(root/"sdk/src/meemee_client/_version.py").read_text()).group(1)
    assert sdk_version == imported == __version__
    for path in (root/"sdk/README.md",root/"sdk/src/meemee_client/__init__.py",root/"sdk/pyproject.toml"):
        assert "v0.41.0" not in path.read_text()
