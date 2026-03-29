"""
Heights Review Search Tool — Flask backend
"""
import os
import json
import re
import time
import threading
import logging
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, send_from_directory

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
PORT             = int(os.getenv("PORT", 3000))
SLACK_TOKEN      = os.getenv("SLACK_TOKEN", "")
OKENDO_API_KEY   = os.getenv("OKENDO_API_KEY", "fb60e365-8937-400d-90a1-9fc782131d41")
SLACK_CHANNEL_ID = "C07UNRQLTL7"
CACHE_TTL        = int(os.getenv("CACHE_TTL_MINUTES", 60)) * 60  # seconds
CACHE_DIR        = Path(__file__).parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__, static_folder="public", static_url_path="")

# ── Shared state ──────────────────────────────────────────────────────────────
_state = {
    "status":    "idle",   # idle | loading | ready | error
    "reviews":   None,
    "error":     None,
    "loaded_at": None,
    "progress":  {"okendo": 0, "slack": 0},
}
_lock = threading.Lock()


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
            with _lock:
                _state["progress"]["okendo"] = len(reviews)

        next_path = data.get("nextUrl")
        if not next_path or not batch:
            break
        # nextUrl omits /v1 — add it back
        url = "https://api.okendo.io/v1" + next_path

    log.info(f"[Okendo] Done — {len(reviews)} reviews")
    return reviews


def transform_okendo(r):
    rating = r.get("rating") or 0
    return {
        "id":        f"okendo_{r.get('reviewId','')}",
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

    log.info(f"[Slack] {len(messages)} total messages fetched")

    reviews = [r for r in (parse_trustpilot(m) for m in messages if is_trustpilot(m)) if r]
    log.info(f"[Slack] {len(reviews)} Trustpilot reviews parsed")
    return reviews


def is_trustpilot(m):
    if m.get("subtype") and m.get("subtype") != "bot_message":
        return False
    username = (m.get("username") or
                (m.get("bot_profile") or {}).get("name") or "").lower()
    return username == "trustpilot"


def get_message_text(m):
    blocks = m.get("blocks") or []
    if blocks:
        parts = []
        for block in blocks:
            btype = block.get("type")
            if btype == "section":
                txt = (block.get("text") or {}).get("text", "")
                if txt:
                    parts.append(txt)
            elif btype == "rich_text":
                for section in block.get("elements") or []:
                    line = "".join(
                        el.get("text", "") for el in (section.get("elements") or [])
                    )
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
        # First two *bold* items are name and title
        bold_matches = re.findall(r"\*([^*\n]+)\*", text)
        if len(bold_matches) < 2:
            return None

        name  = bold_matches[0].strip()
        title = bold_matches[1].strip()
        if not name or not title:
            return None

        # Star rating (e.g. ★★★★☆)
        star_match = re.search(r"([★☆]{1,5})", text)
        stars  = star_match.group(1) if star_match else ""
        rating = stars.count("★")
        if not rating:
            return None

        # Verified status
        verified = bool(re.search(r"\bVerified\b", text)) and not re.search(r"Not verified", text, re.IGNORECASE)

        # Body: lines after second bold heading, before the star line
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

        body_lines = [l.strip() for l in lines[body_start:star_line_idx] if l.strip()]
        body = " ".join(body_lines).strip()
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
        log.warning(f"[Slack] Failed to parse message: {e}")
        return None


# ── Cache helpers ─────────────────────────────────────────────────────────────
def cache_path(name):
    return CACHE_DIR / f"{name}.json"


def read_cache(name):
    p = cache_path(name)
    if not p.exists():
        return None
    age = time.time() - p.stat().st_mtime
    if age > CACHE_TTL:
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def write_cache(name, data):
    cache_path(name).write_text(json.dumps(data))


def clear_cache():
    for name in ("okendo", "slack"):
        p = cache_path(name)
        if p.exists():
            p.unlink()


# ── Data loading ──────────────────────────────────────────────────────────────
def load_data(force=False):
    with _lock:
        if _state["status"] == "loading":
            return
        _state["status"] = "loading"
        _state["error"]  = None

    def _run():
        try:
            okendo_raw = None if force else read_cache("okendo")
            slack_raw  = None if force else read_cache("slack")

            threads = []

            def fetch_okendo():
                nonlocal okendo_raw
                data = fetch_all_okendo()
                write_cache("okendo", data)
                okendo_raw = data
                with _lock:
                    _state["progress"]["okendo"] = len(data)

            def fetch_slack():
                nonlocal slack_raw
                data = fetch_all_slack()
                write_cache("slack", data)
                slack_raw = data
                with _lock:
                    _state["progress"]["slack"] = len(data)

            if okendo_raw is None:
                t = threading.Thread(target=fetch_okendo, daemon=True)
                t.start()
                threads.append(t)
            else:
                with _lock:
                    _state["progress"]["okendo"] = len(okendo_raw)

            if slack_raw is None:
                t = threading.Thread(target=fetch_slack, daemon=True)
                t.start()
                threads.append(t)
            else:
                with _lock:
                    _state["progress"]["slack"] = len(slack_raw) if slack_raw else 0

            for t in threads:
                t.join()

            combined = sorted(
                [transform_okendo(r) for r in (okendo_raw or [])] + (slack_raw or []),
                key=lambda r: r["date"],
                reverse=True,
            )

            with _lock:
                _state["reviews"]   = combined
                _state["status"]    = "ready"
                _state["loaded_at"] = datetime.now(tz=timezone.utc).isoformat()
            log.info(f"[Data] Ready — {len(combined)} total reviews")

        except Exception as e:
            log.error(f"[Data] Load failed: {e}")
            with _lock:
                _state["status"] = "error"
                _state["error"]  = str(e)

    threading.Thread(target=_run, daemon=True).start()


# ── Routes ────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory("public", "index.html")


@app.route("/api/reviews")
def api_reviews():
    with _lock:
        status = _state["status"]
        if status == "error":
            return jsonify({"status": "error", "error": _state["error"]}), 500
        if status != "ready":
            return jsonify({"status": status, "progress": _state["progress"]}), 202
        return jsonify({
            "status":    "ready",
            "count":     len(_state["reviews"]),
            "loaded_at": _state["loaded_at"],
            "reviews":   _state["reviews"],
        })


@app.route("/api/status")
def api_status():
    with _lock:
        return jsonify({
            "status":    _state["status"],
            "progress":  _state["progress"],
            "error":     _state["error"],
            "count":     len(_state["reviews"]) if _state["reviews"] else 0,
            "loaded_at": _state["loaded_at"],
        })


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    clear_cache()
    with _lock:
        _state["reviews"] = None
        _state["status"]  = "idle"
    load_data(force=True)
    return jsonify({"status": "refreshing"})


# ── Boot ──────────────────────────────────────────────────────────────────────
# Runs whether started via `python3 server.py` or gunicorn
if not SLACK_TOKEN:
    log.warning("[Slack] SLACK_TOKEN not set — Trustpilot reviews will be unavailable.")
load_data()

if __name__ == "__main__":
    log.info(f"Heights Review Search → http://localhost:{PORT}")
    app.run(host="0.0.0.0", port=PORT, threaded=True)
