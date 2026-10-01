"""Source-only packaging guard, not Docker/container acceptance."""

import ast
import re
import shlex
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def copy_runtime_subset(dockerfile: str, destination: Path) -> None:
    """Reconstruct this Dockerfile's simple COPY instructions in a clean dir.

    Fail rather than silently misread future multi-stage/JSON/flagged COPY forms.
    This is not a Docker implementation and does not apply .dockerignore rules.
    """
    for line in dockerfile.splitlines():
        if not line.strip().upper().startswith("COPY "):
            continue
        parts = shlex.split(line)
        assert len(parts) >= 3 and not parts[1].startswith(("--", "["))
        target = Path(parts[-1])
        assert not target.is_absolute() and ".." not in target.parts
        for name in parts[1:-1]:
            source = ROOT / name
            assert source.exists(), f"Missing COPY source: {name}"
            assert ".." not in Path(name).parts
            output = destination / target
            if source.is_dir():
                shutil.copytree(source, output, dirs_exist_ok=True)
            else:
                output.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, output / source.name)


def api_local_imports() -> set[str]:
    """Walk nested/conditional imports too; relative imports belong to meemee."""
    tree = ast.parse((ROOT / "meemee/api.py").read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add("meemee" if node.level else (node.module or "").split(".")[0])
    return {
        name for name in imported
        if (ROOT / name).is_dir() or (ROOT / f"{name}.py").is_file()
    }


def assert_api_packages_copied(destination: Path) -> None:
    required = api_local_imports()
    assert required, "No repository-local API imports discovered"
    missing = sorted(
        name for name in required
        if not (destination / name).exists() and not (destination / f"{name}.py").exists()
    )
    assert not missing, f"API packages missing from Docker COPY subset: {missing}"


def test_every_repository_local_api_import_is_copied(tmp_path):
    copy_runtime_subset((ROOT / "Dockerfile").read_text(), tmp_path)
    assert_api_packages_copied(tmp_path)
    # Hatch's explicit package list must also carry those imports into the wheel.
    wheel_config = (tmp_path / "pyproject.toml").read_text().split(
        "[tool.hatch.build.targets.wheel]", 1
    )[1].split("\n[", 1)[0]
    package_line = re.search(r"^packages\s*=\s*(\[.*?\])", wheel_config, re.MULTILINE)
    assert package_line, "Explicit wheel package list missing"
    packages = ast.literal_eval(package_line.group(1))
    assert api_local_imports() <= set(packages)
    for package in ("console", "product_site", "webapp"):
        assert (tmp_path / package / "index.html").is_file()
        assert (tmp_path / package / "mount.py").is_file()


@pytest.mark.parametrize("package", ["product_site", "webapp"])
def test_guard_rejects_each_original_missing_package(tmp_path, package):
    dockerfile = (ROOT / "Dockerfile").read_text()
    broken = "\n".join(
        line for line in dockerfile.splitlines()
        if line != f"COPY {package} ./{package}"
    )
    copy_runtime_subset(broken, tmp_path)
    with pytest.raises(AssertionError, match=package):
        assert_api_packages_copied(tmp_path)


def test_local_compose_requires_runtime_secrets_and_disables_hosted_fallback():
    compose = (ROOT / "compose.yaml").read_text()
    assert '"127.0.0.1:8787:8787"' in compose
    for secret in ("MEEMEE_VAULT_KEY", "MEEMEE_API_TOKEN"):
        assert f"{secret}: ${{{secret}:?" in compose
    for flag in ("MEEMEE_ALLOW_PAID_MODELS", "MEEMEE_HF_FALLBACK", "MEEMEE_SHARED_ALLOW_HOSTED"):
        assert f'{flag}: "false"' in compose
    assert "meemee-data:/home/meemee/.meemee" in compose
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "MEEMEE_VAULT_KEY" not in dockerfile
    ignored = (ROOT / ".dockerignore").read_text().splitlines()
    assert {".env*", "**/.env*", "**/browser-profiles"} <= set(ignored)
