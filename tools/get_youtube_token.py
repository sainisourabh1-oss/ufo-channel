"""
Run ONCE on your laptop to get the YOUTUBE_REFRESH_TOKEN secret.

    pip install google-auth-oauthlib
    python tools/get_youtube_token.py

It opens your browser. Log in with the GOOGLE ACCOUNT THAT OWNS THE CHANNEL
(the dedicated channel account — not your personal one). It then prints a
refresh token; paste that into the YOUTUBE_REFRESH_TOKEN GitHub secret.

You need your OAuth client ID + secret (from Google Cloud, Step 6 in SETUP.md).
Either set them below or paste when prompted.
"""
from google_auth_oauthlib.flow import InstalledAppFlow

CLIENT_ID = "PASTE_YOUR_CLIENT_ID"
CLIENT_SECRET = "PASTE_YOUR_CLIENT_SECRET"
SCOPES = ["https://www.googleapis.com/auth/youtube"]


def main():
    cid = CLIENT_ID if "PASTE" not in CLIENT_ID else input("Client ID: ").strip()
    csec = CLIENT_SECRET if "PASTE" not in CLIENT_SECRET else input("Client secret: ").strip()
    cfg = {"installed": {
        "client_id": cid, "client_secret": csec,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://localhost"]}}
    flow = InstalledAppFlow.from_client_config(cfg, SCOPES)
    creds = flow.run_local_server(port=0)
    print("\n==============================================")
    print("YOUTUBE_REFRESH_TOKEN =", creds.refresh_token)
    print("==============================================")
    print("Paste the value above into your GitHub secret.")


if __name__ == "__main__":
    main()
