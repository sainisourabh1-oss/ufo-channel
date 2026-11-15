"""
Daily orchestrator - fully automatic.

  python -m pipeline.run                # build + publish today's video and Shorts
  python -m pipeline.run --dry-run      # build only, outputs to ./out (no upload, ledgers untouched)
  python -m pipeline.run --force        # build even if today's video already exists

Resilience: up to 5 cases are tried; every stage has its own fallbacks (Gemini model chain,
TTS provider chain, 4 image sources + guaranteed unique fallback frames). The run only fails
if nothing at all could be published, and the workflow then opens a GitHub issue.
"""
import argparse
import json
import random
import re
import shutil
import subprocess
import traceback
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter
from .settings import ist_now, set_output, workdir, CONFIG, ROOT
from . import cases, script_gen, tts, images, render, thumbnail, dedup

V, SH, LOC = CONFIG["video"], CONFIG["shorts"], CONFIG["localization"]
FORMAT_PLAYLIST = {
    "case_file": ("UFO केस फाइलें | Real UFO Case Files", "दुनिया के सबसे चर्चित, दस्तावेज़ों में दर्ज UFO केस - हिंदी में।"),
    "debunk": ("दावा बनाम सबूत | UFO Claims Investigated", "मशहूर UFO दावे और उनकी असली जाँच।"),
    "top_five": ("टॉप 5 UFO रहस्य | Top 5 UFO Mysteries", "एक थीम, पाँच सबसे हैरान करने वाले केस।"),
}


# ---------------------------------------------------------------- helpers
def starfield(dest: Path, W: int, H: int, seed: int):
    """guaranteed-unique fallback frame (random star field + nebula glow) if every source fails."""
    rnd = random.Random(seed)
    im = Image.new("RGB", (W, H), (4, 6, 14))
    glow = Image.new("RGB", (W, H), (0, 0, 0))
    d = ImageDraw.Draw(glow)
    for _ in range(3):
        x, y, r = rnd.randint(0, W), rnd.randint(0, H), rnd.randint(H // 4, H // 2)
        c = rnd.choice([(20, 60, 40), (30, 20, 60), (10, 40, 70)])
        d.ellipse((x - r, y - r, x + r, y + r), fill=c)
    im = Image.blend(im, glow.filter(ImageFilter.GaussianBlur(H // 6)), 0.8)
    d = ImageDraw.Draw(im)
    for _ in range(int(W * H / 2500)):
        x, y, b = rnd.randint(0, W - 1), rnd.randint(0, H - 1), rnd.randint(120, 255)
        d.point((x, y), fill=(b, b, min(255, b + 20)))
    im.save(dest, "JPEG", quality=92)
    return dest


def concat_narration(wavs, work: Path, gap: float):
    silence = work / "gap.wav"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
                    "-t", f"{gap}", "-c:a", "pcm_s16le", str(silence)], check=True)
    seq, starts, t = [], [], 0.0
    durs = [tts.wav_seconds(w) for w in wavs]
    for i, (w, d) in enumerate(zip(wavs, durs)):
        if i:
            seq.append(silence); t += gap
        starts.append(t); seq.append(w); t += d
    lst = work / "narr.txt"
    lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in seq), encoding="utf-8")
    out = work / "narration.wav"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                    "-c:a", "pcm_s16le", str(out)], check=True)
    return out, starts, durs, t


def sentences(text):
    parts = [p.strip() for p in re.split(r"(?<=[।?!])\s+", text) if p.strip()]
    return parts or [text]


def timed_lines(texts, starts, durs, max_chars=None):
    """[(start, end, line)] - each narration split into sentences (or word groups), timed by length."""
    out = []
    for text, s0, d in zip(texts, starts, durs):
        lines = sentences(text)
        if max_chars:
            grouped = []
            for sen in lines:
                words, cur = sen.split(), ""
                for w in words:
                    if cur and len(cur) + len(w) > max_chars:
                        grouped.append(cur); cur = ""
                    cur = (cur + " " + w).strip()
                if cur:
                    grouped.append(cur)
            lines = grouped
        total = sum(len(l) for l in lines) or 1
        t = s0
        for l in lines:
            dt = d * len(l) / total
            out.append((t, t + dt, l)); t += dt
    return out


def srt(lines, path: Path):
    def ts(x):
        h, m, s = int(x // 3600), int(x % 3600 // 60), x % 60
        return f"{h:02}:{m:02}:{int(s):02},{int((s - int(s)) * 1000):03}"
    path.write_text("".join(f"{i}\n{ts(a)} --> {ts(b)}\n{l}\n\n" for i, (a, b, l) in enumerate(lines, 1)),
                    encoding="utf-8")
    return path


def mmss(x):
    x = int(x)
    return f"{x // 60:02}:{x % 60:02}"


# ---------------------------------------------------------------- production
def produce(case, script, dry: bool):
    vid_key = ist_now().strftime("%Y%m%d") + "_" + re.sub(r"[^A-Za-z0-9_]", "", case["case_id"])[:60]
    work = workdir(vid_key)
    seed = random.randrange(10**9)
    W, H = V["width"], V["height"]
    scenes = script["scenes"]

    # 1. narration
    texts = [sc["narration_hi"] for sc in scenes]
    wavs, provider = tts.narrate_all(texts, work, "scene")
    narration, starts, durs, total = concat_narration(wavs, work, render.SCENE_GAP)
    print(f"[run] narration {total / 60:.1f} min via {provider}")

    # 2. images - fresh for every scene, never seen before
    col = images.Collector(work, case["title"] if case["format"] != "top_five" else case["sources"][0]["title"],
                           seed, W, H)
    plan = []
    for i, sc in enumerate(scenes):
        secs = durs[i] + (render.SCENE_GAP if i < len(scenes) - 1 else 0)
        need = max(2, round(secs / V["seconds_per_image"]))
        if case["format"] == "top_five":
            col.title = next((a["title"] for a in case["sources"]
                              if a["title"].lower() in (sc.get("chapter_hi", "") + " " + " ".join(sc["visual_queries_en"])).lower()),
                             col.title)
        got = col.for_scene(sc["visual_queries_en"], need, i)
        while len(got) < need:
            got.append(starfield(work / f"star_{i}_{len(got)}.jpg", W, H, seed + i * 100 + len(got)))
        prepped = [render.prepare_image(p, work / f"prep_{i:02}_{k:02}.jpg", W, H, seed=seed + k)
                   for k, p in enumerate(got)]
        plan.append({"images": prepped, "secs": secs})

    # 3. overlay: cold-open hook, title card at scene 2, chapters, lower thirds, end card
    ov = render.Overlay(W, H)
    title_at = starts[1] if len(starts) > 1 else 0.3
    ov.title_card(title_at + 0.2, title_at + 3.8, script.get("kicker_hi", ""), script.get("title_hi", "").split("|")[0].split("(")[0].strip()[:60])
    chapters = [(0.0, "शुरुआत")]
    for i, sc in enumerate(scenes):
        if i >= 2 and sc.get("chapter_hi"):
            ov.chapter(starts[i] + 0.15, f"अध्याय {len(chapters)}", sc["chapter_hi"])
        if i >= 1 and sc.get("chapter_hi"):
            chapters.append((starts[i], sc["chapter_hi"]))
        if sc.get("lower_third_hi"):
            ov.lower_third(starts[i] + (4.2 if i in (1,) else 1.2), sc["lower_third_hi"])
    ov.chapter(max(0, total - 6.5), "Real UFO Stories", "सब्सक्राइब करें", dur=6.0)
    long_mp4 = render.render_video(work, plan, narration, starts, total, ov, W, H, work / "long.mp4", seed)

    # 4. captions (hi + en)
    lines = timed_lines(texts, starts, durs)
    srt_hi = srt(lines, work / "captions_hi.srt")
    en = script_gen.translate_lines([l for _, _, l in lines])
    srt_en = srt([(a, b, t) for (a, b, _), t in zip(lines, en)], work / "captions_en.srt") if en else None

    # 5. thumbnail from the most relevant real image
    best = next((e["path"] for e in col.got if e["source"] == "wiki"), None) or \
        next((e["path"] for e in col.got if e["source"] != "ai"), None) or plan[0]["images"][0]
    thumb = thumbnail.make(best, script.get("thumbnail_text_hi") or script["title_hi"][:20], work / "thumb.jpg")

    # 6. Shorts (vertical, burned-in captions, fresh images)
    shorts = []
    sw, shh = SH["width"], SH["height"]
    for k, s in enumerate(script.get("shorts", [])[:SH["count"]] if SH.get("enabled") else []):
        try:
            swav, _ = tts.narrate_all([s["narration_hi"]], work, f"short{k}", providers=[provider])
            sdur = tts.wav_seconds(swav[0])
            if sdur > SH["max_seconds"]:
                print(f"[run] short {k} too long ({sdur:.0f}s) - skipped"); continue
            need = max(4, round(sdur / 3.2))
            col.W, col.H = sw, shh
            col.new_budget(6)     # each Short gets its own image time budget (the long render used the first one up)
            got = col.for_scene(s["visual_queries_en"], need, 100 + k)
            while len(got) < need:
                got.append(starfield(work / f"sstar_{k}_{len(got)}.jpg", sw, shh, seed + 7000 + k * 50 + len(got)))
            prepped = [render.prepare_image(p, work / f"sprep_{k}_{j:02}.jpg", sw, shh, seed=seed + j) for j, p in enumerate(got)]
            sov = render.Overlay(sw, shh)
            sov.add(2, 0.1, 3.2, "Chapter", f"{{\\fad(150,300)\\pos({sw // 2},{int(shh * 0.17)})}}{render._esc(s['title_hi'][:40])}")
            for a, b, l in timed_lines([s["narration_hi"]], [0.0], [sdur], max_chars=22):
                sov.caption(a, b, l)
            smp4 = render.render_video(work, [{"images": prepped, "secs": sdur}], swav[0], [0.0], sdur, sov,
                                       sw, shh, work / f"short_{k}.mp4", seed + k)
            shorts.append((s, smp4))
        except Exception as e:
            print(f"[run] short {k} failed: {e}")
    col.W, col.H = W, H

    # 7. metadata
    tags = list(dict.fromkeys((script.get("tags") or []) + CONFIG["metadata"]["base_tags"]))
    tags, total_len = [t for t in tags if t], 0
    kept = []
    for t in tags:                                   # YouTube: tags total <= 500 chars
        if total_len + len(t) + 2 > 480:
            break
        kept.append(t); total_len += len(t) + 2
    chap_txt = "\n".join(f"{mmss(t)} {name}" for t, name in chapters) if len(chapters) >= 3 else ""
    srcs = "\n".join(f"- {a['title']}: {a.get('url', '')}" for a in case["sources"])
    credits_rel = f"used/credits/{vid_key}.md"
    hashtags = "#UFO #UFOHindi #रहस्य #RealUFOStories"
    desc = (f"{script['description_hi'].strip()}\n\n"
            + (f"अध्याय:\n{chap_txt}\n\n" if chap_txt else "")
            + f"स्रोत (Sources):\n{srcs}\n"
            f"चित्र: Wikimedia Commons, NASA, Openverse (CC0 / Public Domain / CC-BY). "
            f"Image credits: https://github.com/sainisourabh1-oss/ufo-channel/blob/main/{credits_rel}\n"
            "कुछ दृश्य AI से बने चित्र हो सकते हैं; कथन AI वॉइस में है। सभी तथ्य दर्ज स्रोतों पर आधारित हैं।\n\n"
            f"{hashtags}")
    locs = script_gen.translations(script, LOC["languages"])
    credits = "\n".join(f"- {e['credit']}" for e in col.got)
    (ROOT / "used" / "credits").mkdir(parents=True, exist_ok=True)
    (ROOT / credits_rel).write_text(f"# Image credits - {script['title_hi']}\n\n{credits}\n", encoding="utf-8")

    if dry:
        out = ROOT / "out"; out.mkdir(exist_ok=True)
        for p in [long_mp4, thumb, srt_hi] + ([srt_en] if srt_en else []) + [m for _, m in shorts]:
            shutil.copy(p, out / p.name)
        (out / "meta.json").write_text(json.dumps({"case": case["case_id"], "format": case["format"],
                                                   "title": script["title_hi"], "description": desc, "tags": kept,
                                                   "localizations": list(locs), "script": script},
                                                  ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[run] DRY RUN done -> {out}")
        return "dry"

    # 8. publish (fully automatic)
    from . import youtube as yt
    vid = yt.upload(long_mp4, script["title_hi"], desc, kept,
                    yt.release_status(CONFIG["release"]["publish_hour"], CONFIG["release"]["publish_minute"]), locs)
    dedup.log(case["case_id"], script["title_hi"], vid, "long")
    images.record(col.got, vid)
    yt.thumbnail(vid, thumb)
    yt.captions(vid, "hi", "हिन्दी", srt_hi)
    if srt_en:
        yt.captions(vid, "en", "English", srt_en)
    pl = FORMAT_PLAYLIST[case["format"]]
    yt.playlist(vid, case["format"], *pl)
    if script.get("pinned_comment_hi"):
        yt.comment(vid, script["pinned_comment_hi"])

    for (s, smp4), hhmm in zip(shorts, CONFIG["release"]["short_times"]):
        hour, minute = (int(x) for x in hhmm.split(":"))
        try:
            sdesc = (f"{s['title_hi']}\n\nपूरी कहानी देखें: https://youtu.be/{vid}\n\n#Shorts #UFO #UFOHindi #रहस्य")
            sid = yt.upload(smp4, (s["title_hi"][:88] + " #Shorts"), sdesc, kept[:12], yt.release_status(hour, minute))
            dedup.log(case["case_id"], s["title_hi"], sid, "short")
            yt.playlist(sid, "shorts", "UFO Shorts | 60 सेकंड में रहस्य", "छोटे, चौंकाने वाले UFO तथ्य।")
        except Exception as e:
            print(f"[run] short upload failed: {e}")

    set_output("video_id", vid)
    set_output("youtube_url", f"https://youtu.be/{vid}")
    summary = (f"### {script['title_hi']}\n- video: https://youtu.be/{vid}\n- format: {case['format']}\n"
               f"- length: {total / 60:.1f} min, {len(scenes)} scenes, {len(col.got)} new images, voice: {provider}\n"
               f"- shorts: {len(shorts)}\n- localized languages: {len(locs)}\n")
    import os
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(summary)
    print(summary)
    return vid


def build(dry=False, force=False):
    if not force and not dry and dedup.already_made_today():
        print("[run] today's video already published - nothing to do")
        set_output("skipped", "true")
        return
    tried = set()
    for attempt in range(5):
        case = cases.pick(exclude=frozenset(tried))
        if not case:
            break
        tried.add(case["case_id"])
        try:
            script = script_gen.write(case)
            if not script:
                print("[run] no valid script - next case"); continue
            result = produce(case, script, dry)
            set_output("skipped", "false")
            return result
        except Exception:
            traceback.print_exc()
            print(f"[run] case {case['case_id']} failed - trying another")
    raise SystemExit("[run] FAILED: no video could be produced today")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    build(dry=a.dry_run, force=a.force)
