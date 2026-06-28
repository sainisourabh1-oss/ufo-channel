"""Loads config.yaml and exposes shared paths/helpers."""
import os
import datetime as dt
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parent.parent


def load():
    with open(ROOT / "config.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


CONFIG = load()


def ist_now():
    """Current time in IST regardless of where the runner is."""
    return dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=5, minutes=30)


def workdir(video_id: str) -> Path:
    d = ROOT / "work" / video_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def set_output(name: str, value: str):
    """Expose a value to the GitHub Actions workflow."""
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"{name}={value}\n")
    print(f"[output] {name}={value}")
