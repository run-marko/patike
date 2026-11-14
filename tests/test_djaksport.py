from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from common.http import FetchError, PoliteClient
from database.models import PriceObservation, utcnow
from pricing.store import store_listing, CURRENT
from retailers.djaksport import DjakSportAdapter


URL = "https://www.djaksport.com/adidas-patike-adizero-evo-sl-m-za-muskarce-jh6206"
CATEGORY = DjakSportAdapter.running_category_urls[0]
FIXTURE = Path(__file__).parent / "fixtures" / "djak_evo_sl.html"


def test_real_djak_page_prices_fractional_sizes_and_taxonomy():
    adapter = DjakSportAdapter()
    item = adapter.parse_page(FIXTURE.read_text(), URL)[0]
    adapter._mark_running(item, True)
    assert item.retailer == "djaksport"
    assert item.sku == "JH6206"
    assert item.current_price == 13799 and item.old_price == 17599
    assert item.gender_attr == "MUŠKARCI"
    assert item.running is True
    assert item.purpose == "Patike za trčanje po asfaltu"
    assert item.availability == "AVAILABLE"
    assert [s.label for s in item.sizes if s.available] == [
        "40", "41 1/3", "42", "42 2/3", "43 1/3", "44", "44 2/3", "45 1/3"]
    assert not any(s.eu == 46 for s in item.sizes)  # size chart is not stock
    assert next(s for s in item.sizes if s.label == "45 1/3").eu == 45.333


def test_djak_listing_is_written_as_its_own_retailer(factory, matcher):
    item = DjakSportAdapter().parse_page(FIXTURE.read_text(), URL)[0]
    with factory() as session:
        store_listing(session, item, matcher, None, utcnow())
        session.commit()
        obs = session.scalars(select(PriceObservation).where(PriceObservation.price_type == CURRENT)).one()
        assert obs.retailer == "djaksport"
        assert obs.price == 13799
        assert obs.verification_status == "VERIFIED"
        assert len(obs.sizes) == 8


def adapter_for(handler, tmp_path):
    client = PoliteClient("https://www.djaksport.com", sleep=lambda _: None,
                          client=httpx.Client(transport=httpx.MockTransport(handler)))
    return DjakSportAdapter(client=client, cache_dir=tmp_path)


def test_nested_sitemap_discovers_products(tmp_path):
    def handler(request):
        if request.url.path == "/robots.txt":
            text = "User-agent: *"
        elif request.url.path == "/media/sitemap/djak.xml":
            text = '<sitemapindex><sitemap><loc>https://www.djaksport.com/media/sitemap/djak-1-1.xml</loc></sitemap></sitemapindex>'
        else:
            text = f'<urlset><url><loc>{URL}</loc></url></urlset>'
        return httpx.Response(200, text=text)

    assert adapter_for(handler, tmp_path).all_urls() == [URL]


def category_html(url, next_url=None):
    html = f'<div class="products"><strong class="product-item-name"><a href="{url}">Shoe</a></strong></div>'
    if next_url:
        html += f'<div class="pages"><a class="next" href="{next_url}">Naredni</a></div>'
    return html


def test_category_pagination(tmp_path):
    other = "https://www.djaksport.com/nike-patike-vomero-18"

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *")
        return httpx.Response(200, text=category_html(other) if request.url.query else category_html(URL, CATEGORY + "?p=2"))

    assert adapter_for(handler, tmp_path)._read_category(CATEGORY) == [URL, other]


def test_category_does_not_silently_accept_a_repeated_page(tmp_path):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *")
        return httpx.Response(200, text=category_html(URL, CATEGORY + "?p=2"))

    with pytest.raises(FetchError, match="ponovljena"):
        adapter_for(handler, tmp_path)._read_category(CATEGORY)


@pytest.mark.parametrize("status, body", [(403, "Forbidden"), (200, "Maintenance")])
def test_djak_block_or_invalid_sitemap_does_not_create_observations(tmp_path, status, body):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *")
        return httpx.Response(status, text=body)

    result = adapter_for(handler, tmp_path).run(lambda _: True)
    assert result.status == "NOT_VERIFIED" and not result.listings
