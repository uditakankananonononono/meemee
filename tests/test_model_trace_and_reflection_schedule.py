import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
import respx

from meemee.companion.engine import CompanionEngine
from meemee.companion.store import CompanionStore
from meemee.config import Settings
from meemee.context import ContextRecord, ContextStore
from meemee.model_profiles import ModelCatalog, RoutedModel, last_model_trace
from meemee.personal_model import PersonalModelStore
from meemee.reflection_schedule import ReflectionSchedule, reflect_due_once


def settings(**kw):
    return Settings(_env_file=None, **kw)


def completion(text):
    return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})


class Plain:
    model = "qwen2.5-coder:14b"

    def __init__(self, replies):
        self.replies = list(replies)

    async def chat(self, messages, temperature=0.7, max_tokens=None):
        return self.replies.pop(0)


async def test_plain_model_reply_carries_and_persists_default_trace(tmp_path: Path):
    store = CompanionStore(tmp_path / "c.db")
    engine = CompanionEngine(Plain(["hi there", '{"facts": []}']), store)
    reply = await engine.reply("udita", "hello")
    assert reply.model_trace["profile"] == "default" and reply.model_trace["model"] == "qwen2.5-coder:14b"
    traces = store.model_traces(reply.conversation_id)
    assert len(traces) == 1 and traces[0]["profile"] == "default"


@respx.mock
async def test_routed_reply_records_fallback_attempts(tmp_path: Path):
    s = settings(model_routes="chat=inkling,local", hf_token="hf_x", model_base_url="http://local.test/v1")
    respx.post("https://router.huggingface.co/v1/chat/completions").mock(return_value=httpx.Response(402))
    respx.post("http://local.test/v1/chat/completions").mock(
        side_effect=[completion("from local"), completion('{"facts": []}')])
    model = RoutedModel(ModelCatalog.from_settings(s), "chat", max_attempts=1)
    store = CompanionStore(tmp_path / "c.db")
    reply = await CompanionEngine(model, store).reply("udita", "hello")
    assert reply.reply == "from local"
    t = reply.model_trace
    assert t["role"] == "chat" and t["profile"] == "local"
    assert [a["profile"] for a in t["attempts"]] == ["inkling", "local"]
    assert "402" in t["attempts"][0]["outcome"]
    saved = store.model_traces(reply.conversation_id)[0]
    assert saved["profile"] == "local" and saved["attempts"][0]["profile"] == "inkling"
    exported = store.export_user_data("udita")
    assert exported["model_traces"] and exported["model_traces"][0]["profile"] == "local"
    deleted = store.delete_user_data("udita")
    assert deleted["model_traces"] == 1 and store.model_traces(reply.conversation_id) == []
    await model.aclose()


@respx.mock
async def test_trace_is_isolated_between_concurrent_tasks():
    s = settings(model_routes="chat=inkling,local", hf_token="hf_x", model_base_url="http://local.test/v1")

    async def hf(request):
        await asyncio.sleep(0.05)
        return completion("hf")

    respx.post("https://router.huggingface.co/v1/chat/completions").mock(side_effect=hf)
    model = RoutedModel(ModelCatalog.from_settings(s), "chat", max_attempts=1)
    fast = RoutedModel(ModelCatalog.from_settings(settings(model_base_url="http://local.test/v1")), "chat",
                       max_attempts=1)
    respx.post("http://local.test/v1/chat/completions").mock(return_value=completion("local"))

    async def call(m):
        await m.chat([{"role": "user", "content": "x"}])
        return last_model_trace(m)["profile"]

    assert await asyncio.gather(call(model), call(fast)) == ["inkling", "local"]
    await model.aclose()
    await fast.aclose()


class Reflect:
    def __init__(self, payload=None, fail=False):
        self.payload, self.fail, self.calls = payload, fail, 0

    async def chat(self, messages, temperature=0.1, max_tokens=None):
        self.calls += 1
        if self.fail:
            raise RuntimeError("model down")
        return json.dumps(self.payload)


def seed(tmp_path, n=1):
    context = ContextStore(tmp_path / "context.db")
    context.register_source("udita", "mail", "gmail", {})
    for i in range(n):
        context.ingest(ContextRecord("udita", "mail", f"m{i}", "document", "Project",
                                     f"I am building Atlas {i}", "2026-09-22T10:00:00Z", {"message_id": f"m{i}"}))
    return context


CLAIM = {"claims": [{"kind": "project", "title": "Atlas", "value": "Building Atlas", "confidence": 0.9,
                     "source_id": "mail", "source_record_id": "m0"}]}


async def test_scheduled_reflection_runs_once_per_new_evidence(tmp_path: Path):
    context = seed(tmp_path)
    personal = PersonalModelStore(tmp_path / "p.db")
    sched = ReflectionSchedule(tmp_path / "s.db")
    model = Reflect(CLAIM)
    t0 = datetime(2026, 9, 24, 12, tzinfo=timezone.utc)
    out = await reflect_due_once(context, personal, model, sched, timedelta(hours=6), now=t0)
    assert out[0]["status"] == "ok" and out[0]["accepted"] == 1
    assert personal.list("udita")[0]["value"] == "Building Atlas"
    # nothing new: skipped even after the interval
    assert await reflect_due_once(context, personal, model, sched, timedelta(hours=6), now=t0 + timedelta(days=1)) == []
    # new evidence but interval not elapsed: skipped; after interval: runs
    context.ingest(ContextRecord("udita", "mail", "m9", "document", "New", "More", "2026-09-23T10:00:00Z", {}))
    assert await reflect_due_once(context, personal, model, sched, timedelta(hours=6), now=t0 + timedelta(hours=1)) == []
    assert len(await reflect_due_once(context, personal, model, sched, timedelta(hours=6), now=t0 + timedelta(hours=7))) == 1
    assert model.calls == 2


async def test_failed_reflection_keeps_watermark_and_audits(tmp_path: Path):
    context = seed(tmp_path)
    personal = PersonalModelStore(tmp_path / "p.db")
    sched = ReflectionSchedule(tmp_path / "s.db")

    class Audit:
        def __init__(self):
            self.rows = []

        def append(self, *args):
            self.rows.append(args)

    audit = Audit()
    t0 = datetime(2026, 9, 24, 12, tzinfo=timezone.utc)
    out = await reflect_due_once(context, personal, Reflect(fail=True), sched, timedelta(hours=1), audit, now=t0)
    assert out[0]["status"] == "failed" and "model down" in out[0]["error"]
    st = sched.state("udita")
    assert st["watermark"] == 0 and st["last_status"] == "failed" and st["last_success_at"] is None
    assert audit.rows[0][1] == "personal_model.reflect.scheduled" and audit.rows[0][3] == "failed"
    out = await reflect_due_once(context, personal, Reflect(CLAIM), sched, timedelta(hours=1), audit,
                                 now=t0 + timedelta(hours=2))
    assert out[0]["status"] == "ok" and sched.state("udita")["watermark"] > 0
    assert sched.delete_owner("udita") == 1


async def test_reflection_worker_refuses_when_disabled(tmp_path):
    from meemee.reflection_schedule import reflection_forever

    with pytest.raises(ValueError, match="disabled"):
        await reflection_forever(settings(data_dir=tmp_path, reflection_interval_minutes=0))
