"""
Heights Review Search — static site builder
Fetches all reviews from Okendo + Slack and generates dist/index.html
with the data embedded. Deployed to GitHub Pages via Actions.
"""
import json
import os
import re
import logging
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

OKENDO_API_KEY   = os.getenv("OKENDO_API_KEY", "fb60e365-8937-400d-90a1-9fc782131d41")
SLACK_TOKEN      = os.getenv("SLACK_TOKEN", "")
SLACK_CHANNEL_ID = "C07UNRQLTL7"
DIST_DIR         = Path(__file__).parent / "dist"
TEMPLATE         = Path(__file__).parent / "public" / "index.html"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


# ── Okendo ────────────────────────────────────────────────────────────────────

def fetch_all_okendo():
    log.info("[Okendo] Starting fetch…")
    reviews = []
    url = f"https://api.okendo.io/v1/stores/{OKENDO_API_KEY}/reviews?limit=100"
    headers = {"Authorization": f"Bearer {OKENDO_API_KEY}"}
    page = 0

    while url:
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        batch = data.get("reviews", [])
        reviews.extend(batch)
        page += 1
        if page % 20 == 0:
            log.info(f"[Okendo] Page {page} — {len(reviews)} reviews so far")
        next_path = data.get("nextUrl")
        if not next_path or not batch:
            break
        url = "https://api.okendo.io/v1" + next_path

    log.info(f"[Okendo] Done — {len(reviews)} reviews")
    return reviews


def transform_okendo(r):
    rating = r.get("rating") or 0
    return {
        "id":        f"okendo_{r.get('reviewId', '')}",
        "source":    "okendo",
        "name":      (r.get("reviewer") or {}).get("displayName") or "Anonymous",
        "title":     r.get("title") or "",
        "body":      r.get("body") or "",
        "rating":    rating,
        "date":      r.get("dateCreated", ""),
        "verified":  (r.get("reviewer") or {}).get("isVerified", False),
        "product":   r.get("productName") or "",
        "sentiment": "positive" if rating >= 4 else "negative",
    }


# ── Slack / Trustpilot ────────────────────────────────────────────────────────

def fetch_all_slack():
    if not SLACK_TOKEN:
        log.info("[Slack] No SLACK_TOKEN — skipping.")
        return []

    log.info("[Slack] Starting fetch…")
    messages = []
    cursor = ""
    headers = {"Authorization": f"Bearer {SLACK_TOKEN}"}

    while True:
        params = {"channel": SLACK_CHANNEL_ID, "limit": 200}
        if cursor:
            params["cursor"] = cursor
        resp = requests.get("https://slack.com/api/conversations.history",
                            params=params, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            log.error(f"[Slack] API error: {data.get('error')}")
            break
        messages.extend(data.get("messages", []))
        if not data.get("has_more"):
            break
        cursor = (data.get("response_metadata") or {}).get("next_cursor", "")
        if not cursor:
            break

    log.info(f"[Slack] {len(messages)} total messages")
    reviews = [r for r in (parse_trustpilot(m) for m in messages if is_trustpilot(m)) if r]
    log.info(f"[Slack] {len(reviews)} Trustpilot reviews parsed")
    return reviews


def is_trustpilot(m):
    if m.get("subtype") and m.get("subtype") != "bot_message":
        return False
    username = (m.get("username") or (m.get("bot_profile") or {}).get("name") or "").lower()
    return username == "trustpilot"


def get_message_text(m):
    blocks = m.get("blocks") or []
    if blocks:
        parts = []
        for block in blocks:
            if block.get("type") == "section":
                txt = (block.get("text") or {}).get("text", "")
                if txt:
                    parts.append(txt)
            elif block.get("type") == "rich_text":
                for section in block.get("elements") or []:
                    line = "".join(el.get("text", "") for el in (section.get("elements") or []))
                    if line:
                        parts.append(line)
        if parts:
            return "\n".join(parts)
    return m.get("text") or ""


def parse_trustpilot(message):
    text = get_message_text(message)
    if not text.strip() or not re.search(r"[★☆]", text):
        return None
    try:
        bold_matches = re.findall(r"\*([^*\n]+)\*", text)
        if len(bold_matches) < 2:
            return None
        name  = bold_matches[0].strip()
        title = bold_matches[1].strip()
        if not name or not title:
            return None
        star_match = re.search(r"([★☆]{1,5})", text)
        stars  = star_match.group(1) if star_match else ""
        rating = stars.count("★")
        if not rating:
            return None
        verified = bool(re.search(r"\bVerified\b", text)) and not re.search(r"Not verified", text, re.IGNORECASE)
        lines = text.split("\n")
        star_line_idx = next((i for i, l in enumerate(lines) if re.search(r"[★☆]", l)), len(lines))
        bold_count = 0
        body_start = -1
        for i, line in enumerate(lines):
            if re.match(r"^\*[^*]+\*\s*$", line.strip()):
                bold_count += 1
                if bold_count == 2:
                    body_start = i + 1
                    break
        if body_start == -1:
            return None
        body = " ".join(l.strip() for l in lines[body_start:star_line_idx] if l.strip()).strip()
        if not body:
            return None
        ts   = float(message.get("ts", 0))
        date = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        return {
            "id":        f"slack_{message['ts']}",
            "source":    "trustpilot",
            "name":      name,
            "title":     title,
            "body":      body,
            "rating":    rating,
            "date":      date,
            "verified":  verified,
            "product":   "",
            "sentiment": "positive" if rating >= 4 else "negative",
        }
    except Exception as e:
        log.warning(f"Parse error: {e}")
        return None


# ── Build ─────────────────────────────────────────────────────────────────────

def build():
    okendo_raw = fetch_all_okendo()
    slack_raw  = fetch_all_slack()

    combined = sorted(
        [transform_okendo(r) for r in okendo_raw] + slack_raw,
        key=lambda r: r["date"],
        reverse=True,
    )
    log.info(f"[Build] {len(combined)} total reviews")

    built_at = datetime.now(tz=timezone.utc).isoformat()
    data_script = (
        "<script>\n"
        f"window.__HEIGHTS_REVIEWS__ = {json.dumps(combined, separators=(',', ':'))};\n"
        f"window.__HEIGHTS_BUILT_AT__ = {json.dumps(built_at)};\n"
        "</script>"
    )

    template = TEMPLATE.read_text(encoding="utf-8")
    output   = template.replace("<!-- DATA_INJECTION_POINT -->", data_script)

    DIST_DIR.mkdir(exist_ok=True)
    out_path = DIST_DIR / "index.html"
    out_path.write_text(output, encoding="utf-8")

    size_kb = out_path.stat().st_size / 1024
    log.info(f"[Build] Written to {out_path} ({size_kb:.0f} KB)")


if __name__ == "__main__":
    build()
