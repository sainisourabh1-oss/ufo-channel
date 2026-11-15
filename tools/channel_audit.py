"""
Channel inventory + low-view cleanup.

  python -m tools.channel_audit                       # list every upload (dry run)
  python -m tools.channel_audit --delete --max-views 9 --confirm DELETE

Deletes only videos whose viewCount <= max-views AND that are older than
--min-age-days (so a video published yesterday isn't judged on day one).
Writes used/channel_inventory.csv every run.
"""
import argparse
import csv
import datetime as dt
from pipeline.youtube import service as _service
from pipeline.settings import ROOT


def _uploads(yt):
    ch = yt.channels().list(part="contentDetails,snippet,statistics", mine=True).execute()["items"][0]
    print(f"[audit] channel: {ch['snippet']['title']}  subs={ch['statistics'].get('subscriberCount')}"
          f"  videos={ch['statistics'].get('videoCount')}  views={ch['statistics'].get('viewCount')}")
    pl = ch["contentDetails"]["relatedPlaylists"]["uploads"]
    ids, tok = [], None
    while True:
        r = yt.playlistItems().list(part="contentDetails", playlistId=pl, maxResults=50, pageToken=tok).execute()
        ids += [i["contentDetails"]["videoId"] for i in r["items"]]
        tok = r.get("nextPageToken")
        if not tok:
            return ids


def _details(yt, ids):
    out = []
    for i in range(0, len(ids), 50):
        r = yt.videos().list(part="snippet,status,statistics,contentDetails", id=",".join(ids[i:i + 50])).execute()
        for v in r["items"]:
            out.append({
                "id": v["id"],
                "published": v["snippet"]["publishedAt"][:10],
                "privacy": v["status"]["privacyStatus"],
                "publishAt": v["status"].get("publishAt", ""),
                "views": int(v["statistics"].get("viewCount", 0)),
                "likes": int(v["statistics"].get("likeCount", 0)),
                "duration": v["contentDetails"]["duration"],
                "title": v["snippet"]["title"],
            })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--delete", action="store_true")
    ap.add_argument("--max-views", type=int, default=9)
    ap.add_argument("--min-age-days", type=int, default=7)
    ap.add_argument("--confirm", default="")
    a = ap.parse_args()

    yt = _service()
    vids = _details(yt, _uploads(yt))
    vids.sort(key=lambda v: v["published"])
    inv = ROOT / "used" / "channel_inventory.csv"
    with open(inv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(vids[0].keys()) if vids else ["id"])
        w.writeheader(); w.writerows(vids)

    today = dt.date.today()
    doomed = [v for v in vids
              if v["views"] <= a.max_views
              and (today - dt.date.fromisoformat(v["published"])).days >= a.min_age_days]
    by_priv = {}
    for v in vids:
        by_priv[v["privacy"]] = by_priv.get(v["privacy"], 0) + 1
    print(f"[audit] {len(vids)} uploads by privacy: {by_priv}")
    for v in vids:
        mark = "DEL" if v in doomed else "   "
        print(f"{mark} {v['published']} {v['privacy']:8} views={v['views']:<6} {v['duration']:10} {v['id']} {v['title'][:70]}")
    print(f"[audit] {len(doomed)} videos match views<={a.max_views} and age>={a.min_age_days}d")

    if not a.delete:
        print("[audit] dry run — nothing deleted")
        return
    if a.confirm != "DELETE":
        raise SystemExit("[audit] refusing to delete: pass --confirm DELETE")
    for v in doomed:
        yt.videos().delete(id=v["id"]).execute()
        print(f"[audit] deleted {v['id']} ({v['views']} views) {v['title'][:60]}")
    print(f"[audit] deleted {len(doomed)} videos")


if __name__ == "__main__":
    main()
