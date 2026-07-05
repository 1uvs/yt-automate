# clip-factory

Auto-generate viral vertical clips from Twitch streamers (Kai Cenat, JasonTheWeen,
KSI, AMP, …), caption them, and queue them for one-tap publishing to YouTube Shorts.

Pipeline: **Twitch top clips → download → Whisper captions → 9:16 render → viral
title/desc/hashtags → review queue → publish to YouTube.**

> ⚠️ **Copyright:** you're re-posting other creators' content. YouTube can issue
> Content ID claims or copyright strikes (3 strikes = channel terminated). Stick to
> clip-friendly streamers, keep it transformative (captions/edits help), and review
> every clip before it publishes. This ships with a **review queue** on purpose.
> IShowSpeed is on Kick/YouTube (not Twitch) so he isn't pullable via the Clips API.

## Setup (one time)

```bash
cd ~/clip-factory
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

1. **Twitch API** — create an app at <https://dev.twitch.tv/console/apps>, copy the
   Client ID + Secret into `.env`.
2. **YouTube** — in Google Cloud Console: enable *YouTube Data API v3*, create an
   **OAuth client ID → Desktop app**, download the JSON, save it as
   `client_secret.json` in this folder. First publish opens a browser to authorize.
3. **(Optional) Better titles** — put an `ANTHROPIC_API_KEY` in `.env`. Without it,
   solid templates are used.

## Daily use

```bash
source .venv/bin/activate
python run.py                 # discover + render new clips into the queue
python review.py list         # see what's waiting
python review.py open <id>    # preview a clip in QuickTime
python review.py approve <id> # publish it to YouTube
python review.py approve-all  # publish everything pending
```

Tune everything (streamers, view thresholds, layout, caption style, privacy) in
`config.yaml`.

## Automate (optional)

Run discovery every few hours with cron (clips still wait for your approval):

```bash
crontab -e
# every 4 hours:
0 */4 * * * cd ~/clip-factory && ./.venv/bin/python run.py >> data/cron.log 2>&1
```

To go fully hands-off later, point cron at `review.py approve-all` too — but expect
copyright risk without human review.

## How it works

| Stage | File | Tool |
|-------|------|------|
| Discover top clips | `clipfactory/twitch.py` | Twitch Helix API |
| Download | `clipfactory/download.py` | yt-dlp |
| Captions (word-level) | `clipfactory/captions.py` | faster-whisper → `.ass` |
| 9:16 render + burn-in | `clipfactory/edit.py` | ffmpeg |
| Viral metadata | `clipfactory/metadata.py` | Claude Haiku (optional) |
| Publish | `clipfactory/youtube.py` | YouTube Data API v3 |
| Queue / state | `clipfactory/state.py` | SQLite |
