"""Download all MCQ test PDFs from a Google Drive folder (combined Q+A files)."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

ROOT = Path(__file__).resolve().parents[1]
OAUTH = ROOT / "secrets" / "gdrive" / "gcp-oauth.keys.json"
CREDS = ROOT / "secrets" / "gdrive" / "credentials.json"
OUT = ROOT / "output" / "drive-mcq-fullmarks"
FOLDER_ID = "18_CVy1uniFI-Yvspbq-Lm8zuV78j2WFm"
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]


def load_creds() -> Credentials:
    raw = json.loads(CREDS.read_text(encoding="utf-8"))
    oauth = json.loads(OAUTH.read_text(encoding="utf-8"))
    installed = oauth.get("installed") or oauth.get("web") or {}
    info = {
        "token": raw.get("token") or raw.get("access_token"),
        "refresh_token": raw.get("refresh_token"),
        "token_uri": raw.get("token_uri") or installed.get("token_uri") or "https://oauth2.googleapis.com/token",
        "client_id": raw.get("client_id") or installed.get("client_id"),
        "client_secret": raw.get("client_secret") or installed.get("client_secret"),
        "scopes": raw.get("scopes") or SCOPES,
    }
    creds = Credentials.from_authorized_user_info(info, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        CREDS.write_text(creds.to_json(), encoding="utf-8")
    if not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(str(OAUTH), SCOPES)
        creds = flow.run_local_server(port=0)
        CREDS.write_text(creds.to_json(), encoding="utf-8")
    return creds


def list_pdfs(service, folder_id: str) -> list[dict]:
    files: list[dict] = []
    page_token = None
    while True:
        resp = (
            service.files()
            .list(
                q=f"'{folder_id}' in parents and trashed=false",
                fields="nextPageToken, files(id, name, mimeType, size)",
                pageSize=200,
                pageToken=page_token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute()
        )
        files.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return files


def download(service, file_id: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file() and dest.stat().st_size > 0:
        return
    request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
    with dest.open("wb") as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()


def slugify(name: str) -> str:
    text = re.sub(r"^Salinan\s+", "", name, flags=re.I)
    text = text.replace(".pdf", "")
    text = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-")
    return text


def main() -> int:
    creds = load_creds()
    service = build("drive", "v3", credentials=creds, cache_discovery=False)
    files = list_pdfs(service, FOLDER_ID)
    pdfs = [f for f in files if f.get("name", "").lower().endswith(".pdf")]
    print(f"Found {len(pdfs)} PDFs")

    OUT.mkdir(parents=True, exist_ok=True)
    manifest = {"folderId": FOLDER_ID, "files": []}
    for f in pdfs:
        slug = slugify(f["name"])
        dest = OUT / f"{slug}.pdf"
        print(f"Downloading {f['name']} -> {dest.name}")
        download(service, f["id"], dest)
        manifest["files"].append(
            {
                "id": f["id"],
                "name": f["name"],
                "slug": slug,
                "path": str(dest),
            }
        )

    manifest_path = OUT / "raw_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Wrote {manifest_path} ({len(manifest['files'])} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
