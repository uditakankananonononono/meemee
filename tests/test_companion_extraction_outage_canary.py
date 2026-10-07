"""Optional extraction outage must not invalidate an already-published reply."""
import asyncio

import pytest

from meemee.companion.engine import CompanionEngine
from meemee.companion.store import CompanionStore


class ExtractionOutage:
    def __init__(self, error):
        self.error = error
        self.calls = 0

    async def chat(self, messages, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return 'Saved reply'
        raise self.error


@pytest.mark.asyncio
@pytest.mark.parametrize('error', [RuntimeError('provider outage'), OSError('connection lost')])
async def test_extraction_outage_preserves_reply(tmp_path, error):
    store = CompanionStore(tmp_path / 'c.db')
    model = ExtractionOutage(error)
    reply = await CompanionEngine(model, store).reply('owner', 'hello')
    assert reply.reply == 'Saved reply'
    assert reply.facts_learned == 0
    assert len(store.history(reply.conversation_id)) == 2
    assert model.calls == 2


@pytest.mark.asyncio
async def test_extraction_cancellation_still_propagates(tmp_path):
    store = CompanionStore(tmp_path / 'c.db')
    with pytest.raises(asyncio.CancelledError):
        await CompanionEngine(ExtractionOutage(asyncio.CancelledError()), store).reply('owner', 'hello')
