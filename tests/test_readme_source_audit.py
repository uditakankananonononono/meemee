"""The source audit cannot quietly drop a claim or change its original words."""
import csv
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_all_187_original_claims_are_preserved_and_audited_once():
    original = list(csv.DictReader((ROOT / "docs/readme-original-187-claims.csv").open()))
    audit = list(csv.DictReader((ROOT / "docs/audit/readme-audit.csv").open()))
    assert len(original) == len(audit) == 187
    assert {int(r["id"]) for r in original} == {int(r["id"]) for r in audit} == set(range(1, 188))
    assert {r["id"]: r["original_claim"] for r in original} == {r["id"]: r["original_claim"] for r in audit}
    assert all(r["code_read"] and r["evidence_and_limit"] for r in audit)
    assert all(r["verdict"] in {"real", "partial", "false", "blocked-on-model"} for r in audit)
    readme = (ROOT / "README.md").read_text()
    entries = re.findall(r"^\| (\d+) \| (real|partial|false|blocked-on-model) \|", readme, re.MULTILINE)
    assert len(entries) == 187 and dict(entries) == {r["id"]: r["verdict"] for r in audit}


def test_first_186_original_words_unchanged_in_complete_inventory():
    old = list(csv.DictReader((ROOT / "docs/readme-original-186-claims.csv").open()))
    new = list(csv.DictReader((ROOT / "docs/readme-original-187-claims.csv").open()))
    assert old == new[:186]
