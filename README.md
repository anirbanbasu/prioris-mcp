[![Python 3.13+](https://img.shields.io/badge/python-3.13+-blue?logo=python&logoColor=3776ab&labelColor=e4e4e4)](https://www.python.org/downloads/release/python-3130/) [![pytest](https://github.com/anirbanbasu/prioris-mcp/actions/workflows/uv-pytest-coverage.yml/badge.svg)](https://github.com/anirbanbasu/prioris-mcp/actions/workflows/uv-pytest-coverage.yml) [![PyPI](https://img.shields.io/pypi/v/prioris-mcp?label=pypi%20package)](https://pypi.org/project/prioris-mcp/#history) ![GitHub commits since latest release](https://img.shields.io/github/commits-since/anirbanbasu/prioris-mcp/latest) [![CodeQL Advanced](https://github.com/anirbanbasu/prioris-mcp/actions/workflows/codeql.yml/badge.svg)](https://github.com/anirbanbasu/prioris-mcp/actions/workflows/codeql.yml) [![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/anirbanbasu/prioris-mcp/badge)](https://scorecard.dev/viewer/?uri=github.com/anirbanbasu/prioris-mcp) [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

# Prioris MCP

A MCP server to facilitate looking up prior art. The following features are available on this MCP server.

## Tools

1. **`greet`**
  - Greets the caller with a quintessential Hello World message.
  - Input(s)
    - `name`: _`string`_ (_optional_): The name to greet. Default value is none.
  - Output(s)
    - `TextContent` with a UTC time-stamped greeting.

# Installation

The directory where you clone this repository will be referred to as the _working directory_ or _WD_ hereinafter.

Install [`uv`](https://docs.astral.sh/uv/getting-started/installation/). Install [`just`](https://github.com/casey/just?tab=readme-ov-file#installation). To install the project with its minimal dependencies in a virtual environment, run the following in the _WD_. To install all non-essential dependencies (_which are required for developing and testing_), replace the `install` taget with the `install-all` target in the following command.

```bash
just install
```

# Environment variables

The following environment variables can be configured.

 - `PRIORIS_MCP_LOG_LEVEL`: Sets the [Python log level](https://docs.python.org/3/library/logging.html#logging-levels) for this server. Default is `INFO`. Allowed values are `NOTSET`, `DEBUG`, `INFO`, `WARNING`, `ERROR`, and `CRITICAL`.
 - `PRIORIS_MCP_TRANSPORT`: Sets the [FastMCP transport](https://gofastmcp.com/deployment/running-server#transport-protocols) for this MCP server. Default is `stdio`. Allowed values are `stdio`, `streamable-http`, and `http`.
 - `PRIORIS_MCP_RESPONSE_CACHE_TTL`: Sets the cache time-to-live (TTL), in seconds, for prompt, resource, and tool responses. Default is `30`. Valid values are integers from `0` to `86400` (inclusive); `0` disables caching.
 - `PRIORIS_MCP_HOST`: Sets the host address for network transports. Default is `localhost`.
 - `PRIORIS_MCP_PORT`: Sets the port number for network transports. Default is `8000`.
 - `PRIORIS_MCP_ASGI_CORS_ALLOWED_ORIGINS`: Sets the [CORS allowed origins](https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/CORS) for HTTP-based transports. Default is `["*"]`.
 - `PRIORIS_MCP_UNVERIFIED_HTTPS`: Controls whether HTTPS certificate verification for upstream HTTPS requests is disabled. Default is `False` (verification enabled). Set to `True` only for development/testing when you intentionally need unverified HTTPS.

# Standalone usage
PriorisMCP can be started standalone as a MCP server with `stdio` transport by running the following. Alternatively, it can be started using `streamable-http` or `sse` transports by specifying the transport type using the `MCP_SERVER_TRANSPORT` environment variable.

```bash
uv run prioris-mcp
```

# Test with the MCP Inspector

The [MCP Inspector](https://github.com/modelcontextprotocol/inspector) is an _official_ Model Context Protocol tool that can be used by developers to test and debug MCP servers. This is the most comprehensive way to explore the MCP server.

To use it, you must have Node.js installed. The best way to install and manage `node` as well as packages such as the MCP Inspector is to use the [Node Version Manager (or, `nvm`)](https://github.com/nvm-sh/nvm). Once you have `nvm` installed, you can install and use the latest Long Term Release version of `node` by executing the following.

```bash
nvm install --lts
nvm use --lts
```

Following that, run the MCP Inspector and PriorisMCP by executing the following in the _WD_.

```bash
npx @modelcontextprotocol/inspector uv run prioris-mcp
```

This will create a local URL at port 6274 with an authentication token, which you can copy and browse to on your browser. Once on the MCP Inspector UI, press _Connect_ to connect to the MCP server. Thereafter, you can explore the tools available on the server.

You can, alternatively, launch the MCP inspector by running `just launch-inspector`.

# Use it with Claude Desktop, Visual Studio, and so on

The server entry to run with `stdio` transport that you can use with systems such as Claude Desktop, Visual Studio Code, and so on is as follows.

```json
{
    "command": "uv",
    "args": [
        "run",
        "prioris-mcp"
    ]
}
```

Instead of having `prioris-mcp` as the last item in the list of `args`, you may need to specify the full path to the script, e.g., _WD_`/.venv/bin/prioris-mcp`.


# Testing and coverage

To run the provided set of tests using `pytest`, execute the following in _WD_. To get a report on coverage while invoking the tests, run the following in _WD_.

```bash
just test-coverage
```

This will generate something like the following output.

```bash
Name    Stmts   Miss    Cover   Missing
---------------------------------------
TOTAL     98      0  100.00%
```

# Contributing

See the [Contributing guide](CONTRIBUTING.md).

# License

[MIT](https://choosealicense.com/licenses/mit/).
