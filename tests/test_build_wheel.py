import subprocess
import sys
import zipfile
from pathlib import Path


def test_real_wheel_contains_postgres_package_and_migrations(tmp_path):
    root=Path(__file__).parents[1]
    subprocess.run([sys.executable,"-m","build","--wheel","--outdir",str(tmp_path)],cwd=root,check=True,capture_output=True,text=True)
    wheel=next(tmp_path.glob("*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        names=set(archive.namelist())
    assert "meemee_persist_pg/__init__.py" in names
    assert "meemee_persist_pg/sql/001_initial.sql" in names
    assert "meemee_persist_pg/sql/002_job_ownership.sql" in names
