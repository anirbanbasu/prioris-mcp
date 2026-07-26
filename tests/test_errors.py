import asyncio

import pytest

from prioris_mcp.errors import (
    FormatUnavailableError,
    InvalidRequestError,
    NotFoundError,
    UnsupportedProviderError,
    call_returning_envelope,
    to_error_envelope,
)
from prioris_mcp.rate_limit import ProviderUnavailableError, RateLimitedError


class TestToErrorEnvelope:
    """`to_error_envelope` maps each known exception type to its documented error code."""

    @pytest.mark.parametrize(
        ("exc", "expected_code"),
        [
            (NotFoundError("x"), "not_found"),
            (FormatUnavailableError("x"), "format_unavailable"),
            (UnsupportedProviderError("x"), "unsupported_provider"),
            (InvalidRequestError("x"), "invalid_request"),
            (RateLimitedError("x"), "rate_limited"),
            (ProviderUnavailableError("x"), "provider_unavailable"),
        ],
    )
    def test_maps_known_exception_types(self, exc: Exception, expected_code: str):
        assert to_error_envelope(exc) == {"error": expected_code, "message": "x"}

    def test_raises_type_error_for_unmapped_exception(self):
        with pytest.raises(TypeError):
            to_error_envelope(ValueError("unmapped"))


class TestCallReturningEnvelope:
    """`call_returning_envelope` is the one place tool methods translate exceptions to envelopes."""

    def test_returns_coroutine_result_on_success(self):
        async def scenario():
            async def op():
                return {"ok": True}

            return await call_returning_envelope(op())

        assert asyncio.run(scenario()) == {"ok": True}

    def test_returns_envelope_on_mapped_exception(self):
        async def scenario():
            async def op():
                raise NotFoundError("missing")

            return await call_returning_envelope(op())

        assert asyncio.run(scenario()) == {"error": "not_found", "message": "missing"}

    def test_propagates_unmapped_exception(self):
        async def scenario():
            async def op():
                raise ValueError("bug")

            return await call_returning_envelope(op())

        with pytest.raises(ValueError):
            asyncio.run(scenario())
