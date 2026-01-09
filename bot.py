import os
import json
from datetime import datetime, timezone
import feedparser
import requests

RSS_FILE = "rss_list.txt"
STATE_FILE = "state.json"

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")  # pentru canal public: @username_canal
MAX_ITEMS = int(os.getenv("MAX_ITEMS", "10"))  # câte titluri per buletin

def load_rss_list():
    with open(RSS_FILE, "r", encoding="utf-8") as f:
        return [
            line.strip()
            for line in f
            if line.strip() and not line.strip().startswith("#")
        ]

def load_state():
    if not os.path.exists(STATE_FILE):
        return {"posted_ids": []}
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def entry_id(entry):
    # ID stabil pentru deduplicare
    return (
        entry.get("id")
        or entry.get("guid")
        or (entry.get("link", "") + "|" + entry.get("title", ""))
    )[:500]

def build_message(items):
    now = datetime.now(timezone.utc).astimezone()
    header = f"🗞️ IT Moldova – Buletin ({now:%d.%m.%Y %H:%M})\n\n"
    lines = []
    for i, item in enumerate(items, 1):
        lines.append(f"{i}. {item['title']}")
    return header + "\n".join(lines)

def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "disable_web_page_preview": True
    }
    response = requests.post(url, json=payload, timeout=30)
    if not response.ok:
        raise RuntimeError(
            f"Telegram API error {response.status_code}: {response.text}"
        )

def main():
    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError("BOT_TOKEN sau CHAT_ID nu sunt setate")

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
            if not title:
                continue

            new_items.append({
                "id": eid,
                "title": title
            })

    if not new_items:
        print("Nu sunt știri noi.")
        return

    # limitează numărul de titluri
    new_items = new_items[:MAX_ITEMS]

    message = build_message(new_items)

    # limită Telegram ~4096 caractere
    if len(message) > 3800:
        message = message[:3800] + "\n…"

    send_telegram_message(message)

    # salvează ID-urile postate
    for item in new_items:
        posted_ids.add(item["id"])

    state["posted_ids"] = list(posted_ids)[-2000:]
    save_state(state)

    print(f"Postate {len(new_items)} titluri.")

if __name__ == "__main__":
    main()
