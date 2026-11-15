"""Capability probe: which Gemini models/TTS work with this key, and whether edge-tts works here."""
import asyncio
import base64
import os
import subprocess
import traceback
import wave
from pathlib import Path

OUT = Path("probe_out"); OUT.mkdir(exist_ok=True)
LINE = "रिकॉर्ड के मुताबिक, उस रात वॉशिंगटन के आसमान में रडार पर सात अनजानी चीज़ें दिखाई दीं। अधिकारियों ने कहा — यह मौसम का असर था। लेकिन पायलटों ने कुछ और ही देखा।"


def section(t):
    print(f"\n===== {t} =====", flush=True)


def gemini():
    from google import genai
    from google.genai import types
    c = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    section("models")
    names = []
    for m in c.models.list():
        names.append(m.name)
        print(m.name, getattr(m, "supported_actions", ""))
    section("text")
    for mdl in ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite", "gemini-2.5-flash-lite", "gemini-2.5-flash"]:
        try:
            r = c.models.generate_content(model=mdl, contents="Reply with the single word OK")
            print("TEXT OK", mdl, repr((r.text or "")[:20]))
        except Exception as e:
            print("TEXT FAIL", mdl, str(e)[:200])
    section("tts")
    for mdl in ["gemini-3.8-flash-tts", "gemini-3.8-flash-lite-tts", "gemini-3.1-flash-tts-preview", "gemini-2.5-flash-preview-tts"]:
        for voice in ["Charon", "Orus"]:
            try:
                r = c.models.generate_content(
                    model=mdl,
                    contents="Narrate in a deep, calm, suspenseful Hindi documentary voice: " + LINE,
                    config=types.GenerateContentConfig(
                        response_modalities=["AUDIO"],
                        speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
                            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice)))))
                part = r.candidates[0].content.parts[0]
                data = part.inline_data.data
                if isinstance(data, str):
                    data = base64.b64decode(data)
                p = OUT / f"{mdl}_{voice}.wav"
                with wave.open(str(p), "wb") as w:
                    w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000); w.writeframes(data)
                print("TTS OK", mdl, voice, part.inline_data.mime_type, len(data), "bytes")
            except Exception as e:
                print("TTS FAIL", mdl, voice, str(e)[:300])


async def edge():
    import edge_tts
    section("edge-tts")
    for v in ["hi-IN-MadhurNeural", "hi-IN-SwaraNeural"]:
        try:
            await edge_tts.Communicate(LINE, v, rate="-5%").save(str(OUT / f"edge_{v}.mp3"))
            print("EDGE OK", v, (OUT / f"edge_{v}.mp3").stat().st_size)
        except Exception as e:
            print("EDGE FAIL", v, repr(e)[:300])
    vs = await edge_tts.list_voices()
    print("multilingual/hindi voices:", [x["ShortName"] for x in vs if x["ShortName"].startswith("hi-") or "Multilingual" in x["ShortName"]])


if __name__ == "__main__":
    try:
        gemini()
    except Exception:
        traceback.print_exc()
    try:
        asyncio.run(edge())
    except Exception:
        traceback.print_exc()
    section("env")
    from PIL import features
    print("pillow raqm:", features.check("raqm"))
    print(subprocess.run(["ffmpeg", "-hide_banner", "-buildconf"], capture_output=True, text=True).stdout.count("harfbuzz"), "harfbuzz mentions")
    print(subprocess.run(["ffmpeg", "-hide_banner", "-version"], capture_output=True, text=True).stdout.splitlines()[0])
