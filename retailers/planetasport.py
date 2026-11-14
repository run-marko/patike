"""Planeta Sport: javni Magento HTML, bez browsera i privatnih API-ja."""
from __future__ import annotations

import json
import math
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from common.http import FetchError, canonicalize_url
from retailers.base import ParsedListing, RetailerAdapter


from retailers.magento import parse_magento


def parse_planeta(html: str, url: str) -> list[ParsedListing]:
    return parse_magento(html, url, "planetasport")


class PlanetaSportAdapter(RetailerAdapter):
    key = "planetasport"
    name = "Planeta Sport"
    base_url = "https://planetasport.rs"
    platform = "Magento"
    note = "Javni HTML; pri odbijenom pristupu status ostaje NOT VERIFIED"
    min_delay_s = 3.0
    sitemap_urls = [
        base_url + "/zsdev_sitemap/products/sitemap_products_muskarci.xml",
        base_url + "/zsdev_sitemap/products/sitemap_products_zene.xml",
    ]
    running_category_urls = [
        base_url + "/muskarci/obuca/patike-za-trcanje.html",
        base_url + "/zene/obuca/patike-za-trcanje.html",
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.client.delay_s = max(self.client.delay_s, self.min_delay_s)

    def is_shoe_url(self, url: str) -> bool:
        parts = urlsplit(url)
        return (parts.hostname == "planetasport.rs" and not parts.query
                and parts.path.endswith(".html") and parts.path.count("/") == 1
                and "patike-" in parts.path.lower())

    def _read_category(self, category: str) -> list[str]:
        found = {}
        total_pages = 1
        for page in range(1, self.max_category_pages + 1):
            url = category if page == 1 else f"{category}?p={page}"
            resp = self.client.get(url)
            soup = BeautifulSoup(resp.text, "lxml")
            data_el = soup.select_one("#constructor-data-json")
            try:
                data = json.loads(data_el.get_text() if data_el else "")
                request, response = data["request"], data["response"]
                if int(request["page"]) != page:
                    raise ValueError("pogrešna stranica rezultata")
                total_pages = math.ceil(int(response["total_num_results"]) / int(request["num_results_per_page"]))
            except (KeyError, ValueError, TypeError, ZeroDivisionError) as exc:
                raise FetchError(f"Planeta: paginacija nije proverena ({url})") from exc
            links = {canonicalize_url(urljoin(url, a["href"]))
                     for a in soup.select("#nst-search-results .product-item-name a[href]")}
            links = {link for link in links if self.is_shoe_url(link)}
            if total_pages and not links - found.keys():
                raise FetchError(f"Planeta: prazna ili ponovljena stranica kategorije ({url})")
            found.update(dict.fromkeys(sorted(links)))
            if page >= total_pages:
                return list(found)
        raise FetchError("Planeta: kategorija ima više stranica od dozvoljenog limita")

    def parse_page(self, html: str, url: str) -> list[ParsedListing]:
        return parse_planeta(html, url)
