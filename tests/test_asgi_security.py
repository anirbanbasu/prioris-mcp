import httpx

from prioris_mcp import EnvVars
from prioris_mcp.server import app


class TestHttpIngressDefaults:
    """docs/requirement-specification/05-security.md#priorismcps-own-http-ingress-surface."""

    def test_host_defaults_to_localhost(self):
        assert EnvVars.PRIORIS_MCP_HOST == "localhost"

    def test_cors_origins_do_not_default_to_wildcard(self):
        assert "*" not in EnvVars.PRIORIS_MCP_ASGI_CORS_ALLOWED_ORIGINS

    def test_asgi_app_rejects_arbitrary_origin_cors_preflight(self):
        from starlette.middleware import Middleware
        from starlette.middleware.cors import CORSMiddleware

        mcp_app = app()
        asgi_app = mcp_app.http_app(
            middleware=[
                Middleware(
                    CORSMiddleware,
                    allow_origins=EnvVars.PRIORIS_MCP_ASGI_CORS_ALLOWED_ORIGINS,
                    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
                )
            ],
            transport="http",
        )

        async def scenario():
            transport = httpx.ASGITransport(app=asgi_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                return await client.options(
                    "/mcp",
                    headers={
                        "Origin": "https://attacker.example",
                        "Access-Control-Request-Method": "POST",
                    },
                )

        import asyncio

        response = asyncio.run(scenario())
        assert "access-control-allow-origin" not in response.headers
