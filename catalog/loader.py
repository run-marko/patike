"""Seed katalog i ručni uvoz. Katalog NIKADA ne nastaje iz podataka srpskih prodavnica."""
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from database.models import CatalogModel

CATALOG_DIR = Path(__file__).resolve().parent
SEED_FILE = CATALOG_DIR / "seed_catalog.json"
MANUAL_FILE = CATALOG_DIR / "manual_catalog.json"
REQUIRED = ("brand", "model_name", "source_url")
JSON_FIELDS = {"categories"}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0).isoformat()


def read_json(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def validate_entry(entry: dict) -> list[str]:
    problems = [f"nedostaje polje {name}" for name in REQUIRED if not entry.get(name)]
    if entry.get("source_url") and not str(entry["source_url"]).startswith("http"):
        problems.append("source_url mora biti proverljiv URL")
    if entry.get("review_score") is not None and not entry.get("review_source"):
        problems.append("ocena bez izvora nije dozvoljena")
    return problems


def read_import_file(path: Path) -> list[dict]:
    if path.suffix.lower() == ".json":
        return json.loads(path.read_text(encoding="utf-8"))
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        for key, value in list(row.items()):
            if value == "":
                row[key] = None
        if isinstance(row.get("categories"), str):
            row["categories"] = [c.strip() for c in row["categories"].split(";") if c.strip()]
        if row.get("review_score"):
            row["review_score"] = float(row["review_score"])
        if row.get("msrp_usd"):
            row["msrp_usd"] = float(row["msrp_usd"])
        if row.get("release_year"):
            row["release_year"] = int(row["release_year"])
    return rows


def upsert_entries(session: Session, entries: list[dict]) -> int:
    from catalog.runrepeat import parse_generation, slug_id

    count = 0
    for entry in entries:
        entry = dict(entry)
        entry.setdefault("id", slug_id(entry["brand"], entry["model_name"]))
        if not entry["id"]:
            entry["id"] = slug_id(entry["brand"], entry["model_name"])
        entry.setdefault("generation", parse_generation(entry["model_name"]))
        verified = entry.get("last_verified_at") or now_iso()
        row = session.get(CatalogModel, entry["id"]) or CatalogModel(id=entry["id"])
        for field in ("brand", "model_name", "generation", "genders", "surface", "category_basis", "cushioning",
                      "midsole_foam", "plate", "msrp_usd", "review_score", "review_source", "review_url", "release_year",
                      "source_url"):
            setattr(row, field, entry.get(field))
        row.categories = entry.get("categories") or []
        row.last_verified_at = datetime.fromisoformat(verified)
        session.merge(row)
        count += 1
    session.commit()
    return count


def load_catalog(session: Session) -> int:
    """Učitava seed + ručne unose u bazu. Ručni unosi imaju prednost."""
    return upsert_entries(session, read_json(SEED_FILE)) + upsert_entries(session, read_json(MANUAL_FILE))
