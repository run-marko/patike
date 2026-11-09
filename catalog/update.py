"""`python main.py catalog update`: osvežava seed katalog iz RunRepeat-a."""
from __future__ import annotations

import logging
import sys

from catalog import runrepeat
from catalog.loader import SEED_FILE, now_iso, read_json
from common.http import PoliteClient

log = logging.getLogger("catalog.update")


def update_seed(delay_s: float = 1.5, full: bool = False, max_pages: int | None = None) -> dict:
    """Čita katalog. Bez `full` čita detalje samo za nove modele; kod starih osvežava ocenu iz liste."""
    client = PoliteClient(runrepeat.BASE, delay_s=delay_s)
    previous = {e["source_url"]: e for e in read_json(SEED_FILE)}
    items = runrepeat.fetch_listing(client, max_pages=max_pages)

    def progress(index: int, total: int, name: str) -> None:
        if index % 25 == 0 or index == total:
            print(f"  katalog {index}/{total}: {name}", flush=True)

    entries, failures = runrepeat.fetch_entries(
        client, items, now_iso(), known=None if full else previous, progress=progress, fallback=previous)
    merged = {e["source_url"]: e for e in previous.values()}
    merged.update({e["source_url"]: e for e in entries})
    SEED_FILE.write_text(runrepeat.dumps(list(merged.values())), encoding="utf-8")
    return {"listed": len(items), "total": len(merged), "failures": failures,
            "new": len(set(e["source_url"] for e in entries) - set(previous))}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO)
    result = update_seed()
    print({k: v for k, v in result.items() if k != "failures"}, "greške:", len(result["failures"]))
