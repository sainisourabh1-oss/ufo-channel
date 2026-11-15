"""
Image sourcing with a permanent never-reuse guarantee.

Sources (all free + legal; share-alike excluded):
  wiki     - images inside the case's own Wikipedia article (most relevant)
  commons  - Wikimedia Commons search, random result depth (gsroffset) so results differ per run
  nasa     - NASA Image Library (public domain), random page
  openverse- Openverse (CC0 / PDM / CC-BY), random page
  ai       - keyless AI illustration, last resort only (unique by construction)

Every accepted image is recorded in used/images.csv with its URL, SHA1 and a 64-bit
perceptual hash. An image is rejected if its URL, bytes or *look* (dHash distance
<= config images.dhash_distance) matches anything ever used - so the same picture can't
come back even from a different site, size or crop-compressed copy.
"""
import csv
import hashlib
import io
import random
import time
import urllib.parse
from pathlib import Path
import requests
from PIL import Image
from .settings import ROOT, CONFIG, ist_now

UA = {"User-Agent": "RealUFOStoriesBot/2.0 (educational Hindi documentary channel; GitHub Actions)"}
LEDGER = ROOT / CONFIG["dedup"]["image_ledger"]
IMG = CONFIG["images"]
COMMONS = "https://commons.wikimedia.org/w/api.php"
WIKI = "https://en.wikipedia.org/w/api.php"

FILLER_QUERIES = [
    "night sky stars", "radar screen", "fighter jet", "military aircraft 1950s", "vintage newspaper",
    "observatory telescope", "storm clouds night", "air force base", "full moon clouds", "searchlight sky",
    "cockpit instruments", "milky way", "aurora sky", "desert night", "airport control tower 1950s",
    "naval ship ocean", "aircraft carrier", "satellite dish", "radio telescope", "space shuttle launch",
    "astronaut", "earth from orbit", "meteor shower", "lightning storm", "fog forest night",
    "farmland night", "mountain night sky", "city lights night aerial", "government archive documents",
    "typewriter document", "1960s police car", "vintage camera", "film reel", "newspaper printing press",
    "jet contrail sky", "weather balloon", "rocket launch", "nebula", "comet", "solar eclipse",
    "missile silo", "nuclear test", "submarine", "pentagon", "capitol building night", "highway night",
    "lighthouse night", "lake night", "volcano night", "snow mountains", "sand dunes night",
]

_led = None


def _dhash(im: Image.Image) -> int:
    g = im.convert("L").resize((9, 8), Image.LANCZOS)
    px = list(g.getdata())
    v = 0
    for r in range(8):
        for c in range(8):
            v = (v << 1) | (px[r * 9 + c] > px[r * 9 + c + 1])
    return v


def ledger():
    global _led
    if _led is None:
        _led = {"urls": set(), "sha": set(), "dh": []}
        if LEDGER.exists():
            with open(LEDGER, newline="", encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    _led["urls"].add(r["url"])
                    if r.get("sha1"):
                        _led["sha"].add(r["sha1"])
                    if r.get("dhash"):
                        _led["dh"].append(int(r["dhash"], 16))
        print(f"[images] ledger: {len(_led['urls'])} urls, {len(_led['dh'])} hashes")
    return _led


def _seen_look(h: int) -> bool:
    lim = IMG["dhash_distance"]
    return any(bin(h ^ x).count("1") <= lim for x in ledger()["dh"])


def record(entries, video_id):
    """entries: [{'url','sha1','dhash','source'}] -> append to ledger file."""
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    new = not LEDGER.exists()
    with open(LEDGER, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["date", "video", "source", "url", "sha1", "dhash"])
        for e in entries:
            w.writerow([ist_now().strftime("%Y-%m-%d"), video_id, e["source"], e["url"], e.get("sha1", ""),
                        f"{e['dhash']:016x}" if e.get("dhash") is not None else ""])


def _get(url, **kw):
    for attempt in range(3):
        try:
            r = requests.get(url, headers=UA, timeout=kw.pop("timeout", 45), **kw)
            if r.status_code == 429:
                time.sleep(20 * (attempt + 1)); continue
            r.raise_for_status()
            return r
        except Exception as e:
            if attempt == 2:
                print(f"[images] GET failed {url[:90]}: {e}")
            time.sleep(3 * (attempt + 1))
    return None


CREDIT = {}        # url -> "author | license | source page" (CC-BY requires attribution)


def _strip_html(s):
    import re as _re
    return _re.sub(r"<[^>]+>", "", s or "").strip()[:120]


def _wm_credit(p, ii):
    m = ii.get("extmetadata") or {}
    artist = _strip_html((m.get("Artist") or {}).get("value", "")) or "Unknown author"
    lic = (m.get("LicenseShortName") or {}).get("value", "")
    return f"{artist} | {lic} | {ii.get('descriptionurl', '')}"


def _ok_license(lic):
    l = (lic or "").lower()
    if "-sa" in l or "share" in l or "nc" in l.split("-"):
        return False
    return any(k in l for k in ["public domain", "cc0", "pdm", "pd-", "cc by", "cc-by", "attribution", "no restrictions"])


# ---------------------------------------------------------------- sources -> candidate URLs
def src_wiki(title, rnd):
    r = _get(WIKI, params={"action": "query", "titles": title, "generator": "images", "gimlimit": "50",
                           "prop": "imageinfo", "iiprop": "url|extmetadata|size", "iiurlwidth": "1920",
                           "format": "json", "redirects": "1"})
    out = []
    if r:
        for p in r.json().get("query", {}).get("pages", {}).values():
            ii = (p.get("imageinfo") or [{}])[0]
            name = p.get("title", "").lower()
            if any(x in name for x in (".svg", "logo", "icon", "flag of", "commons-", "question_book", "edit-clear", "wiki")):
                continue
            lic = ((ii.get("extmetadata") or {}).get("LicenseShortName") or {}).get("value", "")
            if ii.get("width", 0) >= IMG["min_width"] and _ok_license(lic):
                u = ii.get("thumburl") or ii.get("url")
                CREDIT[u] = _wm_credit(p, ii)
                out.append(u)
    rnd.shuffle(out)
    return out


def src_commons(q, rnd):
    r = _get(COMMONS, params={"action": "query", "format": "json", "generator": "search",
                              "gsrsearch": f"{q} filetype:bitmap", "gsrnamespace": "6", "gsrlimit": "40",
                              "gsroffset": str(rnd.choice([0, 0, 20, 40, 60, 80])),
                              "prop": "imageinfo", "iiprop": "url|extmetadata|size", "iiurlwidth": "1920"})
    out = []
    if r:
        for p in r.json().get("query", {}).get("pages", {}).values():
            ii = (p.get("imageinfo") or [{}])[0]
            lic = ((ii.get("extmetadata") or {}).get("LicenseShortName") or {}).get("value", "")
            if ii.get("width", 0) >= IMG["min_width"] and _ok_license(lic):
                u = ii.get("thumburl") or ii.get("url")
                CREDIT[u] = _wm_credit(p, ii)
                out.append(u)
    rnd.shuffle(out)
    return out


def src_nasa(q, rnd):
    r = _get("https://images-api.nasa.gov/search",
             params={"q": q, "media_type": "image", "page": str(rnd.randint(1, 3)), "page_size": "40"})
    out = []
    if r:
        for it in r.json().get("collection", {}).get("items", []):
            for l in it.get("links", []):
                h = l.get("href", "")
                if "~thumb" in h:
                    u = h.replace("~thumb", "~large")
                    CREDIT[u] = "NASA | public domain | images.nasa.gov"
                    out.append(u)
    rnd.shuffle(out)
    return out


def src_openverse(q, rnd):
    r = _get("https://api.openverse.org/v1/images/",
             params={"q": q, "page_size": "20", "page": str(rnd.randint(1, 4)),
                     "license": "cc0,pdm,by", "size": "large",
                     # Flickr refuses automated downloads (403) - the old pipeline silently lost
                     # most images to this and kept repeating the few that loaded.
                     "excluded_source": "flickr"}, timeout=30)
    out = []
    if r:
        # skip Wikimedia-hosted hits: full-size originals (heavy, throttled) already covered by src_commons
        for it in r.json().get("results", []):
            if it.get("url") and "wikimedia.org" not in it["url"] and (it.get("width") or 9999) >= IMG["min_width"]:
                CREDIT[it["url"]] = (f"{it.get('creator') or 'Unknown'} | {it.get('license', '').upper()} "
                                     f"{it.get('license_version', '')} | {it.get('foreign_landing_url', '')}")
                out.append(it["url"])
    rnd.shuffle(out)
    return out


def ai_url(prompt, rnd, W, H):
    q = urllib.parse.quote(prompt + ", cinematic documentary still, realistic, moody lighting, film grain")
    u = f"https://image.pollinations.ai/prompt/{q}?width={W}&height={H}&nologo=true&seed={rnd.randint(1, 10**9)}"
    CREDIT[u] = "AI-generated illustration (not a real photograph)"
    return u


# ---------------------------------------------------------------- download + dedup
def fetch(url, dest: Path, source: str, batch_dh: list, batch_sha: set):
    if url in ledger()["urls"]:
        return None
    r = _get(url, timeout=90)
    if not r:
        return None
    try:
        im = Image.open(io.BytesIO(r.content))
        im.load()
        im = im.convert("RGB")
    except Exception:
        return None
    if source == "ai" and im.height > 100:          # trim the provider's footer watermark strip
        im = im.crop((0, 0, im.width, int(im.height * 0.94)))
    if im.width < (IMG["min_width"] if source != "ai" else 600) or im.height < 400:
        return None
    sha = hashlib.sha1(r.content).hexdigest()
    dh = _dhash(im)
    lim = IMG["dhash_distance"]
    if sha in ledger()["sha"] or sha in batch_sha or _seen_look(dh) or \
            any(bin(dh ^ x).count("1") <= lim for x in batch_dh):
        return None
    # reject near-blank images (scans of empty pages, solid colours)
    ext = im.convert("L").getextrema()
    if ext[1] - ext[0] < 40:
        return None
    im.save(dest, "JPEG", quality=92)
    batch_dh.append(dh); batch_sha.add(sha)
    return {"path": dest, "url": url, "sha1": sha, "dhash": dh, "source": source, "credit": CREDIT.get(url, source)}


class Collector:
    """Gathers never-before-used images for each scene of one video."""

    def __init__(self, work: Path, case_title: str, seed: int, W: int, H: int):
        self.work, self.rnd, self.W, self.H = work, random.Random(seed), W, H
        self.title = case_title
        self.dh, self.sha, self.got, self.n = [], set(), [], 0
        self.wiki_pool = None
        self.tried = set()

    def _try(self, urls, source, need, out):
        for u in urls:
            if len(out) >= need:
                return
            if not u or u in self.tried:
                continue
            self.tried.add(u)
            self.n += 1
            e = fetch(u, self.work / f"img_{self.n:04}.jpg", source, self.dh, self.sha)
            if e:
                out.append(e)
            time.sleep(1.0 if "wikimedia" in u else 0.3)   # Wikimedia throttles cumulative load

    def for_scene(self, queries: list, need: int, scene_idx: int):
        out = []
        if self.wiki_pool is None:
            self.wiki_pool = src_wiki(self.title, self.rnd)
        # the case's own article images go to the first scenes
        take = self.wiki_pool[:max(0, need - 1)]
        self.wiki_pool = self.wiki_pool[len(take):]
        self._try(take, "wiki", need, out)
        qs = [q for q in queries if q][:5]
        self.rnd.shuffle(qs)
        sources = [("commons", src_commons), ("nasa", src_nasa), ("openverse", src_openverse)]
        for q in qs:
            for name, fn in self.rnd.sample(sources, len(sources)):
                if len(out) >= need:
                    break
                self._try(fn(q, self.rnd)[:12], name, need, out)
        fillers = self.rnd.sample(FILLER_QUERIES, 6)
        for q in fillers:
            if len(out) >= need:
                break
            for name, fn in self.rnd.sample(sources, 2):
                self._try(fn(q, self.rnd)[:10], name, need, out)
        if len(out) < need and IMG.get("ai_fallback", True):
            prompts = (qs or [self.title])
            for k in range(need - len(out) + 2):
                if len(out) >= need:
                    break
                self._try([ai_url(prompts[k % len(prompts)], self.rnd, 1920 if self.W > self.H else 1080,
                                  1080 if self.W > self.H else 1920)], "ai", need, out)
        print(f"[images] scene {scene_idx}: {len(out)}/{need} fresh images "
              f"({', '.join(sorted({e['source'] for e in out}))})")
        self.got += out
        return [e["path"] for e in out]
