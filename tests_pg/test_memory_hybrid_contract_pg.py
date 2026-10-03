"""Fixed-corpus backend-native ranking contract, using real SQLite and PostgreSQL.

Expected orders are regression fixtures, not production ranking scores. Both lexical
rankers stay native. Hash-vector parity does not imply fused ranking parity.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest
from test_memory_vectors_pg import db, ref_rrf, server, uri  # noqa: F401

from meemee.memory import MemoryStore as SQLiteMemory
from meemee_persist_pg.memory import MemoryStore

CORPUS = [
    "postgres database backup and restore procedure",
    "restore the database from a nightly dump",
    "browser login cookies and session",
    "database restore drill checklist",
    "quarterly roadmap planning notes",
]

@pytest.mark.parametrize("query,lite_lex,pg_lex,lite_hybrid,pg_hybrid", [
    ("database restore", [4, 1, 2], [4, 2, 1], [4, 1, 2, 5, 3], [4, 2, 1, 5, 3]),
    ("database backup restore dump", [1, 2, 4], [2, 1, 4], [1, 4, 2, 5, 3], [1, 2, 4, 5, 3]),
    ("zzz unrelated", [], [], [3, 5, 4, 1, 2], [3, 5, 4, 1, 2]),
])
def test_fixed_corpus_native_rankings(tmp_path, db, query, lite_lex, pg_lex, lite_hybrid, pg_hybrid):  # noqa: F811 - pytest fixture injection
    lite = SQLiteMemory(tmp_path / "memory.db")
    pg = MemoryStore(db)
    try:
        for text in CORPUS:
            lite.add("r", "fact", text)
            pg.add("r", "fact", text)
        ids = lambda rows: [r["id"] for r in rows]
        assert ids(lite.search(query, 20)) == lite_lex
        assert ids(pg.search(query, 20)) == pg_lex
        a, b = lite.semantic_search(query, 20), pg.semantic_search(query, 20)
        assert ids(a) == ids(b)
        assert [r["semantic_score"] for r in a] == pytest.approx([r["semantic_score"] for r in b], abs=1e-12)
        for store, lexical, expected in [(lite, lite_lex, lite_hybrid), (pg, pg_lex, pg_hybrid)]:
            got = store.hybrid_search(query, 5)
            ref = ref_rrf([lexical, ids(a)], 5)
            assert ids(got) == expected == [i for i, _ in ref]
            assert [r["hybrid_score"] for r in got] == pytest.approx([s for _, s in ref], abs=1e-15)
    finally:
        lite.connection.close()


def test_repro_reports_contract_and_backend_evidence():
    script = Path(__file__).resolve().parents[1] / "scripts" / "repro_pg_semantic_parity.py"
    result = subprocess.run([sys.executable, str(script), "--json"], capture_output=True, text=True, check=True)
    report = json.loads(result.stdout)
    assert report["contract"] == "hash-vector parity; backend-native lexical and hybrid rankings"
    assert report["postgres_version"].startswith("PostgreSQL ")
    assert len(report["queries"]) == 3
    assert all(row["semantic_equal"] and row["native_rrf_valid"] for row in report["queries"])
    assert [row["hybrid_equal"] for row in report["queries"]] == [False, False, True]
    assert all(set(row["sqlite"]) == {"lexical", "semantic", "hybrid"} for row in report["queries"])
    assert report["passed"] is True


def test_strict_hybrid_parity_assumption_fails():
    script = Path(__file__).resolve().parents[1] / "scripts" / "repro_pg_semantic_parity.py"
    result = subprocess.run([sys.executable, str(script), "--json", "--require-hybrid-parity"], capture_output=True, text=True, check=False)
    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert report["passed"] is True  # native contract still holds
    assert report["selected_mode"] == "strict-hybrid-parity"
    assert report["selected_mode_passed"] is False
    assert [row["hybrid_equal"] for row in report["queries"]] == [False, False, True]


def test_strict_text_summary_marks_selected_mode_failure():
    script = Path(__file__).resolve().parents[1] / "scripts" / "repro_pg_semantic_parity.py"
    result = subprocess.run([sys.executable, str(script), "--require-hybrid-parity"], capture_output=True, text=True, check=False)
    assert result.returncode == 1
    assert "Native contract: PASS" in result.stdout
    assert result.stdout.rstrip().endswith("Strict hybrid parity: FAIL")
