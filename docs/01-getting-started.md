---
icon: lucide/rocket
---

# Getting started

The directory where you clone this repository is referred to as the _working directory_ or _WD_ hereinafter.

## Install

Install [`uv`](https://docs.astral.sh/uv/getting-started/installation/) and [`just`](https://github.com/casey/just?tab=readme-ov-file#installation). To install the project with its minimal (runtime) dependencies in a virtual environment, run the following in the _WD_.

```bash
just install
```

To also install the dependencies required for developing and testing PriorisMCP, use the `install-all` target instead.

```bash
just install-all
```

## Standalone usage

PriorisMCP can be started standalone as an MCP server with the `stdio` transport by running the following in the _WD_.

```bash
uv run prioris-mcp
```

Alternatively, it can be started using the `streamable-http` or `http` transports by setting the `PRIORIS_MCP_TRANSPORT` environment variable — see [Configuration](02-configuration.md).

## Connect it to Claude

PriorisMCP exposes a `stdio`-transport entry point (`prioris-mcp`), which is what both Claude Desktop and Claude Code expect for a locally-run MCP server. There are two ways to launch it, depending on whether you are running from a published release or a local clone.

### Using `uvx` (published package, no clone required)

Once PriorisMCP is published to PyPI, [`uvx`](https://docs.astral.sh/uv/guides/tools/) can fetch and run it on demand without installing anything into a project environment first.

**Claude Code**

```bash
claude mcp add prioris-mcp -- uvx prioris-mcp
```

**Claude Desktop** — add the following to your `claude_desktop_config.json` (macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`; Windows: `%APPDATA%\Claude\claude_desktop_config.json`):

```json
{
    "mcpServers": {
        "prioris-mcp": {
            "command": "uvx",
            "args": ["prioris-mcp"]
        }
    }
}
```

### Using `uv` (local clone, e.g. for development)

If you are working from a local clone of this repository (see [Install](#install) above), point `uv run` at the _WD_ with `--directory` so it resolves regardless of Claude's own working directory.

**Claude Code**

```bash
claude mcp add prioris-mcp -- uv run --directory /absolute/path/to/prioris-mcp prioris-mcp
```

**Claude Desktop**

```json
{
    "mcpServers": {
        "prioris-mcp": {
            "command": "uv",
            "args": ["run", "--directory", "/absolute/path/to/prioris-mcp", "prioris-mcp"]
        }
    }
}
```

Instead of `uv run --directory ... prioris-mcp`, you may also point `command` directly at the virtual environment's script, e.g. `/absolute/path/to/prioris-mcp/.venv/bin/prioris-mcp`.

To pass configuration (see [Configuration](02-configuration.md)) to the server, add an `env` block alongside `command`/`args` in either config, e.g. `"env": {"PRIORIS_MCP_LOG_LEVEL": "DEBUG"}`, or pass `--env KEY=VALUE` flags to `claude mcp add`.

## Test with the MCP Inspector

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

You can, alternatively, launch the MCP Inspector by running `just launch-inspector`.

## Testing and coverage

To run the provided set of tests with a coverage report, execute the following in the _WD_.

```bash
just test-coverage
```

This will generate something like the following output.

```bash
Name    Stmts   Miss    Cover   Missing
---------------------------------------
TOTAL     98      0  100.00%
```

See the [Contributing guide](https://github.com/anirbanbasu/prioris-mcp/blob/master/CONTRIBUTING.md) for the full development workflow.
