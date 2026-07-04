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
    tried = set()
    for _ in range(4):
        case = source_news.pick_case(exclude=tried)
        if not case:
            return _skip("no fresh case available")
        tried.add(case["case_id"])
        video_id = ist_now().strftime("%Y%m%d") + "_" + case["case_id"]
        print(f"[run] trying: {case.get('title')}")

        script = case.get("prewritten") or script_gen.write_script(case)
        if not script:
            print("[run] script failed — trying another case")
            continue

        if case.get("prewritten"):
            images = [a["url"] for a in case.get("assets", []) if a.get("url")]
        else:
            images = source_news.fetch_images(case, n=60)
        if len(images) < 6:
            print("[run] too few images — trying another case")
            continue

        mp3 = narrate.narrate(video_id, script["script_hi"])
        long_mp4 = assemble.assemble(video_id, script, mp3, images)
        meta = metadata.build(script, is_short=False)
        vid = upload.upload_private(long_mp4, meta)

        dedup.log(case["case_id"], meta["title"], vid)
        (workdir(video_id) / "meta.json").write_text(
            json.dumps({"video_id": vid, **meta}, ensure_ascii=False, indent=2), encoding="utf-8")
        set_output("skipped", "false")
        set_output("video_id", vid)
        set_output("youtube_url", f"https://youtu.be/{vid}")
        print(f"[run] DONE — private video {vid} awaiting your approval")
        return

    return _skip("no workable case this run")


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
