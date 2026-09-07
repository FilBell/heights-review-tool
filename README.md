# Heights Review Search

Internal tool for searching Heights' Trustpilot and Okendo customer reviews.

## Local development

```bash
pip install -r requirements-dev.txt   # local dev (adds Flask + gunicorn)
cp .env.example .env                   # then fill in the tokens
python3 server.py
```

> Production (Vercel) only installs `requirements.txt` — a minimal set covering
> `build.py` and the `api/` serverless functions. Flask is only needed for the
> local dev server, so it lives in `requirements-dev.txt`.

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

---

## Ownership

| | |
| --- | --- |
| **Built by** | Fil Bell, March–May 2026 |
| **Current owner** | Fil Bell |
| **Backup owner** | _unassigned — needs filling in_ |
| **Escalate to** | _to be set_ |
| **Who consumes the output** | Customer care and marketing, searching reviews ad hoc |

## ⚠️ This repo is PUBLIC, and that needs resolving

**Why it is public:** the site is served by **GitHub Pages**, and Pages from a
private repo requires GitHub Team or above. On a free personal account, public
was the only way to make it work. It was a structural constraint, not an
oversight.

**What that means in practice:**

- The built site at `https://filbell.github.io/heights-review-tool/` is **live
  and world-readable** (~5.7 MB), rebuilt hourly by `build.yml`.
- `build.py` embeds the whole review corpus into `dist/index.html` as inline JSON
  (`window.__HEIGHTS_REVIEWS__`), including reviewer display names.
- The reviews are already public on Trustpilot and Okendo, so this is **not** a
  leak of confidential data. But it is an internal tool, publicly browsable,
  aggregating customer names in one place — a data-protection question even when
  every input is public.
- **A real `OKENDO_API_KEY` was committed in `.env.example`** in this public
  repo. It has been replaced with a placeholder, but **it remains in git
  history**, so the only real fix is to **rotate that key**. `heights-voc` uses
  the same credential and its Actions secret must be updated at the same time.

**Two ways out:**

| Option | Cost | Notes |
| --- | --- | --- |
| Upgrade the org to GitHub **Team** | $4/user/month | Pages from a private repo; keeps everything as-is otherwise |
| Move hosting to **Vercel behind Google `@heights.com` auth** | £0 | The pattern already used by `benchmarking-insights` and `growth-partnership-dashboard`. Make the repo private, turn Pages off. |

The second is cheaper and gives real access control, which the current setup has
no way to express. `vercel.json` already exists in this repo.

## How it runs

| | |
| --- | --- |
| **Trigger** | GitHub Actions cron `0 * * * *` — **hourly**, plus on push to `main` |
| **Entry point** | `build.py` → `dist/`, deployed to GitHub Pages |
| **Local dev server** | `server.py` (Flask) — not used in production |
| **Typical duration** | ~1m20s |
| **Billable cost** | **£0 today** — Actions is free for public repos. Making this repo private without moving off Pages would add ~1,440 billable minutes/month, more than the entire free allowance. |

## What it reads and writes

| Direction | System | Notes |
| --- | --- | --- |
| Reads | Okendo API | all Heights reviews |
| Reads | Slack `#trustpilot_reviews` | Trustpilot reviews, via `channels:history` |
| Writes | GitHub Pages | the public static site |
| Writes | This repo | `dist/index.html` is committed |

## Secrets and access

| Secret | Stored as | Notes |
| --- | --- | --- |
| `OKENDO_API_KEY` | Actions secret | **rotate — was committed publicly** |
| `SLACK_TOKEN` | Actions secret | named `SLACK_BOT_TOKEN` in six other repos; standardise on handover |
| `ANTHROPIC_API_KEY` | Vercel / local only | Campaign Match; feature degrades gracefully without it |

## If it fails

**How you find out**
- Nothing today — `build.yml` has no failure alert. Add the standard one from
  `heights-standards/failure-alert.yml`.
- Because the last good `dist/` stays deployed, **a failed build leaves a working
  but increasingly stale site**. There is no visible symptom.

**What breaks downstream, and how urgent**
- Review search returns stale results. Low urgency — but silently stale is worse
  than obviously broken, because people trust what they see.

**How to diagnose**
1. Actions → *Build & Deploy Review Search* → latest run.
2. Common causes:
   - **Okendo 401** → key rotated (expected, see above).
   - **Slack `not_in_channel`** → bot removed from `#trustpilot_reviews`.
   - **Pages deploy failure** → check the `github-pages` environment.

**Is a re-run safe?**
- **Yes, fully idempotent.** The build regenerates `dist/` from scratch and
  redeploys. No external writes, no state to corrupt.

**How to recover**
- Re-run the workflow. The site is a pure function of the two upstreams.

**If nobody can fix it**
- Search Okendo and Trustpilot directly. Nothing is lost — this tool holds no
  data of its own.

## Significant changes

| Date | Change | By |
| --- | --- | --- |
| 2026-09-07 | Brought to the Heights README standard; real Okendo key replaced with a placeholder in `.env.example`; public-repo/Pages constraint documented | Fil |
