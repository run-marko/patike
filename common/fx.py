"""Kurs dolara i evra za poređenje sa inostranstvom. Besplatan izvor bez ključa; bez kursa se prikazuje samo strana valuta."""
from __future__ import annotations

import json
import logging
import time

import httpx

from common.config import DATA_DIR

log = logging.getLogger("fx")
SOURCE_URL = "https://open.er-api.com/v6/latest/USD"
ATTRIBUTION_URL = "https://www.exchangerate-api.com"
CACHE = DATA_DIR / "fx_usd_rsd.json"
MAX_AGE_S = 24 * 3600


def load_rate() -> dict | None:
    """Poslednji sačuvani kurs: {"rate", "date"}. Ne ide na mrežu."""
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def refresh_rate() -> dict | None:
    """Osvežava kurs jednom dnevno. Kad izvor ne radi, ostaje poslednji kurs sa svojim datumom."""
    if CACHE.exists() and time.time() - CACHE.stat().st_mtime < MAX_AGE_S and (load_rate() or {}).get("eur_rate"):
        return load_rate()
    try:
        response = httpx.get(SOURCE_URL, timeout=15, headers={"User-Agent": "RunningShoeDealFinderSerbia/1.0"})
        response.raise_for_status()
        body = response.json()
        rates = body["rates"]
        fresh = {"rate": float(rates["RSD"]), "eur_rate": float(rates["RSD"]) / float(rates["EUR"]),
                 "date": body.get("time_last_update_utc")}
    except (httpx.HTTPError, ValueError, KeyError, TypeError, ZeroDivisionError) as exc:
        log.warning("kurs nije osvežen: %s", exc)
        return load_rate()
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(fresh), encoding="utf-8")
    return fresh
