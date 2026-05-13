"""
Vercel serverless function: campaign match endpoint.

Mirrors the Flask /api/match-reviews route in server.py, but packaged
as a Vercel Python serverless handler so it works on Vercel deploys
(where there is no long-running Flask process).
"""
import json
import os
import re
from http.server import BaseHTTPRequestHandler

import requests

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL   = "claude-sonnet-4-5"
ANTHROPIC_URL     = "https://api.anthropic.com/v1/messages"

SYSTEM_PROMPT = """You are helping Heights' Customer Care team surface customer reviews that are relevant to an upcoming brand or marketing campaign.

You will be given:
1. A campaign brief (themes, angle, target audience)
2. A JSON array of customer reviews (author, rating, source, date, body, productName, reviewId)

Your job:
- Identify which reviews best support the campaign brief
- Consider thematic alignment, not just keyword matches (e.g. a review about "waking up clearer" is relevant to a campaign about "morning energy" even if it doesn't say "energy")
- Consider sentiment fit (positive campaigns generally want positive reviews; reviews discussing transformation/journey can support empowerment-led campaigns even with mixed sentiment)
- Surface reviews that could yield quotable, on-brand customer voice for marketing use
- Use British English in all reasoning

Return ONLY a JSON object with this exact shape, no preamble or markdown:
{
  "matches": [
    {
      "reviewId": "string — the original reviewId",
      "relevanceScore": number 1-10,
      "reasoning": "one sentence explaining the match in British English"
    }
  ]
}

Include only reviews scoring 6 or higher. Return up to 15 matches. If no reviews score 6+, return an empty matches array."""


# Cap on reviews sent in a single request — protects the 10s Hobby timeout
# and keeps the Claude prompt under sane limits. If the filtered set is
# larger, the most recent N are used (the frontend already sorts by date).
MAX_REVIEWS_PER_REQUEST = 500


class handler(BaseHTTPRequestHandler):
    def _json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if not ANTHROPIC_API_KEY:
            return self._json(500, {"error": "ANTHROPIC_API_KEY not configured"})

        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            payload = json.loads(raw or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self._json(400, {"error": "Invalid JSON body"})

        brief   = (payload.get("brief") or "").strip()
        reviews = payload.get("reviews") or []

        if not brief:
            return self._json(400, {"error": "Missing brief"})
        if not reviews:
            return self._json(200, {"matches": []})

        if len(reviews) > MAX_REVIEWS_PER_REQUEST:
            reviews = reviews[:MAX_REVIEWS_PER_REQUEST]

        review_payload = [
            {
                "reviewId":    r.get("id", ""),
                "author":      r.get("name", ""),
                "rating":      r.get("rating", 0),
                "source":      r.get("source", ""),
                "date":        r.get("date", ""),
                "body":        r.get("body", ""),
                "productName": r.get("product", ""),
            }
            for r in reviews
        ]

        user_message = (
            f"Campaign brief:\n{brief}\n\n"
            f"Reviews (JSON):\n{json.dumps(review_payload, ensure_ascii=False)}"
        )

        try:
            resp = requests.post(
                ANTHROPIC_URL,
                headers={
                    "x-api-key":         ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type":      "application/json",
                },
                json={
                    "model":      ANTHROPIC_MODEL,
                    "max_tokens": 4000,
                    "system":     SYSTEM_PROMPT,
                    "messages":   [{"role": "user", "content": user_message}],
                },
                timeout=55,
            )
            resp.raise_for_status()
        except requests.RequestException:
            return self._json(502, {"error": "Anthropic API request failed"})

        body = resp.json()
        text = "".join(
            b.get("text", "") for b in body.get("content", []) if b.get("type") == "text"
        ).strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return self._json(502, {"error": "Could not parse model response"})

        matches = parsed.get("matches") or []
        matches.sort(key=lambda m: m.get("relevanceScore", 0), reverse=True)
        return self._json(200, {"matches": matches[:15]})
