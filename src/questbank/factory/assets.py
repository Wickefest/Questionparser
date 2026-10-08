"""Resolve local parser assets and upload to Supabase Storage."""

from __future__ import annotations

import hashlib
import logging
import mimetypes
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class AssetUploadError(RuntimeError):
    """Raised when a referenced local asset cannot be persisted."""


class AssetUploader(Protocol):
    def upload(
        self,
        *,
        storage_path: str,
        data: bytes,
        content_type: str,
    ) -> str:
        """Upload bytes; return storage path (not a local filesystem path)."""


@dataclass(slots=True)
class ResolvedAsset:
    key: str
    filename: str
    role: str
    local_path: Path
    data: bytes
    mime_type: str
    sha256: str
    width: int | None
    height: int | None
    bbox: list[float] | None
    source_page: int | None
    storage_path: str


@dataclass(slots=True)
class MemoryAssetUploader:
    """Test double: records uploads without network I/O."""

    uploaded: list[dict[str, Any]]

    def __init__(self) -> None:
        self.uploaded = []

    def upload(self, *, storage_path: str, data: bytes, content_type: str) -> str:
        self.uploaded.append(
            {
                "storage_path": storage_path,
                "bytes": len(data),
                "content_type": content_type,
            }
        )
        return storage_path


class SupabaseAssetUploader:
    """Upload into the configured questions bucket via existing Supabase helpers."""

    def __init__(self, client: Any | None = None, *, bucket: str | None = None) -> None:
        from questbank.integrations.supabase_store import get_supabase_client, questions_bucket

        self._client = client if client is not None else get_supabase_client()
        if self._client is None:
            raise RuntimeError(
                "Supabase is not configured. Set SUPABASE_URL and SUPABASE_KEY "
                "to upload Question Factory assets."
            )
        self._bucket = bucket or questions_bucket()

    def upload(self, *, storage_path: str, data: bytes, content_type: str) -> str:
        self._client.storage.from_(self._bucket).upload(
            path=storage_path,
            file=data,
            file_options={"content-type": content_type, "upsert": "true"},
        )
        return storage_path


def resolve_and_upload_assets(
    *,
    descriptors: list[dict[str, Any]],
    assets_dir: Path,
    batch_id: str,
    run_id: str,
    uploader: AssetUploader,
) -> list[ResolvedAsset]:
    """Resolve each descriptor under assets_dir and upload to Storage."""
    resolved: list[ResolvedAsset] = []
    for desc in descriptors:
        filename = desc["filename"]
        local = _find_asset(assets_dir, filename)
        if local is None:
            raise AssetUploadError(f"Missing referenced asset: {filename}")
        data = local.read_bytes()
        mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        width, height = _image_size(data)
        storage_path = f"question-assets/{batch_id}/{run_id}/{filename}"
        uploader.upload(storage_path=storage_path, data=data, content_type=mime)
        resolved.append(
            ResolvedAsset(
                key=desc["key"],
                filename=filename,
                role=desc["role"],
                local_path=local,
                data=data,
                mime_type=mime,
                sha256=hashlib.sha256(data).hexdigest(),
                width=width,
                height=height,
                bbox=desc.get("bbox"),
                source_page=desc.get("source_page"),
                storage_path=storage_path,
            )
        )
    return resolved


def _find_asset(assets_dir: Path, filename: str) -> Path | None:
    direct = assets_dir / filename
    if direct.is_file():
        return direct
    # Allow nested smoke paths like assets/q01-stem-1.png already stripped.
    matches = list(assets_dir.rglob(filename))
    for match in matches:
        if match.is_file():
            return match
    return None


def _image_size(data: bytes) -> tuple[int | None, int | None]:
    try:
        from PIL import Image

        with Image.open(BytesIO(data)) as img:
            return img.size
    except Exception:  # noqa: BLE001
        return None, None
