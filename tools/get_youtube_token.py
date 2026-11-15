"""
Re-issue the channel's YouTube token with the scopes the pipeline needs and store it
straight into the GitHub secret YOUTUBE_REFRESH_TOKEN (never printed).

    python tools/get_youtube_token.py ["D:/ufo-channel/ufo youtube secrets.txt"]

A browser opens: sign in with the Google account that OWNS the channel and allow access.
Scopes: youtube (upload/manage) + youtube.force-ssl (captions, pinned comment).
The OAuth client ID/secret are read from the given text file by pattern, or from env
YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET, or typed in.
"""
import os
import re
import subprocess
import sys
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube",
          "https://www.googleapis.com/auth/youtube.force-ssl"]
REPO = "sainisourabh1-oss/ufo-channel"


def creds_from(path):
    if path and os.path.exists(path):
        t = open(path, encoding="utf-8", errors="ignore").read()
        cid = re.search(r"[0-9]+-[a-z0-9]+\.apps\.googleusercontent\.com", t)
        sec = re.search(r"GOCSPX-[A-Za-z0-9_\-]+", t)
        if cid and sec:
            return cid.group(0), sec.group(0)
    cid, sec = os.environ.get("YOUTUBE_CLIENT_ID"), os.environ.get("YOUTUBE_CLIENT_SECRET")
    if cid and sec:
        return cid, sec
    return input("Client ID: ").strip(), input("Client secret: ").strip()


def main():
    cid, sec = creds_from(sys.argv[1] if len(sys.argv) > 1 else None)
    cfg = {"installed": {"client_id": cid, "client_secret": sec,
                         "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                         "token_uri": "https://oauth2.googleapis.com/token",
                         "redirect_uris": ["http://localhost"]}}
    flow = InstalledAppFlow.from_client_config(cfg, SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
    if not creds.refresh_token:
        sys.exit("No refresh token returned - remove the app at myaccount.google.com/permissions and retry.")
    r = subprocess.run(["gh", "secret", "set", "YOUTUBE_REFRESH_TOKEN", "-R", REPO],
                       input=creds.refresh_token, text=True, capture_output=True)
    if r.returncode == 0:
        print("OK: YOUTUBE_REFRESH_TOKEN updated in GitHub (token not shown). Granted scopes:", creds.scopes)
    else:
        print("Could not set the GitHub secret automatically:", r.stderr.strip())
        print("Run: gh secret set YOUTUBE_REFRESH_TOKEN -R", REPO, "  and paste the token from a private source.")


if __name__ == "__main__":
    main()
