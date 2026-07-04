"""
Assembles ONE clean long-form video with ffmpeg:
  - a new image every few seconds (config: video.seconds_per_image)
  - continuous slow zoom-in on every image (motion)
  - every unique image used before any repeat; never the same image twice in a row
  - narration + soft, fading background music (assets/music/*.mp3)
  - NO subtitles, NO source labels (clean screen)
"""
import math
import random
import subprocess
from pathlib import Path
import requests
from PIL import Image
from .settings import CONFIG, workdir, ROOT

VID = CONFIG["video"]
W, H, FPS = VID["width"], VID["height"], VID["fps"]
SECONDS_PER_IMAGE = VID.get("seconds_per_image", 3)
HEADERS = {"User-Agent": "ufo-channel/1.0 (educational documentary; contact legendshipper@gmail.com)"}


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
        r = requests.get(url, headers=HEADERS, timeout=90, allow_redirects=True)
        r.raise_for_status()
        raw = dest.with_suffix(".raw")
        raw.write_bytes(r.content)
        with Image.open(raw) as im:
            im.convert("RGB").save(dest, "JPEG", quality=90)
        raw.unlink(missing_ok=True)
        return dest
    except Exception as e:
        print(f"[assemble] could NOT fetch {url}  ->  {e}")
        return None


def _zoom_clip(img: Path, seconds: float, out: Path):
    """One image -> a clip of `seconds` with a continuous slow zoom-in."""
    frames = max(1, int(seconds * FPS))
    vf = (f"scale={W*2}:-2,"
          f"zoompan=z='min(zoom+0.0015,1.5)':d={frames}:s={W}x{H}:fps={FPS},"
          f"setsar=1")
    _run(["ffmpeg", "-y", "-loop", "1", "-i", str(img), "-t", f"{seconds:.2f}",
          "-vf", vf, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS), str(out)])


def _build_order(unique_imgs, n_segments):
    """Sequence of images: every unique used before any repeat, never twice in a row."""
    if len(unique_imgs) == 1:
        return unique_imgs * n_segments
    order = []
    while len(order) < n_segments:
        cycle = unique_imgs[:]
        random.shuffle(cycle)
        for img in cycle:
            if order and img == order[-1]:
                continue
            order.append(img)
            if len(order) >= n_segments:
                break
    return order


def assemble(video_id: str, script: dict, narration_mp3: Path) -> Path:
    wd = workdir(video_id)

    # 1. Download each UNIQUE image once.
    urls = []
    for shot in script["shot_list"]:
        u = shot.get("url")
        if u and u not in urls:
            urls.append(u)
    local = []
    for i, u in enumerate(urls):
        dest = wd / f"img_{i:02}.jpg"
        if _download(u, dest):
            local.append(dest)
    if not local:                                   # safety: never all-black
        slate = wd / "slate.jpg"
        _run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=0x0a0a12:s={W}x{H}",
              "-frames:v", "1", str(slate)])
        local = [slate]
    print(f"[assemble] unique images ready: {len(local)}")

    # 2. Timeline: a new image every SECONDS_PER_IMAGE, filling the narration.
    total = _duration(narration_mp3)
    n_segments = max(1, math.ceil(total / SECONDS_PER_IMAGE))
    order = _build_order(local, n_segments)

    clips = []
    for i, img in enumerate(order):
        secs = SECONDS_PER_IMAGE
        if i == len(order) - 1:                     # last clip covers any remainder
            secs = max(1.0, total - SECONDS_PER_IMAGE * (len(order) - 1)) + 0.5
        clip = wd / f"clip_{i:04}.mp4"
        _zoom_clip(img, secs, clip)
        clips.append(clip)

    concat_list = wd / "concat.txt"
    concat_list.write_text("".join(f"file '{c}'\n" for c in clips), encoding="utf-8")
    silent = wd / "silent.mp4"
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
          "-c", "copy", str(silent)])

    # 3. Narration + soft fading background music (if a track exists).
    music = next(iter(sorted((ROOT / "assets" / "music").glob("*.mp3"))), None)
    out = wd / "long.mp4"
    if music:
        fade_out_start = max(0.0, total - 4)
        filt = (f"[2:a]volume=0.12,afade=t=in:st=0:d=3,"
                f"afade=t=out:st={fade_out_start:.2f}:d=4[m];"
                f"[1:a][m]amix=inputs=2:duration=first:normalize=0[a]")
        _run(["ffmpeg", "-y", "-i", str(silent), "-i", str(narration_mp3),
              "-stream_loop", "-1", "-i", str(music),
              "-filter_complex", filt, "-map", "0:v", "-map", "[a]",
              "-shortest", "-c:v", "libx264", "-c:a", "aac", str(out)])
    else:
        print("[assemble] no music file in assets/music/ — narration only")
        _run(["ffmpeg", "-y", "-i", str(silent), "-i", str(narration_mp3),
              "-map", "0:v", "-map", "1:a",
              "-shortest", "-c:v", "libx264", "-c:a", "aac", str(out)])

    print(f"[assemble] wrote {out}")
    return out
