from pathlib import Path

from meemee import __version__

ROOT = Path(__file__).resolve().parents[1]


def test_docs_versions_and_ledger_are_current():
    readme = (ROOT / "README.md").read_text()
    status = (ROOT / "STATUS.md").read_text()
    changelog = (ROOT / "CHANGELOG.md").read_text()
    assert f"Verified in v{__version__}" in readme
    assert f"Current core version:** {__version__}" in status
    assert f"## {__version__}" in changelog
    assert "## Thin (0)" in readme and "## Thin (0)" in status
    assert "## Missing, not claimed" in readme and "## Missing, not claimed" in status


def test_docs_bound_postgres_wiring_to_verified_contract():
    status = (ROOT / "STATUS.md").read_text()
    assert "Opaque PostgreSQL job cursor parity" in status
    assert "Live PostgreSQL test run" in status
    assert "MEEMEE_TEST_DATABASE_URL" in status
