"""Local-filesystem StorageBackend.

See docs/requirement-specification/02-storage.md for the design this implements.
"""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from anyio import to_thread

from prioris_mcp import EnvVars
from prioris_mcp.storage.backend import StorageBackend

logger = logging.getLogger(__name__)


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
                size_bytes = manifest.get("size_bytes")
                if size_bytes is None:
                    try:
                        size_bytes = manifest_path.with_suffix(".data").stat().st_size
                    except FileNotFoundError:
                        size_bytes = 0
                entries.append(
                    {
                        "provider": manifest["provider"],
                        "identifier": manifest.get("public_identifier") or manifest["canonical_identifier"],
                        "format": manifest["format"],
                        "fetched_at": manifest["fetched_at"],
                        "size_bytes": size_bytes,
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
