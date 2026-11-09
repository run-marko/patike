"""Pakuje i raspakuje folder data/ (baza, keš sitemapa, kurs, oznake, sličice) za GitHub Actions.

Na GitHubu svaki sken počinje na praznom računaru, pa se stanje čuva kao fajl izdanja "stanje"
u privatnom repozitorijumu: preuzme se pre skena, a posle skena se vrati nazad.

    python -m cloud.state pack  stanje.tar.gz
    python -m cloud.state unpack stanje.tar.gz
"""
from __future__ import annotations

import sqlite3
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SKIP = {"app.log", "daily_scan.log", "shoes.db-wal", "shoes.db-shm"}


def pack(target: Path) -> None:
    db = DATA / "shoes.db"
    if db.exists():
        # sve iz WAL dnevnika ide u samu bazu, da arhiva ima jedan potpun fajl
        with sqlite3.connect(db) as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    with tarfile.open(target, "w:gz", compresslevel=6) as tar:
        for path in sorted(DATA.rglob("*")):
            if path.is_file() and path.name not in SKIP:
                tar.add(path, arcname=str(path.relative_to(DATA)))


def unpack(source: Path) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    with tarfile.open(source, "r:gz") as tar:
        tar.extractall(DATA, filter="data")  # "data" filter odbija apsolutne putanje i ../


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in ("pack", "unpack"):
        sys.exit(__doc__)
    (pack if sys.argv[1] == "pack" else unpack)(Path(sys.argv[2]))
