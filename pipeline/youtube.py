"""
YouTube Data API v3: upload (fully automatic publish), localizations, captions,
thumbnail, playlists, first comment. Everything after the upload itself is best-effort:
a failure there is logged and never loses the video.
"""
import datetime as dt
import json
import os
import time
from pathlib import Path
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build as gbuild
from googleapiclient.http import MediaFileUpload
from .settings import CONFIG, ROOT, ist_now

PLAYLISTS = ROOT / "used" / "playlists.json"
_yt = None


def service():
    global _yt
    if _yt is None:
        creds = Credentials(None, refresh_token=os.environ["YOUTUBE_REFRESH_TOKEN"],
                            token_uri="https://oauth2.googleapis.com/token",
                            client_id=os.environ["YOUTUBE_CLIENT_ID"],
                            client_secret=os.environ["YOUTUBE_CLIENT_SECRET"])
        _yt = gbuild("youtube", "v3", credentials=creds, cache_discovery=False)
    return _yt


def release_status(hour: int, minute: int = 0) -> dict:
    """public now if the slot has passed, else private + scheduled publishAt (auto-publishes)."""
    now = ist_now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now + dt.timedelta(minutes=20):
        return {"privacyStatus": "public"}
    utc = (target - dt.timedelta(hours=5, minutes=30)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return {"privacyStatus": "private", "publishAt": utc}


def upload(path: Path, title: str, description: str, tags: list, release: dict,
           localizations: dict | None = None) -> str:
    body = {
        "snippet": {"title": title[:100], "description": description[:4900], "tags": tags,
                    "categoryId": "27", "defaultLanguage": "hi", "defaultAudioLanguage": "hi"},
        "status": {**release, "selfDeclaredMadeForKids": False,
                   "containsSyntheticMedia": bool(CONFIG["metadata"].get("ai_disclosure", True)),
                   "embeddable": True, "publicStatsViewable": True},
    }
    parts = "snippet,status"
    if localizations:
        body["localizations"] = {k: {"title": v["title"][:100], "description": v["description"][:4900]}
                                 for k, v in localizations.items()}
        parts += ",localizations"
    last = None
    for attempt in range(3):
        try:
            media = MediaFileUpload(str(path), chunksize=16 * 1024 * 1024, resumable=True)
            req = service().videos().insert(part=parts, body=body, media_body=media)
            resp = None
            while resp is None:
                _, resp = req.next_chunk()
            print(f"[youtube] uploaded {resp['id']} ({release})")
            return resp["id"]
        except Exception as e:
            last = e
            print(f"[youtube] upload attempt {attempt + 1} failed: {str(e)[:300]}")
            if "localizations" in str(e) and "localizations" in parts:
                parts = "snippet,status"; body.pop("localizations", None)   # never lose a video to a bad translation
            time.sleep(20 * (attempt + 1))
    raise RuntimeError(f"upload failed: {last}")


def _soft(label, fn):
    try:
        out = fn()
        print(f"[youtube] {label}: ok")
        return out
    except Exception as e:
        print(f"[youtube] {label}: skipped ({str(e)[:220]})")
        return None


def thumbnail(video_id, jpg: Path):
    return _soft("thumbnail", lambda: service().thumbnails().set(
        videoId=video_id, media_body=MediaFileUpload(str(jpg), mimetype="image/jpeg")).execute())


def captions(video_id, lang, name, srt: Path):
    return _soft(f"captions {lang}", lambda: service().captions().insert(
        part="snippet", body={"snippet": {"videoId": video_id, "language": lang, "name": name, "isDraft": False}},
        media_body=MediaFileUpload(str(srt), mimetype="application/octet-stream")).execute())


def comment(video_id, text):
    return _soft("first comment", lambda: service().commentThreads().insert(
        part="snippet", body={"snippet": {"videoId": video_id,
                                          "topLevelComment": {"snippet": {"textOriginal": text}}}}).execute())


def playlist(video_id, key: str, title: str, description: str):
    def go():
        ids = json.loads(PLAYLISTS.read_text(encoding="utf-8")) if PLAYLISTS.exists() else {}
        if key not in ids:
            pl = service().playlists().insert(part="snippet,status", body={
                "snippet": {"title": title, "description": description, "defaultLanguage": "hi"},
                "status": {"privacyStatus": "public"}}).execute()
            ids[key] = pl["id"]
            PLAYLISTS.write_text(json.dumps(ids, ensure_ascii=False, indent=2), encoding="utf-8")
        service().playlistItems().insert(part="snippet", body={"snippet": {
            "playlistId": ids[key], "resourceId": {"kind": "youtube#video", "videoId": video_id}}}).execute()
    return _soft(f"playlist {key}", go)
