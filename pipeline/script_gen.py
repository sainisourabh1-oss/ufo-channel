"""
Uses Gemini (free tier) to write the Hindi script + shot list + metadata,
strictly from the case's real Wikipedia summary (the 'seed'). Then validates
against the hard rules. If validation fails -> return None (skip the day).
"""
import os
import re
import json
import google.generativeai as genai
from .settings import ROOT, CONFIG

SPEC = (ROOT / "CHANNEL_SPEC.md").read_text(encoding="utf-8")
PROMPT = (ROOT / "prompts" / "script_prompt.txt").read_text(encoding="utf-8")


def _model():
    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
    # response_mime_type forces Gemini to return a single valid JSON object.
    return genai.GenerativeModel(
        CONFIG["script"]["model"],
        generation_config={"response_mime_type": "application/json", "temperature": 0.9},
    )


def _parse(text: str):
    """Tolerant JSON extraction: strip fences, isolate the outermost braces."""
    t = text.strip()
    t = re.sub(r"^```(?:json)?", "", t).strip()
    t = re.sub(r"```$", "", t).strip()
    try:
        return json.loads(t)
    except Exception:
        pass
    start, end = t.find("{"), t.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(t[start:end + 1])
        except Exception:
            return None
    return None


def write_script(case: dict) -> dict | None:
    sources = case.get("seed") or json.dumps(
        case.get("summary_sources", case.get("assets", [])), ensure_ascii=False, indent=2)
    prompt = (PROMPT
              .replace("{{SPEC}}", SPEC)
              .replace("{{TARGET_MIN}}", str(CONFIG["script"]["target_minutes"]))
              .replace("{{TITLE}}", case.get("title", ""))
              .replace("{{SOURCES}}", str(sources)))

    for attempt in range(2):                       # one retry if the first is messy
        try:
            resp = _model().generate_content(prompt)
        except Exception as e:
            print(f"[script_gen] Gemini call failed: {e}")
            return None
        data = _parse(resp.text or "")
        if data:
            if validate(data):
                return data
            return None
        print(f"[script_gen] unparseable response (attempt {attempt + 1}) — retrying")
    print("[script_gen] could not get clean JSON — skipping today")
    return None


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
