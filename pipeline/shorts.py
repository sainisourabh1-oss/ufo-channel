"""Cut 2-3 vertical Shorts from the long video and add the end CTA."""
import subprocess
from pathlib import Path
from .settings import CONFIG, workdir

S = CONFIG["shorts"]


def _run(cmd):
    subprocess.run(cmd, check=True)


def make_shorts(video_id: str, long_mp4: Path) -> list[Path]:
    if not S.get("enabled"):
        return []
    wd = workdir(video_id)
    outs = []
    # Simple, reliable strategy: take the opening hook window(s).
    # (The hook is engineered to be the strongest moment, so it makes the best Short.)
    starts = [0, S["max_seconds"], S["max_seconds"] * 2][: S["count"]]
    for i, start in enumerate(starts):
        out = wd / f"short_{i:02}.mp4"
        # crop to vertical 9:16, scale, and keep it under the max length
        vf = (f"crop=ih*9/16:ih,scale={S['width']}:{S['height']},"
              f"drawtext=text='{S['end_cta']}':x=(w-text_w)/2:y=h-260:"
              f"fontsize=44:fontcolor=white:box=1:boxcolor=black@0.6:"
              f"boxborderw=16:enable='gte(t,{S['max_seconds']-6})'")
        _run(["ffmpeg", "-y", "-ss", str(start), "-t", str(S["max_seconds"]),
              "-i", str(long_mp4), "-vf", vf, "-c:v", "libx264", "-c:a", "aac", str(out)])
        outs.append(out)
    print(f"[shorts] made {len(outs)} shorts")
    return outs
