"""YouTube upload via Data API v3 (OAuth desktop flow)."""
from __future__ import annotations

from pathlib import Path

from .config import ROOT

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]
CLIENT_SECRET = ROOT / "client_secret.json"
TOKEN = ROOT / "token.json"


def is_connected() -> bool:
    return TOKEN.exists()


def has_client_secret() -> bool:
    return CLIENT_SECRET.exists()


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


def connect() -> dict:
    """Trigger OAuth (opens browser on first run) and return the linked channel."""
    return channel_info()


def channel_info() -> dict:
    """Return {title, id, subscribers} for the authorized channel."""
    r = _service().channels().list(part="snippet,statistics", mine=True).execute()
    items = r.get("items") or []
    if not items:
        return {"title": "(unknown)", "id": "", "subscribers": ""}
    it = items[0]
    return {
        "title": it["snippet"]["title"],
        "id": it["id"],
        "subscribers": it.get("statistics", {}).get("subscriberCount", ""),
    }


def upload(
    video_path: Path,
    title: str,
    description: str,
    tags: list[str],
    privacy: str = "public",
    category_id: str = "24",
    made_for_kids: bool = False,
    publish_at: str | None = None,  # ISO8601 -> schedules (forces privacy=private)
    thumbnail: Path | None = None,
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
    svc = _service()
    media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True, mimetype="video/mp4")
    req = svc.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        _, response = req.next_chunk()
    video_id = response["id"]

    if thumbnail and Path(thumbnail).exists():
        try:
            svc.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(str(thumbnail), mimetype="image/jpeg"),
            ).execute()
        except Exception as e:
            # custom thumbnails require a verified channel; don't fail the upload
            print(f"    (thumbnail not set: {e})")
    return video_id
