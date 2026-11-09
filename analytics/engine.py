"""Sklapa aktuelne ponude iz baze: tržište, istorija, veličine i ocena po ponudi."""
from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from analytics.deal import CLASS_ORDER, CONFIDENCE_ORDER, DealInput, evaluate_deal
from analytics.history import Point, history_metrics
from database.models import CatalogModel, Listing, PriceObservation, RetailerScanStatus, ScanRun, utcnow
from matching.matcher import ObservedModel, observed_model, tokenize
from matching.sizes import matches_preferred, normalize_size_label, parse_size_value, size_status
from pricing.store import COUPON, CURRENT, LOYALTY, ORIGINAL, REGULAR, STATED_30D
from retailers.registry import RETAILER_NAMES


def resolve_models(session: Session, query: str) -> list[CatalogModel]:
    """Tačan skup tokena ima prednost: 'Novablast 5' ne vraća 'Novablast 5 GTX'."""
    wanted = tokenize(query)
    hits = []
    for model in session.scalars(select(CatalogModel)):
        full = tokenize(f"{model.brand} {model.model_name}")
        name = tokenize(model.model_name)
        if all(token in full for token in wanted):
            exact = set(wanted) in (set(full), set(name))
            hits.append((not exact, len(full), model))
    hits.sort(key=lambda h: (h[0], h[1], h[2].id))
    if hits and not hits[0][0]:
        return [hits[0][2]]
    return [h[2] for h in hits]


@dataclass
class OfferOptions:
    sizes: list[float] | None = None
    all_sizes: bool = False


def comparable_key(model_id: str, gender: str, qualifiers: list[str]) -> str:
    return f"{model_id}|{gender}|{'+'.join(sorted(qualifiers or []))}"


def build_offers(session: Session, config: dict, options: OfferOptions | None = None) -> list[dict]:
    """Jedan red po oglasu sa MATCHED uparivanjem i bar jednom javnom cenom."""
    options = options or OfferOptions()
    preferred = options.sizes if options.sizes is not None else config["preferred_eu_sizes"]
    tolerance = config.get("eu_size_tolerance", 0.2)
    now = utcnow()
    stale_after = timedelta(hours=config["scan"]["stale_after_hours"])

    models = {m.id: m for m in session.scalars(select(CatalogModel))}
    listings = list(session.scalars(select(Listing).where(Listing.match_status != "EXCLUDED")))
    identities = _resolve_identities(listings)
    observations: dict[str, list[PriceObservation]] = defaultdict(list)
    # Model dolazi iz oglasa, ne iz opažanja: posle `rematch` važi i za starija opažanja, a ona ostaju nepromenjena.
    for obs in session.scalars(select(PriceObservation).order_by(PriceObservation.observed_at, PriceObservation.id)):
        observations[obs.listing_id].append(obs)

    offers: list[dict] = []
    for listing in listings:
        rows = observations.get(listing.id) or []
        public = [o for o in rows if o.price_type == CURRENT]
        identity = identities.get(listing.id)
        if not public or identity is None:
            continue
        model_id, qualifiers, notes, observed = identity
        model = models.get(model_id) or _ObservedRow(observed)
        latest = public[-1]
        same_scan = [o for o in rows if o.observed_at == latest.observed_at]
        by_type = {o.price_type: o for o in same_scan}
        sizes = latest.sizes or []
        status = None if options.all_sizes else size_status(sizes, preferred, tolerance)
        personal_price = latest.price
        if status and status["status"] == "CONFIRMED" and status["price"] is not None:
            personal_price = status["price"]
        history = history_metrics([Point(o.observed_at, o.price) for o in public], config["history"], now)
        old = by_type[ORIGINAL].price if ORIGINAL in by_type else None
        offers.append({
            "listing_id": listing.id,
            "model_id": model_id,
            "variant_id": listing.variant_id,
            "sku": listing.sku,
            "group": comparable_key(model_id, listing.gender or "unknown", qualifiers),
            "catalog_status": "OBSERVED" if observed else "CATALOG",
            "notes": notes,
            "brand": model.brand,
            "model": model.model_name,
            "generation": model.generation,
            "gender": listing.gender,
            "qualifiers": qualifiers,
            "categories": model.categories or [],
            "category_basis": model.category_basis,
            "surface": model.surface,
            "cushioning": model.cushioning,
            "midsole_foam": model.midsole_foam,
            "plate": model.plate,
            # Cena iz kataloga važi za osnovni model; GTX i slične varijante imaju drugačiju cenu.
            "msrp_usd": None if qualifiers else model.msrp_usd,
            "review_score": model.review_score,
            "review_source": model.review_source,
            "review_url": model.review_url,
            "catalog_url": model.source_url,
            "title": listing.title,
            "image_url": listing.image_url,
            "retailer": listing.retailer,
            "retailer_name": RETAILER_NAMES.get(listing.retailer, listing.retailer),
            "seller": latest.seller,
            "url": latest.url,
            "price": latest.price,
            "personal_price": personal_price,
            "old_price": old,
            "regular_price": by_type[REGULAR].price if REGULAR in by_type else None,
            "loyalty_price": by_type[LOYALTY].price if LOYALTY in by_type else None,
            "loyalty_condition": by_type[LOYALTY].condition if LOYALTY in by_type else None,
            "coupon_price": by_type[COUPON].price if COUPON in by_type else None,
            "coupon_condition": by_type[COUPON].condition if COUPON in by_type else None,
            "retailer_30d_min": by_type[STATED_30D].price if STATED_30D in by_type else None,
            "availability": latest.availability,
            "sizes": [{**s, "norm": normalize_size_label(s.get("label"))} for s in sizes],
            "size_status": status["status"] if status else "NOT_FILTERED",
            "matched_sizes": [m.get("label") for m in status["matched"]] if status else [],
            "size_approximate": bool(status and status["approximate"]),
            "delivery_rsd": latest.delivery_rsd,
            "delivery_note": latest.delivery_note,
            "observed_at": latest.observed_at,
            "stale": now - latest.observed_at > stale_after,
            "price_verified": latest.verification_status == "VERIFIED",
            "history": history,
            "history_points": [{"at": o.observed_at.isoformat(), "price": o.price} for o in public],
            "observed_reference_seen": bool(old and any(o.price >= old * 0.98 for o in public)),
        })

    _apply_market_and_deals(offers, config, options)
    return offers


class _ObservedRow:
    """Model van kataloga: ima samo ime. Specifikacije i ocene ostaju prazne, ne izmišljaju se."""
    generation = category_basis = cushioning = midsole_foam = plate = msrp_usd = None
    review_score = review_source = review_url = source_url = None
    categories: list = []

    def __init__(self, observed: ObservedModel):
        self.brand, self.model_name, self.surface = observed.brand, observed.name, observed.surface


TRUNCATED_TITLE_LENGTH = 38  # N Sport i Intersport seku naslove na oko 40 znakova


def _is_running(listing: Listing) -> bool:
    return listing.running if listing.running is not None else "trcanje" in listing.url.lower()


def _resolve_identities(listings: list[Listing]) -> dict[str, tuple]:
    """Oglas -> (model_id, varijante, napomene, model van kataloga).

    Redosled: uparivanje sa katalogom, zatim isti SKU kao upareni oglas (isti proizvod po šifri proizvođača),
    zatim model van kataloga, ali samo ako ga prodavac vodi pod trčanjem.
    """
    out: dict[str, tuple] = {}
    by_sku: dict[str, Listing] = {}
    for listing in sorted((x for x in listings if x.match_status == "MATCHED" and x.model_id), key=lambda x: x.id):
        out[listing.id] = (listing.model_id, listing.qualifiers or [], [], None)
        if listing.sku:
            by_sku.setdefault(listing.sku, listing)

    candidates: list[tuple[Listing, ObservedModel]] = []
    for listing in listings:
        if listing.id in out:
            continue
        twin = by_sku.get(listing.sku) if listing.sku else None
        if twin is not None:
            note = f"Povezano sa katalogom preko SKU {listing.sku}; naslov prodavca: {listing.title}"
            out[listing.id] = (twin.model_id, twin.qualifiers or [], [note], None)
            continue
        observed = observed_model(listing.brand, listing.title)
        if observed:
            candidates.append((listing, observed))
    # Ananas ne vodi kategoriju trčanja: isti naziv koji druga prodavnica vodi pod trčanjem je dovoljan dokaz.
    proven = {observed.id for listing, observed in candidates if _is_running(listing)}
    pending = [(listing, observed) for listing, observed in candidates if observed.id in proven]

    # Isti SKU pod različitim naslovima (npr. skraćen naziv) je jedan model: bira se najčešći naslov.
    names_by_sku: dict[str, Counter] = defaultdict(Counter)
    for listing, observed in pending:
        if listing.sku:
            names_by_sku[listing.sku][observed.id] += 1
    registry = {observed.id: observed for _, observed in pending}
    # Naslov odsečen na granici polja ("Evo SL Wove") je isti model kao pun naziv ("Evo SL Woven").
    cut_off = {observed.id for listing, observed in pending if len(listing.title) >= TRUNCATED_TITLE_LENGTH}
    full_name: dict[str, str] = {}
    for short in cut_off:
        longer = [other for other in registry if other != short and other.startswith(short)
                  and other[len(short)] != "-"]
        if len(longer) == 1:
            full_name[short] = longer[0]
    for listing, observed in pending:
        qualifiers = observed.qualifiers  # varijanta se čita iz naslova ovog oglasa: GTX i obična verzija dele naziv modela
        if listing.sku and listing.sku in names_by_sku:
            observed = registry[sorted(names_by_sku[listing.sku].items(), key=lambda kv: (-kv[1], kv[0]))[0][0]]
        observed = registry[full_name.get(observed.id, observed.id)]
        note = "Model nije u nezavisnom katalogu: nema ocene, a naziv potiče od prodavca"
        out[listing.id] = (observed.id, qualifiers, [note], observed)
    return out


def _market_pool(offers: list[dict]) -> dict[str, dict]:
    """Najbolja dostupna, proverena i sveža cena po prodavcu u okviru uporedive grupe."""
    pool: dict[str, dict] = defaultdict(dict)
    for offer in offers:
        if offer["availability"] != "AVAILABLE" or offer["stale"] or not offer["price_verified"]:
            continue
        best = pool[offer["group"]].get(offer["retailer"])
        if best is None or offer["price"] < best:
            pool[offer["group"]][offer["retailer"]] = offer["price"]
    return pool


def _apply_market_and_deals(offers: list[dict], config: dict, options: OfferOptions) -> None:
    pool = _market_pool(offers)
    sku_retailers: dict[str, set] = defaultdict(set)
    for offer in offers:
        if offer["sku"]:
            sku_retailers[offer["sku"]].add(offer["retailer"])
    for offer in offers:
        others = [p for r, p in pool[offer["group"]].items() if r != offer["retailer"]]
        median = statistics.median(others) if others else None
        offer["market_median"] = median
        offer["market_retailers"] = len(others)
        size_counts = not options.all_sizes and config.get("filter_preferred_size", True)
        size_value = offer["size_status"] if size_counts else None
        result = evaluate_deal(DealInput(
            price=offer["personal_price"], old_price=offer["old_price"],
            retailer_30d_min=offer["retailer_30d_min"], market_median=median, market_retailers=len(others),
            history_advantage=offer["history"]["advantage"], observed_reference_seen=offer["observed_reference_seen"],
            quality_score=offer["review_score"], quality_source=offer["review_source"], size_status=size_value,
            stale=offer["stale"], price_verified=offer["price_verified"],
            identity_proven=len(sku_retailers.get(offer["sku"] or "", ())) >= 2,
        ), config["scoring"])
        offer.update(score=result.score, classification=result.classification, confidence=result.confidence,
                     market_advantage=result.market_advantage, claimed_discount=result.claimed_discount,
                     claim_status=result.claim_status, confirmed_savings=result.confirmed_savings,
                     components=result.components, reasons=result.reasons,
                     warnings=offer["notes"] + result.warnings)


@dataclass
class DealFilters:
    brand: str | None = None
    model: str | None = None
    category: str | None = None
    max_price: float | None = None
    min_discount: float | None = None
    retailer: str | None = None
    all_sizes: bool = False
    include_insufficient: bool = False


def _previous_generation(offer: dict, newest: dict[str, float]) -> bool:
    gen = offer.get("generation")
    if not gen:
        return False
    stem = offer["brand"] + "|" + " ".join(tokenize(offer["model"])[:-1])
    try:
        return float(gen) < newest.get(stem, float(gen))
    except ValueError:
        return False


def rank_deals(offers: list[dict], config: dict, filters: DealFilters | None = None) -> list[dict]:
    """Jedan red po modelu, sa najboljom ponudom i linkovima ka ostalim prodavnicama."""
    ranked = _one_row_per_model(rank_candidates(offers, config, filters))
    links = store_links(offers)
    for best in ranked:
        best["other_stores"] = [s for s in links.get(best["group"], []) if s["retailer"] != best["retailer"]]
    return ranked


def rank_candidates(offers: list[dict], config: dict, filters: DealFilters | None = None) -> list[dict]:
    """Sve ponude koje prolaze profil: pol, kategorija, budžet, veličina, pouzdanost; najbolje klase prve."""
    filters = filters or DealFilters()
    newest: dict[str, float] = {}
    for offer in offers:
        if offer.get("generation"):
            stem = offer["brand"] + "|" + " ".join(tokenize(offer["model"])[:-1])
            try:
                newest[stem] = max(newest.get(stem, 0), float(offer["generation"]))
            except ValueError:
                pass
    categories = set(config.get("categories") or [])
    surfaces = set(config.get("surfaces") or [])
    gender = config.get("gender", "men")
    minimum = CONFIDENCE_ORDER.get(config.get("minimum_deal_confidence", "medium"), 1)
    budget = config.get("maximum_budget_rsd")
    min_score = config.get("minimum_independent_score")
    not_running = set(config.get("not_running_models") or [])
    out = []
    for offer in offers:
        if offer["model_id"] in not_running:
            continue
        if offer["gender"] not in (gender, "unisex"):
            continue
        if offer["availability"] != "AVAILABLE":
            continue
        if config.get("filter_preferred_size", True) and not filters.all_sizes and offer["size_status"] != "CONFIRMED":
            continue
        if surfaces and offer["surface"] and offer["surface"] not in surfaces:
            continue
        if categories and offer["categories"] and not categories & set(offer["categories"]):
            continue
        if filters.category and filters.category not in offer["categories"]:
            continue
        if filters.brand and offer["brand"].lower() != filters.brand.lower():
            continue
        if filters.model:
            full = tokenize(f"{offer['brand']} {offer['model']}")
            if not all(t in full for t in tokenize(filters.model)):
                continue
        if filters.retailer and filters.retailer.lower().replace(" ", "") not in (
                offer["retailer"], offer["retailer_name"].lower().replace(" ", "")):
            continue
        price = offer["personal_price"]
        limit = filters.max_price if filters.max_price is not None else budget
        if limit is not None and price > limit:
            continue
        if filters.min_discount is not None and (offer["claimed_discount"] or 0) * 100 < filters.min_discount:
            continue
        if offer["review_score"] is None and not config.get("include_unknown_review_scores", True):
            continue
        if min_score is not None and offer["review_score"] is not None and offer["review_score"] < min_score:
            continue
        if not config.get("include_previous_generations", True) and _previous_generation(offer, newest):
            continue
        if CONFIDENCE_ORDER[offer["confidence"]] < minimum:
            continue
        if offer["classification"] == "INSUFFICIENT DATA" and not filters.include_insufficient:
            continue
        out.append(offer)
    out.sort(key=lambda o: (CLASS_ORDER[o["classification"]], -(o["score"] or 0), o["personal_price"],
                            -len(o["matched_sizes"])))
    return out


def store_links(offers: list[dict]) -> dict[str, list[dict]]:
    """Za svaki model po jedan link po prodavnici: njena najjeftinija dostupna ponuda, sa veličinama te ponude."""
    picks: dict[tuple[str, str], dict] = {}
    for offer in offers:
        if offer["availability"] != "AVAILABLE" or offer["stale"]:
            continue
        key = (offer["group"], offer["retailer"])
        if key not in picks or offer["price"] < picks[key]["price"]:
            picks[key] = offer
    out: dict[str, list[dict]] = defaultdict(list)
    for offer in sorted(picks.values(), key=lambda o: o["price"]):
        out[offer["group"]].append({
            "retailer": offer["retailer"], "retailer_name": offer["retailer_name"], "price": offer["price"],
            "sizes": available_sizes(offer), "url": offer["url"]})
    return dict(out)


def apply_msrp_rate(offers: list[dict], fx: dict | None) -> None:
    """Dodaje američku preporučenu cenu u dinarima. Bez kursa ili cene ostaje None; ništa se ne procenjuje."""
    for offer in offers:
        usd = offer.get("msrp_usd")
        offer["msrp_rsd"] = round(usd * fx["rate"]) if usd and fx else None


def available_sizes(offer: dict) -> list[str]:
    labels = {s["norm"] for s in offer["sizes"] if s.get("available") is True}
    return sorted(labels, key=size_key)


def _one_row_per_model(offers: list[dict]) -> list[dict]:
    """Rang prikazuje model jednom, sa najboljom ponudom. Ostale boje i prodavnice ostaju u poređenju i detaljima."""
    kept: dict[str, dict] = {}
    for offer in offers:  # lista je već sortirana, pa je prva ponuda grupe najbolja
        best = kept.get(offer["group"])
        if best is None:
            offer["other_offers"] = 0
            kept[offer["group"]] = offer
        else:
            best["other_offers"] += 1
    return list(kept.values())


def latest_retailer_status(session: Session) -> dict[str, dict]:
    """Poslednji status svakog prodavca, nezavisno od toga koji sken ga je proverio."""
    out: dict[str, dict] = {}
    rows = session.scalars(select(RetailerScanStatus).order_by(RetailerScanStatus.checked_at, RetailerScanStatus.id))
    for row in rows:
        out[row.retailer] = {"retailer": row.retailer, "name": RETAILER_NAMES.get(row.retailer, row.retailer),
                             "status": row.status, "pages_ok": row.pages_ok, "pages_failed": row.pages_failed,
                             "listings": row.listings, "message": row.message, "checked_at": row.checked_at}
    return out


def compare_model(offers: list[dict], statuses: dict[str, dict], model_id: str, config: dict,
                  preferred: list[float] | None = None) -> list[dict]:
    """Poređenje po uporedivim grupama (pol + varijanta se nikad ne mešaju)."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for offer in offers:
        if offer["model_id"] == model_id:
            groups[offer["group"]].append(offer)
    out = []
    for group, rows in sorted(groups.items()):
        rows.sort(key=lambda o: o["price"])
        purchasable = [o for o in rows if o["availability"] == "AVAILABLE" and not o["stale"]]
        best_by_retailer: dict[str, dict] = {}
        for row in purchasable:
            best_by_retailer.setdefault(row["retailer"], row)
        ranked = sorted(best_by_retailer.values(), key=lambda o: o["price"])
        cheapest = ranked[0] if ranked else None
        second = ranked[1] if len(ranked) > 1 else None
        confirmed = sorted((o for o in purchasable if o["size_status"] == "CONFIRMED"),
                           key=lambda o: o["personal_price"])
        out.append({
            "group": group,
            "gender": rows[0]["gender"],
            "qualifiers": rows[0]["qualifiers"],
            "offers": rows,
            "cheapest_observed": rows[0],
            "cheapest_available": cheapest,
            "second_cheapest": second,
            "difference_rsd": second["price"] - cheapest["price"] if second else None,
            "difference_pct": (second["price"] - cheapest["price"]) / cheapest["price"] if second else None,
            "cheapest_confirmed_size": confirmed[0] if confirmed else None,
            "retailers_verified": sum(1 for s in statuses.values() if s["status"] in ("VERIFIED", "PARTIAL")),
            "retailers_carrying": len({o["retailer"] for o in rows}),
            "retailers_with_size": len({o["retailer"] for o in confirmed}),
            "not_verified": [s["name"] for s in statuses.values() if s["status"] == "NOT_VERIFIED"],
        })
    return out


def last_scan_time(session: Session):
    row = session.scalars(select(ScanRun).where(ScanRun.finished_at.is_not(None))
                          .order_by(ScanRun.finished_at.desc())).first()
    return row.finished_at if row else None


def size_key(label: str) -> float:
    value = parse_size_value(label)
    return value if value is not None else 999.0


__all__ = ["build_offers", "rank_deals", "compare_model", "resolve_models", "DealFilters", "OfferOptions",
           "latest_retailer_status", "last_scan_time", "matches_preferred"]
