"""Canonical provider boundary with deterministic fake and guarded live adapter."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import ssl
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Optional, Protocol
from urllib.parse import urlparse

from .errors import ProviderError
from .version import PLUGIN_VERSION


@dataclass(frozen=True)
class ProviderResponse:
    provider: str
    request_id: str
    cost_usd: float
    payload: dict[str, Any]


class KeywordProvider(Protocol):
    name: str

    def expand(self, seed_topic: str, *, market: str, language: str) -> ProviderResponse: ...
    def keyword_metrics(self, keywords: list[str], *, market: str, language: str) -> ProviderResponse: ...


class SerpProvider(Protocol):
    name: str

    def search(self, keyword: str, *, market: str, language: str, device: str) -> ProviderResponse: ...


class PageProvider(Protocol):
    name: str

    def inspect(self, url: str) -> ProviderResponse: ...


def _request_id(operation: str, value: object) -> str:
    material = json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return f"{operation}_{hashlib.sha256(material).hexdigest()[:16]}"


def _status_subject(subject: object) -> str:
    """Render the keyword(s) or topic a failed DataForSEO call was made for."""
    if isinstance(subject, str):
        return subject.strip()
    if isinstance(subject, (list, tuple)):
        return ", ".join(str(item).strip() for item in subject if str(item).strip())
    return ""


def _unsuccessful_status_message(
    operation: str,
    payload: dict[str, Any],
    response_task: object,
    subject: object = None,
) -> str:
    """Describe a non-20000 DataForSEO status without exposing credentials.

    DataForSEO reports the reason at the top level, the task level, or both.
    Keep the raw ``status_code``/``status_message`` and the keyword that
    triggered the failure so a live failure can be diagnosed instead of
    collapsing into a generic "unsuccessful status".
    """
    detail: list[str] = []
    if isinstance(payload, dict):
        if payload.get("status_code") is not None:
            detail.append(f"status_code={payload.get('status_code')}")
        top_message = str(payload.get("status_message") or "").strip()
        if top_message and top_message.lower() != "ok.":
            detail.append(f"status_message={top_message!r}")
    if isinstance(response_task, dict):
        if response_task.get("status_code") is not None:
            detail.append(f"task_status_code={response_task.get('status_code')}")
        task_message = str(response_task.get("status_message") or "").strip()
        if task_message and task_message.lower() != "ok.":
            detail.append(f"task_status_message={task_message!r}")
    subject_text = _status_subject(subject)
    if subject_text:
        detail.append(f"keyword={subject_text!r}")
    suffix = f" ({', '.join(detail)})" if detail else ""
    return f"DataForSEO {operation} returned an unsuccessful status{suffix}"


# DataForSEO's ``location_name`` requires a full location name, not a country
# code.  Normalize the common codes the Planner accepts; pass anything else
# through unchanged.
_LOCATION_NAMES = {
    "us": "United States",
    "usa": "United States",
    "united states": "United States",
    "uk": "United Kingdom",
    "gb": "United Kingdom",
    "united kingdom": "United Kingdom",
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
}


def _location_name(market: str) -> str:
    return _LOCATION_NAMES.get((market or "").strip().lower(), market)


def _domain(url: Any) -> str:
    host = (urlparse(str(url or "")).hostname or "").strip().lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _extract_aio(row: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize a DataForSEO ``ai_overview`` item into text + references."""

    texts: list[str] = []
    references: list[dict[str, str]] = []
    direct = row.get("text")
    if isinstance(direct, str) and direct.strip():
        texts.append(direct.strip())
    for sub in row.get("items") or []:
        if not isinstance(sub, dict):
            continue
        sub_text = sub.get("text")
        if isinstance(sub_text, str) and sub_text.strip():
            texts.append(sub_text.strip())
        for reference in sub.get("references") or []:
            if isinstance(reference, dict) and reference.get("url"):
                url = str(reference["url"])
                references.append(
                    {
                        "url": url,
                        "title": str(reference.get("title") or ""),
                        "domain": str(reference.get("domain") or _domain(url)),
                    }
                )
    return {"present": True, "text": " ".join(texts)[:2000], "references": references[:10]}


def _normalize_serp_items(keyword: str, items: Any) -> dict[str, Any]:
    """Keep organic, AI overview, people-also-ask and related-search items.

    The live adapters previously discarded everything except ``type ==
    "organic"``; this preserves the structured SERP surface the decision engine
    needs without persisting raw provider JSON.
    """

    organic: list[dict[str, Any]] = []
    paa: list[dict[str, str]] = []
    related: list[str] = []
    aio: dict[str, Any] | None = None
    for row in items or []:
        if not isinstance(row, dict):
            continue
        row_type = row.get("type")
        if row_type == "organic" and row.get("url"):
            url = str(row["url"])
            organic.append(
                {
                    "rank": row.get("rank_group") or row.get("rank_absolute"),
                    "url": url,
                    "title": str(row.get("title") or ""),
                    "type": str(row.get("type") or "organic"),
                    "domain": _domain(url),
                    "description": str(row.get("description") or "")[:300],
                }
            )
        elif row_type == "ai_overview":
            aio = _extract_aio(row)
        elif row_type == "people_also_ask":
            for sub in row.get("items") or []:
                if not isinstance(sub, dict):
                    continue
                question = sub.get("title") or sub.get("question")
                if question:
                    paa.append({"question": str(question), "answer": str(sub.get("answer") or "")[:500]})
        elif row_type == "related_searches":
            for sub in row.get("items") or []:
                if not isinstance(sub, dict):
                    continue
                label = sub.get("title") or sub.get("keyword")
                if label:
                    related.append(str(label))
    features = ["organic"] if organic else []
    if aio:
        features.append("ai_overview")
    if paa:
        features.append("people_also_ask")
    if related:
        features.append("related_searches")
    dominant = organic[0]["type"] if organic else "unknown"
    return {
        "keyword": keyword,
        "organic": organic,
        "aio": aio,
        "paa": paa,
        "related": related,
        "serp_features": features,
        "dominant_page_type": dominant,
    }


class FakeSeoProvider:
    """Repeatable, zero-cost data that exercises the full evidence pipeline."""

    name = "fake"

    def expand(self, seed_topic: str, *, market: str, language: str) -> ProviderResponse:
        root = " ".join(seed_topic.lower().split())
        keywords = [
            root,
            f"{root} strategy",
            f"{root} implementation",
            f"{root} use cases",
            f"{root} best practices",
            f"{root} platform comparison",
        ]
        return ProviderResponse(
            self.name, _request_id("expand", [root, market, language]), 0.0, {"keywords": keywords}
        )

    def keyword_metrics(self, keywords: list[str], *, market: str, language: str) -> ProviderResponse:
        rows: list[dict[str, Any]] = []
        for keyword in keywords:
            digest = int(hashlib.sha256(keyword.encode()).hexdigest()[:8], 16)
            rows.append(
                {
                    "keyword": keyword,
                    "volume": 180 + digest % 6200,
                    "difficulty": 18 + digest % 65,
                    "cpc": round(1.2 + (digest % 900) / 100, 2),
                    "intent": "commercial" if "comparison" in keyword else "informational",
                }
            )
        return ProviderResponse(
            self.name, _request_id("metrics", [keywords, market, language]), 0.0, {"keywords": rows}
        )

    def search(self, keyword: str, *, market: str, language: str, device: str) -> ProviderResponse:
        slug = keyword.replace(" ", "-")
        digest = int(hashlib.sha256(f"{keyword}:{market}".encode()).hexdigest()[:8], 16)
        types = ["guide", "article", "comparison", "video", "forum"]
        organic = [
            {
                "rank": index,
                "url": f"https://example-{index}.test/{slug}",
                "title": f"{keyword.title()} — guide {index}",
                "type": types[(digest + index) % len(types)],
                "domain": f"example-{index}.test",
                "description": f"A deterministic sample result for {keyword}.",
            }
            for index in range(1, 6)
        ]
        aio = (
            {
                "present": True,
                "text": f"A deterministic AI overview for {keyword}.",
                "references": [
                    {
                        "url": f"https://example-1.test/{slug}",
                        "title": f"{keyword.title()} — guide 1",
                        "domain": "example-1.test",
                    }
                ],
            }
            if digest % 2 == 0
            else None
        )
        paa = [
            {"question": f"What is {keyword}?", "answer": ""},
            {"question": f"How do teams implement {keyword}?", "answer": ""},
        ]
        related = [f"{keyword} tools", f"{keyword} examples"]
        features = ["organic", "people_also_ask", "related_searches"]
        if aio:
            features.append("ai_overview")
        return ProviderResponse(
            self.name,
            _request_id("serp", [keyword, market, language, device]),
            0.0,
            {
                "keyword": keyword,
                "organic": organic,
                "aio": aio,
                "paa": paa,
                "related": related,
                "serp_features": features,
                "dominant_page_type": organic[0]["type"],
                "weakness": "Deterministic sample SERP used only by offline tests.",
            },
        )

    def inspect(self, url: str) -> ProviderResponse:
        return ProviderResponse(
            self.name,
            _request_id("page", url),
            0.0,
            {
                "url": url,
                "page_type": "guide",
                "main_sections": ["definition", "benefits", "examples"],
                "coverage_gap": "implementation constraints and decision criteria are thin",
            },
        )


class DataForSeoProvider:
    """Minimum live DataForSEO adapter; never serializes or returns credentials.

    The caller must explicitly enable this provider. Environment variables are
    read only inside this adapter and are not included in exceptions, evidence,
    cache keys, or MCP responses.
    """

    name = "dataforseo"

    def __init__(
        self,
        *,
        enabled: bool = False,
        base_url: str = "https://api.dataforseo.com",
        timeout: float = 90.0,
        retries: int = 2,
        backoff: float = 1.0,
    ) -> None:
        self.enabled = enabled
        self.base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._retries = max(0, retries)
        self._backoff = backoff

    def connection_status(self) -> dict[str, str]:
        """Report readiness without exposing or testing credentials."""
        if not self.enabled or os.environ.get("TOPIC_PLANNER_ENABLE_LIVE_DATAFORSEO") != "1":
            return {"provider": self.name, "status": "DISABLED"}
        if not (os.environ.get("DATAFORSEO_LOGIN") and os.environ.get("DATAFORSEO_PASSWORD")):
            return {"provider": self.name, "status": "AUTH_REQUIRED"}
        return {"provider": self.name, "status": "READY"}

    def _post(self, path: str, task: dict[str, Any], operation: str) -> ProviderResponse:
        if not self.enabled or os.environ.get("TOPIC_PLANNER_ENABLE_LIVE_DATAFORSEO") != "1":
            raise ProviderError("live DataForSEO is disabled; use the fake provider or explicitly enable it")
        login = os.environ.get("DATAFORSEO_LOGIN")
        password = os.environ.get("DATAFORSEO_PASSWORD")
        if not login or not password:
            raise ProviderError("DataForSEO credentials are not configured")
        body = json.dumps([task]).encode("utf-8")
        auth = base64.b64encode(f"{login}:{password}".encode()).decode("ascii")
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            body,
            headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json"},
            method="POST",
        )
        payload: dict[str, Any] | None = None
        attempt = 0
        while True:
            try:
                with urllib.request.urlopen(request, timeout=self._timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as exc:
                if exc.code >= 500 and attempt < self._retries:
                    attempt += 1
                    time.sleep(self._backoff * attempt)
                    continue
                raise ProviderError(f"DataForSEO {operation} request failed (HTTPError)") from exc
            except (urllib.error.URLError, TimeoutError, ssl.SSLError) as exc:
                # Transient transport failure; a retry may duplicate a paid task,
                # but the run's budget abort bounds total exposure and the cache
                # prevents re-paying for calls that already returned evidence.
                if attempt < self._retries:
                    attempt += 1
                    time.sleep(self._backoff * attempt)
                    continue
                raise ProviderError(
                    f"DataForSEO {operation} request failed ({exc.__class__.__name__})"
                ) from exc
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise ProviderError(f"DataForSEO {operation} returned an invalid response") from exc
        tasks = payload.get("tasks") if isinstance(payload, dict) else None
        first = tasks[0] if isinstance(tasks, list) and tasks else {}
        if payload.get("status_code") != 20000 or first.get("status_code") != 20000:
            subject = (task.get("keyword") or task.get("keywords")) if isinstance(task, dict) else None
            raise ProviderError(_unsuccessful_status_message(operation, payload, first, subject))
        return ProviderResponse(
            self.name,
            _request_id(operation, task),
            float(first.get("cost") or 0),
            {"result": first.get("result") or []},
        )

    def expand(self, seed_topic: str, *, market: str, language: str) -> ProviderResponse:
        raw = self._post(
            "/v3/dataforseo_labs/google/keyword_suggestions/live",
            {
                "keyword": seed_topic,
                "location_name": _location_name(market),
                "language_code": language,
                "limit": 20,
            },
            "expand",
        )
        result = raw.payload.get("result") or []
        first = result[0] if result and isinstance(result[0], dict) else {}
        items = first.get("items") or []
        keywords = [str(row.get("keyword")) for row in items if isinstance(row, dict) and row.get("keyword")]
        return ProviderResponse(raw.provider, raw.request_id, raw.cost_usd, {"keywords": keywords})

    def keyword_metrics(self, keywords: list[str], *, market: str, language: str) -> ProviderResponse:
        raw = self._post(
            "/v3/keywords_data/google/search_volume/live",
            {
                "keywords": keywords,
                "location_name": _location_name(market),
                "language_code": language,
            },
            "keyword_metrics",
        )
        rows = [
            {
                "keyword": row.get("keyword"),
                "volume": row.get("search_volume") or 0,
                "difficulty": row.get("competition_index") or 0,
                "cpc": row.get("cpc") or 0,
                "intent": "unknown",
            }
            for row in raw.payload["result"]
            if row.get("keyword")
        ]
        return ProviderResponse(raw.provider, raw.request_id, raw.cost_usd, {"keywords": rows})

    def search(self, keyword: str, *, market: str, language: str, device: str) -> ProviderResponse:
        raw = self._post(
            "/v3/serp/google/organic/live/advanced",
            {
                "keyword": keyword,
                "location_name": _location_name(market),
                "language_code": language,
                "device": device,
                "depth": 10,
            },
            "search",
        )
        result = raw.payload["result"][0] if raw.payload["result"] else {}
        items = result.get("items") or []
        payload = _normalize_serp_items(keyword, items)
        payload["weakness"] = "Only SERP metadata was sampled; page content was not fetched."
        return ProviderResponse(raw.provider, raw.request_id, raw.cost_usd, payload)

    def inspect(self, url: str) -> ProviderResponse:
        raise ProviderError("live competitor-page inspection is intentionally not implemented in this MVP")


McpTransport = Callable[[dict[str, Any], Optional[str]], tuple[dict[str, Any], Mapping[str, str]]]


class DataForSeoMcpProvider:
    """Adapter for the official DataForSEO v3 remote MCP server.

    The Planner owns the OAuth connection (see ``oauth.py``) and supplies a
    short-lived access token through ``token_provider``; no password, refresh
    token, or long-lived secret is stored on this object.  This keeps
    credentials out of MCP arguments, persisted records, cache keys, and logs.

    The server is stateless and replies as Server-Sent Events; ``transport``
    exists only for offline contract tests.
    """

    name = "dataforseo_mcp"
    protocol_version = "2025-06-18"

    def __init__(
        self,
        *,
        endpoint: str | None = None,
        access_token: str | None = None,
        token_provider: Callable[[], str] | None = None,
        status_provider: Callable[[], Mapping[str, str]] | None = None,
        transport: McpTransport | None = None,
        timeout: float = 90.0,
        retries: int = 2,
        backoff: float = 1.0,
    ) -> None:
        self.endpoint = (
            endpoint or os.environ.get("DATAFORSEO_MCP_URL") or "https://mcp.dataforseo.com/v3/mcp"
        )
        self._access_token = (
            access_token if access_token is not None else os.environ.get("DATAFORSEO_MCP_ACCESS_TOKEN")
        )
        # A trusted local/Core connection can supply a fresh token on demand; the
        # lazy provider also refreshes an expired OAuth token.  Secrets are never
        # stored on this object beyond the process lifetime.
        self._token_provider = token_provider
        self._status_provider = status_provider
        self._transport = transport or self._http_transport
        self._timeout = timeout
        self._retries = max(0, retries)
        self._backoff = backoff
        self._session_id: str | None = None
        self._initialized = False

    def connection_status(self) -> dict[str, str]:
        """Return connection readiness without contacting a provider or exposing secrets."""
        parsed = urlparse(self.endpoint)
        is_local_http = parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        if (parsed.scheme != "https" and not is_local_http) or not parsed.netloc:
            return {"provider": self.name, "status": "MISCONFIGURED"}
        if self._status_provider is not None:
            return dict(self._status_provider())
        if not self._access_token:
            return {"provider": self.name, "status": "AUTH_REQUIRED"}
        return {"provider": self.name, "status": "READY"}

    def _current_token(self) -> str:
        if self._access_token is not None:
            return self._access_token
        if self._token_provider is not None:
            return self._token_provider()
        return ""

    def _require_ready(self) -> None:
        status = self.connection_status()["status"]
        if status == "AUTH_REQUIRED":
            raise ProviderError("DataForSEO MCP authorization is not connected")
        if status != "READY":
            raise ProviderError("DataForSEO MCP endpoint is misconfigured")

    def _http_transport(
        self, payload: dict[str, Any], session_id: str | None
    ) -> tuple[dict[str, Any], Mapping[str, str]]:
        self._require_ready()
        headers = {
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {self._current_token()}",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": self.protocol_version,
        }
        if session_id:
            headers["Mcp-Session-Id"] = session_id
        request = urllib.request.Request(
            self.endpoint, json.dumps(payload).encode("utf-8"), headers=headers, method="POST"
        )
        raw = ""
        response_headers: dict[str, Any] = {}
        attempt = 0
        while True:
            try:
                with urllib.request.urlopen(request, timeout=self._timeout) as response:
                    raw = response.read().decode("utf-8")
                    response_headers = dict(response.headers.items())
                break
            except urllib.error.HTTPError as exc:
                if exc.code >= 500 and attempt < self._retries:
                    attempt += 1
                    time.sleep(self._backoff * attempt)
                    continue
                raise ProviderError("DataForSEO MCP request failed (HTTPError)") from exc
            except (urllib.error.URLError, TimeoutError, ssl.SSLError) as exc:
                if attempt < self._retries:
                    attempt += 1
                    time.sleep(self._backoff * attempt)
                    continue
                raise ProviderError(f"DataForSEO MCP request failed ({exc.__class__.__name__})") from exc
            except UnicodeDecodeError as exc:
                raise ProviderError("DataForSEO MCP returned an invalid response") from exc
        if not raw.strip():
            return {}, response_headers
        # The official server replies as Server-Sent Events, where the JSON body
        # follows one or more ``event:``/``data:`` lines.  Extract the last data
        # payload; a plain JSON response is left untouched.
        data_lines = []
        for line in raw.splitlines():
            candidate = line.lstrip()
            if candidate.startswith("data:"):
                data_lines.append(candidate[len("data:") :].strip())
        if data_lines:
            raw = data_lines[-1]
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProviderError("DataForSEO MCP returned an invalid response") from exc
        if not isinstance(parsed, dict):
            raise ProviderError("DataForSEO MCP returned an invalid response")
        return parsed, response_headers

    def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        response, _ = self._transport(payload, self._session_id)
        error = response.get("error")
        if error:
            raise ProviderError("DataForSEO MCP rejected the request")
        result = response.get("result")
        if not isinstance(result, dict):
            raise ProviderError("DataForSEO MCP returned an invalid result")
        return result

    def _ensure_session(self) -> None:
        if self._initialized:
            return
        self._require_ready()
        response, headers = self._transport(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": self.protocol_version,
                    "capabilities": {},
                    "clientInfo": {"name": "seo-topic-planner", "version": PLUGIN_VERSION},
                },
            },
            None,
        )
        if response.get("error") or not isinstance(response.get("result"), dict):
            raise ProviderError("DataForSEO MCP initialization failed")
        # The official server is stateless and omits ``Mcp-Session-Id``; that is
        # valid per the streamable-HTTP spec, so a missing id is not an error.
        self._session_id = headers.get("Mcp-Session-Id") or headers.get("mcp-session-id")
        self._initialized = True
        # Notifications intentionally have no response; a compliant transport can
        # return an empty object for this call.
        self._transport(
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}, self._session_id
        )

    @staticmethod
    def _content_json(result: dict[str, Any]) -> dict[str, Any]:
        content = result.get("content")
        if not isinstance(content, list):
            raise ProviderError("DataForSEO MCP returned no tool content")
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
                try:
                    value = json.loads(block["text"])
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    return value
        raise ProviderError("DataForSEO MCP returned unusable tool content")

    def _api_request(self, path: str, data: list[dict[str, Any]]) -> dict[str, Any]:
        self._ensure_session()
        result = self._request(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "api_request",
                    "arguments": {"method": "POST", "path": path, "data": data, "noAiMode": True},
                },
            }
        )
        return self._content_json(result)

    @staticmethod
    def _task(
        payload: dict[str, Any], operation: str, subject: object = None
    ) -> tuple[dict[str, Any], float]:
        tasks = payload.get("tasks") if isinstance(payload, dict) else None
        first = tasks[0] if isinstance(tasks, list) and tasks and isinstance(tasks[0], dict) else {}
        if payload.get("status_code") != 20000 or first.get("status_code") != 20000:
            raise ProviderError(_unsuccessful_status_message(operation, payload, first, subject))
        return first, float(first.get("cost") or 0)

    def expand(self, seed_topic: str, *, market: str, language: str) -> ProviderResponse:
        payload = self._api_request(
            "/v3/dataforseo_labs/google/keyword_suggestions/live",
            [
                {
                    "keyword": seed_topic,
                    "location_name": _location_name(market),
                    "language_code": language,
                    "limit": 20,
                }
            ],
        )
        task, cost = self._task(payload, "keyword expansion", seed_topic)
        rows = task.get("result") or []
        first = rows[0] if rows and isinstance(rows[0], dict) else {}
        items = first.get("items") or []
        keywords = [str(row.get("keyword")) for row in items if isinstance(row, dict) and row.get("keyword")]
        return ProviderResponse(
            self.name, _request_id("expand", [seed_topic, market, language]), cost, {"keywords": keywords}
        )

    def keyword_metrics(self, keywords: list[str], *, market: str, language: str) -> ProviderResponse:
        payload = self._api_request(
            "/v3/keywords_data/google/search_volume/live",
            [
                {
                    "keywords": keywords,
                    "location_name": _location_name(market),
                    "language_code": language,
                }
            ],
        )
        task, cost = self._task(payload, "keyword metrics", keywords)
        rows = task.get("result") or []
        metrics = [
            {
                "keyword": row.get("keyword"),
                "volume": row.get("search_volume") or 0,
                "difficulty": row.get("competition_index") or 0,
                "cpc": row.get("cpc") or 0,
                "intent": "unknown",
            }
            for row in rows
            if isinstance(row, dict) and row.get("keyword")
        ]
        return ProviderResponse(
            self.name, _request_id("metrics", [keywords, market, language]), cost, {"keywords": metrics}
        )

    def search(self, keyword: str, *, market: str, language: str, device: str) -> ProviderResponse:
        payload = self._api_request(
            "/v3/serp/google/organic/live/advanced",
            [
                {
                    "keyword": keyword,
                    "location_name": _location_name(market),
                    "language_code": language,
                    "device": device,
                    "depth": 10,
                }
            ],
        )
        task, cost = self._task(payload, "SERP", keyword)
        result = (task.get("result") or [{}])[0]
        items = result.get("items") if isinstance(result, dict) else []
        normalized = _normalize_serp_items(keyword, items)
        normalized["weakness"] = "Only SERP metadata was sampled; page content was not fetched."
        return ProviderResponse(
            self.name,
            _request_id("serp", [keyword, market, language, device]),
            cost,
            normalized,
        )

    def inspect(self, url: str) -> ProviderResponse:
        raise ProviderError("competitor page content is not fetched by the DataForSEO MCP provider")
