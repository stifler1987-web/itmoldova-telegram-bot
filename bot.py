import os
import json
import calendar
from datetime import datetime, timezone
from urllib.parse import urlparse

import feedparser
import requests

FEEDS_FILE = "feeds.json"
STATE_FILE = "state.json"
RULES_FILE = "emoji_rules.json"

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

MAX_ITEMS = int(os.getenv("MAX_ITEMS", "10"))
TIMEZONE = os.getenv("TIMEZONE", "Europe/Chisinau")
PER_SOURCE_LIMIT = int(os.getenv("PER_SOURCE_LIMIT", "2"))


# =========================
# CONFIG LOADERS
# =========================

def load_feeds_config():
    with open(FEEDS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    cats = data.get("categories", {})
    cleaned = []

    # EXPECTED FORMAT:
    # "categories": {
    #   "Category Name": { "limit": X, "feeds": [...] }
    # }
    if isinstance(cats, dict):
        for name, cfg in cats.items():
            if not isinstance(cfg, dict):
                continue

            cat_name = (name or "").strip()
            limit = int(cfg.get("limit") or 0)
            feeds = cfg.get("feeds") or []
            feeds = [x.strip() for x in feeds if isinstance(x, str) and x.strip()]

            if not cat_name or limit <= 0 or not feeds:
                continue

            cleaned.append({
                "name": cat_name,
                "limit": limit,
                "feeds": feeds
            })

    return cleaned


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
        raw_rules = data.get("rules", [])

        for r in raw_rules:
            emoji = r.get("emoji")
            keywords = r.get("keywords", [])
            if emoji and isinstance(keywords, list):
                rules.append({
                    "emoji": emoji,
                    "keywords": [k.lower() for k in keywords if isinstance(k, str) and k.strip()]
                })
    except Exception:
        pass

    return default, rules


# =========================
# HELPERS
# =========================

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


def detect_emoji(title, default, rules):
    t = (title or "").lower()
    for r in rules:
        for kw in r["keywords"]:
            if kw in t:
                return r["emoji"]
    return default


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


# =========================
# OUTPUT
# =========================

def build_message(grouped_items, default_emoji, rules):
    now = local_now()
    header = f"🗞️ <b>IT Moldova</b>\n<i>Buletin {now:%d.%m.%Y %H:%M}</i>\n\n"

    sections = []
    for cat_name, items in grouped_items:
        if not items:
            continue

        lines = [f"<b>{html_escape(cat_name)}</b>"]
        for it in items:
            emoji = detect_emoji(it["title"], default_emoji, rules)
            title = html_escape(it["title"])
            link = it["link"]
            src = html_escape(domain_of(link))
            lines.append(f"{emoji} <a href=\"{link}\">{title}</a>\n<i>{src}</i>")

        sections.append("\n".join(lines))

    return header + "\n\n".join(sections)


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


# =========================
# MAIN
# =========================

def main():
    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError("Missing BOT_TOKEN or CHAT_ID")

    now = local_now()
    if now.hour >= 22 or now.hour < 9:
        return

    categories = load_feeds_config()
    if not categories:
        return

    default_emoji, rules = load_emoji_rules()
    state = load_state()
    posted = set(state.get("posted_ids", []))

    grouped_items = []
    all_selected = []
    all_selected_ids = set()

    for cat in categories:
        candidates = []

        for feed_url in cat["feeds"]:
            feed = feedparser.parse(feed_url)
            taken = 0

            for e in feed.entries[:50]:
                if taken >= PER_SOURCE_LIMIT:
                    break

                eid = entry_id(e)
                if eid in posted or eid in all_selected_ids:
                    continue

                title = (e.get("title") or "").strip()
                link = (e.get("link") or "").strip()
                if not title or not link:
                    continue

                candidates.append({
                    "id": eid,
                    "title": title,
                    "link": link,
                    "ts": entry_ts(e)
                })
                taken += 1

        if not candidates:
            grouped_items.append((cat["name"], []))
            continue

        candidates.sort(key=lambda x: x["ts"], reverse=True)
        selected = candidates[:cat["limit"]]

        for it in selected:
            all_selected.append(it)
            all_selected_ids.add(it["id"])

        grouped_items.append((cat["name"], selected))

    if not all_selected:
        return

    message = build_message(grouped_items, default_emoji, rules)
    if len(message) > 3800:
        message = message[:3800] + "\n…"

    send_message(message)

    for it in all_selected:
        posted.add(it["id"])

    state["posted_ids"] = list(posted)[-2000:]
    save_state(state)


if __name__ == "__main__":
    main()
