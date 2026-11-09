"""Strane prodavnice čiji robots.txt i uslovi ne zabranjuju čitanje (provereno 2026-11-06). Cene se čitaju iz JSON-LD-a."""
from __future__ import annotations

import html as html_lib
import json
import re
import time

from bs4 import BeautifulSoup

from matching.sizes import SizeInfo, classify_label, normalize_size_label
from retailers.base import ParsedListing, RetailerAdapter


def _product_group(soup: BeautifulSoup) -> dict | None:
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "", strict=False)
        except ValueError:
            continue
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict) and item.get("@type") == "ProductGroup":
                return item
    return None


def _price(offer: dict) -> float | None:
    try:
        value = float(offer.get("price"))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _in_stock(offer: dict) -> bool:
    return str(offer.get("availability", "")).endswith("/InStock")


def _size(label: str, price: float | None) -> SizeInfo:
    clean = normalize_size_label(label)
    system, eu = classify_label(clean)
    return SizeInfo(label=clean, eu=eu, system=system, available=True, price=price)


def _brand(group: dict) -> str | None:
    brand = group.get("brand")
    return brand.get("name") if isinstance(brand, dict) else brand


def _top4_size(offer_url: str) -> str | None:
    """'...?size=39-5-eu-5-5-uk' -> '39.5', '...size=46-2-3-eu' -> '46 2/3'."""
    match = re.search(r"[?&]size=([\d-]+?)-eu", html_lib.unescape(offer_url or ""))
    if not match:
        return None
    parts = match.group(1).split("-")
    if len(parts) == 1:
        return parts[0]
    return f"{parts[0]}.{parts[1]}" if len(parts) == 2 else f"{parts[0]} {parts[1]}/{parts[2]}"


def parse_top4running(html: str, url: str, source: str = "top4running") -> list[ParsedListing]:
    soup = BeautifulSoup(html, "lxml")
    group = _product_group(soup)
    if group is None or not re.search(r"schuh", group.get("name", ""), re.I):
        return []
    offers = [v.get("offers") or {} for v in group.get("hasVariant") or []]
    live = [o for o in offers if _in_stock(o) and _price(o) is not None]
    current = min((_price(o) for o in live), default=None)
    listed = max((p for p in map(_price, offers) if p), default=None)
    gender = re.search(r"\b(Herren|Damen|Unisex|Kinder)\s*,", soup.get_text(" ", strip=True))
    meta = soup.find("meta", attrs={"property": "og:price:amount"})
    shown = _price({"price": meta.get("content")}) if meta else None
    warnings = []
    # Cena u JSON-LD-u mora da se slaže sa cenom koju stranica prikazuje; inače se ne upisuje.
    if current is not None and shown is not None and abs(current - shown) >= 1:
        warnings.append(f"JSON-LD {current} i prikazana cena {shown} se ne slažu")
        current = None
    listing = ParsedListing(
        retailer=source, url=url, current_price=current, brand=_brand(group),
        title=re.sub(r"^\S*schuhe\s+", "", group["name"], flags=re.I).strip(),
        sku=(group.get("productID") or "").upper() or None, color=group.get("color"),
        gender_attr={"Herren": "men", "Damen": "women", "Unisex": "unisex", "Kinder": "kids"}.get(
            gender.group(1) if gender else "", "unknown"),
        old_price=listed if listed and current and listed > current else None,
        availability="AVAILABLE" if current is not None else "OUT_OF_STOCK", warnings=warnings,
        sizes=[_size(label, _price(o)) for o in live if (label := _top4_size(o.get("url")))],
    )
    return [listing]


def _bergfreunde_gender(group: dict) -> str:
    audience = group.get("audience")
    value = str(audience.get("suggestedGender", "")).lower() if isinstance(audience, dict) else ""
    return "women" if "female" in value else "men" if "male" in value else "unisex" if "unisex" in value else "unknown"


def parse_bergfreunde(html: str, url: str, source: str = "bergfreunde") -> list[ParsedListing]:
    """Jedna stranica ima više boja: svaka boja (šifra proizvođača) je zaseban zapis."""
    group = _product_group(BeautifulSoup(html, "lxml"))
    if group is None:
        return []
    parts = [p.strip() for p in group.get("name", "").split(" - ")]
    if len(parts) < 3 or "running" not in parts[-1].lower():
        return []
    brand = _brand(group)
    colors: dict[str, list[dict]] = {}
    for variant in group.get("hasVariant") or []:
        # Hoka dodaje veličinu na kraj šifre: 1162030-SGNN-10D -> 1162030-SGNN.
        sku = re.sub(r"-\d{1,2}(?:\.\d)?[A-Z]$", "", variant.get("mpn") or "") or None
        colors.setdefault(sku or variant.get("color") or "", []).append(variant)
    out = []
    for key, variants in colors.items():
        live = [v for v in variants if _in_stock(v.get("offers") or {}) and _price(v.get("offers") or {}) is not None]
        current = min((_price(v["offers"]) for v in live), default=None)
        out.append(ParsedListing(
            retailer=source, url=url, title=f"{brand} {parts[1]}", current_price=current, brand=brand,
            sku=key if variants[0].get("mpn") else None, color=variants[0].get("color"),
            gender_attr=_bergfreunde_gender(group),
            availability="AVAILABLE" if current is not None else "OUT_OF_STOCK",
            sizes=[_size(v["size"]["name"], _price(v["offers"])) for v in live if isinstance(v.get("size"), dict)],
        ))
    return out


class Top4RunningAdapter(RetailerAdapter):
    key = "top4running"
    name = "Top4Running.de"
    base_url = "https://top4running.de"
    platform = "Nemačka prodavnica"
    sitemap_urls = ["https://top4running.de/export/sitemap_index.xml"]
    min_delay_s = 3.0  # na 1,5 s sajt je posle oko 50 minuta vratio HTTP 429

    def is_shoe_url(self, url: str) -> bool:
        return "/p/" in url

    def all_urls(self) -> list[str]:
        cache = self.cache_dir / f"sitemap_{self.key}.txt"
        if cache.exists() and time.time() - cache.stat().st_mtime < self.cache_hours * 3600:
            return cache.read_text(encoding="utf-8").splitlines()
        urls: list[str] = []
        for part in self._read_sitemap(self.sitemap_urls[0]):
            urls.extend(self._read_sitemap(part))
        urls = [u for u in urls if self.is_shoe_url(u)]
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text("\n".join(urls), encoding="utf-8")
        return urls

    def parse_page(self, html: str, url: str) -> list[ParsedListing]:
        return parse_top4running(html, url, self.key)


class BergfreundeAdapter(RetailerAdapter):
    key = "bergfreunde"
    name = "Bergfreunde"
    base_url = "https://www.bergfreunde.eu"
    platform = "Nemačka prodavnica"
    sitemap_urls = ["https://www.bergfreunde.eu/sitemap/sitemap_products.xml.gz"]

    def is_shoe_url(self, url: str) -> bool:
        low = url.lower()
        return "running-shoes" in low and "women" not in low and "kids" not in low

    def parse_page(self, html: str, url: str) -> list[ParsedListing]:
        return parse_bergfreunde(html, url, self.key)


FOREIGN_ADAPTERS: list[type[RetailerAdapter]] = [Top4RunningAdapter, BergfreundeAdapter]
FOREIGN_NAMES = {a.key: a.name for a in FOREIGN_ADAPTERS}
