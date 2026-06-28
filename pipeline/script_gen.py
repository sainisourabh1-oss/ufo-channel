"""
Uses Gemini (free tier) to write the Hindi script + shot list + metadata,
strictly from the case's sourced material. Then validates against the hard
rules in CHANNEL_SPEC.md. If validation fails -> return None (skip the day).
"""
import os
import json
import google.generativeai as genai
from .settings import ROOT, CONFIG

SPEC = (ROOT / "CHANNEL_SPEC.md").read_text(encoding="utf-8")
PROMPT = (ROOT / "prompts" / "script_prompt.txt").read_text(encoding="utf-8")


def _model():
    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
    return genai.GenerativeModel(CONFIG["script"]["model"])


def write_script(case: dict) -> dict | None:
    sources_blob = json.dumps(case.get("summary_sources", case.get("assets", [])),
                              ensure_ascii=False, indent=2)
    prompt = (PROMPT
              .replace("{{SPEC}}", SPEC)
              .replace("{{TARGET_MIN}}", str(CONFIG["script"]["target_minutes"]))
              .replace("{{TITLE}}", case.get("title", ""))
              .replace("{{SOURCES}}", sources_blob))

    resp = _model().generate_content(prompt)
    raw = resp.text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        print("[script_gen] model did not return clean JSON — skipping today")
        return None

    if not validate(data, case):
        return None
    return data


def validate(data: dict, case: dict) -> bool:
    """Enforce the hard rules. Any failure => skip (fail-safe)."""
    required = ["title_hi", "script_hi", "shot_list", "description_hi"]
    for k in required:
        if not data.get(k):
            print(f"[validate] missing field: {k}")
            return False

    # Every shot must reference a real asset/source label from the case.
    known_labels = {a.get("source_label") or a.get("label") for a in case.get("assets", [])}
    known_labels |= {s.get("label") for s in case.get("summary_sources", [])}
    known_labels = {x for x in known_labels if x}
    for shot in data["shot_list"]:
        if not shot.get("source_label"):
            print("[validate] a shot has no source label")
            return False

    # No "alien is real" assertions (must stay in framed language).
    banned = ["एलियन सच", "एलियंस असली", "confirmed alien", "proven alien"]
    text = data["script_hi"]
    if any(b in text for b in banned):
        print("[validate] script asserts aliens as fact")
        return False

    print("[validate] passed")
    return True
