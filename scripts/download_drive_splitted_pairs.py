"""Download Splitted Topical Qs from Google Drive and pair P{n} with MS{n}."""

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
OUT = ROOT / "output" / "drive-splitted-topical"
FOLDER_ID = "1Cunz7rajZdHZf2FXTLOYU4HeyJeNSHSb"
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

P_RE = re.compile(r"^P(\d+)_", re.I)
MS_RE = re.compile(r"^MS(\d+)_", re.I)


def load_creds() -> Credentials:
    if not CREDS.is_file():
        flow = InstalledAppFlow.from_client_secrets_file(str(OAUTH), SCOPES)
        creds = flow.run_local_server(port=0)
        CREDS.write_text(creds.to_json(), encoding="utf-8")
        return creds

    raw = json.loads(CREDS.read_text(encoding="utf-8"))
    oauth = json.loads(OAUTH.read_text(encoding="utf-8"))
    installed = oauth.get("installed") or oauth.get("web") or {}

    # Merge MCP-style token files that omit client_id/client_secret.
    info = {
        "token": raw.get("token") or raw.get("access_token"),
        "refresh_token": raw.get("refresh_token"),
        "token_uri": raw.get("token_uri") or installed.get("token_uri") or "https://oauth2.googleapis.com/token",
        "client_id": raw.get("client_id") or installed.get("client_id"),
        "client_secret": raw.get("client_secret") or installed.get("client_secret"),
        "scopes": raw.get("scopes") or SCOPES,
    }
    if not info["token"] and not info["refresh_token"]:
        raise SystemExit("credentials.json has no access/refresh token — re-run Drive MCP auth")

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
                pageSize=100,
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


def main() -> int:
    if not OAUTH.is_file():
        print(f"Missing OAuth keys: {OAUTH}", file=sys.stderr)
        return 1
    creds = load_creds()
    service = build("drive", "v3", credentials=creds, cache_discovery=False)
    files = list_pdfs(service, FOLDER_ID)
    pdfs = [f for f in files if f.get("name", "").lower().endswith(".pdf")]
    print(f"Found {len(pdfs)} PDFs in Splitted Topical Qs")

    questions: dict[int, dict] = {}
    answers: dict[int, dict] = {}
    for f in pdfs:
        name = f["name"]
        m = P_RE.match(name)
        if m:
            questions[int(m.group(1))] = f
            continue
        m = MS_RE.match(name)
        if m:
            answers[int(m.group(1))] = f

    nums = sorted(set(questions) | set(answers))
    pairs = []
    missing_q = []
    missing_a = []
    for n in nums:
        q = questions.get(n)
        a = answers.get(n)
        if q and a:
            pairs.append({"n": n, "question": q, "answer": a})
        elif q and not a:
            missing_a.append(n)
        elif a and not q:
            missing_q.append(n)

    OUT.mkdir(parents=True, exist_ok=True)
    manifest = {
        "folderId": FOLDER_ID,
        "pairCount": len(pairs),
        "missingAnswers": missing_a,
        "missingQuestions": missing_q,
        "pairs": [],
    }

    for pair in pairs:
        n = pair["n"]
        q = pair["question"]
        a = pair["answer"]
        q_path = OUT / f"P{n}_question.pdf"
        a_path = OUT / f"MS{n}_answer.pdf"
        print(f"Downloading P{n} <-> MS{n} ...")
        download(service, q["id"], q_path)
        download(service, a["id"], a_path)
        # title from question filename without P{n}_ prefix
        title = re.sub(r"^P\d+_[^-]*-\s*", "", q["name"], flags=re.I)
        title = title.replace(".pdf", "").replace("_", " ").strip()
        if not title:
            title = f"Topical Structured P{n}"
        manifest["pairs"].append(
            {
                "n": n,
                "title": title,
                "questionName": q["name"],
                "answerName": a["name"],
                "questionPath": str(q_path),
                "answerPath": str(a_path),
                "questionId": q["id"],
                "answerId": a["id"],
            }
        )

    manifest_path = OUT / "pairs_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Paired {len(pairs)} sets. Missing MS: {missing_a}. Missing P: {missing_q}")
    print(f"Manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
