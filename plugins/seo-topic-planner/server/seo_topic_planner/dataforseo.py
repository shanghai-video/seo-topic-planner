"""DataForSEO client: one request shape, two transports.

* ``McpTransport`` calls the official DataForSEO remote MCP server with the
  Planner's own OAuth token (the default for installed copies).
* ``RestTransport`` calls the REST API with ``DATAFORSEO_LOGIN`` /
  ``DATAFORSEO_PASSWORD`` from the environment, for local or CI use.

Both return the raw DataForSEO response envelope.  ``DataForSeoClient`` builds
each endpoint's task and normalizes the result; the service decides whether a
call may be made (budget, cache) before it reaches this module.
"""

from __future__ import annotations

import base64
import json
import os
import ssl
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from .domains import host_of
from .errors import ProviderError
from .metrics import labs_keyword_metrics
from .version import PLUGIN_VERSION

MCP_ENDPOINT = "https://mcp.dataforseo.com/v3/mcp"
REST_BASE = "https://api.dataforseo.com"

_LOCATIONS = {
    "us": "United States",
    "usa": "United States",
    "uk": "United Kingdom",
    "gb": "United Kingdom",
    "ca": "Canada",
    "au": "Australia",
    "de": "Germany",
    "fr": "France",
    "es": "Spain",
    "it": "Italy",
    "nl": "Netherlands",
    "in": "India",
    "jp": "Japan",
    "br": "Brazil",
    "mx": "Mexico",
    "sg": "Singapore",
    "ae": "United Arab Emirates",
    "ie": "Ireland",
    "nz": "New Zealand",
}


def location_name(market: str) -> str:
    return _LOCATIONS.get(market.strip().lower(), market.strip())


@dataclass(frozen=True)
class Call:
    """One billable DataForSEO task.  ``key`` identifies it for cache and ledger."""

    operation: str
    path: str
    task: dict[str, Any]

    @property
    def key(self) -> dict[str, Any]:
        return {"operation": self.operation, "path": self.path, "task": self.task}


class Transport(Protocol):
    name: str

    def status(self) -> str: ...
    def post(self, path: str, task: dict[str, Any]) -> dict[str, Any]: ...


def _http(request: urllib.request.Request, *, timeout: float, retries: int = 2) -> str:
    attempt = 0
    while True:
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            if exc.code >= 500 and attempt < retries:
                attempt += 1
                time.sleep(attempt)
                continue
            raise ProviderError(
                f"DataForSEO request failed (HTTP {exc.code})", cost_uncertain=exc.code >= 500
            ) from exc
        except (urllib.error.URLError, TimeoutError, ssl.SSLError, UnicodeDecodeError) as exc:
            if attempt < retries:
                attempt += 1
                time.sleep(attempt)
                continue
            raise ProviderError(
                f"DataForSEO request failed ({exc.__class__.__name__})", cost_uncertain=True
            ) from exc


class RestTransport:
    name = "dataforseo_rest"

    def __init__(self, base_url: str = REST_BASE, timeout: float = 120.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def status(self) -> str:
        if os.environ.get("DATAFORSEO_LOGIN") and os.environ.get("DATAFORSEO_PASSWORD"):
            return "READY"
        return "AUTH_REQUIRED"

    def post(self, path: str, task: dict[str, Any]) -> dict[str, Any]:
        login = os.environ.get("DATAFORSEO_LOGIN")
        password = os.environ.get("DATAFORSEO_PASSWORD")
        if not login or not password:
            raise ProviderError("DataForSEO API credentials are not configured")
        auth = base64.b64encode(f"{login}:{password}".encode()).decode("ascii")
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            json.dumps([task]).encode("utf-8"),
            headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            payload = json.loads(_http(request, timeout=self.timeout))
        except json.JSONDecodeError as exc:
            raise ProviderError("DataForSEO returned an invalid response", cost_uncertain=True) from exc
        if not isinstance(payload, dict):
            raise ProviderError("DataForSEO returned an invalid response", cost_uncertain=True)
        return payload


class McpTransport:
    """Streamable-HTTP client for DataForSEO's official MCP ``api_request`` tool."""

    name = "dataforseo_mcp"
    protocol_version = "2025-06-18"

    def __init__(
        self,
        token_provider: Callable[[], str],
        status_provider: Callable[[], str],
        endpoint: str | None = None,
        timeout: float = 120.0,
    ) -> None:
        self._token = token_provider
        self._status = status_provider
        self.endpoint = endpoint or os.environ.get("DATAFORSEO_MCP_URL") or MCP_ENDPOINT
        self.timeout = timeout
        self._session: str | None = None
        self._initialized = False
        self._lock = threading.Lock()

    def status(self) -> str:
        return self._status()

    def _rpc(self, payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {self._token()}",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": self.protocol_version,
        }
        if self._session:
            headers["Mcp-Session-Id"] = self._session
        request = urllib.request.Request(
            self.endpoint, json.dumps(payload).encode("utf-8"), headers=headers, method="POST"
        )
        response_headers: dict[str, str] = {}
        attempt = 0
        while True:
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    raw = response.read().decode("utf-8")
                    response_headers = {k.lower(): v for k, v in response.headers.items()}
                break
            except urllib.error.HTTPError as exc:
                if exc.code >= 500 and attempt < 2:
                    attempt += 1
                    time.sleep(attempt)
                    continue
                raise ProviderError(
                    f"DataForSEO MCP request failed (HTTP {exc.code})", cost_uncertain=exc.code >= 500
                ) from exc
            except (urllib.error.URLError, TimeoutError, ssl.SSLError, UnicodeDecodeError) as exc:
                if attempt < 2:
                    attempt += 1
                    time.sleep(attempt)
                    continue
                raise ProviderError(
                    f"DataForSEO MCP request failed ({exc.__class__.__name__})", cost_uncertain=True
                ) from exc
        if not raw.strip():
            return {}, response_headers
        data_lines = [
            line.strip()[5:].strip() for line in raw.splitlines() if line.strip().startswith("data:")
        ]
        try:
            parsed = json.loads(data_lines[-1] if data_lines else raw)
        except json.JSONDecodeError as exc:
            raise ProviderError("DataForSEO MCP returned an invalid response", cost_uncertain=True) from exc
        return (parsed if isinstance(parsed, dict) else {}), response_headers

    def _ensure_session(self) -> None:
        with self._lock:
            if not self._initialized:
                self._initialize()

    def _initialize(self) -> None:
        response, headers = self._rpc(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": self.protocol_version,
                    "capabilities": {},
                    "clientInfo": {"name": "seo-topic-planner", "version": PLUGIN_VERSION},
                },
            }
        )
        if response.get("error") or not isinstance(response.get("result"), dict):
            raise ProviderError("DataForSEO MCP initialization failed")
        self._session = headers.get("mcp-session-id")
        self._initialized = True
        self._rpc({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

    def post(self, path: str, task: dict[str, Any]) -> dict[str, Any]:
        if self.status() != "READY":
            raise ProviderError("DataForSEO is not connected")
        self._ensure_session()
        response, _ = self._rpc(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "api_request",
                    "arguments": {"method": "POST", "path": path, "data": [task], "noAiMode": True},
                },
            }
        )
        if response.get("error"):
            raise ProviderError("DataForSEO MCP rejected the request")
        result = response.get("result")
        content = result.get("content") if isinstance(result, dict) else None
        for block in content or []:
            if isinstance(block, dict) and block.get("type") == "text":
                try:
                    value = json.loads(block.get("text") or "")
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    return value
        raise ProviderError("DataForSEO MCP returned unusable content", cost_uncertain=True)


def task_result(payload: dict[str, Any], operation: str) -> tuple[list[Any], float]:
    """Return ``(result, cost)`` of the single task, or raise on a failed status."""

    tasks = payload.get("tasks") if isinstance(payload, dict) else None
    task = tasks[0] if isinstance(tasks, list) and tasks and isinstance(tasks[0], dict) else {}
    if payload.get("status_code") != 20000 or task.get("status_code") != 20000:
        detail = task.get("status_message") or payload.get("status_message") or "unknown error"
        raise ProviderError(f"DataForSEO {operation} failed: {str(detail)[:200]}")
    return list(task.get("result") or []), float(task.get("cost") or 0.0)


# --- calls ----------------------------------------------------------------------


def _base(market: str, language: str) -> dict[str, Any]:
    return {"location_name": location_name(market), "language_code": language}


def keyword_suggestions(seed: str, market: str, language: str, limit: int) -> Call:
    return Call(
        "keyword_suggestions",
        "/v3/dataforseo_labs/google/keyword_suggestions/live",
        {
            "keyword": seed,
            **_base(market, language),
            "include_seed_keyword": True,
            "limit": limit,
            "order_by": ["keyword_info.search_volume,desc"],
        },
    )


def related_keywords(seed: str, market: str, language: str, limit: int) -> Call:
    return Call(
        "related_keywords",
        "/v3/dataforseo_labs/google/related_keywords/live",
        {
            "keyword": seed,
            **_base(market, language),
            "depth": 2,
            "include_seed_keyword": True,
            "limit": limit,
        },
    )


def ranked_keywords(domain: str, market: str, language: str, limit: int) -> Call:
    return Call(
        "ranked_keywords",
        "/v3/dataforseo_labs/google/ranked_keywords/live",
        {
            "target": host_of(domain) or domain,
            **_base(market, language),
            "limit": limit,
            "item_types": ["organic"],
            "filters": [["ranked_serp_element.serp_item.rank_group", "<=", 20]],
            "order_by": ["keyword_data.keyword_info.search_volume,desc"],
        },
    )


def serp(keyword: str, market: str, language: str) -> Call:
    return Call(
        "serp",
        "/v3/serp/google/organic/live/advanced",
        {"keyword": keyword, **_base(market, language), "device": "desktop", "depth": 10},
    )


# --- normalization -----------------------------------------------------------------


def keyword_rows(operation: str, result: list[Any]) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Flatten a Labs response into metric rows plus the seed's own row."""

    rows: list[dict[str, Any]] = []
    seed_row: dict[str, Any] | None = None
    for block in result:
        if not isinstance(block, dict):
            continue
        seed_data = block.get("seed_keyword_data")
        if isinstance(seed_data, dict) and block.get("seed_keyword"):
            seed_row = labs_keyword_metrics({"keyword": block.get("seed_keyword"), **seed_data})
        for item in block.get("items") or []:
            if not isinstance(item, dict):
                continue
            source = item.get("keyword_data") if isinstance(item.get("keyword_data"), dict) else item
            if not source.get("keyword"):
                continue
            row = labs_keyword_metrics(source)
            if operation == "ranked_keywords":
                serp_item = (item.get("ranked_serp_element") or {}).get("serp_item") or {}
                row["competitor_rank"] = serp_item.get("rank_group")
                row["competitor_url"] = serp_item.get("url")
            rows.append(row)
    return rows, seed_row


def serp_snapshot(keyword: str, result: list[Any]) -> dict[str, Any]:
    block = result[0] if result and isinstance(result[0], dict) else {}
    organic: list[dict[str, Any]] = []
    paa: list[str] = []
    related: list[str] = []
    features: list[str] = []
    aio_refs: list[str] = []
    for item in block.get("items") or []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or "")
        if kind and kind not in features:
            features.append(kind)
        if kind == "organic" and item.get("url"):
            organic.append(
                {
                    "rank": item.get("rank_group"),
                    "url": str(item["url"]),
                    "domain": host_of(item["url"]),
                    "title": str(item.get("title") or "")[:200],
                    "snippet": str(item.get("description") or "")[:300],
                }
            )
        elif kind == "people_also_ask":
            for sub in item.get("items") or []:
                if isinstance(sub, dict) and (sub.get("title") or sub.get("question")):
                    paa.append(str(sub.get("title") or sub.get("question")))
        elif kind == "related_searches":
            for sub in item.get("items") or []:
                label = (
                    sub
                    if isinstance(sub, str)
                    else (sub or {}).get("title")
                    if isinstance(sub, dict)
                    else None
                )
                if label:
                    related.append(str(label))
        elif kind == "ai_overview":
            for sub in item.get("items") or []:
                for reference in (sub or {}).get("references") or [] if isinstance(sub, dict) else []:
                    if isinstance(reference, dict) and reference.get("url"):
                        aio_refs.append(str(reference["url"]))
            for reference in item.get("references") or []:
                if isinstance(reference, dict) and reference.get("url"):
                    aio_refs.append(str(reference["url"]))
    return {
        "keyword": keyword,
        "organic": organic[:10],
        "paa": list(dict.fromkeys(paa))[:12],
        "related": list(dict.fromkeys(related))[:12],
        "features": features,
        "ai_overview_refs": list(dict.fromkeys(aio_refs))[:10],
    }
