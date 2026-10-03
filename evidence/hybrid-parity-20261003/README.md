# Hybrid-search parity: local reproduction, 2026-10-03

Status: SCOPED. This change documents and tests backend-native hybrid ranking;
it does not deliver identical fused rankings. Independent audit still required.

Base: bc8698c97c9539f6fe5991b880ceba1e985a2dbf, one commit after main
6b8c444. Source lineage: 834dc3b introduced PostgreSQL lexical retrieval using
native ts_rank_cd; bc8698c replaced the misleading semantic/hybrid lexical aliases
with float8[] hash vectors and RRF, retaining the native lexical ranker.

## Why no replacement scoring

The current docs/PG.md explicitly specified ts_rank_cd versus SQLite bm25.
The original owner instruction of October 1, 15:22 IST says:
"Never mark a record 'implemented' because code exists. Mark it only when it runs
and produces the real thing." It also rejects "heuristics, fixed scores,
simulations-as-features" and requires correction of overclaims with the original
quote. Her October 1, 18:03 IST instruction says "code it, change it whatever you
want, your the pilot". These support repair and honest reporting, not inventing a
new ranker. No owner instruction specifically requiring identical hybrid rankings
was located in the recent owner record or available document observations. The
historical export was not available here, so that original-intent boundary remains
unverified; this is not a claim to have audited every historical requirement.

Correction: bc8698c's title "Postgres: real hash-vector semantic_search and RRF
hybrid_search matching SQLite" is too broad. Hash-vector cosine and the RRF
algorithm match on the tested data; hybrid rankings differ because lexical ranks
differ. No production ranker was changed. The PostgreSQL store docstring, README,
PG documentation and changelog now state this boundary.

## Reproduced behavior

Real embedded PostgreSQL 16.2 and SQLite 3.37.2, not mocked database calls.
Baseline vector suite: 44 passed. New fixed-corpus/reporting suite failed first:
3 passed, 1 failed because the original reproduction ignored --json and exposed
no machine-checkable contract, scores or versions. After repair, 56 tests passed:
44 existing PostgreSQL vector tests, 5 new contract tests, 2 SQLite semantic tests
and 5 persistence-selection tests. The full repository suite was not run.

The independent RRF calculation reproduces both native hybrid orderings and
scores. Vector orders and scores agree (absolute tolerance 1e-12) on the five-row,
three-query corpus. Two hybrid orders differ, one agrees. Lexical score values and
rankings are captured in backend-evidence.json. Fixed expected ids live only in
tests, never in production scoring. The --require-hybrid-parity mode exits 1 on
this exact corpus; the native contract mode exits 0. Script finalization closes
both connections and cleans up the embedded server/temp directory.

## Re-run

    python3 -m venv .venv
    .venv/bin/pip install -e '.[dev,postgresql,pgtest]'
    .venv/bin/pytest tests_pg/test_memory_vectors_pg.py tests_pg/test_memory_hybrid_contract_pg.py tests/test_semantic_memory.py tests/test_persistence_selection.py -q
    .venv/bin/python scripts/repro_pg_semantic_parity.py --json
    .venv/bin/python scripts/repro_pg_semantic_parity.py --require-hybrid-parity

The last command deliberately exits 1. No pgvector or learned model is used.
Feature hashing is a lexical-feature similarity heuristic, not learned semantic
intelligence, model training, arbitrary-query parity, deployment or a whole-build
completion claim. Only Linux local execution was exercised. Main is unchanged;
this branch is local and unaudited, with no remote push performed.
