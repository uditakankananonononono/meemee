from pathlib import Path


def test_docker_build_context_contains_all_commercial_runtime_assets():
    root = Path(__file__).parents[1]
    docker = (root / "Dockerfile").read_text()
    ignored = (root / ".dockerignore").read_text()
    for required in ("COPY pyproject.toml README.md LICENSE", "COPY meemee ./meemee", "COPY meemee_persist_pg ./meemee_persist_pg", "COPY console ./console", "http://127.0.0.1:8787/ready"):
        assert required in docker
    for excluded in (".git", ".env", ".venv", "*.sqlite3", "sdk"):
        assert excluded in ignored.splitlines()


def test_kubernetes_manifest_uses_current_image_and_readiness():
    root = Path(__file__).parents[1]
    manifest = (root / "deploy/k8s/meemee.yaml").read_text()
    from meemee import __version__
    assert manifest.count(f"ghcr.io/uditakankananonononono/meemee:{__version__}") == 2
    assert manifest.count("kind: Deployment") == 1
    assert "name: api" in manifest and "name: worker" in manifest
    assert "replicas: 1" in manifest and "strategy: {type: Recreate}" in manifest
    assert "readOnlyRootFilesystem: true" in manifest
    assert "readinessProbe: {httpGet: {path: /ready" in manifest
    assert "livenessProbe: {httpGet: {path: /health" in manifest
