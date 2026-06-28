"""Hindi narration via Google Cloud Text-to-Speech (Chirp 3: HD)."""
from pathlib import Path
from google.cloud import texttospeech as tts
from .settings import CONFIG, workdir

V = CONFIG["voice"]
MAX_BYTES = 4500  # stay under the 5000-byte request limit


def _chunks(text: str):
    out, cur = [], ""
    for sentence in text.replace("।", "।\n").splitlines():
        if len((cur + sentence).encode("utf-8")) > MAX_BYTES and cur:
            out.append(cur); cur = ""
        cur += sentence + " "
    if cur.strip():
        out.append(cur)
    return out


def _synthesize(client, text, voice_name):
    voice = tts.VoiceSelectionParams(language_code="hi-IN", name=voice_name)
    audio = tts.AudioConfig(audio_encoding=tts.AudioEncoding.MP3,
                            speaking_rate=V["speaking_rate"])
    resp = client.synthesize_speech(
        input=tts.SynthesisInput(text=text), voice=voice, audio_config=audio)
    return resp.audio_content


def narrate(video_id: str, script_hi: str) -> Path:
    client = tts.TextToSpeechClient()
    voice_name = V["voice_name"]
    parts = []
    for i, chunk in enumerate(_chunks(script_hi)):
        try:
            parts.append(_synthesize(client, chunk, voice_name))
        except Exception as e:
            print(f"[narrate] {voice_name} failed ({e}); falling back to {V['fallback_voice']}")
            voice_name = V["fallback_voice"]
            parts.append(_synthesize(client, chunk, voice_name))

    out = workdir(video_id) / "narration.mp3"
    with open(out, "wb") as f:
        for p in parts:
            f.write(p)
    print(f"[narrate] wrote {out} using {voice_name}")
    return out
