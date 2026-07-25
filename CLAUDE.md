# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

PriorisMCP: a MCP server to facilitate looking up prior art, built on FastMCP.

## Commands

All commands run via [`just`](https://github.com/casey/just) (`just -l` lists every target) and [`uv`](https://docs.astral.sh/uv/) manages the Python 3.13 environment — do not use `pip` directly.

```bash
just install              # sync minimal (runtime-only) dependencies
just install-all           # sync all dependency groups (dev, docs, test)
just format                 # ruff format + ruff check --fix
just type-check             # uv run ty check
just test-coverage          # pytest (via coverage) across tests/, then coverage report
just launch-inspector       # run the MCP Inspector against the server (needs nvm/node)
just start-documentation-server  # serve the Zensical docs site locally
just vulnerability-scan     # osv-scanner over the source tree
just install-pre-commit-hooks / just pre-commit-update   # manage hooks via `prek`, not `pre-commit`
```

Run a single test:

```bash
uv run pytest tests/test_server.py::TestMCPServer::test_tool_greet -v
```

(`greet` is a placeholder tool — see the note under Architecture below.)

`just test-coverage` enforces `fail_under = 100` (see `[tool.coverage.report]` in `pyproject.toml`); use `# pragma: no cover` / `# pragma: lax no cover` for genuinely unreachable branches rather than writing tests around them. Running a single test with plain `pytest`/`uv run pytest` skips the coverage gate, which is expected during iterative development.

Ruff (line length 120, Google-style docstrings, isort, pyupgrade, complexity ≤ 15) and `ty` are also run by pre-commit hooks (`.pre-commit-config.yaml`), installed with `prek`, not the `pre-commit` CLI.

## Architecture

### Feature registration (`mixin.py`)

Tools, resources, and prompts are registered declaratively, not via decorators on each method. `MCPMixin` (`src/prioris_mcp/mixin.py`) defines three `ClassVar[list[dict]]`: `tools`, `resources`, `prompts`. Each entry is a dict with a required `"fn"` key (the method name on the class) plus arbitrary keyword metadata forwarded straight to FastMCP's `mcp.tool()` / `mcp.resource()` / `mcp.prompt()` decorators (e.g. `tags`, `annotations`, `uri`). `register_features()` iterates these lists and wires each method into the FastMCP instance.

`PriorisMCP` (`src/prioris_mcp/server.py`) subclasses `MCPMixin`: it declares its `tools` list and implements the corresponding async methods. To add a new tool/resource/prompt, add the method to `PriorisMCP` and add its metadata entry to the relevant class list — no manual decorator wiring needed.

**Placeholder tool:** `greet` is scaffolding from the initial project setup, not a real feature. It (and its test in `tests/test_server.py`) will be removed once the software requirement specification is written and the actual prior-art tools are implemented — don't build on it or treat it as a pattern to preserve.

### App assembly (`server.py`)

`app()` builds the `FastMCP` instance, instantiates `PriorisMCP`, calls `register_features`, and attaches middleware in a specific, order-dependent chain:

1. `StripUnknownArgumentsMiddleware` — drops unrecognized tool-call arguments before they reach the tool.
2. `ResponseCachingMiddleware` (FastMCP built-in) — caches list/call/read responses per-feature-type, TTL from `PRIORIS_MCP_RESPONSE_CACHE_TTL`.
3. `ResponseMetadataMiddleware` — must stay last; it stamps package name/version and timing info onto `result.meta` for tool responses.

`main()` picks the transport from `PRIORIS_MCP_TRANSPORT`: `stdio` runs `mcp_app.run()` directly; `streamable-http`/`http` build an ASGI app via `http_app()` with CORS middleware and serve it with `uvicorn`.

### Configuration (`__init__.py`)

All environment variables are declared once as class attributes on `EnvVars` in `src/prioris_mcp/__init__.py`, using `environs`/`marshmallow` for typed parsing and validation (`OneOf`, `Range`) — this is also where module-level logging is configured (Rich handler). Add new environment-driven config here rather than reading `os.environ` elsewhere; consumers (e.g. `server.py`) import `EnvVars` and reference attributes directly.

### Tests (`tests/`)

Tests exercise the server in-process via FastMCP's `Client`/`FastMCP` pair (no network transport): instantiate `PriorisMCP`, call `register_features` on a bare `FastMCP()`, wrap it in a `Client`, and call tools through `async with mcp_client:` blocks. Follow this pattern for new tool tests rather than invoking methods directly, since it exercises the full middleware chain and MCP protocol serialization.

## Documentation

`docs/` uses [Zensical](https://zensical.org) (config in `zensical.toml`, served locally with `just start-documentation-server`, published at https://docs-prioris-mcp.anirbanbasu.com). It holds the project's user-facing documentation — `README.md` is intentionally minimal (badges, license, contributing) and points there instead of duplicating content.

`docs/requirement-specification` holds user- and Claude-readable Software Requirement Specifications in Zensical form, tracked in git. Keep it distinct from `docs/superpowers`, which holds Superpowers-plugin-generated intermediate artifacts and is git-ignored — never treat it as committed project documentation.
