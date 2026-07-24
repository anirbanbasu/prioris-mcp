import logging

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
        default=["*"],
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


logging.basicConfig(
    level=EnvVars.PRIORIS_MCP_LOG_LEVEL,
    format="%(message)s",
    datefmt="[%X]",
    handlers=[RichHandler(rich_tracebacks=False, markup=True, show_path=False, show_time=False)],
)
