"""Izvoz podataka: JSON za dashboard, CSV i JSON izveštaji."""
from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from analytics.alerts import list_alerts
from analytics.engine import DealFilters, apply_msrp_rate, build_offers, compare_model, last_scan_time, \
    latest_retailer_status, rank_candidates, rank_deals
from common import not_running
from common.config import ROOT
from common.fx import load_rate
from database.models import CatalogModel, Listing, PriceObservation
from foreign.scan import foreign_by_group, foreign_summary, latest_foreign
from retailers.registry import ADAPTERS

OUTPUT_DIR = ROOT / "reports" / "output"
CSV_FIELDS = ["classification", "score", "confidence", "brand", "model", "gender", "qualifiers", "categories",
              "retailer_name", "seller", "sku", "personal_price", "price", "old_price", "discount_pct",
              "claim_status", "market_median", "market_retailers", "market_advantage", "availability", "size_status",
              "matched_sizes", "review_score", "review_source", "observed_at", "url", "other_stores", "msrp_usd", "msrp_rsd", "vs_msrp_pct",
              "eu_price_eur", "eu_price_rsd", "eu_source", "vs_eu_pct"]


def _default(value):
    return value.isoformat() if isinstance(value, datetime) else str(value)


def offers_to_csv(offers: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for offer in offers:
            row = dict(offer)
            discount = offer.get("claimed_discount")
            row["discount_pct"] = "" if discount is None else round(discount * 100)
            row["msrp_rsd"] = offer.get("msrp_rsd") or ""
            row["vs_msrp_pct"] = round((offer["price"] / offer["msrp_rsd"] - 1) * 100) if offer.get("msrp_rsd") else ""
            row["vs_eu_pct"] = round((offer["price"] / offer["eu_price_rsd"] - 1) * 100) if offer.get("eu_price_rsd") else ""
            row["other_stores"] = " | ".join(f"{s['retailer_name']} {s['price']:.0f} {s['url']}"
                                             for s in offer.get("other_stores") or [])
            for key in ("qualifiers", "categories", "matched_sizes"):
                row[key] = "; ".join(str(v) for v in offer.get(key) or [])
            writer.writerow(row)


def dashboard_data(session: Session, config: dict) -> dict:
    offers = build_offers(session, config)
    fx = load_rate()
    apply_msrp_rate(offers, fx)
    foreign_rows = latest_foreign(session)
    foreign = foreign_by_group(foreign_rows, offers, fx)
    for offer in offers:
        best = (foreign.get(offer["group"]) or [None])[0]
        if best:
            offer["eu_price_eur"], offer["eu_price_rsd"], offer["eu_source"] = best["price_eur"], best["price_rsd"], best["name"]
    statuses = latest_retailer_status(session)
    # Dashboard sam svodi na jedan red po modelu, posle filtera: izabrana veličina može biti samo u drugoj prodavnici.
    # Oznake "nije za trčanje" primenjuje dashboard, da model posle dugmeta Vrati odmah bude u listama.
    # Rang se računa za oba pola, a dashboard prikazuje izabrani.
    ranked_ids = {o["listing_id"] for gender in ("men", "women")
                  for o in rank_candidates(offers, {**config, "gender": gender, "not_running_models": []})}
    for offer in offers:
        offer["in_personal_ranking"] = offer["listing_id"] in ranked_ids
    comparisons = []
    for model_id in sorted({o["model_id"] for o in offers}):
        for row in compare_model(offers, statuses, model_id, config):
            comparisons.append({
                "group": row["group"], "model_id": model_id, "gender": row["gender"], "qualifiers": row["qualifiers"],
                "cheapest_observed": row["cheapest_observed"]["listing_id"],
                "cheapest_available": row["cheapest_available"]["listing_id"] if row["cheapest_available"] else None,
                "second_cheapest": row["second_cheapest"]["listing_id"] if row["second_cheapest"] else None,
                "difference_rsd": row["difference_rsd"], "difference_pct": row["difference_pct"],
                "cheapest_confirmed_size": row["cheapest_confirmed_size"]["listing_id"]
                if row["cheapest_confirmed_size"] else None,
                "retailers_verified": row["retailers_verified"], "retailers_carrying": row["retailers_carrying"],
                "retailers_with_size": row["retailers_with_size"], "not_verified": row["not_verified"],
            })
    known = set(statuses)
    retailer_rows = list(statuses.values())
    for adapter in ADAPTERS:
        if adapter.key not in known:
            retailer_rows.append({"retailer": adapter.key, "name": adapter.name, "status": "NOT_CHECKED",
                                  "pages_ok": 0, "pages_failed": 0, "listings": 0,
                                  "message": adapter.note or "Još nije skenirano", "checked_at": None})
    review = [{"retailer": row.retailer, "title": row.title, "url": row.url, "reason": row.match_reason}
              for row in session.scalars(select(Listing).where(Listing.match_status == "AMBIGUOUS"))]
    alerts = [{"message": a.message, "created_at": a.created_at, "rule": a.rule_type}
              for a in list_alerts(session, mark_seen=False)]
    return {
        "generated_at": last_scan_time(session),
        "profile": {
            "gender": config["gender"], "sizes": config["preferred_eu_sizes"],
            "shoe_length_cm": config["shoe_length_cm"], "categories": config["categories"],
            "minimum_deal_confidence": config["minimum_deal_confidence"],
        },
        "counts": {
            "catalog_models": session.scalar(select(func.count()).select_from(CatalogModel)),
            "models_seen": len({o["model_id"] for o in offers}),
            "observations": session.scalar(select(func.count()).select_from(PriceObservation)),
        },
        "offers": offers,
        "not_running": not_running.load(),
        "fx": fx,
        "foreign": foreign,
        "foreign_sources": foreign_summary(foreign_rows),
        "comparisons": comparisons,
        "retailers": retailer_rows,
        "review": review,
        "alerts": alerts,
    }


def write_reports(session: Session, config: dict) -> dict[str, Path]:
    """Piše JSON i CSV u reports/output i generiše HTML dashboard."""
    from dashboard.build import build_artifact_html, build_html, build_page_html

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    data = dashboard_data(session, config)
    from dashboard.thumbs import build_sheets
    # sličice za artifakt, samo iz keša (preuzima ih "main.py thumbs")
    data["thumbs"] = build_sheets([o.get("image_url") for o in data["offers"] if o.get("availability") == "AVAILABLE"],
                                  OUTPUT_DIR / "thumbs")
    payload = json.dumps(data, ensure_ascii=False, default=_default)
    paths = {
        "json": OUTPUT_DIR / "offers.json",
        "csv_all": OUTPUT_DIR / "offers_all.csv",
        "csv_deals": OUTPUT_DIR / "deals.csv",
        "html": OUTPUT_DIR / "dashboard.html",
    }
    paths["json"].write_text(payload, encoding="utf-8")
    offers_to_csv(data["offers"], paths["csv_all"])
    offers_to_csv(rank_deals(data["offers"], config, DealFilters()), paths["csv_deals"])
    paths["html"].write_text(build_html(payload), encoding="utf-8")
    paths["artifact"] = OUTPUT_DIR / "dashboard_artifact.html"
    paths["artifact"].write_text(build_artifact_html(payload), encoding="utf-8")
    # stranica za telefon (GitHub Pages); sličice su u reports/output/thumbs
    paths["page"] = OUTPUT_DIR / "page.html"
    paths["page"].write_text(build_page_html(payload), encoding="utf-8")
    return paths
