"""
Channel art, drawn in code (no paid generators): neon-green saucer + tractor beam on a deep
starfield. Writes assets/brand/logo.png (800x800) and assets/brand/banner.png (2560x1440,
key content inside YouTube's 1546x423 safe area). Text via libass/harfbuzz (Hindi-correct).

    python -m tools.branding
"""
import math
import random
import subprocess
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter, ImageChops
from pipeline.render import FONTS, FONT

OUT = Path(__file__).resolve().parent.parent / "assets" / "brand"
GREEN = (122, 255, 57)
CYAN = (90, 230, 255)


def sky(W, H, seed=7, center=(0.5, 0.5)):
    rnd = random.Random(seed)
    im = Image.new("RGB", (W, H), (3, 5, 14))
    neb = Image.new("RGB", (W, H), (0, 0, 0))
    d = ImageDraw.Draw(neb)
    for c, n in (((30, 70, 50), 4), ((40, 25, 80), 4), ((10, 50, 90), 3)):
        for _ in range(n):
            x, y = rnd.uniform(0, W), rnd.uniform(0, H)
            r = rnd.uniform(0.18, 0.4) * max(W, H)
            d.ellipse((x - r, y - r * 0.6, x + r, y + r * 0.6), fill=c)
    im = ImageChops.add(im, neb.filter(ImageFilter.GaussianBlur(max(W, H) / 9)))
    # vignette
    vg = Image.new("L", (W, H), 0)
    ImageDraw.Draw(vg).ellipse((-W * 0.25, -H * 0.35, W * 1.25, H * 1.35), fill=255)
    vg = vg.filter(ImageFilter.GaussianBlur(max(W, H) / 7))
    im = Image.composite(im, Image.new("RGB", (W, H), (0, 0, 0)), vg)
    stars = Image.new("RGB", (W, H), (0, 0, 0))
    d = ImageDraw.Draw(stars)
    for _ in range(int(W * H / 1400)):
        x, y = rnd.uniform(0, W), rnd.uniform(0, H)
        b = int(rnd.uniform(90, 255) * rnd.random() ** 0.4)
        s = rnd.choice([0, 0, 0, 0.6, 1.0, 1.6])
        col = (b, b, min(255, b + 25))
        d.ellipse((x - s, y - s, x + s, y + s), fill=col) if s else d.point((x, y), fill=col)
    glow = stars.filter(ImageFilter.GaussianBlur(2))
    return ImageChops.add(ImageChops.add(im, stars), glow)


def glow_layer(size, draw_fn, blur, strength=1.0):
    layer = Image.new("RGB", size, (0, 0, 0))
    draw_fn(ImageDraw.Draw(layer))
    g = layer.filter(ImageFilter.GaussianBlur(blur))
    if strength != 1.0:
        g = Image.eval(g, lambda v: min(255, int(v * strength)))
    return layer, g


def saucer(im, cx, cy, w, beam_len, tilt=-6):
    """metallic saucer with glowing dome, rim lights, and a tractor beam."""
    W, H = im.size
    S = 3                                             # supersample for smooth edges
    big = Image.new("RGBA", (W * S, H * S), (0, 0, 0, 0))
    d = ImageDraw.Draw(big)
    X, Y, R = cx * S, cy * S, w * S / 2
    # beam (drawn first, behind) - soft cone
    beam = Image.new("L", (W * S, H * S), 0)
    bd = ImageDraw.Draw(beam)
    bw_top, bw_bot = R * 0.42, R * 1.25
    for i in range(40):
        a = int(110 * (1 - i / 40))
        f = i / 40
        bd.polygon([(X - bw_top * (1 - f * 0.2), Y + R * 0.18), (X + bw_top * (1 - f * 0.2), Y + R * 0.18),
                    (X + bw_bot * (1 - f * 0.5), Y + beam_len * S), (X - bw_bot * (1 - f * 0.5), Y + beam_len * S)],
                   fill=min(255, 30 + a))
    beam = beam.filter(ImageFilter.GaussianBlur(R * 0.08))
    beam_rgb = Image.new("RGBA", (W * S, H * S), GREEN + (0,))
    beam_rgb.putalpha(Image.eval(beam, lambda v: int(v * 0.85)))
    big = Image.alpha_composite(big, beam_rgb)
    d = ImageDraw.Draw(big)
    # hull: stacked ellipses light->dark for a metallic sheen
    for i in range(30):
        f = i / 30
        c = int(40 + 150 * (1 - abs(f - 0.35) * 1.6))
        c = max(30, min(210, c))
        hh = R * 0.30 * (1 - f * 0.55)
        d.ellipse((X - R * (1 - f * 0.08), Y - hh + f * R * 0.05, X + R * (1 - f * 0.08), Y + hh + f * R * 0.05),
                  fill=(c, c + 8, c + 18, 255))
    # rim band + lights
    d.ellipse((X - R, Y - R * 0.07, X + R, Y + R * 0.12), outline=(160, 255, 140, 255), width=int(R * 0.02))
    for k in range(9):
        ang = math.pi * (k + 0.5) / 9
        lx = X - R * 0.92 * math.cos(ang)
        ly = Y + R * 0.06 + R * 0.05 * math.sin(ang)
        rr = R * 0.035
        d.ellipse((lx - rr, ly - rr, lx + rr, ly + rr), fill=GREEN + (255,))
    # dome
    for i in range(20):
        f = i / 20
        c = (int(60 + 140 * f), int(170 + 80 * f), 255, 255)
        d.ellipse((X - R * 0.42 * (1 - f * 0.5), Y - R * 0.52 + f * R * 0.12,
                   X + R * 0.42 * (1 - f * 0.5), Y - R * 0.02), fill=c)
    d.ellipse((X - R * 0.16, Y - R * 0.44, X - R * 0.02, Y - R * 0.34), fill=(255, 255, 255, 200))
    big = big.rotate(tilt, center=(X, Y), resample=Image.BICUBIC)
    small = big.resize((W, H), Image.LANCZOS)
    # outer glow
    alpha = small.split()[3]
    glow = Image.new("RGB", (W, H), GREEN)
    glow_a = alpha.filter(ImageFilter.GaussianBlur(w * 0.06)).point(lambda v: int(v * 0.55))
    base = im.convert("RGBA")
    base = Image.alpha_composite(base, Image.merge("RGBA", (*glow.split(), glow_a)))
    return Image.alpha_composite(base, small).convert("RGB")


def text(img: Path, out: Path, events, W, H, styles):
    ass = out.with_suffix(".ass")
    ass.write_text(f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{styles}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
""" + "\n".join(f"Dialogue: {l},0:00:00.00,0:00:01.00,{s},,0,0,0,,{t}" for l, s, t in events) + "\n", encoding="utf-8")
    a = ass.resolve().as_posix().replace(":", "\\:")
    f = FONTS.as_posix().replace(":", "\\:")
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(img), "-vf",
                    f"ass=filename='{a}':fontsdir='{f}':shaping=complex", "-frames:v", "1", str(out)], check=True)
    ass.unlink()


def logo():
    W = H = 800
    im = sky(W, H, seed=11)
    # glowing ring
    ring = Image.new("RGB", (W, H), (0, 0, 0))
    ImageDraw.Draw(ring).ellipse((40, 40, W - 40, H - 40), outline=GREEN, width=14)
    im = ImageChops.add(im, ring.filter(ImageFilter.GaussianBlur(18)))
    im = ImageChops.add(im, ring)
    im = saucer(im, W * 0.5, H * 0.40, W * 0.66, beam_len=H * 0.42, tilt=-8)
    raw = OUT / "logo_raw.png"; im.save(raw)
    styles = (f"Style: G,{FONT} Black,120,&H0039FF7A,&H000000FF,&H0039FF7A,&H00000000,1,0,0,0,100,100,0,0,1,14,0,5,0,0,0,1\n"
              f"Style: T,{FONT} Black,120,&H00FFFFFF,&H000000FF,&H00101010,&H00000000,1,0,0,0,100,100,0,0,1,5,0,5,0,0,0,1")
    text(raw, OUT / "logo.png", [(0, "G", f"{{\\blur10\\pos({W // 2},{int(H * 0.79)})}}UFO"),
                                 (1, "T", f"{{\\pos({W // 2},{int(H * 0.79)})}}UFO")], W, H, styles)
    raw.unlink()


def banner():
    W, H = 2560, 1440
    im = sky(W, H, seed=23)
    im = saucer(im, W * 0.705, 612, 340, beam_len=300, tilt=-10)
    # horizon silhouette (hills) under the beam, inside the safe band
    d = ImageDraw.Draw(im)
    rnd = random.Random(5)
    pts, x = [(0, H)], 0
    while x <= W:
        pts.append((x, H * 0.80 + rnd.uniform(-14, 14) + 30 * math.sin(x / 260)))
        x += 40
    pts.append((W, H))
    d.polygon(pts, fill=(2, 3, 8))
    raw = OUT / "banner_raw.png"; im.save(raw)
    cx, top = int(W * 0.38), 616
    styles = (f"Style: G,{FONT} Black,132,&H0039FF7A,&H000000FF,&H0039FF7A,&H00000000,1,0,0,0,100,100,0,0,1,16,0,5,0,0,0,1\n"
              f"Style: T,{FONT} Black,132,&H00FFFFFF,&H000000FF,&H00101010,&H00000000,1,0,0,0,100,100,0,0,1,5,0,5,0,0,0,1\n"
              f"Style: S,{FONT} Bold,54,&H0039FF7A,&H000000FF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,3,0,5,0,0,0,1\n"
              f"Style: B,{FONT} Bold,40,&H00FFFFFF,&H000000FF,&H00000000,&HA0000000,1,0,0,0,100,100,0,0,3,12,0,5,0,0,0,1")
    text(raw, OUT / "banner.png", [
        (0, "G", f"{{\\blur12\\pos({cx},{top})}}REAL UFO STORIES"),
        (1, "T", f"{{\\pos({cx},{top})}}REAL UFO STORIES"),
        (1, "S", f"{{\\pos({cx},{top + 108})}}दुनिया के असली यूएफओ केस | हिंदी में"),
        (1, "B", f"{{\\pos({cx},{top + 196})}}  रोज़ नया रहस्य  |  शाम 6 बजे  "),
    ], W, H, styles)
    raw.unlink()


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    logo(); banner()
    print("wrote", OUT / "logo.png", OUT / "banner.png")
