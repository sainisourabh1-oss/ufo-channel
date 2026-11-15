"""
1280x720 click-worthy thumbnail: strongest case image, punched-up contrast, dark gradient
on the text side, 2-4 huge Hindi words (yellow, thick black stroke, green glow) and a red
"UFO FILES" tag. Rendered through libass (harfbuzz) so Devanagari shapes correctly.
"""
import subprocess
from pathlib import Path
from PIL import Image, ImageEnhance, ImageOps
from .render import FONTS, FONT, _esc

TW, TH = 1280, 720


def make(img: Path, text_hi: str, out: Path, tag="UFO FILES", side=None):
    with Image.open(img) as im:
        im = ImageOps.fit(im.convert("RGB"), (TW, TH), Image.LANCZOS)
        im = ImageEnhance.Contrast(im).enhance(1.25)
        im = ImageEnhance.Color(im).enhance(1.15)
        im = ImageEnhance.Sharpness(im).enhance(1.4)
        base = out.with_suffix(".base.jpg")
        im.save(base, "JPEG", quality=95)
    words = text_hi.split()
    if len(words) >= 3:
        mid = (len(words) + 1) // 2
        text = _esc(" ".join(words[:mid])) + "\\N" + _esc(" ".join(words[mid:]))
    else:
        text = _esc(text_hi)
    side = side or "left"
    x = 60 if side == "left" else TW - 60
    an = 4 if side == "left" else 6
    grad = []
    for i in range(40):                       # smooth dark gradient behind the text
        w = int(TW * 0.78 * (1 - i / 40))
        x0 = 0 if side == "left" else TW - w
        grad.append(f"Dialogue: 0,0:00:00.00,0:00:01.00,T,,0,0,0,,{{\\an7\\pos(0,0)\\p1\\bord0\\shad0"
                    f"\\1c&H000000&\\1a&HF5&}}m {x0} 0 l {x0 + w} 0 l {x0 + w} {TH} l {x0} {TH}{{\\p0}}")
    ass = out.with_suffix(".ass")
    ass.write_text(f"""[Script Info]
ScriptType: v4.00+
PlayResX: {TW}
PlayResY: {TH}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: T,{FONT} Black,150,&H0000E5FF,&H000000FF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,10,6,{an},40,40,40,1
Style: G,{FONT} Black,150,&H0039FF7A,&H000000FF,&H0039FF7A,&H00000000,1,0,0,0,100,100,0,0,1,22,0,{an},40,40,40,1
Style: Tag,{FONT} Black,44,&H00FFFFFF,&H000000FF,&H001A1AE0,&H001A1AE0,1,0,0,0,100,100,0,0,3,10,0,7,40,40,30,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
""" + "\n".join(grad) + f"""
Dialogue: 1,0:00:00.00,0:00:01.00,G,,0,0,0,,{{\\blur14\\pos({x},{TH // 2 + 20})}}{text}
Dialogue: 2,0:00:00.00,0:00:01.00,T,,0,0,0,,{{\\pos({x},{TH // 2 + 20})}}{text}
Dialogue: 3,0:00:00.00,0:00:01.00,Tag,,0,0,0,,{{\\pos({40 if side == 'left' else TW - 360},34)}} {tag}
""", encoding="utf-8")
    a = ass.resolve().as_posix().replace(":", "\\:")
    f = FONTS.as_posix().replace(":", "\\:")
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(base),
                    "-vf", f"ass=filename='{a}':fontsdir='{f}':shaping=complex",
                    "-frames:v", "1", "-q:v", "2", str(out)], check=True)
    print(f"[thumb] wrote {out}")
    return out
