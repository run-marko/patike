"""Transparentna ocena ponude. Popust prodavca sam po sebi nikad ne može da podigne klasu."""
from __future__ import annotations

from dataclasses import dataclass, field

EXCEPTIONAL, VERY_GOOD, GOOD, NORMAL, OVERPRICED, INSUFFICIENT = (
    "EXCEPTIONAL", "VERY GOOD", "GOOD", "NORMAL", "OVERPRICED", "INSUFFICIENT DATA")
CLASS_ORDER = {EXCEPTIONAL: 0, VERY_GOOD: 1, GOOD: 2, NORMAL: 3, OVERPRICED: 4, INSUFFICIENT: 5}
CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}


def scale(value: float, low: float, high: float) -> float:
    return max(0.0, min(100.0, (value - low) / (high - low) * 100))


def pct_text(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:+.1f}%"


def rsd(value: float) -> str:
    return f"{value:,.0f}".replace(",", ".") + " RSD"


@dataclass
class DealInput:
    price: float
    old_price: float | None = None
    retailer_30d_min: float | None = None
    market_median: float | None = None
    market_retailers: int = 0            # broj DRUGIH prodavaca u medijani
    history_advantage: float | None = None
    observed_reference_seen: bool = False  # da li smo sami videli cenu blizu precrtane
    quality_score: float | None = None
    quality_source: str | None = None
    size_status: str | None = None       # CONFIRMED | UNAVAILABLE | AMBIGUOUS | UNKNOWN | None (svi brojevi)
    match_confidence: str = "high"
    stale: bool = False
    price_verified: bool = True
    identity_proven: bool = False        # isti SKU proizvođača kod bar dva prodavca


@dataclass
class DealResult:
    score: int | None
    classification: str
    confidence: str
    market_advantage: float | None
    claimed_discount: float | None
    claim_status: str
    confirmed_savings: bool
    components: dict = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def evaluate_claim(item: DealInput) -> tuple[float | None, str, bool, list[str]]:
    """Vraća (popust, status, potvrđena ušteda, objašnjenja). Status je NONE, CLAIMED, CONTRADICTED ili SUPPORTED."""
    notes: list[str] = []
    if not item.old_price or item.old_price <= item.price:
        return None, "NONE", False, notes
    discount = (item.old_price - item.price) / item.old_price
    text = f"Prodavac tvrdi popust {discount * 100:.0f}% (sa {rsd(item.old_price)})"
    contradicted = False
    if item.retailer_30d_min is not None and item.retailer_30d_min <= item.price:
        notes.append(f"{text}, ali sam navodi najnižu cenu u 30 dana od {rsd(item.retailer_30d_min)}, "
                     f"što nije više od trenutne")
        contradicted = True
    if item.market_median is not None and item.market_median <= item.price:
        notes.append(f"{text}, ali je medijana drugih prodavaca {rsd(item.market_median)}, "
                     f"što nije više od ove cene")
        contradicted = True
    if contradicted:
        return discount, "CONTRADICTED", False, notes
    if item.observed_reference_seen:
        notes.append(f"{text}; ranije smo sami zabeležili cenu blizu reference")
        return discount, "SUPPORTED", True, notes
    notes.append(f"{text}; to nije potvrđena ušteda jer referentnu cenu nismo sami zabeležili")
    return discount, "CLAIMED", False, notes


def evaluate_deal(item: DealInput, cfg: dict) -> DealResult:
    weights = cfg["weights"]
    reasons: list[str] = []
    warnings: list[str] = []
    components: dict[str, dict] = {}

    market_adv = None
    if item.market_median and item.market_retailers >= 1:
        market_adv = (item.market_median - item.price) / item.market_median
        components["market"] = {"value": scale(market_adv, *cfg["market_range"]), "weight": weights["market"]}
        reasons.append(f"Tržište: {pct_text(market_adv)} u odnosu na medijanu {rsd(item.market_median)} "
                       f"kod {item.market_retailers} drugih prodavaca")
    else:
        reasons.append("Tržište: nema drugih proverenih prodavaca za poređenje")

    if item.history_advantage is not None:
        components["history"] = {"value": scale(item.history_advantage, *cfg["history_range"]),
                                 "weight": weights["history"]}
        reasons.append(f"Istorija: {pct_text(item.history_advantage)} u odnosu na istorijsku medijanu ovog oglasa")
    else:
        reasons.append("Istorija: INSUFFICIENT DATA")

    if item.quality_score is not None:
        components["quality"] = {"value": scale(item.quality_score, *cfg["quality_range"]),
                                 "weight": weights["quality"]}
        reasons.append(f"Kvalitet: {item.quality_score:.0f}/100 ({item.quality_source})")
    else:
        reasons.append("Kvalitet: nema nezavisne ocene; nije izmišljana")
        warnings.append("Nema nezavisne ocene modela")

    if item.size_status == "CONFIRMED":
        components["size"] = {"value": 100.0, "weight": weights["size"]}
        reasons.append("Veličina: željena veličina je potvrđena")
    elif item.size_status == "UNAVAILABLE":
        components["size"] = {"value": 0.0, "weight": weights["size"]}
        reasons.append("Veličina: željena veličina NIJE dostupna")
    elif item.size_status in ("AMBIGUOUS", "UNKNOWN"):
        reasons.append("Veličina: nije moguće potvrditi")
        warnings.append("Željena EU veličina nije potvrđena")

    discount, claim_status, confirmed, claim_notes = evaluate_claim(item)
    reasons.extend(claim_notes)
    if discount is not None:
        claim_value = 0.0 if claim_status == "CONTRADICTED" else scale(discount, *cfg["claim_range"])
        components["retailer_claim"] = {"value": claim_value, "weight": weights["retailer_claim"]}

    if not components:
        score = None
    else:
        total = sum(c["weight"] for c in components.values())
        raw = sum(c["value"] * c["weight"] for c in components.values()) / total
        # Pokrivenost: manje dokaza znači niži maksimalni skor.
        coverage = total / sum(weights.values())
        score = round(raw * (0.6 + 0.4 * coverage))
        for component in components.values():
            component["value"] = round(component["value"], 1)
        reasons.append(f"Skor: {raw:.0f} sirovo x pokrivenost dokaza {coverage * 100:.0f}% = {score}")

    # Klasa zavisi od dokaza o ceni (tržište/istorija), a ne od popusta koji prodavac tvrdi.
    available = [(a, w) for a, w in ((market_adv, 0.6), (item.history_advantage, 0.4)) if a is not None]
    thresholds = cfg["thresholds"]
    if not available or score is None:
        classification = INSUFFICIENT
        reasons.append("Klasa INSUFFICIENT DATA: nema tržišnog ni istorijskog poređenja; "
                       "tvrdnja prodavca o popustu nije dokaz")
    else:
        advantage = sum(a * w for a, w in available) / sum(w for _, w in available)
        total_retailers = item.market_retailers + 1
        ex = thresholds["exceptional"]
        if advantage >= ex["advantage"] and score >= ex["score"] and total_retailers >= ex["min_retailers"] \
                and item.quality_score is not None and item.quality_score >= ex["min_quality"] \
                and item.size_status != "UNAVAILABLE":
            classification = EXCEPTIONAL
        elif advantage >= thresholds["very_good"]["advantage"] and score >= thresholds["very_good"]["score"]:
            classification = VERY_GOOD
        elif advantage >= thresholds["good"]["advantage"]:
            classification = GOOD
        elif advantage >= thresholds["normal"]["advantage"]:
            classification = NORMAL
        else:
            classification = OVERPRICED
        if classification in (EXCEPTIONAL, VERY_GOOD) and total_retailers < 3 and item.history_advantage is None:
            classification = GOOD
            warnings.append("Samo jedan drugi prodavac za poređenje: klasa je ograničena na GOOD")
        reasons.append(f"Klasa {classification}: dokazana prednost {pct_text(advantage)}, skor {score}")

    confidence = _confidence(item, market_adv, warnings)
    return DealResult(score, classification, confidence, market_adv, discount, claim_status, confirmed,
                      components, reasons, warnings)


def _confidence(item: DealInput, market_adv: float | None, warnings: list[str]) -> str:
    if item.stale:
        warnings.append("Podaci su zastareli")
    if not item.price_verified:
        warnings.append("Cena nije u potpunosti potvrđena")
    if item.stale or not item.price_verified or item.match_confidence == "low":
        return "low"
    retailers = item.market_retailers + 1 if market_adv is not None else 1
    has_history = item.history_advantage is not None
    has_quality = item.quality_score is not None
    if retailers >= 3 and has_history and has_quality:
        return "high"
    if retailers >= 3 or (retailers >= 2 and (has_history or has_quality or item.identity_proven)):
        return "medium"
    if retailers < 2 and not has_history:
        warnings.append("Mali uzorak: nema drugih prodavaca ni istorije")
    return "low"
