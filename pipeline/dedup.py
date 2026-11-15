"""
Ledgers (all committed to the repo so they survive between runs):
  used/ledger.csv   every video ever made: date, case_id, title, video_id, kind
  used/retired.csv  case_ids whose video was deleted -> may be remade
  used/images.csv   every image ever shown (url, sha1, perceptual hash) -> never shown again
"""
import csv
from .settings import ROOT, CONFIG, ist_now

LEDGER = ROOT / CONFIG["dedup"]["ledger"]
RETIRED = ROOT / CONFIG["dedup"]["retired"]


def _read(path):
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def rows():
    return _read(LEDGER)


def retired_case_ids():
    return {r["case_id"] for r in _read(RETIRED)}


def used_case_ids():
    """cases with a live video (retired ones are free to remake)."""
    retired = retired_case_ids()
    live = {r["case_id"] for r in rows() if r.get("kind", "long") == "long"}
    # a case remade after retirement is live again
    remade = {r["case_id"] for r in rows() if r.get("kind", "long") == "long" and r["date"] >= "2026-11-15"}
    return (live - retired) | remade


def already_made_today():
    today = ist_now().strftime("%Y-%m-%d")
    return any(r["date"] == today and r.get("kind", "long") == "long" for r in rows())


def log(case_id: str, title: str, video_id: str, kind: str = "long"):
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    existing = rows()
    fields = ["date", "case_id", "title", "video_id", "kind"]
    with open(LEDGER, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in existing:
            w.writerow({k: r.get(k, "long" if k == "kind" else "") for k in fields})
        w.writerow({"date": ist_now().strftime("%Y-%m-%d"), "case_id": case_id,
                    "title": title, "video_id": video_id, "kind": kind})


def retire(video_ids: set):
    """mark the cases of deleted videos as retired."""
    have = retired_case_ids()
    new = [r for r in rows() if r["video_id"] in video_ids and r["case_id"] not in have]
    write_header = not RETIRED.exists()
    with open(RETIRED, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if write_header:
            w.writerow(["date", "case_id", "video_id"])
        for r in new:
            w.writerow([ist_now().strftime("%Y-%m-%d"), r["case_id"], r["video_id"]])
    return len(new)
