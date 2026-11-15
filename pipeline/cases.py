"""
Case discovery - a much wider pool than before, plus three video formats.

Pool: Wikipedia UFO categories crawled two levels deep (sightings by country/decade,
abductions, hoaxes, government investigations, ufologists, UAP reports...), each page
verified to read like a UFO topic and to have enough text for a full script.

Formats (weights in config.script.formats):
  case_file - one documented case, deep dive (full article text, not just the intro)
  debunk    - a hoax/explained case: the claim vs the evidence
  top_five  - countdown of five cases sharing a theme (pilots, photographs, India/Asia...)

Never repeats a case that still has a live video. Cases whose video was deleted
(used/retired.csv) become eligible again, after all never-covered cases.
"""
import csv
import random
import re
import requests
from .settings import ROOT, CONFIG, ist_now
from . import dedup

WIKI = "https://en.wikipedia.org/w/api.php"
UA = {"User-Agent": "RealUFOStoriesBot/2.0 (educational Hindi documentary channel; GitHub Actions)"}

ROOT_CATS = [
    "Category:UFO sightings", "Category:Reported UFO sightings", "Category:UFO sightings by country",
    "Category:UFO sightings in the United States", "Category:Alien abduction reports",
    "Category:UFO-related phenomena", "Category:UFO hoaxes", "Category:Ufology",
    "Category:Government responses to UFOs", "Category:Unidentified flying objects",
    "Category:UFO conspiracy theories", "Category:Ufologists", "Category:Contactees",
    "Category:Ghost rockets", "Category:Unidentified anomalous phenomena",
]
HOAX_CATS = ("hoax", "conspiracy", "contactee", "pseudoscience")
SKIP_PREFIX = ("List of", "Category:", "Template:", "Index of", "Timeline of", "Wikipedia:", "Portal:", "Draft:")
SKIP_WORDS = ("(film)", "(TV series)", "(album)", "(song)", "(band)", "(novel)", "(video game)", "(magazine)", "episode")
UFO_WORDS = ("ufo", "uap", "unidentified", "flying saucer", "flying object", "extraterrestrial",
             "aerial phenomen", "sighting", "abduct", "close encounter", "alien")

THEMES = {   # top_five themes -> keywords matched against page title + intro
    "pilots": ("pilot", "aircraft", "airline", "flight", "air force", "jet"),
    "military": ("military", "navy", "army", "base", "radar", "missile", "nuclear"),
    "photos": ("photograph", "photo", "film", "footage", "video"),
    "abductions": ("abduct", "abduction", "missing time"),
    "hoaxes": ("hoax", "fake", "fabricat", "prank", "debunk"),
    "asia": ("india", "china", "japan", "russia", "soviet", "iran", "asia", "pakistan", "indonesia", "philippines"),
    "europe": ("france", "britain", "england", "uk ", "belgium", "italy", "spain", "sweden", "germany", "portugal"),
    "mass": ("thousands", "hundreds", "witnesses", "crowd", "residents", "wave", "flap"),
    "historic": ("1561", "1566", "1600", "1700", "18", "airship", "century"),
    "south_america": ("brazil", "argentina", "chile", "peru", "mexico", "colombia", "venezuela"),
    "oceans": ("ocean", "sea", "underwater", "ship", "submarine", "uso"),
    "official_files": ("project", "report", "study", "investigation", "committee", "files", "declassif"),
}

_POOL = None


def _get(params):
    params = {**params, "format": "json"}
    for attempt in range(3):
        try:
            r = requests.get(WIKI, params=params, headers=UA, timeout=60)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            print(f"[cases] wiki retry {attempt + 1}: {e}")
    return {}


def _members(cat, kind):
    out, cont = [], None
    for _ in range(5):
        p = {"action": "query", "list": "categorymembers", "cmtitle": cat, "cmlimit": "500", "cmtype": kind}
        if cont:
            p["cmcontinue"] = cont
        d = _get(p)
        out += [m["title"] for m in d.get("query", {}).get("categorymembers", [])]
        cont = d.get("continue", {}).get("cmcontinue")
        if not cont:
            break
    return out


def pool():
    """{title: {'hoax': bool}} for every candidate page."""
    global _POOL
    if _POOL is not None:
        return _POOL
    seen_cats, frontier, pages = set(), [(c, 0) for c in ROOT_CATS], {}
    while frontier:
        cat, depth = frontier.pop(0)
        if cat in seen_cats or len(seen_cats) > 160:
            continue
        seen_cats.add(cat)
        is_hoax = any(h in cat.lower() for h in HOAX_CATS)
        for t in _members(cat, "page"):
            if t.startswith(SKIP_PREFIX) or any(w in t for w in SKIP_WORDS):
                continue
            pages.setdefault(t, {"hoax": False})
            pages[t]["hoax"] |= is_hoax
        if depth < 2:
            frontier += [(c, depth + 1) for c in _members(cat, "subcat")
                         if not any(x in c.lower() for x in ("fiction", "film", "television", "music", "novels", "video games", "comics"))]
    _POOL = pages
    print(f"[cases] pool: {len(pages)} pages from {len(seen_cats)} categories")
    return pages


def article(title, full=True):
    p = {"action": "query", "prop": "extracts|pageimages|info", "explaintext": "1", "redirects": "1",
         "titles": title, "inprop": "url", "piprop": "original"}
    if not full:
        p["exintro"] = "1"
    for pg in _get(p).get("query", {}).get("pages", {}).values():
        text = pg.get("extract", "") or ""
        # drop reference-ish tail sections
        text = re.split(r"\n==+ ?(See also|References|Notes|Further reading|External links|Bibliography|Sources) ?==+", text)[0]
        return {"title": pg.get("title", title), "text": text.strip(), "url": pg.get("fullurl", "")}
    return {"title": title, "text": "", "url": ""}


def intros(titles):
    """intro text for many pages, 20 per request."""
    out = {}
    for i in range(0, len(titles), 20):
        d = _get({"action": "query", "prop": "extracts", "exintro": "1", "explaintext": "1",
                  "exlimit": "max", "titles": "|".join(titles[i:i + 20])})
        for pg in d.get("query", {}).get("pages", {}).values():
            out[pg.get("title", "")] = pg.get("extract", "") or ""
    return out


def case_id(title):
    return "wiki_" + title.replace(" ", "_")[:90]


def _ufoish(text):
    t = text.lower()
    return sum(t.count(w) for w in UFO_WORDS) >= 3


def _pick_format(rnd):
    f = CONFIG["script"]["formats"]
    names = list(f)
    return rnd.choices(names, weights=[f[n] for n in names])[0]


def _single(rnd, want_hoax, exclude):
    live, retired = dedup.used_case_ids(), dedup.retired_case_ids()
    titles = list(pool())
    rnd.shuffle(titles)
    fresh = [t for t in titles if case_id(t) not in live and case_id(t) not in exclude]
    # never-covered first, then cases whose video was deleted (remakes)
    fresh.sort(key=lambda t: case_id(t) in retired)
    if want_hoax is not None:
        pref = [t for t in fresh if pool()[t]["hoax"] == want_hoax]
        fresh = pref + [t for t in fresh if t not in pref]
    for t in fresh[:60]:
        a = article(t)
        if len(a["text"]) < CONFIG["safety"]["min_seed_chars"] or not _ufoish(a["text"]):
            continue
        return {"format": "debunk" if pool()[t]["hoax"] else "case_file", "case_id": case_id(t),
                "title": a["title"], "sources": [a], "remake": case_id(t) in retired}
    return None


def _top_five(rnd, exclude):
    used_themes = {r["case_id"] for r in dedup.rows() if r["case_id"].startswith("top5_")}
    themes = [k for k in THEMES if f"top5_{k}" not in used_themes and f"top5_{k}" not in exclude]
    if not themes:   # every theme done once -> allow a second round with a dated id
        themes = list(THEMES)
    rnd.shuffle(themes)
    titles = list(pool()); rnd.shuffle(titles)
    blurbs = intros(titles[:400])
    for theme in themes:
        picks = []
        for t in titles[:400]:
            hay = (t + " " + blurbs.get(t, "")).lower()
            if not any(k in hay for k in THEMES[theme]):
                continue
            a = article(t)
            if len(a["text"]) >= 800 and _ufoish(a["text"]):
                a["text"] = a["text"][:5000]
                picks.append(a)
            if len(picks) == 5:
                break
        if len(picks) == 5:
            cid = f"top5_{theme}" if f"top5_{theme}" not in used_themes else f"top5_{theme}_{ist_now():%Y%m%d}"
            return {"format": "top_five", "case_id": cid, "title": f"Top 5: {theme}",
                    "theme": theme, "sources": picks, "remake": False}
    return None


def pick(exclude=frozenset(), seed=None):
    rnd = random.Random(seed)
    fmt = _pick_format(rnd)
    print(f"[cases] format roll: {fmt}")
    case = None
    if fmt == "top_five":
        case = _top_five(rnd, exclude)
    if case is None:
        case = _single(rnd, want_hoax=(fmt == "debunk"), exclude=exclude)
    if case:
        print(f"[cases] picked {case['case_id']} ({case['format']}{', remake' if case['remake'] else ''})")
    return case
