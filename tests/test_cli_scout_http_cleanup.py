"""Scout CLI releases its constructed HTTP transport on every exit path."""
import asyncio

import httpx
import pytest

from meemee import cli


@pytest.mark.parametrize('failure', ['none', 'request', 'validation', 'output'])
def test_cli_scout_releases_owned_transport(monkeypatch, failure):
    released, made = [], []
    class Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            if failure == 'request':
                raise httpx.ConnectError('request failed')
            return httpx.Response(200, json={'items': []})
        async def aclose(self):
            released.append('transport')
    original = cli.GitHubRepoSearch
    def create(*args):
        value = original(*args)
        value.client._transport = Transport()
        made.append(value)
        return value
    def echo(*args):
        if failure == 'output':
            raise ValueError('output failed')
    monkeypatch.setattr(cli, 'GitHubRepoSearch', create)
    monkeypatch.setattr(cli.typer, 'echo', echo)
    try:
        if failure == 'none':
            cli.scout('test')
        else:
            with pytest.raises(httpx.ConnectError if failure == 'request' else ValueError):
                cli.scout('x' if failure == 'validation' else 'test')
        assert released == ['transport']
        assert made[0].client.is_closed
    finally:
        for tool in made:
            asyncio.run(tool.aclose())
