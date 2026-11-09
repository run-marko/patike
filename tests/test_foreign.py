import json
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import text

from analytics.engine import build_offers
from catalog.loader import upsert_entries
from catalog.runrepeat import slug_id
from common.http import PoliteClient, RateLimitedError, RobotsRules
from database.models import ForeignPrice, make_session_factory, utcnow
from pricing.store import store_listing
from foreign.scan import foreign_by_group, latest_foreign, run_foreign_scan, save_foreign, sold_in_serbia
from foreign.sources import parse_bergfreunde, parse_top4running
from retailers.base import ParsedListing, RetailerAdapter
from tests.conftest import _entries, listing

FX = {"rate": 104.0, "eur_rate": 117.0}


def ld(data):
    return f'<script type="application/ld+json">{json.dumps(data)}</script>'


def offer(price, status="InStock", url="https://t/p?size=45-eu-10-uk"):
    return {"offers": {"price": price, "url": url, "availability": f"http://schema.org/{status}"}}


def top4(shown="135", gender="Herren", variants=None):
    group = {"@type": "ProductGroup", "name": "Laufschuhe ASICS NOVABLAST 5", "productID": "1011b974-004",
             "brand": {"name": "ASICS"}, "color": "black",
             "hasVariant": variants if variants is not None else [
                 offer("150", "OutOfStock", "https://t/p?size=39-eu-5-uk"), offer(135),
                 offer(135, url="https://t/p?size=45-5-eu-10-uk"), offer(135, url="https://t/p?size=46-2-3-eu-11-uk")]}
    return (f'<meta property="og:price:amount" content="{shown}">{ld(group)}'
            f"<p>€150,00 €135,00 {gender}, Schwarz</p><nav>Herren Damen Kinder</nav>")


def test_top4_uses_price_of_sizes_in_stock():
    item = parse_top4running(top4(), "https://t/p")[0]
    assert (item.current_price, item.old_price, item.sku, item.gender_attr) == (135, 150, "1011B974-004", "men")
    assert item.title == "ASICS NOVABLAST 5"
    assert [s.label for s in item.sizes] == ["45", "45.5", "46 2/3"]
    assert item.availability == "AVAILABLE"


def test_top4_does_not_save_price_that_differs_from_shown_price():
    item = parse_top4running(top4(shown="120"), "https://t/p")[0]
    assert item.current_price is None and item.warnings


def test_top4_sold_out_has_no_price_and_non_shoe_is_ignored():
    item = parse_top4running(top4(variants=[offer("150", "OutOfStock")], shown="150"), "https://t/p")[0]
    assert item.current_price is None and item.availability == "OUT_OF_STOCK"
    assert parse_top4running(ld({"@type": "ProductGroup", "name": "Socken ASICS Run"}), "https://t/p") == []


def berg_variant(mpn, size, price, status="InStock", color="Black"):
    return {"mpn": mpn, "color": color, "size": {"name": size},
            "offers": {"price": price, "availability": f"https://schema.org/{status}"}}


def test_bergfreunde_keeps_colors_apart_and_ignores_sold_out_sizes():
    group = {"@type": "ProductGroup", "name": "Nike - Vomero 18 GTX - Running shoes", "brand": {"name": "Nike"},
             "audience": {"suggestedGender": "https://schema.org/Male"},
             "hasVariant": [berg_variant("HQ7001-600", "45", 163.75), berg_variant("HQ7001-600", "44,5", 179.95),
                            berg_variant("HQ7001-600", "46", 100, "OutOfStock"),
                            berg_variant("HQ7001-004", "45", 0, "OutOfStock", "Grey")]}
    items = {i.sku: i for i in parse_bergfreunde(ld(group), "https://b/x")}
    red, grey = items["HQ7001-600"], items["HQ7001-004"]
    assert (red.current_price, red.gender_attr, red.title) == (163.75, "men", "Nike Vomero 18 GTX")
    assert sorted(s.label for s in red.sizes) == ["44.5", "45"]
    assert grey.current_price is None and grey.availability == "OUT_OF_STOCK"


def test_bergfreunde_removes_size_from_code_and_reads_women():
    group = {"@type": "ProductGroup", "name": "HOKA - Clifton 10 - Running shoes", "brand": {"name": "HOKA"},
             "audience": {"suggestedGender": "https://schema.org/Female"},
             "hasVariant": [berg_variant("1162030-SGNN-10D", "45", 120)]}
    item = parse_bergfreunde(ld(group), "https://b/x")[0]
    assert item.sku == "1162030-SGNN" and item.gender_attr == "women"


def foreign_item(title="ASICS Novablast 5", sku="OTHER-1", gender="men", price=100.0, source="top4running", brand="ASICS"):
    return ParsedListing(retailer=source, url=f"https://{source}/{sku}", title=title, current_price=price, brand=brand,
                         sku=sku, gender_attr=gender, availability="AVAILABLE" if price else "OUT_OF_STOCK")


def stored(factory, matcher, *items, when=None):
    with factory() as session:
        saved = [save_foreign(session, item, matcher, when or utcnow()) for item in items]
        session.commit()
    return saved


def groups_for(factory, config):
    with factory() as session:
        offers = build_offers(session, config)
        return offers, foreign_by_group(latest_foreign(session), offers, FX)


def test_model_gender_and_generation_are_never_mixed(factory, save, config, matcher):
    save(listing())  # muški Novablast 5
    stored(factory, matcher, foreign_item(sku="A-1", price=100.0),
           foreign_item(title="ASICS Novablast 6", sku="A-2", price=60.0),
           foreign_item(sku="A-3", gender="unisex", price=50.0),
           foreign_item(sku="A-4", gender="unknown", price=40.0))
    offers, foreign = groups_for(factory, config)
    rows = foreign[offers[0]["group"]]
    assert [r["sku"] for r in rows] == ["A-1"]
    assert rows[0]["price_rsd"] == 11700


def test_women_link_only_to_women_and_kids_are_not_saved(factory, save, config, matcher):
    save(listing())
    save(listing(title="Ženske patike ASICS Novablast 5", sku="W1", gender_attr="women"))
    assert stored(factory, matcher, foreign_item(sku="F-W", gender="women"), foreign_item(sku="F-K", gender="kids")) == [True, False]
    offers, foreign = groups_for(factory, config)
    by_gender = {o["gender"]: o["group"] for o in offers}
    assert [r["sku"] for r in foreign[by_gender["women"]]] == ["F-W"]
    assert by_gender["men"] not in foreign


def test_gtx_is_not_compared_with_plain_model(factory, save, config, matcher):
    save(listing())
    stored(factory, matcher, foreign_item(title="ASICS Novablast 5 GTX", sku="G-1"))
    offers, foreign = groups_for(factory, config)
    assert offers[0]["group"] not in foreign


def test_same_manufacturer_code_links_even_with_other_title(factory, save, config, matcher):
    save(listing(sku="1011B974-001"))
    stored(factory, matcher, foreign_item(title="ASICS Neki Drugi Naziv", sku="1011B974-001"),
           foreign_item(title="ASICS Treci Naziv", sku="1011B974-001-7", source="bergfreunde"))
    offers, foreign = groups_for(factory, config)
    assert {r["source"] for r in foreign[offers[0]["group"]]} == {"top4running", "bergfreunde"}


def test_foreign_price_does_not_change_serbian_market_or_score(factory, save, config, matcher):
    save(listing())
    before = groups_for(factory, config)[0][0]
    stored(factory, matcher, foreign_item(price=1.0))
    after = groups_for(factory, config)[0][0]
    for key in ("market_median", "market_advantage", "score", "classification"):
        assert before[key] == after[key]


def test_old_and_sold_out_prices_are_not_shown(factory, save, config, matcher):
    save(listing())
    stored(factory, matcher, foreign_item(sku="OLD"), when=utcnow() - timedelta(days=8))
    stored(factory, matcher, foreign_item(sku="GONE"), when=utcnow() - timedelta(days=2))
    stored(factory, matcher, foreign_item(sku="GONE", price=None), when=utcnow())
    assert groups_for(factory, config)[1] == {}


def test_without_euro_rate_only_rsd_is_missing(factory, save, config, matcher):
    save(listing())
    stored(factory, matcher, foreign_item())
    with factory() as session:
        offers = build_offers(session, config)
        rows = foreign_by_group(latest_foreign(session), offers, {"rate": 104.0})
    entry = rows[offers[0]["group"]][0]
    assert entry["price_eur"] == 100.0 and entry["price_rsd"] is None


def limited_client(*statuses, headers=None):
    answers = iter(statuses)
    pauses = []

    def handler(request):
        return httpx.Response(next(answers), text="x" * 40000, headers=headers or {})

    client = PoliteClient("https://x.test", delay_s=2, max_retries=3, sleep=pauses.append,
                          client=httpx.Client(transport=httpx.MockTransport(handler)))
    client._robots = RobotsRules()
    return client, pauses


def test_429_waits_as_told_then_continues_slower():
    client, pauses = limited_client(429, 200, headers={"retry-after": "120"})
    assert client.get("https://x.test/p").status_code == 200
    assert 120 in pauses and client.delay_s == 4


def test_429_twice_stops_instead_of_retrying():
    client, _ = limited_client(429, 429)  # treći odgovor ne postoji: treći pokušaj bi pao sa StopIteration
    with pytest.raises(RateLimitedError):
        client.get("https://x.test/p")
    assert client.delay_s == 8


class Limited:
    def __init__(self, fail_from):
        self.fail_from, self.requested = fail_from, []

    def get(self, url):
        self.requested.append(url)
        if len(self.requested) >= self.fail_from:
            raise RateLimitedError("HTTP 429")
        return SimpleNamespace(text=top4(), url=url, content=b"")


class Shop(RetailerAdapter):
    key = "top4running"
    base_url = "https://t.test"

    def all_urls(self):
        return [f"https://t.test/p/asics-novablast-5-{n}" for n in range(1, 6)]

    def parse_page(self, html, url):
        return parse_top4running(html, url, self.key)


def test_rate_limit_keeps_read_pages_and_next_run_continues(tmp_path, config, matcher):
    factory = make_session_factory(tmp_path / "t.db")  # sken radi u nitima, a baza u memoriji je po niti
    with factory() as session:
        upsert_entries(session, _entries())
        store_listing(session, listing(), matcher, None, utcnow())
        session.commit()
    first = Limited(fail_from=3)
    (result, saved), = run_foreign_scan(factory, config, adapters=[Shop(client=first)])
    assert len(first.requested) == 3  # staje na prvoj stranici sa 429, ostale ne pokušava
    assert result.status == "PARTIAL" and "usporimo" in result.message and saved == 2
    second = Limited(fail_from=99)
    (result, saved), = run_foreign_scan(factory, config, adapters=[Shop(client=second)])
    assert second.requested == [f"https://t.test/p/asics-novablast-5-{n}" for n in (3, 4, 5)]
    assert result.status == "VERIFIED" and saved == 3


def test_only_models_on_stock_in_serbia_are_opened(factory, save, config):
    save(listing())
    save(listing(title="Muške patike ASICS Novablast 6", sku="X6", availability="OUT_OF_STOCK"))
    with factory() as session:
        assert {e.model_name for e in sold_in_serbia(session, config)} == {"Novablast 5"}


@pytest.mark.parametrize("statement", ["UPDATE foreign_prices SET price = 1", "DELETE FROM foreign_prices"])
def test_foreign_history_cannot_be_changed(factory, matcher, statement):
    stored(factory, matcher, foreign_item())
    with factory() as session:
        with pytest.raises(Exception, match="nepromenljiva"):
            session.execute(text(statement))
        assert session.get(ForeignPrice, 1).model_id == slug_id("ASICS", "Novablast 5")
