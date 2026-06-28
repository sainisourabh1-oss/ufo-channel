"""
Assembles the long-form video with ffmpeg:
  stills -> Ken Burns pan/zoom, small on-screen source labels, concat,
  narration audio (+ optional ducked background music).

No burned-in narration captions (removed by request).
"""
import subprocess
from pathlib import Path
import requests
from PIL import Image
from .settings import CONFIG, workdir, ROOT

VID = CONFIG["video"]
W, H, FPS = VID["width"], VID["height"], VID["fps"]
SHOW_LABELS = VID.get("source_labels", True)   # small source credit per shot
FONT = "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf"

# Wikimedia (and many sites) REJECT requests without a descriptive User-Agent.
# This header is the key fix for the black-screen problem.
HEADERS = {
    "User-Agent": "ufo-channel/1.0 (educational documentary project; contact legendshipper@gmail.com)"
}


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
    """Download an image, follow redirects, normalise any format to clean JPG.
    Returns None (and logs why) if anything fails, so the slate fallback kicks in."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=90, allow_redirects=True)
        r.raise_for_status()
        raw = dest.with_suffix(".raw")
        raw.write_bytes(r.content)
        # Image.open validates it's a real image AND lets us accept png/tiff/webp.
        with Image.open(raw) as im:
            im.convert("RGB").save(dest, "JPEG", quality=90)
        raw.unlink(missing_ok=True)
        print(f"[assemble] fetched OK: {url}  ({dest.stat().st_size} bytes)")
        return dest
    except Exception as e:
        print(f"[assemble] could NOT fetch {url}  ->  {e}")
        return None


def _escape(text: str) -> str:
    return text.replace(":", r"\:").replace("'", r"\u2019")


def _kenburns_clip(img: Path, seconds: float, label: str, out: Path):
    frames = max(1, int(seconds * FPS))
    vf = (f"scale={W*2}:-2,"
          f"zoompan=z='min(zoom+0.0008,1.25)':d={frames}:s={W}x{H}:fps={FPS}")
    if SHOW_LABELS and label:
        vf += (f",drawtext=fontfile={FONT}:text='{_escape(label)}':"
               f"x=40:y=H-80:fontsize=34:fontcolor=white:box=1:"
               f"boxcolor=black@0.55:boxborderw=12")
    _run(["ffmpeg", "-y", "-loop", "1", "-i", str(img), "-t", f"{seconds:.2f}",
          "-vf", vf, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS), str(out)])


def assemble(video_id: str, script: dict, narration_mp3: Path) -> Path:
    wd = workdir(video_id)
    shots = script["shot_list"]
    total = _duration(narration_mp3)
    per = total / max(1, len(shots))

    fetched = 0
    clips = []
    for i, shot in enumerate(shots):
        img = wd / f"shot_{i:02}.jpg"
        url = shot.get("url") or shot.get("visual_url")
        ok = _download(url, img) if url and str(url).startswith("http") else None
        if ok:
            fetched += 1
        else:
            _run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=0x0a0a12:s={W}x{H}",
                  "-frames:v", "1", str(img)])
        clip = wd / f"clip_{i:02}.mp4"
        _kenburns_clip(img, per, shot.get("source_label", ""), clip)
        clips.append(clip)
    print(f"[assemble] images fetched: {fetched}/{len(shots)}")

    concat_list = wd / "concat.txt"
    concat_list.write_text("".join(f"file '{c}'\n" for c in clips), encoding="utf-8")
    silent = wd / "silent.mp4"
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
          "-c", "copy", str(silent)])

    music = next(iter(sorted((ROOT / "assets" / "music").glob("*.mp3"))), None)
    out = wd / "long.mp4"
    if music:
        filt = ("[2:a]volume=0.18[m];"
                "[1:a][m]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=300[a]")
        _run(["ffmpeg", "-y", "-i", str(silent), "-i", str(narration_mp3), "-i", str(music),
              "-filter_complex", filt, "-map", "0:v", "-map", "[a]",
              "-shortest", "-c:v", "libx264", "-c:a", "aac", str(out)])
    else:
        _run(["ffmpeg", "-y", "-i", str(silent), "-i", str(narration_mp3),
              "-map", "0:v", "-map", "1:a",
              "-shortest", "-c:v", "libx264", "-c:a", "aac", str(out)])

    print(f"[assemble] wrote {out}")
    return out
