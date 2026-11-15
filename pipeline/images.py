"""
Image sourcing with a permanent never-reuse guarantee.

Sources (all free + legal; share-alike excluded):
  wiki     - images inside the case's own Wikipedia article (most relevant)
  commons  - Wikimedia Commons search, random result depth (gsroffset) so results differ per run
  nasa     - NASA Image Library (public domain), random page
  openverse- Openverse (CC0 / PDM / CC-BY), random page
  wellcome - Wellcome Collection: historic prints/broadsheets/astronomy (PDM / CC0 / CC-BY)
  cleveland- Cleveland Museum of Art open access (CC0)
  met      - The Metropolitan Museum of Art open access (CC0)
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


# Speed: a throttled host is put on cooldown and skipped (other sources fill in) instead of
# sleeping 20/40/60 s per URL - serial sleeps over hundreds of candidates made a build take 1h+.
_COOL = {}                      # host -> time.time() when it may be used again
_STATS = {"ok": 0, "fail": 0, "cooled": 0}


def _host(url):
    return urllib.parse.urlparse(url).netloc


def _get(url, attempts=2, **kw):
    host = _host(url)
    if _COOL.get(host, 0) > time.time():
        _STATS["cooled"] += 1
        return None
    timeout = kw.pop("timeout", 30)
    for attempt in range(attempts):
        try:
            r = requests.get(url, headers=UA, timeout=timeout, **kw)
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", "0") or 0) or 90
                _COOL[host] = time.time() + min(max(wait, 30), 300)
                print(f"[images] {host} throttled -> skipping it for {min(max(wait, 30), 300)}s")
                _STATS["fail"] += 1
                return None
            r.raise_for_status()
            _STATS["ok"] += 1
            return r
        except Exception as e:
            if attempt == attempts - 1:
                _STATS["fail"] += 1
                print(f"[images] GET failed {url[:90]}: {str(e)[:80]}")
            else:
                time.sleep(2)
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


def src_wellcome(q, rnd):
    """Wellcome Collection (keyless): historic prints, broadsheets, astronomy plates. PDM / CC0 / CC-BY only."""
    r = _get("https://api.wellcomecollection.org/catalogue/v2/images",
             params={"query": q, "pageSize": "30", "page": str(rnd.randint(1, 2)),
                     "locations.license": "pdm,cc-0,cc-by", "include": "source.contributors"})
    out = []
    if r:
        for it in r.json().get("results", []):
            loc = (it.get("locations") or [{}])[0]
            info = loc.get("url", "")
            if not info.endswith("/info.json"):
                continue
            u = info[:-len("info.json")] + "full/1920,/0/default.jpg"
            src = it.get("source") or {}
            who = ", ".join(c.get("agent", {}).get("label", "") for c in src.get("contributors", [])[:2]) or "Unknown"
            CREDIT[u] = (f"{who} | {loc.get('license', {}).get('label', 'PDM')} | "
                         f"https://wellcomecollection.org/works/{src.get('id', '')} (Wellcome Collection)")
            out.append(u)
    rnd.shuffle(out)
    return out


def src_cleveland(q, rnd):
    """Cleveland Museum of Art open access (keyless, CC0 only)."""
    r = _get("https://openaccess-api.clevelandart.org/api/artworks/",
             params={"q": q, "has_image": "1", "cc0": "1", "limit": "25", "skip": str(rnd.choice([0, 0, 25, 50]))})
    out = []
    if r:
        for it in r.json().get("data", []):
            ims = it.get("images") or {}
            web = next((ims[k] for k in ("print", "web") if (ims.get(k) or {}).get("url")
                        and int(ims[k].get("width") or 0) >= IMG["min_width"]), {})
            if web.get("url"):
                CREDIT[web["url"]] = f"Cleveland Museum of Art | CC0 | {it.get('url', '')}"
                out.append(web["url"])
    rnd.shuffle(out)
    return out


def src_met(q, rnd):
    """The Met open access (keyless). Search returns ids; each object is checked for public domain."""
    r = _get("https://collectionapi.metmuseum.org/public/collection/v1.1/search",
             params={"q": q, "hasImages": "true"})
    ids = (r.json().get("objectIDs") or []) if r else []
    rnd.shuffle(ids)
    out = []
    for oid in ids[:8]:
        o = _get(f"https://collectionapi.metmuseum.org/public/collection/v1/objects/{oid}", attempts=1, timeout=15)
        if not o:
            continue
        d = o.json()
        u = d.get("primaryImage") or d.get("primaryImageSmall")   # "small" is only ~600px
        if d.get("isPublicDomain") and u:
            CREDIT[u] = f"{d.get('artistDisplayName') or 'Unknown'} | CC0 | {d.get('objectURL', '')} (The Met)"
            out.append(u)
    return out


def ai_url(prompt, rnd, W, H):
    q = urllib.parse.quote(prompt + ", cinematic documentary still, realistic, moody lighting, film grain")
    u = f"https://image.pollinations.ai/prompt/{q}?width={W}&height={H}&nologo=true&seed={rnd.randint(1, 10**9)}"
    CREDIT[u] = "AI-generated illustration (not a real photograph)"
    return u


# ---------------------------------------------------------------- download + dedup
def download(url, source):
    # candidate images: one try each (there are always more candidates); AI renders are slow
    return _get(url, attempts=1, timeout=(75 if source == "ai" else 25))


def fetch(url, dest: Path, source: str, batch_dh: list, batch_sha: set, r=None):
    if url in ledger()["urls"]:
        return None
    r = r if r is not None else download(url, source)
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
        self.t0 = time.time()
        self.budget = IMG.get("time_budget_min", 22) * 60    # whole-video image budget

    def new_budget(self, minutes):
        self.t0, self.budget = time.time(), minutes * 60

    def _over(self):
        return time.time() - self.t0 > self.budget

    def _try(self, urls, source, need, out):
        """Downloads run 4 at a time (2 for throttle-prone Wikimedia); the dedup check stays
        serial so two copies of one picture can never both get in."""
        from concurrent.futures import ThreadPoolExecutor
        todo = [u for u in urls if u and u not in self.tried and u not in ledger()["urls"]]
        width = 2 if any("wikimedia" in u for u in todo) else 4
        with ThreadPoolExecutor(width) as ex:
            for i in range(0, len(todo), width):
                if len(out) >= need:
                    return
                chunk = todo[i:i + width]
                self.tried.update(chunk)
                for u, r in zip(chunk, ex.map(lambda u: download(u, source), chunk)):
                    if len(out) >= need:
                        break
                    self.n += 1
                    e = fetch(u, self.work / f"img_{self.n:04}.jpg", source, self.dh, self.sha, r=r) if r else None
                    if e:
                        out.append(e)
                if any("wikimedia" in u for u in chunk):
                    time.sleep(0.6)                      # Wikimedia throttles cumulative load

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
        # photos/archives first (closest match), museum art second (moody, period-correct filler)
        primary = [("commons", src_commons), ("nasa", src_nasa), ("openverse", src_openverse),
                   ("wellcome", src_wellcome)]
        museums = [("cleveland", src_cleveland), ("met", src_met)]
        rounds = [(q, self.rnd.sample(primary, len(primary)), 12) for q in qs]
        rounds += [(q, self.rnd.sample(museums, len(museums)), 8) for q in qs[:3]]
        rounds += [(q, self.rnd.sample(primary + museums, 3), 10) for q in self.rnd.sample(FILLER_QUERIES, 6)]
        for q, srcs, cap in rounds:
            for name, fn in srcs:
                if len(out) >= need or self._over():
                    break
                self._try(fn(q, self.rnd)[:cap], name, need, out)
        if len(out) < need and IMG.get("ai_fallback", True):
            prompts = (qs or [self.title])
            for k in range(need - len(out) + 2):
                if len(out) >= need or time.time() - self.t0 > self.budget + 600:
                    break
                self._try([ai_url(prompts[k % len(prompts)], self.rnd, 1920 if self.W > self.H else 1080,
                                  1080 if self.W > self.H else 1920)], "ai", need, out)
        print(f"[images] scene {scene_idx}: {len(out)}/{need} fresh images "
              f"({', '.join(sorted({e['source'] for e in out}))}) "
              f"t={time.time() - self.t0:.0f}s http ok/fail/cooled={_STATS['ok']}/{_STATS['fail']}/{_STATS['cooled']}")
        self.got += out
        return [e["path"] for e in out]
