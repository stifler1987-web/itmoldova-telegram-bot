import os
import json
import calendar
from datetime import datetime, timezone
from urllib.parse import urlparse

import feedparser
import requests

# =====================
# CONFIG
# =====================

FEEDS_FILE = "feeds.json"
STATE_FILE = "state.json"
RULES_FILE = "emoji_rules.json"

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

MAX_ITEMS = 25
PER_SOURCE_LIMIT = 5
TIMEZONE = "Europe/Chisinau"
MAX_AGE_HOURS = 24

# =====================
# HELPERS
# =====================

def local_now():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(TIMEZONE))
    except Exception:
        return datetime.now(timezone.utc)

def cutoff_ts():
    return int(datetime.now(timezone.utc).timestamp()) - (MAX_AGE_HOURS * 3600)

def entry_ts(e):
    if getattr(e, "published_parsed", None):
        return int(calendar.timegm(e.published_parsed))
    if getattr(e, "updated_parsed", None):
        return int(calendar.timegm(e.updated_parsed))
    return 0

def entry_id(e):
    return (e.get("id") or e.get("link") or "")[:500]

def html_escape(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def domain_of(url):
    try:
        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return ""

# =====================
# LOADERS
# =====================

def load_feeds():
    with open(FEEDS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)["categories"]

def load_state():
    if not os.path.exists(STATE_FILE):
        return {"posted_ids": []}
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)

# =====================
# TELEGRAM
# =====================

def send_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    r = requests.post(url, json={
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }, timeout=30)

    if not r.ok:
        print("[ERROR] Telegram error:", r.text)

# =====================
# MAIN
# =====================

def main():
    print("===== BOT START =====")

    if not BOT_TOKEN or not CHAT_ID:
        print("[ERROR] Missing BOT_TOKEN or CHAT_ID")
        return

    now = local_now()
    print(f"[TIME] Local time: {now}")

    if now.hour < 9 or now.hour >= 22:
        print("[INFO] Quiet hours – exiting")
        return

    cutoff = cutoff_ts()
    print(f"[INFO] Cutoff timestamp (24h): {cutoff}")

    state = load_state()
    posted = set(state.get("posted_ids", []))

    categories = load_feeds()
    final_items = []

    for cat_name, cat in categories.items():
        print(f"\n[CATEGORY] {cat_name}")
        for feed_url in cat["feeds"]:
            print(f"  [FEED] {feed_url}")
            feed = feedparser.parse(feed_url)

            entries = getattr(feed, "entries", [])
            print(f"    entries found: {len(entries)}")

            kept = 0
            old = 0

            for e in entries[:50]:
                ts = entry_ts(e)
                if ts and ts < cutoff:
                    old += 1
                    continue

                eid = entry_id(e)
                if eid in posted:
                    continue

                title = (e.get("title") or "").strip()
                link = (e.get("link") or "").strip()
                if not title or not link:
                    continue

                final_items.append({
                    "id": eid,
                    "title": title,
                    "link": link
                })
                kept += 1

            print(f"    kept={kept} old_filtered={old}")

    if not final_items:
        print("\n[RESULT] ZERO stiri gasite.")
        return

    msg = "🗞️ <b>IT Moldova</b>\n\n"
    for it in final_items[:MAX_ITEMS]:
        msg += f"📰 <a href=\"{html_escape(it['link'])}\">{html_escape(it['title'])}</a>\n<i>{domain_of(it['link'])}</i>\n\n"

    send_message(msg)

    for it in final_items:
        posted.add(it["id"])

    save_state({"posted_ids": list(posted)[-1000:]})

    print(f"\n[RESULT] Trimise {len(final_items)} stiri")

if __name__ == "__main__":
    main()
