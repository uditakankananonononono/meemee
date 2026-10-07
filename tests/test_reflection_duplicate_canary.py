"""Concurrent passes must not both call a model for the same evidence."""
import asyncio
from datetime import timedelta

from test_model_trace_and_reflection_schedule import seed

from meemee.personal_model import PersonalModelStore
from meemee.reflection_schedule import ReflectionSchedule, reflect_due_once


async def test_independent_pass_skips_owner_already_reflecting(tmp_path):
    context = seed(tmp_path)
    personal = PersonalModelStore(tmp_path / 'p.db')
    schedules = [ReflectionSchedule(tmp_path / 's.db'), ReflectionSchedule(tmp_path / 's.db')]
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    class Model:
        async def chat(self, messages, **kwargs):
            calls.append(messages)
            entered.set()
            await release.wait()
            return '{"claims": []}'

    async def run(i):
        return await reflect_due_once(context, personal, Model(), schedules[i], timedelta(0))

    first = asyncio.create_task(run(0))
    await entered.wait()
    second = asyncio.create_task(run(1))
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(first, second)
    assert len(calls) == 1


async def test_cancelled_reflection_releases_owner_guard(tmp_path):
    context = seed(tmp_path)
    personal = PersonalModelStore(tmp_path / 'p.db')
    schedule = ReflectionSchedule(tmp_path / 's.db')
    entered = asyncio.Event()

    class Blocked:
        async def chat(self, messages, **kwargs):
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(reflect_due_once(context, personal, Blocked(), schedule, timedelta(0)))
    await entered.wait()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    with ReflectionSchedule(tmp_path / 's.db').owner_guard('udita') as claimed:
        assert claimed
