"""Webhook subscriptions and the delivery outbox across hosts (PostgreSQL mode).

With per-host webhooks.sqlite3 a subscription created on API host A was unknown to host B (not
listed, 404 on pause/rotate/delete, not counted against the plan limit) and to workers and
dispatchers on other machines, so jobs finished there never reached the subscriber. Here two API
servers, a worker and two webhook dispatchers each have their own data directory and share only
PostgreSQL, and a real local HTTPS receiver checks signatures.
"""
from __future__ import annotations

import json
import time
import uuid

import httpx
import pytest
from multihost_helpers import pg_hosts
from test_live_runs_e2e import (  # noqa: F401
    PG_DSN,
    WebhookReceiver,
    _headers,
    _minted,
    _subscribe,
    receiver,
    tls,
)

from meemee.webhook_verify import verify_signature

pytestmark = pytest.mark.skipif(not PG_DSN, reason="requires real PostgreSQL")


@pytest.fixture(scope="module")
def cluster(tmp_path_factory, tls):  # noqa: F811
    pytest.importorskip("uvicorn")
    extra = {"MEEMEE_WEBHOOK_ALLOW_PRIVATE_HOSTS": "1", "MEEMEE_WEBHOOK_POLL_SECONDS": "0.2",
             "MEEMEE_WORKER_POLL_SECONDS": "0.2", "SSL_CERT_FILE": str(tls["bundle"])}
    with pg_hosts(tmp_path_factory, extra_env=extra) as h:
        _, worker_log = h["spawn"]("worker", "worker-host")
        dispatcher_logs = [h["spawn"]("webhook-worker", f"dispatch-{i}")[1] for i in range(2)]
        yield {**h, "logs": [worker_log, *dispatcher_logs]}


def _hits(path, count, cluster, seconds=25):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        found = [hit for hit in WebhookReceiver.hits if hit["path"] == path]
        if len(found) >= count:
            return found
        time.sleep(0.1)
    logs = "\n".join(log.read_text()[-1500:] for log in cluster["logs"])
    pytest.fail(f"{path}: {len(found)}/{count} deliveries\n{logs}")


def test_subscription_made_on_a_is_managed_on_b(cluster, receiver):  # noqa: F811
    a, b = cluster["bases"]
    alice, bob = _minted(a)["token"], _minted(b)["token"]
    hook = _subscribe(a, alice, receiver + "/manage", ["job.done"])
    listed = httpx.get(f"{b}/v1/webhooks", headers=_headers(alice), timeout=10).json()["webhooks"]
    assert [w["id"] for w in listed] == [hook["id"]] and "secret" not in listed[0]
    assert httpx.get(f"{b}/v1/webhooks", headers=_headers(bob), timeout=10).json()["webhooks"] == []
    assert httpx.get(f"{b}/v1/whoami", headers=_headers(alice), timeout=10).json()["entitlement"]["usage"]["webhooks"] == 1
    assert httpx.post(f"{b}/v1/webhooks/{hook['id']}/pause", headers=_headers(bob), timeout=10).status_code == 404
    assert httpx.post(f"{b}/v1/webhooks/{hook['id']}/pause", headers=_headers(alice), timeout=10).status_code == 200
    assert httpx.post(f"{a}/v1/webhooks/{hook['id']}/test", headers=_headers(alice), timeout=10).status_code == 404
    assert httpx.post(f"{a}/v1/webhooks/{hook['id']}/resume", headers=_headers(alice), timeout=10).status_code == 200
    assert httpx.delete(f"{b}/v1/webhooks/{hook['id']}", headers=_headers(alice), timeout=10).status_code == 200
    assert httpx.get(f"{a}/v1/whoami", headers=_headers(alice), timeout=10).json()["entitlement"]["usage"]["webhooks"] == 0


def test_job_from_b_run_by_worker_host_is_delivered_once_signed_and_in_tenant(cluster, receiver):  # noqa: F811
    a, b = cluster["bases"]
    alice, bob = _minted(a)["token"], _minted(b)["token"]
    tag = uuid.uuid4().hex[:8]
    alice_path, bob_path = f"/fail-once/alice-{tag}", f"/bob-{tag}"
    hook = _subscribe(a, alice, receiver + alice_path, ["job.done"])
    _subscribe(b, bob, receiver + bob_path, ["*"])
    job = httpx.post(f"{b}/v1/jobs", headers=_headers(alice), json={"goal": "Read note.txt"}, timeout=10).json()
    first, second = _hits(alice_path, 2, cluster)[:2]  # 500 once, then retried by whichever dispatcher
    assert first["headers"]["X-Meemee-Delivery"] == second["headers"]["X-Meemee-Delivery"]
    for hit in (first, second):
        assert verify_signature(hook["secret"], hit["headers"]["X-Meemee-Timestamp"], hit["body"],
                                hit["headers"]["X-Meemee-Signature-256"])
    assert json.loads(second["body"])["data"]["job_id"] == job["id"]
    delivery_id = first["headers"]["X-Meemee-Delivery"]
    deadline = time.monotonic() + 10
    while (d := httpx.get(f"{a}/v1/webhook-deliveries/{delivery_id}", headers=_headers(alice), timeout=10).json()).get("status") != "delivered":
        assert time.monotonic() < deadline, d
        time.sleep(0.2)
    assert d["attempts"] == 2
    attempts = httpx.get(f"{b}/v1/webhook-deliveries/{delivery_id}/attempts", headers=_headers(alice), timeout=10).json()["attempts"]
    assert [x["outcome"] for x in attempts] == ["queued", "delivered"]
    assert httpx.get(f"{a}/v1/webhook-deliveries/{delivery_id}", headers=_headers(bob), timeout=10).status_code == 404
    time.sleep(1.5)  # several polls by both dispatchers: no duplicate, nothing misrouted to bob
    assert len([h for h in WebhookReceiver.hits if h["path"] == alice_path]) == 2
    assert [h for h in WebhookReceiver.hits if h["path"] == bob_path] == []


def test_many_events_two_dispatchers_each_delivered_exactly_once_and_rotation_applies(cluster, receiver):  # noqa: F811
    a, b = cluster["bases"]
    alice = _minted(a)["token"]
    path = f"/burst-{uuid.uuid4().hex[:8]}"
    hook = _subscribe(b, alice, receiver + path, ["*"])
    events = [httpx.post(f"{(a, b)[i % 2]}/v1/webhooks/{hook['id']}/test", headers=_headers(alice), timeout=10).json()["event_id"]
              for i in range(12)]
    _hits(path, 12, cluster)
    time.sleep(1.0)  # both dispatchers keep polling: a double claim would show up as a 13th hit
    hits = [h for h in WebhookReceiver.hits if h["path"] == path]
    assert len(hits) == 12 and len(set(events)) == 12 and all(json.loads(h["body"]) == {"test": True, "webhook_id": hook["id"]} for h in hits)
    assert len({h["headers"]["X-Meemee-Delivery"] for h in hits}) == 12
    rotated = httpx.post(f"{a}/v1/webhooks/{hook['id']}/rotate-secret", headers=_headers(alice), timeout=10).json()["secret"]
    httpx.post(f"{b}/v1/webhooks/{hook['id']}/test", headers=_headers(alice), timeout=10)
    last = _hits(path, 13, cluster)[12]
    assert verify_signature(rotated, last["headers"]["X-Meemee-Timestamp"], last["body"], last["headers"]["X-Meemee-Signature-256"])
    assert not verify_signature(hook["secret"], last["headers"]["X-Meemee-Timestamp"], last["body"], last["headers"]["X-Meemee-Signature-256"])
