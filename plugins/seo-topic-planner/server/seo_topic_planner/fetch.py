"""Guarded single-page fetching and readable-content extraction.

The planner reads ranking pages, the client's own pages and external sources
itself, so every quote an analysis relies on can be checked against text the
server actually captured.  Fetching is deliberately narrow: public http(s)
only, every hop resolved to a public IP that the socket is pinned to (no DNS
rebinding), bounded size and time, no cookies, no JavaScript.
"""

from __future__ import annotations

import gzip
import http.client
import ipaddress
import json
import re
import socket
import ssl
import unicodedata
import urllib.error
import urllib.request
import zlib
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Callable
from urllib.parse import urljoin, urlparse

TIMEOUT_SECONDS = 15
MAX_BYTES = 3_000_000
MAX_REDIRECTS = 5
MAX_TEXT_CHARS = 120_000
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


class FetchError(Exception):
    """A page could not be fetched; ``status`` is BLOCKED or FAILED."""

    def __init__(self, message: str, status: str = "FAILED") -> None:
        super().__init__(message)
        self.status = status


# --- network ----------------------------------------------------------------


def _public(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _resolve(host: str) -> str:
    addresses = sorted({info[4][0] for info in socket.getaddrinfo(host, None)})
    for address in addresses:
        if _public(address):
            return address
    raise FetchError("refused: host has no public address", "BLOCKED")


class _PinnedHTTP(http.client.HTTPConnection):
    def __init__(self, host: str, *, pinned: str, **kwargs: Any) -> None:
        super().__init__(host, **kwargs)
        self._pinned = pinned

    def connect(self) -> None:
        self.sock = socket.create_connection((self._pinned, self.port), self.timeout)
        if self.sock.getpeername()[0] != self._pinned:
            self.sock.close()
            raise OSError("peer is not the pinned public address")


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host: str, *, pinned: str, proxy: tuple[str, int] | None, **kwargs: Any) -> None:
        super().__init__(host, **kwargs)
        self._pinned = pinned
        self._proxy = proxy

    def connect(self) -> None:
        if self._proxy:
            # Tunnel to the vetted address through the user's configured proxy;
            # TLS still verifies the certificate against the real hostname.
            self.set_tunnel(self._pinned, self.port)
            self.sock = socket.create_connection(self._proxy, self.timeout)
            self._tunnel()
        else:
            self.sock = socket.create_connection((self._pinned, self.port), self.timeout)
            if self.sock.getpeername()[0] != self._pinned:
                self.sock.close()
                raise OSError("peer is not the pinned public address")
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def _https_proxy() -> tuple[str, int] | None:
    configured = urllib.request.getproxies().get("https")
    if not configured:
        return None
    parsed = urlparse(configured)
    if parsed.scheme != "http" or not parsed.hostname or parsed.username:
        return None
    return parsed.hostname, parsed.port or 80


def _open_once(url: str) -> tuple[int, dict[str, str], bytes]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise FetchError("only public http(s) URLs can be fetched", "BLOCKED")
    pinned = _resolve(parsed.hostname)
    proxy = _https_proxy() if parsed.scheme == "https" else None
    context = ssl.create_default_context()

    class _HTTP(urllib.request.HTTPHandler):
        def http_open(self, req):  # noqa: ANN001
            return self.do_open(lambda host, **kw: _PinnedHTTP(host, pinned=pinned, **kw), req)

    class _HTTPS(urllib.request.HTTPSHandler):
        def https_open(self, req):  # noqa: ANN001
            return self.do_open(lambda host, **kw: _PinnedHTTPS(host, pinned=pinned, proxy=proxy, **kw), req)

    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), _NoRedirect, _HTTP, _HTTPS(context=context)
    )
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.8",
            "Accept-Encoding": "gzip, deflate",
        },
    )
    try:
        with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
            body = response.read(MAX_BYTES + 1)
            return response.status, {k.lower(): v for k, v in response.headers.items()}, body
    except urllib.error.HTTPError as exc:
        headers = {k.lower(): v for k, v in (exc.headers or {}).items()}
        return exc.code, headers, b""


def fetch_url(url: str) -> tuple[str, str, str]:
    """Return ``(final_url, content_type, decoded_text)`` or raise FetchError."""

    current = url
    for _ in range(MAX_REDIRECTS + 1):
        try:
            status, headers, body = _open_once(current)
        except FetchError:
            raise
        except (OSError, http.client.HTTPException, ValueError) as exc:
            raise FetchError(f"network error: {exc.__class__.__name__}") from exc
        if status in {301, 302, 303, 307, 308} and headers.get("location"):
            current = urljoin(current, headers["location"])
            continue
        if status in {401, 403, 429, 451}:
            raise FetchError(f"HTTP {status}", "BLOCKED")
        if status >= 400:
            raise FetchError(f"HTTP {status}")
        if len(body) > MAX_BYTES:
            raise FetchError("page exceeds the size limit")
        encoding = headers.get("content-encoding", "").lower()
        try:
            if encoding == "gzip":
                body = gzip.decompress(body)
            elif encoding == "deflate":
                body = zlib.decompress(body)
        except (OSError, zlib.error) as exc:
            raise FetchError("could not decompress the response") from exc
        content_type = headers.get("content-type", "")
        charset_match = re.search(r"charset=([\w-]+)", content_type, re.I)
        charset = charset_match.group(1) if charset_match else "utf-8"
        try:
            text = body.decode(charset, errors="replace")
        except LookupError:
            text = body.decode("utf-8", errors="replace")
        return current, content_type, text
    raise FetchError("too many redirects")


# --- extraction ---------------------------------------------------------------

_SKIP = {"script", "style", "noscript", "template", "svg", "iframe", "canvas", "button", "select"}
_CHROME = {"nav", "footer", "aside", "form"}
_BLOCK = {
    "p",
    "div",
    "li",
    "tr",
    "td",
    "th",
    "section",
    "article",
    "main",
    "br",
    "pre",
    "blockquote",
    "dd",
    "dt",
    "figcaption",
    "table",
    "ul",
    "ol",
}
_HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4}


@dataclass
class ExtractedPage:
    title: str = ""
    h1: str = ""
    meta_description: str = ""
    published_at: str = ""
    modified_at: str = ""
    headings: list[dict[str, Any]] = field(default_factory=list)
    sections: list[dict[str, Any]] = field(default_factory=list)
    text: str = ""
    word_count: int = 0
    links: list[str] = field(default_factory=list)


class _Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.chrome = 0
        self.main_depth = 0
        self.saw_main = False
        self.heading: str | None = None
        self.heading_text: list[str] = []
        self.in_title = False
        self.in_jsonld = False
        self.jsonld: list[str] = []
        self.title = ""
        self.meta: dict[str, str] = {}
        self.links: list[str] = []
        # Two parallel streams: everything outside chrome, and only main/article.
        self.all_events: list[tuple[str, Any]] = []
        self.main_events: list[tuple[str, Any]] = []

    def _emit(self, kind: str, value: Any) -> None:
        self.all_events.append((kind, value))
        if self.main_depth:
            self.main_events.append((kind, value))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key: value or "" for key, value in attrs}
        if tag == "script" and attributes.get("type", "").lower() == "application/ld+json":
            self.in_jsonld = True
            return
        if tag in _SKIP:
            self.skip += 1
            return
        if tag == "title":
            self.in_title = True
        if tag == "meta":
            key = (attributes.get("name") or attributes.get("property") or "").lower()
            if key and attributes.get("content"):
                self.meta.setdefault(key, attributes["content"])
        if tag == "a" and attributes.get("href"):
            self.links.append(attributes["href"])
        if tag in _CHROME:
            self.chrome += 1
        if tag in {"main", "article"}:
            self.main_depth += 1
            self.saw_main = True
        if tag in _HEADINGS and not self.skip:
            self.heading = tag
            self.heading_text = []
        elif tag in _BLOCK:
            self._emit("break", None)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self.in_jsonld:
            self.in_jsonld = False
            return
        if tag in _SKIP:
            self.skip = max(0, self.skip - 1)
            return
        if tag == "title":
            self.in_title = False
        if tag in _CHROME:
            self.chrome = max(0, self.chrome - 1)
        if tag in {"main", "article"}:
            self.main_depth = max(0, self.main_depth - 1)
        if tag in _HEADINGS and self.heading == tag:
            text = _clean(" ".join(self.heading_text))
            if text and not self.chrome:
                self._emit("heading", (_HEADINGS[tag], text))
            self.heading = None
        elif tag in _BLOCK:
            self._emit("break", None)

    def handle_data(self, data: str) -> None:
        if self.in_jsonld:
            self.jsonld.append(data)
            return
        if self.in_title:
            self.title += data
            return
        if self.skip:
            return
        if self.heading:
            self.heading_text.append(data)
            return
        if self.chrome:
            return
        self._emit("text", data)


_INVISIBLE = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff"), None)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.translate(_INVISIBLE)).strip()


def _jsonld_dates(blocks: list[str]) -> tuple[str, str]:
    published = modified = ""
    for block in blocks:
        try:
            data = json.loads(block)
        except (json.JSONDecodeError, ValueError):
            continue
        stack = [data]
        while stack:
            item = stack.pop()
            if isinstance(item, list):
                stack.extend(item)
            elif isinstance(item, dict):
                published = published or str(item.get("datePublished") or "")
                modified = modified or str(item.get("dateModified") or "")
                stack.extend(value for value in item.values() if isinstance(value, (dict, list)))
    return published[:40], modified[:40]


def _assemble(events: list[tuple[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    headings: list[dict[str, Any]] = []
    sections: list[dict[str, Any]] = [{"heading": "", "level": 0, "text": ""}]
    paragraphs: list[str] = []
    buffer: list[str] = []

    def flush() -> None:
        chunk = _clean(" ".join(buffer))
        buffer.clear()
        if chunk:
            paragraphs.append(chunk)
            current = sections[-1]
            current["text"] = f"{current['text']}\n{chunk}" if current["text"] else chunk

    for kind, value in events:
        if kind == "text":
            buffer.append(value)
        elif kind == "break":
            flush()
        elif kind == "heading":
            flush()
            level, text = value
            headings.append({"level": level, "text": text})
            paragraphs.append(text)
            sections.append({"heading": text, "level": level, "text": ""})
    flush()
    sections = [section for section in sections if section["heading"] or section["text"]]
    return headings, sections, "\n".join(paragraphs)


def extract(html: str) -> ExtractedPage:
    parser = _Extractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001 - malformed markup keeps whatever was parsed
        pass
    events = parser.main_events if parser.saw_main and parser.main_events else parser.all_events
    headings, sections, text = _assemble(events)
    if parser.saw_main and len(text) < 400:
        # A thin <main> usually means the real body sits elsewhere.
        headings, sections, text = _assemble(parser.all_events)
    published, modified = _jsonld_dates(parser.jsonld)
    meta = parser.meta
    h1 = next((item["text"] for item in headings if item["level"] == 1), "")
    text = text[:MAX_TEXT_CHARS]
    return ExtractedPage(
        title=_clean(parser.title)[:300],
        h1=h1[:300],
        meta_description=_clean(meta.get("description") or meta.get("og:description") or "")[:500],
        published_at=published or meta.get("article:published_time", "")[:40],
        modified_at=modified or meta.get("article:modified_time", "")[:40],
        headings=headings[:150],
        sections=sections[:200],
        text=text,
        word_count=len(re.findall(r"\w+", text)),
        links=parser.links[:2000],
    )


def sitemap_urls(xml: str) -> tuple[list[str], list[str]]:
    """Return ``(page_urls, child_sitemaps)`` from a sitemap or sitemap index."""

    locs = [_clean(item) for item in re.findall(r"<loc>\s*(.*?)\s*</loc>", xml, re.I | re.S)]
    locs = [item.replace("&amp;", "&") for item in locs if item]
    if re.search(r"<sitemapindex", xml, re.I):
        return [], locs
    return locs, []


# --- quote verification ---------------------------------------------------------

_QUOTE_MAP = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", " ": " "})


def normalize_for_match(text: str) -> str:
    text = unicodedata.normalize("NFKC", str(text or "")).translate(_QUOTE_MAP).lower()
    return re.sub(r"[^\w]+", " ", text).strip()


def quote_in(quote: str, text: str) -> bool:
    """A quote counts only when its normalized form appears in the captured text."""

    needle = normalize_for_match(quote)
    return len(needle) >= 12 and needle in normalize_for_match(text)


Fetcher = Callable[[str], tuple[str, str, str]]
