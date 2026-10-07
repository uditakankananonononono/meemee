"""A redirect is not evidence of delivery to the configured destination."""
import httpx
import pytest

from meemee.companion.channels import ChannelError, ProviderChannel, WebhookChannel


@pytest.mark.parametrize('channel', ['webhook', 'provider'])
async def test_redirect_response_is_not_delivery(monkeypatch, channel):
    monkeypatch.setattr('meemee.companion.channels.validate_webhook_url', lambda url: url)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(302, headers={'Location':'https://other.invalid'}))) as client:
        adapter = WebhookChannel(client=client) if channel == 'webhook' else ProviderChannel('fixture','https://fixture.invalid','fixture-token',client=client)
        with pytest.raises(ChannelError, match='HTTP 302'):
            await adapter.send('https://fixture.invalid' if channel == 'webhook' else 'dest', 'fixture message')
