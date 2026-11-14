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
    import os
    from cloud.direct_collector import HOSTS, CollectedAdapter, ResponseSource
    identity = os.environ.get("DIRECT_COLLECTOR_RUN_ID")
    hosted = bool(os.environ.get("HOSTED_COLLECTOR_TOKEN"))
    source = ResponseSource(identity) if identity and not hosted else None
    for cls in ADAPTERS:
        if wanted and cls.key not in wanted and cls.name.lower().replace(" ", "") not in wanted:
            continue
        kwargs = {}
        if hosted and cls.key in HOSTS:
            from cloud.hosted_collector import hosted_client
            kwargs["client"] = hosted_client(cls.base_url, scan)
        adapter = cls(delay_s=scan["default_delay_s"], timeout_s=scan["request_timeout_s"],
                      max_retries=scan["max_retries"], cache_hours=scan["sitemap_cache_hours"], **kwargs)
        out.append(CollectedAdapter(adapter, source) if source and cls.key in HOSTS else adapter)
    return out
