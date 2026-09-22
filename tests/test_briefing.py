from datetime import datetime, timezone
from pathlib import Path

from meemee.briefing import BriefingBuilder
from meemee.context import ContextRecord, ContextStore
from meemee.personal_model import PersonalItemInput, PersonalModelStore
from meemee.timeline import Timeline


def test_briefing_combines_recent_events_priorities_and_provenance(tmp_path: Path):
    context = ContextStore(tmp_path / "context.db")
    context.register_source("u", "mail", "test", {})
    context.ingest(
        ContextRecord(
            "u",
            "mail",
            "m1",
            "event",
            "Deadline",
            "Due Friday",
            "2026-09-22T10:00:00+00:00",
            {"url": "mail://m1"},
        )
    )
    model = PersonalModelStore(tmp_path / "model.db")
    model.upsert(
        "u",
        PersonalItemInput(
            kind="goal",
            title="Ship",
            value="Ship product",
            confidence=0.9,
            source_id="mail",
            source_record_id="m1",
        ),
    )
    briefing = BriefingBuilder(Timeline(context), model).build(
        "u", at=datetime(2026, 9, 23, 0, 0, tzinfo=timezone.utc)
    )
    data = briefing.to_dict()
    assert [section["key"] for section in data["sections"]] == ["timeline", "priorities"]
    assert data["sections"][0]["items"][0]["provenance"] == {"url": "mail://m1"}
    assert data["source_ids"] == ["mail"] and data["item_count"] == 2


def test_briefing_is_owner_scoped_and_validates_window(tmp_path: Path):
    context = ContextStore(tmp_path / "context.db")
    context.register_source("other", "mail", "test", {})
    context.ingest(
        ContextRecord(
            "other", "mail", "m1", "event", "Private", "x", "2026-09-22T10:00:00+00:00", {}
        )
    )
    builder = BriefingBuilder(Timeline(context))
    assert (
        builder.build("u", at=datetime(2026, 9, 23, tzinfo=timezone.utc)).to_dict()["item_count"]
        == 0
    )
    import pytest

    with pytest.raises(ValueError):
        builder.build("u", lookback_hours=0)
