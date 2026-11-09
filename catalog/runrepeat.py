"""Preuzimanje nezavisnog kataloga sa RunRepeat-a.

Status izvora (proveren 2026-11-05): robots.txt dozvoljava /catalog/running-shoes?page=N i stranice
modela; pravna stranica nema klauzulu o automatskom prikupljanju, ali sajt nudi i plaćeno licenciranje
podataka (data.runrepeat.com). Zato: samo lična lokalna upotreba, spor tempo, uvek se čuva izvorni URL.

Kategorije: "Pace" sa RunRepeat-a se direktno mapira na daily_trainer/tempo/racing, a long_run i recovery
se izvode pravilom iz njihovih laboratorijskih oznaka. RunRepeat te dve kategorije ne daje direktno, pa je
osnova uvek zapisana u polju category_basis.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Iterable

from bs4 import BeautifulSoup

from common.http import FetchError, PoliteClient

log = logging.getLogger("catalog.runrepeat")
BASE = "https://runrepeat.com"
LISTING = BASE + "/catalog/running-shoes"
SOURCE = "RunRepeat"

KNOWN_BRANDS = [
    "New Balance", "Under Armour", "361 Degrees", "Topo Athletic", "La Sportiva", "The North Face", "Li-Ning",
    "ASICS", "Nike", "Adidas", "Hoka", "Puma", "Saucony", "Brooks", "Mizuno", "On", "Skechers", "Altra",
    "Salomon", "Reebok", "Merrell", "Diadora", "Inov-8", "Inov8", "Craft", "Kiprun", "Decathlon", "Xtep",
    "Anta", "Peak", "Karhu", "Scott", "Dynafit", "Norda", "Arc'teryx", "Speedland", "Tracksmith", "Veja",
    "Joma", "Kalenji", "Newton", "Vibram", "Xero Shoes", "Lululemon", "Qiaodan", "Bmai",
]


def split_brand(full_name: str) -> tuple[str, str]:
    for brand in sorted(KNOWN_BRANDS, key=len, reverse=True):
        if full_name.lower().startswith(brand.lower() + " "):
            return brand, full_name[len(brand) + 1:].strip()
    head, _, tail = full_name.partition(" ")
    return head, tail.strip()


def parse_generation(model_name: str) -> str | None:
    match = re.search(r"\bv?(\d+(?:\.\d+)?)\s*$", model_name, re.I)
    return match.group(1) if match else None


def slug_id(brand: str, model: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", f"{brand} {model}".lower()).strip("-")


def parse_listing_page(html: str) -> list[dict]:
    """Vraća [{name, url, score}] iz jedne stranice kataloga."""
    soup = BeautifulSoup(html, "lxml")
    items: list[dict] = []
    for li in soup.select("li.product_list"):
        link = li.select_one(".product-name a")
        score = li.select_one(".corescore-big__score")
        if not link:
            continue
        value = None
        if score and re.fullmatch(r"\d{1,3}", score.get_text(strip=True)):
            value = float(score.get_text(strip=True))
        items.append({"name": link.get_text(strip=True), "url": BASE + link["href"], "score": value})
    return items


def parse_max_page(html: str) -> int:
    pages = [int(p) for p in re.findall(r"running-shoes\?page=(\d+)", html)]
    return max(pages) if pages else 1


def _facts(soup: BeautifulSoup) -> dict[str, str]:
    """Činjenice sa stranice modela u obliku 'Terrain: Road'."""
    out: dict[str, str] = {}
    text = soup.get_text("\n", strip=True)
    for key in ("Terrain", "Pace", "Arch support", "Use", "Technology", "Collection"):
        match = re.search(rf"^{key}:\s*\n?(.+(?:\n,.*|\n[A-Z][^\n:]{{0,24}}\n?,)*)", text, re.M)
        if match:
            out[key] = re.sub(r"\s*\n\s*", " ", match.group(1)).strip(" |")
    return out


def _comparison_column(soup: BeautifulSoup) -> dict[str, str]:
    """Prva tabela poređenja: prva kolona sa vrednostima pripada modelu čija je stranica."""
    table = soup.find("table")
    out: dict[str, str] = {}
    if not table:
        return out
    for row in table.select("tr"):
        cells = row.select("th,td")
        if len(cells) >= 2:
            label = cells[0].get_text(" ", strip=True)
            value = cells[1].get_text(" ", strip=True)
            if label and label not in out:
                out[label] = value
    return out


def parse_model_page(html: str) -> dict:
    """Ekstraktuje samo ono što stranica zaista navodi. Sve ostalo ostaje None."""
    soup = BeautifulSoup(html, "lxml")
    facts = _facts(soup)
    column = _comparison_column(soup)
    surface = facts.get("Terrain", "").split(",")[0].strip().lower() or None

    pace = column.get("Pace") or facts.get("Pace") or ""
    arch = column.get("Arch support") or facts.get("Arch support") or ""
    shock = column.get("Shock absorption")
    energy = column.get("Energy return")
    softness = column.get("Midsole softness")

    categories: list[str] = []
    basis: list[str] = []
    if "Daily running" in pace:
        categories.append("daily_trainer")
    if "Tempo" in pace:
        categories.append("tempo")
    if "Competition" in pace:
        categories.append("racing")
    if pace:
        basis.append(f"RunRepeat Pace: {pace}")
    if "Stability" in arch:
        categories.append("stability")
        basis.append(f"RunRepeat Arch support: {arch}")
    if "daily_trainer" in categories and shock == "High":
        categories.append("long_run")
        basis.append("long_run izveden: Pace=Daily running i Shock absorption=High")
    if "daily_trainer" in categories and shock == "High" and softness == "Soft" and energy != "High":
        categories.append("recovery")
        basis.append("recovery izveden: Shock absorption=High, Midsole softness=Soft, Energy return nije High")

    cushioning_parts = [f"{label}: {value}" for label, value in
                        (("Shock absorption", shock), ("Energy return", energy), ("Midsole softness", softness))
                        if value]
    page_text = soup.get_text(" ", strip=True)
    plate = None
    column_text = " ".join(column.values())
    if re.search(r"carbon plate", column_text, re.I):
        plate = "carbon"
    elif re.search(r"nylon plate", column_text, re.I):
        plate = "nylon"
    # "Technology" na RunRepeat-u meša penu, uložak (Ortholite) i membranu (Gore-Tex), pa nije podatak o peni.
    technology = facts.get("Technology")
    if technology:
        cushioning_parts.append(f"Technology: {technology}")
    msrp = re.fullmatch(r"\$\s?(\d{2,4})", column.get("Price", ""))
    score = None
    score_el = soup.select_one(".our-score__value")
    if score_el and re.fullmatch(r"\d{1,3}", score_el.get_text(strip=True)):
        score = float(score_el.get_text(strip=True))
    return {
        "surface": surface,
        "categories": categories,
        "category_basis": "; ".join(basis) or None,
        "cushioning": ("RunRepeat lab: " + ", ".join(cushioning_parts)) if cushioning_parts else None,
        "midsole_foam": None,  # nema pouzdanog strukturisanog izvora; ne nagađamo
        "plate": plate,
        "msrp_usd": float(msrp.group(1)) if msrp else None,  # američka preporučena cena, a ne cena u prodavnici
        "score": score,
        "has_content": bool(page_text),
    }


def build_entry(item: dict, details: dict | None, verified_at: str) -> dict:
    brand, model = split_brand(item["name"])
    details = details or {}
    score = item.get("score") if item.get("score") is not None else details.get("score")
    return {
        "id": slug_id(brand, model),
        "brand": brand,
        "model_name": model,
        "generation": parse_generation(model),
        "genders": None,  # RunRepeat model nije podeljen po polu; pol se utvrđuje na nivou oglasa
        "surface": details.get("surface"),
        "categories": details.get("categories", []),
        "category_basis": details.get("category_basis"),
        "cushioning": details.get("cushioning"),
        "midsole_foam": details.get("midsole_foam"),
        "plate": details.get("plate"),
        "msrp_usd": details.get("msrp_usd"),
        "review_score": score,
        "review_source": SOURCE if score is not None else None,
        "review_url": item["url"] if score is not None else None,
        "release_year": None,  # stranica ga ne navodi strukturirano; ne nagađamo
        "source_url": item["url"],
        "last_verified_at": verified_at,
    }


def fetch_listing(client: PoliteClient, max_pages: int | None = None) -> list[dict]:
    first = client.get(LISTING).text
    items = parse_listing_page(first)
    last = parse_max_page(first)
    if max_pages:
        last = min(last, max_pages)
    for page in range(2, last + 1):
        try:
            items.extend(parse_listing_page(client.get(f"{LISTING}?page={page}").text))
        except FetchError as exc:
            log.warning("stranica %s kataloga nije učitana: %s", page, exc)
    seen: dict[str, dict] = {}
    for item in items:
        seen.setdefault(item["url"], item)
    return list(seen.values())


def fetch_entries(client: PoliteClient, items: Iterable[dict], verified_at: str,
                  known: dict[str, dict] | None = None, progress=None,
                  fallback: dict[str, dict] | None = None) -> tuple[list[dict], list[str]]:
    """Za svaki model čita stranicu sa detaljima. `known` preskače već proverene sa istim URL-om.

    `fallback`: kad stranica ne može da se učita, ostaje stari unos, da neuspeo zahtev ne obriše podatke.
    """
    entries: list[dict] = []
    failures: list[str] = []
    known = known or {}
    fallback = fallback or {}
    items = list(items)
    for index, item in enumerate(items, 1):
        if progress:
            progress(index, len(items), item["name"])
        previous = known.get(item["url"])
        if previous:
            refreshed = dict(previous)
            if item.get("score") is not None:
                refreshed.update(review_score=item["score"], review_source=SOURCE, review_url=item["url"])
            refreshed["last_verified_at"] = verified_at
            entries.append(refreshed)
            continue
        try:
            details = parse_model_page(client.get(item["url"]).text)
        except FetchError as exc:
            failures.append(f"{item['name']}: {exc}")
            if item["url"] in fallback:
                entries.append(fallback[item["url"]])
                continue
            details = None
        entries.append(build_entry(item, details, verified_at))
    return entries, failures


def dumps(entries: list[dict]) -> str:
    return json.dumps(sorted(entries, key=lambda e: e["id"]), ensure_ascii=False, indent=1)
