"""
YouTube upload + scheduled publish.

build stage  -> upload_private(): uploads the video as PRIVATE, returns its id.
publish stage -> schedule_release(): called only AFTER you approve in GitHub;
                 sets publishAt to today 6 PM IST (or publishes now if past 6 PM).
"""
import os
import datetime as dt
from pathlib import Path
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build as gbuild
from googleapiclient.http import MediaFileUpload
from .settings import CONFIG, ist_now

SCOPES = ["https://www.googleapis.com/auth/youtube"]


def _service():
    creds = Credentials(
        None,
        refresh_token=os.environ["YOUTUBE_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["YOUTUBE_CLIENT_ID"],
        client_secret=os.environ["YOUTUBE_CLIENT_SECRET"],
        scopes=SCOPES,
    )
    return gbuild("youtube", "v3", credentials=creds)


def upload_private(video_path: Path, meta: dict) -> str:
    yt = _service()
    body = {
        "snippet": {
            "title": meta["title"],
            "description": meta["description"],
            "tags": meta["tags"],
            "categoryId": "27",  # Education
            "defaultLanguage": "hi",
        },
        "status": {
            "privacyStatus": "private",          # stays private until you approve
            "selfDeclaredMadeForKids": False,
            "containsSyntheticMedia": True,       # AI/altered-content disclosure
        },
    }
    media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True)
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    resp = req.execute()
    vid = resp["id"]
    print(f"[upload] uploaded PRIVATE: https://youtu.be/{vid}")
    return vid


def schedule_release(video_id: str):
    """Set the video to publish at 6 PM IST today (or now if already past)."""
    yt = _service()
    now = ist_now()
    target = now.replace(hour=CONFIG["release"]["publish_hour"],
                         minute=CONFIG["release"]["publish_minute"],
                         second=0, microsecond=0)
    if target <= now:
        # already past 6 PM -> publish immediately
        yt.videos().update(part="status", body={
            "id": video_id, "status": {"privacyStatus": "public"}}).execute()
        print("[publish] past 6 PM IST — published now")
        return
    publish_at_utc = (target - dt.timedelta(hours=5, minutes=30)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    yt.videos().update(part="status", body={
        "id": video_id,
        "status": {"privacyStatus": "private", "publishAt": publish_at_utc,
                   "selfDeclaredMadeForKids": False}}).execute()
    print(f"[publish] scheduled to go live at 6 PM IST ({publish_at_utc} UTC)")
