"""Tracks every case/clip ever used so nothing is repeated."""
import csv
from pathlib import Path
from .settings import ROOT, CONFIG, ist_now

LEDGER = ROOT / CONFIG["dedup"]["ledger"]


def _rows():
    if not LEDGER.exists():
        return []
    with open(LEDGER, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def already_used(case_id: str) -> bool:
    return any(r["case_id"] == case_id for r in _rows())


def used_case_ids():
    return {r["case_id"] for r in _rows()}


def log(case_id: str, title: str, video_id: str):
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    new = not LEDGER.exists()
    with open(LEDGER, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["date", "case_id", "title", "video_id"])
        w.writerow([ist_now().strftime("%Y-%m-%d"), case_id, title, video_id])
