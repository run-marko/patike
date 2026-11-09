import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from catalog.loader import upsert_entries  # noqa: E402
from common.config import default_config  # noqa: E402
from database.models import make_session_factory, utcnow  # noqa: E402
from matching.matcher import CatalogEntry, Matcher  # noqa: E402
from matching.sizes import SizeInfo  # noqa: E402
from pricing.store import store_listing  # noqa: E402
from retailers.base import ParsedListing  # noqa: E402

CATALOG = [
    ("ASICS", "Novablast 5", 93.0), ("ASICS", "Novablast 6", 93.0), ("Nike", "Vomero 18", 88.0),
    ("Nike", "Vomero Plus", 91.0), ("Adidas", "Adizero Boston 12", 90.0), ("Adidas", "Adizero Boston 13", 90.0),
    ("New Balance", "FuelCell Rebel v4", 89.0), ("New Balance", "FuelCell Rebel v5", 89.0),
    ("New Balance", "Fresh Foam X 1080 v14", 85.0), ("Hoka", "Mach X 3", 90.0), ("Hoka", "Mach 7", None),
    ("ASICS", "Gel Nimbus 27", 86.0), ("Nike", "Pegasus 41", 87.0), ("Nike", "Pegasus Plus", 88.0),
]


def _entries():
    out = []
    for brand, model, score in CATALOG:
        out.append({"brand": brand, "model_name": model, "surface": "road", "categories": ["daily_trainer"],
                    "review_score": score, "review_source": "RunRepeat" if score else None,
                    "source_url": f"https://runrepeat.com/{brand}-{model}".replace(" ", "-").lower()})
    return out


@pytest.fixture
def config():
    return default_config()


@pytest.fixture
def factory():
    session_factory = make_session_factory(":memory:")
    with session_factory() as session:
        upsert_entries(session, _entries())
    return session_factory


@pytest.fixture
def matcher():
    from catalog.runrepeat import slug_id
    return Matcher([CatalogEntry(slug_id(b, m), b, m) for b, m, _ in CATALOG])


def men_sizes(price, available=(45, 45.5, 46), unavailable=(42,)):
    sizes = [SizeInfo(label=str(s), eu=float(s), system="EU", available=True, price=price) for s in available]
    sizes += [SizeInfo(label=str(s), eu=float(s), system="EU", available=False) for s in unavailable]
    return sizes


def listing(retailer="sportvision", title="Muške patike ASICS Novablast 5", price=15000.0, brand="ASICS",
            sku="1011B974-001", sizes=None, url=None, **extra):
    availability = extra.pop("availability", "AVAILABLE")
    return ParsedListing(retailer=retailer, url=url or f"https://{retailer}.test/{title.replace(' ', '-')}-{sku}",
                         title=title, current_price=price, brand=brand, sku=sku,
                         sizes=men_sizes(price) if sizes is None else sizes, availability=availability, **extra)


@pytest.fixture
def save(factory, matcher):
    def _save(item: ParsedListing, when: datetime | None = None, scan_id=None):
        with factory() as session:
            result = store_listing(session, item, matcher, scan_id, when or utcnow())
            session.commit()
            return result
    return _save


def days_ago(days: int) -> datetime:
    return utcnow() - timedelta(days=days)
