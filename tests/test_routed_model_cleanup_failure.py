"""A failed profile closer must not abandon other owned cached models."""
import httpx
import pytest

from meemee.config import Settings
from meemee.model_profiles import ModelCatalog, RoutedModel


async def test_routed_model_attempts_all_actual_cached_transports_after_error():
    catalog = ModelCatalog.from_settings(Settings(_env_file=None))
    routed = RoutedModel(catalog)
    released = []
    class Transport(httpx.AsyncBaseTransport):
        def __init__(self, label, fails=False):
            self.label, self.fails = label, fails
        async def aclose(self):
            released.append(self.label)
            if self.fails:
                raise RuntimeError('close failed')
    first = routed._model_for(catalog.profiles['local'])
    second = routed._model_for(catalog.profiles['inkling-vllm'])
    # Fail both iteration orders: LIFO finalization must attempt all as well.
    for label, model in [('first', first), ('second', second)]:
        await model.client._transport.aclose()
        model.client._transport = Transport(label, fails=True)
    try:
        with pytest.raises(RuntimeError, match='close failed'):
            await routed.aclose()
        assert sorted(released) == ['first', 'second']
        assert first.client.is_closed and second.client.is_closed
    finally:
        for model in [first, second]:
            await model.client.aclose()
