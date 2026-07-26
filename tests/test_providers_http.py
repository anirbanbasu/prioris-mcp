import asyncio
from collections.abc import Callable

import httpx
import pytest

from prioris_mcp.providers.http import request
from prioris_mcp.rate_limit import ProviderUnavailableError, RateLimitedError


def _client_with_handler(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class TestRequest:
    """`providers.http.request` maps transport-level outcomes to rate-limit-queue errors."""

    def test_returns_response_on_200(self):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"ok": True})

        async def scenario():
            async with _client_with_handler(handler) as client:
                return await request(client, "GET", "https://example.test/thing")

        response = asyncio.run(scenario())
        assert response.status_code == 200
        assert response.json() == {"ok": True}

    def test_passes_through_404_without_raising(self):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(404)

        async def scenario():
            async with _client_with_handler(handler) as client:
                return await request(client, "GET", "https://example.test/thing")

        response = asyncio.run(scenario())
        assert response.status_code == 404

    def test_raises_rate_limited_on_429(self):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(429)

        async def scenario():
            async with _client_with_handler(handler) as client:
                await request(client, "GET", "https://example.test/thing")

        with pytest.raises(RateLimitedError):
            asyncio.run(scenario())

    def test_raises_provider_unavailable_on_5xx(self):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(503)

        async def scenario():
            async with _client_with_handler(handler) as client:
                await request(client, "GET", "https://example.test/thing")

        with pytest.raises(ProviderUnavailableError):
            asyncio.run(scenario())

    def test_raises_provider_unavailable_on_transport_error(self):
        def handler(req: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("boom", request=req)

        async def scenario():
            async with _client_with_handler(handler) as client:
                await request(client, "GET", "https://example.test/thing")

        with pytest.raises(ProviderUnavailableError):
            asyncio.run(scenario())
