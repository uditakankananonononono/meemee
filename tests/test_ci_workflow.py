from pathlib import Path


def test_ci_runs_complete_commercial_release_gate():
    text = (Path(__file__).parents[1] / ".github/workflows/ci.yml").read_text()
    for required in (
        "permissions:", "contents: read", "timeout-minutes:",
        "ruff check", "pytest -q && pytest -q tests_pg/test_contract.py",
        "working-directory: sdk", "python -m build --wheel",
        "meemee release-audit", "meemee package-audit", "actions/upload-artifact@v4",
    ):
        assert required in text
