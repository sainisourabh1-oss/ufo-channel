"""
Narration with a provider chain (config voice.providers):
  edge    - Microsoft neural voice hi-IN-MadhurNeural (free, no key, native Hindi)
  gcp     - Google Cloud TTS Chirp3-HD (only if billing is re-enabled on the GCP project)

Probed 2026-11-15: this Gemini key has NO TTS model (all 404) and 0 free image quota,
so Gemini is used for writing only.

A whole video is voiced by ONE provider: if any piece fails on a provider, every
piece is redone on the next one, so the narrator never changes mid-video.
Each piece comes back as a 44.1 kHz mono WAV with edge silences trimmed.
"""
import asyncio
import os
import subprocess
import time
import wave
from pathlib import Path
from .settings import CONFIG

V = CONFIG["voice"]


def _norm(src: Path, dst: Path, raw_pcm_rate: int | None = None):
    pre = ["-f", "s16le", "-ar", str(raw_pcm_rate), "-ac", "1"] if raw_pcm_rate else []
    af = ("silenceremove=start_periods=1:start_threshold=-50dB:start_silence=0.05,"
          "areverse,silenceremove=start_periods=1:start_threshold=-50dB:start_silence=0.15,areverse,"
          "highpass=f=70,acompressor=threshold=-20dB:ratio=2.5:attack=10:release=200,aresample=44100")
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *pre, "-i", str(src),
                    "-af", af, "-ac", "1", "-c:a", "pcm_s16le", str(dst)], check=True)
    return dst


def _split(text: str, limit: int):
    """Split on sentence ends (। ? !) into pieces under `limit` chars."""
    sents, cur, out = [], "", []
    for ch in text:
        cur += ch
        if ch in "।?!":
            sents.append(cur.strip()); cur = ""
    if cur.strip():
        sents.append(cur.strip())
    cur = ""
    for s in sents:
        if cur and len(cur) + len(s) > limit:
            out.append(cur.strip()); cur = ""
        cur += " " + s
    if cur.strip():
        out.append(cur.strip())
    return out


def _concat(pieces: list, dst: Path):
    lst = dst.with_suffix(".txt")
    lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in pieces), encoding="utf-8")
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
                    "-i", str(lst), "-c", "copy", str(dst)], check=True)
    return dst


# ---------------------------------------------------------------- providers
def _edge_piece(text: str, out: Path):
    import edge_tts
    last = None
    for attempt in range(5):
        try:
            mp3 = out.with_suffix(".mp3")
            asyncio.run(edge_tts.Communicate(text, V["edge_voice"], rate=V.get("edge_rate", "+0%"),
                                             pitch=V.get("edge_pitch", "+0Hz")).save(str(mp3)))
            if mp3.stat().st_size < 2000:
                raise RuntimeError("tiny audio")
            return _norm(mp3, out)
        except Exception as e:
            last = e
            print(f"[tts:edge] attempt {attempt + 1}: {repr(e)[:160]}")
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"edge tts failed: {last}")


def _gcp_piece(text: str, out: Path):
    from google.cloud import texttospeech as tts
    client = tts.TextToSpeechClient()
    resp = client.synthesize_speech(
        input=tts.SynthesisInput(text=text),
        voice=tts.VoiceSelectionParams(language_code="hi-IN", name=V["gcp_voice"]),
        audio_config=tts.AudioConfig(audio_encoding=tts.AudioEncoding.MP3))
    mp3 = out.with_suffix(".mp3"); mp3.write_bytes(resp.audio_content)
    return _norm(mp3, out)


PROVIDERS = {"edge": (_edge_piece, 2500), "gcp": (_gcp_piece, 1400)}


def _available(name):
    if name == "gcp":
        return bool(os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")) and \
            Path(os.environ["GOOGLE_APPLICATION_CREDENTIALS"]).exists() and \
            Path(os.environ["GOOGLE_APPLICATION_CREDENTIALS"]).stat().st_size > 50
    return True


def narrate_all(texts: list, work: Path, tag: str, providers=None):
    """texts -> list of WAV paths (one per text) + provider name used."""
    errors = []
    for name in providers or V["providers"]:
        if not _available(name):
            continue
        fn, limit = PROVIDERS[name]
        try:
            outs = []
            for i, text in enumerate(texts):
                pieces = []
                for j, chunk in enumerate(_split(text, limit)):
                    pieces.append(fn(chunk, work / f"{tag}_{i:02}_{j:02}_{name}.wav"))
                dst = work / f"{tag}_{i:02}.wav"
                outs.append(_concat(pieces, dst) if len(pieces) > 1 else pieces[0].replace(dst))
            print(f"[tts] {tag}: {len(outs)} parts voiced by {name}")
            return outs, name
        except Exception as e:
            errors.append(f"{name}: {e}")
            print(f"[tts] provider {name} failed for {tag} -> trying next ({str(e)[:200]})")
    raise RuntimeError("all TTS providers failed: " + " | ".join(errors))


def wav_seconds(p: Path) -> float:
    with wave.open(str(p)) as w:
        return w.getnframes() / w.getframerate()
