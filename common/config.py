"""Učitavanje config.yaml i CLI izmena profila."""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DEFAULT_DB = DATA_DIR / "shoes.db"

DEFAULTS: dict[str, Any] = {
    "country": "Serbia",
    "currency": "RSD",
    "gender": "men",
    "shoe_length_cm": 29.5,
    "preferred_eu_sizes": [45, 45.5, 46],
    "eu_size_tolerance": 0.2,
    "other_size_formats": "preserve",
    "categories": ["daily_trainer", "long_run", "tempo", "racing", "recovery"],
    "surfaces": ["road"],
    "maximum_budget_rsd": None,
    "minimum_independent_score": None,
    "include_previous_generations": True,
    "include_unknown_review_scores": True,
    "minimum_deal_confidence": "medium",
    "scan_interval": "on_demand",
    "scan": {
        "skip_other_gender_urls": False,
        "sitemap_cache_hours": 24,
        "request_timeout_s": 30,
        "max_retries": 3,
        "default_delay_s": 1.5,
        "stale_after_hours": 72,
    },
    "history": {
        "min_observations_for_average": 3,
        "min_observations_for_advantage": 5,
        "min_span_days_for_advantage": 14,
    },
    "scoring": {
        "weights": {"market": 0.40, "history": 0.25, "quality": 0.20, "size": 0.10, "retailer_claim": 0.05},
        "market_range": [-0.15, 0.25],
        "history_range": [-0.10, 0.25],
        "quality_range": [60, 95],
        "claim_range": [0.0, 0.50],
        "thresholds": {
            "exceptional": {"advantage": 0.20, "score": 80, "min_retailers": 3, "min_quality": 75},
            "very_good": {"advantage": 0.12, "score": 65},
            "good": {"advantage": 0.05},
            "normal": {"advantage": -0.05},
        },
    },
    "watchlist": [],
}


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (over or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(path: Path | str | None = None) -> dict[str, Any]:
    path = Path(path) if path else ROOT / "config.yaml"
    raw = {}
    if path.exists():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    from common.not_running import load as load_not_running  # taj modul uzima DATA_DIR odavde

    return {**_merge(DEFAULTS, raw), "not_running_models": sorted(load_not_running())}


def default_config() -> dict[str, Any]:
    return copy.deepcopy(DEFAULTS)
