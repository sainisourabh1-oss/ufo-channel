"""
Cinematic renderer (ffmpeg + libass).

Per scene: every image gets its own motion (zoom in / zoom out / pan in 4 directions),
images are joined with varied transitions, scenes dip through black. On top:
colour grade, vignette, film grain, animated title / chapter / lower-third cards
(ASS, so Devanagari is shaped correctly by harfbuzz), and a synthesized ambient
score with whooshes on every scene change, ducked under the narration.

Works for 16:9 long videos and 9:16 Shorts (pass width/height).
"""
import math
import random
import subprocess
from pathlib import Path
from PIL import Image, ImageFilter, ImageOps, ImageEnhance
from .settings import CONFIG, ROOT

FONTS = ROOT / "assets" / "fonts"
FONT = "Noto Sans Devanagari"
TRANS = 0.6                      # seconds of overlap per in-scene transition
SCENE_GAP = 0.45                 # silence between scenes (narration) == dip to black
XFADES = ["fade", "dissolve", "smoothleft", "smoothright", "smoothup", "circleopen",
          "fadeblack", "radial", "zoomin", "slideleft", "hblur", "fadegrays"]
MOTIONS = ["zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "pan_down"]


def run(cmd, quiet=True):
    import time
    t0 = time.time()
    r = subprocess.run([str(c) for c in cmd], capture_output=quiet, text=True)
    print(f"[ffmpeg] {time.time() - t0:6.1f}s", " ".join(str(c) for c in cmd[:6])[:90], flush=True)
    if r.returncode != 0:
        print((r.stderr or "")[-3000:])
        raise RuntimeError(f"ffmpeg failed ({r.returncode})")


def duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "default=nk=1:nw=1", str(path)], capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


# ---------------------------------------------------------------- images
def prepare_image(src: Path, dest: Path, W: int, H: int, margin=1.22, seed=0):
    """Fit any image into a (W*margin x H*margin) canvas: subject fully visible on a
    blurred, darkened copy of itself (so portrait scans and small photos look intentional)."""
    rnd = random.Random(seed)
    cw, ch = int(W * margin) // 2 * 2, int(H * margin) // 2 * 2
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        bg = ImageOps.fit(im, (cw, ch), Image.LANCZOS).filter(ImageFilter.GaussianBlur(40))
        bg = ImageEnhance.Brightness(bg).enhance(0.45)
        ratio = min(cw / im.width, ch / im.height)
        # landscape images that nearly match the frame fill it; others sit on the blur
        if abs((im.width / im.height) - (cw / ch)) < 0.35:
            fg = ImageOps.fit(im, (cw, ch), Image.LANCZOS)
            bg.paste(fg, (0, 0))
        else:
            fg = im.resize((max(1, int(im.width * ratio)), max(1, int(im.height * ratio))), Image.LANCZOS)
            bg.paste(fg, ((cw - fg.width) // 2, (ch - fg.height) // 2))
        # gentle per-image grade variation so consecutive stills don't look stamped
        bg = ImageEnhance.Contrast(bg).enhance(1.0 + rnd.uniform(0.02, 0.12))
        bg.save(dest, "JPEG", quality=92)
    return dest


def _motion_filter(kind: str, secs: float, W: int, H: int, fps: int) -> str:
    """prepared image is W*1.22 x H*1.22; animate a W x H window across it."""
    n = max(2, int(secs * fps))
    cw, ch = int(W * 1.22) // 2 * 2, int(H * 1.22) // 2 * 2
    p = f"(n/{n})"                                   # 0 -> 1 progress
    ease = f"(0.5-0.5*cos(PI*{p}))"                  # ease in-out
    if kind in ("zoom_in", "zoom_out"):
        # zoompan on a 2x-upscaled canvas (sub-pixel steps -> no jitter). A per-frame
        # `scale` that shrinks crashes ffmpeg, so zoom is done here, not with scale eval=frame.
        e = f"(0.5-0.5*cos(PI*in/{n}))"
        z = f"1.22+0.2*{e}" if kind == "zoom_in" else f"1.42-0.2*{e}"
        return (f"scale={W * 2}:-2:flags=bicubic,"
                f"zoompan=z='{z}':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d=1:s={W}x{H}:fps={fps}")
    dx, dy = cw - W, ch - H
    if kind == "pan_left":
        x, y = f"{dx}*(1-{ease})", f"{dy}/2"
    elif kind == "pan_right":
        x, y = f"{dx}*{ease}", f"{dy}/2"
    elif kind == "pan_up":
        x, y = f"{dx}/2", f"{dy}*(1-{ease})"
    else:
        x, y = f"{dx}/2", f"{dy}*{ease}"
    return f"crop={W}:{H}:x='{x}':y='{y}'"


def render_scene(images: list, secs: float, out: Path, W: int, H: int, fps: int, rnd: random.Random,
                 dip_in=True, dip_out=True):
    """images -> one scene clip of exactly `secs` seconds."""
    n = len(images)
    seg = (secs + (n - 1) * TRANS) / n if n > 1 else secs
    cmd = ["ffmpeg", "-y", "-hide_banner"]
    for img in images:
        cmd += ["-loop", "1", "-framerate", str(fps), "-t", f"{seg:.3f}", "-i", str(img)]
    parts, last_motion = [], None
    for i in range(n):
        m = rnd.choice([k for k in MOTIONS if k != last_motion]); last_motion = m
        parts.append(f"[{i}:v]{_motion_filter(m, seg, W, H, fps)},setsar=1,fps={fps},format=yuv420p[v{i}]")
    cur = "v0"
    for i in range(1, n):
        off = i * (seg - TRANS)
        parts.append(f"[{cur}][v{i}]xfade=transition={rnd.choice(XFADES)}:duration={TRANS}:offset={off:.3f}[x{i}]")
        cur = f"x{i}"
    fades = []
    if dip_in:
        fades.append("fade=t=in:st=0:d=0.35")
    if dip_out:
        fades.append(f"fade=t=out:st={max(0, secs - 0.35):.3f}:d=0.35")
    # look: cool cinematic grade (eq per-channel gamma; colorbalance is ~3x slower), vignette,
    # live luma grain. Done per scene because these filters are single-threaded and scenes
    # render in parallel.
    look = ["eq=contrast=1.07:saturation=0.86:gamma=0.97:gamma_r=0.97:gamma_b=1.04",
            "vignette=angle=PI/4.6", "noise=c0s=7:c0f=t"]
    parts.append(f"[{cur}]trim=duration={secs:.3f},setpts=PTS-STARTPTS," + ",".join(look + fades) + "[out]")
    cmd += ["-filter_complex", ";".join(parts), "-map", "[out]", "-r", str(fps),
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "16", "-pix_fmt", "yuv420p", str(out)]
    run(cmd)
    return out


# ---------------------------------------------------------------- overlay text (ASS)
def _t(s: float) -> str:
    s = max(0.0, s)
    return f"{int(s // 3600)}:{int(s % 3600 // 60):02}:{s % 60:05.2f}"


def _esc(text: str) -> str:
    # "·" / "•" are not in Noto Devanagari (they render as broken glyphs) -> "|"
    return ((text or "").replace("\\", "").replace("{", "(").replace("}", ")")
            .replace("·", "|").replace("•", "|").replace("\n", "\\N"))


class Overlay:
    def __init__(self, W, H):
        self.W, self.H, self.events = W, H, []
        s = H / 1080
        big = int(128 * s) if W > H else int(118 * W / 1080)
        self.header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Glow,{FONT} Black,{big},&H0039FF7A,&H000000FF,&H0039FF7A,&H00000000,1,0,0,0,100,100,0,0,1,10,0,5,80,80,40,1
Style: Title,{FONT} Black,{big},&H00FFFFFF,&H000000FF,&H00101010,&H00000000,1,0,0,0,100,100,0,0,1,3,0,5,80,80,40,1
Style: Kicker,{FONT} Bold,{int(big*0.40)},&H0039FF7A,&H000000FF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,2,0,5,80,80,40,1
Style: Chapter,{FONT} Black,{int(big*0.78)},&H00FFFFFF,&H000000FF,&H00000000,&H64000000,1,0,0,0,100,100,0,0,1,4,2,5,80,80,40,1
Style: Lower,{FONT} Bold,{int(54*s) if W > H else int(50*W/1080)},&H00FFFFFF,&H000000FF,&H00000000,&H78000000,1,0,0,0,100,100,0,0,3,14,0,1,60,60,{int(70*s)},1
Style: Cap,{FONT} Black,{int(80*W/1080) if H > W else int(54*s)},&H00FFFFFF,&H000000FF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,6,3,2,60,60,{int(H*0.28) if H > W else int(90*s)},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    def add(self, layer, start, end, style, text):
        self.events.append(f"Dialogue: {layer},{_t(start)},{_t(end)},{style},,0,0,0,,{text}")

    def dim(self, start, end, alpha="70"):
        """semi-transparent black full-frame plate behind a card (ASS vector drawing)."""
        self.add(0, start, end, "Lower",
                 f"{{\\an7\\pos(0,0)\\p1\\bord0\\shad0\\1c&H000000&\\1a&H{alpha}&\\fad(350,350)}}"
                 f"m 0 0 l {self.W} 0 l {self.W} {self.H} l 0 {self.H}{{\\p0}}")

    def title_card(self, start, end, kicker, title):
        self.dim(start, end, "60")
        cy = self.H // 2
        self.add(0, start, end, "Glow", f"{{\\fad(500,400)\\blur12\\pos({self.W//2},{cy})}}{_esc(title)}")
        self.add(1, start, end, "Title", f"{{\\fad(500,400)\\pos({self.W//2},{cy})\\t(0,{int((end-start)*1000)},\\fscx106\\fscy106)}}{_esc(title)}")
        self.add(1, start + 0.3, end, "Kicker", f"{{\\fad(400,400)\\pos({self.W//2},{cy - int(self.H*0.12)})}}{_esc(kicker)}")

    def chapter(self, start, num, title, dur=2.6):
        self.dim(start, start + dur, "80")
        cy = self.H // 2
        self.add(2, start, start + dur, "Kicker", f"{{\\fad(250,350)\\pos({self.W//2},{cy - int(self.H*0.08)})}}{_esc(num)}")
        self.add(2, start, start + dur, "Chapter", f"{{\\fad(250,350)\\pos({self.W//2},{cy + int(self.H*0.02)})\\t(0,{int(dur*1000)},\\fscx104\\fscy104)}}{_esc(title)}")

    def lower_third(self, start, text, dur=4.5):
        x0, y = -600, self.H - int(self.H * 0.09)
        self.add(2, start, start + dur, "Lower",
                 f"{{\\an1\\move({x0},{y},60,{y},0,350)\\fad(0,400)}}{_esc(text)}")

    def caption(self, start, end, text):
        self.add(3, start, end, "Cap", f"{{\\fad(60,0)\\t(0,120,\\fscx108\\fscy108)\\t(120,220,\\fscx100\\fscy100)}}{_esc(text)}")

    def save(self, path: Path):
        path.write_text(self.header + "\n".join(self.events) + "\n", encoding="utf-8")
        return path


# ---------------------------------------------------------------- sound design
def make_score(total: float, out: Path, rnd: random.Random):
    """Ambient drone: two detuned low tones + a slow-breathing fifth + filtered noise bed,
    with reverb. A different root/tempo every video so it never sounds stamped."""
    root = rnd.choice([41.2, 43.65, 46.25, 49.0, 51.9, 55.0])
    br = rnd.uniform(0.03, 0.08)
    expr = (f"0.22*sin(2*PI*{root}*t)+0.16*sin(2*PI*{root*1.003:.3f}*t)"
            f"+0.10*sin(2*PI*{root*1.5:.3f}*t)*(0.5+0.5*sin(2*PI*{br:.3f}*t))"
            f"+0.05*sin(2*PI*{root*4:.3f}*t)*(0.5+0.5*sin(2*PI*{br*1.7:.3f}*t+1))")
    run(["ffmpeg", "-y", "-hide_banner",
         "-f", "lavfi", "-i", f"aevalsrc='{expr}':s=44100:d={total + 1:.2f}",
         "-f", "lavfi", "-i", f"anoisesrc=color=brown:amplitude=0.25:d={total + 1:.2f}:r=44100",
         "-filter_complex",
         "[1:a]lowpass=f=420,volume=0.5[n];[0:a][n]amix=inputs=2:normalize=0,"
         "aecho=0.8:0.7:600|1100:0.25|0.18,lowpass=f=2400,"
         f"afade=t=in:d=3,afade=t=out:st={max(0, total - 4):.2f}:d=4,"
         "aformat=channel_layouts=stereo,volume=0.9[a]",
         "-map", "[a]", "-c:a", "pcm_s16le", str(out)])
    return out


def make_whoosh(out: Path):
    run(["ffmpeg", "-y", "-hide_banner", "-f", "lavfi", "-i", "anoisesrc=color=pink:amplitude=0.6:d=1.4:r=44100",
         "-af", "bandpass=f=900:width_type=o:w=2,afade=t=in:d=0.55:curve=exp,afade=t=out:st=0.6:d=0.8,"
         "aecho=0.6:0.5:120:0.3,aformat=channel_layouts=stereo,volume=0.8", "-c:a", "pcm_s16le", str(out)])
    return out


# ---------------------------------------------------------------- full video
def render_video(work: Path, scenes: list, narration: Path, scene_starts: list, total: float,
                 overlay: Overlay, W: int, H: int, out: Path, seed: int, grain=True):
    """scenes: [{'images': [Path...], 'secs': float}] covering 0..total contiguously."""
    fps = CONFIG["video"]["fps"]
    rnd = random.Random(seed)
    clips = []
    from concurrent.futures import ThreadPoolExecutor
    import os
    jobs = []
    for i, sc in enumerate(scenes):
        clip = work / f"scene_{i:03}.mp4"
        jobs.append((sc["images"], sc["secs"], clip, W, H, fps, random.Random(rnd.random()),
                     i > 0, i < len(scenes) - 1))
        clips.append(clip)
    with ThreadPoolExecutor(max_workers=max(2, (os.cpu_count() or 4) // 2)) as ex:
        list(ex.map(lambda j: render_scene(*j[:7], dip_in=j[7], dip_out=j[8]), jobs))
    lst = work / "scenes.txt"
    lst.write_text("".join(f"file '{c.as_posix()}'\n" for c in clips), encoding="utf-8")
    joined = work / "joined.mp4"
    run(["ffmpeg", "-y", "-hide_banner", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", joined])

    score = make_score(total, work / "score.wav", rnd)
    whoosh = make_whoosh(work / "whoosh.wav")
    ass = overlay.save(work / "overlay.ass")

    # audio: narration + ducked score + a whoosh into every scene change
    hits = [max(0.0, s - 0.55) for s in scene_starts[1:]][:60]
    a_inputs = ["-i", narration, "-i", score] + sum([["-i", whoosh] for _ in hits], [])
    fa = ["[1:a]aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo,asplit=2[nar][key]",
          "[2:a]aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo[sc]",
          "[sc][key]sidechaincompress=threshold=0.02:ratio=6:attack=30:release=600[duck]"]
    mix = ["[nar]", "[duck]"]
    for k, t0 in enumerate(hits):
        d = int(t0 * 1000)
        fa.append(f"[{k + 3}:a]aformat=sample_fmts=fltp:channel_layouts=stereo,adelay={d}|{d},volume=0.55[w{k}]")
        mix.append(f"[w{k}]")
    fa.append("".join(mix) + f"amix=inputs={len(mix)}:duration=first:normalize=0,"
              "alimiter=limit=0.93,loudnorm=I=-14:TP=-1.5:LRA=11[aout]")

    ass_path = ass.as_posix().replace(":", "\\:")
    fonts_path = FONTS.as_posix().replace(":", "\\:")
    # `ass` filter with shaping=complex: harfbuzz shaping, required for correct Devanagari
    vf = f"ass=filename='{ass_path}':fontsdir='{fonts_path}':shaping=complex"

    run(["ffmpeg", "-y", "-hide_banner", "-i", joined] + a_inputs +
        ["-filter_complex", ";".join(fa) + f";[0:v]{vf}[vout]",
         "-map", "[vout]", "-map", "[aout]", "-t", f"{total:.3f}",
         "-c:v", "libx264", "-preset", CONFIG["video"].get("preset", "veryfast"),
         "-crf", str(CONFIG["video"].get("crf", 21)), "-pix_fmt", "yuv420p", "-r", str(fps),
         "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out])
    print(f"[render] wrote {out} ({duration(out):.1f}s)")
    return out
