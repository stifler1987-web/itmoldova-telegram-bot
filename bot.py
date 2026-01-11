import os
import json
import calendar
from datetime import datetime, timezone
from urllib.parse import urlparse

import feedparser
import requests

# =========================
# CONFIG
# =========================

FEEDS_FILE = "feeds.json"
STATE_FILE = "state.json"
RULES_FILE = "emoji_rules.json"

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

MAX_ITEMS = 25
PER_SOURCE_LIMIT = 5
TIMEZONE = "Europe/Chisinau"

# RSS realistic window
MAX_AGE_HOURS = 72

# -------------------------
# Routing rules (CORECTATE)
# -------------------------
ROUTING_RULES = [
    # 🛠️ strict: doar vulnerabilități reale
    {
        "keywords": ["cve-", "zero-day", "0-day", "exploit", "poc", "rce"],
        "target": "🛠️ Critical Vulnerabilities",
    },

    # 🚨 policy / CISA / regulator → Breaking
    {
        "keywords": ["cisa", "directive", "policy", "sunsets", "regulator"],
        "target": "🚨 Breaking & Incidents",
    },

    # 🚨 incidente generale
    {
        "keywords": ["phishing", "scam", "fraud", "spoof", "breach"],
        "target": "🚨 Breaking & Incidents",
    },

    # 🧠 threat intel
    {
        "keywords": ["apt", "malware", "botnet", "campaign"],
        "target": "🧠 Threat Intelligence",
    },
]

# =========================
# HELPERS
# =========================

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
    return (e.get("id") or e.get("guid") or e.get("link", "") + "|" + e.get("title", ""))[:500]

def html_escape(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def domain_of(url):
    try:
        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return ""

# =========================
# LOADERS
# =========================

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
        json.dump(state, f, ensure_ascii=False, indent=2)

def load_emoji_rules():
    default = "📰"
    rules = []

    try:
        with open(RULES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        default = data.get("default", default)
        for r in data.get("rules", []):
            rules.append({
                "emoji": r["emoji"],
                "keywords": [k.lower() for k in r["keywords"]]
            })
    except Exception:
        pass

    return default, rules

def detect_emoji(title, default, rules):
    t = (title or "").lower()
    for r in rules:
        for kw in r["keywords"]:
            if kw in t:
                return r["emoji"]
    return default

def route_category(title, current, known):
    t = (title or "").lower()
    for r in ROUTING_RULES:
        if r["target"] in known:
            for kw in r["keywords"]:
                if kw in t:
                    return r["target"]
    return current

# =========================
# TELEGRAM
# =========================

def send_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(url, json={
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }, timeout=30)

# =========================
# MAIN
# =========================

def main():
    now = local_now()
    if now.hour < 9 or now.hour >= 22:
        return

    feeds = load_feeds()
    state = load_state()
    posted = set(state.get("posted_ids", []))
    default_emoji, emoji_rules = load_emoji_rules()
    cutoff = cutoff_ts()

    known_categories = set(feeds.keys())
    cat_map = {k: [] for k in feeds}

    for cat_name, cfg in feeds.items():
        for feed_url in cfg["feeds"]:
            feed = feedparser.parse(feed_url)
            taken = 0

            for e in feed.entries[:50]:
                if taken >= PER_SOURCE_LIMIT:
                    break

                ts = entry_ts(e)
                if ts and ts < cutoff:
                    continue

                eid = entry_id(e)
                if eid in posted:
                    continue

                title = (e.get("title") or "").strip()
                link = (e.get("link") or "").strip()
                if not title or not link:
                    continue

                final_cat = route_category(title, cat_name, known_categories)
                cat_map[final_cat].append({
                    "id": eid,
                    "title": title,
                    "link": link,
                    "ts": ts
                })
                taken += 1

    message = f"🗞️ <b>IT Moldova</b>\n<i>Buletin {now:%d.%m.%Y %H:%M}</i>\n\n"
    total = 0
    kept_ids = []

    for cat, items in cat_map.items():
        if not items:
            continue

        items.sort(key=lambda x: x["ts"], reverse=True)
        message += f"<b>{html_escape(cat)}</b>\n────────\n"

        for it in items:
            if total >= MAX_ITEMS:
                break
            emoji = detect_emoji(it["title"], default_emoji, emoji_rules)
            message += (
                f'{emoji} <a href="{html_escape(it["link"])}">{html_escape(it["title"])}</a>\n'
                f'<i>{domain_of(it["link"])}</i>\n\n'
            )
            kept_ids.append(it["id"])
            total += 1

        message += "\n"

    if total == 0:
        return

    send_message(message)

    posted.update(kept_ids)
    save_state({"posted_ids": list(posted)[-2000:]})

if __name__ == "__main__":
    main()
