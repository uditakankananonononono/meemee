from pathlib import Path

from meemee.release_audit import REQUIRED, audit_tree


def valid_tree(root: Path) -> None:
    for relative in REQUIRED:
        path = root / relative; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("0.37.0\n")
    package = root / "meemee" / "__init__.py"; package.parent.mkdir(); package.write_text('__version__ = "0.37.0"\n')
    (root / "pyproject.toml").write_text('[project]\nversion = "0.37.0"\n')
    (root / "README.md").write_text("0.37.0\n## Verified in v0.37.0 (1)\n1. capability\n")


def test_release_audit_accepts_complete_aligned_tree(tmp_path):
    valid_tree(tmp_path)
    assert audit_tree(tmp_path) == {"status":"pass","findings":[],"summary":{"findings":0}}


def test_release_audit_finds_stub_missing_file_and_version_drift(tmp_path):
    valid_tree(tmp_path)
    (tmp_path / "LICENSE").unlink()
    (tmp_path / "STATUS.md").write_text("old release")
    source = tmp_path / "meemee" / "feature.py"; source.write_text("# " + "TO" + "DO implement this\n")
    report = audit_tree(tmp_path)
    codes = {item["code"] for item in report["findings"]}
    assert report["status"] == "fail"
    assert codes == {"missing_required_file", "version_document_drift", "stub_marker"}
    assert next(item for item in report["findings"] if item["code"] == "stub_marker")["line"] == 1


def test_release_audit_rejects_stale_but_aligned_version(tmp_path):
    valid_tree(tmp_path)
    report = audit_tree(tmp_path, expected_version="0.50.0")
    assert report["status"] == "fail"
    finding = next(item for item in report["findings"] if item["code"] == "expected_version_mismatch")
    assert finding["actual"] == "0.37.0" and finding["expected"] == "0.50.0"


def test_release_audit_rejects_sdk_drift_and_stale_capability_claims(tmp_path):
    valid_tree(tmp_path)
    (tmp_path/"sdk/src/meemee_client").mkdir(parents=True)
    (tmp_path/"sdk/pyproject.toml").write_text('version = "0.9.0"')
    (tmp_path/"sdk/src/meemee_client/_version.py").write_text('__version__ = "0.9.0"')
    (tmp_path/"README.md").write_text((tmp_path/"README.md").read_text()+"\nWebSocket streaming (resume-safe SSE is implemented)")
    codes={item["code"] for item in audit_tree(tmp_path)["findings"]}
    assert {"sdk_version_drift","stale_capability_claim"} <= codes


def test_release_audit_rejects_verified_heading_count_drift(tmp_path):
    valid_tree(tmp_path)
    (tmp_path/"README.md").write_text("0.37.0\n## Verified in v0.37.0 (2)\n1. one\n")
    assert "verified_ledger_drift" in {item["code"] for item in audit_tree(tmp_path)["findings"]}


def test_release_audit_rejects_stale_sdk_contract_label(tmp_path):
    import shutil
    root = Path(__file__).parents[1]
    for relative in ("README.md", "STATUS.md", "CHANGELOG.md", "OPERATIONS.md", "LICENSE", "pyproject.toml", "Dockerfile", ".dockerignore", ".env.example", ".github/workflows/ci.yml", "meemee/__init__.py", "sdk/pyproject.toml", "sdk/src/meemee_client/_version.py", "sdk/README.md"):
        source = root / relative; target = tmp_path / relative; target.parent.mkdir(parents=True, exist_ok=True); shutil.copy(source, target)
    target = tmp_path / "sdk/README.md"
    target.write_text(target.read_text().replace("v0.98.0", "v0.1.0"))
    report = audit_tree(tmp_path, "0.98.0")
    assert "sdk_contract_version_drift" in {item["code"] for item in report["findings"]}
