"""Scheduled reflection must not watermark records it never supplied."""
import json
from datetime import timedelta

from meemee.context import ContextRecord, ContextStore
from meemee.personal_model import PersonalModelStore
from meemee.reflection_schedule import ReflectionSchedule, reflect_due_once


class CaptureModel:
    def __init__(self):
        self.seen = []

    async def chat(self, messages, **kwargs):
        self.seen.extend(row['source_record_id'] for row in json.loads(messages[-1]['content']))
        return '{"claims": []}'


async def test_backlog_is_processed_before_watermark_advances(tmp_path):
    context = ContextStore(tmp_path / 'c.db')
    context.register_source('o', 's', 'fixture', {})
    for i in range(101):
        context.ingest(ContextRecord('o', 's', str(i), 'document', 'record', str(i), '2026-01-01T00:00:00Z', {}))
    model = CaptureModel()
    schedule = ReflectionSchedule(tmp_path / 'r.db')
    personal = PersonalModelStore(tmp_path / 'p.db')
    for _ in range(3):
        await reflect_due_once(context, personal, model, schedule, timedelta(0))
    assert len(model.seen) == 101
    assert set(model.seen) == {str(i) for i in range(101)}


async def test_late_old_event_is_not_skipped_by_recent_sort(tmp_path):
    context = ContextStore(tmp_path / 'c.db')
    context.register_source('o', 's', 'fixture', {})
    for i in range(50):
        context.ingest(ContextRecord('o', 's', str(i), 'document', 'record', str(i), '2026-10-01T00:00:00Z', {}))
    model = CaptureModel()
    schedule = ReflectionSchedule(tmp_path / 'r.db')
    personal = PersonalModelStore(tmp_path / 'p.db')
    await reflect_due_once(context, personal, model, schedule, timedelta(0))
    context.ingest(ContextRecord('o', 's', 'late', 'document', 'record', 'old event', '2025-01-01T00:00:00Z', {}))
    await reflect_due_once(context, personal, model, schedule, timedelta(0))
    assert model.seen.count('late') == 1
