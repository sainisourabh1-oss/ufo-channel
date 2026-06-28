"""
Uses Gemini (free tier) to write the Hindi script + shot list + metadata,
strictly from the case's real Wikipedia summary (the 'seed'). Then validates
against the hard rules. If validation fails -> return None (skip the day).
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
    sources = case.get("seed") or json.dumps(
        case.get("summary_sources", case.get("assets", [])), ensure_ascii=False, indent=2)
    prompt = (PROMPT
              .replace("{{SPEC}}", SPEC)
              .replace("{{TARGET_MIN}}", str(CONFIG["script"]["target_minutes"]))
              .replace("{{TITLE}}", case.get("title", ""))
              .replace("{{SOURCES}}", str(sources)))

    try:
        resp = _model().generate_content(prompt)
    except Exception as e:
        print(f"[script_gen] Gemini call failed: {e}")
        return None

    raw = resp.text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        print("[script_gen] model did not return clean JSON — skipping today")
        return None

    if not validate(data):
        return None
    return data


def validate(data: dict) -> bool:
    """Enforce the hard rules. Any failure => skip (fail-safe)."""
    for k in ["title_hi", "script_hi", "shot_list", "description_hi"]:
        if not data.get(k):
            print(f"[validate] missing field: {k}")
            return False
    banned = ["एलियन सच", "एलियंस असली", "confirmed alien", "proven alien", "यह एलियन था"]
    if any(b in data["script_hi"] for b in banned):
        print("[validate] script asserts aliens as fact")
        return False
    if len(data["script_hi"]) < 600:
        print("[validate] script too short")
        return False
    print("[validate] passed")
    return True
