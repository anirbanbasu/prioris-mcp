import asyncio
import json
from pathlib import Path

import pytest

from prioris_mcp import EnvVars
from prioris_mcp.storage import FilesystemStorageBackend, KeyedAsyncLockManager, StorageBackend


class _InMemoryStorageBackend(StorageBackend):
    """Minimal concrete backend for exercising StorageBackend.get_or_create in isolation.

    Isolated from any filesystem behaviour (that's tested separately against
    FilesystemStorageBackend).
    """

    def __init__(self) -> None:
        super().__init__()
        self._data: dict[str, bytes] = {}

    async def exists(self, provider: str, identifier: str, format: str) -> bool:
        return self._storage_key(provider, identifier, format) in self._data

    async def write(
        self, provider: str, identifier: str, format: str, content: bytes, *, original_identifier: str | None = None
    ) -> str:
        key = self._storage_key(provider, identifier, format)
        self._data[key] = content
        return key

    async def read(self, provider: str, identifier: str, format: str) -> bytes:
        return self._data[self._storage_key(provider, identifier, format)]


class TestKeyedAsyncLockManager:
    """Dedicated test class for KeyedAsyncLockManager."""

    def test_same_key_serialises(self):
        async def scenario():
            manager = KeyedAsyncLockManager()
            order: list[str] = []

            async def worker(name: str, hold_seconds: float):
                async with manager.acquire("shared-key"):
                    order.append(f"{name}-start")
                    await asyncio.sleep(hold_seconds)
                    order.append(f"{name}-end")

            await asyncio.gather(worker("first", 0.05), worker("second", 0.0))
            return order

        assert asyncio.run(scenario()) == ["first-start", "first-end", "second-start", "second-end"]

    def test_different_keys_do_not_block_each_other(self):
        async def scenario():
            manager = KeyedAsyncLockManager()
            started: list[str] = []

            async def worker(key: str):
                async with manager.acquire(key):
                    started.append(key)
                    await asyncio.sleep(0.05)

            await asyncio.gather(worker("a"), worker("b"))
            return started

        started = asyncio.run(scenario())
        assert set(started) == {"a", "b"}

    def test_lock_entry_is_discarded_once_unused(self):
        async def scenario():
            manager = KeyedAsyncLockManager()
            async with manager.acquire("k"):
                pass
            return manager._locks, manager._waiters

        locks, waiters = asyncio.run(scenario())
        assert locks == {}
        assert waiters == {}


class TestStorageKey:
    """Dedicated test class for StorageBackend._storage_key."""

    def test_stable_for_identical_arguments(self):
        assert StorageBackend._storage_key("arxiv", "2601.05525v2", "pdf") == StorageBackend._storage_key(
            "arxiv", "2601.05525v2", "pdf"
        )

    def test_distinguishes_each_argument(self):
        base = StorageBackend._storage_key("arxiv", "2601.05525v2", "pdf")
        assert base != StorageBackend._storage_key("arxiv", "2601.05525v2", "html")
        assert base != StorageBackend._storage_key("europepmc", "2601.05525v2", "pdf")
        assert base != StorageBackend._storage_key("arxiv", "2601.05525v3", "pdf")

    def test_no_ambiguous_concatenation_collision(self):
        # f"{provider}:{identifier}:{format}" would collide these two triples; the JSON-based
        # hash must not.
        assert StorageBackend._storage_key("a", "b:c", "d") != StorageBackend._storage_key("a:b", "c", "d")


class TestStorageBackendGetOrCreate:
    """Dedicated test class for StorageBackend.get_or_create."""

    def test_calls_factory_once_when_absent(self):
        async def scenario():
            backend = _InMemoryStorageBackend()
            calls = 0

            async def factory() -> bytes:
                nonlocal calls
                calls += 1
                return b"content"

            content, served_from_storage = await backend.get_or_create("arxiv", "2601.05525v2", "pdf", factory)
            return content, served_from_storage, calls

        content, served_from_storage, calls = asyncio.run(scenario())
        assert content == b"content"
        assert served_from_storage is False
        assert calls == 1

    def test_serves_persisted_content_without_calling_factory(self):
        async def scenario():
            backend = _InMemoryStorageBackend()
            await backend.write("arxiv", "2601.05525v2", "pdf", b"already there")

            async def factory() -> bytes:
                raise AssertionError("factory must not be called when content already exists")

            return await backend.get_or_create("arxiv", "2601.05525v2", "pdf", factory)

        content, served_from_storage = asyncio.run(scenario())
        assert content == b"already there"
        assert served_from_storage is True

    def test_deduplicates_concurrent_calls_for_the_same_key(self):
        async def scenario():
            backend = _InMemoryStorageBackend()
            calls = 0

            async def factory() -> bytes:
                nonlocal calls
                calls += 1
                await asyncio.sleep(0.05)
                return b"content"

            results = await asyncio.gather(
                backend.get_or_create("arxiv", "2601.05525v2", "pdf", factory),
                backend.get_or_create("arxiv", "2601.05525v2", "pdf", factory),
            )
            return results, calls

        results, calls = asyncio.run(scenario())
        assert calls == 1
        assert results[0] == (b"content", False)
        assert results[1] == (b"content", True)

    def test_does_not_deduplicate_different_keys(self):
        async def scenario():
            backend = _InMemoryStorageBackend()
            calls = 0

            async def factory() -> bytes:
                nonlocal calls
                calls += 1
                return b"content"

            await asyncio.gather(
                backend.get_or_create("arxiv", "2601.05525v2", "pdf", factory),
                backend.get_or_create("arxiv", "2601.05525v2", "html", factory),
            )
            return calls

        assert asyncio.run(scenario()) == 2


class TestFilesystemStorageBackend:
    """Dedicated test class for FilesystemStorageBackend."""

    def test_exists_is_false_before_write(self, tmp_path: Path):
        backend = FilesystemStorageBackend(tmp_path)
        assert asyncio.run(backend.exists("arxiv", "2601.05525v2", "pdf")) is False

    def test_write_then_read_round_trips_content(self, tmp_path: Path):
        backend = FilesystemStorageBackend(tmp_path)

        async def scenario():
            await backend.write("arxiv", "2601.05525v2", "pdf", b"%PDF-1.4 fake content")
            exists = await backend.exists("arxiv", "2601.05525v2", "pdf")
            content = await backend.read("arxiv", "2601.05525v2", "pdf")
            return exists, content

        exists, content = asyncio.run(scenario())
        assert exists is True
        assert content == b"%PDF-1.4 fake content"

    def test_write_persists_a_manifest_sidecar(self, tmp_path: Path):
        backend = FilesystemStorageBackend(tmp_path)
        asyncio.run(backend.write("arxiv", "2601.05525v2", "pdf", b"content", original_identifier="2601.05525"))
        key = FilesystemStorageBackend._storage_key("arxiv", "2601.05525v2", "pdf")
        manifest = json.loads((tmp_path / f"{key}.json").read_text())
        assert manifest == {
            "provider": "arxiv",
            "canonical_identifier": "2601.05525v2",
            "original_identifier": "2601.05525",
            "format": "pdf",
            "fetched_at": manifest["fetched_at"],  # asserted separately below
        }
        # fetched_at must be a real, parseable ISO-8601 UTC timestamp
        from datetime import datetime

        datetime.fromisoformat(manifest["fetched_at"])

    def test_write_without_original_identifier_stores_null(self, tmp_path: Path):
        backend = FilesystemStorageBackend(tmp_path)
        asyncio.run(backend.write("arxiv", "2601.05525v2", "pdf", b"content"))
        key = FilesystemStorageBackend._storage_key("arxiv", "2601.05525v2", "pdf")
        manifest = json.loads((tmp_path / f"{key}.json").read_text())
        assert manifest["original_identifier"] is None

    def test_read_missing_content_raises_file_not_found(self, tmp_path: Path):
        backend = FilesystemStorageBackend(tmp_path)
        with pytest.raises(FileNotFoundError):
            asyncio.run(backend.read("arxiv", "does-not-exist", "pdf"))

    def test_base_dir_is_created_if_missing(self, tmp_path: Path):
        target = tmp_path / "nested" / "downloads"
        FilesystemStorageBackend(target)
        assert target.is_dir()

    def test_no_leftover_temp_files_after_write(self, tmp_path: Path):
        backend = FilesystemStorageBackend(tmp_path)
        asyncio.run(backend.write("arxiv", "2601.05525v2", "pdf", b"content"))
        assert list(tmp_path.glob("*.tmp")) == []

    def test_defaults_base_dir_to_env_var(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        monkeypatch.setattr(EnvVars, "PRIORIS_MCP_STORAGE_DIR", tmp_path / "from-env")
        backend = FilesystemStorageBackend()
        assert backend._base_dir == tmp_path / "from-env"
        assert (tmp_path / "from-env").is_dir()

    def test_get_or_create_works_end_to_end_against_the_real_filesystem(self, tmp_path: Path):
        backend = FilesystemStorageBackend(tmp_path)

        async def factory() -> bytes:
            return b"downloaded bytes"

        first = asyncio.run(backend.get_or_create("arxiv", "2601.05525v2", "pdf", factory))
        second = asyncio.run(backend.get_or_create("arxiv", "2601.05525v2", "pdf", factory))
        assert first == (b"downloaded bytes", False)
        assert second == (b"downloaded bytes", True)
