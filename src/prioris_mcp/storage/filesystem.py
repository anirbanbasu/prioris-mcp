"""Local-filesystem StorageBackend: documents/<hash>/<format>/{document,markdown,metadata.jsonl}.

See docs/requirement-specification/02-storage.md#directory-layout and
docs/requirement-specification/02-storage.md#v1-local-filesystem-backend.
"""

import hashlib
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from shutil import rmtree

from anyio import to_thread

from prioris_mcp import EnvVars
from prioris_mcp.storage.backend import StorageBackend
from prioris_mcp.storage.catalogue import Catalogue
from prioris_mcp.storage.manifest import DocumentManifest
from prioris_mcp.storage.migration import migrate_if_needed

logger = logging.getLogger(__name__)


class FilesystemStorageBackend(StorageBackend):
    """Persists content under a directory on local disk. See module docstring."""

    def __init__(self, base_dir: Path | None = None) -> None:
        super().__init__()
        self._base_dir = base_dir if base_dir is not None else EnvVars.PRIORIS_MCP_STORAGE_DIR
        self._base_dir.mkdir(parents=True, exist_ok=True)
        (self._base_dir / "documents").mkdir(parents=True, exist_ok=True)
        migrate_if_needed(self._base_dir)
        self._catalogue = Catalogue(self._base_dir / "catalogue.sqlite")

    @staticmethod
    def _document_hash(provider: str, identifier: str) -> str:
        payload = json.dumps([provider, identifier])
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _document_dir(self, provider: str, identifier: str) -> Path:
        return self._base_dir / "documents" / self._document_hash(provider, identifier)

    def _artefact_path(self, provider: str, identifier: str, format: str, artefact: str) -> Path:
        return self._document_dir(provider, identifier) / format / artefact

    def _metadata_path(self, provider: str, identifier: str, format: str) -> Path:
        return self._document_dir(provider, identifier) / format / "metadata.jsonl"

    def manifest_for(self, provider: str, identifier: str) -> DocumentManifest:
        return DocumentManifest(self._document_dir(provider, identifier) / "manifest.sqlite")

    async def exists(self, provider: str, identifier: str, format: str, artefact: str = "document") -> bool:
        path = self._artefact_path(provider, identifier, format, artefact)
        return await to_thread.run_sync(path.exists)

    async def read(self, provider: str, identifier: str, format: str, artefact: str = "document") -> bytes:
        path = self._artefact_path(provider, identifier, format, artefact)
        return await to_thread.run_sync(path.read_bytes)

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
        artefact_path = self._artefact_path(provider, identifier, format, artefact)
        await to_thread.run_sync(lambda: artefact_path.parent.mkdir(parents=True, exist_ok=True))
        await to_thread.run_sync(self._atomic_write, artefact_path, content)
        entry = {
            "provider": provider,
            "canonical_identifier": identifier,
            "original_identifier": original_identifier,
            "public_identifier": public_identifier,
            "format": format,
            "artefact": artefact,
            "size_bytes": len(content),
            "recorded_at": datetime.now(UTC).isoformat(),
        }
        await self._catalogue.upsert(entry)
        await to_thread.run_sync(self._append_metadata_line, provider, identifier, format, entry)
        return str(artefact_path)

    def _append_metadata_line(self, provider: str, identifier: str, format: str, entry: dict) -> None:
        metadata_path = self._metadata_path(provider, identifier, format)
        with metadata_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")

    @staticmethod
    def _atomic_write(path: Path, content: bytes) -> None:
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_bytes(content)
        tmp_path.replace(path)

    async def list(self, provider: str | None = None, format: str | None = None) -> list[dict]:
        entries = await self._catalogue.list(provider, format)
        return [
            {
                "provider": entry["provider"],
                "identifier": entry["public_identifier"] or entry["canonical_identifier"],
                "format": entry["format"],
                "artefact": entry["artefact"],
                "fetched_at_or_parsed_at": entry["recorded_at"],
                "size_bytes": entry["size_bytes"],
            }
            for entry in entries
        ]

    async def delete(self, provider: str, identifier: str, format: str, artefact: str) -> bool:
        entry = await self._catalogue.find_by_external_identifier(provider, identifier, format)
        if entry is None:
            return False
        canonical_identifier = entry["canonical_identifier"]

        if artefact != "all":
            removed = await self._catalogue.remove(provider, identifier, format, artefact)
            if removed:
                path = self._artefact_path(provider, canonical_identifier, format, artefact)
                await to_thread.run_sync(lambda: path.unlink(missing_ok=True))
            return removed

        removed_artefacts = await self._catalogue.remove_all_artefacts(provider, canonical_identifier, format)
        if not removed_artefacts:
            return False
        format_dir = self._document_dir(provider, canonical_identifier) / format
        await to_thread.run_sync(lambda: rmtree(format_dir, ignore_errors=True))
        await self.manifest_for(provider, canonical_identifier).delete_format(format)
        remaining_formats = await self._catalogue.count_formats(provider, canonical_identifier)
        if remaining_formats == 0:
            doc_dir = self._document_dir(provider, canonical_identifier)
            await to_thread.run_sync(lambda: rmtree(doc_dir, ignore_errors=True))
        return True

    # TODO(Task 7c): read_manifest/find_canonical_identifier over the catalogue.
    async def read_manifest(
        self, provider: str, identifier: str, format: str, artefact: str = "document"
    ) -> dict | None:
        raise NotImplementedError

    # TODO(Task 7c): read_manifest/find_canonical_identifier over the catalogue.
    async def find_canonical_identifier(self, provider: str, public_identifier: str, format: str) -> str | None:
        raise NotImplementedError
