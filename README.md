# Heights Review Search

Internal tool for searching Heights' Trustpilot and Okendo customer reviews.

## Local development

```bash
pip install -r requirements.txt
cp .env.example .env   # then fill in the tokens
python3 server.py
```

Then open http://localhost:3000.

## Environment variables

| Variable            | Required                       | Notes                                                             |
| ------------------- | ------------------------------ | ----------------------------------------------------------------- |
| `OKENDO_API_KEY`    | yes                            | Okendo store key (pre-set for Heights in `.env.example`).         |
| `SLACK_TOKEN`       | yes (for Trustpilot reviews)   | Slack bot token with `channels:history` for `#trustpilot_reviews`. |
| `ANTHROPIC_API_KEY` | yes (for **Campaign Match**)   | See below.                                                        |
| `CACHE_TTL_MINUTES` | no                             | Default `60`.                                                     |
| `PORT`              | no                             | Default `3000`.                                                   |

### `ANTHROPIC_API_KEY` — Campaign Match

The "Campaign Match" panel on the main page calls the Anthropic Messages API to
score reviews against a campaign brief. The feature is disabled (returns a
graceful error) if `ANTHROPIC_API_KEY` is not set.

- **Local dev:** add it to your `.env` file.
- **Production (Vercel):** add it in the Vercel dashboard under
  **Settings → Environment Variables**, then redeploy.
- **Other hosts:** set it in the platform's environment-variable settings
  (Railway, Heroku, etc.) and restart the app.

Get a key at <https://console.anthropic.com/settings/keys>.
