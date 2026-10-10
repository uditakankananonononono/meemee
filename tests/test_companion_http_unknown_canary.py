"""A lost HTTP response cannot establish that a companion message was unsent."""
import httpx
import pytest
from test_companion_worker import queue_checkin, setup

from meemee.companion.channels import ProviderChannel, WebhookChannel
from meemee.companion.worker import deliver_due_once


@pytest.mark.parametrize('kind', ['webhook', 'whatsapp'])
async def test_accepted_http_message_with_lost_response_is_not_retried(tmp_path, kind, monkeypatch):
    monkeypatch.setattr("meemee.companion.channels.validate_webhook_url", lambda url: url)
    store, engine, channels = setup(tmp_path)
    calls = []

    def lost_response(request):
        calls.append(request.content)
        raise httpx.ReadError('response lost after provider acceptance', request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(lost_response)) as client:
        channels[kind] = (WebhookChannel(client=client) if kind == 'webhook'
                          else ProviderChannel(kind, 'https://example.com', 'synthetic-token', client=client))
        queue_checkin(store, channel=kind, address='https://example.com/inbox' if kind == 'webhook' else 'synthetic-recipient')
        if kind == 'webhook':
            proof, nonce = store.create_destination_challenge('udita', 'https://example.com/inbox')
            store.consume_destination_challenge('udita', proof['id'], nonce)
        summary = await deliver_due_once(store, engine, channels)
        if kind == 'whatsapp':
            assert summary['status'] == 'cancelled' and calls == []
            return
        assert summary['status'] == 'failed'
        assert 'unknown' in store.list_checkins('udita')[0]['last_error']
        assert await deliver_due_once(store, engine, channels) == {'claimed': False}
    assert len(calls) == 1


@pytest.mark.parametrize('error', [httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout])
async def test_before_connection_failure_still_retries(tmp_path, monkeypatch, error):
    monkeypatch.setattr("meemee.companion.channels.validate_webhook_url", lambda url: url)
    store, engine, channels = setup(tmp_path)

    def fail(request):
        raise error('no connection established', request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as client:
        channels['webhook'] = WebhookChannel(client=client)
        queue_checkin(store, channel='webhook', address='https://example.com/inbox')
        # Synthetic server proof only; this is a transport-error regression.
        proof, nonce = store.create_destination_challenge('udita', 'https://example.com/inbox')
        store.consume_destination_challenge('udita', proof['id'], nonce)
        summary = await deliver_due_once(store, engine, channels)
        assert summary['status'] == 'queued'
