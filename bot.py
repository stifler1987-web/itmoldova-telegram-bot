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

MAX_ITEMS = int(os.getenv("MAX_ITEMS", "25"))
TIMEZONE = os.getenv("TIMEZONE", "Europe/Chisinau")
PER_SOURCE_LIMIT = int(os.getenv("PER_SOURCE_LIMIT", "5"))

# -------------------------
# Routing rules (simple keywords -> target category)
# Adjust keywords anytime.
# -------------------------
ROUTING_RULES = [
    {
        "keywords": ["cve-", "zero-day", "0-day", "exploit", "poc"],
        "target": "🛠️ Critical Vulnerabilities",
    },
    {
        "keywords": ["phishing", "scam", "fraud", "spoof"],
        "target": "🚨 Breaking & Incidents",
    },
    {
        "keywords": ["apt", "malware", "botnet", "campaign"],
        "target": "🧠 Threat Intelligence",
    },
]


# =========================
# CONFIG LOADERS
# =========================

def load_feeds_config():
    """
    Supports your feeds.json format:
    {
      "categories": {
        "Name": { "limit": X, "feeds": [...] },
        ...
      }
    }
    """
    with open(FEEDS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    cats = data.get("categories", {})
    cleaned = []

    if isinstance(cats, dict):
        # dict preserves insertion order in Python 3.7+
        for name, cfg in cats.items():
            if not isinstance(cfg, dict):
                continue

            cat_name = (name or "").strip()
            limit = int(cfg.get("limit") or 0)
            feeds = cfg.get("feeds") or []
            feeds = [x.strip() for x in feeds if isinstance(x, str) and x.strip()]

            if not cat_name or limit <= 0 or not feeds:
                continue

            cleaned.append({"name": cat_name, "limit": limit, "feeds": feeds})

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
                rules.append(
                    {
                        "emoji": emoji,
                        "keywords": [
                            k.lower() for k in keywords if isinstance(k, str) and k.strip()
                        ],
                    }
                )
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


def route_category(title, current_category, known_categories):
    """
    Simple routing: if title contains keywords, move to target category.
    Only routes to categories that exist in feeds.json (known_categories).
    """
    t = (title or "").lower()
    for rule in ROUTING_RULES:
        target = rule.get("target")
        if target and target in known_categories:
            for kw in rule.get("keywords", []):
                if kw and kw in t:
                    return target
    return current_category


def html_escape_text(s):
    """Escape for text nodes (between tags)."""
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def html_escape_attr(s):
    """Escape for attribute values, e.g. href="...". """
    return (
        (s or "")
        .replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


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


def fits_telegram_html(msg: str) -> bool:
    # Telegram limit is 4096 chars, but safer to stay under ~3800 bytes (UTF-8)
    return len(msg.encode("utf-8", errors="ignore")) <= 3800


# =========================
# OUTPUT
# =========================

def build_message(grouped_items, default_emoji, rules):
    now = local_now()
    header = f"🗞️ <b>IT Moldova</b>\n<i>Buletin {now:%d.%m.%Y %H:%M}</i>\n\n"

    sections = []

    for cat in grouped_items:
        cat_name = cat["name"]
        limit = cat["limit"]
        items = cat["items"]

        if not items:
            continue

        lines = [
            f"<b>{html_escape_text(cat_name)}</b>",
            "────────"
        ]

        for it in items[:limit]:
            emoji = detect_emoji(it["title"], default_emoji, rules)

            title = html_escape_text(it["title"])
            raw_link = it["link"]
            link = html_escape_attr(raw_link)

            src = html_escape_text(domain_of(raw_link))

            lines.append(f'{emoji} <a href="{link}">{title}</a>\n<i>{src}</i>')

        sections.append("\n".join(lines))

    return header + "\n\n".join(sections)


def send_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    r = requests.post(url, json=payload, timeout=30)
    if not r.ok:
        # Fallback: resend as plain text (no HTML parsing) to avoid pipeline failure
        payload.pop("parse_mode", None)
        r2 = requests.post(url, json=payload, timeout=30)
        if not r2.ok:
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

    known_categories = {c["name"] for c in categories}

    default_emoji, rules = load_emoji_rules()

    state = load_state()
    posted = set(state.get("posted_ids", []))

    # Collect routed items globally, then regroup by target category
    routed_items = []
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

                candidates.append(
                    {
                        "id": eid,
                        "title": title,
                        "link": link,
                        "ts": entry_ts(e),
                        "source_category": cat["name"],
                    }
                )
                taken += 1

        if not candidates:
            continue

        candidates.sort(key=lambda x: x["ts"], reverse=True)

        # Take up to category limit from this category's feeds (before routing)
        selected = candidates[: cat["limit"]]

        for it in selected:
            it["category"] = route_category(it["title"], it["source_category"], known_categories)
            routed_items.append(it)
            all_selected_ids.add(it["id"])

    if not routed_items:
        return

    # Regroup by routed category, keep category order from feeds.json
    cat_map = {c["name"]: {"name": c["name"], "limit": c["limit"], "items": []} for c in categories}
    for it in routed_items:
        target = it.get("category") or it.get("source_category")
        if target not in cat_map:
            target = it.get("source_category")
        cat_map[target]["items"].append(it)

    # Sort items inside each category by time desc
    for c in categories:
        cat_map[c["name"]]["items"].sort(key=lambda x: x.get("ts", 0), reverse=True)

    # Enforce MAX_ITEMS globally while preserving category order and per-category limits
    grouped_items = []
    total = 0
    kept = []

    for c in categories:
        name = c["name"]
        limit = c["limit"]
        items = cat_map[name]["items"]

        if not items:
            grouped_items.append({"name": name, "limit": limit, "items": []})
            continue

        allowed = []
        for it in items:
            if total >= MAX_ITEMS:
                break
            if len(allowed) >= limit:
                break
            allowed.append(it)
            kept.append(it)
            total += 1

        grouped_items.append({"name": name, "limit": limit, "items": allowed})

        if total >= MAX_ITEMS:
            break

    if not kept:
        return

    # Build message and ensure it fits Telegram without breaking HTML
    message = build_message(grouped_items, default_emoji, rules)
    if not fits_telegram_html(message):
        # reduce items from the end until it fits
        for _ in range(300):
            removed = False
            for gi in reversed(grouped_items):
                if gi["items"]:
                    gi["items"].pop()
                    removed = True
                    break
            if not removed:
                break
            message = build_message(grouped_items, default_emoji, rules)
            if fits_telegram_html(message):
                # also keep kept in sync (best effort)
                break

        if not fits_telegram_html(message):
            # last resort: send header only
            now = local_now()
            message = f"🗞️ <b>IT Moldova</b>\n<i>Buletin {now:%d.%m.%Y %H:%M}</i>\n\n…"

    send_message(message)

    for it in kept:
        posted.add(it["id"])

    state["posted_ids"] = list(posted)[-2000:]
    save_state(state)


if __name__ == "__main__":
    main()
