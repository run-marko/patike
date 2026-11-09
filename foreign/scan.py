"""Sken stranih prodavnica i povezivanje njihovih cena sa modelima iz srpskih prodavnica."""
from __future__ import annotations

import logging
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from analytics.engine import build_offers, comparable_key
from common.http import canonicalize_url
from database.models import CatalogModel, ForeignPrice, utcnow
from foreign.sources import FOREIGN_ADAPTERS, FOREIGN_NAMES
from matching.matcher import CatalogEntry, Matcher, observed_model
from matching.sizes import normalize_size_label
from retailers.base import ParsedListing, RetailerAdapter, RetailerResult

log = logging.getLogger("foreign")
MAX_AGE_DAYS = 7  # starija cena se ne prikazuje, jer sniženja kratko traju
RECHECK_HOURS = 20  # kraće od dana, da dnevni sken ponovo proveri sve


def resolve_model(matcher: Matcher, brand: str | None, title: str) -> tuple[str | None, list[str]] | None:
    """(model, varijante) po istim pravilima kao u Srbiji. Dečji model vraća None."""
    match = matcher.match(brand, title)
    if match.status == "EXCLUDED":
        return None
    if match.status == "MATCHED":
        return match.model_id, match.qualifiers
    observed = observed_model(brand, title)
    return (observed.id, observed.qualifiers) if observed else (None, [])


def save_foreign(session: Session, item: ParsedListing, matcher: Matcher, now: datetime) -> bool:
    gender = item.gender_attr or "unknown"
    if gender == "kids":
        return False
    resolved = resolve_model(matcher, item.brand, item.title)
    if resolved is None:
        return False
    model_id, qualifiers = resolved
    session.add(ForeignPrice(
        source=item.retailer, url=item.url, title=item.title, brand=item.brand, sku=item.sku, color=item.color,
        gender=gender, model_id=model_id, qualifiers=qualifiers, price=item.current_price, old_price=item.old_price,
        currency="EUR", availability=item.availability, observed_at=now,
        sizes=[normalize_size_label(s.label) for s in item.sizes if s.available]))
    return True


def recently_checked(session: Session, now: datetime) -> set[str]:
    """Stranice već pročitane u poslednjih RECHECK_HOURS: prekinut sken nastavlja gde je stao, bez novih zahteva."""
    limit = now - timedelta(hours=RECHECK_HOURS)
    return set(session.scalars(select(ForeignPrice.url).where(ForeignPrice.observed_at > limit)))


def sold_in_serbia(session: Session, config: dict) -> list[CatalogEntry]:
    """Modeli koji su na stanju kod nas. Samo njih vredi porediti, pa se ne otvaraju stranice ostatka kataloga."""
    entries: dict[str, CatalogEntry] = {}
    for offer in build_offers(session, config):
        if offer["availability"] == "AVAILABLE":
            entries[offer["model_id"]] = CatalogEntry(offer["model_id"], offer["brand"], offer["model"])
    return list(entries.values())


def run_foreign_scan(factory: sessionmaker[Session], config: dict, max_pages: int | None = None,
                     adapters: list[RetailerAdapter] | None = None, progress=None) -> list[tuple[RetailerResult, int]]:
    with factory() as session:
        catalog = [CatalogEntry(m.id, m.brand, m.model_name) for m in session.scalars(select(CatalogModel))]
        known = sold_in_serbia(session, config)
        done = recently_checked(session, utcnow())
    matcher, scope = Matcher(catalog), Matcher(known)
    scan = config["scan"]
    if adapters is None:
        adapters = [cls(delay_s=max(scan["default_delay_s"], cls.min_delay_s), timeout_s=scan["request_timeout_s"],
                        max_retries=scan["max_retries"], cache_hours=scan["sitemap_cache_hours"])
                    for cls in FOREIGN_ADAPTERS]

    def keep(url: str) -> bool:
        return canonicalize_url(url) not in done and scope.candidates_in_slug(url)

    def work(adapter: RetailerAdapter) -> tuple[RetailerResult, int]:
        saved = [0]

        def store(items: list[ParsedListing]) -> None:
            with factory() as session:
                saved[0] += sum(save_foreign(session, item, matcher, utcnow()) for item in items)
                session.commit()

        try:
            return adapter.run(keep, max_pages=max_pages, progress=progress, on_parsed=store), saved[0]
        except Exception as exc:  # jedan sajt ne sme da obori drugi
            log.exception("strani izvor %s nije uspeo", adapter.key)
            return RetailerResult(adapter.key, "NOT_VERIFIED", f"Neočekivana greška: {exc}"), saved[0]

    with ThreadPoolExecutor(max_workers=max(1, len(adapters))) as pool:
        return [future.result() for future in as_completed([pool.submit(work, adapter) for adapter in adapters])]


def latest_foreign(session: Session, now: datetime | None = None) -> list[ForeignPrice]:
    """Poslednje stanje svake boje iz svakog izvora, ako je novije od MAX_AGE_DAYS. Rasprodato se ne vraća."""
    limit = (now or utcnow()) - timedelta(days=MAX_AGE_DAYS)
    latest: dict[tuple, ForeignPrice] = {}
    query = select(ForeignPrice).where(ForeignPrice.observed_at > limit).order_by(ForeignPrice.observed_at, ForeignPrice.id)
    for row in session.scalars(query):
        latest[(row.source, row.url, row.sku or row.color)] = row
    return [r for r in latest.values() if r.availability == "AVAILABLE" and r.price]


def foreign_by_group(rows: list[ForeignPrice], offers: list[dict], fx: dict | None) -> dict[str, list[dict]]:
    """Grupa srpske ponude -> strane cene, od najniže. Ista šifra proizvođača je najjači dokaz; bez nje važe model,
    pol i varijanta, pa se generacije, polovi i GTX nikad ne mešaju."""
    groups = {o["group"] for o in offers}
    by_sku = {o["sku"].upper(): o["group"] for o in offers if o.get("sku")}
    rate = (fx or {}).get("eur_rate")
    out: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        sku = (row.sku or "").upper()
        group = by_sku.get(sku) or by_sku.get(sku.rsplit("-", 1)[0])
        if group is None and row.model_id:
            key = comparable_key(row.model_id, row.gender, row.qualifiers or [])
            group = key if key in groups else None
        if group is None:
            continue
        out[group].append({
            "source": row.source, "name": FOREIGN_NAMES.get(row.source, row.source), "url": row.url, "sku": row.sku,
            "color": row.color, "title": row.title, "price_eur": row.price, "old_price_eur": row.old_price,
            "price_rsd": round(row.price * rate) if rate else None, "sizes": row.sizes or [],
            "observed_at": row.observed_at,
        })
    for entries in out.values():
        entries.sort(key=lambda e: e["price_eur"])
    return dict(out)


def foreign_summary(rows: list[ForeignPrice]) -> list[dict]:
    summary: dict[str, dict] = {}
    for row in rows:
        entry = summary.setdefault(row.source, {"name": FOREIGN_NAMES.get(row.source, row.source), "prices": 0,
                                                "checked_at": row.observed_at})
        entry["prices"] += 1
        entry["checked_at"] = max(entry["checked_at"], row.observed_at)
    return list(summary.values())
