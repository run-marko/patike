"""Generiše samostalan HTML dashboard (bez CDN-a) i po želji ga servira lokalno."""
from __future__ import annotations

import base64
import functools
import gzip
import http.server
import json
import threading
import webbrowser
from pathlib import Path

from common import not_running

TEMPLATE = Path(__file__).resolve().parent / "template.html"
NOT_RUNNING_PATH = "/api/not-running"
LOCAL_HOSTS = ("127.0.0.1", "localhost")
MAX_BODY = 4096


def build_html(payload: str) -> str:
    safe = payload.replace("</", "<\\/")
    return TEMPLATE.read_text(encoding="utf-8").replace("/*__DATA__*/null", safe)


# Verzija za claude.ai artifakt (otvara se i sa telefona). Ceo JSON ima ~19 MB, a stranica sme 16 MB,
# pa se podaci šalju kao gzip u base64 (~2 MB) i raspakuju u pregledaču. Nema lokalnog servera,
# pa nema ni dugmeta "Nije za trčanje"; oznake koje već postoje su ugrađene u podatke.
ARTIFACT_LOADER = (
    "window.DEAL_STATIC=true;\n(async()=>{\n"
    "const DATA=JSON.parse(await new Response(new Blob([Uint8Array.from(atob(\"%s\"),c=>c.charCodeAt(0))])"
    ".stream().pipeThrough(new DecompressionStream(\"gzip\"))).text());\n"
)


def build_artifact_html(payload: str) -> str:
    packed = base64.b64encode(gzip.compress(payload.encode("utf-8"), 9)).decode("ascii")
    html = TEMPLATE.read_text(encoding="utf-8")
    head_end = html.index("</head>")
    style = html[html.index("<title>"):head_end]  # naslov i stil; omot stranice dodaje artifakt
    body = html[html.index("<body>") + len("<body>"):html.rindex("</body>")]
    body = body.replace("const DATA = /*__DATA__*/null;\n", ARTIFACT_LOADER % packed, 1)
    last = body.rindex("</script>")
    return style + body[:last] + "})();\n" + body[last:]


def build_page_html(payload: str) -> str:
    """Ista stranica kao artifakt, ali kao ceo HTML dokument za GitHub Pages.
    Na GitHubu se pre objave šifruje lozinkom (staticrypt); nešifrovana se nikad ne objavljuje."""
    return ('<!doctype html>\n<html lang="sr">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            '<meta name="robots" content="noindex, nofollow">\n</head>\n<body>\n'
            + build_artifact_html(payload) + "\n</body>\n</html>\n")


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == NOT_RUNNING_PATH:
            self._json(200, not_running.load())
        else:
            super().do_GET()

    def _drain(self) -> None:
        """Čita telo odbijenog zahteva (najviše MAX_BODY), da klijent dobije odgovor umesto prekinute veze."""
        try:
            length = min(int(self.headers.get("Content-Length") or 0), MAX_BODY)
            if length > 0:
                self.rfile.read(length)
        except (ValueError, OSError):
            pass
        self.close_connection = True

    def do_POST(self):
        if self.path != NOT_RUNNING_PATH:
            self._drain()
            self._json(404, {"error": "nepoznata putanja"})
        elif not self._from_dashboard():
            self._drain()
            self._json(403, {"error": "zahtev nije sa lokalnog dashboarda"})
        else:
            body = self._read_body()
            if body is None:
                self._json(400, {"error": "neispravan zahtev"})
            else:
                self._json(200, not_running.set_mark(body["model_id"], body["name"], body["marked"]))

    def _from_dashboard(self) -> bool:
        """Druga web stranica u browseru ne sme da menja oznake: traži se lokalni host, isti origin i JSON."""
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin")
        return (host.split(":")[0] in LOCAL_HOSTS and origin in (None, f"http://{host}")
                and self.headers.get("Content-Type", "") == "application/json")

    def _read_body(self) -> dict | None:
        try:
            length = int(self.headers.get("Content-Length", 0))
            if not 0 < length <= MAX_BODY:
                return None
            body = json.loads(self.rfile.read(length))
            valid = (isinstance(body, dict) and isinstance(body.get("model_id"), str) and body["model_id"]
                     and isinstance(body.get("name"), str) and isinstance(body.get("marked"), bool))
            return body if valid else None
        except ValueError:
            return None

    def _json(self, status: int, data: dict) -> None:
        content = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)


def serve(directory: Path, port: int = 8765, open_browser: bool = True) -> None:
    handler = functools.partial(_QuietHandler, directory=str(directory))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    url = f"http://127.0.0.1:{port}/dashboard.html"
    print(f"Dashboard: {url}  (Ctrl+C za kraj)")
    if open_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
