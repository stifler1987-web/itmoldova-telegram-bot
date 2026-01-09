import os
import json
from datetime import datetime, timezone
from urllib.parse import urlparse

import feedparser
import requests

RSS_FILE = "rss_list.txt"
STATE_FILE = "state.json"

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
MAX_ITEMS = int(os.getenv("MAX_ITEMS", "10"))
TIMEZONE = os.getenv("TIMEZONE", "Europe/Chisinau")

def load_rss_list():
    with open(RSS_FILE, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]

def load_state():
    if not os.path.exists(STATE_FILE):
        return {"posted_ids": []}
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def entry_id(entry):
    return (entry.get("id") or entry.get("guid") or (entry.get("link", "") + "|" + entry.get("title", "")))[:500]

def html_escape(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def domain_of(url):
    try:
        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return ""

def local_now():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(TIMEZONE))
    except Exception:
        return datetime.now(timezone.utc).astimezone()

def build_message(items):
    now = local_now()
    header = f"🗞️ <b>IT Moldova</b>\n<i>Buletin {now:%d.%m.%Y %H:%M}</i>\n\n"
    blocks = []
    for it in items:
        title = html_escape(it["title"])
        link = it["link"]
        src = html_escape(domain_of(link))
        blocks.append(f"• <a href=\"{link}\">{title}</a>\n<i>{src}</i>")
    return header + "\n\n".join(blocks)

def send_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    r = requests.post(url, json=payload, timeout=30)
    if not r.ok:
        raise RuntimeError(f"{r.status_code}: {r.text}")

def main():
    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError("Missing BOT_TOKEN or CHAT_ID")

    now = local_now()
    if now.hour >= 22 or now.hour < 9:
        print("Quiet hours, skip")
        return

    urls = load_rss_list()
    state = load_state()
    posted = set(state.get("posted_ids", []))
    items = []

    for u in urls:
        feed = feedparser.parse(u)
        for e in feed.entries[:30]:
            eid = entry_id(e)
            if eid in posted:
                continue
            title = (e.get("title") or "").strip()
            link = (e.get("link") or "").strip()
            if not title or not link:
                continue
            items.append({"id": eid, "title": title, "link": link})

    if not items:
        print("No new items")
        return

    items = items[:MAX_ITEMS]
    msg = build_message(items)
    if len(msg) > 3800:
        msg = msg[:3800] + "\n…"

    send_message(msg)

    for it in items:
        posted.add(it["id"])

    state["posted_ids"] = list(posted)[-2000:]
    save_state(state)

if __name__ == "__main__":
    main()
