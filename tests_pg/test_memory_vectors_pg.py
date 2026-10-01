"""Hash-vector / hybrid memory search on a real disposable PostgreSQL server (pgserver, local, free).

References are computed independently inside this file (own hashing, own cosine, own RRF, own
lexical ts_rank_cd query); no fixed score constants.
Run: pip install -e ".[dev,postgresql,pgtest]" && pytest tests_pg/test_memory_vectors_pg.py -v
"""
from __future__ import annotations

import hashlib
import math
import re
import threading
import uuid
import warnings

import pytest

warnings.filterwarnings("ignore")
import pgserver  # noqa: E402

from meemee.memory import MemoryStore as SQLiteMemory  # noqa: E402
from meemee.semantic_memory import HashingEmbedder  # noqa: E402
from meemee_persist_pg import Database, MemoryStore, MigrationStore  # noqa: E402
from meemee_persist_pg.memory import EmbeddingIndexError  # noqa: E402
from meemee_persist_pg.migrations import bundled_migrations  # noqa: E402

CORPUS = [
    ("r1", "postgres database backup and restore procedure"),
    ("r1", "browser login cookies and session"),
    ("r2", "restore the database from a nightly dump"),
    ("r2", "database restore drill checklist"),
    ("r1", "quarterly roadmap planning notes"),
    ("r2", "backup rotation schedule for the file server"),
]


# ---- independent reference ------------------------------------------------------------------
def ref_embed(text: str, dims: int) -> list[float]:
    toks = re.findall(r"[a-z0-9]+", text.lower())
    feats: dict[str, int] = {}
    for f in toks + [f"{a}_{b}" for a, b in zip(toks, toks[1:])]:
        feats[f] = feats.get(f, 0) + 1
    v = [0.0] * dims
    for f, n in feats.items():
        d = hashlib.blake2b(f.encode(), digest_size=8).digest()
        v[int.from_bytes(d, "big") % dims] += (1.0 if d[0] & 1 else -1.0) * (1.0 + math.log(n))
    norm = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / norm for x in v]


def ref_semantic(rows, query, limit, dims=256, candidates=200):
    """rows: [(id, run_id, content)]; newest max(limit,candidates) window, cosine, ties newest id."""
    window = sorted(rows, key=lambda r: -r[0])[: max(limit, candidates)]
    q = ref_embed(query, dims)
    scored = [(sum(a * b for a, b in zip(q, ref_embed(r[2], dims))), r[0]) for r in window]
    return sorted(scored, key=lambda s: (-s[0], -s[1]))[: max(1, limit)]


def ref_rrf(lists, limit):
    score: dict[int, float] = {}
    for lst in lists:
        for rank, ident in enumerate(lst, 1):
            score[ident] = score.get(ident, 0.0) + 1.0 / (60 + rank)
    return sorted(score.items(), key=lambda kv: (-kv[1], -kv[0]))[:limit]


def ref_lexical(db, query, n, run_id=None):
    """Independent lexical ranking straight from PostgreSQL (OR of per-token tsqueries)."""
    toks = list(dict.fromkeys(query.split()))
    if not toks:
        return []
    q = " || ".join(["plainto_tsquery('simple',%s)"] * len(toks))
    with db.transaction() as c:
        return [r["id"] for r in c.execute(
            f"""SELECT id FROM meemee_memories WHERE search @@ ({q}) AND (%s::text IS NULL OR run_id=%s)
                ORDER BY ts_rank_cd(search,({q})) DESC, id DESC LIMIT %s""",
            (*toks, run_id, run_id, *toks, n)).fetchall()]


# ---- fixtures -------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def server(tmp_path_factory):
    srv = pgserver.get_server(str(tmp_path_factory.mktemp("pg")))
    yield srv
    srv.cleanup()


@pytest.fixture()
def uri(server):
    name = "t_" + uuid.uuid4().hex[:10]
    server.psql(f"CREATE DATABASE {name}")
    return server.get_uri(name)


@pytest.fixture()
def db(uri):
    d = Database(uri, max_size=12)
    MigrationStore(d).apply()
    yield d
    d.close()


@pytest.fixture()
def store(db):
    s = MemoryStore(db)
    for run, text in CORPUS:
        s.add(run, "fact", text)
    return s


def rows_of(db):
    with db.transaction() as c:
        return [(r["id"], r["run_id"], r["content"]) for r in c.execute(
            "SELECT id,run_id,content FROM meemee_memories ORDER BY id").fetchall()]


QUERIES = ["database restore", "backup", "database backup restore dump", "zzz unrelated", "roadmap notes"]


# ---- tests ----------------------------------------------------------------------------------
@pytest.mark.parametrize("query", QUERIES)
@pytest.mark.parametrize("limit", [1, 3, 50])
def test_semantic_matches_independent_reference(db, store, query, limit):
    got = store.semantic_search(query, limit)
    want = ref_semantic([(i, r, c) for i, r, c in rows_of(db)], query, limit)
    assert [r["id"] for r in got] == [i for _, i in want]
    assert [r["semantic_score"] for r in got] == pytest.approx([s for s, _ in want], abs=1e-12)


@pytest.mark.parametrize("query", QUERIES)
@pytest.mark.parametrize("limit", [1, 2, 5])
def test_hybrid_is_rrf_of_independent_lexical_and_vector_rankings(db, store, query, limit):
    n = max(limit * 3, 20)
    lexical = ref_lexical(db, query, n)
    vector = [i for _, i in ref_semantic(rows_of(db), query, n)]
    want = ref_rrf([lexical, vector], limit)
    got = store.hybrid_search(query, limit)
    assert [r["id"] for r in got] == [i for i, _ in want]
    assert [r["hybrid_score"] for r in got] == pytest.approx([s for _, s in want], abs=1e-15)


def test_semantic_matches_sqlite_backend_on_same_corpus(tmp_path, db, store):
    lite = SQLiteMemory(tmp_path / "m.db")
    for run, text in CORPUS:
        lite.add(run, "fact", text)
    for q in QUERIES + [""]:
        a, b = lite.semantic_search(q, 4), store.semantic_search(q, 4)
        assert [r["id"] for r in a] == [r["id"] for r in b]
        assert [r["semantic_score"] for r in a] == pytest.approx([r["semantic_score"] for r in b], abs=1e-12)


def test_lexical_and_vector_paths_are_distinct(db, store):
    # no shared token: lexical finds nothing, vector still ranks (hash-vector over all candidates)
    assert store.search("zzz unrelated") == []
    assert len(store.semantic_search("zzz unrelated", 3)) == 3
    # lexical is OR over tokens (websearch AND semantics would return nothing for this query)
    ids = {r["id"] for r in store.search("database zzzmissing", 10)}
    assert ids == {i for i, _, c in rows_of(db) if "database" in c.split()}
    # a doc can be lexically absent yet vector-contributing in hybrid
    lex = {r["id"] for r in store.search("database restore dump", 20)}
    hyb = {r["id"] for r in store.hybrid_search("database restore dump", 20)}
    assert hyb - lex, "hybrid must include rows only the vector ranking contributed"
    assert [r["id"] for r in store.search("database restore", 5)] != \
        [r["id"] for r in store.semantic_search("database restore", 5)]


def test_semantic_rows_carry_semantic_score_not_lexical_score(store):
    row = store.semantic_search("database", 1)[0]
    assert "semantic_score" in row and "score" not in row and "e_vec" not in row


def test_run_scoping(db, store):
    for run in ("r1", "r2"):
        got = store.semantic_search("database restore", 10, run_id=run)
        assert got and {r["run_id"] for r in got} == {run}
        scoped = [(i, r, c) for i, r, c in rows_of(db) if r == run]
        assert [r["id"] for r in got] == [i for _, i in ref_semantic(scoped, "database restore", 10)]
        assert {r["run_id"] for r in store.search("database", 10, run_id=run)} <= {run}
        assert {r["run_id"] for r in store.hybrid_search("database restore", 10, run_id=run)} == {run}
    assert store.semantic_search("database", 5, run_id="nobody") == []
    assert store.hybrid_search("database", 5, run_id="nobody") == []


def test_ties_break_newest_id_first_and_are_deterministic(db):
    s = MemoryStore(db)
    ids = [s.add("t", "fact", "identical tie text") for _ in range(6)]
    want = sorted(ids, reverse=True)
    for _ in range(3):
        assert [r["id"] for r in s.semantic_search("identical tie text", 6)] == want
        assert [r["id"] for r in s.search("identical tie text", 6)] == want
        assert [r["id"] for r in s.hybrid_search("identical tie text", 6)] == want


def test_limits_and_candidate_window(db, store):
    assert len(store.semantic_search("database", 2)) == 2
    assert len(store.semantic_search("database", 1000)) == len(CORPUS)  # fewer rows than limit
    assert len(store.semantic_search("database", 0)) == 1  # limit floor matches SQLite (max(1, limit))
    window = store.semantic_search("database restore", 2, candidates=2)
    newest2 = sorted(i for i, _, _ in rows_of(db))[-2:]
    assert {r["id"] for r in window} == set(newest2)
    assert len(store.hybrid_search("database", 100)) <= len(CORPUS)


def test_empty_and_blank_queries(db, store):
    assert store.search("") == [] and store.search("   ") == []
    want = ref_semantic(rows_of(db), "", 4)  # zero vector: every cosine 0, newest first
    got = store.semantic_search("", 4)
    assert [r["id"] for r in got] == [i for _, i in want] == sorted((i for i, _, _ in rows_of(db)), reverse=True)[:4]
    assert all(r["semantic_score"] == 0.0 for r in got)
    assert [r["id"] for r in store.hybrid_search("", 4)] == [i for i, _ in ref_rrf([[], [i for _, i in want]], 4)]


def test_migration_backfill_of_pre_existing_rows(uri):
    d = Database(uri)
    try:
        migrations = bundled_migrations()
        assert migrations[-1].name == "memory_vectors"
        ms = MigrationStore(d)
        ms.apply(target=migrations[-1].version - 1)
        with d.transaction() as c:  # legacy rows written before the vector table existed
            for run, text in CORPUS:
                c.execute("INSERT INTO meemee_memories(run_id,kind,content) VALUES(%s,'fact',%s)", (run, text))
        assert ms.apply() == [migrations[-1].version]
        s = MemoryStore(d)
        with pytest.raises(EmbeddingIndexError):  # fail loud until backfilled
            s.semantic_search("database", 3)
        assert s.backfill_embeddings(batch_size=4) == len(CORPUS)
        assert s.backfill_embeddings() == 0  # idempotent
        got = s.semantic_search("database restore", 6)
        assert [r["id"] for r in got] == [i for _, i in ref_semantic(rows_of(d), "database restore", 6)]
        with d.transaction() as c:
            v = c.execute("SELECT embedding,model,version,dimensions FROM meemee_memory_embeddings ORDER BY memory_id LIMIT 1").fetchone()
        assert v["embedding"] == pytest.approx(ref_embed(CORPUS[0][1], 256), abs=1e-12)
        assert (v["model"], v["version"], v["dimensions"]) == ("hashing-blake2b-uni-bigram", 1, 256)
    finally:
        d.close()


def test_dimension_and_version_mismatch_fail_loud_then_backfill_repairs(db, store):
    small = MemoryStore(db, HashingEmbedder(64))
    with pytest.raises(EmbeddingIndexError, match="backfill_embeddings"):
        small.semantic_search("database", 3)
    with pytest.raises(EmbeddingIndexError):
        small.hybrid_search("database", 3)

    class V2(HashingEmbedder):
        version = 2
    v2 = MemoryStore(db, V2(256))
    with pytest.raises(EmbeddingIndexError):
        v2.semantic_search("database", 3)
    assert v2.backfill_embeddings() == len(CORPUS)
    assert len(v2.semantic_search("database", 3)) == 3
    with pytest.raises(EmbeddingIndexError):  # original embedder is now the mismatched one
        store.semantic_search("database", 3)
    # new rows by a second embedder are visible as mismatches to the first, not silently mixed
    small.backfill_embeddings()
    assert [r["id"] for r in small.semantic_search("database restore", 6)] == \
        [i for _, i in ref_semantic(rows_of(db), "database restore", 6, dims=64)]


def test_stored_vector_with_wrong_cardinality_is_rejected_by_schema(db, store):
    import psycopg
    with pytest.raises(psycopg.errors.CheckViolation):
        with db.transaction() as c:
            c.execute("UPDATE meemee_memory_embeddings SET embedding=ARRAY[0.1,0.2]::float8[] WHERE memory_id=1")
    with pytest.raises(ValueError):
        HashingEmbedder(8)


def test_missing_vector_in_window_fails_loud(db, store):
    with db.transaction() as c:
        c.execute("DELETE FROM meemee_memory_embeddings WHERE memory_id=(SELECT max(id) FROM meemee_memories)")
    with pytest.raises(EmbeddingIndexError):
        store.semantic_search("database", 3)
    assert store.backfill_embeddings() == 1
    assert store.semantic_search("database", 3)


def test_close_and_reopen_preserve_ranking(uri):
    d1 = Database(uri); MigrationStore(d1).apply()
    s1 = MemoryStore(d1)
    for run, text in CORPUS:
        s1.add(run, "fact", text)
    before = [(r["id"], r["semantic_score"]) for r in s1.semantic_search("database restore", 6)]
    hybrid_before = [(r["id"], r["hybrid_score"]) for r in s1.hybrid_search("database restore", 6)]
    d1.close()
    d2 = Database(uri)
    try:
        s2 = MemoryStore(d2)
        assert MigrationStore(d2).apply() == []
        assert [(r["id"], r["semantic_score"]) for r in s2.semantic_search("database restore", 6)] == before
        assert [(r["id"], r["hybrid_score"]) for r in s2.hybrid_search("database restore", 6)] == hybrid_before
    finally:
        d2.close()


def test_concurrent_inserts_are_all_indexed_and_ranked(db):
    s = MemoryStore(db)
    errors, ids = [], []
    lock = threading.Lock()

    def worker(n):
        try:
            for k in range(10):
                i = s.add(f"w{n}", "fact", f"concurrent note {n} item {k} database restore")
                with lock:
                    ids.append(i)
        except Exception as exc:  # pragma: no cover
            errors.append(exc)
    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert not errors and len(set(ids)) == 80
    with db.transaction() as c:
        assert c.execute("SELECT count(*) AS n FROM meemee_memory_embeddings").fetchone()["n"] == 80
    got = s.semantic_search("concurrent note 3 item 7 database restore", 5)
    assert [r["id"] for r in got] == [i for _, i in ref_semantic(rows_of(db), "concurrent note 3 item 7 database restore", 5)]
    assert s.backfill_embeddings() == 0


def test_delete_runs_removes_vectors_and_secrets_are_scrubbed_before_embedding(db, store):
    secret = "ghp_abcdefghijklmnopqrstuvwxyz123456"
    mid = store.add("sec", "fact", f"token {secret} database")
    with db.transaction() as c:
        content = c.execute("SELECT content FROM meemee_memories WHERE id=%s", (mid,)).fetchone()["content"]
        vec = c.execute("SELECT embedding FROM meemee_memory_embeddings WHERE memory_id=%s", (mid,)).fetchone()["embedding"]
    assert secret not in content
    assert vec == pytest.approx(ref_embed(content, 256), abs=1e-12)  # embeds the stored (scrubbed) text
    assert store.delete_runs(["sec"]) == 1
    with db.transaction() as c:
        assert c.execute("SELECT count(*) AS n FROM meemee_memory_embeddings WHERE memory_id=%s", (mid,)).fetchone()["n"] == 0
