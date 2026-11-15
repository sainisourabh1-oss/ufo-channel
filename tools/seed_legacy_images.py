"""One-time: replay the OLD pipeline's fixed filler searches (top-5 results each from Commons
and Openverse - the images every old video reused) and record them in used/images.csv so
the new pipeline never shows them again."""
import hashlib
import io
import requests
from PIL import Image
from pipeline import images

OLD_QUERIES = ["ufo", "flying saucer", "night sky stars", "radar screen", "fighter jet", "military aircraft",
               "vintage newspaper", "observatory telescope", "dark clouds sky", "1950s aircraft", "air force jet",
               "full moon night", "spotlight beam sky", "cockpit instruments", "milky way galaxy", "old photograph sky"]
OLD_UA = {"User-Agent": "ufo-channel/1.0 (educational documentary)"}


def old_commons(q):
    r = requests.get(images.COMMONS, params={"action": "query", "format": "json", "generator": "search",
                     "gsrsearch": f"{q} filetype:bitmap", "gsrnamespace": "6", "gsrlimit": "30",
                     "prop": "imageinfo", "iiprop": "url|extmetadata", "iiurlwidth": "1600"}, headers=OLD_UA, timeout=60)
    out = []
    for p in r.json().get("query", {}).get("pages", {}).values():
        ii = (p.get("imageinfo") or [{}])[0]
        lic = ((ii.get("extmetadata") or {}).get("LicenseShortName") or {}).get("value", "")
        l = lic.lower()
        if ("-sa" in l or "share" in l) or not any(k in l for k in ["public domain", "cc0", "pdm", "pd-", "cc by", "attribution"]):
            continue
        u = ii.get("thumburl") or ii.get("url")
        if u:
            out.append(u)
    return out[:8]


def old_openverse(q):
    try:
        r = requests.get("https://api.openverse.org/v1/images/", params={"q": q, "page_size": "20",
                         "license": "cc0,pdm,by", "format": "json"}, headers=OLD_UA, timeout=60)
        return [it["url"] for it in r.json().get("results", []) if it.get("url")][:8]
    except Exception:
        return []


if __name__ == "__main__":
    entries = []
    known = images.ledger()["urls"]
    for q in OLD_QUERIES:
        for u in old_commons(q) + old_openverse(q):
            if u in known:
                continue
            e = {"url": u, "source": "legacy", "sha1": "", "dhash": None}
            try:
                r = requests.get(u, headers=images.UA, timeout=60); r.raise_for_status()
                im = Image.open(io.BytesIO(r.content)).convert("RGB")
                e["sha1"] = hashlib.sha1(r.content).hexdigest(); e["dhash"] = images._dhash(im)
            except Exception as ex:
                print("hash skipped", u[:80], ex)
            entries.append(e); known.add(u)
        print(q, len(entries))
    images.record(entries, "legacy")
    print("recorded", len(entries))
