"""Resolve the external binaries (yt-dlp, ffmpeg, ffprobe) to absolute paths.

cron and launchd run jobs with a minimal PATH — `/usr/bin:/bin` — which does not
include Homebrew's `/opt/homebrew/bin`. Every scheduled run therefore died with
"No such file or directory: 'yt-dlp'" while the same code run by hand from a
terminal worked fine, because an interactive shell has Homebrew on PATH.

So we do two things here:

* resolve each binary to an absolute path, searching PATH *and* the usual
  install prefixes, so our own subprocess calls never depend on the caller's
  environment; and
* prepend those prefixes to os.environ["PATH"], because yt-dlp shells out to
  ffmpeg itself (`--merge-output-format mp4`) and needs to find it on its own.
"""
from __future__ import annotations

import os
import shutil

# Checked in order, after PATH. Covers Homebrew on Apple Silicon and Intel,
# plus MacPorts.
_EXTRA_DIRS = ("/opt/homebrew/bin", "/usr/local/bin", "/opt/local/bin")


def ensure_path() -> None:
    """Add the usual install prefixes to PATH for any child process we spawn."""
    parts = os.environ.get("PATH", "").split(os.pathsep)
    missing = [d for d in _EXTRA_DIRS if os.path.isdir(d) and d not in parts]
    if missing:
        os.environ["PATH"] = os.pathsep.join(missing + parts)


def resolve(name: str) -> str:
    """Absolute path to `name`, falling back to the bare name if not found.

    Falling back rather than raising keeps a missing ffprobe from taking down
    the whole app at import time — the caller still fails, with the same error
    it used to give.
    """
    override = os.environ.get(f"{name.replace('-', '_').upper()}_BIN")
    if override:
        return override
    found = shutil.which(name)
    if found:
        return found
    for d in _EXTRA_DIRS:
        candidate = os.path.join(d, name)
        if os.access(candidate, os.X_OK):
            return candidate
    return name


ensure_path()

YTDLP = resolve("yt-dlp")
FFMPEG = resolve("ffmpeg")
FFPROBE = resolve("ffprobe")
