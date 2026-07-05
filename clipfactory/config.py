"""Configuration loading from config.yaml + .env."""
from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CLIPS_DIR = DATA / "clips"        # raw downloaded source clips
OUTPUT_DIR = DATA / "output"      # rendered vertical clips + metadata json
DB_PATH = DATA / "state.db"

load_dotenv(ROOT / ".env")


def load_config() -> dict:
    with open(ROOT / "config.yaml") as f:
        cfg = yaml.safe_load(f)
    for d in (DATA, CLIPS_DIR, OUTPUT_DIR):
        d.mkdir(parents=True, exist_ok=True)
    return cfg


def env(key: str, default: str | None = None) -> str | None:
    return os.environ.get(key, default)
