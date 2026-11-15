"""Capability probe #2: Gemini TTS via the Interactions API + Gemini image generation."""
import base64
import json
import os
import traceback
from pathlib import Path

OUT = Path("probe_out"); OUT.mkdir(exist_ok=True)
LINE = "रिकॉर्ड के मुताबिक, उस रात वॉशिंगटन के आसमान में रडार पर सात अनजानी चीज़ें दिखाई दीं। अधिकारियों ने कहा — यह मौसम का असर था। लेकिन पायलटों ने कुछ और ही देखा।"
STYLE = "Deep, calm, confident male Hindi documentary narrator. Measured pace, suspense in the pauses."


def find_blobs(obj, path=""):
    """yield (path, mime, b64) for every base64 media blob in a nested response."""
    if isinstance(obj, dict):
        if "data" in obj and isinstance(obj["data"], str) and len(obj["data"]) > 1000:
            yield path, obj.get("mime_type") or obj.get("mimeType"), obj["data"]
        for k, v in obj.items():
            if k != "data":
                yield from find_blobs(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from find_blobs(v, f"{path}[{i}]")


def dump(r):
    d = r.model_dump(mode="json") if hasattr(r, "model_dump") else r
    return d


def tts(c):
    print("===== tts (interactions) =====")
    for mdl in ["gemini-3.8-flash-tts", "gemini-3.8-flash-lite-tts", "gemini-3.1-flash-tts-preview"]:
        for fmt in [{"type": "audio", "mime_type": "audio/mp3"}, {"type": "audio"}]:
            try:
                r = c.interactions.create(
                    model=mdl,
                    input=[{"type": "user_input", "content": [{"type": "text", "text": LINE,
                            "annotations": [{"type": "speech_metadata", "style": STYLE}]}]}],
                    response_format=fmt,
                    generation_config={"speech_config": [{"voice": "Charon", "language": "hi-IN"}]})
                d = dump(r)
                blobs = list(find_blobs(d))
                skel = json.dumps(d, default=str)
                print("TTS OK?", mdl, fmt, "blobs:", [(p, m, len(b)) for p, m, b in blobs], "usage:", d.get("usage"))
                if blobs:
                    p, m, b = blobs[0]
                    ext = "mp3" if m and "mp3" in m else ("wav" if m and "wav" in m else "bin")
                    (OUT / f"tts_{mdl}_{fmt.get('mime_type','default').replace('/','_')}.{ext}").write_bytes(base64.b64decode(b))
                else:
                    print("   response:", skel[:1500])
                break
            except Exception as e:
                print("TTS FAIL", mdl, fmt, str(e)[:400])


def image(c):
    print("===== image =====")
    from google.genai import types
    prompt = ("A bold, minimal YouTube channel logo: a glowing neon-green flying saucer silhouette casting a "
              "tractor beam, inside a circle, deep night-sky navy background, high contrast, flat vector style, "
              "no text, centered, 1:1")
    for mdl in ["gemini-3.1-flash-image", "gemini-3.1-flash-lite-image", "gemini-2.5-flash-image", "gemini-3-pro-image"]:
        try:
            r = c.models.generate_content(model=mdl, contents=prompt,
                                          config=types.GenerateContentConfig(response_modalities=["IMAGE"]))
            got = False
            for part in r.candidates[0].content.parts:
                if part.inline_data and part.inline_data.data:
                    data = part.inline_data.data
                    data = data if isinstance(data, bytes) else base64.b64decode(data)
                    (OUT / f"img_{mdl}.png").write_bytes(data); got = True
                    print("IMG OK", mdl, part.inline_data.mime_type, len(data))
            if not got:
                print("IMG EMPTY", mdl, str(r)[:300])
        except Exception as e:
            print("IMG FAIL", mdl, str(e)[:300])


if __name__ == "__main__":
    from google import genai
    c = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    for fn in (tts, image):
        try:
            fn(c)
        except Exception:
            traceback.print_exc()
