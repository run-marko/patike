"""Parser za platformu koju koriste N Sport i Intersport Srbija (ista struktura stranice).

Veličine su input elementi sa data-size i data-max-quantity, a na stranici su samo one koje su na stanju.
Veličina se računa kao EU samo kad je broj u EU opsegu; ostalo ostaje nepotvrđeno.
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from matching.sizes import SizeInfo, classify_label
from retailers.base import ParsedListing, RetailerAdapter, parse_money


def parse_nsport_platform(html: str, url: str, retailer: str) -> list[ParsedListing]:
    soup = BeautifulSoup(html, "lxml")
    wrap = soup.select_one(".product-price-wrap")
    if wrap is None:
        return []
    price_el, old_el = wrap.select_one(".product-price"), wrap.select_one(".product-old-price")
    current = parse_money(price_el.get_text(" ", strip=True)) if price_el else None
    title_el = soup.select_one("h1")
    title = title_el.get_text(" ", strip=True) if title_el else (soup.title.string if soup.title else "") or ""
    code_el = soup.select_one(".product_code")
    sku = None
    if code_el:
        match = re.search(r":\s*(\S+)", code_el.get_text(" ", strip=True))
        sku = match.group(1) if match else None
    color_el = soup.select_one(".short_description")
    color = None
    if color_el:
        match = re.search(r"Boja:\s*(.+)", color_el.get_text(" ", strip=True))
        color = match.group(1).strip() if match else None
    brand_match = re.search(r'"item_brand"\s*:\s*"([^"]+)"', html)
    meta = soup.find("meta", attrs={"name": "description"})
    listing = ParsedListing(
        retailer=retailer, url=url, title=title, current_price=current,
        brand=brand_match.group(1) if brand_match else None,
        sku=sku, color=color, description=(meta.get("content") if meta else "") or "",
        old_price=parse_money(old_el.get_text(" ", strip=True)) if old_el else None,
    )
    sizes = []
    for field in soup.select("ul.size-list input.fnc-product-cart-size"):
        label = field.get("data-size") or ""
        system, eu = classify_label(label)
        try:
            quantity = int(field.get("data-max-quantity") or 0)
        except ValueError:
            quantity = 0
        sizes.append(SizeInfo(label=label, eu=eu, system=system, available=quantity > 0, price=current))
    listing.sizes = sizes
    out_of_stock = "nije na lageru" in html.lower()
    if any(s.available for s in sizes):
        listing.availability = "AVAILABLE"
    elif out_of_stock:
        listing.availability = "OUT_OF_STOCK"
    if listing.old_price and current and listing.old_price <= current:
        listing.old_price = None
    delivery = re.search(r"Cena isporuke je\s*([\d.]+)\s*RSD", html)
    if delivery:
        listing.delivery_rsd = parse_money(delivery.group(1))
        listing.delivery_note = "tekst sa stranice proizvoda"
    return [listing]


class NSportPlatformAdapter(RetailerAdapter):
    platform = "N Sport / Intersport"

    def is_shoe_url(self, url: str) -> bool:
        low = url.lower()
        return "/patike" in low and low.endswith(".html") and "filter" not in low

    def parse_page(self, html: str, url: str) -> list[ParsedListing]:
        return parse_nsport_platform(html, url, self.key)
