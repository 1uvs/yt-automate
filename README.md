# clip-factory

Auto-generate viral vertical clips from Twitch streamers (Kai Cenat, JasonTheWeen,
KSI, AMP, …), caption them, and queue them for one-tap publishing to YouTube Shorts.

Pipeline: **Twitch top clips → download → Whisper captions → 9:16 render → viral
title/desc/hashtags → review queue → publish to YouTube.**

> ⚠️ **Copyright:** you're re-posting other creators' content. YouTube can issue
> Content ID claims or copyright strikes (3 strikes = channel terminated). Stick to
> clip-friendly streamers, keep it transformative (captions/edits help), and review
> every clip before it publishes. This ships with a **review queue** on purpose.

## Two kinds of sources

- **Twitch** (`streamers:` in config) — pulls the already-popular community **Clips**
  via the Helix API. Best signal, lowest effort. Kai Cenat, JasonTheWeen, KSI, AMP…
- **YouTube VODs** (`youtube_streamers:` in config) — for streamers not on Twitch like
  **IShowSpeed**. Pulls recent uploads, auto-detects the **loudest / most hype moments**
  (Speed screams when things go viral → loudness peaks are a strong highlight signal),
  cuts them, and runs the same caption→render→queue pipeline.

  Notes on the YouTube source:
  - Speed's channel is mostly produced vlogs/music videos, not raw stream clips. The
    default `max_video_sec: 2400` skips his multi-hour travel vlogs (they're 1–2 GB
    downloads). Raise it to mine those too — expect bigger downloads and longer runs.
  - **Skip music videos** — they contain licensed audio (guaranteed Content ID claim).
  - He also streams on **Kick**; `yt-dlp` supports Kick URLs, so you can point another
    `youtube_streamers` entry at a Kick channel/VOD the same way.

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

## Easiest way — the web UI

```bash
source .venv/bin/activate
python app.py        # then open http://localhost:5000
```

From the browser you can:
1. **Connect YouTube channel** — one click, authorizes in your browser, shows your
   channel name + sub count once linked.
2. **⚡ Generate clips** — runs the whole pipeline with a live log; new clips appear
   in the review queue with an auto-made **thumbnail**, title, and description.
3. **Review** — watch each clip, tweak the title/description inline, then **Publish ▶**
   (or Reject). Publishing pushes the video *and* its thumbnail to your channel.

Everything (streamers, thresholds, layout, caption style, privacy) is tuned in
`config.yaml`.

## CLI (same thing, no browser)

```bash
python run.py                 # discover + render new clips into the queue
python review.py list         # see what's waiting
python review.py open <id>    # preview a clip in QuickTime
python review.py approve <id> # publish it to YouTube
python review.py approve-all  # publish everything pending
```

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
| Discover top clips (Twitch) | `clipfactory/twitch.py` | Twitch Helix API |
| Discover clips (YouTube/Kick) | `clipfactory/youtube_source.py` | yt-dlp |
| Highlight detection | `clipfactory/highlight.py` | ffmpeg + numpy (loudness peaks) |
| Download / cut segment | `clipfactory/download.py` | yt-dlp / ffmpeg |
| Captions (word-level) | `clipfactory/captions.py` | faster-whisper → `.ass` |
| 9:16 render + burn-in | `clipfactory/edit.py` | ffmpeg |
| Viral metadata + hook | `clipfactory/metadata.py` | Claude Haiku (optional) |
| Auto thumbnail | `clipfactory/thumbnail.py` | ffmpeg + Pillow |
| Publish (+ thumbnail) | `clipfactory/youtube.py` | YouTube Data API v3 |
| Web UI | `app.py` + `templates/` | Flask |
| Queue / state | `clipfactory/state.py` | SQLite |

> **Custom thumbnails** require a verified YouTube channel. If yours isn't verified
> yet, the video still publishes fine — YouTube just uses a frame instead of the
> generated thumbnail (a one-time phone verification at youtube.com/verify fixes it).
