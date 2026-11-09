from __future__ import annotations

from retailers.ananas import AnanasAdapter
from retailers.base import RetailerAdapter
from retailers.buzz import BuzzAdapter
from retailers.djaksport import DjakSportAdapter
from retailers.etsport import ETSportAdapter
from retailers.extrasports import ExtraSportsAdapter
from retailers.intersport import IntersportAdapter
from retailers.nsport import NSportAdapter
from retailers.planetasport import PlanetaSportAdapter
from retailers.runnmore import RunNMoreAdapter
from retailers.sportvision import SportVisionAdapter

ADAPTERS: list[type[RetailerAdapter]] = [
    SportVisionAdapter, RunNMoreAdapter, ETSportAdapter, ExtraSportsAdapter, BuzzAdapter,
    IntersportAdapter, NSportAdapter, AnanasAdapter, PlanetaSportAdapter, DjakSportAdapter,
]
RETAILER_NAMES = {a.key: a.name for a in ADAPTERS}


def build_adapters(config: dict, only: list[str] | None = None) -> list[RetailerAdapter]:
    scan = config["scan"]
    wanted = {o.lower().replace(" ", "") for o in only} if only else None
    out = []
    for cls in ADAPTERS:
        if wanted and cls.key not in wanted and cls.name.lower().replace(" ", "") not in wanted:
            continue
        out.append(cls(delay_s=scan["default_delay_s"], timeout_s=scan["request_timeout_s"],
                       max_retries=scan["max_retries"], cache_hours=scan["sitemap_cache_hours"]))
    return out
