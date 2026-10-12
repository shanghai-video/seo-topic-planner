"""URL and registrable-domain helpers backed by the bundled Public Suffix List.

The Public Suffix List (MPL-2.0) ships as a data file and is parsed with the
standard library, so ``blog.example.co.uk`` and ``example.co.uk`` resolve to the
same site while two tenants of ``github.io`` never do.
"""

from __future__ import annotations

import functools
import hashlib
import ipaddress
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

_PSL_PATH = Path(__file__).resolve().parent / "data" / "public_suffix_list.dat"


@functools.lru_cache(maxsize=1)
def _rules() -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    exact: set[str] = set()
    wildcard: set[str] = set()
    exception: set[str] = set()
    for line in _PSL_PATH.read_text(encoding="utf-8").splitlines():
        entry = line.strip()
        if not entry or entry.startswith("//"):
            continue
        entry = entry.split()[0].lower().rstrip(".")
        if entry.startswith("!"):
            exception.add(entry[1:])
        elif entry.startswith("*."):
            wildcard.add(entry[2:])
        elif entry:
            exact.add(entry)
    return frozenset(exact), frozenset(wildcard), frozenset(exception)


def _public_suffix(host: str) -> str:
    exact, wildcard, exception = _rules()
    labels = host.split(".")
    for index in range(len(labels)):
        if ".".join(labels[index:]) in exception:
            return ".".join(labels[index + 1 :])
    best = ""
    for index in range(len(labels)):
        suffix = ".".join(labels[index:])
        if suffix in exact and len(suffix) > len(best):
            best = suffix
        parent = ".".join(labels[index + 1 :])
        if parent and parent in wildcard and len(suffix) > len(best):
            best = suffix
    return best or labels[-1]


def is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def host_of(value: Any) -> str:
    """Lower-case hostname of a URL or bare host, without ``www.``."""

    text = str(value or "").strip().lower()
    if not text:
        return ""
    parsed = urlparse(text if "://" in text else f"//{text}")
    host = (parsed.hostname or "").rstrip(".")
    return host[4:] if host.startswith("www.") else host


def registrable_domain(value: Any) -> str:
    """eTLD+1 of a URL or host, or ``""`` for IPs, bare suffixes and junk."""

    host = host_of(value)
    if not host or "." not in host or is_ip(host):
        return ""
    suffix = _public_suffix(host)
    if suffix == host:
        return ""
    labels = host.split(".")
    return ".".join(labels[len(labels) - len(suffix.split(".")) - 1 :])


def same_site(url: Any, domain: str) -> bool:
    site = registrable_domain(url)
    return bool(site) and site == registrable_domain(domain)


def normalize_url(value: Any) -> str:
    """Canonical form used as a page key: https-agnostic, no fragment, no www."""

    text = str(value or "").strip()
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    host = parsed.hostname.lower()
    if host.startswith("www."):
        host = host[4:]
    netloc = host if parsed.port in (None, 80, 443) else f"{host}:{parsed.port}"
    path = parsed.path or "/"
    if len(path) > 1:
        path = path.rstrip("/")
    return urlunparse(("https", netloc, path, "", parsed.query, ""))


def page_id(url: str) -> str:
    """Stable id of a captured page, derived from its normalized URL."""

    return "pg_" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
