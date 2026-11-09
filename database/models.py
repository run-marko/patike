"""SQLite šema. Tri nivoa identiteta su odvojena: model -> varijanta (SKU/boja) -> oglas prodavca.

Tabela price_observations je nepromenljiva: UPDATE i DELETE blokiraju SQLite trigeri.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text,
                        create_engine, event, text)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class CatalogModel(Base):
    __tablename__ = "catalog_models"
    id: Mapped[str] = mapped_column(String, primary_key=True)  # npr. asics-novablast-5
    brand: Mapped[str] = mapped_column(String, index=True)
    model_name: Mapped[str] = mapped_column(String)  # bez brenda, npr. "Novablast 5"
    generation: Mapped[str | None] = mapped_column(String, nullable=True)
    genders: Mapped[str | None] = mapped_column(String, nullable=True)
    surface: Mapped[str | None] = mapped_column(String, nullable=True)
    categories: Mapped[list] = mapped_column(JSON, default=list)
    category_basis: Mapped[str | None] = mapped_column(Text, nullable=True)
    cushioning: Mapped[str | None] = mapped_column(Text, nullable=True)
    midsole_foam: Mapped[str | None] = mapped_column(String, nullable=True)
    plate: Mapped[str | None] = mapped_column(String, nullable=True)
    msrp_usd: Mapped[float | None] = mapped_column(Float, nullable=True)  # američka preporučena cena
    review_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    review_source: Mapped[str | None] = mapped_column(String, nullable=True)
    review_url: Mapped[str | None] = mapped_column(String, nullable=True)
    release_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_url: Mapped[str] = mapped_column(String)
    last_verified_at: Mapped[datetime] = mapped_column(DateTime)


class Variant(Base):
    __tablename__ = "variants"
    id: Mapped[str] = mapped_column(String, primary_key=True)  # brand:SKU ili listing-specifičan id
    model_id: Mapped[str | None] = mapped_column(ForeignKey("catalog_models.id"), nullable=True, index=True)
    sku: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    color: Mapped[str | None] = mapped_column(String, nullable=True)
    gender: Mapped[str | None] = mapped_column(String, nullable=True)


class Listing(Base):
    __tablename__ = "listings"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    retailer: Mapped[str] = mapped_column(String, index=True)
    url: Mapped[str] = mapped_column(String)
    title: Mapped[str] = mapped_column(String)
    seller: Mapped[str | None] = mapped_column(String, nullable=True)
    # Sirovi podaci prodavca, potrebni za ponovno uparivanje bez novih zahteva (python main.py rematch).
    brand: Mapped[str | None] = mapped_column(String, nullable=True)
    sku: Mapped[str | None] = mapped_column(String, nullable=True)
    color: Mapped[str | None] = mapped_column(String, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    gender_attr: Mapped[str | None] = mapped_column(String, nullable=True)
    # Da li prodavac oglas vodi kao patiku za trčanje. Oglas van kataloga se prikazuje samo ako je True.
    running: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    image_url: Mapped[str | None] = mapped_column(String, nullable=True)  # slika ostaje na sajtu prodavnice
    variant_id: Mapped[str | None] = mapped_column(ForeignKey("variants.id"), nullable=True)
    model_id: Mapped[str | None] = mapped_column(ForeignKey("catalog_models.id"), nullable=True, index=True)
    gender: Mapped[str | None] = mapped_column(String, nullable=True)
    qualifiers: Mapped[list] = mapped_column(JSON, default=list)
    match_status: Mapped[str] = mapped_column(String)  # MATCHED | AMBIGUOUS | NO_MATCH | EXCLUDED
    match_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime)
    last_seen: Mapped[datetime] = mapped_column(DateTime)


class PriceObservation(Base):
    __tablename__ = "price_observations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scan_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    listing_id: Mapped[str] = mapped_column(ForeignKey("listings.id"), index=True)
    model_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    variant_id: Mapped[str | None] = mapped_column(String, nullable=True)
    retailer: Mapped[str] = mapped_column(String)
    seller: Mapped[str | None] = mapped_column(String, nullable=True)
    # CURRENT_PUBLIC | DISPLAYED_ORIGINAL | REGULAR | LOYALTY | COUPON | RETAILER_STATED_30D_MIN
    price_type: Mapped[str] = mapped_column(String)
    price: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String, default="RSD")
    condition: Mapped[str | None] = mapped_column(Text, nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    url: Mapped[str] = mapped_column(String)
    availability: Mapped[str] = mapped_column(String)  # AVAILABLE | OUT_OF_STOCK | UNKNOWN
    sizes: Mapped[list] = mapped_column(JSON, default=list)
    delivery_rsd: Mapped[float | None] = mapped_column(Float, nullable=True)
    delivery_note: Mapped[str | None] = mapped_column(String, nullable=True)
    verification_status: Mapped[str] = mapped_column(String)  # VERIFIED | RETAILER_CLAIMED | CONDITIONAL | UNVERIFIED


Index("ix_obs_listing_type_time", PriceObservation.listing_id, PriceObservation.price_type,
      PriceObservation.observed_at)


class ForeignPrice(Base):
    """Cena iz strane prodavnice. Odvojena je od srpskih opažanja, pa ne utiče na tržište, skor ni rang. Samo se dopisuje."""
    __tablename__ = "foreign_prices"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String, index=True)
    url: Mapped[str] = mapped_column(String)
    title: Mapped[str] = mapped_column(String)
    brand: Mapped[str | None] = mapped_column(String, nullable=True)
    sku: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    color: Mapped[str | None] = mapped_column(String, nullable=True)
    gender: Mapped[str] = mapped_column(String)
    model_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    qualifiers: Mapped[list] = mapped_column(JSON, default=list)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)  # bez cene kad nijedna veličina nije na stanju
    old_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str] = mapped_column(String, default="EUR")
    availability: Mapped[str] = mapped_column(String)
    sizes: Mapped[list] = mapped_column(JSON, default=list)  # brojevi na stanju
    observed_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class ScanRun(Base):
    __tablename__ = "scan_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    filters: Mapped[dict] = mapped_column(JSON, default=dict)


class RetailerScanStatus(Base):
    __tablename__ = "retailer_scan_status"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scan_id: Mapped[int] = mapped_column(ForeignKey("scan_runs.id"), index=True)
    retailer: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)  # VERIFIED | PARTIAL | NOT_VERIFIED
    pages_ok: Mapped[int] = mapped_column(Integer, default=0)
    pages_failed: Mapped[int] = mapped_column(Integer, default=0)
    listings: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime)


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dedupe_key: Mapped[str] = mapped_column(String, unique=True)
    rule_type: Mapped[str] = mapped_column(String)
    model_id: Mapped[str | None] = mapped_column(String, nullable=True)
    listing_id: Mapped[str | None] = mapped_column(String, nullable=True)
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    seen: Mapped[bool] = mapped_column(Boolean, default=False)


IMMUTABLE_TRIGGERS = [
    """CREATE TRIGGER IF NOT EXISTS price_obs_no_update BEFORE UPDATE ON price_observations
       BEGIN SELECT RAISE(ABORT, 'price_observations je nepromenljiva tabela'); END""",
    """CREATE TRIGGER IF NOT EXISTS price_obs_no_delete BEFORE DELETE ON price_observations
       BEGIN SELECT RAISE(ABORT, 'price_observations je nepromenljiva tabela'); END""",
    """CREATE TRIGGER IF NOT EXISTS foreign_prices_no_update BEFORE UPDATE ON foreign_prices
       BEGIN SELECT RAISE(ABORT, 'foreign_prices je nepromenljiva tabela'); END""",
    """CREATE TRIGGER IF NOT EXISTS foreign_prices_no_delete BEFORE DELETE ON foreign_prices
       BEGIN SELECT RAISE(ABORT, 'foreign_prices je nepromenljiva tabela'); END""",
]


def make_session_factory(db_path: str | Path = ":memory:") -> sessionmaker[Session]:
    if str(db_path) != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{db_path}", future=True)

    @event.listens_for(engine, "connect")
    def _pragmas(conn, _record):
        cur = conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        if str(db_path) != ":memory:":
            cur.execute("PRAGMA journal_mode=WAL")
        cur.close()

    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        # Dopuna postojeće baze: istorija cena se ne sme izgubiti zbog nove kolone.
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(listings)"))}
        if "running" not in columns:
            conn.execute(text("ALTER TABLE listings ADD COLUMN running BOOLEAN"))
        if "image_url" not in columns:
            conn.execute(text("ALTER TABLE listings ADD COLUMN image_url VARCHAR"))
        catalog_columns = {row[1] for row in conn.execute(text("PRAGMA table_info(catalog_models)"))}
        if "msrp_usd" not in catalog_columns:
            conn.execute(text("ALTER TABLE catalog_models ADD COLUMN msrp_usd FLOAT"))
        for ddl in IMMUTABLE_TRIGGERS:
            conn.execute(text(ddl))
    return sessionmaker(engine, expire_on_commit=False)
