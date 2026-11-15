"""
Apply channel branding + details via the YouTube API:
  banner image, description, keywords, country, default language, channel trailer
  (for non-subscribers), and translated channel title/description for other languages.
The profile picture cannot be set through the API (YouTube Studio only).

    python -m tools.channel_branding
"""
import io
import json
from PIL import Image
from googleapiclient.http import MediaIoBaseUpload
from pipeline.youtube import service
from pipeline.settings import ROOT, CONFIG
from pipeline import llm

DESC_HI = """दुनिया के सबसे चर्चित और दस्तावेज़ों में दर्ज UFO / UAP केस - अब हिंदी में।

सरकारी रिकॉर्ड, पायलटों और रडार ऑपरेटरों की गवाही, पुराने अख़बारों की रिपोर्टें - हर कहानी स्रोतों के साथ, आधिकारिक जवाब और उस पर उठे सवालों के साथ। हम दावे को सच नहीं बताते; हम सबूत दिखाते हैं, फ़ैसला आप करते हैं।

🛸 रोज़ शाम 6 बजे एक नया रहस्य  |  रोज़ 2 Shorts
📂 केस फाइलें  •  दावा बनाम सबूत  •  टॉप 5 रहस्य

सब्सक्राइब करें और घंटी दबाएँ, ताकि कोई रहस्य छूट न जाए।

Real, documented UFO & UAP cases from around the world, told as cinematic Hindi documentaries - official records, witness testimony and the debate around every case. New video every day."""

KEYWORDS = ('"UFO hindi" "UFO in hindi" UFO UAP "aliens hindi" "real ufo stories" यूएफओ रहस्य '
            '"mystery documentary hindi" "UFO case" "UFO documentary" "UFO sightings" "UAP hindi" '
            '"alien encounters" "unsolved mysteries hindi" "Roswell hindi"')
TRAILER = "tCIrorC4QD8"   # Exeter incident - the channel's best-performing video


def banner_url(yt):
    im = Image.open(ROOT / "assets" / "brand" / "banner.png").convert("RGB")
    buf = io.BytesIO(); im.save(buf, "JPEG", quality=92); buf.seek(0)
    r = yt.channelBanners().insert(media_body=MediaIoBaseUpload(buf, mimetype="image/jpeg", resumable=False)).execute()
    print("[brand] banner uploaded")
    return r["url"]


def main():
    yt = service()
    ch = yt.channels().list(part="brandingSettings,snippet,localizations", mine=True).execute()["items"][0]
    cid = ch["id"]
    bs = ch.get("brandingSettings", {})
    bs.setdefault("channel", {})
    bs["channel"].update({"description": DESC_HI, "keywords": KEYWORDS, "country": "IN",
                          "defaultLanguage": "hi", "unsubscribedTrailer": TRAILER})
    try:
        bs.setdefault("image", {})["bannerExternalUrl"] = banner_url(yt)
    except Exception as e:
        print(f"[brand] banner failed: {e}")
    yt.channels().update(part="brandingSettings", body={"id": cid, "brandingSettings": bs}).execute()
    print("[brand] description / keywords / country / language / trailer updated")

    langs = CONFIG["localization"]["languages"]
    try:
        tr = llm.ask_json(
            "Translate this YouTube channel description into each language code: " + ", ".join(langs) +
            '. Keep emojis and line breaks, natural for native speakers. Return ONLY JSON {"<code>": "<description>"}.\n\n' +
            DESC_HI, "bulk", temperature=0.3)
        locs = {k: {"title": ch["snippet"]["title"], "description": v[:990]}
                for k, v in tr.items() if k in langs and isinstance(v, str) and v.strip()}
        yt.channels().update(part="localizations", body={"id": cid, "localizations": locs}).execute()
        print(f"[brand] channel localized into {len(locs)} languages")
    except Exception as e:
        print(f"[brand] localizations failed: {e}")


if __name__ == "__main__":
    main()
