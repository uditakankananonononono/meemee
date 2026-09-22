from pathlib import Path

from meemee.context import ContextRecord, ContextStore
from meemee.timeline import Timeline


def add(store, source, external, title, at, content="body"):
    store.ingest(
        ContextRecord("u", source, external, "event", title, content, at, {"external": external})
    )


def test_range_filters_paginates_and_is_owner_scoped(tmp_path: Path):
    store = ContextStore(tmp_path / "c.db")
    for source in ("mail", "calendar"):
        store.register_source("u", source, "test", {})
    store.register_source("other", "mail", "test", {})
    add(store, "mail", "1", "one", "2026-09-22T10:00:00+00:00")
    add(store, "calendar", "2", "two", "2026-09-22T11:00:00+00:00")
    store.ingest(
        ContextRecord("other", "mail", "x", "event", "secret", "x", "2026-09-22T12:00:00+00:00", {})
    )
    timeline = Timeline(store)
    page = timeline.range("u", limit=1)
    assert [x.title for x in page] == ["two"]
    older = timeline.range("u", before=(page[-1].occurred_at, page[-1].id))
    assert [x.title for x in older] == ["one"]
    assert timeline.range("u", sources={"mail"})[0].provenance == {"external": "1"}


def test_versions_around_and_summary(tmp_path: Path):
    store = ContextStore(tmp_path / "c.db")
    store.register_source("u", "mail", "test", {})
    add(store, "mail", "same", "v1", "2026-09-22T10:00:00+00:00", "first")
    add(store, "mail", "same", "v2", "2026-09-22T10:02:00+00:00", "second")
    timeline = Timeline(store)
    assert [x.title for x in timeline.versions("u", "mail", "same")] == ["v1", "v2"]
    assert len(timeline.around("u", "2026-09-22T10:01:00+00:00", seconds=61)) == 2
    assert timeline.source_summary("u")[0]["event_count"] == 2
