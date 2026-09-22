from pathlib import Path

ROOT=Path(__file__).parents[1]

def test_env_example_has_current_persistence_and_anchor_controls():
    env=(ROOT/".env.example").read_text()
    for key in ("MEEMEE_PERSISTENCE_BACKEND","MEEMEE_POSTGRES_DSN","MEEMEE_AUDIT_ANCHOR_KEY"):
        assert f"{key}=" in env

def test_operations_has_no_obsolete_postgres_or_rate_limit_claims():
    text=(ROOT/"OPERATIONS.md").read_text()
    assert "does not yet claim a supported PostgreSQL path" not in text
    assert "future distributed limiter" not in text
    assert "keep one API replica" in text and "PostgreSQL workers scale horizontally" in text

def test_kubernetes_manifest_uses_current_image_version():
    from meemee import __version__
    text=(ROOT/"deploy/k8s/meemee.yaml").read_text()
    assert text.count(f"ghcr.io/uditakankananonononono/meemee:{__version__}") == 2
