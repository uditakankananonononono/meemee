"""Reproduce Docker source-copy omissions with an actual wheel build."""
import shutil
import subprocess
import sys
import zipfile


def test_docker_copied_source_build_contains_all_runtime_packages(tmp_path):
    root = __import__('pathlib').Path(__file__).parents[1]
    # Mirror COPY instructions, rather than testing a wheel of the full checkout.
    for line in (root / 'Dockerfile').read_text().splitlines():
        if not line.startswith('COPY '):
            continue
        parts = line.split()[1:]
        sources, destination = parts[:-1], parts[-1]
        for source in sources:
            src = root / source
            dst = tmp_path / (src.name if destination == './' else destination.removeprefix('./'))
            if src.is_dir():
                shutil.copytree(src, dst)
            else:
                shutil.copy(src, dst)
    output = tmp_path / 'dist'
    subprocess.run([sys.executable, '-m', 'build', '--wheel', '--outdir', str(output)],
                   cwd=tmp_path, check=True, capture_output=True)
    with zipfile.ZipFile(next(output.glob('*.whl'))) as wheel:
        files = set(wheel.namelist())
        for module in ['meemee/__init__.py', 'console/__init__.py',
                       'webapp/__init__.py', 'product_site/__init__.py', 'meemee_persist_pg/__init__.py']:
            assert module in files, f'Docker build omitted runtime module: {module}'
