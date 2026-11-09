"""Ananas (ananas.rs) - marketplace. Svaki oglas je jedna veličina jednog stvarnog prodavca.

Podaci o ceni, prodavcu, veličini i stanju čitaju se iz serijalizovanog proizvoda na samoj stranici.
Sken spaja oglase istog naslova (model + boja) i istog prodavca u jednu ponudu sa listom veličina, jer
Ananas daje različit groupId i za veličine iste patike. Različiti prodavci ostaju odvojene ponude.
"""
from __future__ import annotations

import re
import time

from bs4 import BeautifulSoup

from common.http import NotFoundError
from matching.sizes import SizeInfo, classify_label
from retailers.base import ParsedListing, RetailerAdapter


def _field(text: str, name: str, start: int = 0) -> str | None:
    match = re.compile(rf'"{name}":"((?:[^"\\]|\\.)*)"').search(text, start)
    return match.group(1) if match else None


def _number(text: str, name: str, start: int = 0) -> float | None:
    match = re.compile(rf'"{name}":(-?\d+(?:\.\d+)?)').search(text, start)
    return float(match.group(1)) if match else None


def _attribute(spec: str, key: str) -> str | None:
    match = re.search(rf'"key":"{key}","trait":"[^"]*",(?:"name":"([^"]*)"|"content":"([^"]*)")', spec)
    return (match.group(1) or match.group(2)) if match else None


def parse_ananas(html: str, url: str, retailer: str = "ananas") -> list[ParsedListing]:
    data = html.replace('\\"', '"')
    start = data.find('"product":{"id"')
    if start < 0:
        return []
    price_at = data.find('"priceV2":{', start)
    if price_at < 0:
        return []
    soup = BeautifulSoup(html, "lxml")
    seller_el = soup.select_one("[data-testid=product-merchant-title]")
    seller = seller_el.get_text(strip=True) if seller_el else None
    if seller is None:
        merchant_at = data.find('"merchant":{"id"', start)
        seller = _field(data, "displayName", merchant_at) if merchant_at > 0 else None

    title = _field(data, "name", start) or (soup.title.string if soup.title else "") or ""
    brand_at = data.find('"brand":{', start)
    brand = _field(data, "name", brand_at) if brand_at > 0 else None
    spec_at = data.find('"specifications":{', start)
    spec_end = data.find('"merchant":{"id"', spec_at)
    spec = data[spec_at:spec_end] if spec_at > 0 else ""

    sellable = _number(data, "sellablePrice", price_at)
    base = _number(data, "basePrice", price_at)
    lowest_30 = _number(data, "lowestPrice30Days", price_at)
    available = _number(data, "available", price_at)
    shipping = re.compile(r'"shippingCost":\{"price":(\d+(?:\.\d+)?)').search(data, price_at)

    label = _attribute(spec, "Size") or ""
    system, eu = classify_label(label)
    is_available = None if available is None else available > 0
    size = SizeInfo(label=label, eu=eu, system=system, available=is_available, price=sellable, url=url,
                    stock_note=f"{int(available)} kom" if available else None) if label else None
    listing = ParsedListing(
        retailer=retailer, url=url, title=title, current_price=sellable, brand=brand,
        sku=None,  # EAN na Ananasu važi za jednu veličinu, a ne za boju; nije SKU varijante
        listing_key=f"{title}|{seller or ''}",
        color=_attribute(spec, "Color"), gender_attr=_attribute(spec, "Gender"),
        seller=seller,
        old_price=base if base and sellable and base > sellable else None,
        retailer_30d_min=lowest_30,
        sizes=[size] if size else [],
        delivery_rsd=float(shipping.group(1)) if shipping else None,
        delivery_note="procena Ananas-a za ovog prodavca" if shipping else None,
    )
    if is_available is True:
        listing.availability = "AVAILABLE"
    elif is_available is False:
        listing.availability = "OUT_OF_STOCK"
    return [listing]


def merge_marketplace_listings(listings: list[ParsedListing]) -> list[ParsedListing]:
    """Spaja oglase po veličini (isti naslov + isti prodavac). Različiti prodavci ostaju odvojeni."""
    merged: dict[str, ParsedListing] = {}
    for item in listings:
        key = item.listing_key or item.url
        current = merged.get(key)
        if current is None:
            merged[key] = item
            continue
        current.sizes.extend(item.sizes)
        current.image_url = current.image_url or item.image_url
        if item.availability == "AVAILABLE":
            current.availability = "AVAILABLE"
        elif current.availability == "UNKNOWN":
            current.availability = item.availability
        if item.current_price is not None and (current.current_price is None
                                               or item.current_price < current.current_price):
            current.current_price = item.current_price
            current.url = item.url
            current.old_price = item.old_price
            current.retailer_30d_min = item.retailer_30d_min
    return list(merged.values())


class AnanasAdapter(RetailerAdapter):
    key = "ananas"
    name = "Ananas"
    base_url = "https://ananas.rs"
    platform = "Marketplace"
    max_product_sitemaps = 80

    def is_shoe_url(self, url: str) -> bool:
        return "/proizvod/" in url and "patike" in url.lower()

    def all_urls(self) -> list[str]:
        # Indeks navodi samo deo fajlova products_sitemap_N.xml, pa se čitaju redom do prvog koji ne postoji.
        cache = self.cache_dir / f"sitemap_{self.key}.txt"
        if cache.exists() and time.time() - cache.stat().st_mtime < self.cache_hours * 3600:
            return cache.read_text(encoding="utf-8").splitlines()
        urls: list[str] = []
        for index in range(1, self.max_product_sitemaps + 1):
            try:
                urls.extend(self._read_sitemap(f"{self.base_url}/products_sitemap_{index}.xml"))
            except NotFoundError:
                break
        urls = [u for u in urls if self.is_shoe_url(u)]
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text("\n".join(urls), encoding="utf-8")
        return urls

    def parse_page(self, html: str, url: str) -> list[ParsedListing]:
        return parse_ananas(html, url, self.key)

    def run(self, keep, max_pages=None, progress=None, broad_keep=None):
        result = super().run(keep, max_pages=max_pages, progress=progress, broad_keep=broad_keep)
        result.listings = merge_marketplace_listings(result.listings)
        return result

    def check(self) -> tuple[str, str]:
        try:
            urls = self._read_sitemap(f"{self.base_url}/products_sitemap_1.xml")
            return "OK", f"sitemap dostupan ({len(urls)} URL-ova u prvom fajlu)"
        except Exception as exc:
            return "NOT_VERIFIED", str(exc)
