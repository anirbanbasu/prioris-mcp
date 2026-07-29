"""Local filesystem research-publication source: fetch_full_text/parse_full_text only.

See docs/requirement-specification/01-architecture.md#local-filesystem-source.
"""

import logging
import string
from pathlib import Path

from anyio import to_thread

from prioris_mcp.errors import FileTooLargeError, InvalidRequestError, NotFoundError
from prioris_mcp.parsers.base import ParserBackend
from prioris_mcp.providers.base import ResearchPublicationProvider
from prioris_mcp.storage import KeyedAsyncLockManager, StorageBackend

logger = logging.getLogger(__name__)

# A ".pdf" extension is a caller's claim, not a guarantee - see
# docs/requirement-specification/05-security.md#fetched-content-is-untrusted-input-to-parse_full_text.
PDF_MAGIC_PREFIX = b"%PDF-"

# Base-36: digits + lowercase letters, matching the caller-facing ID format documented in
# docs/requirement-specification/06-interface-specification.md#local-filesystem.
_SUFFIX_ALPHABET = string.digits + string.ascii_lowercase


class LocalFileProvider(ResearchPublicationProvider):
    """Reads/parses a caller-supplied local PDF; implements only fetch_full_text/parse_full_text.

    No canonical identifier exists to resolve (see
    docs/requirement-specification/01-architecture.md#local-filesystem-source) - `search`,
    `fetch_metadata`, `resolve_identifier`, and `list_top_n` all use
    `ResearchPublicationProvider`'s shared CapabilityNotSupportedError defaults, unmodified.
    """

    def __init__(
        self,
        storage: StorageBackend,
        pdf_backend: ParserBackend,
        root_dir: Path,
        max_size_bytes: int,
        default_inline_char_limit: int = 20000,
    ) -> None:
        self._storage = storage
        self._pdf_backend = pdf_backend
        self._root_dir = root_dir.resolve()
        self._max_size_bytes = max_size_bytes
        self._default_inline_char_limit = default_inline_char_limit
        # Guards the check-existing-hash-then-mint-then-write sequence in fetch_full_text so two
        # concurrent calls for the same content never both mint a caller-facing ID or both write -
        # see docs/requirement-specification/07-test-specification.md#cross-cutting-concurrency.
        self._mint_locks = KeyedAsyncLockManager()

    def _resolve_within_root(self, path: str) -> Path:
        """Resolve `path` against the configured root and verify it stays within it.

        Containment is checked *after* resolving symlinks (`Path.resolve()` follows them), not
        before, so a symlink inside the root pointing outside it is caught - see
        docs/requirement-specification/05-security.md#local-filesystem-access-is-confined-to-an-operator-configured-root.
        An absolute `path` is rejected outright: `Path(root) / Path(absolute)` in pathlib
        discards `root` entirely and evaluates to the absolute path, which would otherwise
        silently bypass containment.
        """
        if Path(path).is_absolute():
            raise InvalidRequestError(f"path must be relative to the configured root, got absolute path: {path}")
        candidate = (self._root_dir / path).resolve()
        if candidate != self._root_dir and self._root_dir not in candidate.parents:
            raise InvalidRequestError(f"path escapes the configured root: {path}")
        return candidate

    # invalid-method-override: base.py's fetch_full_text(identifier, format) is generic; this
    # source's first argument is a filesystem path, not an opaque identifier, and format has
    # exactly one valid value - see providers/arxiv.py's search() override for the same pattern.
    async def fetch_full_text(self, path: str, format: str = "pdf") -> dict:  # ty: ignore[invalid-method-override]
        """See docs/requirement-specification/06-interface-specification.md#research_localfile_fetch_full_text."""
        if format != "pdf":
            raise InvalidRequestError(f"Unsupported format for local filesystem source: {format}")
        resolved_path = self._resolve_within_root(path)
        if not await to_thread.run_sync(resolved_path.is_file):
            raise NotFoundError(f"No file at path: {path}")
        size_bytes = await to_thread.run_sync(lambda: resolved_path.stat().st_size)
        if size_bytes > self._max_size_bytes:
            raise FileTooLargeError(
                f"{path} is {size_bytes} bytes, exceeding PRIORIS_MCP_LOCAL_FILE_MAX_SIZE_BYTES "
                f"({self._max_size_bytes} bytes)"
            )
        content = await to_thread.run_sync(resolved_path.read_bytes)
        if not content.startswith(PDF_MAGIC_PREFIX):
            raise InvalidRequestError(f"File does not sniff as a PDF: {path}")
        raise NotImplementedError  # replaced in the next step

    # invalid-method-override: see fetch_full_text above for why this source's signature diverges
    # from base.py's generic one.
    async def parse_full_text(  # ty: ignore[invalid-method-override]
        self, path: str, format: str = "pdf", offset: int = 0, limit: int | None = None
    ) -> dict:
        """See docs/requirement-specification/06-interface-specification.md#research_localfile_parse_full_text.

        Not yet implemented - Task 6's job. `fetch_full_text` (this task, Task 4/5) must land
        first since parsing operates on content `fetch_full_text` has already persisted.
        """
        raise NotImplementedError
