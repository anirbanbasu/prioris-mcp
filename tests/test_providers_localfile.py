import asyncio
import base64
import random
import re
import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from prioris_mcp.errors import FileTooLargeError, InvalidRequestError, NotFoundError
from prioris_mcp.models.common import ParsedFullText
from prioris_mcp.models.localfile import LocalFileFetchResult, LocalFileUploadChunkResult
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


def _provider(
    tmp_path: Path,
    max_size_bytes: int = 10_000_000,
    upload_ttl_seconds: float = 300.0,
    upload_max_chunk_bytes: int = 1_048_576,
    upload_max_concurrent: int = 16,
) -> LocalFileProvider:
    storage = FilesystemStorageBackend(tmp_path / "storage")
    return LocalFileProvider(
        storage=storage,
        pdf_backend=LiteParsePdfBackend(),
        max_size_bytes=max_size_bytes,
        upload_session_manager=UploadSessionManager(
            ttl_seconds=upload_ttl_seconds,
            max_chunk_bytes=upload_max_chunk_bytes,
            max_total_bytes=max_size_bytes,
            max_concurrent=upload_max_concurrent,
        ),
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

    def test_begin_retries_on_session_id_collision(self, monkeypatch: "pytest.MonkeyPatch"):
        manager = _session_manager()
        # First begin() draws "aaaaaaaaaaaa" and registers it. Second begin() draws the same
        # value again (a collision with the already-open session), forcing the `while` loop in
        # `begin` to redraw once more before landing on the distinct "bbbbbbbbbbbb".
        draws = iter(["aaaaaaaaaaaa", "aaaaaaaaaaaa", "bbbbbbbbbbbb"])
        monkeypatch.setattr(random, "choices", lambda population, k: list(next(draws)))

        async def scenario():
            first_id = await manager.begin(filename=None)
            second_id = await manager.begin(filename=None)
            return first_id, second_id

        first_id, second_id = asyncio.run(scenario())
        assert first_id == "aaaaaaaaaaaa"
        assert second_id == "bbbbbbbbbbbb"
        assert first_id != second_id


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


class TestUploadSessionManagerPopForFinalize:
    """Popping a session's buffered content for finalize_upload."""

    def test_pop_for_finalize_returns_concatenated_bytes_and_filename(self):
        manager = _session_manager()

        async def scenario():
            session_id = await manager.begin(filename="paper.pdf")
            await manager.append_chunk(session_id, 0, b"hello ")
            await manager.append_chunk(session_id, 1, b"world")
            return await manager.pop_for_finalize(session_id)

        content, filename = asyncio.run(scenario())
        assert content == b"hello world"
        assert filename == "paper.pdf"

    def test_pop_for_finalize_removes_the_session_on_success(self):
        manager = _session_manager()

        async def scenario():
            session_id = await manager.begin(filename=None)
            await manager.append_chunk(session_id, 0, b"hello")
            await manager.pop_for_finalize(session_id)
            # A second finalize on the same, now-removed, session must fail not_found.
            await manager.pop_for_finalize(session_id)

        with pytest.raises(NotFoundError):
            asyncio.run(scenario())

    def test_pop_for_finalize_with_zero_chunks_raises_invalid_request_and_removes_session(self):
        manager = _session_manager()

        async def begin_and_pop_empty():
            session_id = await manager.begin(filename=None)
            await manager.pop_for_finalize(session_id)
            return session_id

        try:
            asyncio.run(begin_and_pop_empty())
        except InvalidRequestError:
            pass
        else:
            pytest.fail("expected InvalidRequestError")

        # Re-derive session_id isn't possible after the exception path above discarded it inside
        # the coroutine, so instead assert removal via a fresh session with a captured id.
        async def begin_pop_empty_then_pop_again():
            sid = await manager.begin(filename=None)
            try:
                await manager.pop_for_finalize(sid)
            except InvalidRequestError:
                pass
            await manager.pop_for_finalize(sid)  # session must already be gone -> not_found

        with pytest.raises(NotFoundError):
            asyncio.run(begin_pop_empty_then_pop_again())

    def test_pop_for_finalize_on_unknown_session_raises_not_found(self):
        manager = _session_manager()
        with pytest.raises(NotFoundError):
            asyncio.run(manager.pop_for_finalize("nonexistent-session"))

    def test_pop_for_finalize_on_expired_session_raises_not_found(self, monkeypatch: "pytest.MonkeyPatch"):
        manager = _session_manager(ttl_seconds=1.0)
        current_time = [1000.0]
        monkeypatch.setattr(time, "monotonic", lambda: current_time[0])

        async def scenario():
            session_id = await manager.begin(filename=None)
            await manager.append_chunk(session_id, 0, b"hello")
            current_time[0] += 2.0
            await manager.pop_for_finalize(session_id)

        with pytest.raises(NotFoundError):
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
        assert isinstance(result, LocalFileFetchResult)
        assert re.fullmatch(r"\d{8}-\d{4}-[0-9a-z]{4}", result.id)
        assert result.served_from_storage is False
        assert result.resource_uri == f"research://localfile/{result.id}/pdf/fulltext"
        assert result.size_bytes == len(PDF_BYTES)

    def test_second_fetch_of_unchanged_content_reuses_the_same_id(self, tmp_path: Path):
        provider = _provider(tmp_path)
        first = asyncio.run(provider.fetch_full_text(PDF_BASE64, filename="paper.pdf"))
        second = asyncio.run(provider.fetch_full_text(PDF_BASE64, filename="paper.pdf"))
        assert second.id == first.id
        assert second.served_from_storage is True

    def test_changed_content_gets_a_new_id_and_old_id_remains_valid(self, tmp_path: Path):
        provider = _provider(tmp_path)
        first = asyncio.run(provider.fetch_full_text(PDF_BASE64, filename="paper.pdf"))

        changed_base64 = base64.b64encode(PDF_BYTES + b" more content").decode("ascii")
        second = asyncio.run(provider.fetch_full_text(changed_base64, filename="paper.pdf"))

        assert second.id != first.id
        assert second.served_from_storage is False
        # The first id's content is still independently readable.
        first_manifest_identifier = asyncio.run(
            provider._storage.find_canonical_identifier("localfile", first.id, "pdf")
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
        assert first.id == second.id
        assert {first.served_from_storage, second.served_from_storage} == {True, False}

    def test_filename_is_optional(self, tmp_path: Path):
        provider = _provider(tmp_path)
        result = asyncio.run(provider.fetch_full_text(PDF_BASE64))
        assert isinstance(result, LocalFileFetchResult)
        assert result.served_from_storage is False


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
        parsed = asyncio.run(provider.parse_full_text(fetched.id))
        assert isinstance(parsed, ParsedFullText)
        assert parsed.resource_uri == f"research://localfile/{fetched.id}/pdf/markdown"
        assert isinstance(parsed.markdown, str)
        assert parsed.offset == 0
        assert parsed.has_more is False

    def test_second_parse_of_same_id_does_not_reparse(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        provider = _provider(tmp_path)
        fetched = asyncio.run(provider.fetch_full_text(PDF_BASE64, filename="paper.pdf"))
        asyncio.run(provider.parse_full_text(fetched.id))

        original_to_markdown = provider._pdf_backend.to_markdown

        async def _fail_if_called(content: bytes) -> str:
            raise AssertionError("must not re-parse an already-parsed identifier")

        monkeypatch.setattr(provider._pdf_backend, "to_markdown", _fail_if_called)
        asyncio.run(provider.parse_full_text(fetched.id))
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
        asyncio.run(provider._storage.delete("localfile", fetched.id, "pdf"))
        assert asyncio.run(provider._storage.find_canonical_identifier("localfile", fetched.id, "pdf")) is None


def _chunks(content: bytes, chunk_size: int) -> list[bytes]:
    return [content[i : i + chunk_size] for i in range(0, len(content), chunk_size)]


class TestLocalFileProviderChunkedUpload:
    """docs/requirement-specification/06-interface-specification.md#local-filesystem."""

    def test_upload_chunk_returns_received_index_and_cumulative_bytes(self, tmp_path: Path):
        provider = _provider(tmp_path)

        async def scenario():
            session_id = await provider.begin_upload(filename=None)
            return await provider.upload_chunk(session_id, 0, base64.b64encode(PDF_BYTES[:10]).decode("ascii"))

        result = asyncio.run(scenario())
        assert isinstance(result, LocalFileUploadChunkResult)
        assert result.received_index == 0
        assert result.bytes_so_far == 10

    def test_happy_path_matches_fetch_full_text_result(self, tmp_path: Path):
        chunked_provider = _provider(tmp_path / "chunked")
        whole_provider = _provider(tmp_path / "whole")

        async def chunked_scenario():
            session_id = await chunked_provider.begin_upload(filename="paper.pdf")
            for index, chunk in enumerate(_chunks(PDF_BYTES, 20)):
                await chunked_provider.upload_chunk(session_id, index, base64.b64encode(chunk).decode("ascii"))
            return await chunked_provider.finalize_upload(session_id)

        chunked_result = asyncio.run(chunked_scenario())
        whole_result = asyncio.run(whole_provider.fetch_full_text(PDF_BASE64, filename="paper.pdf"))

        assert isinstance(chunked_result, LocalFileFetchResult)
        assert isinstance(whole_result, LocalFileFetchResult)
        assert chunked_result.format_ == whole_result.format_ == "pdf"
        assert chunked_result.size_bytes == whole_result.size_bytes == len(PDF_BYTES)
        assert chunked_result.served_from_storage is False
        assert re.fullmatch(r"\d{8}-\d{4}-[0-9a-z]{4}", chunked_result.id)

    def test_finalize_of_identical_content_already_fetched_dedupes(self, tmp_path: Path):
        provider = _provider(tmp_path)

        async def scenario():
            await provider.fetch_full_text(PDF_BASE64, filename="paper.pdf")
            session_id = await provider.begin_upload(filename="paper.pdf")
            for index, chunk in enumerate(_chunks(PDF_BYTES, 20)):
                await provider.upload_chunk(session_id, index, base64.b64encode(chunk).decode("ascii"))
            return await provider.finalize_upload(session_id)

        result = asyncio.run(scenario())
        assert isinstance(result, LocalFileFetchResult)
        assert result.served_from_storage is True

    def test_upload_chunk_with_skipped_index_raises_invalid_request(self, tmp_path: Path):
        provider = _provider(tmp_path)

        async def scenario():
            session_id = await provider.begin_upload(filename=None)
            await provider.upload_chunk(session_id, 0, base64.b64encode(PDF_BYTES[:10]).decode("ascii"))
            await provider.upload_chunk(session_id, 2, base64.b64encode(PDF_BYTES[10:20]).decode("ascii"))

        with pytest.raises(InvalidRequestError):
            asyncio.run(scenario())

    def test_upload_chunk_on_unknown_session_raises_not_found(self, tmp_path: Path):
        provider = _provider(tmp_path)
        with pytest.raises(NotFoundError):
            asyncio.run(provider.upload_chunk("nonexistent-session", 0, base64.b64encode(b"hello").decode("ascii")))

    def test_finalize_upload_on_unknown_session_raises_not_found(self, tmp_path: Path):
        provider = _provider(tmp_path)
        with pytest.raises(NotFoundError):
            asyncio.run(provider.finalize_upload("nonexistent-session"))

    def test_upload_chunk_on_expired_session_raises_not_found(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"):
        provider = _provider(tmp_path, upload_ttl_seconds=1.0)
        current_time = [1000.0]
        monkeypatch.setattr(time, "monotonic", lambda: current_time[0])

        async def scenario():
            session_id = await provider.begin_upload(filename=None)
            current_time[0] += 2.0
            await provider.upload_chunk(session_id, 0, base64.b64encode(b"hello").decode("ascii"))

        with pytest.raises(NotFoundError):
            asyncio.run(scenario())

    def test_upload_chunk_with_invalid_base64_raises_invalid_request(self, tmp_path: Path):
        provider = _provider(tmp_path)

        async def scenario():
            session_id = await provider.begin_upload(filename=None)
            await provider.upload_chunk(session_id, 0, "not-valid-base64!!!")

        with pytest.raises(InvalidRequestError):
            asyncio.run(scenario())

    def test_upload_chunk_over_max_chunk_bytes_raises_file_too_large(self, tmp_path: Path):
        provider = _provider(tmp_path, upload_max_chunk_bytes=4)

        async def scenario():
            session_id = await provider.begin_upload(filename=None)
            await provider.upload_chunk(session_id, 0, base64.b64encode(b"12345").decode("ascii"))

        with pytest.raises(FileTooLargeError):
            asyncio.run(scenario())

    def test_cumulative_total_over_max_size_bytes_raises_file_too_large_without_full_decode(self, tmp_path: Path):
        provider = _provider(tmp_path, max_size_bytes=8, upload_max_chunk_bytes=10)

        async def scenario():
            session_id = await provider.begin_upload(filename=None)
            await provider.upload_chunk(session_id, 0, base64.b64encode(b"12345").decode("ascii"))
            await provider.upload_chunk(session_id, 1, base64.b64encode(b"1234").decode("ascii"))

        with pytest.raises(FileTooLargeError):
            asyncio.run(scenario())

    def test_begin_upload_at_max_concurrent_sessions_raises_invalid_request(self, tmp_path: Path):
        provider = _provider(tmp_path, upload_max_concurrent=1)

        async def scenario():
            await provider.begin_upload(filename=None)
            await provider.begin_upload(filename=None)

        with pytest.raises(InvalidRequestError):
            asyncio.run(scenario())

    def test_begin_upload_succeeds_again_after_one_is_finalized(self, tmp_path: Path):
        provider = _provider(tmp_path, upload_max_concurrent=1)

        async def scenario():
            session_id = await provider.begin_upload(filename=None)
            for index, chunk in enumerate(_chunks(PDF_BYTES, 20)):
                await provider.upload_chunk(session_id, index, base64.b64encode(chunk).decode("ascii"))
            await provider.finalize_upload(session_id)
            return await provider.begin_upload(filename=None)

        second_session_id = asyncio.run(scenario())
        assert isinstance(second_session_id, str)

    def test_finalize_upload_with_zero_chunks_raises_invalid_request(self, tmp_path: Path):
        provider = _provider(tmp_path)

        async def scenario():
            session_id = await provider.begin_upload(filename=None)
            await provider.finalize_upload(session_id)

        with pytest.raises(InvalidRequestError):
            asyncio.run(scenario())

    def test_finalize_upload_on_non_pdf_content_raises_invalid_request(self, tmp_path: Path):
        provider = _provider(tmp_path)

        async def scenario():
            session_id = await provider.begin_upload(filename=None)
            await provider.upload_chunk(session_id, 0, base64.b64encode(b"not actually a pdf").decode("ascii"))
            await provider.finalize_upload(session_id)

        with pytest.raises(InvalidRequestError):
            asyncio.run(scenario())
