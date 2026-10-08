"""Area 219: job worker per-job failure containment and job.failed webhook payload.

Seam: work_forever is driven with the claim-once-then-raise-EndLoop pattern from
test_worker_model_http_cleanup.py and a fake agent.run. No sockets, no PG, no concurrent worker, no
real lease expiry. Tests marked PROTECTION already pass on base.
"""
import json
import socket

import httpx
import pytest

from meemee import worker
from meemee.config import Settings
from meemee.llm import ModelError
from meemee.persistence import build_persistence
from meemee.webhooks import WebhookStore

KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="


class EndLoop(Exception):
    pass


def _close_all(stores):
    # persistence.close() leaves most sqlite handles open; ~37 fds per call otherwise, which pushed
    # the whole suite to the 1024 fd limit and broke an unrelated later test.
    for obj in list(vars(stores).values()):
        db = getattr(obj, "db", None)
        if db is not None:
            db.close()


async def drive(monkeypatch, tmp_path, exc, keep=None):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))])
    stores = build_persistence("sqlite", tmp_path, vault_key=KEY)
    hooks = WebhookStore(tmp_path / "webhooks.sqlite3", 256_000, KEY)
    hooks.subscribe("owner", "https://hooks.example/x", {"job.failed"})
    job_id = stores.jobs.enqueue("work", principal="owner", max_attempts=1)
    original_claim = stores.jobs.claim
    claimed = []

    def claim():
        if claimed:
            raise EndLoop
        claimed.append(True)
        return original_claim()
    monkeypatch.setattr(stores.jobs, "claim", claim)
    monkeypatch.setattr(stores, "webhooks", hooks)  # the worker would otherwise open (and leak) its own store
    monkeypatch.setattr(worker, "persistence_from_settings", lambda settings: stores)
    agents = []

    async def fake_build(*a, **k):
        class A:
            async def run(self, goal, **kw):
                raise exc
        agents.append(A())
        return agents[-1]
    monkeypatch.setattr(worker, "build_agent_async", fake_build)
    outcome = None
    try:
        await worker.work_forever(Settings(_env_file=None, data_dir=tmp_path, vault_key=KEY))
    except BaseException as caught:  # noqa: BLE001 - the test inspects what escaped the loop
        outcome = caught
    payloads = [json.loads(r["payload"]) for r in hooks.db.execute("SELECT payload FROM webhook_deliveries")]
    status = stores.jobs.get(job_id)["status"]
    if keep is not None:
        keep.update(stores.jobs.get(job_id))
    hooks.db.close()  # sqlite close is idempotent if persistence.close() already closed it
    _close_all(stores)
    return outcome, payloads, status


HTTPX_ESCAPERS = [httpx.ReadError("https://secret.test/?token=PRIVATE"), httpx.ConnectError("c"),
                  httpx.InvalidURL("u"), httpx.StreamConsumed(), httpx.CookieConflict("c")]


@pytest.mark.parametrize("exc", HTTPX_ESCAPERS, ids=lambda e: type(e).__name__)
async def test_httpx_failure_fails_the_job_and_keeps_the_loop_alive(monkeypatch, tmp_path, exc):
    outcome, payloads, status = await drive(monkeypatch, tmp_path, exc)
    assert isinstance(outcome, EndLoop)  # loop survived to claim again
    assert status == "failed"
    assert [p["event_type"] for p in payloads] == ["job.failed"]
    assert payloads[0]["data"]["error"] == type(exc).__name__


@pytest.mark.parametrize("exc", [OSError("/home/secret/path"), ValueError("PRIVATE"), RuntimeError("PRIVATE"),
                                 RecursionError("PRIVATE"), ModelError("PRIVATE provider detail")])
async def test_webhook_payload_error_is_class_label_not_text(monkeypatch, tmp_path, exc):
    outcome, payloads, status = await drive(monkeypatch, tmp_path, exc)
    assert isinstance(outcome, EndLoop) and status == "failed"
    text = json.dumps(payloads)
    assert "PRIVATE" not in text and "/home/secret" not in text
    assert payloads[0]["data"]["error"] == type(exc).__name__


async def test_third_party_subclass_name_collapses_to_allowlisted_ancestor(monkeypatch, tmp_path):
    PrivateTokenError = type("PrivateTokenError", (ValueError,), {"__module__": "thirdparty.mod"})
    _, payloads, _ = await drive(monkeypatch, tmp_path, PrivateTokenError("x"))
    assert payloads[0]["data"]["error"] == "ValueError"


# ---- PROTECTION (pass on base)
@pytest.mark.parametrize("exc", [KeyError("tool bug"), AttributeError("tool bug")])
async def test_PROTECTION_unlisted_exceptions_still_stop_the_worker_loudly(monkeypatch, tmp_path, exc):
    outcome, payloads, _ = await drive(monkeypatch, tmp_path, exc)
    assert type(outcome) is type(exc)
    assert payloads == []


async def test_PROTECTION_owner_visible_job_error_text_unchanged(monkeypatch, tmp_path):
    row = {}
    _, _, status = await drive(monkeypatch, tmp_path, ValueError("diagnostic for owner"), keep=row)
    assert status == "failed"
    assert "diagnostic for owner" in json.dumps(row, default=str)
