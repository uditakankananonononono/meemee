"""Test-only webhook SSRF override and principal-scoped webhook fan-out."""
from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from meemee import webhooks as webhook_module
from meemee.webhooks import WebhookStore, check_private_hosts_override, validate_webhook_url

REPO_ROOT = Path(__file__).resolve().parents[1]
KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
LOCAL = "https://127.0.0.1:8443/hook"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("MEEMEE_WEBHOOK_ALLOW_PRIVATE_HOSTS", "MEEMEE_ENV"):
        monkeypatch.delenv(name, raising=False)


def test_private_hosts_rejected_by_default():
    with pytest.raises(ValueError, match="private or reserved"):
        validate_webhook_url(LOCAL)


def test_override_allows_loopback_but_still_requires_https(monkeypatch):
    monkeypatch.setenv("MEEMEE_WEBHOOK_ALLOW_PRIVATE_HOSTS", "1")
    assert validate_webhook_url(LOCAL) == LOCAL
    with pytest.raises(ValueError, match="HTTPS"):
        validate_webhook_url("http://127.0.0.1:8080/hook")


@pytest.mark.parametrize("label", ["production", "Production", " production "])
def test_override_refused_in_production(monkeypatch, label):
    monkeypatch.setenv("MEEMEE_WEBHOOK_ALLOW_PRIVATE_HOSTS", "1")
    monkeypatch.setenv("MEEMEE_ENV", label)
    with pytest.raises(RuntimeError, match="refused when MEEMEE_ENV=production"):
        validate_webhook_url(LOCAL)
    with pytest.raises(RuntimeError):
        check_private_hosts_override("api")


def test_production_without_override_is_unaffected(monkeypatch):
    monkeypatch.setenv("MEEMEE_ENV", "production")
    check_private_hosts_override("api")
    with pytest.raises(ValueError, match="private or reserved"):
        validate_webhook_url(LOCAL)


@pytest.mark.parametrize("command", [
    ["-c", "import meemee.api"],
    ["-c", "from meemee.cli import app; app()", "webhook-worker"],
])
def test_processes_refuse_to_start_with_override_in_production(tmp_path, command):
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT), "MEEMEE_DATA_DIR": str(tmp_path), "MEEMEE_VAULT_KEY": KEY,
           "MEEMEE_WEBHOOK_ALLOW_PRIVATE_HOSTS": "1", "MEEMEE_ENV": "production"}
    done = subprocess.run([sys.executable, *command], env=env, cwd=tmp_path, capture_output=True, text=True, timeout=60, check=False)
    assert done.returncode != 0
    assert "refused when MEEMEE_ENV=production" in done.stderr


def test_job_events_reach_only_the_owning_principal(tmp_path, monkeypatch):
    """Regression: enqueue fanned every job event out to every principal's subscriptions."""
    monkeypatch.setattr(webhook_module.socket, "getaddrinfo",
                        lambda *_a, **_k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))])
    store = WebhookStore(tmp_path / "w.sqlite3", encryption_key=KEY)
    alice, _ = store.subscribe("alice", "https://a.example/h", {"job.done"})
    store.subscribe("bob", "https://b.example/h", {"*"})
    assert store.enqueue("job:1:done", "job.done", {"result": "alice only"}, principal="alice") == 1
    rows = store.db.execute("SELECT subscription_id FROM webhook_deliveries").fetchall()
    assert [row[0] for row in rows] == [alice]
    assert store.enqueue("job:2:done", "job.done", {}, principal="carol") == 0
    with pytest.raises(TypeError):
        store.enqueue("job:3:done", "job.done", {})  # the owner is required, never implied
