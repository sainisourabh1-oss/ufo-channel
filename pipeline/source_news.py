"""
Picks the day's case and gathers its real, sourced material.

Order of preference:
  1. A curated case file in cases/queue/ that hasn't been used yet
     (most reliable — you control the sourcing). Video #1 lives here.
  2. Automated discovery from Chronicling America (LoC) — a documented
     historical UFO case from a real newspaper page.

Returns a `case` dict, or None to signal "skip today" (fail-safe).
"""
import json
import requests
from pathlib import Path
from .settings import ROOT, CONFIG
from . import dedup

QUEUE = ROOT / "cases" / "queue"
LOC_API = "https://www.loc.gov/collections/chronicling-america/"


def _from_queue():
    for path in sorted(QUEUE.glob("*.json")):
        case = json.loads(path.read_text(encoding="utf-8"))
        if not dedup.already_used(case["case_id"]):
            case["_source_file"] = str(path)
            return case
    return None


def _from_chronicling_america():
    """Search the LoC free API for a documented 'flying saucer' page."""
    try:
        params = {
            "q": "flying saucer sighting",
            "fa": "partof:chronicling america",
            "dates": "1947/1963",
            "fo": "json",
            "c": 25,
        }
        r = requests.get(LOC_API, params=params, timeout=30)
        r.raise_for_status()
        results = r.json().get("results", [])
        used = dedup.used_case_ids()
        for item in results:
            cid = "loc_" + (item.get("id", "").rstrip("/").split("/")[-1])
            if cid in used or not item.get("title"):
                continue
            return {
                "case_id": cid,
                "title": item.get("title"),
                "date": (item.get("date") or "")[:10],
                "summary_sources": [
                    {
                        "type": "newspaper",
                        "label": item.get("title"),
                        "url": (item.get("image_url") or [None])[0],
                        "page_url": item.get("id"),
                        "text": " ".join(item.get("description", []))[:4000],
                    }
                ],
                "assets": [
                    {"kind": "image", "label": item.get("title"),
                     "url": (item.get("image_url") or [None])[0], "source_label": item.get("title")}
                ],
            }
    except Exception as e:
        print(f"[source_news] LoC discovery failed: {e}")
    return None


def pick_case():
    case = _from_queue()
    if case:
        print(f"[source_news] using curated case: {case['case_id']}")
        return case
    case = _from_chronicling_america()
    if case:
        print(f"[source_news] using discovered case: {case['case_id']}")
        return case
    print("[source_news] no fresh case found — will skip today")
    return None


def enough_material(case) -> bool:
    """Fail-safe gate: need a minimum of distinct sourced assets."""
    need = CONFIG["safety"]["min_sources_per_video"]
    assets = case.get("assets", [])
    have = len([a for a in assets if a.get("url") or a.get("local")])
    print(f"[source_news] sourced assets: {have} (need {need})")
    return have >= need
