"""Parser za NBShop platformu (ET Sport, Sport Vision, Run'n'More, Extra Sports, Buzz).

Glavni proizvod se određuje preko JSON-LD Product (productID, sku, price). Stranice imaju i blokove sa
sličnim proizvodima, zato se veličine čitaju samo iz liste čiji id odgovara productID-u, a cene samo iz
bloka detalja proizvoda. Cena po veličini je u data-productsize-price.
"""
from __future__ import annotations

import html as html_lib
import re

from bs4 import BeautifulSoup, Tag

from matching.sizes import SizeInfo, classify_label, parse_size_value
from retailers.base import ParsedListing, RetailerAdapter, parse_money


def _product_ld(soup: BeautifulSoup) -> str | None:
    for script in soup.find_all("script", type="application/ld+json"):
        text = script.string or ""
        if re.search(r'"@type"\s*:\s*"Product"', text):
            return text
    return None


def _ld_field(ld: str, name: str) -> str | None:
    """Regex umesto json.loads: JSON-LD na ovim sajtovima često ima nevažeće prelome reda u opisu."""
    match = re.search(rf'"{name}"\s*:\s*"((?:[^"\\]|\\.)*)"', ld)
    return html_lib.unescape(match.group(1)).strip() if match else None


def _ld_brand(ld: str) -> str | None:
    match = re.search(r'"brand"\s*:\s*\{.*?"name"\s*:\s*"([^"]+)"', ld, re.S)
    return match.group(1).strip() if match else None


def _not_hidden(el: Tag | None) -> bool:
    if el is None:
        return False
    classes = el.get("class") or []
    return "hide" not in classes and "hidden" not in classes


def _value(scope: Tag, selector: str) -> float | None:
    el = scope.select_one(selector)
    return parse_money(el.get_text(" ", strip=True)) if el else None


def _price_block(soup: BeautifulSoup, ld_price: float | None) -> Tag | None:
    block = soup.select_one(".product-details-price")  # stariji šablon (nbshop5)
    if block:
        return block
    for widget in soup.select("[class*=nb-product-prices]"):
        parent_classes = " ".join(" ".join(p.get("class") or []) for p in list(widget.parents)[:4])
        if "detail" in parent_classes:
            return widget.parent or widget
    return None


def _parse_prices_theme5(block: Tag, listing: ParsedListing) -> None:
    old_wrap = block.select_one(".product-oldprice")
    if _not_hidden(old_wrap):
        listing.old_price = _value(old_wrap, ".value")
    regular_wrap = block.select_one(".product-pricewithoutdiscount")
    if _not_hidden(regular_wrap):
        listing.regular_price = _value(regular_wrap, ".value")
    min_wrap = block.select_one(".product-lastminprice")
    if _not_hidden(min_wrap):
        listing.retailer_30d_min = _value(min_wrap, ".value")


def _parse_prices_theme7(block: Tag, listing: ParsedListing) -> None:
    old_wrap = block.select_one(".nb-price-old")
    if _not_hidden(old_wrap):
        listing.old_price = _value(old_wrap, ".nbf-price-value")
    regular_wrap = block.select_one(".nb-price-regular")
    if _not_hidden(regular_wrap):
        listing.regular_price = _value(regular_wrap, ".nbf-price-value")
    text = block.get_text(" ", strip=True)
    match = re.search(r"Najni[žz]a cena[^0-9]{0,60}30\s*dana[^0-9]{0,12}([\d.,]+)\s*RSD", text, re.I)
    if match:
        listing.retailer_30d_min = parse_money(match.group(1))


LOYALTY_SELECTORS = ".loyalty-info-wrapper, .nb-price-loyalty, .nb-price-loyalty-dynamic, .product-loyalty-price"


def parse_conditional(block: Tag | None, listing: ParsedListing) -> None:
    """Uslovna cena nikad nije javna cena. Beleži se odvojeno, sa tekstom uslova."""
    if block is None:
        return
    for el in block.select(LOYALTY_SELECTORS):
        text = el.get_text(" ", strip=True)
        price = parse_money(text) if re.search(r"\d", text) else None
        if price:
            listing.loyalty_price = price
            listing.loyalty_condition = text[:200]
            break
    text = block.get_text(" ", strip=True)
    match = re.search(r"kupon(?:om)?\s*:?\s*([A-Z0-9]{3,20})[^0-9]{0,40}([\d.,]+)\s*RSD", text)
    if match:
        listing.coupon_price = parse_money(match.group(2))
        listing.coupon_condition = f"kupon {match.group(1)}: {text[:160]}"


def _sizes_theme5(soup: BeautifulSoup) -> list[SizeInfo] | None:
    wrapper = soup.select_one(".product-attributes-wrapper ul.product-attributes")
    if not wrapper:
        return None
    sizes = []
    for li in wrapper.select("li"):
        eu_el, original, cm = li.select_one(".eur-size"), li.select_one(".original-size"), li.select_one(".cm-size")
        label = (eu_el or original or li).get_text(" ", strip=True)
        eu = parse_size_value(eu_el.get_text(strip=True)) if eu_el else None
        system = "EU" if eu is not None else classify_label(label)[0]
        if eu is None and system == "EU":
            eu = classify_label(label)[1]
        disabled = "disabled" in (li.get("class") or [])
        sizes.append(SizeInfo(
            label=label, eu=eu, system=system,
            us=original.get_text(strip=True) if original else None,
            cm=cm.get_text(strip=True) if cm else None,
            available=not disabled,
            price=parse_money(li.get("data-productsize-price")) if not disabled else None,
            old_price=parse_money(li.get("data-productsize-oldprice")) if not disabled else None,
            stock_note=li.get("data-productsize-indicator-availability-label"),
        ))
    return sizes


def _sizes_theme7(soup: BeautifulSoup, product_id: str | None) -> list[SizeInfo] | None:
    container = soup.find(id=product_id) if product_id else None
    if container is None:
        return None
    sizes = []
    for el in container.select(".nbf-size-value"):
        label = el.get("data-filter_eu_velue") or el.get("data-filter_sizename_value") or el.get_text(strip=True)
        system, eu = classify_label(label)
        sizes.append(SizeInfo(
            label=label, eu=eu, system=system,
            us=el.get("data-filter_us_value") or None,
            uk=el.get("data-filter_uk_value") or None,
            cm=el.get("data-filter_cm_value") or None,
            available="disabled" not in (el.get("class") or []),
            price=parse_money(el.get("data-productsize-price")),
            old_price=parse_money(el.get("data-productsize-oldprice")),
            stock_note=el.get("data-productsize-indicator-availability-label"),
        ))
    # Oznake koje ponavljaju EU vrednost (ET Sport upisuje istu vrednost u US/UK/cm) nisu prave konverzije.
    for size in sizes:
        if size.us == size.label:
            size.us = None
        if size.uk == size.label:
            size.uk = None
        if size.cm == size.label:
            size.cm = None
    return sizes


def _delivery(ld: str, listing: ParsedListing) -> None:
    match = re.search(r'"shippingRate"\s*:\s*\{[^}]*?"value"\s*:\s*"?([\d.]+)', ld)
    if match:
        listing.delivery_rsd = float(match.group(1))
        listing.delivery_note = "iz strukturisanih podataka prodavca; uslovi mogu zavisiti od vrednosti korpe"


def _specifications(soup: BeautifulSoup, listing: ParsedListing) -> None:
    """Tabela 'Deklaracija' na stranici: Pol, Uzrast, Boja. Meni se uklanja jer i on sadrži reč 'Pol'."""
    for tag in soup.select("nav, header, footer, script, style, [class*=menu]"):
        tag.decompose()
    text = soup.get_text(" ", strip=True)
    age = re.search(r"\bUzrast\s+(Za\s+\w+|\w+)", text)
    if age and re.search(r"dec|dje|beb", age.group(1), re.I):
        listing.gender_attr = "deca"
    else:
        gender = re.search(r"\bPol\s+(Za\s+\w+|Mu[sš]karci|MU[SŠ]KARCI|[ŽZžz]ene|[ŽZ]ENE|Unisex|UNISEX|Dec\w+|DEC\w+)", text)
        if gender:
            listing.gender_attr = gender.group(1)
    color = re.search(r"\bBoja\s+([^\s]+)", text)
    if color:
        listing.color = color.group(1)
    # ET Sport piše "Sport TRČANJE"; spisak sportova sprečava da "Sport VISION" bude shvaćen kao namena.
    purpose = re.search(r"\bNamena\s+(\S+)", text) or re.search(
        r"\bSport\s+(TR[CČ]ANJE|TRENING|FUDBAL|KO[SŠ]ARKA|TENIS|OUTDOOR|LIFESTYLE|FITNES)\b", text)
    if purpose:
        listing.purpose = purpose.group(1)


def parse_nbshop(html: str, url: str, retailer: str) -> list[ParsedListing]:
    soup = BeautifulSoup(html, "lxml")
    ld = _product_ld(soup)
    if not ld:
        return []
    price_match = re.search(r'"price"\s*:\s*"?([\d.]+)"?', ld)
    current = float(price_match.group(1)) if price_match else None
    product_id = _ld_field(ld, "productID")
    title_el = soup.select_one("h1")
    title = title_el.get_text(" ", strip=True) if title_el else (_ld_field(ld, "name") or "")
    meta = soup.find("meta", attrs={"name": "description"})
    listing = ParsedListing(
        retailer=retailer, url=url, title=title, current_price=current,
        brand=_ld_brand(ld), sku=_ld_field(ld, "sku"),
        description=(meta.get("content") if meta else "") or "",
        seller=None,
    )
    block = _price_block(soup, current)
    is_theme5 = soup.select_one(".product-details-price") is not None
    if block is not None:
        (_parse_prices_theme5 if is_theme5 else _parse_prices_theme7)(block, listing)
    parse_conditional(block, listing)

    sizes = _sizes_theme5(soup) if is_theme5 else _sizes_theme7(soup, product_id)
    if sizes:
        listing.sizes = sizes
        # Cena po veličini mora da se slaže sa JSON-LD cenom; razlika se ne krije.
        priced = [s for s in sizes if s.available and s.price is not None]
        if priced:
            if listing.old_price is None:
                olds = [s.old_price for s in priced if s.old_price and current and s.old_price > current]
                listing.old_price = max(olds) if olds else None
            if current and not any(abs(s.price - current) < 1 for s in priced):
                listing.warnings.append("JSON-LD cena se ne poklapa ni sa jednom cenom po veličini")
                listing.price_verified = False
        listing.availability = "AVAILABLE" if any(s.available for s in sizes) else "OUT_OF_STOCK"
    else:
        # Bez liste veličina: JSON-LD je jedini signal, koji ne potvrđuje nijednu veličinu.
        availability = _ld_field(ld, "availability") or ""
        if "OutOfStock" in availability:
            listing.availability = "OUT_OF_STOCK"
        listing.warnings.append("veličine nisu pronađene na stranici")
    if listing.old_price and current and listing.old_price <= current:
        listing.old_price = None
    _delivery(ld, listing)
    _specifications(soup, listing)  # poslednje: uklanja meni iz stabla
    return [listing]


class NBShopAdapter(RetailerAdapter):
    platform = "NBShop"
    product_link_pattern = r'href="([^"#?]*/patike(?:-za-trcanje)?/\d+-[^"#?]+)"'

    def parse_page(self, html: str, url: str) -> list[ParsedListing]:
        return parse_nbshop(html, url, self.key)
