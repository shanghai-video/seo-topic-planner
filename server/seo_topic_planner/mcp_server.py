"""Dependency-free stdio MCP server for the local SEO Topic Planner plugin.

It implements the small MCP surface that represents user actions, rather than
leaking provider endpoints.  Protocol traffic is stdout-only; diagnostics are
deliberately omitted so a host never parses logs as MCP messages.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Callable

from .errors import TopicPlannerError
from .oauth import DataForSeoConnection, TokenStore
from .providers import DataForSeoMcpProvider, DataForSeoProvider
from .service import TopicPlannerService
from .version import PLUGIN_VERSION


def _schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


def _tool(
    name: str,
    description: str,
    properties: dict[str, Any],
    required: list[str],
    *,
    title: str,
    read_only: bool,
    open_world: bool,
    destructive: bool = False,
    idempotent: bool = False,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an MCP tool with explicit, review-visible behavior hints."""
    annotations: dict[str, Any] = {
        "title": title,
        "readOnlyHint": read_only,
        "idempotentHint": idempotent,
        "openWorldHint": open_world,
    }
    if not read_only:
        # destructiveHint is meaningful only for writing tools; default is true,
        # so additive writers must state it explicitly.
        annotations["destructiveHint"] = destructive
    tool = {
        "name": name,
        "description": description,
        "inputSchema": _schema(properties, required),
        "annotations": annotations,
    }
    if meta is not None:
        tool["_meta"] = meta
    return tool


STRING = {"type": "string", "minLength": 1}
ARRAY = {"type": "array", "items": {"type": "string"}}
_UI_ROOT = Path(__file__).resolve().parents[2] / "ui"
# MCP App resources render as interactive cards in hosts that support them.
# Every tool below still returns complete structured JSON, so the Planner is
# fully usable in a text-only host as well.
APP_RESOURCES: dict[str, dict[str, Any]] = {
    "ui://seo-topic-planner/connection-settings": {
        "name": "DataForSEO connection settings",
        "path": _UI_ROOT / "connection-settings.html",
        "meta": {
            "openai/ui": {"availableDisplayModes": ["fullscreen"], "preferredDisplayMode": "fullscreen"}
        },
    },
    "ui://seo-topic-planner/brand-context": {
        "name": "Brand context confirmation",
        "path": _UI_ROOT / "brand-context.html",
        "meta": {
            "openai/ui": {"availableDisplayModes": ["inline", "fullscreen"], "preferredDisplayMode": "inline"}
        },
    },
    "ui://seo-topic-planner/brand-entity": {
        "name": "Brand entity confirmation",
        "path": _UI_ROOT / "brand-entity.html",
        "meta": {
            "openai/ui": {"availableDisplayModes": ["inline", "fullscreen"], "preferredDisplayMode": "inline"}
        },
    },
}
CONNECTION_RESOURCE_URI = "ui://seo-topic-planner/connection-settings"
BRAND_CONTEXT_RESOURCE_URI = "ui://seo-topic-planner/brand-context"
BRAND_ENTITY_RESOURCE_URI = "ui://seo-topic-planner/brand-entity"


def _app_resource(uri: str) -> dict[str, Any]:
    resource = APP_RESOURCES[uri]
    return {
        "uri": uri,
        "name": resource["name"],
        "mimeType": "text/html;profile=mcp-app",
        "_meta": resource["meta"],
    }


TOOLS: list[dict[str, Any]] = [
    _tool(
        "open_connection_settings",
        "Open the DataForSEO connection guide and read-only status. Only the user can approve authorization.",
        {"workspace_id": {"type": "string"}},
        [],
        title="Open DataForSEO connection settings",
        read_only=True,
        open_world=False,
        meta={
            "ui": {"resourceUri": CONNECTION_RESOURCE_URI},
            "openai/ui": {"entrypoints": [{"type": "settings"}, {"type": "thread"}]},
        },
    ),
    _tool(
        "open_brand_context_card",
        "Open the brand-context card so the user can confirm the target market, language and the "
        "product's core features, plus ICP and business anchors. Use it the first time a website is "
        "seen (no remembered context) or to review a remembered one. Pass any scrape-derived "
        "suggestions; nothing is assumed and no research or paid call occurs.",
        {
            "workspace_id": STRING,
            "website": STRING,
            "suggestions": {
                "type": "object",
                "description": "Optional scrape-derived field suggestions; the user edits and confirms.",
            },
        },
        ["workspace_id", "website"],
        title="Brand context card",
        read_only=True,
        open_world=False,
        meta={"ui": {"resourceUri": BRAND_CONTEXT_RESOURCE_URI}},
    ),
    _tool(
        "create_brand_profile",
        "Create a DRAFT brand context, or reuse/update the one remembered for this website. "
        "The context stays DRAFT until the user explicitly confirms the target market, language, "
        "core features, ICP and business anchors, normally through the brand-context card. No "
        "external research or paid calls occur.",
        {
            "workspace_id": STRING,
            "name": STRING,
            "website": STRING,
            "product": {"type": "string"},
            "target_customer": {"type": "string"},
            "target_market": {"type": "string"},
            "language": {"type": "string"},
            "positioning": {"type": "string"},
            "primary_scenario": {"type": "string"},
            "main_business": {"type": "string"},
            "core_features": ARRAY,
            "competitors": ARRAY,
            "business_goals": ARRAY,
            "seed_topics": ARRAY,
            "site_pages": {"type": "array", "items": {"type": "object"}},
            "update_existing": {"type": "boolean"},
        },
        ["workspace_id", "name", "website"],
        title="Create brand profile",
        read_only=False,
        open_world=False,
    ),
    _tool(
        "get_brand_profile",
        "Read one brand profile in the caller's workspace.",
        {"workspace_id": STRING, "brand_id": STRING},
        ["workspace_id", "brand_id"],
        title="Get brand profile",
        read_only=True,
        open_world=False,
    ),
    _tool(
        "get_brand_profile_by_website",
        "Look up the brand context and site snapshot remembered for a website host, without creating it.",
        {"workspace_id": STRING, "website": STRING},
        ["workspace_id", "website"],
        title="Get brand profile by website",
        read_only=True,
        open_world=False,
    ),
    _tool(
        "confirm_brand_context",
        "Explicitly confirm a DRAFT brand context (target market, language, ICP, positioning, "
        "main business, primary scenario and core features). Only a user-confirmed context can be "
        "researched.",
        {
            "workspace_id": STRING,
            "brand_id": STRING,
            "target_market": STRING,
            "language": STRING,
            "target_customer": STRING,
            "positioning": STRING,
            "primary_scenario": STRING,
            "main_business": STRING,
            "core_features": ARRAY,
        },
        [
            "workspace_id",
            "brand_id",
            "target_market",
            "language",
            "target_customer",
            "positioning",
            "primary_scenario",
            "main_business",
            "core_features",
        ],
        title="Confirm brand context",
        read_only=False,
        open_world=False,
    ),
    _tool(
        "open_brand_entity_card",
        "Open the brand entity card so the user can confirm which organization, brand, product and "
        "concept entities are real for this website. Pass structured candidate entities and external "
        "profiles the host agent found; nothing is written and the Planner never researches the open "
        "web itself.",
        {
            "workspace_id": STRING,
            "website": {"type": "string"},
            "brand_id": {"type": "string"},
            "suggestions": {
                "type": "object",
                "description": "Optional candidate entities and external profiles for the user to confirm.",
            },
        },
        ["workspace_id"],
        title="Brand entity card",
        read_only=True,
        open_world=False,
        meta={"ui": {"resourceUri": BRAND_ENTITY_RESOURCE_URI}},
    ),
    _tool(
        "create_brand_entity",
        "Create or reuse one typed brand entity (organization, brand, product, sub_brand, technology "
        "or concept). Separate entities model the company, the brand and its products. Same-named "
        "entities under different brands are never merged, and only genuinely verified URLs may be "
        "recorded as external profiles.",
        {
            "workspace_id": STRING,
            "brand_id": STRING,
            "canonical_name": STRING,
            "entity_type": {"type": "string"},
            "aliases": ARRAY,
            "legal_name": {"type": "string"},
            "official_website": {"type": "string"},
            "official_product_urls": ARRAY,
            "description": {"type": "string"},
            "disambiguation": {"type": "string"},
            "parent_entity_id": {"type": "string"},
            "related_entity_ids": ARRAY,
            "verified_external_profiles": {"type": "array", "items": {"type": "object"}},
            "candidate_external_profiles": {"type": "array", "items": {"type": "object"}},
            "notes": {"type": "string"},
            "update_existing": {"type": "boolean"},
        },
        ["workspace_id", "brand_id", "canonical_name"],
        title="Create brand entity",
        read_only=False,
        open_world=False,
    ),
    _tool(
        "update_brand_entity",
        "Update one brand entity by id. Unspecified fields are preserved; identity_status stays as it "
        "was until the user confirms the entity.",
        {
            "workspace_id": STRING,
            "entity_id": STRING,
            "canonical_name": {"type": "string"},
            "entity_type": {"type": "string"},
            "aliases": ARRAY,
            "legal_name": {"type": "string"},
            "official_website": {"type": "string"},
            "official_product_urls": ARRAY,
            "description": {"type": "string"},
            "disambiguation": {"type": "string"},
            "parent_entity_id": {"type": "string"},
            "related_entity_ids": ARRAY,
            "verified_external_profiles": {"type": "array", "items": {"type": "object"}},
            "candidate_external_profiles": {"type": "array", "items": {"type": "object"}},
            "notes": {"type": "string"},
        },
        ["workspace_id", "entity_id"],
        title="Update brand entity",
        read_only=False,
        open_world=False,
        idempotent=True,
    ),
    _tool(
        "confirm_brand_entity",
        "User-confirm one brand entity's identity. Only an external profile the user has verified as "
        "the same entity may be recorded; candidate leads are never promoted automatically, so a "
        "sameAs set stays empty until a real URL is verified.",
        {
            "workspace_id": STRING,
            "entity_id": STRING,
            "official_website": {"type": "string"},
            "legal_name": {"type": "string"},
            "aliases": ARRAY,
            "verified_external_profiles": {"type": "array", "items": {"type": "object"}},
        },
        ["workspace_id", "entity_id"],
        title="Confirm brand entity",
        read_only=False,
        open_world=False,
    ),
    _tool(
        "get_brand_entity",
        "Read one brand entity record.",
        {"workspace_id": STRING, "entity_id": STRING},
        ["workspace_id", "entity_id"],
        title="Get brand entity",
        read_only=True,
        open_world=False,
    ),
    _tool(
        "list_brand_entities",
        "List the typed brand entities recorded for one brand profile.",
        {"workspace_id": STRING, "brand_id": STRING},
        ["workspace_id", "brand_id"],
        title="List brand entities",
        read_only=True,
        open_world=False,
    ),
    _tool(
        "get_brand_entity_recommendations",
        "Read evidence-carrying brand entity building actions: each item states the observed issue, "
        "the observed evidence, the recommended action, target page or channel, priority, confidence, "
        "objective and validation method. These are entity-establishment tasks, not keyword opportunities.",
        {"workspace_id": STRING, "brand_id": STRING},
        ["workspace_id", "brand_id"],
        title="Brand entity recommendations",
        read_only=True,
        open_world=False,
    ),
    _tool(
        "record_site_pages",
        "Store page titles and summaries the host agent captured from the user's own site, so "
        "existing-content coverage can be computed. Never submit full page bodies.",
        {
            "workspace_id": STRING,
            "brand_id": STRING,
            "pages": {"type": "array", "items": {"type": "object"}},
            "capture_scope": {"type": "string"},
        },
        ["workspace_id", "brand_id", "pages"],
        title="Record site pages",
        read_only=False,
        open_world=False,
    ),
    _tool(
        "get_data_provider_status",
        "Read the selected provider's readiness without making a provider request or exposing credentials.",
        {"workspace_id": STRING},
        ["workspace_id"],
        title="Get data provider status",
        read_only=True,
        open_world=False,
    ),
    _tool(
        "connect_dataforseo",
        "Start the local DataForSEO OAuth connection. Returns an authorization URL for the user "
        "to approve in a browser; this Plugin never accepts a token or password as an argument.",
        {"workspace_id": STRING},
        ["workspace_id"],
        title="Connect DataForSEO",
        read_only=False,
        open_world=True,
    ),
    _tool(
        "complete_dataforseo_connection",
        "Wait for the user to finish DataForSEO authorization, then store the token locally "
        "and enable live data.",
        {"workspace_id": STRING, "timeout_seconds": {"type": "number", "minimum": 1, "maximum": 900}},
        ["workspace_id"],
        title="Complete DataForSEO connection",
        read_only=False,
        open_world=True,
    ),
    _tool(
        "disconnect_dataforseo",
        "Delete the locally stored DataForSEO authorization. After this the Planner has no data "
        "provider and every data-dependent action reports AUTH_REQUIRED until reconnected.",
        {"workspace_id": STRING},
        ["workspace_id"],
        title="Disconnect DataForSEO",
        read_only=False,
        open_world=False,
        destructive=True,
        idempotent=True,
    ),
    _tool(
        "estimate_topic_research",
        "Create a visible DataForSEO call and cost estimate for a confirmed brand context. This "
        "action does not run research; the returned confirmation token is required to spend.",
        {
            "workspace_id": STRING,
            "brand_id": STRING,
            "seed_topics": ARRAY,
            "market": {"type": "string"},
            "language": {"type": "string"},
            "device": {"type": "string"},
            "budget_cap_usd": {"type": "number", "minimum": 0},
        },
        ["workspace_id", "brand_id"],
        title="Estimate topic research",
        read_only=False,
        open_world=False,
    ),
    _tool(
        "run_topic_research",
        "Run confirmed keyword, SERP, and sampled competitor research, then return "
        "evidence-backed prioritized opportunities with an explainable content gap. This may "
        "incur the previously estimated provider cost and aborts if actual cost exceeds the "
        "approved budget. Requires a connected DataForSEO account.",
        {"workspace_id": STRING, "research_run_id": STRING, "confirmation_token": STRING},
        ["workspace_id", "research_run_id", "confirmation_token"],
        title="Run topic research",
        read_only=False,
        open_world=True,
    ),
    _tool(
        "record_source_observations",
        "Write back viewpoints the host agent gathered from browser / Computer Use / yt-dlp sources "
        "(rendered SERP results, competitor pages, community threads/videos, official documentation, "
        "GitHub issues, product docs, industry analysis, case studies, company profiles). Each "
        "observation needs a source, fetch time, a short viewpoint and 1-3 quotes, plus optional "
        "credibility, relevance and research_purpose. Full text, usernames and comment bodies are "
        "rejected. Re-submitting the same viewpoint is idempotent.",
        {
            "workspace_id": STRING,
            "research_run_id": {"type": "string"},
            "observations": {"type": "array", "items": {"type": "object"}},
        },
        ["workspace_id", "observations"],
        title="Record source observations",
        read_only=False,
        open_world=False,
        idempotent=True,
    ),
    _tool(
        "list_source_observations",
        "List the host-agent viewpoint observations stored for a workspace or research run.",
        {"workspace_id": STRING, "research_run_id": {"type": "string"}},
        ["workspace_id"],
        title="List source observations",
        read_only=True,
        open_world=False,
    ),
    _tool(
        "record_source_attempts",
        "Record external sources the host agent tried but could not read (a blocked browser session, "
        "a 403, an unavailable tool). Each attempt needs a source type, url, read method "
        "(browser|http|jina|yt-dlp|other), outcome (blocked|not_available|gated), a reason and "
        "attempted_at. Attempts are honest, visible gaps and never count as research evidence; they "
        "are reported by get_cluster_readiness and by the create_content_cluster refusal. "
        "Idempotent.",
        {
            "workspace_id": STRING,
            "research_run_id": {"type": "string"},
            "attempts": {"type": "array", "items": {"type": "object"}},
        },
        ["workspace_id", "attempts"],
        title="Record source attempts",
        read_only=False,
        open_world=False,
        idempotent=True,
    ),
    _tool(
        "list_source_attempts",
        "List the external sources the host agent tried but could not read for a workspace or run.",
        {"workspace_id": STRING, "research_run_id": {"type": "string"}},
        ["workspace_id"],
        title="List source attempts",
        read_only=True,
        open_world=False,
    ),
    _tool(
        "record_topical_entities",
        "Write back the topic entities and their relationships the host agent found for a research "
        "run. Entities are idempotent by canonical name; relations reference entities by name and "
        "carry evidence, confidence and a verification status. Inferred relationships are never "
        "treated as verified facts.",
        {
            "workspace_id": STRING,
            "research_run_id": STRING,
            "entities": {"type": "array", "items": {"type": "object"}},
            "relations": {"type": "array", "items": {"type": "object"}},
        },
        ["workspace_id", "research_run_id", "entities"],
        title="Record topical entities",
        read_only=False,
        open_world=False,
        idempotent=True,
    ),
    _tool(
        "list_topical_entities",
        "List the topic entities and relationships recorded for a research run.",
        {"workspace_id": STRING, "research_run_id": STRING},
        ["workspace_id", "research_run_id"],
        title="List topical entities",
        read_only=True,
        open_world=False,
    ),
    _tool(
        "record_opportunity_analysis",
        "Write back the host agent's semantic judgement for one opportunity: semantic coverage "
        "(intent/entity/relation/question/explanation) it judged by reading page bodies, structured "
        "information-gain items, linked entity ids, evidence readiness and missing evidence. A "
        "COVERED judgement flips the decision to IGNORE; GAP/PARTIAL keep it a CREATE. The Planner "
        "does not infer this itself.",
        {
            "workspace_id": STRING,
            "opportunity_id": STRING,
            "semantic_coverage": {"type": "object"},
            "information_gain": {"type": "array", "items": {"type": "object"}},
            "entity_ids": ARRAY,
            "evidence_readiness": {"type": "string"},
            "missing_evidence": ARRAY,
        },
        ["workspace_id", "opportunity_id"],
        title="Record opportunity analysis",
        read_only=False,
        open_world=False,
        idempotent=True,
    ),
    _tool(
        "list_topic_opportunities",
        "List ranked topic decisions for a completed research run.",
        {"workspace_id": STRING, "research_run_id": STRING},
        ["workspace_id", "research_run_id"],
        title="List topic opportunities",
        read_only=True,
        open_world=False,
    ),
    _tool(
        "get_topic_opportunity",
        "Read a decision, score breakdown, AI overview saturation, and evidence links for one opportunity.",
        {"workspace_id": STRING, "opportunity_id": STRING},
        ["workspace_id", "opportunity_id"],
        title="Get topic opportunity",
        read_only=True,
        open_world=False,
    ),
    _tool(
        "get_research_evidence",
        "Drill into one keyword, SERP (organic + AI overview + PAA + related), competitor-page, or "
        "site-snapshot evidence record.",
        {"workspace_id": STRING, "evidence_id": STRING},
        ["workspace_id", "evidence_id"],
        title="Get research evidence",
        read_only=True,
        open_world=False,
    ),
    _tool(
        "create_content_cluster",
        "Record the user's selected CREATE opportunity and build an evidence-backed content cluster: "
        "one pillar page plus only the supporting pages the research justifies. Gated server-side: "
        "the rendered SERP must be read and at least one external third-party evidence family "
        "(community, technical, business or competitor) recorded first; the brand's own site does "
        "not satisfy this gate. The funnel is descriptive, not a mandate, so a cluster need not "
        "contain a BOFU page. No new research calls occur.",
        {"workspace_id": STRING, "opportunity_id": STRING},
        ["workspace_id", "opportunity_id"],
        title="Create content cluster",
        read_only=False,
        open_world=False,
    ),
    _tool(
        "get_cluster_readiness",
        "Report whether a research run has met the external-research gate: SERP read and at least one "
        "external third-party family (community, technical, business, competitor) covered. Returns "
        "the gate_status (BLOCKED_NO_SERP | BLOCKED_NO_EXTERNAL | DEGRADED_MINIMUM | FULL_COVERAGE), "
        "which families are missing, and any blocked source attempts. Use it before "
        "create_content_cluster.",
        {"workspace_id": STRING, "research_run_id": STRING},
        ["workspace_id", "research_run_id"],
        title="Get cluster readiness",
        read_only=True,
        open_world=False,
    ),
    _tool(
        "get_topic_cluster",
        "Read one funnel-staged content cluster, its pillar, supporting pages and coverage.",
        {"workspace_id": STRING, "cluster_id": STRING},
        ["workspace_id", "cluster_id"],
        title="Get topic cluster",
        read_only=True,
        open_world=False,
    ),
    _tool(
        "get_outline",
        "Read one generated outline and its evidence lineage.",
        {"workspace_id": STRING, "outline_id": STRING},
        ["workspace_id", "outline_id"],
        title="Get outline",
        read_only=True,
        open_world=False,
    ),
]


def _data_dir() -> Path:
    return Path(
        os.environ.get("SEO_TOPIC_PLANNER_DATA_DIR")
        or os.environ.get("PLUGIN_DATA")
        or Path.home() / ".seo-topic-planner"
    )


def _service() -> TopicPlannerService:
    base = _data_dir()
    connection = DataForSeoConnection(TokenStore(base / "dataforseo-oauth.json"))
    # The Planner owns the DataForSEO OAuth connection.  A stored token means the
    # user explicitly connected, so the process starts on live data; the
    # estimate/confirmation gate still blocks every paid call.  The environment
    # override exists only for controlled runtimes; no tool argument can switch
    # providers or inject a credential.
    mode = os.environ.get("SEO_TOPIC_PLANNER_PROVIDER")
    if mode == "dataforseo_mcp":
        provider = DataForSeoMcpProvider(
            token_provider=connection.access_token, status_provider=connection.status
        )
        return TopicPlannerService(
            base,
            connection=connection,
            keyword_provider=provider,
            serp_provider=provider,
            page_provider=provider,
        )
    if mode == "dataforseo":
        # Retained only for controlled legacy runtimes.  New installations use
        # the local OAuth connection above.
        provider = DataForSeoProvider(enabled=os.environ.get("TOPIC_PLANNER_ENABLE_LIVE_DATAFORSEO") == "1")
        return TopicPlannerService(
            base,
            connection=connection,
            keyword_provider=provider,
            serp_provider=provider,
            page_provider=provider,
        )
    return TopicPlannerService(base, connection=connection)


def _dispatch(service: TopicPlannerService, name: str, arguments: dict[str, Any]) -> Any:
    handlers: dict[str, Callable[..., Any]] = {
        "create_brand_profile": service.create_brand_profile,
        "get_brand_profile": service.get_brand_profile,
        "get_brand_profile_by_website": service.get_brand_profile_by_website,
        "open_brand_context_card": service.brand_context_card,
        "confirm_brand_context": service.confirm_brand_context,
        "open_brand_entity_card": service.brand_entity_card,
        "create_brand_entity": service.create_brand_entity,
        "update_brand_entity": service.update_brand_entity,
        "confirm_brand_entity": service.confirm_brand_entity,
        "get_brand_entity": service.get_brand_entity,
        "list_brand_entities": service.list_brand_entities,
        "get_brand_entity_recommendations": service.brand_entity_recommendations,
        "record_topical_entities": service.record_topical_entities,
        "list_topical_entities": service.list_topical_entities,
        "record_opportunity_analysis": service.record_opportunity_analysis,
        "record_site_pages": service.record_site_pages,
        "record_source_observations": service.record_source_observations,
        "list_source_observations": service.list_source_observations,
        "record_source_attempts": service.record_source_attempts,
        "list_source_attempts": service.list_source_attempts,
        "get_cluster_readiness": service.get_cluster_readiness,
        "open_connection_settings": lambda workspace_id="settings": service.get_data_provider_status(
            workspace_id=workspace_id
        ),
        "get_data_provider_status": service.get_data_provider_status,
        "connect_dataforseo": service.connect_dataforseo,
        "complete_dataforseo_connection": service.complete_dataforseo_connection,
        "disconnect_dataforseo": service.disconnect_dataforseo,
        "estimate_topic_research": service.estimate_topic_research,
        "run_topic_research": service.run_topic_research,
        "list_topic_opportunities": service.list_topic_opportunities,
        "get_topic_opportunity": service.get_topic_opportunity,
        "get_research_evidence": service.get_research_evidence,
        "create_content_cluster": service.create_content_cluster,
        "get_topic_cluster": service.get_topic_cluster,
        "get_outline": service.get_outline,
    }
    try:
        handler = handlers[name]
    except KeyError as exc:
        raise TopicPlannerError("unknown tool") from exc
    return handler(**arguments)


def _result(payload: Any, *, is_error: bool = False) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, sort_keys=True)}],
        "structuredContent": payload,
        "isError": is_error,
    }


def handle(service: TopicPlannerService, request: dict[str, Any]) -> dict[str, Any] | None:
    method = request.get("method")
    request_id = request.get("id")
    if method == "notifications/initialized":
        return None
    if method == "initialize":
        response = {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}, "resources": {}},
                "serverInfo": {"name": "seo-topic-planner", "version": PLUGIN_VERSION},
            },
        }
    elif method == "resources/list":
        response = {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {"resources": [_app_resource(uri) for uri in APP_RESOURCES]},
        }
    elif method == "resources/read":
        uri = str((request.get("params") or {}).get("uri") or "")
        resource = APP_RESOURCES.get(uri)
        if resource is None:
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32602, "message": "resource not found"},
            }
        else:
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "contents": [
                        {
                            "uri": uri,
                            "mimeType": "text/html;profile=mcp-app",
                            "text": resource["path"].read_text(encoding="utf-8"),
                            "_meta": resource["meta"],
                        }
                    ]
                },
            }
    elif method == "tools/list":
        response = {"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}}
    elif method == "tools/call":
        params = request.get("params") or {}
        try:
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": _result(
                    _dispatch(service, str(params.get("name", "")), dict(params.get("arguments") or {}))
                ),
            }
        except TopicPlannerError as exc:
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": _result({"error": str(exc)}, is_error=True),
            }
        except Exception:
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": _result({"error": "internal Topic Planner error"}, is_error=True),
            }
    else:
        response = {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": "method not found"},
        }
    return response


def main() -> None:
    service = _service()
    for line in sys.stdin:
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                continue
            response = handle(service, request)
            if response is not None:
                sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                sys.stdout.flush()
        except json.JSONDecodeError:
            continue


if __name__ == "__main__":
    main()
