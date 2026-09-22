from meemee.memory import MemoryStore
from meemee.semantic_memory import HashingEmbedder


def test_hashing_embeddings_are_normalized_deterministic_and_distinct():
    embedder=HashingEmbedder(64)
    first=embedder.embed("database backup restore")
    assert first == embedder.embed("database backup restore")
    assert abs(sum(v*v for v in first)-1) < 1e-9
    assert first != embedder.embed("browser cookies login")


def test_semantic_and_hybrid_search_are_persisted_and_ranked(tmp_path):
    memory=MemoryStore(tmp_path/"m.db",HashingEmbedder(128))
    relevant=memory.add("r","fact","postgres database backup and restore procedure")
    memory.add("r","fact","browser login cookies and session")
    semantic=memory.semantic_search("database restore",limit=2)
    assert semantic[0]["id"] == relevant and semantic[0]["semantic_score"] > semantic[1]["semantic_score"]
    hybrid=memory.hybrid_search("database restore",limit=1)
    assert hybrid[0]["id"] == relevant and hybrid[0]["hybrid_score"] > 0
    reopened=MemoryStore(tmp_path/"m.db",HashingEmbedder(128))
    assert reopened.semantic_search("database restore",limit=1)[0]["id"] == relevant
