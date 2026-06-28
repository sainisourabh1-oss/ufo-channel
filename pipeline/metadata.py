"""Assemble final metadata: title, description, and hashtags."""
from .settings import CONFIG

M = CONFIG["metadata"]


def build(script: dict, is_short: bool):
    base = list(M["base_hashtags"])
    topic = script.get("topic_tags", [])[: M.get("trending_extra", 2) + 2]
    # de-dup, keep order, cap at 15 (YouTube ignores ALL tags past 15)
    tags, seen = [], set()
    for t in base + topic:
        t = t if t.startswith("#") else "#" + t
        if t.lower() not in seen:
            seen.add(t.lower()); tags.append(t)
    tags = tags[:15]

    title = script["title_hi"]
    if is_short and "#shorts" not in title.lower():
        title = f"{title} #shorts"

    desc = script["description_hi"].strip() + "\n\n" + " ".join(tags)
    desc += ("\n\nस्रोत: सार्वजनिक रिकॉर्ड (war.gov, Library of Congress, "
             "National Archives). यह वीडियो दर्ज तथ्यों पर आधारित है।")
    return {"title": title[:100], "description": desc[:4900], "tags": [t.lstrip('#') for t in tags]}
