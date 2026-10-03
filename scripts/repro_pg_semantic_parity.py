"""Check hash-vector parity and backend-native RRF on real local PostgreSQL.

Hybrid equality is diagnostic, not the contract. --require-hybrid-parity tests
that stronger (currently false) assumption and exits nonzero on a mismatch.
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import tempfile
import warnings
from pathlib import Path

import pgserver

from meemee.memory import MemoryStore as Lite
from meemee_persist_pg import Database, MigrationStore
from meemee_persist_pg.memory import MemoryStore as PG

CORPUS = ["postgres database backup and restore procedure", "restore the database from a nightly dump",
          "browser login cookies and session", "database restore drill checklist", "quarterly roadmap planning notes"]
QUERIES = ["database restore", "database backup restore dump", "zzz unrelated"]


def _rrf_valid(lexical, semantic, hybrid, limit):
    # Independent reference: never call the production RRF function.
    scores = {}
    for rows in (lexical, semantic):
        for rank, row in enumerate(rows, 1):
            ident = row["id"]
            scores[ident] = scores.get(ident, 0.0) + 1.0 / (60 + rank)
    ids = sorted(scores, key=lambda ident: (-scores[ident], -ident))[:limit]
    return [r["id"] for r in hybrid] == ids and all(
        math.isclose(r["hybrid_score"], scores[r["id"]], rel_tol=0, abs_tol=1e-15) for r in hybrid)


def reproduce():
    with tempfile.TemporaryDirectory() as directory:
        d = Path(directory)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            srv = pgserver.get_server(str(d / "pg"))
        db = lite = None
        try:
            db = Database(srv.get_uri())
            MigrationStore(db).apply()
            with db.transaction() as c:
                version = c.execute("SELECT version() AS version").fetchone()["version"]
            pg, lite = PG(db), Lite(d / "memory.db")
            for text in CORPUS:
                pg.add("r", "fact", text)
                lite.add("r", "fact", text)
            report = {"contract": "hash-vector parity; backend-native lexical and hybrid rankings",
                      "postgres_version": version, "sqlite_version": sqlite3.sqlite_version,
                      "corpus": CORPUS, "queries": []}
            for query in QUERIES:
                entry = {"query": query}
                results = {}
                for backend, store in (("sqlite", lite), ("postgres", pg)):
                    lex, sem, hyb = store.search(query, 20), store.semantic_search(query, 20), store.hybrid_search(query, 5)
                    results[backend] = (lex, sem, hyb)
                    entry[backend] = {
                        "lexical": [{"id": r["id"], "score": r["score"]} for r in lex],
                        "semantic": [{"id": r["id"], "semantic_score": r["semantic_score"]} for r in sem],
                        "hybrid": [{"id": r["id"], "hybrid_score": r["hybrid_score"]} for r in hyb],
                    }
                a, b = results["sqlite"][1], results["postgres"][1]
                entry["semantic_equal"] = [r["id"] for r in a] == [r["id"] for r in b] and all(
                    math.isclose(x["semantic_score"], y["semantic_score"], rel_tol=0, abs_tol=1e-12)
                    for x, y in zip(a, b))
                entry["hybrid_equal"] = entry["sqlite"]["hybrid"] == entry["postgres"]["hybrid"]
                entry["native_rrf_valid"] = all(_rrf_valid(*rows, 5) for rows in results.values())
                report["queries"].append(entry)
            report["passed"] = all(r["semantic_equal"] and r["native_rrf_valid"] for r in report["queries"])
            return report
        finally:
            if lite is not None:
                lite.connection.close()
            if db is not None:
                db.close()
            srv.cleanup()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Emit full rankings, scores, versions and contract checks")
    parser.add_argument("--require-hybrid-parity", action="store_true", help="Fail on the stronger cross-backend equality assumption")
    args = parser.parse_args()
    report = reproduce()
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(report["contract"])
        print(report["postgres_version"])
        print("SQLite", report["sqlite_version"])
        for row in report["queries"]:
            print(repr(row["query"]))
            for path in ("lexical", "semantic", "hybrid"):
                a, b = ([r["id"] for r in row[backend][path]] for backend in ("sqlite", "postgres"))
                print(f"  {path:8} sqlite={a} postgres={b} {'SAME' if a == b else 'DIFFERENT'}")
            print(f"  hash-vector parity={row['semantic_equal']} native RRF valid={row['native_rrf_valid']}")
        print("Contract:", "PASS" if report["passed"] else "FAIL")
    parity = all(r["hybrid_equal"] for r in report["queries"])
    return 0 if report["passed"] and (not args.require_hybrid_parity or parity) else 1


if __name__ == "__main__":
    raise SystemExit(main())
