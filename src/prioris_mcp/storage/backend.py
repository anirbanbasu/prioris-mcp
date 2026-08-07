"""Interface for persisting fetched full text and parsed Markdown.

See docs/requirement-specification/02-storage.md for the design this implements. `identifier`
must already be canonical/version-pinned by the time it reaches this class - resolving "what's
current" is `resolve_identifier`'s job, not storage's.
"""

from abc import ABC, abstractmethod
from asyncio import Lock
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from prioris_mcp.storage.manifest import DocumentManifest


class KeyedAsyncLockManager:
    """Hands out one asyncio.Lock per key (unchanged from before the Task 1 split)."""

    def __init__(self) -> None:
        self._locks: dict[str, Lock] = {}
        self._waiters: dict[str, int] = {}
        self._registry_guard = Lock()

    @asynccontextmanager
    async def acquire(self, key: str) -> AsyncIterator[None]:
        """Acquire the lock for `key`, waiting if another caller currently holds it."""
        async with self._registry_guard:
            lock = self._locks.setdefault(key, Lock())
            self._waiters[key] = self._waiters.get(key, 0) + 1
        try:
            async with lock:
                yield
        finally:
            async with self._registry_guard:
                self._waiters[key] -= 1
                if self._waiters[key] == 0:
                    del self._locks[key]
                    del self._waiters[key]


class StorageBackend(ABC):
    """Persists fetched full text and parsed Markdown as artefacts of (provider, identifier, format)."""

    def __init__(self) -> None:
        self._in_flight = KeyedAsyncLockManager()

    @abstractmethod
    async def exists(self, provider: str, identifier: str, format: str, artefact: str = "document") -> bool:
        """Whether `artefact` ("document" or "markdown") for (provider, identifier, format) exists."""

    @abstractmethod
    async def write(
        self,
        provider: str,
        identifier: str,
        format: str,
        content: bytes,
        *,
        artefact: str = "document",
        original_identifier: str | None = None,
        public_identifier: str | None = None,
    ) -> str:
        """Persist `artefact` content for (provider, identifier, format); returns a location/reference."""

    @abstractmethod
    async def read(self, provider: str, identifier: str, format: str, artefact: str = "document") -> bytes:
        """Retrieve previously persisted `artefact` content.

        Raises:
            FileNotFoundError: if nothing has been persisted for this key/artefact.
        """

    @abstractmethod
    async def list(self, provider: str | None = None, format: str | None = None) -> list[dict]:
        """Enumerate persisted catalogue entries, optionally filtered by provider/format.

        Each entry: {"provider", "identifier", "format", "artefact", "fetched_at_or_parsed_at", "size_bytes"}.
        """

    @abstractmethod
    async def delete(self, provider: str, identifier: str, format: str, artefact: str) -> bool:
        """Remove a persisted artefact ("document", "markdown", or "all") matching (provider, identifier, format).

        Returns:
            True if a matching entry was found and removed, False otherwise.
        """

    @abstractmethod
    async def read_manifest(
        self, provider: str, identifier: str, format: str, artefact: str = "document"
    ) -> dict | None:
        """Return the raw catalogue entry for (provider, identifier, format, artefact), or None if absent."""

    @abstractmethod
    async def find_canonical_identifier(self, provider: str, public_identifier: str, format: str) -> str | None:
        """Return the storage-key identifier whose recorded public_identifier matches, or None."""

    @abstractmethod
    def manifest_for(self, provider: str, identifier: str) -> DocumentManifest:
        """Return the DocumentManifest handle for this document's leaf/chunk structure.

        Synchronous - constructs a handle only, no I/O until a DocumentManifest method is awaited.
        """

    async def get_or_create(
        self,
        provider: str,
        identifier: str,
        format: str,
        factory: Callable[[], Awaitable[bytes]],
        *,
        artefact: str = "document",
        original_identifier: str | None = None,
        public_identifier: str | None = None,
    ) -> tuple[bytes, bool]:
        """Return persisted `artefact` content, producing it via `factory` if absent.

        Only one `factory` call is ever in flight for a given (provider, identifier, format,
        artefact) key at a time.

        Returns:
            (content, served_from_storage).
        """
        key = f"{provider}:{identifier}:{format}:{artefact}"
        async with self._in_flight.acquire(key):
            if await self.exists(provider, identifier, format, artefact=artefact):
                return await self.read(provider, identifier, format, artefact=artefact), True
            content = await factory()
            await self.write(
                provider,
                identifier,
                format,
                content,
                artefact=artefact,
                original_identifier=original_identifier,
                public_identifier=public_identifier,
            )
            return content, False
