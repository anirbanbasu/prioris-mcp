import logging
import os
from pathlib import Path

from environs import Env
from marshmallow.validate import OneOf, Range
from rich.logging import RichHandler

PACKAGE_NAME = "prioris-mcp"
env = Env()
env.read_env()


class EnvVars:
    """Environment variables for Prioris MCP configuration."""

    PRIORIS_MCP_ASGI_CORS_ALLOWED_ORIGINS = env.list(
        "PRIORIS_MCP_ASGI_CORS_ALLOWED_ORIGINS",
        # Default is restricted to localhost, not a wildcard: a wildcard origin lets any webpage's
        # JavaScript read this server's responses, not just tools actually served from localhost.
        # Override explicitly (e.g. to "*") for tools that need it, such as the MCP Inspector.
        default=["http://localhost", "http://127.0.0.1"],
    )

    PRIORIS_MCP_HOST = env.str("PRIORIS_MCP_HOST", default="localhost")
    PRIORIS_MCP_PORT = env.int(
        "PRIORIS_MCP_PORT", default=8000, validate=Range(min=1024, max=49151)
    )  # Valid port range is 1024-49151 for non-privileged ports

    PRIORIS_MCP_LOG_LEVEL = env.str(
        "PRIORIS_MCP_LOG_LEVEL",
        default="INFO",
        validate=OneOf(["NOTSET", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]),
    ).upper()

    PRIORIS_MCP_TRANSPORT: str = env.str(
        name="PRIORIS_MCP_TRANSPORT",
        default="stdio",
        validate=OneOf(["stdio", "streamable-http", "http"]),
    )

    PRIORIS_MCP_RESPONSE_CACHE_TTL: int = env.int(
        name="PRIORIS_MCP_RESPONSE_CACHE_TTL",
        default=30,  # in seconds
        validate=Range(min=0, max=86400),  # 0 seconds to 1 day where 0 means caching is disabled
    )

    PRIORIS_MCP_UNVERIFIED_HTTPS: bool = env.bool(
        name="PRIORIS_MCP_UNVERIFIED_HTTPS",
        default=False,  # By default, HTTPS requests are verified; override in development for testing
    )

    PRIORIS_MCP_HTTP_TIMEOUT_SECONDS: float = env.float(
        name="PRIORIS_MCP_HTTP_TIMEOUT_SECONDS",
        # httpx.AsyncClient()'s own default (5s on connect/read/write/pool) is too tight for
        # export.arxiv.org and Europe PMC, which can legitimately take longer than that under
        # load - too-short a timeout surfaces as a spurious `provider_unavailable`, not a real
        # outage. Applied uniformly to connect/read/write/pool via httpx.AsyncClient(timeout=...).
        default=30.0,
        validate=Range(min=0.1, max=300),
    )

    PRIORIS_MCP_MAX_INLINE_CHARS: int = env.int(
        name="PRIORIS_MCP_MAX_INLINE_CHARS",
        # Default limit for text (e.g. parsed Markdown) returned inline in a tool response - a
        # large parsed PDF/HTML can otherwise exceed an MCP client's own max-tokens-per-result
        # ceiling. Content past this limit stays reachable via the paired resource_uri, paginated
        # with the same offset/limit shape - see
        # docs/requirement-specification/04-non-functional-requirements.md.
        default=20000,
        validate=Range(min=1),
    )

    _default_data_home = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))

    PRIORIS_MCP_STORAGE_DIR = env.path(
        "PRIORIS_MCP_STORAGE_DIR",
        # Data, not configuration, hence XDG_DATA_HOME (not XDG_CONFIG_HOME) - see
        # docs/requirement-specification/02-storage.md.
        default=_default_data_home / "prioris-mcp" / "downloads",
    )

    PRIORIS_MCP_RATE_LIMIT_BACKOFF_BUDGET_SECONDS = env.float(
        "PRIORIS_MCP_RATE_LIMIT_BACKOFF_BUDGET_SECONDS",
        # Total time a single tool call's rate-limit backoff may spend retrying before giving up
        # with `rate_limited` - see docs/requirement-specification/04-non-functional-requirements.md.
        default=60.0,
        validate=Range(min=0, max=3600),
    )

    # Clamped to the host's CPU count even if the environment variable requests more: this bounds
    # concurrently-*executing* JATS transforms (not just concurrently-awaited ones) against
    # unbounded resource use from abandoned, still-running transforms - see
    # docs/requirement-specification/05-security.md#a-bounded-per-call-failure-is-not-sufficient-on-its-own.
    PRIORIS_MCP_JATS_MAX_CONCURRENT_TRANSFORMS = min(
        env.int(
            "PRIORIS_MCP_JATS_MAX_CONCURRENT_TRANSFORMS",
            default=min(4, os.cpu_count() or 4),
            validate=Range(min=1),
        ),
        os.cpu_count() or 4,
    )


logging.basicConfig(
    level=EnvVars.PRIORIS_MCP_LOG_LEVEL,
    format="%(message)s",
    datefmt="[%X]",
    handlers=[RichHandler(rich_tracebacks=False, markup=True, show_path=False, show_time=False)],
)
