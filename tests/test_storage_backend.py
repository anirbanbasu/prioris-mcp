import asyncio

from prioris_mcp.storage.backend import KeyedAsyncLockManager, StorageBackend
from prioris_mcp.storage.manifest import DocumentManifest


class _InMemoryStorageBackend(StorageBackend):
    """Minimal concrete backend for exercising StorageBackend.get_or_create in isolation.

    Isolated from any filesystem behaviour (that's tested separately against
    FilesystemStorageBackend). `list`/`delete`/`read_manifest`/`find_canonical_identifier` are
    not exercised by these tests, so they're implemented minimally rather than fully.
    """

    def __init__(self, tmp_path) -> None:
        super().__init__()
        self._content: dict[tuple, bytes] = {}
        self._manifests: dict[tuple, dict] = {}
        self._tmp_path = tmp_path

    async def exists(self, provider: str, identifier: str, format: str, artefact: str = "document") -> bool:
        return (provider, identifier, format, artefact) in self._content

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
        key = (provider, identifier, format, artefact)
        self._content[key] = content
        self._manifests[key] = {
            "provider": provider,
            "canonical_identifier": identifier,
            "format": format,
            "artefact": artefact,
            "original_identifier": original_identifier,
            "public_identifier": public_identifier,
        }
        return str(key)

    async def read(self, provider: str, identifier: str, format: str, artefact: str = "document") -> bytes:
        return self._content[(provider, identifier, format, artefact)]

    async def list(self, provider: str | None = None, format: str | None = None) -> list[dict]:
        return [
            m
            for m in self._manifests.values()
            if (provider is None or m["provider"] == provider) and (format is None or m["format"] == format)
        ]

    async def delete(self, provider: str, identifier: str, format: str, artefact: str) -> bool:
        key = (provider, identifier, format, artefact)
        found = key in self._content
        self._content.pop(key, None)
        self._manifests.pop(key, None)
        return found

    async def read_manifest(
        self, provider: str, identifier: str, format: str, artefact: str = "document"
    ) -> dict | None:
        return self._manifests.get((provider, identifier, format, artefact))

    async def find_canonical_identifier(self, provider: str, public_identifier: str, format: str) -> str | None:
        for m in self._manifests.values():
            if m["provider"] == provider and m["format"] == format and m.get("public_identifier") == public_identifier:
                return m["canonical_identifier"]
        return None

    def manifest_for(self, provider: str, identifier: str) -> DocumentManifest:
        return DocumentManifest(self._tmp_path / f"{provider}-{identifier}-manifest.sqlite")


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


class TestStorageBackendGetOrCreate:
    """Dedicated test class for StorageBackend.get_or_create."""

    def test_calls_factory_once_when_absent(self, tmp_path):
        async def scenario():
            backend = _InMemoryStorageBackend(tmp_path)
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

    def test_serves_persisted_content_without_calling_factory(self, tmp_path):
        async def scenario():
            backend = _InMemoryStorageBackend(tmp_path)
            await backend.write("arxiv", "2601.05525v2", "pdf", b"already there")

            async def factory() -> bytes:
                raise AssertionError("factory must not be called when content already exists")

            return await backend.get_or_create("arxiv", "2601.05525v2", "pdf", factory)

        content, served_from_storage = asyncio.run(scenario())
        assert content == b"already there"
        assert served_from_storage is True

    def test_deduplicates_concurrent_calls_for_the_same_key(self, tmp_path):
        async def scenario():
            backend = _InMemoryStorageBackend(tmp_path)
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

    def test_does_not_deduplicate_different_keys(self, tmp_path):
        async def scenario():
            backend = _InMemoryStorageBackend(tmp_path)
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


class TestGetOrCreateArtefactParam:
    """Test that get_or_create correctly threads the artefact parameter."""

    def test_get_or_create_defaults_to_document_artefact(self, tmp_path):
        async def scenario():
            backend = _InMemoryStorageBackend(tmp_path)
            calls = []

            async def factory():
                calls.append(1)
                return b"raw bytes"

            content, served = await backend.get_or_create("arxiv", "2106.09685v2", "pdf", factory)
            assert content == b"raw bytes"
            assert served is False
            assert await backend.exists("arxiv", "2106.09685v2", "pdf", artefact="document") is True
            assert await backend.exists("arxiv", "2106.09685v2", "pdf", artefact="markdown") is False

        asyncio.run(scenario())

    def test_get_or_create_with_explicit_markdown_artefact(self, tmp_path):
        async def scenario():
            backend = _InMemoryStorageBackend(tmp_path)

            async def factory():
                return b"# Markdown"

            content, _served = await backend.get_or_create("arxiv", "2106.09685v2", "pdf", factory, artefact="markdown")
            assert content == b"# Markdown"
            assert await backend.exists("arxiv", "2106.09685v2", "pdf", artefact="markdown") is True
            assert await backend.exists("arxiv", "2106.09685v2", "pdf", artefact="document") is False

        asyncio.run(scenario())

    def test_document_and_markdown_artefacts_dedup_independently(self, tmp_path):
        async def scenario():
            backend = _InMemoryStorageBackend(tmp_path)
            doc_calls, md_calls = [], []

            async def doc_factory():
                doc_calls.append(1)
                return b"raw"

            async def md_factory():
                md_calls.append(1)
                return b"md"

            await backend.get_or_create("arxiv", "X", "pdf", doc_factory, artefact="document")
            await backend.get_or_create("arxiv", "X", "pdf", doc_factory, artefact="document")
            await backend.get_or_create("arxiv", "X", "pdf", md_factory, artefact="markdown")
            assert len(doc_calls) == 1
            assert len(md_calls) == 1

        asyncio.run(scenario())


class TestManifestFor:
    """Test that manifest_for returns a DocumentManifest handle."""

    def test_manifest_for_returns_a_document_manifest(self, tmp_path):
        backend = _InMemoryStorageBackend(tmp_path)
        manifest = backend.manifest_for("arxiv", "2106.09685v2")
        assert isinstance(manifest, DocumentManifest)
