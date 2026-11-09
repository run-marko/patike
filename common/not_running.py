"""Modeli koje je korisnik ručno označio da nisu za trčanje. Cene i istorija ostaju; model samo ispada iz lista."""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone

from common.config import DATA_DIR

log = logging.getLogger("not_running")
FILE = DATA_DIR / "not_running.json"
_lock = threading.Lock()


def load() -> dict[str, dict]:
    """model_id -> {"name", "marked_at"}. Bez fajla nema oznaka."""
    try:
        data = json.loads(FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        log.warning("Oznake modela nisu pročitane (%s): %s", FILE, exc)
        return {}
    return data if isinstance(data, dict) else {}


def set_mark(model_id: str, name: str, marked: bool) -> dict[str, dict]:
    """Dodaje ili uklanja oznaku i vraća sve oznake. Upis ide preko privremenog fajla, da prekid ne ošteti spisak."""
    with _lock:
        marks = load()
        if marked:
            now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")
            marks[model_id] = {"name": name, "marked_at": now}
        else:
            marks.pop(model_id, None)
        FILE.parent.mkdir(parents=True, exist_ok=True)
        temp = FILE.with_suffix(".tmp")
        temp.write_text(json.dumps(marks, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(temp, FILE)
        return marks
