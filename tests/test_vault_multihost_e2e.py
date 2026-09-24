"""Secret vault and key rotation across hosts (PostgreSQL mode).

With per-host vault.sqlite3 a secret stored with ``meemee vault-put`` on host A was missing on host
B, and ``meemee rotate-encryption-key`` re-encrypted only local files, leaving the shared PostgreSQL
webhook secrets under the old key. Each CLI call below runs as a separate process with its own data
directory; the processes share only PostgreSQL.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest
from multihost_helpers import pg_hosts
from test_live_runs_e2e import PG_DSN

pytestmark = pytest.mark.skipif(not PG_DSN, reason="requires real PostgreSQL")
OLD = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="  # the key pg_hosts gives every host


@pytest.fixture(scope="module")
def hosts(tmp_path_factory):
    pytest.importorskip("uvicorn")
    with pg_hosts(tmp_path_factory) as h:
        yield h


def _cli(hosts, data_dir, key, *args, stdin=""):
    env = {**os.environ, "MEEMEE_DATA_DIR": str(data_dir), "MEEMEE_VAULT_KEY": key, "MEEMEE_PERSISTENCE_BACKEND": "postgresql",
           "MEEMEE_POSTGRES_DSN": hosts["dsn"]}
    return subprocess.run([sys.executable, "-c", "from meemee.cli import app; app()", *args], input=stdin, env=env,
                          capture_output=True, text=True, timeout=60, check=False)


def test_secret_put_on_a_is_listed_on_b_and_rotation_covers_shared_secrets(hosts):
    from meemee.vault import SecretVault
    from meemee_persist_pg import Database, WebhookStore

    a_dir, b_dir = hosts["dirs"]
    put = _cli(hosts, a_dir, OLD, "vault-put", "api-token", stdin="s3cret\n")
    assert put.returncode == 0, put.stderr
    listed = _cli(hosts, b_dir, OLD, "vault-list")
    assert listed.returncode == 0 and "api-token" in listed.stdout, listed.stdout + listed.stderr

    db = Database(hosts["dsn"], min_size=1, max_size=2)
    try:
        WebhookStore(db, 256_000, OLD).subscribe("p", "https://example.com/hook", {"job.completed"})
        new = SecretVault.generate_key()
        rotated = _cli(hosts, b_dir, OLD, "rotate-encryption-key", stdin=f"{new}\n{new}\n")
        assert rotated.returncode == 0, rotated.stderr
        report = json.loads(rotated.stdout.strip().splitlines()[-1])
        assert report["vault_secrets"] == 1 and report["webhook_secrets"] >= 1
        WebhookStore(db, 256_000, new)  # the shared webhook secrets now decrypt with the new key
        with pytest.raises(Exception):  # noqa: B017
            WebhookStore(db, 256_000, OLD)
    finally:
        db.close()
    assert "api-token" in _cli(hosts, a_dir, new, "vault-list").stdout
