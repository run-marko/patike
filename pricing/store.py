"""Upis oglasa i nepromenljivih opažanja cena."""
from __future__ import annotations

import hashlib
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from common.http import canonicalize_url
from database.models import Listing, PriceObservation, Variant
from matching.matcher import Match, Matcher, detect_gender, normalize_brand
from retailers.base import ParsedListing

# Tip cene -> status verifikacije. Javna cena je jedina koja ulazi u poređenja.
CURRENT = "CURRENT_PUBLIC"
ORIGINAL = "DISPLAYED_ORIGINAL"
REGULAR = "REGULAR"
LOYALTY = "LOYALTY"
COUPON = "COUPON"
STATED_30D = "RETAILER_STATED_30D_MIN"


def listing_id(parsed: ParsedListing) -> str:
    key = parsed.listing_key or canonicalize_url(parsed.url)
    return hashlib.sha1(f"{parsed.retailer}|{key}".encode("utf-8")).hexdigest()[:16]


def _apply_match(session: Session, row: Listing, matcher: Matcher) -> Match:
    """Upisuje uparivanje u oglas iz njegovih sirovih podataka. Važi za sken i za rematch."""
    gender, _source = detect_gender(row.title, row.url, row.description or "", row.sku, row.brand, row.gender_attr)
    match = matcher.match(row.brand, row.title, row.description or "")
    if gender == "kids":
        match = Match("EXCLUDED", reason="dečji model")
    variant_id = None
    if match.status == "MATCHED":
        brand = normalize_brand(row.brand) or "?"
        variant_id = f"{brand}:{row.sku}" if row.sku else f"listing:{row.id}"
        variant = session.get(Variant, variant_id) or Variant(id=variant_id)
        variant.model_id, variant.sku, variant.color, variant.gender = match.model_id, row.sku, row.color, gender
        session.merge(variant)
        session.flush()
    row.variant_id = variant_id  # tek pošto varijanta postoji, zbog spoljnog ključa
    row.model_id = match.model_id
    row.gender, row.qualifiers = gender, match.qualifiers
    row.match_status, row.match_reason = match.status, match.reason
    return match


def rematch_all(session: Session, matcher: Matcher) -> dict[str, int]:
    """Ponovo uparuje sačuvane oglase, npr. posle dopune kataloga. Opažanja cena ostaju netaknuta."""
    counts: dict[str, int] = {}
    for row in session.scalars(select(Listing)):
        status = _apply_match(session, row, matcher).status
        counts[status] = counts.get(status, 0) + 1
    session.commit()
    return counts


def store_listing(session: Session, parsed: ParsedListing, matcher: Matcher, scan_id: int | None,
                  now: datetime) -> tuple[Listing, Match] | None:
    lid = listing_id(parsed)
    row = session.get(Listing, lid) or Listing(id=lid, first_seen=now)
    row.retailer, row.url, row.title, row.seller = parsed.retailer, canonicalize_url(parsed.url), parsed.title, \
        parsed.seller
    row.brand, row.sku, row.color = parsed.brand, parsed.sku, parsed.color
    row.description, row.gender_attr = (parsed.description or "")[:800], parsed.gender_attr
    row.running = parsed.running
    row.image_url = parsed.image_url or row.image_url  # stranica bez slike ne briše već poznatu
    row.last_seen = now
    match = _apply_match(session, row, matcher)
    if match.status == "EXCLUDED":
        return None  # dečji modeli se ne čuvaju
    session.merge(row)
    session.flush()

    if parsed.current_price is None:
        return row, match

    sizes = [s.to_dict() for s in parsed.sizes]

    def add(price_type: str, price: float | None, status: str, condition: str | None = None) -> None:
        if price is None:
            return
        session.add(PriceObservation(
            scan_id=scan_id, listing_id=lid, model_id=match.model_id, variant_id=row.variant_id,
            retailer=parsed.retailer, seller=parsed.seller, price_type=price_type, price=price, currency="RSD",
            condition=condition, observed_at=now, url=canonicalize_url(parsed.url), availability=parsed.availability,
            sizes=sizes if price_type == CURRENT else [], delivery_rsd=parsed.delivery_rsd,
            delivery_note=parsed.delivery_note, verification_status=status))

    add(CURRENT, parsed.current_price, "VERIFIED" if parsed.price_verified else "UNVERIFIED",
        "; ".join(parsed.warnings) or None)
    add(ORIGINAL, parsed.old_price, "RETAILER_CLAIMED", "precrtana cena koju navodi prodavac")
    add(REGULAR, parsed.regular_price, "RETAILER_CLAIMED", "važeća MP cena koju navodi prodavac")
    add(LOYALTY, parsed.loyalty_price, "CONDITIONAL", parsed.loyalty_condition)
    add(COUPON, parsed.coupon_price, "CONDITIONAL", parsed.coupon_condition)
    add(STATED_30D, parsed.retailer_30d_min, "RETAILER_CLAIMED", "najniža cena u 30 dana, po navodu prodavca")
    return row, match
