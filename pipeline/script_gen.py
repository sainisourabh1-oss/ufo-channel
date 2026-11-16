"""
Writes the script (Gemini, quality tier) strictly from the sourced material, validates it,
and produces the translations used to reach non-Hindi viewers (bulk tier).
"""
import re
from .settings import ROOT, CONFIG
from . import llm

SPEC = (ROOT / "CHANNEL_SPEC.md").read_text(encoding="utf-8")
PROMPT = (ROOT / "prompts" / "script_prompt.txt").read_text(encoding="utf-8")
S = CONFIG["script"]

FORMATS = {
    "case_file": ("CASE FILE - one documented case, deep dive",
                  "Tell the case chronologically like a thriller: the night it happened, the witnesses, "
                  "the evidence, the investigation, the official verdict, the dissent, what remains unexplained."),
    "debunk": ("CLAIM vs EVIDENCE - a famous claim, examined",
               "Open with the claim at its most convincing, then walk through what investigators found. "
               "Be fair and never mocking: show why people believed it, then what the evidence shows. "
               "End on what the case teaches about how to judge UFO stories."),
    "top_five": ("TOP 5 COUNTDOWN - five documented cases on one theme",
                 "A countdown from #5 to #1 (most astonishing last). Scene 1 is a hook teasing #1. "
                 "Give each case 2 scenes. Start each case's first scene by naming its number "
                 "(\"नंबर पाँच...\"). The chapter_hi for those scenes must be like \"#5 | <case name>\"."),
}

BANNED = ["एलियन सच", "एलियंस असली", "यह एलियन था", "सबूत है कि एलियन", "confirmed alien", "proven alien"]


def _sources_block(case):
    parts = []
    for a in case["sources"]:
        parts.append(f"### {a['title']}  ({a.get('url', '')})\n{a['text'][:14000]}")
    return "\n\n".join(parts)


def _words(data):
    return sum(len(sc.get("narration_hi", "").split()) for sc in data.get("scenes", []))


def validate(data) -> str | None:
    need = ["title_hi", "scenes", "description_hi"] + (["shorts"] if CONFIG["shorts"].get("enabled") else [])
    for k in need:
        if not data.get(k):
            return f"missing {k}"
    scenes = data["scenes"]
    if len(scenes) < S["min_scenes"]:
        return f"only {len(scenes)} scenes"
    if any(not sc.get("narration_hi") or not sc.get("visual_queries_en") for sc in scenes):
        return "scene missing narration or visual queries"
    w = _words(data)
    if w < S["min_words"]:
        return f"too short ({w} words)"
    text = " ".join(sc["narration_hi"] for sc in scenes)
    dev = len(re.findall(r"[ऀ-ॿ]", text)) / max(1, len(re.sub(r"\s", "", text)))
    if dev < 0.85:
        return f"not mostly Devanagari ({dev:.2f})"
    if any(b.lower() in text.lower() for b in BANNED):
        return "asserts aliens as fact"
    if not CONFIG["shorts"].get("enabled"):
        data["shorts"] = []          # Shorts are off: never block a good long script on them
        return None
    shorts = [s for s in data["shorts"] if len(s.get("narration_hi", "").split()) >= 70]
    if not shorts:
        return "no usable shorts"
    data["shorts"] = shorts[:CONFIG["shorts"]["count"]]
    return None


def write(case) -> dict | None:
    name, brief = FORMATS[case["format"]]
    prompt = (PROMPT.replace("{{SPEC}}", SPEC).replace("{{FORMAT_NAME}}", name)
              .replace("{{FORMAT_BRIEF}}", brief).replace("{{SOURCES}}", _sources_block(case))
              .replace("{{MIN_WORDS}}", str(S["min_words"])).replace("{{MAX_WORDS}}", str(S["max_words"]))
              .replace("{{MIN_SCENES}}", str(S["min_scenes"])))
    data = None
    for attempt in range(3):
        if data is None:
            data = llm.ask_json(prompt, "quality")
        problem = validate(data) if isinstance(data, dict) else "not an object"
        if not problem:
            print(f"[script] ok: {len(data['scenes'])} scenes, {_words(data)} words, {len(data['shorts'])} shorts")
            return data
        print(f"[script] attempt {attempt + 1} rejected: {problem}")
        if isinstance(data, dict) and problem.startswith("too short"):
            # ask the model to expand its own draft instead of starting over
            data = llm.ask_json(
                prompt + "\n\nYOUR PREVIOUS DRAFT WAS TOO SHORT (" + problem + "). Here it is:\n" +
                __import__("json").dumps(data, ensure_ascii=False) +
                f"\n\nRewrite it to {S['min_words']}-{S['max_words']} words using MORE detail from the "
                "sources (more scenes, more description of the evidence and witnesses). Same JSON shape.",
                "quality")
        else:
            data = None
    return None


def translations(data, languages):
    """{lang: {'title':..., 'description':...}} for YouTube localizations."""
    prompt = ("Translate this YouTube video title and description from Hindi into each of these "
              f"languages: {', '.join(languages)}. Keep names accurate, keep it natural and click-worthy "
              "for native speakers of each language, title max 95 characters, keep the hashtags as they are. "
              'Return ONLY JSON: {"<lang code>": {"title": "...", "description": "..."}, ...}\n\n'
              f"TITLE: {data['title_hi']}\n\nDESCRIPTION:\n{data['description_hi']}")
    try:
        out = llm.ask_json(prompt, "bulk", temperature=0.3)
        return {k: v for k, v in out.items() if k in languages and v.get("title") and v.get("description")}
    except Exception as e:
        print(f"[script] translations failed: {e}")
        return {}


def translate_lines(lines, lang="English"):
    """translate caption lines 1:1 (for an English caption track)."""
    prompt = (f"Translate each Hindi line into natural {lang}. Return ONLY a JSON array of strings with "
              f"exactly {len(lines)} items, same order.\n\n" +
              "\n".join(f"{i + 1}. {l}" for i, l in enumerate(lines)))
    try:
        out = llm.ask_json(prompt, "bulk", temperature=0.2)
        if isinstance(out, list) and len(out) == len(lines):
            return [str(x) for x in out]
    except Exception as e:
        print(f"[script] caption translation failed: {e}")
    return None
