import subprocess
import sys
import zipfile
from pathlib import Path

from meemee import __version__


def test_real_sdk_wheel_has_current_version_and_typed_client(tmp_path):
    root=Path(__file__).parents[1]
    subprocess.run([sys.executable,"-m","build","--wheel","--outdir",str(tmp_path)],cwd=root/"sdk",check=True,capture_output=True,text=True)
    wheel=next(tmp_path.glob("*.whl"))
    assert f"-{__version__}-" in wheel.name
    with zipfile.ZipFile(wheel) as archive:
        names=set(archive.namelist())
        metadata=archive.read(next(name for name in names if name.endswith(".dist-info/METADATA"))).decode()
    assert {"meemee_client/client.py","meemee_client/models.py","meemee_client/py.typed"} <= names
    assert f"Version: {__version__}" in metadata
