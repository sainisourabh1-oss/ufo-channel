"""
Autonomous case discovery.

Each run this:
  1. Pulls a large live list of DOCUMENTED UFO cases from Wikipedia
     (the 'List of reported UFO sightings' article + UFO categories).
  2. Skips any case already in the dedup ledger (never repeats).
  3. Takes a fresh case, pulls its real Wikipedia summary as the factual
     basis for the script, and finds public-domain images for it.
"""
import random
import requests
from pathlib import Path
from .settings import ROOT, CONFIG
from . import dedup

QUEUE = ROOT / "cases" / "queue"            # optional hand-made overrides
WIKI_API = "https://en.wikipedia.org/w/api.php"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
HEADERS = {"User-Agent": "ufo-channel/1.0 (educational documentary; contact legendshipper@gmail.com)"}

LIST_ARTICLES = ["List of reported UFO sightings", "List of UFO sightings"]
CATEGORIES = ["Category:UFO sightings", "Category:UFO sightings in the United States"]
SKIP_PREFIX = ("List of", "Category:", "Template:", "Index of", "Timeline of", "Wikipedia:")


def _from_queue():
    if not QUEUE.exists():
        return None
    import json
    for path in sorted(QUEUE.glob("*.json")):
        case = json.loads(path.read_text(encoding="utf-8"))
        if not dedup.already_used(case["case_id"]):
            return case
    return None


def _article_links(title):
    titles, cont = [], None
    for _ in range(6):
        params = {"action": "query", "prop": "links", "titles": title,
                  "plnamespace": "0", "pllimit": "500", "format": "json"}
        if cont:
            params["plcontinue"] = cont
        r = requests.get(WIKI_API, params=params, headers=HEADERS, timeout=60); r.raise_for_status()
        data = r.json()
        for p in data.get("query", {}).get("pages", {}).values():
            for l in p.get("links", []):
                titles.append(l["title"])
        cont = data.get("continue", {}).get("plcontinue")
        if not cont:
            break
    return titles


def _category_members(cat):
    titles, cont = [], None
    for _ in range(4):
        params = {"action": "query", "list": "categorymembers", "cmtitle": cat,
                  "cmlimit": "500", "cmtype": "page", "format": "json"}
        if cont:
            params["cmcontinue"] = cont
        r = requests.get(WIKI_API, params=params, headers=HEADERS, timeout=60); r.raise_for_status()
        data = r.json()
        titles += [m["title"] for m in data.get("query", {}).get("categorymembers", [])]
        cont = data.get("continue", {}).get("cmcontinue")
        if not cont:
            break
    return titles


def _discover_titles():
    titles = set()
    for art in LIST_ARTICLES:
        try:
            titles.update(_article_links(art))
        except Exception as e:
            print(f"[discover] list '{art}' failed: {e}")
    for cat in CATEGORIES:
        try:
            titles.update(_category_members(cat))
        except Exception as e:
            print(f"[discover] category '{cat}' failed: {e}")
    return [t for t in titles if not t.startswith(SKIP_PREFIX)]


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


def pick_case():
    case = _from_queue()
    if case:
        print(f"[source_news] curated override: {case['case_id']}")
        return case

    titles = _discover_titles()
    print(f"[discover] {len(titles)} candidate cases found")
    random.shuffle(titles)                      # variety between runs
    used = dedup.used_case_ids()
    for t in titles:
        cid = "wiki_" + t.replace(" ", "_")[:90]
        if cid in used:
            continue
        seed = _extract(t)
        if len(seed) < 400:                     # too thin to make a real video -> next
            continue
        print(f"[source_news] new case: {t}")
        return {"case_id": cid, "title": t, "seed": seed[:6000], "image_queries": [t]}
    print("[source_news] no fresh case found this run")
    return None


def _is_pd(lic):
    lic = (lic or "").lower()
    return any(k in lic for k in ["public domain", "cc0", "pdm", "pd-"])


def fetch_commons_images(queries, n=8):
    urls, seen = [], set()
    for q in queries:
        if len(urls) >= n:
            break
        try:
            params = {"action": "query", "format": "json", "generator": "search",
                      "gsrsearch": f"{q} filetype:bitmap", "gsrnamespace": "6", "gsrlimit": "25",
                      "prop": "imageinfo", "iiprop": "url|extmetadata", "iiurlwidth": "1600"}
            r = requests.get(COMMONS_API, params=params, headers=HEADERS, timeout=60); r.raise_for_status()
            for p in r.json().get("query", {}).get("pages", {}).values():
                ii = (p.get("imageinfo") or [{}])[0]
                lic = ((ii.get("extmetadata") or {}).get("LicenseShortName") or {}).get("value", "")
                url = ii.get("thumburl") or ii.get("url")
                title = p.get("title", "")
                if url and title not in seen and _is_pd(lic):
                    seen.add(title)
                    urls.append(url)
                    if len(urls) >= n:
                        break
        except Exception as e:
            print(f"[commons] query '{q}' failed: {e}")
    print(f"[source_news] public-domain images found: {len(urls)}")
    return urls


def enough_material(case) -> bool:
    if case.get("prewritten"):
        return len([a for a in case.get("assets", []) if a.get("url")]) >= CONFIG["safety"]["min_sources_per_video"]
    return bool(case.get("seed"))
