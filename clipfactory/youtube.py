"""YouTube upload via Data API v3 (OAuth desktop flow)."""
from __future__ import annotations

from pathlib import Path

from .config import ROOT

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
CLIENT_SECRET = ROOT / "client_secret.json"
TOKEN = ROOT / "token.json"


def _service():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None
    if TOKEN.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CLIENT_SECRET.exists():
                raise RuntimeError(
                    "Missing client_secret.json (OAuth desktop app) in project root."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN.write_text(creds.to_json())
    return build("youtube", "v3", credentials=creds)


def upload(
    video_path: Path,
    title: str,
    description: str,
    tags: list[str],
    privacy: str = "public",
    category_id: str = "24",
    made_for_kids: bool = False,
    publish_at: str | None = None,  # ISO8601 -> schedules (forces privacy=private)
) -> str:
    from googleapiclient.http import MediaFileUpload

    status = {"privacyStatus": "private" if publish_at else privacy,
              "selfDeclaredMadeForKids": made_for_kids}
    if publish_at:
        status["publishAt"] = publish_at

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": category_id,
        },
        "status": status,
    }
    media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True, mimetype="video/mp4")
    req = _service().videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        _, response = req.next_chunk()
    return response["id"]
