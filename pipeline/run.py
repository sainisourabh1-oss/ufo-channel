"""
Orchestrates the pipeline.

  python -m pipeline.run --stage build                 # daily build -> private upload
  python -m pipeline.run --stage publish --video-id X  # after approval -> schedule 6 PM IST
"""
import argparse
import json
from .settings import ist_now, set_output, workdir
from . import source_news, script_gen, narrate, assemble, metadata, dedup, upload


def _skip(reason: str):
    print(f"[run] SKIPPING TODAY: {reason}")
    set_output("skipped", "true")
    set_output("video_id", "")
    set_output("youtube_url", "")


def build():
    # 1. Find a fresh case that ALSO has enough usable images.
    #    Images are checked before writing the script (cheap first), and we try
    #    several cases in one run so a single click reliably produces a video.
    tried, case, imgs = set(), None, []
    for _ in range(12):
        c = source_news.pick_case(exclude=tried)
        if not c:
            break
        if c.get("prewritten"):
            case = c
            break
        tried.add(c["case_id"])
        found = source_news.fetch_commons_images(c.get("image_queries", [c["title"]]), n=8)
        if len(found) >= 3:
            case, imgs = c, found
            break
        print(f"[run] '{c['title']}' had only {len(found)} images — trying another case")
    if not case:
        return _skip("no case with enough images this run")

    video_id = ist_now().strftime("%Y%m%d") + "_" + case["case_id"]
    print(f"[run] building {video_id} — {case.get('title')}")

    # 2. Script: use a prewritten, fact-checked script if the case has one,
    #    else let the AI writer draft + validate.
    script = case.get("prewritten")
    if script:
        print("[run] using prewritten, fact-checked script")
    else:
        script = script_gen.write_script(case)
    if not script:
        return _skip("script failed validation")

    # 3. Images.
    if case.get("prewritten"):
        label_to_url = {(a.get("source_label") or a.get("label")): a.get("url")
                        for a in case.get("assets", [])}
        for shot in script["shot_list"]:
            if not shot.get("url"):
                shot["url"] = label_to_url.get(shot.get("source_label"))
    else:
        for i, shot in enumerate(script["shot_list"]):
            shot["url"] = imgs[i % len(imgs)]
            shot["source_label"] = case.get("title", "")

    # 4. Narration -> assemble -> shorts.
    mp3 = narrate.narrate(video_id, script["script_hi"])
    long_mp4 = assemble.assemble(video_id, script, mp3)
    short_files = shorts.make_shorts(video_id, long_mp4)

    # 5. Upload long video as PRIVATE.
    meta = metadata.build(script, is_short=False)
    vid = upload.upload_private(long_mp4, meta)
    for sf in short_files:
        try:
            upload.upload_private(sf, metadata.build(script, is_short=True))
        except Exception as e:
            print(f"[run] short upload skipped: {e}")

    # 6. Record + hand off to the approval gate.
    dedup.log(case["case_id"], meta["title"], vid)
    (workdir(video_id) / "meta.json").write_text(
        json.dumps({"video_id": vid, **meta}, ensure_ascii=False, indent=2), encoding="utf-8")

    set_output("skipped", "false")
    set_output("video_id", vid)
    set_output("youtube_url", f"https://youtu.be/{vid}")
    print(f"[run] DONE — private video {vid} awaiting your approval")


def publish(video_id: str):
    upload.schedule_release(video_id)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=["build", "publish"], required=True)
    ap.add_argument("--video-id")
    args = ap.parse_args()
    if args.stage == "build":
        build()
    else:
        publish(args.video_id)
