"""Sken: katalog -> sitemap svake prodavnice -> stranice proizvoda -> uparivanje -> opažanja -> alarmi."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from analytics.alerts import evaluate_alerts
from analytics.engine import build_offers
from database.models import CatalogModel, Listing, PriceObservation, RetailerScanStatus, ScanRun, utcnow
from matching.matcher import CatalogEntry, Matcher, observed_model, tokenize, url_is_other_gender
from pricing.store import store_listing
from common.http import BlockedError, FetchError, RobotsDisallowed
from retailers.base import RetailerAdapter, RetailerResult, page_image
from retailers.registry import build_adapters

log = logging.getLogger("scanner")


@dataclass
class ScanFilters:
    brand: str | None = None
    model: str | None = None
    category: str | None = None
    retailers: list[str] | None = None
    max_pages_per_retailer: int | None = None


@dataclass
class ScanSummary:
    scan_id: int
    results: list[RetailerResult] = field(default_factory=list)
    saved: int = 0
    matched: int = 0
    ambiguous: int = 0
    no_match: int = 0
    models_in_scope: int = 0
    new_alerts: list = field(default_factory=list)


def select_models(session: Session, config: dict, filters: ScanFilters) -> list[CatalogModel]:
    models = list(session.scalars(select(CatalogModel)))
    surfaces = set(config.get("surfaces") or [])
    out = []
    for model in models:
        if surfaces and model.surface and model.surface not in surfaces:
            continue
        if filters.brand and model.brand.lower() != filters.brand.lower():
            continue
        if filters.category and filters.category not in (model.categories or []):
            continue
        if filters.model:
            wanted = tokenize(filters.model)
            have = tokenize(f"{model.brand} {model.model_name}")
            if not all(token in have for token in wanted):
                continue
        out.append(model)
    return out


def recently_out_of_stock(session: Session, days: int) -> set[str]:
    """URL-ovi koji su pri poslednjoj proveri bili rasprodati i još ne treba da se otvaraju. Štedi zahteve ka sajtu."""
    if not days:
        return set()
    latest: dict[str, PriceObservation] = {}
    query = select(PriceObservation).where(PriceObservation.price_type == "CURRENT_PUBLIC")
    for obs in session.scalars(query.order_by(PriceObservation.observed_at, PriceObservation.id)):
        latest[obs.listing_id] = obs
    limit = utcnow() - timedelta(days=days)
    return {o.url for o in latest.values() if o.availability == "OUT_OF_STOCK" and o.observed_at > limit}


def observed_running_entries(session: Session) -> list[CatalogEntry]:
    entries: dict[str, CatalogEntry] = {}
    for listing in session.scalars(select(Listing).where(Listing.running.is_(True))):
        observed = observed_model(listing.brand, listing.title)
        if observed and listing.brand:
            entries[observed.id] = CatalogEntry(observed.id, observed.brand, observed.name)
    return list(entries.values())


def _entries(models: list[CatalogModel]) -> list[CatalogEntry]:
    return [CatalogEntry(m.id, m.brand, m.model_name) for m in models]


def fill_missing_images(factory: sessionmaker[Session], config: dict, adapters: list[RetailerAdapter] | None = None,
                        progress=None) -> dict[str, dict[str, int]]:
    """Dopunjuje slike ponuda na stanju, bez čekanja punog skena. Ne upisuje cene, pa istorija ostaje netaknuta."""
    with factory() as session:
        missing: dict[str, dict[str, str]] = {}
        for offer in build_offers(session, config):
            if offer["availability"] == "AVAILABLE" and not offer["image_url"]:
                missing.setdefault(offer["retailer"], {})[offer["listing_id"]] = offer["url"]
    adapters = adapters if adapters is not None else build_adapters(config, list(missing))

    def work(adapter: RetailerAdapter) -> tuple[str, dict[str, str], int]:
        found: dict[str, str] = {}
        failed = blocks = 0
        todo = missing.get(adapter.key, {})
        for index, (listing_id, url) in enumerate(todo.items(), 1):
            if progress and (index % 25 == 0 or index == len(todo)):
                progress(adapter.key, index, len(todo))
            try:
                resp = adapter.client.get(url)
            except BlockedError as exc:
                log.warning("%s: %s", adapter.key, exc)
                failed += 1
                blocks += 1
                if blocks >= 3:  # sajt nas je blokirao: staje se, blokada se ne zaobilazi
                    failed += len(todo) - index
                    break
                continue
            except (FetchError, RobotsDisallowed) as exc:
                log.info("%s: %s", adapter.key, exc)
                failed += 1
                continue
            blocks = 0
            image = page_image(resp.text, str(resp.url))
            if image:
                found[listing_id] = image
            else:
                failed += 1
        return adapter.key, found, failed

    usable = [a for a in adapters if a.supported and missing.get(a.key)]
    counts: dict[str, dict[str, int]] = {}
    with ThreadPoolExecutor(max_workers=max(1, len(usable))) as pool:
        for future in as_completed([pool.submit(work, adapter) for adapter in usable]):
            retailer, found, failed = future.result()
            with factory() as session:
                for listing_id, image in found.items():
                    session.get(Listing, listing_id).image_url = image
                session.commit()
            counts[retailer] = {"found": len(found), "failed": failed}
    return counts


def run_scan(factory: sessionmaker[Session], config: dict, filters: ScanFilters | None = None,
             adapters: list[RetailerAdapter] | None = None, progress=None) -> ScanSummary:
    filters = filters or ScanFilters()
    with factory() as session:
        all_models = list(session.scalars(select(CatalogModel)))
        scope = select_models(session, config, filters)
        run = ScanRun(started_at=utcnow(), filters={k: v for k, v in vars(filters).items() if v})
        session.add(run)
        session.commit()
        scan_id = run.id

    summary = ScanSummary(scan_id=scan_id, models_in_scope=len(scope))
    # Uparivanje uvek gleda ceo katalog, da "Vomero Plus" ne završi kao "Vomero". URL filter gleda samo opseg.
    full_matcher = Matcher(_entries(all_models))
    filtered_scan = filters.brand or filters.model or filters.category
    # Pun sken: uz katalog, URL filter prepoznaje i modele koje je neka prodavnica ranije vodila pod trčanjem.
    with factory() as session:
        known = _entries(scope) + ([] if filtered_scan else observed_running_entries(session))
    scope_matcher = Matcher(known)
    profile_gender = config.get("gender", "men")
    skip_gender = config["scan"].get("skip_other_gender_urls", True)

    with factory() as session:
        resting = recently_out_of_stock(session, config["scan"].get("recheck_out_of_stock_days", 0))

    def allowed(url: str) -> bool:
        if url in resting:
            return False
        return not (skip_gender and url_is_other_gender(url, profile_gender))

    def keep(url: str) -> bool:
        return allowed(url) and scope_matcher.candidates_in_slug(url)

    # Pun sken gleda sve što prodavac vodi pod trčanjem. Katalog ne sme da odlučuje šta postoji.
    broad_keep = None if filtered_scan else allowed

    adapters = adapters if adapters is not None else build_adapters(config, filters.retailers)

    def work(adapter: RetailerAdapter) -> RetailerResult:
        try:
            return adapter.run(keep, max_pages=filters.max_pages_per_retailer, progress=progress,
                               broad_keep=broad_keep)
        except Exception as exc:  # jedan sajt nikad ne sme da obori ostale
            log.exception("adapter %s nije uspeo", adapter.key)
            return RetailerResult(adapter.key, "NOT_VERIFIED", f"Neočekivana greška: {exc}")

    with ThreadPoolExecutor(max_workers=max(1, len(adapters))) as pool:
        futures = [pool.submit(work, adapter) for adapter in adapters]
        results = [future.result() for future in as_completed(futures)]

    now = utcnow()
    with factory() as session:
        for result in results:
            summary.results.append(result)
            stored = 0
            for parsed in result.listings:
                saved = store_listing(session, parsed, full_matcher, scan_id, now)
                if saved is None:
                    continue
                stored += 1
                status = saved[1].status
                summary.matched += status == "MATCHED"
                summary.ambiguous += status == "AMBIGUOUS"
                summary.no_match += status == "NO_MATCH"
            summary.saved += stored
            session.add(RetailerScanStatus(
                scan_id=scan_id, retailer=result.retailer, status=result.status, pages_ok=result.pages_ok,
                pages_failed=result.pages_failed, listings=stored, message=result.message, checked_at=now))
        row = session.get(ScanRun, scan_id)
        row.finished_at = utcnow()
        session.commit()
        offers = build_offers(session, config)
        summary.new_alerts = evaluate_alerts(session, config, offers)
    return summary
