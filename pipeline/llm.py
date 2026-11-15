"""
Gemini (free tier, google-genai SDK) with automatic model selection + fallback.

Models are tried in config order; a model that 404s, is deprecated, or has used up its
daily quota is skipped for the rest of the run. Every call returns parsed JSON or raises.
"""
import json
import os
import re
import time
from .settings import CONFIG

_client = None
_dead = set()


def _c():
    global _client
    if _client is None:
        from google import genai
        _client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    return _client


def _parse(text: str):
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?", "", t).strip()
    t = re.sub(r"```$", "", t).strip()
    try:
        return json.loads(t)
    except Exception:
        pass
    for o, c in (("{", "}"), ("[", "]")):
        a, b = t.find(o), t.rfind(c)
        if a != -1 and b > a:
            try:
                return json.loads(t[a:b + 1])
            except Exception:
                continue
    return None


def ask_json(prompt: str, tier: str = "quality", temperature: float = 0.85, max_tokens: int = 32768):
    from google.genai import types
    models = CONFIG["llm"]["quality_models" if tier == "quality" else "bulk_models"]
    last = None
    for model in models:
        if model in _dead:
            continue
        for attempt in range(3):
            try:
                r = _c().models.generate_content(
                    model=model, contents=prompt,
                    config=types.GenerateContentConfig(
                        temperature=temperature, max_output_tokens=max_tokens,
                        response_mime_type="application/json"))
                data = _parse(r.text)
                if data is None:
                    raise ValueError("unparseable JSON")
                print(f"[llm] {tier} via {model}")
                return data
            except Exception as e:
                last = e
                msg = str(e)
                print(f"[llm] {model} attempt {attempt + 1}: {msg[:180]}")
                if any(k in msg for k in ("404", "NOT_FOUND", "no longer available", "deprecated")):
                    _dead.add(model); break
                if "RESOURCE_EXHAUSTED" in msg or "429" in msg:
                    if "PerDay" in msg or "per day" in msg.lower() or attempt == 2:
                        _dead.add(model); break
                    time.sleep(35)
                    continue
                time.sleep(6)
    raise RuntimeError(f"all Gemini models failed: {last}")
