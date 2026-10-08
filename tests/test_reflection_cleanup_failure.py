"""Persistence cleanup must still run when model shutdown fails."""

from types import SimpleNamespace

import pytest

from meemee import model_profiles, persistence, reflection_schedule


async def test_reflection_worker_closes_persistence_after_model_close_failure(monkeypatch):
    closed = []
    class Model:
        async def aclose(self):
            raise RuntimeError('model close failed')
    stores = SimpleNamespace(context=object(), personal_model=object(), reflection_schedule=object(),
                             audit=object(), close=lambda: closed.append('persistence'))
    monkeypatch.setattr(persistence, 'persistence_from_settings', lambda settings: stores)
    monkeypatch.setattr(model_profiles, 'build_role_model', lambda settings, role: Model())
    async def stopped(*args, **kwargs):
        raise RuntimeError('worker stopped')
    monkeypatch.setattr(reflection_schedule, 'reflect_due_once', stopped)
    settings = SimpleNamespace(reflection_interval_minutes=1)
    with pytest.raises(RuntimeError, match='model close failed'):
        await reflection_schedule.reflection_forever(settings)
    assert closed == ['persistence']


async def test_reflection_worker_closes_persistence_after_model_build_failure(monkeypatch):
    closed = []
    stores = SimpleNamespace(context=object(), personal_model=object(), reflection_schedule=object(),
                             audit=object(), close=lambda: closed.append('persistence'))
    monkeypatch.setattr(persistence, 'persistence_from_settings', lambda settings: stores)
    def broken_model(settings, role):
        raise RuntimeError('model build failed')
    monkeypatch.setattr(model_profiles, 'build_role_model', broken_model)
    with pytest.raises(RuntimeError, match='model build failed'):
        await reflection_schedule.reflection_forever(SimpleNamespace(reflection_interval_minutes=1))
    assert closed == ['persistence']
