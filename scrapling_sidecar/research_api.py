"""HTTP research API for Grok / A0 / the trading backend.

Runs beside Scrapling MCP inside the sidecar. POST /fetch returns page
content as markdown/html/text without requiring an MCP session.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from scrapling.fetchers import Fetcher, StealthyFetcher

try:
    from scrapling_sidecar.url_guard import is_public_http_url
except ImportError:
    from url_guard import is_public_http_url

logging.basicConfig(level=logging.INFO, format="[scrapling-research] %(message)s")
log = logging.getLogger("scrapling-research")

LISTEN_HOST = os.getenv("SCRAPLING_RESEARCH_HOST", "0.0.0.0")
LISTEN_PORT = int(os.getenv("SCRAPLING_RESEARCH_PORT", "8080"))
AUTH_TOKEN = (os.getenv("SCRAPLING_MCP_AUTH_TOKEN") or os.getenv("ADMIN_API_KEY") or "").strip()
MAX_CONTENT_CHARS = int(os.getenv("SCRAPLING_MAX_CONTENT_CHARS", "24000"))
MAX_BODY_BYTES = 65536


def _authorized(handler: BaseHTTPRequestHandler) -> bool:
    if not AUTH_TOKEN:
        return False
    supplied = (handler.headers.get("X-API-Key") or "").strip()
    auth = (handler.headers.get("Authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        supplied = auth[7:].strip()
    if not supplied:
        return False
    return hmac.compare_digest(supplied, AUTH_TOKEN)


def _extract(page: Any, extraction_type: str, css_selector: Optional[str]) -> str:
    target = page.css(css_selector) if css_selector else page
    if extraction_type == "html":
        html = getattr(target, "html_content", None) or getattr(target, "html", None)
        return str(html or target)
    if extraction_type == "text":
        text = getattr(target, "get_all_text", None)
        if callable(text):
            return text()
        return str(getattr(target, "text", target))
    markdown = getattr(target, "to_markdown", None)
    if callable(markdown):
        return markdown()
    return str(target)


def fetch_url(
    url: str,
    *,
    mode: str = "http",
    extraction_type: str = "markdown",
    css_selector: Optional[str] = None,
) -> Dict[str, Any]:
    if not is_public_http_url(url):
        raise ValueError("url must be a public http(s) host")
    if mode not in {"http", "stealth"}:
        raise ValueError("mode must be http or stealth")
    if extraction_type not in {"markdown", "html", "text"}:
        raise ValueError("extraction_type must be markdown, html, or text")

    if mode == "stealth":
        page = StealthyFetcher.fetch(url, headless=True, network_idle=True)
    else:
        page = Fetcher.get(url, timeout=30, follow_redirects="safe")

    content = _extract(page, extraction_type, css_selector)
    if len(content) > MAX_CONTENT_CHARS:
        content = content[:MAX_CONTENT_CHARS] + "\n...[truncated]"

    return {
        "url": url,
        "status": getattr(page, "status", None),
        "mode": mode,
        "extraction_type": extraction_type,
        "css_selector": css_selector,
        "content": content,
    }


class ResearchHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:
        log.info("%s - %s", self.address_string(), fmt % args)

    def _send(self, code: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in {"/health", "/"}:
            self._send(200, {"status": "ok", "service": "scrapling-research"})
            return
        self._send(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if not _authorized(self):
            self._send(401, {"error": "missing or invalid token"})
            return
        if urlparse(self.path).path != "/fetch":
            self._send(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._send(400, {"error": "invalid content length"})
            return
        if length < 0 or length > MAX_BODY_BYTES:
            self._send(413, {"error": "request too large"})
            return
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self._send(400, {"error": "invalid json"})
            return
        url = str(data.get("url") or "").strip()
        try:
            result = fetch_url(
                url,
                mode=str(data.get("mode") or "http"),
                extraction_type=str(data.get("extraction_type") or "markdown"),
                css_selector=data.get("css_selector"),
            )
        except ValueError as exc:
            self._send(400, {"error": str(exc)})
            return
        except Exception:
            log.exception("fetch failed")
            self._send(502, {"error": "fetch failed"})
            return
        self._send(200, result)


def main() -> None:
    server = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), ResearchHandler)
    log.info("listening on %s:%s", LISTEN_HOST, LISTEN_PORT)
    server.serve_forever()


if __name__ == "__main__":
    main()
