from datetime import timedelta

import httpx
import pytest
import respx
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from analytics.alerts import evaluate_alerts, list_alerts
from analytics.engine import apply_msrp_rate, build_offers, rank_deals, resolve_models
from analytics.history import CHANGE_DAYS, Point, history_metrics, last_change
from catalog.loader import read_import_file, validate_entry
from catalog.runrepeat import build_entry, parse_listing_page, parse_model_page, split_brand
from common.http import BlockedError, FetchError, PoliteClient, RobotsDisallowed, RobotsRules, canonicalize_url
from database.models import CatalogModel, Listing, PriceObservation, utcnow
from matching.matcher import detect_gender, tokenize, url_is_other_gender
from retailers.ananas import parse_ananas
from retailers.base import parse_money
from retailers.intersport import IntersportAdapter
from retailers.nbshop import parse_nbshop
from retailers.nsport_platform import parse_nsport_platform
from tests.conftest import days_ago, listing, men_sizes


# --- HTTP ------------------------------------------------------------------
def test_robots_wildcards_and_longest_match():
    rules = RobotsRules.parse("""
User-agent: GPTBot
Disallow: /
User-agent: *
Disallow: /*?search=
Disallow: /*.php$
Allow: /*?p=
Crawl-delay: 2
Sitemap: https://x.test/s.xml
""")
    assert rules.allowed("https://x.test/patike/123-nike")
    assert not rules.allowed("https://x.test/proizvodi?search=nike")
    assert not rules.allowed("https://x.test/a.php")
    assert rules.allowed("https://x.test/a.php?x=1")
    assert rules.crawl_delay == 2
    assert rules.sitemaps == ["https://x.test/s.xml"]


def test_canonicalize_url():
    assert canonicalize_url("https://WWW.X.rs/a/?utm_source=f&id=1#top") == "https://www.x.rs/a?id=1"


def make_client(**kw):
    return PoliteClient("https://x.test", delay_s=0, sleep=lambda _: None, **kw)


@respx.mock
def test_client_retries_transient_with_backoff():
    respx.get("https://x.test/robots.txt").mock(return_value=httpx.Response(404))
    route = respx.get("https://x.test/p").mock(side_effect=[httpx.Response(503), httpx.Response(200, text="ok")])
    waits = []
    client = PoliteClient("https://x.test", delay_s=0, sleep=waits.append)
    assert client.get("https://x.test/p").text == "ok"
    assert route.call_count == 2
    assert 1 in waits


@respx.mock
def test_client_403_is_blocked_without_retry():
    respx.get("https://x.test/robots.txt").mock(return_value=httpx.Response(404))
    route = respx.get("https://x.test/p").mock(return_value=httpx.Response(403, text="blocked"))
    with pytest.raises(BlockedError):
        make_client().get("https://x.test/p")
    assert route.call_count == 1


@respx.mock
def test_client_respects_robots():
    respx.get("https://x.test/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow: /tajno"))
    with pytest.raises(RobotsDisallowed):
        make_client().get("https://x.test/tajno/1")


@respx.mock
def test_client_gives_up_after_retries():
    respx.get("https://x.test/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://x.test/p").mock(side_effect=httpx.ConnectTimeout("t"))
    with pytest.raises(FetchError):
        make_client(max_retries=1).get("https://x.test/p")


# --- Parseri (mock HTML po stvarnoj strukturi sajtova) -----------------------
LD = """<script type="application/ld+json">{"@context":"https://schema.org/","@type":"Product",
"name":"Nike NIKE VOMERO 18","description":"opis
sa prelomom","productID":"70154380","sku":"HM6803-301","brand":{"@type":"Brand","name":"Nike"},
"offers":{"@type":"Offer","priceCurrency":"RSD","price":"16799.21","availability":"https://schema.org/InStock",
"shippingDetails":{"shippingRate":{"@type":"MonetaryAmount","value":0,"currency":"RSD"}}}}</script>"""

THEME5 = "<html><head><meta name='description' content='Muške patike za trčanje'>" + LD + """</head><body>
<h1><span>Nike NIKE VOMERO 18</span></h1>
<div class="block product-details-price">
 <div class="product-oldprice"><span class="value">24.499,00</span></div>
 <div class="product-pricewithoutdiscount"><span class="value">20.999,00</span></div>
 <div class="product-price"><span class="value">16.799,21</span></div>
 <div class="loyalty-info-wrapper"> </div>
 <div class="product-lastminprice"><span class="value">18.000,00</span></div>
</div>
<div class="product-attributes-wrapper"><ul class="product-attributes">
 <li class="ease disabled"><span class="original-size">10</span><span class="eur-size">44</span><span class="cm-size">28</span></li>
 <li class="ease" data-productsize-price="16.799,21" data-productsize-oldprice="24.499,00" data-productsize-indicator-availability-label="Poslednji komadi"><span class="original-size">11</span><span class="eur-size">45</span><span class="cm-size">29</span></li>
</ul></div>
<div class="related"><span class="product-price-value">1.000,00</span></div></body></html>"""

THEME7 = "<html><head>" + LD + """</head><body><h1><span>Nike NIKE VOMERO 18</span></h1>
<div class="nb-product-prices-1 other"><div class="nb-price-sale"><span class="nbf-price-value">5.000,00</span></div></div>
<div class="block product-price-detail-2"><div class="nb-component nb-product-prices-1">
 <div class="nb-price nb-price-sale"><span class="nbf-price-value">16.799,21</span></div>
 <div class="nb-price nb-price-regular"><span class="nbf-price-value">20.999,00</span></div>
 <div class="nb-price nb-price-old hidden"><span class="nbf-price-value">20.999,00</span></div>
</div></div>
<div id="111"><div class="nbf-size-value" data-filter_eu_velue="45" data-productsize-price="1,00"></div></div>
<div id="70154380">
 <div class="nbf-size-value disabled" data-filter_eu_velue="44" data-filter_cm_value="28" data-productsize-price="16.799,21"></div>
 <div class="nbf-size-value" data-filter_eu_velue="45 1/3" data-filter_us_value="45 1/3" data-productsize-price="16.799,21" data-productsize-oldprice="20.999,00"></div>
</div></body></html>"""


def test_parse_money_formats():
    assert parse_money("16.799,21") == 16799.21
    assert parse_money("13.299 RSD") == 13299
    assert parse_money("12,999.00") == 12999
    assert parse_money("n/a") is None


def test_nbshop_theme5():
    item = parse_nbshop(THEME5, "https://x.test/p", "sportvision")[0]
    assert (item.current_price, item.old_price, item.regular_price, item.retailer_30d_min) == \
        (16799.21, 24499, 20999, 18000)
    assert item.sku == "HM6803-301" and item.availability == "AVAILABLE"
    assert item.loyalty_price is None
    disabled, ok = item.sizes
    assert disabled.available is False and disabled.eu == 44
    assert ok.available is True and ok.eu == 45 and ok.cm == "29" and ok.stock_note == "Poslednji komadi"


def test_nbshop_theme7_uses_main_product_only():
    item = parse_nbshop(THEME7, "https://x.test/p", "etsport")[0]
    assert item.current_price == 16799.21 and item.regular_price == 20999
    assert item.old_price == 20999  # sakrivena stara cena dolazi iz cene po veličini
    assert [s.label for s in item.sizes] == ["44", "45 1/3"]
    assert item.sizes[1].eu == pytest.approx(45.333)
    assert item.sizes[1].us is None  # sajt ponavlja EU u US polju; to nije konverzija


def test_nbshop_all_sizes_disabled_is_out_of_stock():
    html = THEME5.replace('class="ease"', 'class="ease disabled"')
    assert parse_nbshop(html, "u", "x")[0].availability == "OUT_OF_STOCK"


def test_nbshop_without_sizes_is_unknown_even_if_ld_says_in_stock():
    html = "<html><head>" + LD + "</head><body><h1>x</h1></body></html>"
    item = parse_nbshop(html, "u", "x")[0]
    assert item.availability == "UNKNOWN" and item.sizes == []


NSPORT = """<html><head><title>Muške patike</title></head><body><h1>Muške patike za trčanje Asics Gel-Nimbus 27</h1>
<script>{"item_brand":"Asics"}</script><div class="product_code">Šifra proizvoda: 1011B958-400</div>
<div class="short_description"> Boja:Plava </div>
<div class="product-price-wrap"><span class="product-price">13.299 RSD</span><span class="product-old-price">24.999 RSD</span></div>
SIZES <p>Cena isporuke je 399 RSD</p></body></html>"""


def test_nsport_platform_in_stock():
    sizes = '<ul class="size-list"><li><input class="fnc-product-cart-size" data-size="45" data-max-quantity="2"></li></ul>'
    item = parse_nsport_platform(NSPORT.replace("SIZES", sizes), "u", "intersport")[0]
    assert item.current_price == 13299 and item.old_price == 24999 and item.delivery_rsd == 399
    assert item.sku == "1011B958-400" and item.color == "Plava" and item.brand == "Asics"
    assert item.availability == "AVAILABLE" and item.sizes[0].eu == 45


def test_nsport_platform_out_of_stock():
    item = parse_nsport_platform(NSPORT.replace("SIZES", "<strong>Proizvod nije na lageru!</strong>"), "u", "x")[0]
    assert item.availability == "OUT_OF_STOCK" and item.sizes == []


ANANAS = r"""<html><body><a data-testid="product-merchant-title">ELEVENTRI SPORTS</a><script>self.__next_f.push([1,"
\"product\":{\"id\":\"\",\"name\":\"Hoka Muške patike za trčanje Mach 7, Plave\",\"groupId\":\"g1\",\"brand\":{\"id\":1,\"name\":\"Hoka\"},
\"specifications\":{\"attributes\":[{\"type\":\"ColorAttribute\",\"key\":\"Color\",\"trait\":\"Boja\",\"name\":\"Plava\"},
{\"type\":\"TextAttribute\",\"key\":\"Size\",\"trait\":\"Veličina\",\"content\":\"46 2/3\"},
{\"type\":\"TextAttribute\",\"key\":\"Gender\",\"trait\":\"Pol\",\"content\":\"Muški\"}]},
\"merchant\":{\"id\":5481,\"displayName\":\"ELEVENTRI SPORTS\"},\"priceV2\":{\"id\":1,\"basePrice\":18000,\"sellablePrice\":15573,\"lowestPrice30Days\":null},
\"shippingCost\":{\"price\":349},\"available\":2,\"x\":1"])</script></body></html>"""


def test_ananas_records_seller_and_size():
    item = parse_ananas(ANANAS, "https://ananas.rs/proizvod/x/1")[0]
    assert item.seller == "ELEVENTRI SPORTS"
    assert (item.current_price, item.old_price, item.retailer_30d_min) == (15573, 18000, None)
    assert item.gender_attr == "Muški" and item.color == "Plava" and item.delivery_rsd == 349
    assert item.sizes[0].label == "46 2/3" and item.sizes[0].available is True
    assert item.availability == "AVAILABLE"


@respx.mock
def test_adapter_discovers_from_sitemap_and_skips_failed_pages(tmp_path):
    respx.get("https://www.intersport.rs/robots.txt").mock(return_value=httpx.Response(200, text="User-agent: *\n"))
    respx.get("https://www.intersport.rs/UserFiles/sitemap.xml").mock(return_value=httpx.Response(200, text=(
        "<urlset><url><loc>https://www.intersport.rs/patike-za-trcanje/a.html</loc></url>"
        "<url><loc>https://www.intersport.rs/patike-za-trcanje/b.html</loc></url>"
        "<url><loc>https://www.intersport.rs/majice/c.html</loc></url></urlset>")))
    sizes = '<ul class="size-list"><li><input class="fnc-product-cart-size" data-size="45" data-max-quantity="2"></li></ul>'
    respx.get("https://www.intersport.rs/patike-za-trcanje/a.html").mock(
        return_value=httpx.Response(200, text=NSPORT.replace("SIZES", sizes)))
    respx.get("https://www.intersport.rs/patike-za-trcanje/b.html").mock(return_value=httpx.Response(404))
    adapter = IntersportAdapter(delay_s=0, max_retries=0, cache_dir=tmp_path)
    result = adapter.run(lambda url: True)
    assert result.status == "PARTIAL" and result.pages_ok == 1 and result.pages_failed == 1
    assert len(result.listings) == 1


# --- Uparivanje -------------------------------------------------------------
def test_kids_excluded(matcher):
    assert matcher.match("Hoka", "Hoka patike za decu Mach 7 Jr").status == "EXCLUDED"
    assert matcher.match("Adidas", "PATIKE ADIDAS ADIZERO BOSTON 13 EL I BT").status == "EXCLUDED"
    assert matcher.match("Adidas", "Adidas Duramo 10 J, Crne").status == "EXCLUDED"
    assert matcher.match("Adidas", "Adidas patike za trčanje i trening Foo").status != "EXCLUDED"


def test_word_before_model_changes_model(matcher):
    # Stvaran slučaj: "Duramo SL2" ne sme da postane "Adizero SL2" samo zato što se "adizero" skida kao prefiks.
    from catalog.runrepeat import slug_id
    from matching.matcher import CatalogEntry, Matcher
    local = Matcher([CatalogEntry(slug_id("Adidas", "Adizero SL2"), "Adidas", "Adizero SL2")])
    assert local.match("Adidas", "Adidas Muške patike Duramo SL2 M, Bele").status == "AMBIGUOUS"
    assert local.match("Adidas", "Adidas Muške patike Adizero SL2, Bele").status == "MATCHED"


@pytest.mark.parametrize("title", ["Patike Nike Vomero 18 Iron Hulk", "Nike Vomero 18 Woven"])
def test_unknown_title_extra_goes_to_review(matcher, title):
    assert matcher.match("Nike", title).status == "AMBIGUOUS"


def test_color_in_title_is_not_a_model_change(matcher):
    assert matcher.match("Hoka", "Hoka Muške patike za trčanje Mach 7, Plave").model_id == "hoka-mach-7"


def test_product_code_and_decimal_generation(matcher):
    assert matcher.match("Hoka", "Hoka Mach 7 HV9272 002").model_id == "hoka-mach-7"
    assert matcher.match("Hoka", "Hoka Mach 7.0").model_id == "hoka-mach-7"
    assert matcher.match("Hoka", "Hoka Mach 7.5").model_id != "hoka-mach-7"


def test_unisex_in_title():
    assert detect_gender("Unisex patike za trčanje Saucony Endorphin Elite 2")[0] == "unisex"


def test_nbshop_reads_gender_from_specification_table_not_menu():
    page = THEME5.replace('content=\'Muške patike za trčanje\'', 'content=\'\'').replace(
        "</body>", "<nav><p>Pol</p><a>Žene</a></nav><table><tr><td>Pol</td><td>Za muškarce</td></tr>"
                   "<tr><td>Uzrast</td><td>Za odrasle</td></tr><tr><td>Boja</td><td>Crna</td></tr></table></body>")
    item = parse_nbshop(page, "u", "x")[0]
    assert item.gender_attr == "Za muškarce" and item.color == "Crna"
    assert detect_gender("Nike Vomero 18", explicit=item.gender_attr)[0] == "men"
    kids = page.replace("Za odrasle", "Za decu")
    assert detect_gender("Nike Vomero 18", explicit=parse_nbshop(kids, "u", "x")[0].gender_attr)[0] == "kids"


def test_rematch_after_catalog_addition_keeps_observations(factory, save, config):
    from catalog.loader import upsert_entries
    from catalog.runrepeat import slug_id
    from matching.matcher import CatalogEntry, Matcher
    from pricing.store import rematch_all
    save(listing(title="Muške ASICS Novablast 7", price=15000))
    with factory() as session:
        assert build_offers(session, config) == []
        upsert_entries(session, [{"brand": "ASICS", "model_name": "Novablast 7", "source_url": "https://a.b/c"}])
        models = session.scalars(select(__import__("database.models", fromlist=["CatalogModel"]).CatalogModel))
        rematch_all(session, Matcher([CatalogEntry(slug_id(m.brand, m.model_name), m.brand, m.model_name)
                                      for m in models]))
        offers = build_offers(session, config)
        stored = session.scalars(select(PriceObservation.model_id)).all()
    assert [o["model_id"] for o in offers] == ["asics-novablast-7"]
    assert stored == [None]  # staro opažanje nije menjano


def test_cyrillic_tokens():
    assert tokenize("Мушке патике Новабласт 5")[:2] == ["muske", "patike"]


@pytest.mark.parametrize("args,expected", [
    (dict(title="Asics Novablast 6", sku="1012C021-500", brand="Asics"), "women"),
    (dict(title="Asics Novablast 6", sku="1011C021-500", brand="Asics"), "men"),
    (dict(title="Asics Novablast 6", sku="1014A396-700", brand="Asics"), "kids"),
    (dict(title="PATIKE ADIDAS ADIZERO PACER M"), "men"),
    (dict(title="PATIKE ADIDAS ADIZERO PACER W"), "women"),
    (dict(title="Nike Vomero 18", description="Muške patike za trčanje"), "men"),
    (dict(title="Nike Vomero 18"), "unknown"),
    (dict(title="x", explicit="Muški"), "men"),
])
def test_gender_detection(args, expected):
    assert detect_gender(**args)[0] == expected


def test_url_gender_filter():
    assert url_is_other_gender("https://ananas.rs/proizvod/hoka-zenske-patike-mach-7/123", "men")
    assert not url_is_other_gender("https://ananas.rs/proizvod/hoka-muske-patike-mach-7/123", "men")
    assert url_is_other_gender("https://www.etsport.rs/patike/1-patike-adidas-pacer-w", "men")


def test_unknown_gender_not_in_personal_ranking(factory, save, config):
    save(listing(retailer="a", title="Nike Vomero 18", brand="Nike", sku="X1", price=15000))
    save(listing(retailer="b", title="Muške Nike Vomero 18", brand="Nike", sku="X2", price=16000))
    with factory() as session:
        offers = build_offers(session, config)
    assert {o["retailer"]: o["gender"] for o in offers} == {"a": "unknown", "b": "men"}
    assert "a" not in {o["retailer"] for o in rank_deals(offers, config)}


def test_resolve_prefers_exact_model(factory):
    with factory() as session:
        assert [m.id for m in resolve_models(session, "ASICS Novablast 5")] == ["asics-novablast-5"]
        assert [m.id for m in resolve_models(session, "Novablast 5")] == ["asics-novablast-5"]
        assert len(resolve_models(session, "Novablast")) == 2


# --- Istorija i baza ---------------------------------------------------------
def test_history_metrics_with_enough_data():
    now = utcnow()
    points = [Point(now - timedelta(days=d), p) for d, p in [(40, 20000), (30, 20000), (20, 18000), (5, 18000),
                                                             (3, 16000), (0, 15000)]]
    cfg = {"min_observations_for_average": 3, "min_observations_for_advantage": 5, "min_span_days_for_advantage": 14}
    out = history_metrics(points, cfg, now)
    assert out["previous"] == 16000
    assert out["change_pct"] == pytest.approx(-0.0625)
    assert out["historical_min"] == 15000 and out["historical_min_date"] == now.date().isoformat()
    assert out["avg_7d"] == pytest.approx((18000 + 16000 + 15000) / 3)
    assert out["median"] == 18000
    assert out["advantage"] == pytest.approx((18000 - 15000) / 18000)


def test_history_short_span_has_no_advantage():
    now = utcnow()
    points = [Point(now - timedelta(days=d), 15000) for d in range(5)]
    cfg = {"min_observations_for_average": 3, "min_observations_for_advantage": 5, "min_span_days_for_advantage": 14}
    assert history_metrics(points, cfg, now)["advantage"] is None


@pytest.mark.parametrize("prices, kind", [
    ([100, 120, 90], "new_low"),   # ispod svih ranijih, ne samo prethodne
    ([100, 120, 100], "down"),     # isto kao ranija najniža nije nova najniža
    ([100, 120, 110], "down"),
    ([100, 90, 95], "up"),
])
def test_last_change_kind(prices, kind):
    now = utcnow()
    points = [Point(now - timedelta(days=len(prices) - 1 - i), p) for i, p in enumerate(prices)]
    change = last_change(points, now)
    assert change["kind"] == kind and change["from"] == prices[-2] and change["to"] == prices[-1]
    assert change["pct"] == pytest.approx(prices[-1] / prices[-2] - 1)
    assert change["tracked_since"] == points[0].at.isoformat()


def test_last_change_stays_after_later_scans_with_same_price():
    now = utcnow()
    points = [Point(now - timedelta(days=2), 100), Point(now - timedelta(days=1), 80), Point(now, 80)]
    change = last_change(points, now)
    assert change["kind"] == "new_low" and change["at"] == points[1].at.isoformat()


def test_last_change_expires_and_needs_a_real_change():
    now = utcnow()
    old = [Point(now - timedelta(days=CHANGE_DAYS + 2), 100), Point(now - timedelta(days=CHANGE_DAYS + 1), 80), Point(now, 80)]
    assert last_change(old, now) is None
    assert last_change([Point(now - timedelta(days=1), 100), Point(now, 100)], now) is None
    assert last_change([Point(now, 100)], now) is None


def test_observations_are_immutable(factory, save):
    save(listing())
    with factory() as session:
        with pytest.raises(DBAPIError):
            session.execute(text("UPDATE price_observations SET price = 1"))
        session.rollback()
        with pytest.raises(DBAPIError):
            session.execute(text("DELETE FROM price_observations"))
        session.rollback()


def test_rescan_adds_observation_instead_of_overwriting(factory, save, config):
    save(listing(price=16000), when=days_ago(2))
    save(listing(price=15000), when=days_ago(1))
    with factory() as session:
        prices = session.scalars(select(PriceObservation.price).where(PriceObservation.price_type == "CURRENT_PUBLIC")
                                 .order_by(PriceObservation.observed_at)).all()
        offer = build_offers(session, config)[0]
        assert session.scalar(select(text("count(*)")).select_from(Listing)) == 1
    assert prices == [16000, 15000]
    assert offer["history"]["previous"] == 16000 and offer["price"] == 15000


def test_stale_offer_flagged_and_excluded_from_market(factory, save, config):
    save(listing(retailer="old", price=9000), when=days_ago(10))
    save(listing(retailer="new", price=15000, sku="1011B974-002"))
    with factory() as session:
        offers = {o["retailer"]: o for o in build_offers(session, config)}
    assert offers["old"]["stale"] is True and offers["old"]["confidence"] == "low"
    assert offers["new"]["market_median"] is None


# --- Klasifikacija ----------------------------------------------------------
def test_exceptional_requires_real_evidence(factory, save, config):
    save(listing(retailer="a", price=10000))
    save(listing(retailer="b", price=16000, sku="1011B974-002"))
    save(listing(retailer="c", price=17000, sku="1011B974-003"))
    with factory() as session:
        offers = {o["retailer"]: o for o in build_offers(session, config)}
    assert offers["a"]["classification"] == "EXCEPTIONAL"
    assert offers["a"]["confidence"] == "medium"
    assert any("Tržište" in reason for reason in offers["a"]["reasons"])


def test_model_is_one_row_in_ranking_with_best_offer(factory, save, config):
    # Stvaran slučaj: Downshifter 13 u dve boje po različitoj ceni izgledao je kao duplikat sa dva skora.
    save(listing(retailer="a", price=12000, sku="1011B974-001"))
    save(listing(retailer="a", price=13000, sku="1011B974-002"))
    save(listing(retailer="b", price=16000, sku="1011B974-003"))
    with factory() as session:
        offers = build_offers(session, config)
    ranked = rank_deals(offers, config)
    assert len(offers) == 3 and len(ranked) == 1
    assert ranked[0]["price"] == 12000 and ranked[0]["other_offers"] == 2


def test_missing_review_score_not_invented_or_excluded(factory, save, config):
    save(listing(retailer="a", title="Muške Hoka Mach 7", brand="Hoka", sku="H1", price=12000))
    save(listing(retailer="b", title="Muške Hoka Mach 7", brand="Hoka", sku="H2", price=14000))
    save(listing(retailer="c", title="Muške Hoka Mach 7", brand="Hoka", sku="H3", price=15000))
    with factory() as session:
        offers = build_offers(session, config)
    ranked = rank_deals(offers, config)
    assert ranked and all(o["review_score"] is None for o in ranked)
    assert "quality" not in ranked[0]["components"]
    assert any("nezavisne ocene" in w for w in ranked[0]["warnings"])


def test_filters_budget_and_sizes(factory, save, config):
    save(listing(retailer="a", price=12000, sizes=men_sizes(12000, available=(45,))))
    save(listing(retailer="b", price=16000, sku="1011B974-002", sizes=men_sizes(16000, available=(44,))))
    with factory() as session:
        from analytics.engine import DealFilters, OfferOptions
        offers = build_offers(session, config, OfferOptions(sizes=[44]))
        assert {o["retailer"] for o in rank_deals(offers, config)} == {"b"}
        offers = build_offers(session, config)
        assert {o["retailer"] for o in rank_deals(offers, config, DealFilters(max_price=10000))} == set()


# --- Alarmi -----------------------------------------------------------------
def test_alerts_fire_once(factory, save, config):
    config["watchlist"] = [{"model": "ASICS Novablast 5", "type": "below_price", "target_rsd": 13000},
                           {"model": "ASICS Novablast 5", "type": "size_available"}]
    save(listing(price=12000))
    with factory() as session:
        offers = build_offers(session, config)
        assert len(evaluate_alerts(session, config, offers)) == 2
        assert evaluate_alerts(session, config, offers) == []
        assert len(list_alerts(session, unseen_only=True)) == 2
        assert list_alerts(session, unseen_only=True) == []


def test_alert_below_price_needs_confirmed_size(factory, save, config):
    config["watchlist"] = [{"model": "ASICS Novablast 5", "type": "below_price", "target_rsd": 13000}]
    save(listing(price=12000, sizes=men_sizes(12000, available=(41,))))
    with factory() as session:
        assert evaluate_alerts(session, config, build_offers(session, config)) == []


def test_alert_historical_min_and_drop_and_cheapest(factory, save, config):
    config["watchlist"] = [{"model": "ASICS Novablast 5", "type": "historical_min"},
                           {"model": "ASICS Novablast 5", "type": "pct_drop", "percent": 10},
                           {"model": "ASICS Novablast 5", "type": "cheapest_confirmed"}]
    for days, price in [(3, 18000), (2, 18000), (0, 15000)]:
        save(listing(retailer="a", price=price), when=days_ago(days))
    save(listing(retailer="b", price=17000, sku="1011B974-002"))
    with factory() as session:
        types = {a.rule_type for a in evaluate_alerts(session, config, build_offers(session, config))}
    assert types == {"historical_min", "pct_drop", "cheapest_confirmed"}


# --- Katalog ----------------------------------------------------------------
RR_LIST = """<ol><li class="product_list"><div class="product-name"><a href="/nike-vomero-plus"><span>Nike Vomero Plus</span></a></div>
<div class="corescore-big__score">91</div></li>
<li class="product_list"><div class="product-name"><a href="/new-balance-rebel"><span>New Balance FuelCell Rebel v5</span></a></div></li></ol>"""
RR_PAGE = """<html><body><table><tr><th></th><th>This</th><th>Other</th></tr>
<tr><td>Pace</td><td>Daily running Tempo</td><td>Competition</td></tr>
<tr><td>Shock absorption</td><td>High</td><td>Low</td></tr>
<tr><td>Energy return</td><td>Moderate</td><td>High</td></tr>
<tr><td>Midsole softness</td><td>Soft</td><td>Firm</td></tr>
<tr><td>Arch support</td><td>Neutral</td><td>Stability</td></tr>
<tr><td>Plate</td><td>-</td><td>Carbon plate</td></tr></table>
<div>Terrain:</div><div>Road</div></body></html>"""


def test_runrepeat_listing_no_invented_score():
    items = parse_listing_page(RR_LIST)
    assert items[0]["score"] == 91 and items[1]["score"] is None
    entry = build_entry(items[1], None, "2026-11-05T00:00:00")
    assert entry["review_score"] is None and entry["review_source"] is None
    assert entry["release_year"] is None and entry["generation"] == "5" and entry["brand"] == "New Balance"


def test_runrepeat_msrp_comes_only_from_own_column():
    table = '<table><tr><th>Price</th><td>{}</td><td>$180</td></tr></table>'
    assert parse_model_page(table.format("$150"))["msrp_usd"] == 150
    assert parse_model_page(table.format("-"))["msrp_usd"] is None
    assert build_entry({"name": "Asics Novablast 5", "url": "https://x.test/a", "score": 90},
                       {"msrp_usd": 150.0}, "2026-11-05T00:00:00")["msrp_usd"] == 150


def test_variant_does_not_borrow_base_model_msrp(factory, save, config):
    with factory() as session:
        session.get(CatalogModel, "asics-novablast-5").msrp_usd = 150.0
        session.commit()
    save(listing(sku="A1", url="https://a.test/1"))
    save(listing(sku="A2", url="https://a.test/2", title="Asics Novablast 5 GTX"))
    with factory() as session:
        prices = {o["sku"]: o["msrp_usd"] for o in build_offers(session, config)}
    assert prices == {"A1": 150.0, "A2": None}


def test_msrp_in_dinars_needs_both_price_and_rate():
    offers = [{"msrp_usd": 150.0}, {"msrp_usd": None}]
    apply_msrp_rate(offers, {"rate": 100.0, "date": "x"})
    assert [o["msrp_rsd"] for o in offers] == [15000, None]
    apply_msrp_rate(offers, None)
    assert offers[0]["msrp_rsd"] is None


def test_runrepeat_page_reads_only_own_column():
    out = parse_model_page(RR_PAGE)
    assert out["categories"] == ["daily_trainer", "tempo", "long_run", "recovery"]
    assert "stability" not in out["categories"] and "racing" not in out["categories"]
    assert out["plate"] is None  # "Carbon plate" pripada drugoj patici u tabeli
    assert out["surface"] == "road"


def test_split_brand():
    assert split_brand("361 Degrees Centauri") == ("361 Degrees", "Centauri")
    assert split_brand("On Cloudmonster 2") == ("On", "Cloudmonster 2")


def test_import_requires_source(tmp_path):
    assert validate_entry({"brand": "Nike", "model_name": "X"}) == ["nedostaje polje source_url"]
    assert validate_entry({"brand": "Nike", "model_name": "X", "source_url": "https://a.b", "review_score": 90})
    path = tmp_path / "m.csv"
    path.write_text("brand,model_name,source_url,categories,review_score\n"
                    "Nike,X,https://a.b,tempo;racing,\n", encoding="utf-8")
    row = read_import_file(path)[0]
    assert row["categories"] == ["tempo", "racing"] and row["review_score"] is None


def test_dashboard_escapes_and_builds(factory, save, config):
    from dashboard.build import build_html
    from reports.export import dashboard_data
    import json
    save(listing(title="Muške ASICS Novablast 5", price=15000, seller="</script><i>y"))
    with factory() as session:
        data = dashboard_data(session, config)
    html = build_html(json.dumps(data, default=str, ensure_ascii=False))
    assert "</script><i>" not in html and "/*__DATA__*/" not in html
    assert data["offers"][0]["in_personal_ranking"] is False  # nema drugih prodavaca: INSUFFICIENT DATA


def test_artifact_html_packs_data_and_has_no_page_wrapper():
    from dashboard.build import build_artifact_html
    import base64, gzip, json, re
    payload = json.dumps({"offers": [], "note": "</script>"})
    html = build_artifact_html(payload)
    assert html.startswith("<title>") and "<body" not in html and "<html" not in html
    assert "window.DEAL_STATIC=true" in html and html.rstrip().endswith("</script>")
    packed = re.search(r'atob\("([A-Za-z0-9+/=]+)"\)', html).group(1)
    assert json.loads(gzip.decompress(base64.b64decode(packed))) == json.loads(payload)


def test_thumbnail_sheets_index_tiles(tmp_path, monkeypatch):
    from PIL import Image
    from dashboard import thumbs
    monkeypatch.setattr(thumbs, "CACHE", tmp_path / "cache")
    thumbs.CACHE.mkdir()
    urls = [f"https://shop.test/{i}.jpg" for i in range(thumbs.GRID * thumbs.GRID + 3)]
    for u in urls:
        thumbs.to_tile(_png(Image, 300, 120)).save(thumbs.path_for(u), "WEBP", lossless=True)
    index = thumbs.build_sheets(urls + [None, "https://shop.test/missing.jpg"], tmp_path / "out")
    assert len(index) == len(urls) and "https://shop.test/missing.jpg" not in index
    sheets = sorted(p.name for p in (tmp_path / "out").glob("*.webp"))
    assert sheets == ["0.webp", "1.webp"]
    with Image.open(tmp_path / "out" / "1.webp") as last:
        assert last.size == (thumbs.GRID * thumbs.TILE, thumbs.TILE)  # 3 sličice, jedan red
    assert {tuple(v[:1]) for v in index.values()} == {(0,), (1,)}


def test_thumbnail_tile_is_square_and_keeps_whole_shoe():
    from PIL import Image
    from dashboard.thumbs import TILE, to_tile
    tile = to_tile(_png(Image, 400, 100, mode="RGBA"))
    assert tile.size == (TILE, TILE) and tile.mode == "RGB"
    assert tile.getpixel((0, 0)) == (255, 255, 255)  # prozirno i prazno postaje belo


def _png(Image, w, h, mode="RGB"):
    import io
    img = Image.new(mode, (w, h), (200, 30, 30) if mode == "RGB" else (200, 30, 30, 0))
    if mode == "RGBA":
        img.paste((200, 30, 30, 255), (w // 4, h // 4, w * 3 // 4, h * 3 // 4))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def test_page_encryption_hides_content(tmp_path):
    import os, shutil, subprocess
    from pathlib import Path
    node = shutil.which("node")
    if not node:
        import pytest
        pytest.skip("nema Node-a")
    src = tmp_path / "in.html"
    src.write_text("<h1>Nike Vaporfly 4 tajna cena 19.199</h1>", encoding="utf-8")
    script = Path(__file__).resolve().parent.parent / "cloud" / "encrypt_page.mjs"
    env = {**os.environ, "PAGE_PASSWORD": "kratka"}
    assert subprocess.run([node, str(script), str(src), str(tmp_path / "a.html")], env=env).returncode == 2
    env["PAGE_PASSWORD"] = "dovoljno-duga-lozinka"
    subprocess.run([node, str(script), str(src), str(tmp_path / "a.html")], env=env, check=True)
    subprocess.run([node, str(script), str(src), str(tmp_path / "b.html")], env=env, check=True)
    a, b = (tmp_path / "a.html").read_text(encoding="utf-8"), (tmp_path / "b.html").read_text(encoding="utf-8")
    assert "Vaporfly" not in a and "19.199" not in a and "dovoljno-duga-lozinka" not in a
    assert a != b  # nova so i IV svaki put


def test_cloud_state_roundtrip(tmp_path, monkeypatch):
    import sqlite3
    from cloud import state
    data = tmp_path / "data"
    (data / "thumbs").mkdir(parents=True)
    with sqlite3.connect(data / "shoes.db") as c:
        c.execute("PRAGMA journal_mode=WAL"); c.execute("CREATE TABLE t(x)"); c.execute("INSERT INTO t VALUES (7)")
    (data / "thumbs" / "a.webp").write_bytes(b"x")
    (data / "app.log").write_text("log")
    monkeypatch.setattr(state, "DATA", data)
    state.pack(tmp_path / "s.tar.gz")
    restored = tmp_path / "back"
    monkeypatch.setattr(state, "DATA", restored)
    state.unpack(tmp_path / "s.tar.gz")
    assert (restored / "thumbs" / "a.webp").read_bytes() == b"x" and not (restored / "app.log").exists()
    with sqlite3.connect(restored / "shoes.db") as c:
        assert c.execute("SELECT x FROM t").fetchone() == (7,)


def test_state_encryption_roundtrip_and_wrong_password(tmp_path):
    import os, shutil, subprocess
    from pathlib import Path
    node = shutil.which("node")
    if not node:
        import pytest
        pytest.skip("nema Node-a")
    script = str(Path(__file__).resolve().parent.parent / "cloud" / "crypt.mjs")
    src = tmp_path / "s.tar.gz"
    src.write_bytes(os.urandom(5000) + b"shoes.db")
    env = {**os.environ, "PAGE_PASSWORD": "dovoljno-duga-lozinka"}
    subprocess.run([node, script, "enc", str(src), str(tmp_path / "s.enc")], env=env, check=True)
    assert b"shoes.db" not in (tmp_path / "s.enc").read_bytes()
    subprocess.run([node, script, "dec", str(tmp_path / "s.enc"), str(tmp_path / "back")], env=env, check=True)
    assert (tmp_path / "back").read_bytes() == src.read_bytes()
    env["PAGE_PASSWORD"] = "neka-druga-lozinka"
    bad = subprocess.run([node, script, "dec", str(tmp_path / "s.enc"), str(tmp_path / "x")], env=env)
    assert bad.returncode == 4 and not (tmp_path / "x").exists()
