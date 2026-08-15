---
icon: lucide/settings
---

# Configuration

PriorisMCP is configured entirely through environment variables.

| Variable | Description | Default | Allowed values |
|---|---|---|---|
| `PRIORIS_MCP_LOG_LEVEL` | [Python log level](https://docs.python.org/3/library/logging.html#logging-levels) for this server. | `INFO` | `NOTSET`, `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |
| `PRIORIS_MCP_TRANSPORT` | [FastMCP transport](https://gofastmcp.com/deployment/running-server#transport-protocols) for this MCP server. | `stdio` | `stdio`, `streamable-http`, `http` |
| `PRIORIS_MCP_RESPONSE_CACHE_TTL` | Cache time-to-live (TTL), in seconds, for prompt, resource, and tool responses. `0` disables caching. | `300` | integer, `0`–`86400` |
| `PRIORIS_MCP_HOST` | Host address for network transports. | `localhost` | — |
| `PRIORIS_MCP_PORT` | Port number for network transports. | `8000` | integer, `1024`–`49151` |
| `PRIORIS_MCP_ASGI_CORS_ALLOWED_ORIGINS` | [CORS allowed origins](https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/CORS) for HTTP-based transports. Override to `["*"]` for tools that require it (e.g. the MCP Inspector) — see [Security](requirement-specification/05-security.md#priorismcps-own-http-ingress-surface). | `["http://localhost", "http://127.0.0.1"]` | — |
| `PRIORIS_MCP_UNVERIFIED_HTTPS` | Disables HTTPS certificate verification for upstream HTTPS requests (passed straight through to the shared `httpx.AsyncClient`'s `verify=`). Set to `True` only for development/testing when you intentionally need unverified HTTPS — enabling it logs a `WARNING` at startup. Prefer `SSL_CERT_FILE`/`SSL_CERT_DIR` (see [Security](requirement-specification/05-security.md#egress-through-a-organisational-https-inspecting-proxy)) for a organisational HTTPS-inspecting proxy's self-signed root instead of disabling verification outright. | `False` | `True`, `False` |
| `PRIORIS_MCP_STORAGE_DIR` | Directory where fetched full text and parsed Markdown are persisted — see [Storage](requirement-specification/storage/01-document-storage.md). | `$XDG_DATA_HOME/prioris-mcp/downloads` (`~/.local/share/prioris-mcp/downloads` if `XDG_DATA_HOME` is unset) | — |
| `PRIORIS_MCP_NOTES_DIR` | Directory where user-authored notes' SQLite databases (`notes.sqlite`, `notes-search.sqlite3`) are persisted. A sibling of `PRIORIS_MCP_STORAGE_DIR`'s `downloads`, not nested inside it — notes are user-authored, not fetched content — see [Notes storage](requirement-specification/storage/02-notes-storage.md#storage-layout). | `$XDG_DATA_HOME/prioris-mcp/notes` (`~/.local/share/prioris-mcp/notes` if `XDG_DATA_HOME` is unset) | — |
| `PRIORIS_MCP_VECTOR_DIR` | Directory where vector search indices are persisted — see [Vector search](requirement-specification/search/02-vector-search.md#storage-layout-one-file-per-corpus-separate-from-every-other-backends-files). A sibling of `PRIORIS_MCP_STORAGE_DIR`, separate from all other storage roots. | `$XDG_DATA_HOME/prioris-mcp/vectors` (`~/.local/share/prioris-mcp/vectors` if `XDG_DATA_HOME` is unset) | — |
| `PRIORIS_MCP_EMBEDDING_MODEL` | Name of the embedding model used for vector search (fastEmbed model identifier). Changing this may or may not change the vector table's dimension, depending on the new model. If it does, the vec0 table is dropped and rebuilt automatically on the next write — no manual recovery step. Either way, rows already indexed under the previous model report `stale` via `index_status` until reindexed — see [ADR-00023](requirement-specification/ADR/00023-embedding-backend-fastembed.md). | `BAAI/bge-small-en-v1.5` | — |
| `PRIORIS_MCP_VECTOR_SEARCH_DEFAULT_LIMIT` | Default result limit for vector-based search in `research_search_fetched` and `research_notes_search`. | `10` | integer, `1`–`200` |
| `PRIORIS_MCP_OPENALEX_API_KEY` | [OpenAlex API key](https://help.openalex.org/api/), sent as the `api_key` query parameter on every `research_discovery`/`research://openalex/work-types` request — see [Discovery → Authentication](requirement-specification/02-discovery.md#authentication-an-api-key-not-mailto). Optional: requests still work unauthenticated, just without the higher rate limits an authenticated key grants. Replaces the deprecated `mailto` polite-pool parameter, ignored by OpenAlex as of February 2026. | unset | — |
| `PRIORIS_MCP_DISCOVERY_MAX_RESULTS` | Default `max_results` for `research_discovery` when the caller doesn't pass one. Capped at 50 — OpenAlex's `search.semantic` has a hard per-query ceiling at that count, not the ordinary `/works` per-page cap of 200. | `25` | integer, `1`–`50` |
| `PRIORIS_MCP_RATE_LIMIT_BACKOFF_BUDGET_SECONDS` | Total time a single tool call's rate-limit backoff may spend retrying a provider before giving up with `rate_limited` — see [Non-functional requirements](requirement-specification/04-non-functional-requirements.md#rate-limit-breaches-are-handled-inside-the-providers-queue-not-by-the-caller). | `60.0` | float, `0`–`3600` |
| `PRIORIS_MCP_JATS_MAX_CONCURRENT_TRANSFORMS` | Maximum number of JATS-to-HTML XSLT transforms allowed to actually execute concurrently, regardless of how many `parse_full_text` calls are in flight or have timed out — see [Security](requirement-specification/05-security.md#a-bounded-per-call-failure-is-not-sufficient-on-its-own). Always clamped to the host's CPU count even if set higher. | `min(4, os.cpu_count())` | integer, ≥ `1` |
| `PRIORIS_MCP_LOCAL_FILE_MAX_SIZE_BYTES` | Maximum decoded size of the `content_base64` payload `research_localfile_fetch_full_text` will accept — see [Security](requirement-specification/05-security.md#fetched-content-is-untrusted-input-to-parse_full_text). | `10485760` (10MB) | integer, ≥ `1` |
| `PRIORIS_MCP_LOCAL_FILE_UPLOAD_SESSION_TTL_SECONDS` | How long an idle chunked-upload session is kept before being swept as abandoned — see [Interface specification](requirement-specification/06-interface-specification.md#research_localfile_begin_upload). | `300` (5 minutes) | float, ≥ `1` |
| `PRIORIS_MCP_LOCAL_FILE_UPLOAD_MAX_CHUNK_BYTES` | Maximum size of a single chunk passed to `research_localfile_upload_chunk`. | `1048576` (1MB) | integer, ≥ `1` |
| `PRIORIS_MCP_LOCAL_FILE_UPLOAD_MAX_CONCURRENT_SESSIONS` | Maximum number of open chunked-upload sessions at once, bounding worst-case buffered memory. | `16` | integer, ≥ `1` |
| `PRIORIS_MCP_PDF_OCR_ENABLED` | Whether `LiteParsePdfBackend` runs OCR on scanned/image-only PDFs — see [Security](requirement-specification/05-security.md#ocr-language-data-is-a-network-dependency-of-parse_full_text). | `True` | `True`, `False` |
| `PRIORIS_MCP_PDF_OCR_TESSDATA_PATH` | Path to a pre-populated directory of Tesseract `.traineddata` files, for airgapped deployments — see [obtaining `.traineddata` files](#obtaining-traineddata-files-for-prioris_mcp_pdf_ocr_tessdata_path) below. Falls back to the standard `TESSDATA_PREFIX` if set and this is not. | unset (falls back to `TESSDATA_PREFIX`, then liteparse's own lazy-download behaviour) | — |
| `PRIORIS_MCP_PDF_OCR_SERVER_URL` | URL of an external OCR server, as an alternative to the bundled Tesseract engine. | unset | — |
| `PRIORIS_MCP_PDF_OCR_SERVER_HEADERS` | Extra HTTP headers (e.g. `Authorization`) sent with requests to `PRIORIS_MCP_PDF_OCR_SERVER_URL`, as a JSON object. | `{}` | JSON object of string keys/values, e.g. `{"Authorization": "Bearer <token>"}` |

## Obtaining `.traineddata` files for `PRIORIS_MCP_PDF_OCR_TESSDATA_PATH`

`PRIORIS_MCP_PDF_OCR_TESSDATA_PATH` (or `TESSDATA_PREFIX`) must point at a directory already containing the Tesseract language file(s) OCR will need, before network access is cut off — liteparse does not fetch or manage these files itself, it only reads whatever is at that path. To populate it:

1. Pick the language(s) actually expected in scanned PDFs (e.g. `eng` for English) — each corresponds to one `<lang>.traineddata` file.
2. Download the matching `.traineddata` file(s), while still on a network that can reach GitHub, from one of Tesseract's own model repositories:
    - [`tessdata_fast`](https://github.com/tesseract-ocr/tessdata_fast) — smaller, faster models; the best default for most deployments.
    - [`tessdata_best`](https://github.com/tesseract-ocr/tessdata_best) — larger, most accurate models, if OCR quality matters more than speed/size.
    - [`tessdata`](https://github.com/tesseract-ocr/tessdata) — the legacy-engine models Tesseract shipped before the LSTM-based models above.
3. Place the downloaded file(s) directly in the directory `PRIORIS_MCP_PDF_OCR_TESSDATA_PATH` points at (no subdirectories) and copy that directory into the airgapped environment.

See liteparse's own [OCR guide](https://developers.llamaindex.ai/liteparse/guides/ocr/) for how `tessdata_path` and the bundled engine interact, and [Security](requirement-specification/05-security.md#ocr-language-data-is-a-network-dependency-of-parse_full_text) for why this is necessary at all in an airgapped deployment.
