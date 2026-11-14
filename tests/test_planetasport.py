import json

import httpx
import pytest

from common.http import FetchError, PoliteClient, USER_AGENT
from retailers.planetasport import PlanetaSportAdapter, parse_planeta


URL = "https://planetasport.rs/adidas-patike-supernova-ease-2-m-jq4335.html"


def product_page(*, show_out=False, salable=None, stock="InStock", currency="RSD"):
    product = {"@type": "Product", "name": "ADIDAS Supernova Ease 2 M", "sku": "JQ4335",
               "brand": {"name": "ADIDAS"},
               "offers": {"price": "5000.99", "priceCurrency": currency,
                          "availability": "https://schema.org/" + stock}}
    config = {
        "attributes": {"158": {"id": "158", "code": "size", "options": [
            {"id": "1", "label": "45.5", "products": ["a"]},
            {"id": "2", "label": "46", "products": ["b"]},
        ]}},
        "prices": {"oldPrice": {"amount": 10000.99}},
        # The cheapest child is not an available size and must never win the ranking.
        "optionPrices": {"a": {"finalPrice": {"amount": 7299.99}},
                         "b": {"finalPrice": {"amount": 7999.99}},
                         "sold-out": {"finalPrice": {"amount": 5000.99}}},
        "canDisplayShowOutOfStockStatus": show_out,
        "salable": [] if salable is None else salable,
    }
    return (f'<script type="application/ld+json">{json.dumps(product)}</script>'
            f'<script type="text/x-magento-init">{json.dumps({"swatches": {"renderer": {"jsonConfig": config}}})}</script>'
            '<table id="product-attribute-specs-table"><tr><th>Pol</th><td>MUSKARCI</td></tr>'
            '<tr><th>Sport</th><td>TRČANJE</td></tr></table>')


def test_only_available_children_determine_price_and_size_prices():
    item = parse_planeta(product_page(), URL)[0]
    assert item.current_price == 7299.99
    assert item.old_price == 10000.99
    assert item.sku == "JQ4335"
    assert item.gender_attr == "MUSKARCI" and item.purpose == "TRČANJE"
    assert [(s.eu, s.available, s.price) for s in item.sizes] == [
        (45.5, True, 7299.99), (46.0, True, 7999.99)]


def test_sizes_are_unknown_when_display_includes_out_of_stock():
    item = parse_planeta(product_page(show_out=True), URL)[0]
    assert all(size.available is None for size in item.sizes)


def test_displayed_reference_price_survives_equal_magento_old_price():
    html = product_page().replace('10000.99', '7299.99')
    html += '<div class="product-info-main"><span class="regular-price"><span class="price">10.599,99 RSD</span></span></div>'
    assert parse_planeta(html, URL)[0].old_price == 10599.99


def test_explicit_salable_map_excludes_sold_out_size():
    item = parse_planeta(product_page(show_out=True, salable={"158": {"1": [], "2": ["b"]}}), URL)[0]
    assert [s.available for s in item.sizes] == [False, True]
    assert item.current_price == 7999.99


def test_product_out_of_stock_overrides_swatch_options():
    item = parse_planeta(product_page(stock="OutOfStock"), URL)[0]
    assert item.availability == "OUT_OF_STOCK"
    assert all(s.available is False for s in item.sizes)


@pytest.mark.parametrize("html", ["<html>Something went wrong</html>", product_page(currency="EUR")])
def test_invalid_page_is_a_failed_parse_not_an_empty_success(html):
    with pytest.raises(ValueError):
        parse_planeta(html, URL)


def category_page(page, total, links):
    data = {"request": {"page": page, "num_results_per_page": 1},
            "response": {"total_num_results": total}}
    return ('<script id="constructor-data-json" type="application/json">' + json.dumps(data) + '</script>'
            '<div id="nst-search-results">' + ''.join(
                f'<strong class="product-item-name"><a href="{link}">Shoe</a></strong>' for link in links) + '</div>')


def adapter_for(handler, tmp_path):
    client = PoliteClient("https://planetasport.rs", sleep=lambda _: None,
                          client=httpx.Client(transport=httpx.MockTransport(handler), headers={"User-Agent": USER_AGENT}))
    return PlanetaSportAdapter(client=client, cache_dir=tmp_path)


def test_category_follows_all_pages_and_excludes_recommendations(tmp_path):
    seen = []
    category = PlanetaSportAdapter.running_category_urls[0]
    second = "https://planetasport.rs/nike-patike-revolution-7-m-fb2207.html"

    def handler(request):
        seen.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /catalogsearch/")
        if str(request.url) == category:
            return httpx.Response(200, text=category_page(1, 2, [URL]) +
                                  '<strong class="product-item-name"><a href="/patike-recommendation.html">Other</a></strong>')
        return httpx.Response(200, text=category_page(2, 2, [second]))

    adapter = adapter_for(handler, tmp_path)
    assert adapter._read_category(category) == [URL, second]
    assert seen[-1] == category + "?p=2"


def test_repeated_category_page_is_reported_incomplete(tmp_path):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *")
        return httpx.Response(200, text=category_page(1, 2, [URL]))

    with pytest.raises(FetchError, match="paginacija"):
        adapter_for(handler, tmp_path)._read_category(PlanetaSportAdapter.running_category_urls[0])


def test_blocked_site_remains_not_verified(tmp_path):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *")
        return httpx.Response(403, text="Forbidden")

    result = adapter_for(handler, tmp_path).run(lambda _: True)
    assert result.status == "NOT_VERIFIED"
    assert not result.listings


def test_empty_magento_attribute_list_is_not_a_parser_failure():
    from bs4 import BeautifulSoup
    html = product_page(stock="OutOfStock")
    soup = BeautifulSoup(html, "lxml")
    script = soup.select_one('script[type="text/x-magento-init"]')
    config = json.loads(script.string)
    config["swatches"]["renderer"]["jsonConfig"]["attributes"] = []
    script.string = json.dumps(config)
    item = parse_planeta(str(soup), URL)[0]
    assert item.availability == "OUT_OF_STOCK"
    assert item.sizes == []
