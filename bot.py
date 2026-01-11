import os
import json
import calendar
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
PER_SOURCE_LIMIT = int(os.getenv("PER_SOURCE_LIMIT", "2"))

EMOJI_RULES = [
    ("🚨", ["critical", "zero-day", "0day", "actively exploited", "emergency"]),
    ("🦠", ["ransomware", "malware", "trojan", "worm", "botnet"]),
    ("🎣", ["phishing", "scam", "fraud", "spoof"]),
    ("🔓", ["breach", "leak", "exposed", "data stolen"]),
    ("🔐", ["password", "authentication", "auth", "login", "2fa", "passkey", "encryption"]),
    ("🛠️", ["patch", "update", "fixed", "mitigation", "released"]),
    ("🏢", ["microsoft", "google", "apple", "vmware", "cisco", "fortinet"]),
    ("💻", ["windows", "linux", "server", "cloud", "vm", "infrastructure"]),
]

def load_rss_list():
    with open(RSS_FILE, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip() and not line.startswith("#")]

def load_state():
    if not os.path.exists(STATE_FILE):
        return {"posted_ids": []}
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def entry_id(entry):
    return (
        entry.get("id")
        or entry.get("guid")
        or (entry.get("link", "") + "|" + entry.get("title", ""))
    )[:500]

def entry_ts(entry):
    if getattr(entry, "published_parsed", None):
        return int(calendar.timegm(entry.published_parsed))
    if getattr(entry, "updated_parsed", None):
        return int(calendar.timegm(entry.updated_parsed))
    return 0

def detect_emoji(title):
    t = title.lower()
    for emoji, keywords in EMOJI_RULES:
        for kw in keywords:
            if kw in t:
                return emoji
    return "📰"

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
        emoji = detect_emoji(it["title"])
        title = html_escape(it["title"])
        link = it["link"]
        src = html_escape(domain_of(link))
        blocks.append(f"{emoji} <a href=\"{link}\">{title}</a>\n<i>{src}</i>")

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
        return

    urls = load_rss_list()
    state = load_state()
    posted = set(state.get("posted_ids", []))
    collected = []

    for u in urls:
        feed = feedparser.parse(u)
        taken = 0

        for e in feed.entries[:50]:
            if taken >= PER_SOURCE_LIMIT:
                break

            eid = entry_id(e)
            if eid in posted:
                continue

            title = (e.get("title") or "").strip()
            link = (e.get("link") or "").strip()
            if not title or not link:
                continue

            collected.append({
                "id": eid,
                "title": title,
                "link": link,
                "ts": entry_ts(e)
            })
            taken += 1

    if not collected:
        return

    collected.sort(key=lambda x: x["ts"], reverse=True)
    items = collected[:MAX_ITEMS]

    message = build_message(items)
    if len(message) > 3800:
        message = message[:3800] + "\n…"

    send_message(message)

    for it in items:
        posted.add(it["id"])

    state["posted_ids"] = list(posted)[-2000:]
    save_state(state)

if __name__ == "__main__":
    main()
