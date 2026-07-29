"""Content-addressed persistence for fetched full text and parsed Markdown.

See docs/requirement-specification/02-storage.md for the design this implements.
"""

import hashlib
import json
import logging
from abc import ABC, abstractmethod
from asyncio import Lock
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from anyio import to_thread

from prioris_mcp import EnvVars

logger = logging.getLogger(__name__)


class KeyedAsyncLockManager:
    """Hands out one asyncio.Lock per key.

    Operations for the same key serialise while operations for different keys run
    independently. Locks are created lazily on first use and discarded once nothing is
    waiting on them, so the registry doesn't grow unboundedly over the life of the process.
    """

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
    """Persists fetched full text and parsed Markdown, keyed by provider + canonical identifier + format.

    `identifier` must already be canonical/version-pinned by the time it reaches this class
    - resolving "what's current" is `resolve_identifier`'s job (see
    docs/requirement-specification/02-storage.md#identifier-canonicalisation), not storage's.

    Concrete backends implement `exists`/`write`/`read`; `get_or_create` is shared by every
    backend and provides the in-flight de-duplication required by
    docs/requirement-specification/04-non-functional-requirements.md#storage-must-de-duplicate-in-flight-work-not-just-completed-work.
    """

    def __init__(self) -> None:
        self._in_flight = KeyedAsyncLockManager()

    @staticmethod
    def _storage_key(provider: str, identifier: str, format: str) -> str:
        """Derive a fixed-length, filesystem-safe storage key.

        Hashes a JSON-encoded list rather than a delimited string: a JSON list is
        unambiguously re-parseable, so no combination of characters across the three
        arguments can collide two different triples onto the same pre-hash string.
        """
        payload = json.dumps([provider, identifier, format])
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @abstractmethod
    async def exists(self, provider: str, identifier: str, format: str) -> bool:
        """Whether content for (provider, identifier, format) has already been persisted."""

    @abstractmethod
    async def write(
        self,
        provider: str,
        identifier: str,
        format: str,
        content: bytes,
        *,
        original_identifier: str | None = None,
        public_identifier: str | None = None,
    ) -> str:
        """Persist content for (provider, identifier, format); returns a location/reference.

        `public_identifier` overrides the externally-visible identifier `list`/`delete` report
        for this entry - defaults to `identifier` itself if unset. Used by sources whose
        caller-facing identifier differs from the storage-key identifier (currently only the
        local filesystem source - see
        docs/requirement-specification/02-storage.md#caller-facing-identifiers-for-sources-without-one).
        """

    @abstractmethod
    async def read(self, provider: str, identifier: str, format: str) -> bytes:
        """Retrieve previously persisted content for (provider, identifier, format).

        Raises:
            FileNotFoundError: if nothing has been persisted for this key.
        """

    @abstractmethod
    async def list(self, provider: str | None = None, format: str | None = None) -> list[dict]:
        """Enumerate persisted manifest entries, optionally filtered by provider/format.

        Each entry is `{"provider", "identifier", "format", "fetched_at", "size_bytes"}` -
        `identifier` is the externally-visible identifier (the entry's `public_identifier` if it
        was written with one, else its storage-key identifier).
        """

    @abstractmethod
    async def delete(self, provider: str, identifier: str, format: str) -> bool:
        """Remove a persisted entry (content + manifest) matching (provider, identifier, format).

        `identifier` is matched against the same externally-visible identifier `list` reports,
        not necessarily the storage-key identifier `exists`/`read`/`write` use - see `list` above.

        Returns:
            True if a matching entry was found and removed, False otherwise.
        """

    @abstractmethod
    async def read_manifest(self, provider: str, identifier: str, format: str) -> dict | None:
        """Return the raw persisted manifest for (provider, identifier, format), or None if absent.

        `identifier` here is the storage-key identifier (as `exists`/`write`/`read` use it), not
        necessarily the externally-visible one `list`/`delete` use - see `list` above. Lets a
        caller (currently only `LocalFileProvider`) check "has this exact content already been
        persisted" and recover its `public_identifier` without a full `list` scan.
        """

    @abstractmethod
    async def find_canonical_identifier(self, provider: str, public_identifier: str, format: str) -> str | None:
        """Return the storage-key identifier whose recorded `public_identifier` matches.

        Lets a source whose caller-facing identifier differs from its storage key (currently only
        the local filesystem source, whose caller-facing ID differs from its content-hash storage
        key - see
        docs/requirement-specification/02-storage.md#caller-facing-identifiers-for-sources-without-one)
        resolve a caller-supplied identifier back to the key `exists`/`write`/`read` use.
        Returns None if no entry has that public identifier for (provider, format).
        """

    async def get_or_create(
        self,
        provider: str,
        identifier: str,
        format: str,
        factory: Callable[[], Awaitable[bytes]],
        *,
        original_identifier: str | None = None,
        public_identifier: str | None = None,
    ) -> tuple[bytes, bool]:
        """Return persisted content for (provider, identifier, format), producing it via `factory` if absent.

        Only one `factory` call is ever in flight for a given key at a time: a second
        concurrent call for the same key waits for the first to finish and is served its
        result rather than starting a redundant `factory` call of its own.

        Returns:
            A `(content, served_from_storage)` pair - `served_from_storage` is `True` if the
            content was already persisted, `False` if `factory` was just called to produce it.
        """
        key = self._storage_key(provider, identifier, format)
        async with self._in_flight.acquire(key):
            if await self.exists(provider, identifier, format):
                return await self.read(provider, identifier, format), True
            content = await factory()
            await self.write(
                provider,
                identifier,
                format,
                content,
                original_identifier=original_identifier,
                public_identifier=public_identifier,
            )
            return content, False


class FilesystemStorageBackend(StorageBackend):
    """Persists content under a directory on local disk.

    See docs/requirement-specification/02-storage.md#v1-local-filesystem-backend.
    """

    def __init__(self, base_dir: Path | None = None) -> None:
        super().__init__()
        self._base_dir = base_dir if base_dir is not None else EnvVars.PRIORIS_MCP_STORAGE_DIR
        self._base_dir.mkdir(parents=True, exist_ok=True)

    def _data_path(self, provider: str, identifier: str, format: str) -> Path:
        return self._base_dir / f"{self._storage_key(provider, identifier, format)}.data"

    def _manifest_path(self, provider: str, identifier: str, format: str) -> Path:
        return self._base_dir / f"{self._storage_key(provider, identifier, format)}.json"

    async def exists(self, provider: str, identifier: str, format: str) -> bool:
        return await to_thread.run_sync(self._data_path(provider, identifier, format).exists)

    async def write(
        self,
        provider: str,
        identifier: str,
        format: str,
        content: bytes,
        *,
        original_identifier: str | None = None,
        public_identifier: str | None = None,
    ) -> str:
        data_path = self._data_path(provider, identifier, format)
        manifest_path = self._manifest_path(provider, identifier, format)
        manifest = {
            "provider": provider,
            "canonical_identifier": identifier,
            "original_identifier": original_identifier,
            "public_identifier": public_identifier,
            "format": format,
            "size_bytes": len(content),
            "fetched_at": datetime.now(UTC).isoformat(),
        }
        await to_thread.run_sync(self._atomic_write, data_path, content)
        await to_thread.run_sync(self._atomic_write, manifest_path, json.dumps(manifest, indent=2).encode("utf-8"))
        return str(data_path)

    async def read(self, provider: str, identifier: str, format: str) -> bytes:
        return await to_thread.run_sync(self._data_path(provider, identifier, format).read_bytes)

    def _manifest_paths(self) -> list[Path]:
        return sorted(self._base_dir.glob("*.json"))

    async def list(self, provider: str | None = None, format: str | None = None) -> list[dict]:
        def _list() -> list[dict]:
            entries = []
            for manifest_path in self._manifest_paths():
                manifest = json.loads(manifest_path.read_text())
                if provider is not None and manifest["provider"] != provider:
                    continue
                if format is not None and manifest["format"] != format:
                    continue
                entries.append(
                    {
                        "provider": manifest["provider"],
                        "identifier": manifest.get("public_identifier") or manifest["canonical_identifier"],
                        "format": manifest["format"],
                        "fetched_at": manifest["fetched_at"],
                        "size_bytes": manifest["size_bytes"],
                    }
                )
            return entries

        return await to_thread.run_sync(_list)

    async def delete(self, provider: str, identifier: str, format: str) -> bool:
        def _delete() -> bool:
            for manifest_path in self._manifest_paths():
                manifest = json.loads(manifest_path.read_text())
                if manifest["provider"] != provider or manifest["format"] != format:
                    continue
                public_id = manifest.get("public_identifier") or manifest["canonical_identifier"]
                if public_id != identifier:
                    continue
                manifest_path.with_suffix(".data").unlink(missing_ok=True)
                manifest_path.unlink(missing_ok=True)
                return True
            return False

        return await to_thread.run_sync(_delete)

    async def read_manifest(self, provider: str, identifier: str, format: str) -> dict | None:
        def _read() -> dict | None:
            manifest_path = self._manifest_path(provider, identifier, format)
            if not manifest_path.exists():
                return None
            return json.loads(manifest_path.read_text())

        return await to_thread.run_sync(_read)

    async def find_canonical_identifier(self, provider: str, public_identifier: str, format: str) -> str | None:
        def _find() -> str | None:
            for manifest_path in self._manifest_paths():
                manifest = json.loads(manifest_path.read_text())
                if manifest["provider"] != provider or manifest["format"] != format:
                    continue
                if manifest.get("public_identifier") == public_identifier:
                    return manifest["canonical_identifier"]
            return None

        return await to_thread.run_sync(_find)

    @staticmethod
    def _atomic_write(path: Path, content: bytes) -> None:
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_bytes(content)
        tmp_path.replace(path)
