"""
Orchestrates the pipeline.

  python -m pipeline.run --stage build                 # daily build -> private upload
  python -m pipeline.run --stage publish --video-id X  # after approval -> schedule 6 PM IST
"""
import argparse
import json
from .settings import ist_now, set_output, workdir
from . import source_news, script_gen, narrate, captions, assemble, shorts, metadata, dedup, upload


def _skip(reason: str):
    print(f"[run] SKIPPING TODAY: {reason}")
    set_output("skipped", "true")
    set_output("video_id", "")
    set_output("youtube_url", "")


def build():
    # 1. Pick a fresh, documented case (fail-safe if none).
    case = source_news.pick_case()
    if not case:
        return _skip("no fresh case available")
    if not source_news.enough_material(case):
        return _skip("case has too little sourced material")

    video_id = ist_now().strftime("%Y%m%d") + "_" + case["case_id"]
    print(f"[run] building {video_id} — {case.get('title')}")

    # 2. Script: use a prewritten, fact-checked script if the case has one
    #    (this is how video #1 ships), else let the AI writer draft + validate.
    script = case.get("prewritten")
    if script:
        print("[run] using prewritten, fact-checked script")
    else:
        script = script_gen.write_script(case)
    if not script:
        return _skip("script failed validation")

    # attach asset URLs to shots (prewritten shots already carry their own url)
    label_to_url = {}
    for a in case.get("assets", []):
        label_to_url[a.get("source_label") or a.get("label")] = a.get("url") or a.get("local")
    for shot in script["shot_list"]:
        if not shot.get("url"):
            shot["url"] = label_to_url.get(shot.get("source_label"))

    # 3. Narration -> captions -> assemble -> shorts.
    mp3 = narrate.narrate(video_id, script["script_hi"])
    srt = captions.captions(video_id, mp3)
    long_mp4 = assemble.assemble(video_id, script, mp3, srt)
    short_files = shorts.make_shorts(video_id, long_mp4)

    # 4. Upload long video as PRIVATE.
    meta = metadata.build(script, is_short=False)
    vid = upload.upload_private(long_mp4, meta)

    # upload shorts as private too (they ride the same approval)
    for sf in short_files:
        try:
            upload.upload_private(sf, metadata.build(script, is_short=True))
        except Exception as e:
            print(f"[run] short upload skipped: {e}")

    # 5. Record + hand off to the approval gate.
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
