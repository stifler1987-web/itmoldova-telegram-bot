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
QUIET_HOUR = int(os.getenv("QUIET_HOUR", "22"))
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

def html_escape(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def domain_of(url: str) -> str:
    try:
        host = urlparse(url).netloc
        return host.replace("www.", "")
    except Exception:
        return ""

def get_local_now():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(TIMEZONE))
    except Exception:
        return datetime.now(timezone.utc).astimezone()

def build_message(items):
    now = get_local_now()
    header = f"🗞️ <b>IT Moldova</b>\n<i>Buletin {now:%d.%m.%Y %H:%M}</i>\n\n"
    blocks = []
    for item in items:
        title = html_escape(item["title"])
        link = (item.get("link") or "").strip()
        src = html_escape(domain_of(link)) if link else ""
        if link:
            line = f"• <a href=\"{link}\">{title}</a>"
        else:
            line = f"• {title}"
        if src:
            line += f"\n<i>{src}</i>"
        blocks.append(line)
    return header + "\n\n".join(blocks)

def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    r = requests.post(url, json=payload, timeout=30)
    if not r.ok:
        raise RuntimeError(f"Telegram API error {r.status_code}: {r.text}")

def main():
    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError("BOT_TOKEN sau CHAT_ID nu sunt setate")

    now = get_local_now()
    if now.hour >= QUIET_HOUR:
        print(f"Quiet hours: now is {now:%Y-%m-%d %H:%M %Z}. Skipping post.")
        return

    rss_urls = load_rss_list()
    state = load_state()
    posted_ids = set(state.get("posted_ids", []))

    new_items = []
    for url in rss_urls:
        feed = feedparser.parse(url)
        for entry in feed.entries[:30]:
            eid = entry_id(entry)
            if eid in posted_ids:
                continue
            title = (entry.get("title") or "").strip()
            link = (entry.get("link") or "").strip()
            if not title:
                continue
            new_items.append({"id": eid, "title": title, "link": link})

    if not new_items:
        print("Nu sunt știri noi.")
        return

    new_items = new_items[:MAX_ITEMS]
    message = build_message(new_items)

    if len(message) > 3800:
        message = message[:3800] + "\n…"

    send_telegram_message(message)

    for item in new_items:
        posted_ids.add(item["id"])

    state["posted_ids"] = list(posted_ids)[-2000:]
    save_state(state)

    print(f"Postate {len(new_items)} titluri.")

if __name__ == "__main__":
    main()
