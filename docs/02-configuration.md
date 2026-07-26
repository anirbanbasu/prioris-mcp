---
icon: lucide/settings
---

# Configuration

PriorisMCP is configured entirely through environment variables.

| Variable | Description | Default | Allowed values |
|---|---|---|---|
| `PRIORIS_MCP_LOG_LEVEL` | [Python log level](https://docs.python.org/3/library/logging.html#logging-levels) for this server. | `INFO` | `NOTSET`, `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |
| `PRIORIS_MCP_TRANSPORT` | [FastMCP transport](https://gofastmcp.com/deployment/running-server#transport-protocols) for this MCP server. | `stdio` | `stdio`, `streamable-http`, `http` |
| `PRIORIS_MCP_RESPONSE_CACHE_TTL` | Cache time-to-live (TTL), in seconds, for prompt, resource, and tool responses. `0` disables caching. | `30` | integer, `0`–`86400` |
| `PRIORIS_MCP_HOST` | Host address for network transports. | `localhost` | — |
| `PRIORIS_MCP_PORT` | Port number for network transports. | `8000` | integer, `1024`–`49151` |
| `PRIORIS_MCP_ASGI_CORS_ALLOWED_ORIGINS` | [CORS allowed origins](https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/CORS) for HTTP-based transports. | `["*"]` | — |
| `PRIORIS_MCP_UNVERIFIED_HTTPS` | Disables HTTPS certificate verification for upstream HTTPS requests. Set to `True` only for development/testing when you intentionally need unverified HTTPS. | `False` | `True`, `False` |
