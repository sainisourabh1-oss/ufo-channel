"""
Assembles the long-form video with ffmpeg:
  stills -> Ken Burns pan/zoom, on-screen source labels, concat,
  narration audio + ducked background music, burned-in Hindi captions.

This is the stage most likely to need small tuning on the first real run
(fonts, exact filter syntax). It is written to be readable and adjustable.
"""
import json
import shutil
import subprocess
from pathlib import Path
import requests
from .settings import CONFIG, workdir, ROOT

VID = CONFIG["video"]
W, H, FPS = VID["width"], VID["height"], VID["fps"]
# A font that renders Devanagari. On the GitHub Ubuntu runner install with:
#   sudo apt-get install -y fonts-noto-devanagari
FONT = "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf"


def _run(cmd):
    print("[ffmpeg]", " ".join(str(c) for c in cmd[:6]), "...")
    subprocess.run(cmd, check=True)


def _duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nk=1:nw=1", str(path)],
        capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def _download(url: str, dest: Path) -> Path | None:
    try:
        r = requests.get(url, timeout=60); r.raise_for_status()
        dest.write_bytes(r.content)
        return dest
    except Exception as e:
        print(f"[assemble] could not fetch {url}: {e}")
        return None


def _escape(text: str) -> str:
    return text.replace(":", r"\:").replace("'", r"\u2019")


def _kenburns_clip(img: Path, seconds: float, label: str, out: Path):
    frames = max(1, int(seconds * FPS))
    drawtext = (f"drawtext=fontfile={FONT}:text='{_escape(label)}':"
                f"x=40:y=H-80:fontsize=34:fontcolor=white:box=1:"
                f"boxcolor=black@0.55:boxborderw=12")
    vf = (f"scale={W*2}:-1,"
          f"zoompan=z='min(zoom+0.0008,1.25)':d={frames}:s={W}x{H}:fps={FPS},"
          f"{drawtext}")
    _run(["ffmpeg", "-y", "-loop", "1", "-i", str(img), "-t", f"{seconds:.2f}",
          "-vf", vf, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS), str(out)])


def assemble(video_id: str, script: dict, narration_mp3: Path, srt: Path) -> Path:
    wd = workdir(video_id)
    shots = script["shot_list"]
    total = _duration(narration_mp3)
    per = total / max(1, len(shots))

    # 1) Build one Ken Burns clip per shot.
    clips = []
    for i, shot in enumerate(shots):
        img = wd / f"shot_{i:02}.jpg"
        url = shot.get("url") or shot.get("visual_url")
        ok = _download(url, img) if url and url.startswith("http") else None
        if not ok:
            # fallback: neutral dark slate so the run still completes
            _run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=0x0a0a12:s={W}x{H}",
                  "-frames:v", "1", str(img)])
        clip = wd / f"clip_{i:02}.mp4"
        _kenburns_clip(img, per, shot.get("source_label", ""), clip)
        clips.append(clip)

    # 2) Concatenate the silent video clips.
    concat_list = wd / "concat.txt"
    concat_list.write_text("".join(f"file '{c}'\n" for c in clips), encoding="utf-8")
    silent = wd / "silent.mp4"
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
          "-c", "copy", str(silent)])

    # 3) Mux narration (+ ducked music if present) and burn captions.
    music = next(iter(sorted((ROOT / "assets" / "music").glob("*.mp3"))), None)
    out = wd / "long.mp4"
    subs = f"subtitles={srt}:force_style='FontName=Noto Sans Devanagari,FontSize=20,Outline=2'"

    if music:
        # narration ducks the music via sidechain compression
        filt = ("[2:a]volume=0.18[m];"
                "[1:a][m]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=300[a]")
        _run(["ffmpeg", "-y", "-i", str(silent), "-i", str(narration_mp3), "-i", str(music),
              "-filter_complex", filt, "-map", "0:v", "-map", "[a]",
              "-vf", subs, "-shortest", "-c:v", "libx264", "-c:a", "aac", str(out)])
    else:
        _run(["ffmpeg", "-y", "-i", str(silent), "-i", str(narration_mp3),
              "-map", "0:v", "-map", "1:a", "-vf", subs,
              "-shortest", "-c:v", "libx264", "-c:a", "aac", str(out)])

    print(f"[assemble] wrote {out}")
    return out
