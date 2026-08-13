import asyncio

from prioris_mcp.vector.backend import DocumentVectorSearchBackend, NoteVectorSearchBackend


class _StubDocumentBackend(DocumentVectorSearchBackend):
    def __init__(self):
        self.indexed: list[tuple] = []

    async def index_entries(self, provider, identifier, format, entries):
        self.indexed.append((provider, identifier, format, entries))

    async def remove_document(self, provider, identifier, format):
        pass

    async def search(self, query_embedding, *, provider=None, identifier=None, format=None, limit=10):
        return []

    async def status(self, provider, identifier, format):
        return "not_built"


class _StubNoteBackend(NoteVectorSearchBackend):
    async def index_note(self, note_id, text):
        pass

    async def remove_note(self, note_id):
        pass

    async def search(self, query_embedding, *, note_ids=None, limit=10):
        return []

    async def status(self, note_id):
        return "not_built"


class TestDocumentVectorSearchBackendContract:
    """Verify DocumentVectorSearchBackend ABC contract is usable via concrete stub."""

    def test_concrete_subclass_is_instantiable_and_callable(self):
        backend = _StubDocumentBackend()
        asyncio.run(backend.index_entries("arxiv", "2106.09685v2", "pdf", [{"chunk_id": "a", "text": "x"}]))
        assert backend.indexed[0][0] == "arxiv"
        assert asyncio.run(backend.status("arxiv", "2106.09685v2", "pdf")) == "not_built"


class TestNoteVectorSearchBackendContract:
    """Verify NoteVectorSearchBackend ABC contract is usable via concrete stub."""

    def test_concrete_subclass_is_instantiable_and_callable(self):
        backend = _StubNoteBackend()
        assert asyncio.run(backend.status("note-1")) == "not_built"
        assert asyncio.run(backend.search([0.1, 0.2])) == []
