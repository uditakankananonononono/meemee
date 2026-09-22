from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from meemee.companion.channels import LocalChannel
from meemee.companion.engine import CompanionEngine
from meemee.companion.models import CheckInPreferences, UserProfile
from meemee.companion.store import CompanionStore
from meemee.companion.worker import deliver_due_once


class FakeChat:
    async def chat(self, messages, temperature=0.7, max_tokens=None):
        return "how are things going?"


def setup(tmp_path: Path):
    store = CompanionStore(tmp_path / "c.db")
    engine = CompanionEngine(FakeChat(), store)
    channels = {"local": LocalChannel(store)}
    return store, engine, channels


def queue_checkin(store, user="udita", channel="local", address=None, minutes=-1):
    store.upsert_user(UserProfile(
        user_id=user, display_name=user.title(),
        checkins=CheckInPreferences(enabled=True, channel=channel, address=address),
    ))
    due = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    store.schedule_checkin(user, due, f"slot-{minutes}", channel, address)


@pytest.mark.asyncio
async def test_nothing_due(tmp_path: Path):
    store, engine, channels = setup(tmp_path)
    summary = await deliver_due_once(store, engine, channels)
    assert summary == {"claimed": False}


@pytest.mark.asyncio
async def test_due_local_checkin_delivered_into_conversation(tmp_path: Path):
    store, engine, channels = setup(tmp_path)
    queue_checkin(store)
    summary = await deliver_due_once(store, engine, channels)
    assert summary["claimed"] and summary["delivered"]
    rows = store.list_checkins("udita", status="done")
    assert rows[0]["message"] == "how are things going?"
    conversation = store.latest_conversation("udita", "local")
    history = store.history(conversation["id"])
    assert history[-1]["content"] == "how are things going?"
    assert history[-1]["role"] == "assistant"


@pytest.mark.asyncio
async def test_unknown_channel_fails_and_retries(tmp_path: Path):
    store, engine, channels = setup(tmp_path)
    queue_checkin(store, channel="carrier-pigeon")
    summary = await deliver_due_once(store, engine, channels)
    assert summary["claimed"] and not summary["delivered"]
    assert summary["status"] == "queued"
    assert "unknown companion channel" in summary["detail"]


@pytest.mark.asyncio
async def test_nonlocal_channel_without_address_fails(tmp_path: Path):
    store, engine, channels = setup(tmp_path)
    channels["webhook"] = LocalChannel(store)
    queue_checkin(store, channel="webhook", address=None)
    summary = await deliver_due_once(store, engine, channels)
    assert not summary["delivered"] and "no delivery address" in summary["detail"]


@pytest.mark.asyncio
async def test_future_checkin_not_claimed(tmp_path: Path):
    store, engine, channels = setup(tmp_path)
    queue_checkin(store, minutes=60)
    summary = await deliver_due_once(store, engine, channels)
    assert summary == {"claimed": False}
