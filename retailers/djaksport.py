"""Đak Sport: javni Magento HTML i sitemap, uz postojeće provere pristupa."""
from __future__ import annotations

from urllib.parse import urljoin, urlsplit, parse_qs

from bs4 import BeautifulSoup

from common.http import FetchError, canonicalize_url
from retailers.base import ParsedListing, RetailerAdapter
from retailers.magento import parse_magento


class DjakSportAdapter(RetailerAdapter):
    key = "djaksport"
    name = "Đak Sport"
    base_url = "https://www.djaksport.com"
    platform = "Magento"
    note = "Javni HTML; pri odbijenom pristupu status ostaje NOT VERIFIED"
    min_delay_s = 3.0
    sitemap_urls = [base_url + "/media/sitemap/djak.xml"]
    running_category_urls = [base_url + "/sport/trcanje/patike-za-trcanje"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.client.delay_s = max(self.client.delay_s, self.min_delay_s)

    def is_shoe_url(self, url: str) -> bool:
        parts = urlsplit(url)
        return (parts.hostname == "www.djaksport.com" and not parts.query
                and parts.path.count("/") == 1 and "patike" in parts.path.lower())

    def _read_sitemap(self, url: str) -> list[str]:
        # Đak objavljuje sitemap indeks, pa prvo čitamo njegove XML delove.
        links = super()._read_sitemap(url)
        if not links:
            raise FetchError(f"Đak: sitemap je prazan ili nije čitljiv ({url})")
        if url == self.sitemap_urls[0]:
            products = []
            for link in links:
                parts = urlsplit(link)
                if parts.hostname != "www.djaksport.com" or not parts.path.startswith("/media/sitemap/"):
                    raise FetchError("Đak: neočekivan link u sitemap indeksu")
                products.extend(super()._read_sitemap(link))
            return products
        return links

    def _read_category(self, category: str) -> list[str]:
        found = {}
        url = category
        for page in range(1, self.max_category_pages + 1):
            response = self.client.get(url)
            soup = BeautifulSoup(response.text, "lxml")
            links = {canonicalize_url(urljoin(url, a["href"]))
                     for a in soup.select(".products .product-item-name a[href]")}
            links = {link for link in links if self.is_shoe_url(link)}
            if not links - found.keys():
                raise FetchError(f"Đak: prazna ili ponovljena stranica kategorije ({url})")
            found.update(dict.fromkeys(sorted(links)))
            next_el = soup.select_one(".pages a.next[href]")
            if next_el is None:
                return list(found)
            next_url = urljoin(url, next_el["href"])
            parts = urlsplit(next_url)
            query = parse_qs(parts.query)
            if (parts.hostname != "www.djaksport.com" or parts.path != urlsplit(category).path
                    or query != {"p": [str(page + 1)]}):
                raise FetchError("Đak: paginacija nije proverena")
            url = next_url
        raise FetchError("Đak: kategorija ima više stranica od dozvoljenog limita")

    def parse_page(self, html: str, url: str) -> list[ParsedListing]:
        return parse_magento(html, url, self.key)
