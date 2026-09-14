# clip-factory

Auto-generate viral vertical clips from Twitch streamers (Kai Cenat, JasonTheWeen,
KSI, AMP, …), caption them, and queue them for one-tap publishing to YouTube Shorts.

Pipeline: **Twitch top clips → download → pick the best stretch → transcribe →
safety/quality screen → captions → 9:16 render → viral title/desc/hashtags →
review queue → publish to YouTube.**

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
python app.py        # then open http://localhost:5050
```

From the browser you can:
1. **Connect YouTube channel** — one click, authorizes in your browser, shows your
   channel name + sub count once linked.
2. **⚡ Generate clips** — runs the whole pipeline with a live log; new clips appear
   in the review queue with an auto-made **thumbnail**, title, and description.
3. **Review** — watch each clip, tweak the title/description inline, then either
   **Publish now ▶**, or **⏰ Schedule** it to auto-post at the next peak time. Use
   **⏰ Schedule all at peak times** to drip your whole queue across the best hours.

Scheduled clips upload as *private* and YouTube flips them public automatically at
the posting time (set the slots + timezone under `schedule:` in `config.yaml`).
They appear in an **⏰ Scheduled** section with their go-live time.

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

## Virality coach (the learning loop)

No tool can *guarantee* virality. What this does is **tilt the odds toward whatever
already works on your channel**, measured with real data:

- Every clip is tagged with its **format, streamer, length, and hashtags**.
- After clips publish, the coach pulls **YouTube Analytics** (views, retention, and
  **subscribers gained**) and ranks each dimension.
- It then **feeds the winners back into generation**: the best hashtags get boosted,
  the best-converting streamers get prioritized, and it tells you the best format +
  length to target.

See it in the web UI (📊 Virality A/B panel → *Refresh from YouTube*) or run:

```bash
python stats.py
```

Requires: the **YouTube Analytics API** enabled in Google Cloud, a channel re-connect
(new analytics permission), and enough published clips (~3+) with a day or two for
data to populate. Early on it just says "publish more" — because consistency and
volume are genuinely the biggest levers.

## Fully hands-off (autopilot)

`autopilot.py` generates new clips **and** auto-schedules them to peak times with no
review. A **launchd agent** runs it daily at 10:00:

```bash
launchctl print gui/$(id -u)/com.clipfactory.autopilot   # is it loaded? when did it last run?
```

The job lives in `~/Library/LaunchAgents/com.clipfactory.autopilot.plist`. It replaced
the old cron entry for one reason: **cron silently skips a run if the Mac is asleep at
10:00 and never makes it up.** A laptop that sleeps overnight simply never posts.
launchd runs a missed job as soon as the Mac wakes, which is what you actually want.

**To pause / stop autopilot:**
```bash
launchctl bootout gui/$(id -u)/com.clipfactory.autopilot     # stop
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.clipfactory.autopilot.plist   # start again
```
Edit `StartCalendarInterval` in the plist to change the hour (bootout + bootstrap to
apply). Cap clips per run with `autopilot.max_per_run` in `config.yaml`.

**macOS gotchas (important):**
- **PATH.** launchd and cron run with a minimal PATH that does *not* include
  Homebrew's `/opt/homebrew/bin`, so `yt-dlp` and `ffmpeg` are invisible to them.
  This silently killed every scheduled run between July and September 2026 — the log
  filled with `No such file or directory: 'yt-dlp'` while manual runs worked fine.
  `clipfactory/binaries.py` now resolves all three binaries to absolute paths, so
  this cannot recur regardless of how the pipeline is launched. Override with
  `YT_DLP_BIN` / `FFMPEG_BIN` / `FFPROBE_BIN` if they live somewhere unusual.
- OAuth tokens for an **unverified** app expire after ~7 days, so headless posting
  stops until you open the app and click **Connect** again (or verify the app with
  Google). Reconnecting weekly is the simplest fix for personal use.

> ⚠️ Fully automatic means clips post with **no human review** — highest copyright
> risk. Watch the first few days; if a strike lands, `launchctl bootout` the job and go
> back to reviewing in the UI.

## Discovery-only automation (safer)

Generate into the review queue on a timer, but still approve/schedule by hand:

```bash
0 */6 * * * cd ~/clip-factory && ./.venv/bin/python run.py >> data/cron.log 2>&1
```

## How a clip is processed

The step order is built around one rule: **never pay for work on a clip that won't
be published.** Rendering is the expensive part, so everything that can reject a
clip happens before it.

| # | Step | What it does | Why it's here |
|---|------|--------------|---------------|
| 1 | **Discover** | Top clips per streamer, best-first | All streamers are queried in parallel; near-duplicate titles (the same moment clipped by five viewers) are dropped |
| 2 | **Download** | yt-dlp fetches the source | Runs a couple of clips ahead of the cursor, so downloading overlaps with rendering instead of blocking it |
| 3 | **Pick the stretch** | Loudness picks the best `max_final_sec` window | A 75s source has to lose ~17s. The payoff is usually the loudest moment and usually near the end, so keeping the *first* 58s often cuts the punchline |
| 4 | **Transcribe** | Whisper word timings | Needed by both the screener and the title writer, so it happens once and both read it |
| 5 | **Screen** ⛔ | Copyright/brand risk + viral quality | The last cheap step. Rejecting here costs a download; rejecting after step 6 would cost a full render |
| 6 | **Caption** | One PNG per word | Only clips that survived screening get here |
| 7 | **Render** | 9:16 crop/blur + caption overlays | ffmpeg |
| 8 | **Metadata** | Title, description, hashtags, hook | Grounded in the real transcript, steered by the learned strategy |
| 9 | **Thumbnail** | Vision picks the most clickable frame | Falls back to ffmpeg's heuristic |
| 10 | **Queue** | Into the review queue (or auto-scheduled) | |

A clip that fails on a transient error (network blip, yt-dlp hiccup) is retried on
the next run rather than blacklisted — it's given up on after 3 attempts.

## Where the code lives

| Stage | File | Tool |
|-------|------|------|
| Discover top clips (Twitch) | `clipfactory/twitch.py` | Twitch Helix API |
| Discover clips (YouTube/Kick) | `clipfactory/youtube_source.py` | yt-dlp |
| Highlight / best-stretch detection | `clipfactory/highlight.py` | ffmpeg + numpy (loudness) |
| Download / cut segment | `clipfactory/download.py` | yt-dlp / ffmpeg |
| Download look-ahead | `clipfactory/prefetch.py` | thread pool |
| Retry + backoff | `clipfactory/retry.py` | — |
| Transcribe + captions (word-level) | `clipfactory/captions.py` | faster-whisper → PNG overlays |
| Burned-in caption detection | `clipfactory/burnin.py` | ffmpeg + numpy |
| Safety + quality screen | `clipfactory/screen.py` | GPT-mini |
| 9:16 render + caption burn-in | `clipfactory/edit.py` | ffmpeg |
| Viral metadata + hook | `clipfactory/metadata.py` | GPT-mini → Gemini → Claude → templates |
| Hashtag strategy | `clipfactory/hashtags.py` | curated + learned boost |
| Virality coach (learning) | `clipfactory/analytics.py` | YouTube Analytics API |
| Strategy brain (evolves daily) | `clipfactory/strategy.py` | GPT |
| Auto thumbnail | `clipfactory/thumbnail.py` + `vision_thumb.py` | ffmpeg + Pillow + GPT vision |
| Publish (+ thumbnail) | `clipfactory/youtube.py` | YouTube Data API v3 |
| Scheduling / peak slots | `clipfactory/schedule.py` | — |
| Disk cleanup | `clipfactory/maintenance.py` | — |
| Web UI | `app.py` + `templates/` | Flask |
| Queue / state | `clipfactory/state.py` | SQLite |

> **Custom thumbnails** require a verified YouTube channel. If yours isn't verified
> yet, the video still publishes fine — YouTube just uses a frame instead of the
> generated thumbnail (a one-time phone verification at youtube.com/verify fixes it).
