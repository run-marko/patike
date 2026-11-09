"""Pokrivenost: sken ne sme da zavisi od kataloga, a model van kataloga ne sme da nestane ni da se spoji sa drugim."""
from types import SimpleNamespace

import pytest

from analytics.engine import DealFilters, build_offers, rank_deals, store_links
from common.http import NotFoundError
from matching.matcher import tokenize
from matching.sizes import SizeInfo, normalize_size_label
from retailers.base import ParsedListing, RetailerAdapter
from scanner import recently_out_of_stock
from tests.conftest import days_ago, listing


def woven(retailer="etsport", sku="JH1", price=12000.0, **extra):
    return listing(retailer=retailer, title="PATIKE ADIDAS ADIZERO EVO SL WOVEN M", brand="Adidas", sku=sku,
                   price=price, running=True, **extra)


def test_uncataloged_running_model_is_visible_without_invented_score(factory, save, config):
    save(woven())
    with factory() as session:
        offers = build_offers(session, config)
    assert [o["model"] for o in offers] == ["Adizero EVO SL Woven"] or offers[0]["model"] == "EVO SL Woven"
    offer = offers[0]
    assert offer["catalog_status"] == "OBSERVED"
    assert offer["review_score"] is None and offer["cushioning"] is None and offer["categories"] == []
    assert any("nije u nezavisnom katalogu" in w for w in offer["warnings"])


def test_uncataloged_listing_not_marked_running_stays_hidden(factory, save, config):
    save(listing(title="Nike Air Force 1 Fly Knit", brand="Nike", sku="X1", running=False))
    with factory() as session:
        assert build_offers(session, config) == []


def test_other_store_running_label_proves_same_name_on_marketplace(factory, save, config):
    save(listing(retailer="etsport", title="Adidas Response Runner 2", brand="Adidas", sku="A1", running=True))
    save(listing(retailer="ananas", title="Adidas Response Runner 2", brand="Adidas", sku=None, running=False))
    with factory() as session:
        offers = build_offers(session, config)
    assert {o["retailer"] for o in offers} == {"etsport", "ananas"} and len({o["group"] for o in offers}) == 1


def test_full_scan_url_filter_recognizes_previously_observed_model(factory, save):
    from matching.matcher import Matcher
    from scanner import observed_running_entries
    save(listing(title="Adidas Response Runner 2", brand="Adidas", sku="A1", running=True))
    with factory() as session:
        entries = observed_running_entries(session)
    assert Matcher(entries).candidates_in_slug("https://ananas.rs/proizvod/adidas-muske-response-runner-2-crne/1")


def test_generations_of_uncataloged_models_are_not_merged(factory, save, config):
    save(listing(title="Adidas Response Runner 2", brand="Adidas", sku="A1", running=True))
    save(listing(title="Adidas Response Runner 3", brand="Adidas", sku="A2", running=True))
    with factory() as session:
        assert len({o["model_id"] for o in build_offers(session, config)}) == 2


def test_same_sku_joins_truncated_title_and_proves_identity(factory, save, config):
    save(woven("etsport", sku="JH1", price=12000.0))
    save(listing(retailer="nsport", title="Muške patike adidas Adizero evo sl wove", brand="Adidas", sku="JH1",
                 price=15000.0, running=True))
    with factory() as session:
        offers = build_offers(session, config)
    assert len({o["group"] for o in offers}) == 1
    best = min(offers, key=lambda o: o["price"])
    assert best["market_retailers"] == 1 and best["confidence"] == "medium"


def test_same_sku_as_catalog_listing_uses_catalog_model(factory, save, config):
    save(listing(sku="1011B974-001"))
    save(listing(retailer="nsport", title="Patike Asics NVBLST5", sku="1011B974-001", running=True))
    with factory() as session:
        offers = build_offers(session, config)
    assert {o["model_id"] for o in offers} == {"asics-novablast-5"}
    assert len({o["group"] for o in offers}) == 1


def test_model_without_my_size_is_offered_but_not_ranked(factory, save, config):
    save(listing(sizes=[]))
    with factory() as session:
        offers = build_offers(session, config)
    assert len(offers) == 1 and rank_deals(offers, config) == []


def test_ranked_model_links_to_every_store_that_has_it(factory, save, config):
    save(listing(retailer="sportvision", price=11000.0, url="https://a.test/sv"))
    save(listing(retailer="etsport", price=12000.0, url="https://a.test/et"))
    save(listing(retailer="buzz", price=9000.0, url="https://a.test/bz", sizes=[]))
    save(listing(retailer="runnmore", price=8000.0, url="https://a.test/rm", availability="OUT_OF_STOCK", sizes=[]))
    with factory() as session:
        offers = build_offers(session, config)
    best = rank_deals(offers, config, DealFilters(include_insufficient=True))[0]
    # Rasprodata prodavnica nije u listi, a skorom najbolja nije duplirana.
    others = [s["retailer"] for s in best["other_stores"]]
    assert best["retailer"] not in others and "runnmore" not in others
    assert set(others) | {best["retailer"]} == {"sportvision", "etsport", "buzz"}


def test_size_filter_off_ranks_other_sizes_and_score_ignores_size(factory, save, config):
    small = [SizeInfo(label="41", eu=41.0, system="EU", available=True),
             SizeInfo(label="45", eu=45.0, system="EU", available=False)]
    save(listing(retailer="sportvision", price=11000.0, sizes=small))
    save(listing(retailer="etsport", price=11000.0))
    config["filter_preferred_size"] = False
    with factory() as session:
        offers = build_offers(session, config)
    by_store = {o["retailer"]: o for o in offers}
    assert by_store["sportvision"]["score"] == by_store["etsport"]["score"]
    ranked = rank_deals(offers, config, DealFilters(include_insufficient=True))
    assert len(ranked) == 1
    links = {s["retailer"]: s["sizes"] for s in store_links(offers)[ranked[0]["group"]]}
    assert links["sportvision"] == ["41"]


@pytest.mark.parametrize("order", [("plain", "gtx"), ("gtx", "plain")])
def test_uncataloged_gtx_and_plain_stay_separate_variants(factory, save, config, order):
    titles = {"plain": "Muške patike Adidas Supernova ease 2 m", "gtx": "Muške patike Adidas Supernova ease 2 gtx m"}
    for key in order:
        save(listing(retailer="intersport", title=titles[key], brand="Adidas", sku=key.upper(), running=True))
    with factory() as session:
        offers = {o["sku"]: o for o in build_offers(session, config)}
    assert offers["PLAIN"]["qualifiers"] == []
    assert offers["GTX"]["qualifiers"] == ["GTX"]
    assert offers["PLAIN"]["group"] != offers["GTX"]["group"]


def test_truncated_title_joins_full_name_but_short_title_does_not(factory, save, config):
    save(woven("etsport", sku="K1"))
    save(listing(retailer="nsport", title="Muške patike adidas Adizero evo sl wove", brand="Adidas", sku="K2",
                 running=True))
    save(listing(retailer="nsport", title="Adidas Pace", brand="Adidas", sku="K3", running=True))
    save(listing(retailer="etsport", title="Adidas Pacer", brand="Adidas", sku="K4", running=True))
    with factory() as session:
        groups = {o["sku"]: o["group"] for o in build_offers(session, config)}
    assert groups["K1"] == groups["K2"] and groups["K3"] != groups["K4"]


def test_store_labeled_lifestyle_line_is_not_a_running_model(factory, save, config):
    save(listing(retailer="nsport", title="Muške patike za trčanje Nike Dunk low retro se", brand="Nike", sku="D1",
                 running=True))
    with factory() as session:
        assert build_offers(session, config) == []


@pytest.mark.parametrize("title,tokens", [("Hoka Arahi9", ["hoka", "arahi", "9"]),
                                          ("Hoka Clifton11gtx", ["hoka", "clifton", "11", "gtx"]),
                                          ("Patikepuma Trinity 2", ["puma", "trinity", "2"]),
                                          ("Adidas Run50s", ["adidas", "run50s"])])
def test_glued_titles_are_split(title, tokens):
    assert tokenize(title) == tokens


@pytest.mark.parametrize("raw,clean", [("451/3", "45 1/3"), ("41-1/3", "41 1/3"), ("45 1/3", "45 1/3"), ("‚44", "44"),
                                       ("45,5", "45.5"), ("45.5", "45.5"), ("US 11", "US 11")])
def test_differently_written_sizes_become_one_label(raw, clean):
    assert normalize_size_label(raw) == clean


class FakeClient:
    def __init__(self, pages):
        self.pages, self.requested = pages, []

    def get(self, url):
        self.requested.append(url)
        if url not in self.pages:
            raise NotFoundError(url)
        return SimpleNamespace(text=self.pages[url], url=url, content=self.pages[url].encode())


class CategoryAdapter(RetailerAdapter):
    key = "kat"
    base_url = "https://kat.test"
    running_category_urls = ["https://kat.test/patike/trcanje"]
    product_link_pattern = r'href="([^"]*/patike/\d+-[^"]+)"'

    def all_urls(self):
        return ["https://kat.test/patike-za-trcanje/9-sitemap", "https://kat.test/patike/8-lifestyle"]

    def parse_page(self, html, url):
        return [ParsedListing(retailer="kat", url=url, title="Patika", current_price=1000.0, purpose=None)]


def make_category_adapter():
    client = FakeClient({
        "https://kat.test/patike/trcanje": '<a href="/patike/1-a">a</a><a href="/patike/2-b">b</a>',
        "https://kat.test/patike/trcanje/page-2": '<a href="/patike/3-c">c</a>',
        "https://kat.test/patike/trcanje/page-3": '<a href="/patike/3-c">c</a>',
        **{f"https://kat.test/patike/{n}": "x" for n in ("1-a", "2-b", "3-c", "8-lifestyle")},
        "https://kat.test/patike-za-trcanje/9-sitemap": "x",
    })
    return CategoryAdapter(client=client), client


def test_full_scan_opens_running_pages_the_catalog_does_not_know():
    adapter, client = make_category_adapter()
    result = adapter.run(lambda url: False, broad_keep=lambda url: True)
    assert {x.url for x in result.listings} == {f"https://kat.test/patike/{n}" for n in ("1-a", "2-b", "3-c")} | {
        "https://kat.test/patike-za-trcanje/9-sitemap"}
    assert all(x.running for x in result.listings)
    assert "https://kat.test/patike/8-lifestyle" not in client.requested


def test_filtered_scan_does_not_read_categories():
    adapter, client = make_category_adapter()
    assert adapter.run(lambda url: False).listings == []
    assert client.requested == []


def test_failed_category_is_reported_as_partial_not_verified():
    adapter, client = make_category_adapter()
    from common.http import FetchError

    def failing(url):
        if "trcanje" in url and "za-trcanje" not in url:
            raise FetchError("HTTP 500")
        return SimpleNamespace(text="x", url=url)
    client.get = failing
    result = adapter.run(lambda url: False, broad_keep=lambda url: True)
    assert result.status == "PARTIAL" and "kategorija" in result.message


def test_explicit_purpose_beats_running_category():
    item = ParsedListing(retailer="k", url="https://k.test/patike/1", title="Patika", current_price=1.0,
                         purpose="Fudbal")
    RetailerAdapter._mark_running(item, True)
    assert item.running is False


def test_recently_sold_out_is_rested_then_rechecked(factory, save):
    fresh = listing(sku="F1", sizes=[], availability="OUT_OF_STOCK", url="https://a.test/fresh")
    old = listing(sku="F2", sizes=[], availability="OUT_OF_STOCK", url="https://a.test/old")
    save(fresh)
    save(old, when=days_ago(9))
    with factory() as session:
        assert recently_out_of_stock(session, 7) == {"https://a.test/fresh"}
        assert recently_out_of_stock(session, 0) == set()
