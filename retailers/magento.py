"""Zajednički parser javnih Magento stranica Planete i Đaka."""
from __future__ import annotations

import json
from bs4 import BeautifulSoup
from matching.sizes import SizeInfo, classify_label
from retailers.base import ParsedListing, parse_money


def _objects(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from _objects(child)


def parse_magento(html: str, url: str, retailer: str = "planetasport") -> list[ParsedListing]:
    soup = BeautifulSoup(html, "lxml")
    product = None
    config = None
    for script in soup.select('script[type="application/ld+json"], script[type="text/x-magento-init"]'):
        try:
            data = json.loads(script.string or script.get_text())
        except (ValueError, TypeError):
            continue
        for obj in _objects(data):
            if obj.get("@type") == "Product":
                product = obj
            candidate = obj.get("jsonConfig")
            if isinstance(candidate, dict) and "attributes" in candidate:
                config = candidate
    if not product:
        raise ValueError("Magento: nema strukturisanih podataka proizvoda")
    offer = product.get("offers", {})
    if isinstance(offer, list):
        if len(offer) != 1:
            raise ValueError("Magento: više ponuda bez nedvosmislene cene")
        offer = offer[0]
    if not isinstance(offer, dict) or offer.get("priceCurrency") != "RSD":
        raise ValueError("Magento: nema nedvosmislene cene u RSD")
    price = parse_money(offer.get("price"))
    if price is None or price <= 0:
        raise ValueError("Magento: cena nije čitljiva")
    specs = {}
    for row in soup.select("#product-attribute-specs-table tr"):
        label, value = row.select_one("th"), row.select_one("td")
        if label and value:
            specs[label.get_text(" ", strip=True)] = value.get_text(" ", strip=True)
    for row in soup.select(".technical-specifications-options tr"):
        cells = row.select("td")
        if len(cells) == 2:
            key = cells[0].get_text(" ", strip=True).rstrip(":")
            specs[key] = cells[1].get_text(" ", strip=True)
    brand = product.get("brand", {})
    brand = brand.get("name") if isinstance(brand, dict) else brand
    meta = soup.select_one('meta[name="description"]')
    old_el = soup.select_one(".product-info-main .old-price .price, .product-info-main .regular-price .price")
    old = parse_money(old_el.get_text(" ", strip=True)) if old_el else None
    sizes = []
    if config:
        if old is None:
            old = parse_money(config.get("prices", {}).get("oldPrice", {}).get("amount"))
        attributes = config.get("attributes", {})
        if isinstance(attributes, dict):
            attributes = attributes.values()
        elif not isinstance(attributes, list):
            raise ValueError("Magento: nečitljiva lista atributa")
        for attribute in attributes:
            if attribute.get("code") != "size":
                continue
            for option in attribute.get("options", []):
                label = str(option.get("label", ""))
                system, eu = classify_label(label)
                children = [str(p) for p in option.get("products", [])]
                # Prikaz svih brojeva nije dokaz stanja ako sajt uključuje rasprodate.
                available = bool(children) if config.get("canDisplayShowOutOfStockStatus") is False else None
                salable = config.get("salable")
                if isinstance(salable, dict):
                    ids = salable.get(str(attribute.get("id")), {}).get(str(option.get("id")))
                    if isinstance(ids, list):
                        children = [str(p) for p in ids]
                        available = bool(children)
                amounts = [parse_money(config.get("optionPrices", {}).get(p, {}).get("finalPrice", {}).get("amount"))
                           for p in children]
                amounts = [p for p in amounts if p is not None and p > 0]
                sizes.append(SizeInfo(label=label, system=system, eu=eu, available=available,
                                      price=min(amounts) if amounts else None))
    availability = str(offer.get("availability", "")).rsplit("/", 1)[-1]
    state = "OUT_OF_STOCK" if availability == "OutOfStock" else "UNKNOWN"
    if state == "OUT_OF_STOCK":
        for size in sizes:
            size.available = False
    elif any(size.available is True for size in sizes):
        state = "AVAILABLE"
        priced = [size.price for size in sizes if size.available is True and size.price is not None]
        if priced:
            price = min(priced)
    elif availability == "InStock":
        state = "AVAILABLE"
    return [ParsedListing(
        retailer=retailer, url=url, title=product.get("name") or "", sku=product.get("sku"),
        brand=brand, current_price=price, old_price=old if old and old > price else None,
        description=(meta.get("content") if meta else "") or "", color=specs.get("Boja"),
        gender_attr=specs.get("Pol"), purpose=specs.get("Sport") or specs.get("Potkategorija"), availability=state, sizes=sizes,
    )]

