#!/usr/bin/env python3
"""Entry point: discover + render clips into the review queue.

Usage:
    python run.py                # all streamers in config.yaml
    python run.py kaicenat ksi   # only these
"""
import sys

from clipfactory.pipeline import run

if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    run(limit_streamers=args or None)
