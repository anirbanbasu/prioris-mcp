import asyncio

from prioris_mcp.storage import KeyedAsyncLockManager, StorageBackend


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
