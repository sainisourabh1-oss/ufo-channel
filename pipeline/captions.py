"""Generate a timed Hindi SRT from the narration audio (for burned-in captions)."""
from pathlib import Path
from faster_whisper import WhisperModel
from .settings import workdir


def _ts(seconds: float) -> str:
    h = int(seconds // 3600); m = int((seconds % 3600) // 60)
    s = int(seconds % 60); ms = int((seconds - int(seconds)) * 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def captions(video_id: str, narration_mp3: Path) -> Path:
    # "small" keeps cloud CPU time low; bump to "medium" for accuracy.
    model = WhisperModel("small", device="cpu", compute_type="int8")
    segments, _ = model.transcribe(str(narration_mp3), language="hi", word_timestamps=True)

    out = workdir(video_id) / "captions.srt"
    with open(out, "w", encoding="utf-8") as f:
        for i, seg in enumerate(segments, 1):
            f.write(f"{i}\n{_ts(seg.start)} --> {_ts(seg.end)}\n{seg.text.strip()}\n\n")
    print(f"[captions] wrote {out}")
    return out
