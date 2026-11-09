"""Zajednička osnova adaptera: otkrivanje preko sitemap-a, obrada greški po sajtu, keš sitemap-a."""
from __future__ import annotations

import gzip
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import urljoin

from bs4 import BeautifulSoup, SoupStrainer

from common.config import DATA_DIR
from common.http import (BlockedError, FetchError, NotFoundError, PoliteClient, RateLimitedError, RobotsDisallowed,
                         canonicalize_url)
from matching.matcher import normalize_text
from matching.sizes import SizeInfo

log = logging.getLogger("retailers")


def parse_money(text: str | float | int | None) -> float | None:
    """'16.799,21' | '13.299 RSD' | 16799.21 -> broj. Ne nagađa kad format nije jasan."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    match = re.search(r"\d[\d.,\s]*", str(text))
    if not match:
        return None
    raw = match.group(0).strip().replace(" ", "")
    if "," in raw and "." in raw:
        raw = raw.replace(".", "").replace(",", ".") if raw.rfind(",") > raw.rfind(".") else raw.replace(",", "")
    elif "," in raw:
        head, tail = raw.rsplit(",", 1)
        raw = head.replace(",", "") + ("." + tail if len(tail) <= 2 else tail)
    elif "." in raw:
        head, tail = raw.rsplit(".", 1)
        raw = head.replace(".", "") + ("." + tail if len(tail) <= 2 else tail)
    try:
        return float(raw)
    except ValueError:
        return None


def page_image(html: str, url: str) -> str | None:
    """Glavna slika proizvoda iz og:image. Rasprodat proizvod ume da ima prazan put (.../big/), što nije slika."""
    meta = BeautifulSoup(html, "lxml", parse_only=SoupStrainer("meta")).find("meta", attrs={"property": "og:image"})
    content = (meta.get("content") or "").strip() if meta else ""
    if not content or content.endswith("/"):
        return None
    return urljoin(url, content)


@dataclass
class ParsedListing:
    retailer: str
    url: str
    title: str
    current_price: float | None
    brand: str | None = None
    sku: str | None = None
    listing_key: str | None = None      # stabilan ID oglasa; podrazumevano kanonski URL
    color: str | None = None
    description: str = ""
    gender_attr: str | None = None
    purpose: str | None = None          # "Namena" sa stranice proizvoda, npr. Trčanje
    running: bool | None = None         # da li prodavac oglas vodi kao patiku za trčanje
    seller: str | None = None
    old_price: float | None = None      # prikazana precrtana / referentna cena
    regular_price: float | None = None  # "važeća MP cena"
    loyalty_price: float | None = None
    loyalty_condition: str | None = None
    coupon_price: float | None = None
    coupon_condition: str | None = None
    retailer_30d_min: float | None = None
    availability: str = "UNKNOWN"       # AVAILABLE | OUT_OF_STOCK | UNKNOWN
    sizes: list[SizeInfo] = field(default_factory=list)
    delivery_rsd: float | None = None
    delivery_note: str | None = None
    price_verified: bool = True
    warnings: list[str] = field(default_factory=list)
    image_url: str | None = None


@dataclass
class RetailerResult:
    retailer: str
    status: str = "NOT_VERIFIED"  # VERIFIED | PARTIAL | NOT_VERIFIED
    message: str | None = None
    listings: list[ParsedListing] = field(default_factory=list)
    pages_ok: int = 0
    pages_failed: int = 0
    candidates: int = 0


class RetailerAdapter:
    key = ""
    name = ""
    base_url = ""
    platform = ""
    supported = True
    note = ""
    sitemap_urls: list[str] = []
    robots_cross_host_note = ""
    running_category_urls: list[str] = []   # javne kategorije patika za trčanje, sa stranicama /page-N
    product_link_pattern = ""               # regex linka ka proizvodu na stranici kategorije
    sitemap_is_running_store = False        # ceo sitemap su patike za trčanje; Namena se proverava po stranici
    max_category_pages = 60
    min_delay_s = 0.0                       # sporiji tempo za sajt koji je već tražio da usporimo

    def __init__(self, delay_s: float = 1.5, timeout_s: float = 30, max_retries: int = 3,
                 cache_hours: int = 24, cache_dir: Path | None = None, client: PoliteClient | None = None):
        self.client = client or PoliteClient(self.base_url, delay_s=delay_s, timeout_s=timeout_s,
                                             max_retries=max_retries)
        self.cache_hours = cache_hours
        self.cache_dir = cache_dir or DATA_DIR / "cache"

    # --- otkrivanje ---------------------------------------------------------
    def _read_sitemap(self, url: str) -> list[str]:
        resp = self.client.get(url)
        body = resp.content
        if body[:2] == b"\x1f\x8b":
            body = gzip.decompress(body)
        text = body.decode("utf-8", "replace")
        return [loc.replace("&amp;", "&") for loc in re.findall(r"<loc>\s*(.*?)\s*</loc>", text)]

    def all_urls(self) -> list[str]:
        cache = self.cache_dir / f"sitemap_{self.key}.txt"
        if cache.exists() and time.time() - cache.stat().st_mtime < self.cache_hours * 3600:
            return cache.read_text(encoding="utf-8").splitlines()
        urls: list[str] = []
        for sitemap in self.sitemap_urls:
            urls.extend(self._read_sitemap(sitemap))
        urls = [u for u in urls if self.is_shoe_url(u)]
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text("\n".join(urls), encoding="utf-8")
        return urls

    def is_shoe_url(self, url: str) -> bool:
        return "patike" in url.lower() and "?" not in url

    def discover(self, keep: Callable[[str], bool]) -> list[str]:
        seen: dict[str, None] = {}
        for url in self.all_urls():
            if keep(url):
                seen.setdefault(canonicalize_url(url), None)
        return list(seen)

    def discover_running(self) -> tuple[list[str], list[str]]:
        """Sve patike koje prodavac vodi pod trčanjem, nezavisno od kataloga. Vraća (URL-ovi, problemi)."""
        problems: list[str] = []
        urls: dict[str, None] = {}
        try:
            for url in self.all_urls():
                if self.sitemap_is_running_store or "trcanje" in url.lower():
                    urls.setdefault(canonicalize_url(url), None)
        except FetchError as exc:
            problems.append(f"sitemap: {exc}")
        for category in self.running_category_urls:
            try:
                for url in self._read_category(category):
                    urls.setdefault(url, None)
            except FetchError as exc:
                problems.append(f"kategorija {category}: {exc}")
        return list(urls), problems

    def _read_category(self, category: str) -> list[str]:
        found: dict[str, None] = {}
        for page in range(1, self.max_category_pages + 1):
            url = category if page == 1 else f"{category.rstrip('/')}/page-{page}"
            try:
                resp = self.client.get(url)
            except NotFoundError:
                break
            links = {canonicalize_url(urljoin(str(resp.url), link))
                     for link in re.findall(self.product_link_pattern, resp.text)}
            if not links - found.keys():
                break
            found.update(dict.fromkeys(sorted(links)))
        return list(found)

    @staticmethod
    def _mark_running(listing: ParsedListing, in_running_category: bool) -> None:
        if listing.purpose:  # izričit podatak prodavca ima prednost: kategorija ume da sadrži i preporuke
            listing.running = bool(re.search(r"tr[cč]anj", listing.purpose, re.I))
        else:
            text = normalize_text(f"{listing.url} {listing.title}")
            listing.running = in_running_category or "trcanje" in text

    # --- parsiranje (implementira adapter) ----------------------------------
    def parse_page(self, html: str, url: str) -> list[ParsedListing]:
        raise NotImplementedError

    # --- izvršavanje --------------------------------------------------------
    def run(self, keep: Callable[[str], bool], max_pages: int | None = None,
            progress: Callable[[str, int, int], None] | None = None,
            broad_keep: Callable[[str], bool] | None = None,
            on_parsed: Callable[[list[ParsedListing]], None] | None = None) -> RetailerResult:
        """Sa `broad_keep` otvara i sve patike iz kategorija za trčanje, a ne samo one koje katalog prepoznaje.

        Sa `on_parsed` se svaka stranica predaje odmah, pa prekid skena ne gubi već pročitano.
        """
        result = RetailerResult(self.key)
        if not self.supported:
            result.message = self.note or "adapter nije podržan"
            return result
        running_urls: set[str] = set()
        problems: list[str] = []
        try:
            urls = self.discover(keep)
            if broad_keep is not None:
                found, problems = self.discover_running()
                running_urls = set(found)
                known = set(urls)
                urls.extend(u for u in found if u not in known and broad_keep(u))
        except BlockedError as exc:
            result.message = f"BLOKIRANO: {exc}"
            return result
        except FetchError as exc:
            result.message = f"Sitemap nije dostupan: {exc}"
            return result
        result.candidates = len(urls)
        if max_pages:
            urls = urls[:max_pages]
        consecutive_blocks = 0
        for index, url in enumerate(urls, 1):
            if progress and (index % 25 == 0 or index == len(urls)):
                progress(self.key, index, len(urls))
            try:
                resp = self.client.get(url)
                parsed = self.parse_page(resp.text, canonicalize_url(str(resp.url)))
                image = page_image(resp.text, str(resp.url)) if parsed else None
                for item in parsed:
                    self._mark_running(item, url in running_urls)
                    item.image_url = image
            except RateLimitedError as exc:
                result.pages_failed += 1 + len(urls) - index
                result.message = f"Sajt traži da usporimo (HTTP 429), sken je stao posle {result.pages_ok} stranica: {exc}"
                log.warning("%s: %s", self.key, exc)
                break
            except BlockedError as exc:
                consecutive_blocks += 1
                result.pages_failed += 1
                log.warning("%s: %s", self.key, exc)
                if consecutive_blocks >= 3:
                    result.message = f"BLOKIRANO tokom skena posle {result.pages_ok} stranica: {exc}"
                    result.pages_failed += len(urls) - index
                    break
                continue
            except RobotsDisallowed as exc:
                log.info("%s: %s", self.key, exc)
                continue
            except FetchError as exc:
                result.pages_failed += 1
                log.warning("%s: %s", self.key, exc)
                continue
            except Exception:  # greška parsera na jednoj stranici ne sme da sruši sken
                result.pages_failed += 1
                log.exception("%s: parser nije uspeo za %s", self.key, url)
                continue
            consecutive_blocks = 0
            result.pages_ok += 1
            if on_parsed:
                on_parsed(parsed)
            else:
                result.listings.extend(parsed)
        if result.pages_ok and not result.pages_failed:
            result.status = "VERIFIED"
        elif result.pages_ok:
            result.status = "PARTIAL"
            result.message = result.message or f"{result.pages_failed} stranica nije provereno"
        elif not urls:
            result.status = "VERIFIED"
            result.message = "Nijedan traženi model nije u sitemap-u ovog sajta"
        else:
            result.message = result.message or "Nijedna stranica nije proverena"
        if problems and result.status == "VERIFIED":
            # Bez kategorije ne znamo da li smo videli sve, pa to ne sme da izgleda kao potpun sken.
            result.status = "PARTIAL"
            result.message = "Otkrivanje nije potpuno: " + "; ".join(problems)
        return result

    def check(self) -> tuple[str, str]:
        """Za `doctor`: proverava pristupnost bez skeniranja."""
        if not self.supported:
            try:
                self.client.robots()
                resp = self.client.get(self.base_url + "/")
                return "UNSUPPORTED", f"{self.note} (naslovna strana odgovara: HTTP {resp.status_code})"
            except FetchError as exc:
                return "UNSUPPORTED", f"{self.note} ({exc})"
        try:
            urls = self._read_sitemap(self.sitemap_urls[0])
            return "OK", f"sitemap dostupan ({len(urls)} URL-ova u prvom fajlu)"
        except FetchError as exc:
            return "NOT_VERIFIED", str(exc)
