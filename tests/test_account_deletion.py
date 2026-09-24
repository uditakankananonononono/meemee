"""Product-wide account deletion: every principal-owned store, crash resume, in-flight work."""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from meemee import api
from meemee import webhooks as webhooks_module
from meemee.account_deletion import AccountPurger, DeletionLedger, PurgeTargets
from meemee.approvals import ApprovalStore
from meemee.auth import TokenStore
from meemee.companion.models import FactInput, UserProfile
from meemee.companion.store import CompanionStore
from meemee.context import ContextRecord, ContextStore
from meemee.entitlements import EntitlementStore
from meemee.idempotency import IdempotencyStore
from meemee.jobs import JobStore
from meemee.memory import MemoryStore
from meemee.monitors import MonitorInput, MonitorStore
from meemee.personal_model import PersonalItemInput, PersonalModelStore
from meemee.quotas import QuotaStore
from meemee.runs import RunStore
from meemee.types import RunReport
from meemee.vault import SecretVault
from meemee.webhooks import WebhookStore
from meemee.worker import cancellation_watcher, purge_if_deleted


def build_targets(root: Path, monkeypatch) -> PurgeTargets:
    monkeypatch.setattr(webhooks_module, "validate_webhook_url", lambda url: url)
    return PurgeTargets(
        jobs=JobStore(root / "jobs.sqlite3"), runs=RunStore(root / "runs.sqlite3"),
        memory=MemoryStore(root / "meemee.sqlite3"), idempotency=IdempotencyStore(root / "idem.sqlite3"),
        quotas=QuotaStore(root / "quotas.sqlite3"), entitlements=EntitlementStore(root / "ent.sqlite3", "starter"),
        approvals=ApprovalStore(root / "approvals.sqlite3"), monitors=MonitorStore(root / "monitors.sqlite3"),
        personal_model=PersonalModelStore(root / "pm.sqlite3"), context=ContextStore(root / "context.sqlite3"),
        webhooks=WebhookStore(root / "webhooks.sqlite3", encryption_key=SecretVault.generate_key()), companion=CompanionStore(root / "companion.sqlite3"),
    )


def seed(t: PurgeTargets, who: str) -> dict:
    job = t.jobs.enqueue(f"{who} secret job goal", principal=who)
    report = RunReport(run_id=f"run-{who}", goal=f"{who} goal", final="done", steps_used=1, tool_results=[])
    t.runs.add(who, report)
    t.memory.add(report.run_id, "goal", f"{who}zebra private memory")
    done = t.jobs.enqueue(f"{who} finished job", principal=who)
    claimed = t.jobs.claim()
    while claimed and claimed["id"] != done:
        t.jobs.finish(claimed["id"], {"run_id": f"jobrun-{claimed['principal']}-{claimed['id'][:4]}"})
        claimed = t.jobs.claim()
    t.jobs.finish(done, {"run_id": f"jobrun-{who}"})
    t.memory.add(f"jobrun-{who}", "final", f"{who}yak job memory")
    t.idempotency.put(who, "/v1/jobs", "k1", {"goal": "x"}, 202, {"id": job})
    t.quotas.consume_job(who)
    t.quotas.set_limit(who, 7)
    t.entitlements.assign(who, "team", datetime.now(timezone.utc).isoformat())
    t.approvals.grant(who, "shell.run", granted_by=who)
    t.monitors.create(who, MonitorInput(name="m", source_id="s", field="f", operator="exists"))
    item = t.personal_model.upsert(who, PersonalItemInput(kind="goal", title="t", value=f"{who} ambition", source_id="user", source_record_id="r"))
    t.personal_model.upsert(who, PersonalItemInput(kind="routine", title="gone", value="soft", source_id="user", source_record_id="r2"))
    t.personal_model.delete(who, t.personal_model.list(who, "routine")[0]["id"])
    t.context.register_source(who, "mail", "gmail", {})
    t.context.ingest(ContextRecord(who, "mail", "e1", "message", "subject", f"{who}walrus inbox text", "2026-09-24T00:00:00Z", {"source": "test"}))
    t.webhooks.subscribe(who, "https://hooks.example.com/x", {"job.done"})
    t.webhooks.enqueue(f"job:{job}:done", "job.done", {"job_id": job}, principal=who)
    t.companion.upsert_user(UserProfile(user_id=who, display_name=who))
    t.companion.add_fact(who, FactInput(text=f"{who} likes tea"), source="test")
    return {"job": job, "item": item}


def test_purge_removes_every_store_for_one_principal_only(tmp_path, monkeypatch):
    t = build_targets(tmp_path, monkeypatch)
    seed(t, "alice"); seed(t, "bob")
    purger = AccountPurger(t, DeletionLedger(tmp_path / "deletions.sqlite3"))
    result = purger.purge("alice", requested_by="alice")

    assert result["status"] == "completed"
    deleted = result["deleted"]
    assert deleted["jobs_deleted"] == 2 and deleted["runs"] == 1 and deleted["memories"] == 2
    assert deleted["personal_items"] == 2 and deleted["context_records"] == 1
    assert deleted["webhook_subscriptions"] == 1 and deleted["webhook_deliveries"] >= 1
    assert deleted["tool_approvals"] == 1 and deleted["monitors"] == 1

    assert t.jobs.list_for_principal("alice")[0] == []
    assert t.runs.run_ids("alice") == []
    assert t.memory.search("alicezebra") == [] and t.memory.search("aliceyak") == []
    assert t.idempotency.get("alice", "/v1/jobs", "k1", {"goal": "x"}) is None
    assert t.quotas.limit("alice") == t.quotas.default
    assert t.approvals.list("alice") == []
    assert t.monitors.list("alice") == []
    assert t.personal_model.list("alice", include_history=True) == []
    assert t.context.search("alice", "alicewalrus") == [] and t.context.recent("alice") == []
    assert t.webhooks.db.execute("SELECT count(*) FROM webhook_subscriptions WHERE principal='alice'").fetchone()[0] == 0
    assert t.companion.export_user_data("alice")["facts"] == []

    # bob is untouched everywhere
    assert len(t.jobs.list_for_principal("bob")[0]) == 2
    assert t.runs.get("bob", "run-bob") is not None
    assert t.memory.search("bobzebra") and t.memory.search("bobyak")
    assert t.approvals.allows("bob", "shell.run")
    assert len(t.monitors.list("bob")) == 1
    assert t.personal_model.list("bob")
    assert t.context.search("bob", "bobwalrus")
    assert t.companion.export_user_data("bob")["facts"]


def test_running_job_is_tombstoned_cancelled_and_deleted_when_worker_settles(tmp_path, monkeypatch):
    t = build_targets(tmp_path, monkeypatch)
    ident = t.jobs.enqueue("alice long running secret", principal="alice")
    assert t.jobs.claim()["id"] == ident
    counts = t.jobs.purge_principal("alice")
    assert counts == {"jobs_deleted": 0, "job_events_deleted": 2, "running_tombstoned": 1}
    row = t.jobs.get(ident)
    assert row["goal"] == "[deleted]" and row["principal"] is None and row["status"] == "cancel_requested"
    assert t.jobs.events(ident) == []

    cancel, stop = threading.Event(), threading.Event()
    cancellation_watcher(t.jobs, ident, cancel, stop)
    assert cancel.is_set()

    t.memory.add("late-run", "tool", "alicequokka written after purge")
    assert t.jobs.cancel_running(ident) is True
    assert purge_if_deleted(t.jobs, t.memory, ident, "late-run") is True
    assert t.jobs.get(ident) is None and t.jobs.events(ident) == []
    assert t.memory.search("alicequokka") == []


def test_failed_tombstone_is_deleted_not_requeued(tmp_path, monkeypatch):
    t = build_targets(tmp_path, monkeypatch)
    ident = t.jobs.enqueue("retryable", principal="alice", max_attempts=5)
    t.jobs.claim()
    t.jobs.purge_principal("alice")
    assert t.jobs.fail(ident, "boom") is True
    assert t.jobs.get(ident) is None and t.jobs.claim() is None


def test_finish_on_tombstone_returns_purged_and_normal_job_unaffected(tmp_path, monkeypatch):
    t = build_targets(tmp_path, monkeypatch)
    mine = t.jobs.enqueue("alice", principal="alice"); t.jobs.claim()
    t.jobs.purge_principal("alice")
    assert t.jobs.finish(mine, {"run_id": "r"}) is True and t.jobs.get(mine) is None
    other = t.jobs.enqueue("bob", principal="bob"); t.jobs.claim()
    assert t.jobs.finish(other, {"run_id": "r2"}) is False
    assert t.jobs.get(other)["status"] == "done"
    assert purge_if_deleted(t.jobs, t.memory, other, "r2") is False


def test_sweep_removes_abandoned_tombstones(tmp_path, monkeypatch):
    t = build_targets(tmp_path, monkeypatch)
    ident = t.jobs.enqueue("orphan", principal="alice"); t.jobs.claim()
    t.jobs.purge_principal("alice")
    assert t.jobs.sweep_purges() == 0  # a worker may still hold it
    future = (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()
    assert t.jobs.sweep_purges(stale_before=future) == 1
    assert t.jobs.get(ident) is None


def test_interrupted_deletion_resumes_without_repeating_finished_steps(tmp_path, monkeypatch):
    t = build_targets(tmp_path, monkeypatch)
    seed(t, "alice")
    ledger = DeletionLedger(tmp_path / "deletions.sqlite3")
    calls = {"runs": 0}
    original_runs = t.runs.delete_principal

    def counting_runs(principal):
        calls["runs"] += 1
        return original_runs(principal)

    monkeypatch.setattr(t.runs, "delete_principal", counting_runs)
    original_webhooks = t.webhooks.delete_principal
    monkeypatch.setattr(t.webhooks, "delete_principal", lambda principal: (_ for _ in ()).throw(OSError("disk gone")))
    with pytest.raises(OSError):
        AccountPurger(t, ledger).purge("alice", requested_by="alice")
    pending = ledger.incomplete()
    assert len(pending) == 1 and pending[0]["principal"] == "alice"
    steps = ledger.done_steps(pending[0]["id"])
    assert {"memory", "jobs", "runs", "context"} <= set(steps) and "webhooks" not in steps

    monkeypatch.setattr(t.webhooks, "delete_principal", original_webhooks)
    resumed = AccountPurger(t, ledger).resume_incomplete()
    assert len(resumed) == 1 and resumed[0]["status"] == "completed"
    assert resumed[0]["deletion_id"] == pending[0]["id"]
    assert calls["runs"] == 1
    assert resumed[0]["steps"]["webhooks"]["webhook_subscriptions"] == 1
    assert ledger.incomplete() == []


def test_late_synchronous_run_is_discarded_only_after_a_deletion(tmp_path, monkeypatch):
    t = build_targets(tmp_path, monkeypatch)
    purger = AccountPurger(t, DeletionLedger(tmp_path / "d.sqlite3"))
    started = datetime.now(timezone.utc).isoformat()
    report = RunReport(run_id="sync-run", goal="g", final="f", steps_used=1, tool_results=[])
    assert purger.discard_late_run("alice", report, started) is False
    purger.purge("alice", requested_by="alice")
    t.memory.add("sync-run", "final", "alicenarwhal")
    assert purger.discard_late_run("alice", report, started) is True
    assert t.memory.search("alicenarwhal") == []
    later = datetime.now(timezone.utc).isoformat()
    assert purger.discard_late_run("alice", report, later) is False


def test_delete_account_endpoint_purges_product_data(monkeypatch, tmp_path):
    t = build_targets(tmp_path, monkeypatch)
    store = TokenStore(tmp_path / "accounts.db")
    monkeypatch.setattr(api, "tokens", store)
    monkeypatch.setattr(api.auth, "store", store)
    ledger = DeletionLedger(tmp_path / "deletions.sqlite3")
    monkeypatch.setattr(api, "deletion_ledger", ledger)
    monkeypatch.setattr(api, "account_purger", AccountPurger(t, ledger))
    client = TestClient(api.app)
    signed = client.post("/v1/accounts/signup", json={"email": "del@example.com", "password": "correct horse battery", "display_name": "Del"})
    account_id = signed.json()["account"]["id"]
    bearer = {"Authorization": f"Bearer {signed.json()['token']}"}
    seed(t, account_id); seed(t, "someone-else")

    response = client.delete("/v1/account", headers=bearer)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["deleted"] and body["sessions_revoked"] and body["audit_retained"]
    assert body["product_records"]["jobs_deleted"] == 2 and body["product_records"]["runs"] == 1
    assert body["companion_records"]["facts"] == 1
    assert ledger.get(body["deletion_id"])["status"] == "completed"
    assert t.jobs.list_for_principal(account_id)[0] == []
    assert len(t.jobs.list_for_principal("someone-else")[0]) == 2
    assert client.get("/v1/account", headers=bearer).status_code == 401


def test_admin_can_purge_external_principal(monkeypatch, tmp_path):
    t = build_targets(tmp_path, monkeypatch)
    ledger = DeletionLedger(tmp_path / "deletions.sqlite3")
    monkeypatch.setattr(api, "deletion_ledger", ledger)
    monkeypatch.setattr(api, "account_purger", AccountPurger(t, ledger))
    monkeypatch.setattr(api.auth, "bootstrap", "admin-bootstrap-token")
    seed(t, "oidc-user-9")
    client = TestClient(api.app)
    admin = {"Authorization": "Bearer admin-bootstrap-token"}
    response = client.delete("/v1/admin/principals/oidc-user-9/data", headers=admin)
    assert response.status_code == 200, response.text
    deletion_id = response.json()["deletion_id"]
    assert client.get(f"/v1/admin/account-deletions/{deletion_id}", headers=admin).json()["status"] == "completed"
    assert t.jobs.list_for_principal("oidc-user-9")[0] == []
    assert client.get("/v1/admin/account-deletions/nope", headers=admin).status_code == 404


def test_cli_account_delete_requires_confirmation_and_purges(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from meemee.cli import app
    from meemee.persistence import build_persistence
    from meemee.vault import SecretVault as _Vault

    monkeypatch.setenv("MEEMEE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MEEMEE_VAULT_KEY", _Vault.generate_key())
    persistence = build_persistence("sqlite", tmp_path)
    persistence.jobs.enqueue("cli secret", principal="zed")
    RunStore(tmp_path / "runs.sqlite3").add("zed", RunReport(run_id="rz", goal="g", final="f", steps_used=1, tool_results=[]))
    persistence.memory.add("rz", "goal", "zedplatypus")
    runner = CliRunner()
    refused = runner.invoke(app, ["account-delete", "zed"])
    assert refused.exit_code != 0 and persistence.jobs.list_for_principal("zed")[0]
    done = runner.invoke(app, ["account-delete", "zed", "--yes"])
    assert done.exit_code == 0, done.output
    assert '"jobs_deleted": 1' in done.output and '"memories": 1' in done.output
    assert persistence.jobs.list_for_principal("zed")[0] == [] and persistence.memory.search("zedplatypus") == []
    resumed = runner.invoke(app, ["account-delete-resume"])
    assert resumed.exit_code == 0 and '"resumed": []' in resumed.output
