"""Veličine. Nikad se ne radi sopstvena konverzija US -> EU: brojevi važe samo ako ih prodavac navede."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass

EU_MIN, EU_MAX = 34.0, 52.0


@dataclass
class SizeInfo:
    label: str                       # originalni tekst prodavca
    eu: float | None = None          # samo ako je EU eksplicitan ili nedvosmislen
    system: str = "UNKNOWN"          # EU | US | UK | CM | UNKNOWN
    us: str | None = None
    uk: str | None = None
    cm: str | None = None
    available: bool | None = None    # True potvrđeno, False potvrđeno da nema, None nepoznato
    price: float | None = None
    old_price: float | None = None
    stock_note: str | None = None
    url: str | None = None

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}


def normalize_size_label(label: str | None) -> str:
    """Isti broj u jednom zapisu: '451/3' i '45-1/3' -> '45 1/3', '45,5' -> '45.5'. Prodavčeva oznaka se čuva posebno."""
    text = re.sub(r"^\W+", "", str(label or "").strip())
    fraction = re.fullmatch(r"(\d{2})[\s-]*(\d)/(\d)", text)
    if fraction:
        return f"{fraction[1]} {fraction[2]}/{fraction[3]}"
    return re.sub(r"^(\d+),(\d+)$", r"\1.\2", text)


def parse_size_value(text: str | None) -> float | None:
    """'45', '45.5', '45,5', '45 1/3', '46 2/3' -> broj. Drugo -> None."""
    if not text:
        return None
    value = normalize_size_label(text).replace(",", ".")
    match = re.fullmatch(r"(\d{2})(?:\s+(\d)/(\d))?", value)
    if match:
        whole = float(match.group(1))
        if match.group(2):
            whole += int(match.group(2)) / int(match.group(3))
        return round(whole, 3)
    match = re.fullmatch(r"\d{1,2}(?:\.\d+)?", value)
    return float(value) if match else None


def classify_label(label: str) -> tuple[str, float | None]:
    """Klasifikuje samostalnu oznaku veličine bez dodatne tabele.

    Vrednost u EU opsegu je EU. Sve ispod (npr. '11' ili 'US 11') NIJE EU i ostaje dvosmisleno:
    konverzija zavisi od brenda, pa se takva veličina ne računa kao potvrđena EU veličina.
    """
    text = (label or "").strip()
    low = text.lower()
    if re.match(r"^(us|uk|cm)\b", low):
        return low[:2].upper(), None
    if re.match(r"^eu[r]?\b", low):
        value = parse_size_value(re.sub(r"^eu[r]?\s*", "", low))
        return ("EU", value) if value is not None and EU_MIN <= value <= EU_MAX else ("UNKNOWN", None)
    value = parse_size_value(text)
    if value is not None and EU_MIN <= value <= EU_MAX:
        return "EU", value
    return "UNKNOWN", None


def matches_preferred(eu: float | None, preferred: list[float], tolerance: float) -> tuple[bool, bool]:
    """Vraća (poklapa se, približno). 45 1/3 je približno 45.5 uz toleranciju 0.2."""
    if eu is None:
        return False, False
    for wanted in preferred:
        if abs(eu - float(wanted)) < 1e-6:
            return True, False
    for wanted in preferred:
        if abs(eu - float(wanted)) <= tolerance + 1e-6:
            return True, True
    return False, False


def size_status(sizes: list[dict], preferred: list[float], tolerance: float) -> dict:
    """Status željenih veličina za jednu ponudu.

    CONFIRMED: željena EU veličina je izričito dostupna.
    UNAVAILABLE: veličine su poznate, a nijedna željena nije dostupna.
    AMBIGUOUS: postoje veličine u US/nepoznatom sistemu, pa se EU ne može potvrditi.
    UNKNOWN: prodavac ne navodi veličine.
    """
    if not sizes:
        return {"status": "UNKNOWN", "matched": [], "price": None, "approximate": False}
    matched = []
    for size in sizes:
        hit, approx = matches_preferred(size.get("eu"), preferred, tolerance)
        if hit and size.get("available") is True:
            matched.append({**size, "approximate": approx})
    if matched:
        priced = [m["price"] for m in matched if m.get("price") is not None]
        return {"status": "CONFIRMED", "matched": matched, "price": min(priced) if priced else None,
                "approximate": all(m["approximate"] for m in matched)}
    has_eu = any(s.get("eu") is not None for s in sizes)
    if not has_eu:
        return {"status": "AMBIGUOUS", "matched": [], "price": None, "approximate": False}
    return {"status": "UNAVAILABLE", "matched": [], "price": None, "approximate": False}
