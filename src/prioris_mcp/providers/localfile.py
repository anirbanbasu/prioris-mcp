"""Local filesystem research-publication source: fetch_full_text/parse_full_text only.

See docs/requirement-specification/01-architecture.md#local-filesystem-source.
"""

import base64
import binascii
import hashlib
import logging
import random
import string
from datetime import UTC, datetime
from urllib.parse import quote

from anyio import to_thread

from prioris_mcp.errors import FileTooLargeError, InvalidRequestError, NotFoundError
from prioris_mcp.pagination import paginate_text
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
    """Parses caller-sent PDF bytes; implements only fetch_full_text/parse_full_text.

    No canonical identifier exists to resolve (see
    docs/requirement-specification/01-architecture.md#local-filesystem-source) - `search`,
    `fetch_metadata`, `resolve_identifier`, and `list_top_n` all use
    `ResearchPublicationProvider`'s shared CapabilityNotSupportedError defaults, unmodified.
    """

    def __init__(
        self,
        storage: StorageBackend,
        pdf_backend: ParserBackend,
        max_size_bytes: int,
        default_inline_char_limit: int = 20000,
    ) -> None:
        self._storage = storage
        self._pdf_backend = pdf_backend
        self._max_size_bytes = max_size_bytes
        self._default_inline_char_limit = default_inline_char_limit
        # Guards the check-existing-hash-then-mint-then-write sequence in fetch_full_text so two
        # concurrent calls for the same content never both mint a caller-facing ID or both write -
        # see docs/requirement-specification/07-test-specification.md#cross-cutting-concurrency.
        # This lock is keyed per content hash, so it does NOT cover concurrent fetches of
        # *different* content: those can theoretically mint colliding caller-facing IDs (same
        # minute-resolution timestamp, same random suffix) since the retry loop only re-checks
        # against already-persisted manifests, not other in-flight mints. Negligible odds
        # (~1/36^4 per same-minute, different-content, truly-concurrent pair); an accepted risk.
        self._mint_locks = KeyedAsyncLockManager()

    # invalid-method-override: base.py's fetch_full_text(identifier, format) is generic; this
    # source's first argument is caller-sent base64 content, not an opaque identifier, and format
    # has exactly one valid value - see providers/arxiv.py's search() override for the same pattern.
    async def fetch_full_text(  # ty: ignore[invalid-method-override]
        self, content_base64: str, filename: str | None = None, format: str = "pdf"
    ) -> dict:
        """See docs/requirement-specification/06-interface-specification.md#research_localfile_fetch_full_text."""
        if format != "pdf":
            raise InvalidRequestError(f"Unsupported format for local filesystem source: {format}")

        # Reject a grossly oversized payload by its base64-encoded length alone, before decoding:
        # base64 has fixed, standard overhead (encoded length = 4 * ceil(n / 3) for n raw bytes),
        # so this bounds the decode to roughly the configured cap - but base64's 3-byte/4-char
        # rounding means a payload that passes this check can still decode to up to 2 bytes over
        # the cap, which the post-decode check below catches exactly -
        # see docs/requirement-specification/05-security.md#fetched-content-is-untrusted-input-to-parse_full_text.
        max_b64_length = 4 * -(-self._max_size_bytes // 3)
        if len(content_base64) > max_b64_length:
            raise FileTooLargeError(
                f"content_base64 is too long to decode within PRIORIS_MCP_LOCAL_FILE_MAX_SIZE_BYTES "
                f"({self._max_size_bytes} bytes)"
            )
        try:
            content = await to_thread.run_sync(base64.b64decode, content_base64, None, True)
        except binascii.Error as exc:
            raise InvalidRequestError(f"content_base64 is not valid base64: {exc}") from exc

        if len(content) > self._max_size_bytes:
            raise FileTooLargeError(
                f"Decoded content is {len(content)} bytes, exceeding PRIORIS_MCP_LOCAL_FILE_MAX_SIZE_BYTES "
                f"({self._max_size_bytes} bytes)"
            )
        if not content.startswith(PDF_MAGIC_PREFIX):
            raise InvalidRequestError("content_base64 does not decode to something that sniffs as a PDF")

        content_hash = await to_thread.run_sync(lambda: hashlib.sha256(content).hexdigest())

        async with self._mint_locks.acquire(content_hash):
            existing_manifest = await self._storage.read_manifest("localfile", content_hash, "pdf")
            if existing_manifest is not None:
                caller_facing_id = existing_manifest["public_identifier"]
                served_from_storage = True
            else:
                caller_facing_id = await self._mint_caller_facing_id()
                await self._storage.write(
                    "localfile",
                    content_hash,
                    "pdf",
                    content,
                    original_identifier=filename,
                    public_identifier=caller_facing_id,
                )
                served_from_storage = False

        return {
            "id": caller_facing_id,
            "location": f"localfile:{content_hash}:pdf",
            "format": "pdf",
            "size_bytes": len(content),
            "served_from_storage": served_from_storage,
            "resource_uri": f"research://localfile/{quote(caller_facing_id, safe='')}/pdf/fulltext",
        }

    async def _mint_caller_facing_id(self) -> str:
        """Mint a minute-resolution-timestamp + random-suffix ID, retrying on manifest collision.

        See docs/requirement-specification/02-storage.md#caller-facing-identifiers-for-sources-without-one -
        no shared counter is needed: the timestamp already scopes the collision space, and a
        4-character base-36 suffix (~1.68M values) keeps collision risk low even under a burst of
        concurrent fetches within the same minute.
        """
        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
        while True:
            suffix = "".join(random.choices(_SUFFIX_ALPHABET, k=4))
            candidate = f"{timestamp}-{suffix}"
            if await self._storage.find_canonical_identifier("localfile", candidate, "pdf") is None:
                return candidate

    # No format choice exists for this source (always "pdf") - see providers/europepmc.py's
    # parse_full_text for the same no-format-parameter shape (also unannotated: giving `format`
    # a default value here doesn't trigger ty's invalid-method-override check, unlike
    # fetch_full_text's first-positional-parameter rename above).
    async def parse_full_text(
        self, identifier: str, format: str = "pdf", offset: int = 0, limit: int | None = None
    ) -> dict:
        """See docs/requirement-specification/06-interface-specification.md#research_localfile_parse_full_text.

        Never re-reads the original path and never triggers fetch_full_text - `identifier` is
        looked up purely via the storage manifest (docs/requirement-specification/01-architecture.md#parse_full_text).
        """
        if format != "pdf":
            raise InvalidRequestError(f"Unsupported format for local filesystem source: {format}")
        content_hash = await self._storage.find_canonical_identifier("localfile", identifier, "pdf")
        if content_hash is None:
            raise NotFoundError(f"Local file identifier not recognised: {identifier}")

        async def factory() -> bytes:
            source_content = await self._storage.read("localfile", content_hash, "pdf")
            markdown = await self._pdf_backend.to_markdown(source_content)
            return markdown.encode("utf-8")

        markdown_bytes, _ = await self._storage.get_or_create(
            "localfile", content_hash, "pdf-markdown", factory, public_identifier=identifier
        )
        page = paginate_text(
            markdown_bytes.decode("utf-8"), offset, limit if limit is not None else self._default_inline_char_limit
        )
        return {
            "markdown": page["content"],
            "offset": page["offset"],
            "limit": page["limit"],
            "total_length": page["total_length"],
            "has_more": page["has_more"],
            "resource_uri": f"research://localfile/{quote(identifier, safe='')}/pdf/markdown",
        }
