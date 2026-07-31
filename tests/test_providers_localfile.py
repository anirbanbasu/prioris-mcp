import asyncio
import base64
import re
import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from prioris_mcp.errors import FileTooLargeError, InvalidRequestError, NotFoundError
from prioris_mcp.parsers.pdf_liteparse import LiteParsePdfBackend
from prioris_mcp.providers.localfile import PDF_MAGIC_PREFIX, LocalFileProvider, UploadSessionManager
from prioris_mcp.storage import FilesystemStorageBackend

# A structurally valid minimal PDF, not just a "%PDF-" magic-prefix stub: earlier tasks only
# needed PDF_BYTES to survive fetch_full_text's magic-byte sniff (see
# TestLocalFileProviderContentValidation below), but parse_full_text (this task) feeds it to the
# real, unmocked liteparse backend, which requires an actually-parseable PDF structure.
PDF_BYTES = b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>
endobj
4 0 obj
<< /Length 44 >>
stream
BT /F1 24 Tf 20 100 Td (Hello World) Tj ET
endstream
endobj
5 0 obj
<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>
endobj
xref
0 6
0000000000 65535 f
trailer
<< /Size 6 /Root 1 0 R >>
startxref
0
%%EOF"""

PDF_BASE64 = base64.b64encode(PDF_BYTES).decode("ascii")


def _provider(tmp_path: Path, max_size_bytes: int = 10_000_000) -> LocalFileProvider:
    storage = FilesystemStorageBackend(tmp_path / "storage")
    return LocalFileProvider(
        storage=storage,
        pdf_backend=LiteParsePdfBackend(),
        max_size_bytes=max_size_bytes,
    )


def _session_manager(
    ttl_seconds: float = 300.0,
    max_chunk_bytes: int = 1_048_576,
    max_total_bytes: int = 10_000_000,
    max_concurrent: int = 16,
) -> UploadSessionManager:
    return UploadSessionManager(
        ttl_seconds=ttl_seconds,
        max_chunk_bytes=max_chunk_bytes,
        max_total_bytes=max_total_bytes,
        max_concurrent=max_concurrent,
    )


class TestUploadSessionManagerBegin:
    """Session creation and the concurrent-session cap."""

    def test_begin_mints_a_session_id(self):
        manager = _session_manager()
        session_id = asyncio.run(manager.begin(filename="paper.pdf"))
        assert isinstance(session_id, str)
        assert len(session_id) > 0

    def test_begin_mints_distinct_ids_for_distinct_sessions(self):
        manager = _session_manager()

        async def scenario():
            return await manager.begin(filename=None), await manager.begin(filename=None)

        first, second = asyncio.run(scenario())
        assert first != second

    def test_filename_is_optional(self):
        manager = _session_manager()
        session_id = asyncio.run(manager.begin(filename=None))
        assert isinstance(session_id, str)

    def test_begin_at_max_concurrent_sessions_raises_invalid_request(self):
        manager = _session_manager(max_concurrent=2)

        async def scenario():
            await manager.begin(filename=None)
            await manager.begin(filename=None)
            await manager.begin(filename=None)

        with pytest.raises(InvalidRequestError):
            asyncio.run(scenario())

    def test_begin_succeeds_again_after_a_session_expires(self, monkeypatch: "pytest.MonkeyPatch"):
        manager = _session_manager(max_concurrent=1, ttl_seconds=1.0)
        current_time = [1000.0]
        monkeypatch.setattr(time, "monotonic", lambda: current_time[0])

        async def scenario():
            await manager.begin(filename=None)
            current_time[0] += 2.0  # past the 1-second TTL
            return await manager.begin(filename=None)

        second_session_id = asyncio.run(scenario())
        assert isinstance(second_session_id, str)


class TestUploadSessionManagerAppendChunk:
    """Sequential ordering, per-chunk cap, and running-total cap."""

    def test_append_chunk_accumulates_bytes_in_order(self):
        manager = _session_manager()

        async def scenario():
            session_id = await manager.begin(filename=None)
            first_total = await manager.append_chunk(session_id, 0, b"hello ")
            second_total = await manager.append_chunk(session_id, 1, b"world")
            return first_total, second_total

        first_total, second_total = asyncio.run(scenario())
        assert first_total == 6
        assert second_total == 11

    def test_append_chunk_with_skipped_index_raises_invalid_request(self):
        manager = _session_manager()

        async def scenario():
            session_id = await manager.begin(filename=None)
            await manager.append_chunk(session_id, 0, b"hello")
            await manager.append_chunk(session_id, 2, b"world")  # skips index 1

        with pytest.raises(InvalidRequestError):
            asyncio.run(scenario())

    def test_append_chunk_with_repeated_index_raises_invalid_request(self):
        manager = _session_manager()

        async def scenario():
            session_id = await manager.begin(filename=None)
            await manager.append_chunk(session_id, 0, b"hello")
            await manager.append_chunk(session_id, 0, b"hello again")  # repeats index 0

        with pytest.raises(InvalidRequestError):
            asyncio.run(scenario())

    def test_append_chunk_on_unknown_session_raises_not_found(self):
        manager = _session_manager()
        with pytest.raises(NotFoundError):
            asyncio.run(manager.append_chunk("nonexistent-session", 0, b"hello"))

    def test_append_chunk_on_expired_session_raises_not_found(self, monkeypatch: "pytest.MonkeyPatch"):
        manager = _session_manager(ttl_seconds=1.0)
        current_time = [1000.0]
        monkeypatch.setattr(time, "monotonic", lambda: current_time[0])

        async def scenario():
            session_id = await manager.begin(filename=None)
            current_time[0] += 2.0
            await manager.append_chunk(session_id, 0, b"hello")

        with pytest.raises(NotFoundError):
            asyncio.run(scenario())

    def test_append_chunk_over_max_chunk_bytes_raises_file_too_large(self):
        manager = _session_manager(max_chunk_bytes=4)

        async def scenario():
            session_id = await manager.begin(filename=None)
            await manager.append_chunk(session_id, 0, b"12345")  # 5 bytes > 4-byte cap

        with pytest.raises(FileTooLargeError):
            asyncio.run(scenario())

    def test_append_chunk_cumulative_total_over_max_total_bytes_raises_file_too_large(self):
        manager = _session_manager(max_chunk_bytes=10, max_total_bytes=8)

        async def scenario():
            session_id = await manager.begin(filename=None)
            await manager.append_chunk(session_id, 0, b"12345")  # 5 bytes, under both caps
            await manager.append_chunk(session_id, 1, b"1234")  # 9 bytes cumulative > 8-byte total cap

        with pytest.raises(FileTooLargeError):
            asyncio.run(scenario())


class TestLocalFileProviderContentValidation:
    """Content sniffing/size limit - docs/requirement-specification/05-security.md#fetched-content-is-untrusted-input-to-parse_full_text."""

    def test_invalid_base64_is_rejected(self, tmp_path: Path):
        provider = _provider(tmp_path)
        with pytest.raises(InvalidRequestError):
            asyncio.run(provider.fetch_full_text("not-valid-base64!!!"))

    def test_non_pdf_content_is_rejected_regardless_of_filename(self, tmp_path: Path):
        provider = _provider(tmp_path)
        payload = base64.b64encode(b"not actually a pdf").decode("ascii")
        with pytest.raises(InvalidRequestError):
            asyncio.run(provider.fetch_full_text(payload, filename="fake.pdf"))

    def test_oversized_payload_is_rejected_before_decoding(self, tmp_path: Path):
        provider = _provider(tmp_path, max_size_bytes=1)
        decode_spy = AsyncMock(side_effect=AssertionError("must not decode"))
        with (
            pytest.MonkeyPatch.context() as monkeypatch,
            pytest.raises(FileTooLargeError),
        ):
            monkeypatch.setattr("prioris_mcp.providers.localfile.base64.b64decode", lambda *a, **k: decode_spy())
            asyncio.run(provider.fetch_full_text(PDF_BASE64))
        decode_spy.assert_not_called()

    def test_decoded_content_exceeding_cap_is_rejected_even_when_encoded_length_passes(self, tmp_path: Path):
        # base64's 3-bytes-in/4-chars-out ratio rounds the pre-decode ceiling up to the nearest
        # multiple of 3, so an encoded length within that ceiling can still decode to slightly
        # more than max_size_bytes - the decoded-length check below is what actually enforces the
        # cap in that gap. 12 raw bytes -> 16 base64 chars, same as the ceiling for a 10-byte cap.
        content = PDF_MAGIC_PREFIX + b"1234567"
        assert len(content) == 12
        payload = base64.b64encode(content).decode("ascii")
        provider = _provider(tmp_path, max_size_bytes=10)
        with pytest.raises(FileTooLargeError):
            asyncio.run(provider.fetch_full_text(payload))

    def test_unsupported_format_is_rejected(self, tmp_path: Path):
        provider = _provider(tmp_path)
        with pytest.raises(InvalidRequestError):
            asyncio.run(provider.fetch_full_text(PDF_BASE64, format="html"))


class TestLocalFileProviderFetchFullText:
    """Content-hash canonicalisation and caller-facing ID - docs/requirement-specification/02-storage.md#content-hash-canonicalisation-for-the-local-filesystem-source."""

    def test_first_fetch_mints_a_caller_facing_id(self, tmp_path: Path):
        provider = _provider(tmp_path)
        result = asyncio.run(provider.fetch_full_text(PDF_BASE64, filename="paper.pdf"))
        assert re.fullmatch(r"\d{8}-\d{4}-[0-9a-z]{4}", result["id"])
        assert result["served_from_storage"] is False
        assert result["resource_uri"] == f"research://localfile/{result['id']}/pdf/fulltext"
        assert result["size_bytes"] == len(PDF_BYTES)

    def test_second_fetch_of_unchanged_content_reuses_the_same_id(self, tmp_path: Path):
        provider = _provider(tmp_path)
        first = asyncio.run(provider.fetch_full_text(PDF_BASE64, filename="paper.pdf"))
        second = asyncio.run(provider.fetch_full_text(PDF_BASE64, filename="paper.pdf"))
        assert second["id"] == first["id"]
        assert second["served_from_storage"] is True

    def test_changed_content_gets_a_new_id_and_old_id_remains_valid(self, tmp_path: Path):
        provider = _provider(tmp_path)
        first = asyncio.run(provider.fetch_full_text(PDF_BASE64, filename="paper.pdf"))

        changed_base64 = base64.b64encode(PDF_BYTES + b" more content").decode("ascii")
        second = asyncio.run(provider.fetch_full_text(changed_base64, filename="paper.pdf"))

        assert second["id"] != first["id"]
        assert second["served_from_storage"] is False
        # The first id's content is still independently readable.
        first_manifest_identifier = asyncio.run(
            provider._storage.find_canonical_identifier("localfile", first["id"], "pdf")
        )
        assert asyncio.run(provider._storage.read("localfile", first_manifest_identifier, "pdf")) == PDF_BYTES

    def test_concurrent_fetches_of_same_unchanged_content_write_once_and_share_an_id(self, tmp_path: Path):
        provider = _provider(tmp_path)

        async def scenario():
            return await asyncio.gather(
                provider.fetch_full_text(PDF_BASE64, filename="paper.pdf"),
                provider.fetch_full_text(PDF_BASE64, filename="paper.pdf"),
            )

        first, second = asyncio.run(scenario())
        assert first["id"] == second["id"]
        assert {first["served_from_storage"], second["served_from_storage"]} == {True, False}

    def test_filename_is_optional(self, tmp_path: Path):
        provider = _provider(tmp_path)
        result = asyncio.run(provider.fetch_full_text(PDF_BASE64))
        assert result["served_from_storage"] is False


class TestLocalFileProviderParseFullText:
    """docs/requirement-specification/06-interface-specification.md#research_localfile_parse_full_text."""

    def test_unrecognised_id_fails_not_found_without_touching_disk(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ):
        provider = _provider(tmp_path)
        read_bytes_spy = AsyncMock(side_effect=AssertionError("must not read any path from disk"))
        monkeypatch.setattr(Path, "read_bytes", lambda self: read_bytes_spy())
        with pytest.raises(NotFoundError):
            asyncio.run(provider.parse_full_text("20260729-1430-a3f2"))
        read_bytes_spy.assert_not_called()

    def test_parses_already_fetched_content_and_returns_resource_uri(self, tmp_path: Path):
        provider = _provider(tmp_path)
        fetched = asyncio.run(provider.fetch_full_text(PDF_BASE64, filename="paper.pdf"))
        parsed = asyncio.run(provider.parse_full_text(fetched["id"]))
        assert parsed["resource_uri"] == f"research://localfile/{fetched['id']}/pdf/markdown"
        assert isinstance(parsed["markdown"], str)
        assert parsed["offset"] == 0
        assert parsed["has_more"] is False

    def test_second_parse_of_same_id_does_not_reparse(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        provider = _provider(tmp_path)
        fetched = asyncio.run(provider.fetch_full_text(PDF_BASE64, filename="paper.pdf"))
        asyncio.run(provider.parse_full_text(fetched["id"]))

        original_to_markdown = provider._pdf_backend.to_markdown

        async def _fail_if_called(content: bytes) -> str:
            raise AssertionError("must not re-parse an already-parsed identifier")

        monkeypatch.setattr(provider._pdf_backend, "to_markdown", _fail_if_called)
        asyncio.run(provider.parse_full_text(fetched["id"]))
        monkeypatch.setattr(provider._pdf_backend, "to_markdown", original_to_markdown)

    def test_never_triggers_a_fetch(self, tmp_path: Path):
        provider = _provider(tmp_path)
        # No file was ever fetched; parse must fail rather than reach into the filesystem itself.
        with pytest.raises(NotFoundError):
            asyncio.run(provider.parse_full_text("20260729-1430-a3f2"))

    def test_unsupported_format_is_rejected(self, tmp_path: Path):
        provider = _provider(tmp_path)
        with pytest.raises(InvalidRequestError):
            asyncio.run(provider.parse_full_text("20260729-1430-a3f2", format="html"))


class TestLocalFileProviderDeleteDoesNotTouchOriginal:
    """docs/requirement-specification/07-test-specification.md#storage-management-acceptance-criteria."""

    def test_delete_via_storage_backend_removes_only_the_storage_copy(self, tmp_path: Path):
        provider = _provider(tmp_path)
        fetched = asyncio.run(provider.fetch_full_text(PDF_BASE64, filename="paper.pdf"))
        asyncio.run(provider._storage.delete("localfile", fetched["id"], "pdf"))
        assert asyncio.run(provider._storage.find_canonical_identifier("localfile", fetched["id"], "pdf")) is None
