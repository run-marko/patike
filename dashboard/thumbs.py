"""Sličice patika za claude.ai artifakt.

Artifakt ne sme da učitava slike sa sajtova prodavnica (CSP), pa slike moraju da idu uz njega.
Svaka slika se preuzima samo jednom, uz robots.txt i razmak između zahteva (PoliteClient), smanji se
na 200 px (88 px na ekranu, do ~240 px kad se uveća) i čuva u data/thumbs.
Za artifakt se sličice slažu u nekoliko WebP listova (sprite), jer artifakt ima ograničen broj fajlova,
a jedan list od 256 sličica je manji od 256 posebnih fajlova.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image

from common.http import FetchError, PoliteClient

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "thumbs"
FAILED = CACHE / "failed.json"
TILE = 200          # px, kvadrat; slika se na prelazak mišem uveća na ~240 px, pa 144 nije bilo dovoljno oštro
GRID = 16           # 16 x 16 = 256 sličica po listu
QUALITY = 80        # WebP; ispod ~75 se na belim đonovima vide mrlje
RETRY_FAILED_DAYS = 7


def key(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:20]


def path_for(url: str) -> Path:
    return CACHE / f"{key(url)}-{TILE}.webp"  # veličina u imenu: promena TILE ne meša stare i nove sličice


def to_tile(data: bytes) -> Image.Image:
    """Smanjuje sliku tako da cela stane u kvadrat, na beloj podlozi (kao object-fit: contain)."""
    img = Image.open(io.BytesIO(data))
    img.load()
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
        img = Image.alpha_composite(bg, img)
    img = img.convert("RGB")
    img.thumbnail((TILE, TILE), Image.LANCZOS)
    tile = Image.new("RGB", (TILE, TILE), (255, 255, 255))
    tile.paste(img, ((TILE - img.width) // 2, (TILE - img.height) // 2))
    return tile


def _load_failed() -> dict[str, float]:
    try:
        return json.loads(FAILED.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def missing(urls: list[str]) -> list[str]:
    failed = _load_failed()
    cutoff = time.time() - RETRY_FAILED_DAYS * 86400
    return sorted({u for u in urls if u and not path_for(u).exists() and failed.get(u, 0) < cutoff})


def fetch(urls: list[str], delay_s: float = 1.5) -> dict[str, int]:
    """Preuzima sličice koje fale. Jedna nit po sajtu, svaki sajt ima svoj razmak između zahteva."""
    CACHE.mkdir(parents=True, exist_ok=True)
    for old in CACHE.glob("*.webp"):  # sličice druge veličine više ne trebaju
        if not old.name.endswith(f"-{TILE}.webp"):
            old.unlink()
    todo = missing(urls)
    by_host: dict[str, list[str]] = {}
    for u in todo:
        parts = urlsplit(u)
        if parts.scheme in ("http", "https") and parts.netloc:
            by_host.setdefault(f"{parts.scheme}://{parts.netloc}", []).append(u)
    failed = _load_failed()
    stats = {"new": 0, "failed": 0}

    def run(base: str, items: list[str]) -> None:
        client = PoliteClient(base, delay_s=delay_s)
        for u in items:
            try:
                resp = client.get(u)
                # bez gubitka u kešu, da se kvalitet gubi samo jednom, pri slaganju lista
                to_tile(resp.content).save(path_for(u), "WEBP", lossless=True)
                stats["new"] += 1
            except (FetchError, OSError, ValueError, Image.DecompressionBombError) as exc:
                log.info("sličica nije preuzeta %s: %s", u, exc)
                failed[u] = time.time()
                stats["failed"] += 1

    with ThreadPoolExecutor(max_workers=max(1, len(by_host))) as pool:
        for f in [pool.submit(run, b, items) for b, items in by_host.items()]:
            f.result()
    FAILED.write_text(json.dumps(failed), encoding="utf-8")
    return stats


def build_sheets(urls: list[str], out_dir: Path) -> dict[str, list[int]]:
    """Slaže sačuvane sličice u listove out_dir/N.webp. Vraća {url: [list, kolona, red]}."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.webp"):
        old.unlink()
    have = sorted({u for u in urls if u and path_for(u).exists()})
    index: dict[str, list[int]] = {}
    per = GRID * GRID
    for n in range(0, len(have), per):
        chunk = have[n:n + per]
        rows = (len(chunk) + GRID - 1) // GRID
        sheet = Image.new("RGB", (GRID * TILE, rows * TILE), (255, 255, 255))
        for i, u in enumerate(chunk):
            try:
                with Image.open(path_for(u)) as t:
                    sheet.paste(t.convert("RGB"), ((i % GRID) * TILE, (i // GRID) * TILE))
            except OSError:
                continue
            index[u] = [n // per, i % GRID, i // GRID, rows]
        sheet.save(out_dir / f"{n // per}.webp", "WEBP", quality=QUALITY, method=6)
    return index
