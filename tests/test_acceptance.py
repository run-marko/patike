"""Deset scenarija neuspeha iz specifikacije."""
import pytest

from analytics.deal import DealInput, evaluate_deal
from analytics.engine import build_offers, compare_model, rank_deals
from analytics.history import INSUFFICIENT
from common.http import BlockedError, FetchError
from matching.sizes import SizeInfo, classify_label, size_status
from retailers.ananas import merge_marketplace_listings
from retailers.base import ParsedListing, RetailerAdapter
from scanner import ScanFilters, run_scan
from tests.conftest import listing


def by_retailer(offers):
    return {o["retailer"]: o for o in offers}


# 1. Dve generacije sličnih naziva se ne spajaju
@pytest.mark.parametrize("title,brand,expected", [
    ("ASICS Novablast 5", "ASICS", "asics-novablast-5"),
    ("ASICS Novablast 6", "ASICS", "asics-novablast-6"),
    ("NIKE VOMERO 18", "Nike", "nike-vomero-18"),
    ("Nike Patike Vomero Plus", "Nike", "nike-vomero-plus"),
    ("PATIKE ADIDAS ADIZERO BOSTON 12 M", "Adidas", "adidas-adizero-boston-12"),
    ("Adidas Adizero Boston 13", "Adidas", "adidas-adizero-boston-13"),
    ("New Balance FuelCell Rebel v4", "New Balance", "new-balance-fuelcell-rebel-v4"),
    ("New Balance Rebel V5", "New Balance", "new-balance-fuelcell-rebel-v5"),
    ("NB 1080v14", "New Balance", "new-balance-fresh-foam-x-1080-v14"),
])
def test_1_generations_not_merged(matcher, title, brand, expected):
    result = matcher.match(brand, title)
    assert result.status == "MATCHED"
    assert result.model_id == expected


def test_1_generations_separate_in_comparison(factory, save, config):
    save(listing(retailer="a", title="Muške ASICS Novablast 5", sku="1011B974-001", price=15000))
    save(listing(retailer="b", title="Muške ASICS Novablast 6", sku="1011C000-001", price=21000))
    with factory() as session:
        offers = build_offers(session, config)
    assert {o["model_id"] for o in offers} == {"asics-novablast-5", "asics-novablast-6"}
    assert all(o["market_retailers"] == 0 for o in offers)


@pytest.mark.parametrize("title,brand", [
    ("Nike Vomero", "Nike"),              # bez broja generacije
    ("ASICS Novablast 7", "ASICS"),       # generacija koje nema u katalogu
    ("ASICS Novablast 5 Lite", "ASICS"),  # drugi model
    ("Nike Vomero 18 Premium", "Nike"),
])
def test_1_ambiguous_goes_to_manual_review(matcher, title, brand):
    result = matcher.match(brand, title)
    assert result.status == "AMBIGUOUS", result
    assert result.model_id is None


def test_men_and_women_not_merged(factory, save, config):
    save(listing(retailer="a", title="Muške ASICS Novablast 5", sku="1011B974-001", price=15000))
    save(listing(retailer="b", title="Ženske ASICS Novablast 5", sku="1012B765-001", price=11000))
    with factory() as session:
        offers = by_retailer(build_offers(session, config))
    assert offers["a"]["gender"] == "men" and offers["b"]["gender"] == "women"
    assert offers["a"]["group"] != offers["b"]["group"]
    assert offers["a"]["market_median"] is None


def test_gtx_is_not_compared_with_base_model(factory, save, config):
    save(listing(retailer="a", title="Muške ASICS Novablast 5", price=15000))
    save(listing(retailer="b", title="Muške ASICS Novablast 5 GTX", sku="1011X", price=19000))
    with factory() as session:
        offers = by_retailer(build_offers(session, config))
    assert offers["b"]["qualifiers"] == ["GTX"]
    assert offers["a"]["group"] != offers["b"]["group"]


# 2. Niska cena, a tražena veličina nije dostupna
def test_2_low_price_without_size_not_ranked(factory, save, config):
    cheap_sizes = [SizeInfo(label="41", eu=41.0, system="EU", available=True, price=9000),
                   SizeInfo(label="45", eu=45.0, system="EU", available=False)]
    save(listing(retailer="cheap", price=9000, sizes=cheap_sizes))
    save(listing(retailer="mid", price=14000, sku="1011B974-002"))
    save(listing(retailer="high", price=16000, sku="1011B974-003"))
    with factory() as session:
        offers = build_offers(session, config)
        from analytics.engine import latest_retailer_status
        comparison = compare_model(offers, latest_retailer_status(session), "asics-novablast-5", config)[0]
    ranked = rank_deals(offers, config)
    assert "cheap" not in {o["retailer"] for o in ranked}
    assert ranked[0]["retailer"] == "mid"
    # Najjeftinije posmatrano i najjeftinije kupljivo u tvojoj veličini su dve različite stvari.
    assert comparison["cheapest_observed"]["retailer"] == "cheap"
    assert comparison["cheapest_confirmed_size"]["retailer"] == "mid"


# 3. Loyalty cena se ne prikazuje kao javna
def test_3_loyalty_price_is_not_public(factory, save, config):
    save(listing(retailer="a", price=15000, loyalty_price=11000, loyalty_condition="uz karticu lojalnosti"))
    save(listing(retailer="b", price=14000, sku="1011B974-002"))
    with factory() as session:
        offers = by_retailer(build_offers(session, config))
    offer = offers["a"]
    assert offer["price"] == 15000 and offer["personal_price"] == 15000
    assert offer["loyalty_price"] == 11000 and "kartic" in offer["loyalty_condition"]
    assert offers["b"]["market_median"] == 15000  # poređenje vidi javnu cenu, ne loyalty


def test_3_parser_separates_loyalty():
    from bs4 import BeautifulSoup
    from retailers.nbshop import parse_conditional
    item = ParsedListing("x", "u", "t", 15000)
    html = '<div><div class="loyalty-info-wrapper">Cena sa loyalty karticom 11.000,00 RSD</div></div>'
    parse_conditional(BeautifulSoup(html, "lxml").div, item)
    assert item.loyalty_price == 11000
    assert item.current_price == 15000


# 4. SKU označava samo jednu boju
def test_4_sku_identifies_only_one_colorway(factory, save, config):
    save(listing(retailer="a", sku="1011B974-001", price=15000))
    save(listing(retailer="b", sku="1011B974-750", price=15500))
    with factory() as session:
        offers = by_retailer(build_offers(session, config))
    assert offers["a"]["variant_id"] != offers["b"]["variant_id"]
    assert offers["a"]["model_id"] == offers["b"]["model_id"]
    # Različite boje su odvojene ponude u okviru poređenja modela.
    assert offers["a"]["group"] == offers["b"]["group"]
    assert offers["a"]["market_median"] == 15500


# 5. Prodavac tvrdi 70%, a konkurencija je jeftinija
def test_5_seventy_percent_claim_but_competitors_cheaper(factory, save, config):
    save(listing(retailer="claims", price=15000, old_price=50000))
    save(listing(retailer="b", price=12000, sku="1011B974-002"))
    save(listing(retailer="c", price=12500, sku="1011B974-003"))
    with factory() as session:
        offers = by_retailer(build_offers(session, config))
    offer = offers["claims"]
    assert offer["claimed_discount"] == pytest.approx(0.7)
    assert offer["claim_status"] == "CONTRADICTED"
    assert offer["confirmed_savings"] is False
    assert offer["classification"] == "OVERPRICED"
    assert offer["components"]["retailer_claim"]["value"] == 0


def test_5_claim_alone_never_exceptional(config):
    result = evaluate_deal(DealInput(price=3000, old_price=10000, quality_score=95, size_status="CONFIRMED"),
                           config["scoring"])
    assert result.classification == "INSUFFICIENT DATA"


# 6. Model bez istorije
def test_6_no_history_is_insufficient(factory, save, config):
    save(listing(price=15000))
    with factory() as session:
        offer = build_offers(session, config)[0]
    history = offer["history"]
    assert history["status"] == INSUFFICIENT
    assert history["observations"] == 1
    assert history["previous"] is None and history["avg_30d"] is None and history["advantage"] is None
    assert offer["classification"] == "INSUFFICIENT DATA"


# 7. Jedan sajt nedostupan, ostali rade
class _Fake(RetailerAdapter):
    base_url = "https://fake.test"
    sitemap_urls = ["https://fake.test/s.xml"]

    def __init__(self, key, listings=None, error=None):
        self.key, self.name = key, key
        self._listings, self._error = listings or [], error

    def run(self, keep, max_pages=None, progress=None, broad_keep=None):
        from retailers.base import RetailerResult
        if self._error:
            raise self._error
        return RetailerResult(self.key, "VERIFIED", listings=self._listings, pages_ok=len(self._listings))


class _BlockedDiscover(RetailerAdapter):
    key = "blocked"
    name = "Blocked"
    base_url = "https://blocked.test"
    sitemap_urls = ["https://blocked.test/s.xml"]

    def __init__(self):
        pass

    def discover(self, keep):
        raise BlockedError("HTTP 403")


def test_7_one_retailer_down_others_continue(factory, config):
    good = _Fake("good", [listing(retailer="good", price=15000)])
    crash = _Fake("crash", error=RuntimeError("pad"))
    summary = run_scan(factory, config, ScanFilters(), adapters=[good, crash, _BlockedDiscover()])
    statuses = {r.retailer: r.status for r in summary.results}
    assert statuses == {"good": "VERIFIED", "crash": "NOT_VERIFIED", "blocked": "NOT_VERIFIED"}
    with factory() as session:
        from analytics.engine import latest_retailer_status
        offers = build_offers(session, config)
        comparison = compare_model(offers, latest_retailer_status(session), "asics-novablast-5", config)[0]
    assert [o["retailer"] for o in offers] == ["good"]
    # Neuspešan zahtev se prijavljuje kao nepoznato, a ne kao "nema proizvod".
    assert comparison["retailers_carrying"] == 1
    assert {"crash", "blocked"} <= set(comparison["not_verified"])


# 8. Marketplace sa više prodavaca
def test_8_marketplace_sellers_kept_separate(factory, save, config):
    def ad(seller, price, size):
        return ParsedListing(
            retailer="ananas", url=f"https://ananas.rs/proizvod/x/{seller}{size}",
            title="ASICS Muške patike Novablast 5", current_price=price, brand="ASICS", seller=seller,
            listing_key=f"ASICS Muške patike Novablast 5|{seller}", availability="AVAILABLE",
            sizes=[SizeInfo(label=str(size), eu=float(size), system="EU", available=True, price=price)])

    merged = merge_marketplace_listings([ad("Prodavac A", 15000, 45), ad("Prodavac A", 15000, 46),
                                         ad("Prodavac B", 14000, 45)])
    assert len(merged) == 2
    assert {m.seller: len(m.sizes) for m in merged} == {"Prodavac A": 2, "Prodavac B": 1}
    for item in merged:
        save(item)
    with factory() as session:
        offers = build_offers(session, config)
    assert {(o["seller"], o["price"]) for o in offers} == {("Prodavac A", 15000), ("Prodavac B", 14000)}
    # Dve ponude istog marketplace-a nisu dva nezavisna prodavca za tržišnu medijanu.
    assert all(o["market_retailers"] == 0 for o in offers)


# 9. US veličina, nejasna EU konverzija
@pytest.mark.parametrize("label,system", [("US 11", "US"), ("11", "UNKNOWN"), ("UK 10", "UK")])
def test_9_us_size_not_converted(label, system):
    assert classify_label(label) == (system, None)


def test_9_ambiguous_size_not_ranked(factory, save, config):
    sizes = [SizeInfo(label="US 11", system="US", available=True, price=12000)]
    save(listing(retailer="us", price=12000, sizes=sizes))
    save(listing(retailer="b", price=15000, sku="1011B974-002"))
    with factory() as session:
        offers = build_offers(session, config)
    us_offer = by_retailer(offers)["us"]
    assert us_offer["size_status"] == "AMBIGUOUS"
    assert "us" not in {o["retailer"] for o in rank_deals(offers, config)}


def test_9_fractional_adidas_is_flagged_approximate():
    sizes = [SizeInfo(label="45 1/3", eu=45.333, system="EU", available=True, price=1).to_dict()]
    status = size_status(sizes, [45, 45.5, 46], 0.2)
    assert status["status"] == "CONFIRMED" and status["approximate"] is True
    sizes = [SizeInfo(label="44 2/3", eu=44.667, system="EU", available=True, price=1).to_dict()]
    assert size_status(sizes, [45, 45.5, 46], 0.2)["status"] == "UNAVAILABLE"


# 10. Lažan ili neproveren popust se ne prikazuje kao potvrđena ušteda
def test_10_unverified_discount_not_confirmed(config):
    result = evaluate_deal(DealInput(price=10000, old_price=20000, market_median=10500, market_retailers=2),
                           config["scoring"])
    assert result.claim_status == "CLAIMED"
    assert result.confirmed_savings is False


def test_10_retailer_own_30d_minimum_contradicts_discount(config):
    result = evaluate_deal(DealInput(price=10000, old_price=20000, retailer_30d_min=9500), config["scoring"])
    assert result.claim_status == "CONTRADICTED"
    assert result.confirmed_savings is False


def test_10_supported_only_with_own_observation(config):
    result = evaluate_deal(DealInput(price=10000, old_price=20000, market_median=15000, market_retailers=2,
                                     observed_reference_seen=True), config["scoring"])
    assert result.claim_status == "SUPPORTED" and result.confirmed_savings is True
