"""Shared outbound HTTP request helper, mapping transport failures to rate-limit-queue errors.

See docs/requirement-specification/04-non-functional-requirements.md - every provider's
outbound call routes through this helper so a 429 becomes `RateLimitedError` (retried inside
`ProviderRequestQueue`) and a timeout/connection error/5xx becomes `ProviderUnavailableError`
(never retried) in exactly one place, not once per provider.
"""

import httpx

from prioris_mcp.rate_limit import ProviderUnavailableError, RateLimitedError


async def request(client: httpx.AsyncClient, method: str, url: str, **kwargs: object) -> httpx.Response:
    """Issue one HTTP request, translating transport-level failures as described above.

    A 4xx status other than 429 (e.g. 404) is returned as-is, not raised - its meaning (e.g.
    `not_found` vs `format_unavailable`) is specific to the calling tool, not generic to HTTP.
    """
    try:
        response = await client.request(method, url, **kwargs)
    except httpx.TransportError as exc:
        raise ProviderUnavailableError(f"{method} {url} failed: {exc}") from exc
    if response.status_code == 429:
        raise RateLimitedError(f"{method} {url} returned 429")
    if response.status_code >= 500:
        raise ProviderUnavailableError(f"{method} {url} returned {response.status_code}")
    return response
