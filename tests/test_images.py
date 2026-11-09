from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from analytics.engine import build_offers
from common.http import BlockedError
from database.models import PriceObservation, make_session_factory
from retailers.ananas import merge_marketplace_listings
from retailers.base import ParsedListing, RetailerAdapter, page_image
from scanner import fill_missing_images
from tests.conftest import listing

PAGE = '<html><head><meta property="og:image" content="https://shop.test/slika.jpg"></head></html>'


@pytest.mark.parametrize("html,expected", [
    (PAGE, "https://shop.test/slika.jpg"),
    ('<meta content="https://shop.test/b.jpg" property="og:image">', "https://shop.test/b.jpg"),
    ('<meta property="og:image" content="/files/c.jpg">', "https://shop.test/files/c.jpg"),
    ('<meta property="og:image" content="https://shop.test/UserFiles/products/big/">', None),
    ('<meta property="og:image" content="">', None),
    ('<meta name="description" content="bez slike">', None),
])
def test_page_image(html, expected):
    assert page_image(html, "https://shop.test/patike/1-a") == expected


class Client:
    def __init__(self, page=PAGE, blocked=False):
        self.page, self.blocked, self.requested = page, blocked, []

    def get(self, url):
        self.requested.append(url)
        if self.blocked:
            raise BlockedError("403")
        return SimpleNamespace(text=self.page, url=url, content=self.page.encode())


class Shop(RetailerAdapter):
    key = "sportvision"
    base_url = "https://sportvision.test"

    def all_urls(self):
        return ["https://sportvision.test/patike/1-a"]

    def parse_page(self, html, url):
        return [ParsedListing(retailer=self.key, url=url, title="Patika", current_price=1000.0)]


def test_scan_reads_image_from_product_page():
    result = Shop(client=Client()).run(lambda url: True)
    assert result.listings[0].image_url == "https://shop.test/slika.jpg"


def test_later_page_without_image_keeps_known_image(factory, save, config):
    save(listing(image_url="https://shop.test/slika.jpg"))
    save(listing(image_url=None))
    with factory() as session:
        assert build_offers(session, config)[0]["image_url"] == "https://shop.test/slika.jpg"


def test_fill_missing_images_adds_only_image_and_skips_known(factory, save, config):
    save(listing())
    save(listing(sku="X2", title="Muške patike ASICS Novablast 6", availability="OUT_OF_STOCK"))
    with factory() as session:
        before = session.scalar(select(func.count()).select_from(PriceObservation))
    client = Client()
    counts = fill_missing_images(factory, config, adapters=[Shop(client=client)])
    assert counts == {"sportvision": {"found": 1, "failed": 0}}
    assert len(client.requested) == 1  # rasprodata ponuda se ne otvara
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(PriceObservation)) == before
        offers = {o["model"]: o for o in build_offers(session, config)}
    assert offers["Novablast 5"]["image_url"] == "https://shop.test/slika.jpg"
    assert fill_missing_images(factory, config, adapters=[Shop(client=client)]) == {}
    assert len(client.requested) == 1


def test_fill_missing_images_stops_when_store_blocks(factory, save, config):
    for number, title in enumerate(["Novablast 5", "Novablast 6", "Vomero 18", "Vomero Plus", "Pegasus 41"]):
        save(listing(sku=f"S{number}", title=f"Muške patike {title}", brand="Nike" if "Nov" not in title else "ASICS"))
    client = Client(blocked=True)
    counts = fill_missing_images(factory, config, adapters=[Shop(client=client)])
    assert len(client.requested) == 3
    assert counts == {"sportvision": {"found": 0, "failed": 5}}


def test_marketplace_merge_keeps_an_image():
    first = ParsedListing(retailer="ananas", url="u1", title="T", current_price=10.0, listing_key="k")
    second = ParsedListing(retailer="ananas", url="u2", title="T", current_price=9.0, listing_key="k", image_url="i")
    assert merge_marketplace_listings([first, second])[0].image_url == "i"


def test_existing_database_gets_image_column_and_keeps_data(tmp_path):
    path = tmp_path / "stara.db"
    make_session_factory(path)
    import sqlite3
    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE listings DROP COLUMN image_url")
    conn.commit()
    conn.close()
    make_session_factory(path)
    conn = sqlite3.connect(path)
    assert "image_url" in {row[1] for row in conn.execute("PRAGMA table_info(listings)")}
    conn.close()
