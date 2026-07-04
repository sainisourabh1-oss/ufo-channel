"""
Autonomous case discovery.

- Pulls real UFO cases from Wikipedia UFO CATEGORIES (curated -> relevant),
  not arbitrary article links (which leaked junk like '1321 lepers plot').
- Verifies each candidate actually reads like a UFO case.
- Finds usable images (public domain or CC-BY, no share-alike) from Commons.
"""
import re
import random
import requests
from pathlib import Path
from .settings import ROOT, CONFIG
from . import dedup

QUEUE = ROOT / "cases" / "queue"
WIKI_API = "https://en.wikipedia.org/w/api.php"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
HEADERS = {"User-Agent": "ufo-channel/1.0 (educational documentary; contact legendshipper@gmail.com)"}

CATEGORIES = ["Category:UFO sightings", "Category:UFO sightings in the United States",
              "Category:UFO sightings by country", "Category:Reported UFO sightings"]
SKIP_PREFIX = ("List of", "Category:", "Template:", "Index of", "Timeline of", "Wikipedia:")
UFO_WORDS = ("ufo", "uap", "unidentified", "flying saucer", "flying object",
             "extraterrestrial", "aerial phenomen", "sighting", "abduction", "close encounter")

_CANDIDATES = None


def _from_queue():
    if not QUEUE.exists():
        return None
    import json
    for path in sorted(QUEUE.glob("*.json")):
        try:
            case = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[source_news] skipping bad queue file {path.name}: {e}")
            continue
        if not dedup.already_used(case["case_id"]):
            return case
    return None


def _cat_query(cat, cmtype):
    out, cont = [], None
    for _ in range(4):
        params = {"action": "query", "list": "categorymembers", "cmtitle": cat,
                  "cmlimit": "500", "cmtype": cmtype, "format": "json"}
        if cont:
            params["cmcontinue"] = cont
        r = requests.get(WIKI_API, params=params, headers=HEADERS, timeout=60); r.raise_for_status()
        data = r.json()
        out += [m["title"] for m in data.get("query", {}).get("categorymembers", [])]
        cont = data.get("continue", {}).get("cmcontinue")
        if not cont:
            break
    return out


def _discover_titles():
    cats = list(CATEGORIES)
    for c in CATEGORIES:                       # expand one level of subcategories
        try:
            cats += _cat_query(c, "subcat")
        except Exception as e:
            print(f"[discover] subcats of '{c}' failed: {e}")
    titles = set()
    for c in cats:
        try:
            titles.update(_cat_query(c, "page"))
        except Exception as e:
            print(f"[discover] members of '{c}' failed: {e}")
    return [t for t in titles if not t.startswith(SKIP_PREFIX)]


def _candidates():
    global _CANDIDATES
    if _CANDIDATES is None:
        _CANDIDATES = _discover_titles()
        random.shuffle(_CANDIDATES)
        print(f"[discover] {len(_CANDIDATES)} candidate cases found")
    return _CANDIDATES


def _extract(title):
    try:
        params = {"action": "query", "prop": "extracts", "exintro": "1",
                  "explaintext": "1", "redirects": "1", "titles": title, "format": "json"}
        r = requests.get(WIKI_API, params=params, headers=HEADERS, timeout=60); r.raise_for_status()
        for p in r.json().get("query", {}).get("pages", {}).values():
            return p.get("extract", "") or ""
    except Exception as e:
        print(f"[discover] extract failed for '{title}': {e}")
    return ""


def _clean(title):
    t = re.sub(r"\(.*?\)", "", title)
    t = re.sub(r"\b(1[89]\d\d|20\d\d)\b", "", t)
    for w in ["incident", "sighting", "UFO", "case", "encounter", "the"]:
        t = re.sub(rf"\b{w}\b", "", t, flags=re.I)
    return " ".join(t.split())


def pick_case(exclude=None):
    exclude = exclude or set()
    q = _from_queue()
    if q and q["case_id"] not in exclude:
        return q
    used = dedup.used_case_ids() | exclude
    for t in _candidates():
        cid = "wiki_" + t.replace(" ", "_")[:90]
        if cid in used:
            continue
        seed = _extract(t)
        if len(seed) < 400:
            continue
        if not any(w in seed.lower() for w in UFO_WORDS):
            continue                            # not actually a UFO case -> skip
        clean = _clean(t)
        queries = [t] + ([clean] if clean and clean.lower() != t.lower() else [])
        return {"case_id": cid, "title": t, "seed": seed[:6000], "image_queries": queries}
    return None


# ---------- images from Wikimedia Commons ----------
def _acceptable(lic):
    l = (lic or "").lower()
    if "-sa" in l or "share" in l:              # share-alike -> avoid (viral licence)
        return False
    return any(k in l for k in ["public domain", "cc0", "pdm", "pd-", "cc by", "attribution"])


def fetch_commons_images(queries, n=8):
    urls, seen = [], set()
    for q in queries:
        if len(urls) >= n:
            break
        try:
            params = {"action": "query", "format": "json", "generator": "search",
                      "gsrsearch": f"{q} filetype:bitmap", "gsrnamespace": "6", "gsrlimit": "30",
                      "prop": "imageinfo", "iiprop": "url|extmetadata", "iiurlwidth": "1600"}
            r = requests.get(COMMONS_API, params=params, headers=HEADERS, timeout=60); r.raise_for_status()
            for p in r.json().get("query", {}).get("pages", {}).values():
                ii = (p.get("imageinfo") or [{}])[0]
                lic = ((ii.get("extmetadata") or {}).get("LicenseShortName") or {}).get("value", "")
                url = ii.get("thumburl") or ii.get("url")
                title = p.get("title", "")
                if url and title not in seen and _acceptable(lic):
                    seen.add(title)
                    urls.append(url)
                    if len(urls) >= n:
                        break
        except Exception as e:
            print(f"[commons] query '{q}' failed: {e}")
    print(f"[source_news] usable images found: {len(urls)}")
    return urls


def enough_material(case) -> bool:
    if case.get("prewritten"):
        return len([a for a in case.get("assets", []) if a.get("url")]) >= CONFIG["safety"]["min_sources_per_video"]
    return bool(case.get("seed"))
