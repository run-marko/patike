"""Pristojan HTTP klijent: robots.txt, razmak između zahteva, retry sa eksponencijalnim čekanjem.

Namerno NE menja User-Agent, ne rotira identitet i ne pokušava da zaobiđe blokade.
HTTP 403 ili bot-izazov se tretira kao "sajt nas je odbio": adapter prijavljuje NOT VERIFIED.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

import httpx

log = logging.getLogger("http")

USER_AGENT = "RunningShoeDealFinderSerbia/1.0 (local personal price tracker)"
TRANSIENT = {500, 502, 503, 504}
MAX_DELAY_S = 30
MAX_RETRY_AFTER_S = 600
CHALLENGE_MARKERS = ("attention required! | cloudflare", "just a moment...", "cf-chl-", "captcha")


class FetchError(Exception):
    """Prolazna ili neočekivana greška: stranica nije proverena."""


class BlockedError(FetchError):
    """Sajt je odbio pristup (403, bot zaštita). Ne pokušava se zaobilaženje."""


class RateLimitedError(BlockedError):
    """Sajt traži da usporimo (429). Posle čekanja i usporavanja se staje, a ne nastavlja se pritisak."""


class RobotsDisallowed(FetchError):
    """robots.txt zabranjuje ovu putanju."""


class NotFoundError(FetchError):
    """HTTP 404/410."""


def canonicalize_url(url: str) -> str:
    """Uklanja fragment, tracking parametre i završnu kosu crtu; host u malim slovima."""
    parts = urlsplit(url.strip())
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith(("utm_", "gclid", "fbclid", "mc_", "ref"))
    ]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), ""))


def _rule_to_regex(rule: str) -> re.Pattern:
    anchored = rule.endswith("$")
    body = rule[:-1] if anchored else rule
    pattern = ".*".join(re.escape(piece) for piece in body.split("*"))
    return re.compile("^" + pattern + ("$" if anchored else ""))


@dataclass
class RobotsRules:
    """robots.txt za grupu `*` (naš bot nema sopstvenu grupu). Podržava * i $, najduže pravilo pobeđuje."""

    rules: list[tuple[bool, str, re.Pattern]] = field(default_factory=list)
    crawl_delay: float | None = None
    sitemaps: list[str] = field(default_factory=list)

    @classmethod
    def parse(cls, text: str) -> "RobotsRules":
        out = cls()
        agents: list[str] = []
        in_rules = False
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            key, value = (p.strip() for p in line.split(":", 1))
            key = key.lower()
            if key == "sitemap":
                out.sitemaps.append(value)
            elif key == "user-agent":
                if in_rules:
                    agents = []
                    in_rules = False
                agents.append(value.lower())
            elif key in ("allow", "disallow"):
                in_rules = True
                if "*" in agents and value:
                    out.rules.append((key == "allow", value, _rule_to_regex(value)))
            elif key == "crawl-delay":
                in_rules = True
                if "*" in agents:
                    try:
                        out.crawl_delay = float(value)
                    except ValueError:
                        pass
        return out

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        target = parts.path + ("?" + parts.query if parts.query else "")
        best: tuple[int, bool] | None = None
        for allow, rule, regex in self.rules:
            if regex.match(target):
                score = (len(rule), allow)
                if best is None or score > best:
                    best = score
        return True if best is None else best[1]


class PoliteClient:
    """Klijent za jedan sajt. Bezbedan za upotrebu iz jedne niti po sajtu."""

    def __init__(self, base_url: str, delay_s: float = 1.5, timeout_s: float = 30, max_retries: int = 3,
                 client: httpx.Client | None = None, sleep=time.sleep):
        self.base_url = base_url.rstrip("/")
        self.delay_s = delay_s
        self.max_retries = max_retries
        self._sleep = sleep
        self._last = 0.0
        self._lock = threading.Lock()
        self._robots: RobotsRules | None = None
        self._client = client or httpx.Client(
            headers={"User-Agent": USER_AGENT, "Accept-Language": "sr,en;q=0.5"},
            timeout=timeout_s,
            follow_redirects=True,
        )

    def _wait_turn(self) -> None:
        with self._lock:
            pause = self.delay_s - (time.monotonic() - self._last)
            if pause > 0:
                self._sleep(pause)
            self._last = time.monotonic()

    def robots(self) -> RobotsRules:
        """Učitava robots.txt. 404 znači da nema pravila; 403 znači da je sajt blokirao pristup."""
        if self._robots is None:
            resp = self._request(self.base_url + "/robots.txt", check_robots=False, allow_404=True)
            if resp.status_code == 200 and "<html" not in resp.text[:500].lower():
                self._robots = RobotsRules.parse(resp.text)
            else:
                self._robots = RobotsRules()
            if self._robots.crawl_delay and self._robots.crawl_delay > self.delay_s:
                self.delay_s = self._robots.crawl_delay
        return self._robots

    def get(self, url: str) -> httpx.Response:
        return self._request(url, check_robots=True)

    def _request(self, url: str, check_robots: bool, allow_404: bool = False) -> httpx.Response:
        if check_robots and not self.robots().allowed(url):
            raise RobotsDisallowed(f"robots.txt ne dozvoljava: {url}")
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self._wait_turn()
            try:
                resp = self._client.get(url)
            except httpx.HTTPError as exc:
                last_error = exc
                log.debug("prolazna greška %s: %s", url, exc)
            else:
                if resp.status_code == 403 or self._is_challenge(resp):
                    raise BlockedError(f"HTTP {resp.status_code}: sajt je odbio pristup ({url})")
                if resp.status_code in (404, 410):
                    if allow_404:
                        return resp
                    raise NotFoundError(f"HTTP {resp.status_code}: {url}")
                if resp.status_code == 429:
                    # Jedna pauza po uputstvu sajta i trajno sporiji tempo. Bez niza ponovnih pokušaja.
                    self.delay_s = min(self.delay_s * 2, MAX_DELAY_S)
                    if attempt >= min(1, self.max_retries):
                        raise RateLimitedError(f"HTTP 429: sajt traži da usporimo ({url})")
                    self._sleep(self._retry_after(resp))
                    continue
                if resp.status_code in TRANSIENT:
                    last_error = FetchError(f"HTTP {resp.status_code}: {url}")
                elif resp.status_code >= 400:
                    raise FetchError(f"HTTP {resp.status_code}: {url}")
                else:
                    return resp
            if attempt < self.max_retries:
                self._sleep(2 ** attempt)
        raise FetchError(f"Nije uspelo posle {self.max_retries + 1} pokušaja: {url} ({last_error})")

    @staticmethod
    def _retry_after(resp: httpx.Response) -> float:
        """Čekanje iz zaglavlja Retry-After, najmanje 60 s. Predugo čekanje se ne radi: tada se odustaje posle pauze."""
        try:
            return min(max(float(resp.headers.get("retry-after", 60)), 60), MAX_RETRY_AFTER_S)
        except ValueError:  # Retry-After može biti i datum
            return 60

    @staticmethod
    def _is_challenge(resp: httpx.Response) -> bool:
        if resp.status_code == 200 and len(resp.content) > 30000:
            return False  # prave stranice proizvoda su velike; "captcha" se tu javlja i u skriptama
        low = resp.text[:6000].lower()
        return any(marker in low for marker in CHALLENGE_MARKERS)
