"""Local disk fallback when Supabase Storage is not configured."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def factory_storage_root() -> Path:
    raw = (os.environ.get("FACTORY_STORAGE_ROOT") or "").strip()
    if raw:
        root = Path(raw)
    else:
        root = Path(__file__).resolve().parents[3] / "output" / "factory-storage"
    root.mkdir(parents=True, exist_ok=True)
    return root


class LocalDiskAssetUploader:
    """Write bytes under output/factory-storage (dev / no-Supabase)."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or factory_storage_root()
        self.uploaded: list[dict[str, Any]] = []

    def upload(self, *, storage_path: str, data: bytes, content_type: str) -> str:
        path = self.root / storage_path.replace("\\", "/").lstrip("/")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        self.uploaded.append(
            {
                "storage_path": storage_path,
                "bytes": len(data),
                "content_type": content_type,
                "local_path": str(path),
            }
        )
        return storage_path

    def resolve_path(self, storage_path: str) -> Path | None:
        path = self.root / storage_path.replace("\\", "/").lstrip("/")
        return path if path.is_file() else None


def _remote_storage_bytes(storage_path: str) -> tuple[bytes, str] | None:
    """Download bytes from Supabase Storage. Returns None when not configured."""
    try:
        from questbank.integrations.supabase_store import get_supabase_client, questions_bucket

        client = get_supabase_client()
        if client is None:
            return None
        bucket = questions_bucket()
        data = client.storage.from_(bucket).download(storage_path)
        import mimetypes

        mime = mimetypes.guess_type(storage_path)[0] or "application/octet-stream"
        return bytes(data), mime
    except Exception:  # noqa: BLE001
        return None


def resolve_storage_bytes(storage_path: str) -> tuple[bytes, str] | None:
    """Load bytes from local factory storage or Supabase when configured."""
    local = LocalDiskAssetUploader().resolve_path(storage_path)
    if local is not None:
        import mimetypes

        mime = mimetypes.guess_type(local.name)[0] or "application/octet-stream"
        return local.read_bytes(), mime
    return _remote_storage_bytes(storage_path)


def materialize_storage_path(
    storage_path: str,
    *,
    source: Path | None = None,
) -> Path | None:
    """Return a local Path the parser can open for a durable storage object.

    Live servers store PDFs/assets in Supabase, not on disk. PyMuPDF still
    needs a filesystem path, so this copies from ``source`` (upload staging)
    or downloads from Storage into FACTORY_STORAGE_ROOT.
    """
    dest = factory_storage_root() / storage_path.replace("\\", "/").lstrip("/")
    if dest.is_file():
        return dest

    data: bytes | None = None
    if source is not None:
        src = Path(source)
        if src.is_file():
            data = src.read_bytes()
    if data is None:
        remote = _remote_storage_bytes(storage_path)
        if remote is None:
            return None
        data = remote[0]

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return dest


def create_asset_uploader():
    """Prefer Supabase; fall back to local disk."""
    try:
        from questbank.factory.assets import SupabaseAssetUploader

        return SupabaseAssetUploader()
    except RuntimeError:
        return LocalDiskAssetUploader()
