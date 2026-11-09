"""Istorijske metrike. Računaju se samo iz direktno posmatranih javnih cena i samo kad ima dovoljno podataka."""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta

INSUFFICIENT = "INSUFFICIENT DATA"
CHANGE_DAYS = 3  # koliko dugo se promena cene ističe na dashboardu; isti broj piše u napomeni kartice Promene cena


@dataclass
class Point:
    at: datetime
    price: float


def pct(new: float, old: float) -> float | None:
    return None if not old else (new - old) / old


def last_change(points: list[Point], now: datetime) -> dict | None:
    """Poslednja promena cene, ako je novija od CHANGE_DAYS. Nova najniža važi u odnosu na sve ranije cene oglasa."""
    for i in range(len(points) - 1, 0, -1):
        new, old = points[i].price, points[i - 1].price
        if new == old:
            continue
        if now - points[i].at > timedelta(days=CHANGE_DAYS):
            return None
        kind = "up" if new > old else "new_low" if new < min(p.price for p in points[:i]) else "down"
        return {"kind": kind, "from": old, "to": new, "pct": pct(new, old), "at": points[i].at.isoformat(),
                "tracked_since": points[0].at.isoformat()}
    return None


def history_metrics(points: list[Point], cfg: dict, now: datetime | None = None) -> dict:
    """`points` je hronološki; poslednja tačka je trenutna cena."""
    points = sorted(points, key=lambda p: p.at)
    n = len(points)
    out: dict = {
        "observations": n,
        "status": INSUFFICIENT if n < 2 else "OK",
        "previous": None, "change_abs": None, "change_pct": None,
        "historical_min": None, "historical_min_date": None, "vs_min_pct": None,
        "avg_7d": None, "avg_30d": None, "avg_90d": None,
        "vs_avg_7d_pct": None, "vs_avg_30d_pct": None, "vs_avg_90d_pct": None,
        "median": None, "advantage": None, "span_days": 0, "last_change": None,
    }
    if n == 0:
        return out
    current = points[-1]
    now = now or current.at
    out["span_days"] = (current.at - points[0].at).days
    out["last_change"] = last_change(points, now)
    lowest = min(points, key=lambda p: p.price)
    if n >= 2:
        previous = points[-2].price
        out["previous"] = previous
        out["change_abs"] = current.price - previous
        out["change_pct"] = pct(current.price, previous)
        out["historical_min"] = lowest.price
        out["historical_min_date"] = lowest.at.date().isoformat()
        out["vs_min_pct"] = pct(current.price, lowest.price)
    need = cfg.get("min_observations_for_average", 3)
    for days in (7, 30, 90):
        window = [p.price for p in points if p.at >= now - timedelta(days=days)]
        if len(window) >= need:
            avg = sum(window) / len(window)
            out[f"avg_{days}d"] = round(avg, 2)
            out[f"vs_avg_{days}d_pct"] = pct(current.price, avg)
    if n >= cfg.get("min_observations_for_advantage", 5) and \
            out["span_days"] >= cfg.get("min_span_days_for_advantage", 14):
        earlier = [p.price for p in points[:-1]]
        median = statistics.median(earlier)
        out["median"] = median
        out["advantage"] = (median - current.price) / median if median else None
    return out
