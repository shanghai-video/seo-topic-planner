"""The Topic Planner decision service.

No MCP transport, provider response shape, credential, or UI concern reaches
this module.  It is the portable Core contract for the local plugin today and
a future server-side Core tomorrow.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from .errors import AuthError, BudgetError, NotFoundError, StateError, ValidationError
from .models import (
    COMMUNITY_SOURCE_FAMILIES,
    ENTITY_TYPES,
    EVIDENCE_FAMILIES,
    EVIDENCE_READINESS,
    EXTERNAL_EVIDENCE_FAMILIES,
    FUNNEL_STAGES,
    OBSERVATION_SOURCE_TYPES,
    RELATION_TYPES,
    SERP_SOURCE_TYPES,
    SOURCE_ATTEMPT_METHODS,
    SOURCE_ATTEMPT_OUTCOMES,
    TOPICAL_ENTITY_TYPES,
    BrandEntity,
    BrandProfile,
    DecisionType,
    EntityRelation,
    Outline,
    ResearchEstimate,
    ResearchEvidence,
    ResearchEvidencePack,
    ResearchRun,
    ResearchStatus,
    SiteSnapshot,
    SourceAttempt,
    SourceObservation,
    TopicalEntity,
    TopicCluster,
    TopicDecision,
    TopicOpportunity,
    json_schemas,
    new_id,
    now,
    website_key,
)
from .oauth import DataForSeoConnection
from .providers import (
    DataForSeoMcpProvider,
    KeywordProvider,
    PageProvider,
    ProviderResponse,
    SerpProvider,
)
from .store import LocalStore

_STOPWORDS = {
    "the",
    "a",
    "an",
    "for",
    "of",
    "to",
    "in",
    "on",
    "and",
    "or",
    "what",
    "how",
    "is",
    "are",
    "with",
    "your",
    "you",
}
_WORD = re.compile(r"[a-z0-9]+")

# Curated starting options for the brand-context card.  They are suggestions,
# never silent defaults: market and language stay empty until the user picks
# one, and a custom value is always allowed.
MARKET_OPTIONS = [
    {"value": "United States", "label": "United States"},
    {"value": "United Kingdom", "label": "United Kingdom"},
    {"value": "Canada", "label": "Canada"},
    {"value": "Australia", "label": "Australia"},
    {"value": "Germany", "label": "Germany"},
    {"value": "France", "label": "France"},
    {"value": "Spain", "label": "Spain"},
    {"value": "Italy", "label": "Italy"},
    {"value": "Netherlands", "label": "Netherlands"},
    {"value": "India", "label": "India"},
    {"value": "Japan", "label": "Japan"},
    {"value": "Brazil", "label": "Brazil"},
    {"value": "Mexico", "label": "Mexico"},
    {"value": "Singapore", "label": "Singapore"},
    {"value": "United Arab Emirates", "label": "United Arab Emirates"},
]
LANGUAGE_OPTIONS = [
    {"value": "en", "label": "English"},
    {"value": "zh", "label": "Chinese"},
    {"value": "es", "label": "Spanish"},
    {"value": "de", "label": "German"},
    {"value": "fr", "label": "French"},
    {"value": "pt", "label": "Portuguese"},
    {"value": "it", "label": "Italian"},
    {"value": "nl", "label": "Dutch"},
    {"value": "ja", "label": "Japanese"},
    {"value": "ko", "label": "Korean"},
    {"value": "hi", "label": "Hindi"},
    {"value": "ar", "label": "Arabic"},
]
MAX_CORE_FEATURES = 20
SCORING_VERSION = "v2"

# A semantic-coverage judgement from the host agent.  UNKNOWN is the honest
# default: without page-body evidence the planner must not assume a page does or
# does not cover a topic.
SEMANTIC_COVERAGE_STATUSES = {"COVERED", "PARTIAL", "GAP", "UNKNOWN"}

# Numeric mapping used only to fold the categorical readiness into the bounded
# score.  Missing data never becomes a silent zero: an unverified brand scores
# lower than a ready one but is not treated as actively bad.
_READINESS_SCORE = {"READY": 100, "NEEDS_EVIDENCE": 40, "UNVERIFIED": 20}
_GAP_TYPES = {
    "original_data",
    "first_hand_experience",
    "technical_depth",
    "decision_framework",
    "novel_insight",
    "question_coverage",
}


def _tokens(text: str) -> set[str]:
    return {token for token in _WORD.findall(str(text or "").lower()) if token not in _STOPWORDS}


def _url_domain(url: Any) -> str:
    host = (urlparse(str(url or "")).hostname or "").strip().lower()
    if host.startswith("www."):
        host = host[4:]
    return host


class TopicPlannerService:
    """User-action service with durable state, budget guardrails and lineage."""

    # Conservative per-request ceilings, observed from a real DataForSEO smoke
    # (2026-10: Labs keyword_suggestions $0.01296, search_volume $0.09, SERP
    # organic advanced $0.002).  These are a policy approval ceiling, not a
    # quote; the runtime budget abort is the hard guard.
    LIVE_ESTIMATE_COSTS = {
        "keyword_expansion": 0.02,
        "keyword_metrics": 0.10,
        "serp": 0.01,
        "competitor_page": 0.0,
    }

    def __init__(
        self,
        data_dir: Path,
        *,
        connection: DataForSeoConnection | None = None,
        keyword_provider: KeywordProvider | None = None,
        serp_provider: SerpProvider | None = None,
        page_provider: PageProvider | None = None,
    ) -> None:
        self.store = LocalStore(data_dir)
        self.connection = connection
        explicit = any(item is not None for item in (keyword_provider, serp_provider, page_provider))
        if not explicit and connection is not None and connection.is_connected():
            # The user already completed the local DataForSEO authorization, so
            # this process starts on real data.  It is not a fallback: the
            # estimate/confirmation gate still blocks every paid call.
            provider = self._live_provider()
            keyword_provider = serp_provider = page_provider = provider
        # There is deliberately no fake fallback.  An unconnected runtime has no
        # provider and every data-dependent action reports AUTH_REQUIRED.
        self.keyword_provider = keyword_provider
        self.serp_provider = serp_provider
        self.page_provider = page_provider

    def _live_provider(self) -> DataForSeoMcpProvider:
        if self.connection is None:
            raise StateError("this runtime does not support a local DataForSEO connection")
        return DataForSeoMcpProvider(
            token_provider=self.connection.access_token,
            status_provider=self.connection.status,
        )

    def _use_live_provider(self) -> None:
        provider = self._live_provider()
        self.keyword_provider = provider
        self.serp_provider = provider
        self.page_provider = provider

    def _require_research_provider(self) -> None:
        if self.keyword_provider is None or self.serp_provider is None:
            raise AuthError("DataForSEO is not connected; connect it before estimating or running research")

    def _provider_name(self) -> str:
        provider = self.keyword_provider
        return getattr(provider, "name", None) or "unavailable"

    @staticmethod
    def _workspace(workspace_id: str) -> str:
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            raise ValidationError("workspace_id is required")
        return workspace_id.strip()

    @staticmethod
    def _clean_topics(seed_topics: list[str]) -> list[str]:
        cleaned = list(
            dict.fromkeys(item.strip() for item in seed_topics if isinstance(item, str) and item.strip())
        )
        return cleaned[:5]

    @staticmethod
    def _topics(seed_topics: list[str]) -> list[str]:
        cleaned = TopicPlannerService._clean_topics(seed_topics)
        if not cleaned:
            raise ValidationError("at least one seed topic is required")
        return cleaned

    @staticmethod
    def _features(values: list[str] | None) -> list[str]:
        if not isinstance(values, list):
            return []
        cleaned = list(
            dict.fromkeys(str(item).strip() for item in values if str(item).strip())
        )
        return cleaned[:MAX_CORE_FEATURES]

    @staticmethod
    def _cache_key(
        provider: str, operation: str, material: object, market: str, language: str, device: str
    ) -> str:
        value = repr((provider, operation, material, market, language, device)).encode("utf-8")
        return hashlib.sha256(value).hexdigest()

    @staticmethod
    def _expiry(days: int = 14) -> str:
        return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _enforce_budget(estimate: ResearchEstimate, actual_cost: float) -> None:
        """Stop a live run as soon as observed cost exceeds the approved cap.

        Provider calls already made are kept as partial evidence; no further
        paid call is issued after the cap is crossed.
        """
        if actual_cost > estimate.budget_cap_usd + 1e-9:
            raise BudgetError("actual research cost exceeded the approved budget; the run was stopped")

    # ------------------------------------------------------------------ brand

    def _latest_snapshot(self, workspace_id: str, brand_id: str) -> SiteSnapshot | None:
        items = self.store.list(
            workspace_id=workspace_id, object_type="site_snapshot", lineage_contains=brand_id
        )
        return items[-1] if items else None  # type: ignore[return-value]

    def _save_snapshot(
        self, workspace_id: str, profile: BrandProfile, pages: list[dict[str, Any]], scope: str
    ) -> SiteSnapshot:
        snapshot = SiteSnapshot(
            id=new_id("site"),
            workspace_id=workspace_id,
            brand_id=profile.id,
            source="user_input" if scope == "manual_brand_brief" else "host_agent",
            status="COMPLETED",
            lineage=[profile.id],
            website=profile.website,
            pages=pages,
            capture_scope=scope,
        )
        self.store.save(snapshot)
        return snapshot

    def create_brand_profile(
        self,
        *,
        workspace_id: str,
        name: str,
        website: str,
        product: str = "",
        target_customer: str = "",
        target_market: str = "",
        language: str = "",
        positioning: str = "",
        primary_scenario: str = "",
        main_business: str = "",
        core_features: list[str] | None = None,
        competitors: list[str] | None = None,
        business_goals: list[str] | None = None,
        seed_topics: list[str] | None = None,
        site_pages: list[dict[str, Any]] | None = None,
        update_existing: bool = False,
    ) -> dict[str, Any]:
        workspace_id = self._workspace(workspace_id)
        if not all(isinstance(value, str) and value.strip() for value in (name, website)):
            raise ValidationError("name and website are required")
        features = self._features(core_features)
        existing = self.store.find_brand_by_website(workspace_id=workspace_id, website=website)
        if existing is not None and not update_existing:
            snapshot = self._latest_snapshot(workspace_id, existing.id)
            return {
                "brand_profile": existing.to_dict(),
                "site_snapshot": snapshot.to_dict() if snapshot else None,
                "reused": True,
                "message": (
                    "An existing brand context is remembered for this website. Call again with "
                    "update_existing=true to replace it, or confirm the existing context."
                ),
            }
        if existing is not None:
            # Updating invalidates any prior explicit confirmation.
            existing.name = name.strip()
            existing.website = website.strip()
            existing.product = product.strip()
            existing.target_customer = target_customer.strip()
            existing.target_market = target_market.strip()
            existing.language = language.strip()
            existing.positioning = positioning.strip()
            existing.primary_scenario = primary_scenario.strip()
            existing.main_business = main_business.strip()
            if core_features is not None:
                existing.core_features = features
            existing.competitors = competitors if competitors is not None else existing.competitors
            existing.business_goals = (
                business_goals if business_goals is not None else existing.business_goals
            )
            existing.seed_topics = self._clean_topics(
                seed_topics if seed_topics is not None else ([product] if product.strip() else [])
            ) or existing.seed_topics
            existing.context_status = "DRAFT"
            existing.status = "DRAFT"
            existing.confirmed_at = ""
            existing.touch()
            self.store.save(existing)
            entity = self._ensure_default_brand_entity(workspace_id, existing)
            snapshot = (
                self._save_snapshot(workspace_id, existing, site_pages, "manual_brand_brief")
                if site_pages is not None
                else self._latest_snapshot(workspace_id, existing.id)
            )
            return {
                "brand_profile": existing.to_dict(),
                "brand_entity": entity.to_dict() if entity else None,
                "site_snapshot": snapshot.to_dict() if snapshot else None,
                "reused": False,
                "updated": True,
            }
        seed_values = seed_topics if seed_topics is not None else ([product] if product.strip() else [])
        seeds = self._clean_topics(seed_values)
        profile = BrandProfile(
            id=new_id("brand"),
            workspace_id=workspace_id,
            brand_id=None,
            source="user_input",
            status="DRAFT",
            lineage=[],
            name=name.strip(),
            website=website.strip(),
            product=product.strip(),
            target_customer=target_customer.strip(),
            target_market=target_market.strip(),
            language=language.strip(),
            positioning=positioning.strip(),
            primary_scenario=primary_scenario.strip(),
            main_business=main_business.strip(),
            core_features=features,
            context_status="DRAFT",
            competitors=competitors or [],
            business_goals=business_goals or [],
            seed_topics=seeds,
        )
        profile.brand_id = profile.id
        self.store.save(profile)
        snapshot = self._save_snapshot(workspace_id, profile, site_pages or [], "manual_brand_brief")
        entity = self._ensure_default_brand_entity(workspace_id, profile)
        return {
            "brand_profile": profile.to_dict(),
            "brand_entity": entity.to_dict() if entity else None,
            "site_snapshot": snapshot.to_dict(),
            "reused": False,
            "message": (
                "Brand context is DRAFT. Open the brand-context card to confirm the target market, "
                "language and core features, plus the ICP and business anchors, before estimating or "
                "running research."
            ),
        }

    def get_brand_profile(self, *, workspace_id: str, brand_id: str) -> dict[str, Any]:
        profile = self.store.get(
            brand_id, workspace_id=self._workspace(workspace_id), object_type="brand_profile"
        )
        return profile.to_dict()

    def get_brand_profile_by_website(self, *, workspace_id: str, website: str) -> dict[str, Any]:
        """Return the website-keyed brand memory, if any, without creating it."""
        workspace_id = self._workspace(workspace_id)
        key = website_key(website)
        profile = self.store.find_brand_by_website(workspace_id=workspace_id, website=website)
        if profile is None:
            return {"website_key": key, "found": False, "brand_profile": None, "site_snapshot": None}
        snapshot = self._latest_snapshot(workspace_id, profile.id)
        return {
            "website_key": key,
            "found": True,
            "brand_profile": profile.to_dict(),
            "site_snapshot": snapshot.to_dict() if snapshot else None,
        }

    def confirm_brand_context(
        self,
        *,
        workspace_id: str,
        brand_id: str,
        target_market: str,
        language: str,
        target_customer: str,
        positioning: str,
        primary_scenario: str,
        main_business: str,
        core_features: list[str] | None = None,
    ) -> dict[str, Any]:
        """Explicitly confirm the market, language, ICP, core features and anchors.

        Only the user (through the host agent or the brand-context card) can move
        a brand profile from DRAFT to CONFIRMED; research is blocked until then.
        """
        workspace_id = self._workspace(workspace_id)
        features = self._features(core_features)
        fields = {
            "target_market": target_market,
            "language": language,
            "target_customer": target_customer,
            "positioning": positioning,
            "primary_scenario": primary_scenario,
            "main_business": main_business,
        }
        missing = [name for name, value in fields.items() if not isinstance(value, str) or not value.strip()]
        if not features:
            missing.append("core_features")
        if missing:
            raise ValidationError("explicit confirmation requires: " + ", ".join(missing))
        profile = self.store.get(brand_id, workspace_id=workspace_id, object_type="brand_profile")
        assert isinstance(profile, BrandProfile)
        profile.target_market = target_market.strip()
        profile.language = language.strip()
        profile.target_customer = target_customer.strip()
        profile.positioning = positioning.strip()
        profile.primary_scenario = primary_scenario.strip()
        profile.main_business = main_business.strip()
        profile.core_features = features
        profile.status = "CONFIRMED"
        profile.context_status = "CONFIRMED"
        profile.confirmed_at = now()
        profile.touch()
        self.store.save(profile)
        return profile.to_dict()

    @staticmethod
    def _suggestion(suggestions: dict[str, Any] | None) -> dict[str, Any]:
        """Normalize optional host-agent scrape suggestions into card fields."""

        source = suggestions if isinstance(suggestions, dict) else {}

        def text(key: str) -> str:
            return str(source.get(key) or "").strip()

        return {
            "name": text("name"),
            "product": text("product"),
            "target_customer": text("target_customer"),
            "target_market": text("target_market"),
            "language": text("language"),
            "positioning": text("positioning"),
            "primary_scenario": text("primary_scenario"),
            "main_business": text("main_business"),
            "core_features": TopicPlannerService._features(source.get("core_features")),
        }

    def brand_context_card(
        self,
        *,
        workspace_id: str,
        website: str,
        suggestions: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return the payload that drives the brand-context confirmation card.

        No writes happen here.  A remembered profile is echoed back for review;
        otherwise the host agent's scrape suggestions are presented for the user
        to accept, edit or replace.  Market and language are never preselected by
        the Planner itself.
        """

        workspace_id = self._workspace(workspace_id)
        key = website_key(website)
        if not key:
            raise ValidationError("a website host is required to open the brand context card")
        profile = self.store.find_brand_by_website(workspace_id=workspace_id, website=website)
        if isinstance(profile, BrandProfile):
            card_fields = {
                "name": profile.name,
                "product": profile.product,
                "target_customer": profile.target_customer,
                "target_market": profile.target_market,
                "language": profile.language,
                "positioning": profile.positioning,
                "primary_scenario": profile.primary_scenario,
                "main_business": profile.main_business,
                "core_features": list(profile.core_features),
            }
            found, mode = True, "review"
        else:
            card_fields = self._suggestion(suggestions)
            found, mode = False, "first_run"
        return {
            "workspace_id": workspace_id,
            "website": website,
            "website_key": key,
            "found": found,
            "mode": mode,
            "brand_profile": profile.to_dict() if isinstance(profile, BrandProfile) else None,
            "suggestions": card_fields,
            "market_options": MARKET_OPTIONS,
            "language_options": LANGUAGE_OPTIONS,
            "note": (
                "The Planner does not assume a target market or language. Confirm them explicitly, "
                "and pick or add the product's core features."
            ),
        }

    def record_site_pages(
        self,
        *,
        workspace_id: str,
        brand_id: str,
        pages: list[dict[str, Any]],
        capture_scope: str = "host_agent_site_snapshot",
    ) -> dict[str, Any]:
        """Persist page summaries the host agent captured from the user's site.

        Only summaries/titles are kept; full page bodies are not accepted.
        """
        workspace_id = self._workspace(workspace_id)
        profile = self.store.get(brand_id, workspace_id=workspace_id, object_type="brand_profile")
        assert isinstance(profile, BrandProfile)
        if not isinstance(pages, list) or not pages:
            raise ValidationError("at least one site page is required")
        validated: list[dict[str, Any]] = []
        for page in pages:
            if not isinstance(page, dict) or not str(page.get("url") or "").strip():
                raise ValidationError("every site page requires a url")
            validated.append(
                {
                    "url": str(page["url"]).strip(),
                    "title": str(page.get("title") or ""),
                    "meta_description": str(page.get("meta_description") or ""),
                    "h1": str(page.get("h1") or ""),
                    "summary": str(page.get("summary") or "")[:1000],
                    "canonical": str(page.get("canonical") or ""),
                    "inferred_topics": [str(item) for item in (page.get("inferred_topics") or [])],
                }
            )
        snapshot = self._save_snapshot(workspace_id, profile, validated, capture_scope)
        return snapshot.to_dict()

    # -------------------------------------------------------- brand entities

    def _brand_entities(self, *, workspace_id: str, brand_id: str) -> list[BrandEntity]:
        return [
            item
            for item in self.store.list(
                workspace_id=workspace_id, object_type="brand_entity", lineage_contains=brand_id
            )
            if isinstance(item, BrandEntity)
        ]

    def _ensure_default_brand_entity(
        self, workspace_id: str, profile: BrandProfile
    ) -> BrandEntity | None:
        """Guarantee one DRAFT brand entity per profile and link it back.

        This is a starting point, not confirmation: the entity stays DRAFT until
        the user confirms its identity.  It is created from what the user already
        supplied, never from open-web research.
        """
        entities = self._brand_entities(workspace_id=workspace_id, brand_id=profile.id)
        default = next((entity for entity in entities if entity.id == profile.entity_id), None)
        if default is None:
            default = next((entity for entity in entities if entity.entity_type == "brand"), None)
        if default is None:
            created = self.create_brand_entity(
                workspace_id=workspace_id,
                brand_id=profile.id,
                canonical_name=profile.name,
                entity_type="brand",
                official_website=profile.website,
            )
            default = self.store.get(
                created["brand_entity"]["id"],
                workspace_id=workspace_id,
                object_type="brand_entity",
            )
        if isinstance(default, BrandEntity) and profile.entity_id != default.id:
            profile.entity_id = default.id
            self.store.save(profile)
        return default if isinstance(default, BrandEntity) else None

    @staticmethod
    def _clean_aliases(values: list[str] | None) -> list[str]:
        return list(dict.fromkeys(str(item).strip() for item in (values or []) if str(item).strip()))

    @staticmethod
    def _clean_urls(values: list[str] | None) -> list[str]:
        return list(dict.fromkeys(str(item).strip() for item in (values or []) if str(item).strip()))

    @classmethod
    def _clean_profiles(cls, values: list[dict[str, Any]] | None, *, verified: bool) -> list[dict[str, Any]]:
        """Normalize external-profile records, dropping anything without a URL.

        A verified profile must name who verified it; a candidate profile is
        only ever a lead.  Neither path invents a URL.
        """

        result: list[dict[str, Any]] = []
        for item in values or []:
            if not isinstance(item, dict) or not str(item.get("url") or "").strip():
                continue
            profile: dict[str, Any] = {
                "url": str(item["url"]).strip(),
                "profile_type": str(item.get("profile_type") or "").strip(),
                "evidence_ids": [str(value) for value in (item.get("evidence_ids") or [])],
            }
            if verified:
                profile["verified_by"] = str(item.get("verified_by") or "").strip()
            else:
                profile["source"] = str(item.get("source") or "").strip()
                profile["status"] = str(item.get("status") or "CANDIDATE").strip()
            result.append(profile)
        return result

    def create_brand_entity(
        self,
        *,
        workspace_id: str,
        brand_id: str,
        canonical_name: str,
        entity_type: str = "brand",
        aliases: list[str] | None = None,
        legal_name: str = "",
        official_website: str = "",
        official_product_urls: list[str] | None = None,
        description: str = "",
        disambiguation: str = "",
        parent_entity_id: str = "",
        related_entity_ids: list[str] | None = None,
        verified_external_profiles: list[dict[str, Any]] | None = None,
        candidate_external_profiles: list[dict[str, Any]] | None = None,
        notes: str = "",
        update_existing: bool = False,
    ) -> dict[str, Any]:
        """Create or update one typed brand entity (organization/brand/product)."""
        workspace_id = self._workspace(workspace_id)
        self.store.get(brand_id, workspace_id=workspace_id, object_type="brand_profile")
        if entity_type not in ENTITY_TYPES:
            raise ValidationError("unsupported brand entity type")
        if not str(canonical_name).strip():
            raise ValidationError("a canonical entity name is required")
        existing = next(
            (
                entity
                for entity in self._brand_entities(workspace_id=workspace_id, brand_id=brand_id)
                if entity.entity_type == entity_type
                and entity.canonical_name.strip().lower() == canonical_name.strip().lower()
            ),
            None,
        )
        if existing is not None and not update_existing:
            return {
                "brand_entity": existing.to_dict(),
                "reused": True,
                "updated": False,
                "message": (
                    "An entity with this name and type already exists. Pass "
                    "update_existing=true to change it."
                ),
            }
        verified = self._clean_profiles(verified_external_profiles, verified=True)
        candidates = self._clean_profiles(candidate_external_profiles, verified=False)
        if existing is None:
            entity = BrandEntity(
                id=new_id("brand_entity"),
                workspace_id=workspace_id,
                brand_id=brand_id,
                source="user_input" if update_existing else "host_agent",
                status="DRAFT",
                lineage=[brand_id],
            )
        else:
            entity = existing
            entity.touch()
        entity.entity_type = entity_type
        entity.canonical_name = canonical_name.strip()
        entity.aliases = self._clean_aliases(aliases)
        entity.legal_name = str(legal_name).strip()
        entity.official_website = str(official_website).strip()
        entity.official_product_urls = self._clean_urls(official_product_urls)
        entity.description = str(description).strip()
        entity.disambiguation = str(disambiguation).strip()
        entity.parent_entity_id = str(parent_entity_id).strip()
        entity.related_entity_ids = self._clean_urls(related_entity_ids)
        if verified:
            entity.verified_external_profiles = verified
        if candidates:
            entity.candidate_external_profiles = candidates
        entity.notes = str(notes).strip()
        if entity.parent_entity_id == entity.id:
            raise ValidationError("a brand entity cannot be its own parent")
        if entity.identity_status == "UNKNOWN":
            entity.identity_status = "DRAFT"
        self.store.save(entity)
        return {
            "brand_entity": entity.to_dict(),
            "reused": False,
            "updated": existing is not None,
        }

    def update_brand_entity(self, *, workspace_id: str, entity_id: str, **changes: Any) -> dict[str, Any]:
        """Update an existing entity by id, preserving unspecified fields."""
        workspace_id = self._workspace(workspace_id)
        entity = self.store.get(entity_id, workspace_id=workspace_id, object_type="brand_entity")
        assert isinstance(entity, BrandEntity)
        result = self.create_brand_entity(
            workspace_id=workspace_id,
            brand_id=entity.brand_id or "",
            canonical_name=changes.get("canonical_name", entity.canonical_name),
            entity_type=changes.get("entity_type", entity.entity_type),
            aliases=changes.get("aliases", entity.aliases),
            legal_name=changes.get("legal_name", entity.legal_name),
            official_website=changes.get("official_website", entity.official_website),
            official_product_urls=changes.get("official_product_urls", entity.official_product_urls),
            description=changes.get("description", entity.description),
            disambiguation=changes.get("disambiguation", entity.disambiguation),
            parent_entity_id=changes.get("parent_entity_id", entity.parent_entity_id),
            related_entity_ids=changes.get("related_entity_ids", entity.related_entity_ids),
            verified_external_profiles=changes.get(
                "verified_external_profiles", entity.verified_external_profiles
            ),
            candidate_external_profiles=changes.get(
                "candidate_external_profiles", entity.candidate_external_profiles
            ),
            notes=changes.get("notes", entity.notes),
            update_existing=True,
        )
        result["updated"] = True
        return result

    def confirm_brand_entity(
        self,
        *,
        workspace_id: str,
        entity_id: str,
        official_website: str | None = None,
        legal_name: str | None = None,
        aliases: list[str] | None = None,
        verified_external_profiles: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """User-confirm an entity's identity and its genuinely verified profiles.

        Only a URL the user has confirmed as the same entity may be recorded as a
        verified profile; candidate leads are never promoted automatically, so
        ``sameAs`` stays empty until a real URL is verified.
        """
        workspace_id = self._workspace(workspace_id)
        entity = self.store.get(entity_id, workspace_id=workspace_id, object_type="brand_entity")
        assert isinstance(entity, BrandEntity)
        if official_website is not None:
            entity.official_website = official_website.strip()
        if legal_name is not None:
            entity.legal_name = legal_name.strip()
        if aliases is not None:
            entity.aliases = self._clean_aliases(aliases)
        if verified_external_profiles is not None:
            cleaned = self._clean_profiles(verified_external_profiles, verified=True)
            missing_verifier = [item for item in cleaned if not item["verified_by"]]
            if missing_verifier:
                raise ValidationError("a verified external profile must name the verifier")
            entity.verified_external_profiles = cleaned
        entity.identity_status = "USER_CONFIRMED"
        entity.status = "CONFIRMED"
        entity.verification_status = (
            "VERIFIED" if entity.verified_external_profiles else "PARTIAL"
        )
        owner = (
            self.store.get(entity.brand_id, workspace_id=workspace_id, object_type="brand_profile")
            if entity.brand_id
            else None
        )
        entity.recommendations = self._brand_entity_recommendations(
            owner if isinstance(owner, BrandProfile) else None,
            self._brand_entities(workspace_id=workspace_id, brand_id=entity.brand_id or ""),
        )
        entity.touch()
        self.store.save(entity)
        return {
            "brand_entity": entity.to_dict(),
            "recommendations": entity.recommendations,
        }

    def get_brand_entity(self, *, workspace_id: str, entity_id: str) -> dict[str, Any]:
        return self.store.get(
            entity_id, workspace_id=self._workspace(workspace_id), object_type="brand_entity"
        ).to_dict()

    def list_brand_entities(self, *, workspace_id: str, brand_id: str) -> dict[str, Any]:
        workspace_id = self._workspace(workspace_id)
        self.store.get(brand_id, workspace_id=workspace_id, object_type="brand_profile")
        entities = self._brand_entities(workspace_id=workspace_id, brand_id=brand_id)
        return {
            "brand_id": brand_id,
            "entities": [entity.to_dict() for entity in entities],
            "count": len(entities),
        }

    def brand_entity_card(
        self,
        *,
        workspace_id: str,
        website: str = "",
        brand_id: str = "",
        suggestions: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Discovery/confirmation payload for the brand entity registry.

        The Planner does not research the open web itself: the host agent passes
        structured candidate entities and external profiles, and the user
        confirms which are real.  Nothing is written here.
        """
        workspace_id = self._workspace(workspace_id)
        profile = None
        if brand_id:
            profile = self.store.get(brand_id, workspace_id=workspace_id, object_type="brand_profile")
        elif website:
            profile = self.store.find_brand_by_website(workspace_id=workspace_id, website=website)
        entities: list[BrandEntity] = (
            self._brand_entities(workspace_id=workspace_id, brand_id=profile.id)
            if isinstance(profile, BrandProfile)
            else []
        )
        source = suggestions if isinstance(suggestions, dict) else {}
        candidate_entities = [
            {
                "entity_type": str(item.get("entity_type") or "brand"),
                "canonical_name": str(item.get("canonical_name") or ""),
                "aliases": self._clean_aliases(item.get("aliases")),
                "official_website": str(item.get("official_website") or ""),
                "parent_canonical_name": str(item.get("parent_canonical_name") or ""),
            }
            for item in (source.get("entities") or [])
            if isinstance(item, dict) and str(item.get("canonical_name") or "").strip()
        ]
        candidate_profiles = self._clean_profiles(source.get("external_profiles"), verified=False)
        return {
            "workspace_id": workspace_id,
            "website": website or (profile.website if isinstance(profile, BrandProfile) else ""),
            "brand_id": profile.id if isinstance(profile, BrandProfile) else brand_id,
            "found": bool(entities),
            "mode": "review" if entities else "first_run",
            "entities": [entity.to_dict() for entity in entities],
            "suggested_entities": candidate_entities,
            "suggested_external_profiles": candidate_profiles,
            "entity_types": sorted(ENTITY_TYPES),
            "note": (
                "Confirm which organization, brand, product and concept entities are real, and "
                "verify any external profile before it can be recorded as sameAs. The Planner "
                "never merges same-named entities across brands and never fetches them itself."
            ),
        }

    @classmethod
    def _brand_entity_recommendations(
        cls, profile: BrandProfile | None, entities: list[BrandEntity]
    ) -> list[dict[str, Any]]:
        """Turn observed identity problems into evidence-carrying brand actions.

        These are entity-establishment tasks, not search-volume opportunities.
        No action promises rankings, knowledge-graph inclusion or AI citations.
        """

        items: list[dict[str, Any]] = []

        def add(
            issue: str,
            observed_evidence: str,
            recommended_action: str,
            target_page_or_channel: str,
            priority: str,
            confidence: int,
            expected_objective: str,
            validation_method: str,
        ) -> None:
            items.append(
                {
                    "issue": issue,
                    "observed_evidence": observed_evidence,
                    "recommended_action": recommended_action,
                    "target_page_or_channel": target_page_or_channel,
                    "priority": priority,
                    "confidence": confidence,
                    "expected_objective": expected_objective,
                    "validation_method": validation_method,
                }
            )

        brands = [entity for entity in entities if entity.entity_type == "brand"]
        organizations = [entity for entity in entities if entity.entity_type == "organization"]
        products = [entity for entity in entities if entity.entity_type == "product"]
        if not entities:
            return items

        profile_name = (profile.name if profile else "").strip()
        if profile_name and brands and all(
            entity.canonical_name.strip().lower() != profile_name.lower() for entity in brands
        ):
            add(
                "Canonical brand name is not consistent",
                f"The brand profile name is '{profile_name}' but the brand entity is "
                f"'{brands[0].canonical_name}'.",
                "Use one canonical brand name across the site, schema and managed profiles.",
                "Homepage / About page",
                "HIGH",
                70,
                "One unambiguous brand name everywhere it appears.",
                "Re-read the homepage <title>/H1 and Organization JSON-LD name.",
            )
        if not organizations and (brands or products):
            add(
                "Company and brand/product subjects are mixed",
                "Brand or product entities exist without a distinct Organization entity.",
                "State the Organization -> Brand -> Product relationship explicitly.",
                "Homepage / About page",
                "MEDIUM",
                65,
                "A dataset where company, brand and product are separate nodes.",
                "Inspect the Organization/Brand/Product JSON-LD graph.",
            )
        if any(not entity.official_website for entity in entities):
            add(
                "A brand entity has no official website",
                "At least one entity is missing an official website.",
                "Record the official site and use a stable, consistent @id.",
                "Homepage / organization schema",
                "HIGH",
                75,
                "A stable entity identifier that resolves to the official site.",
                "Confirm the official URL resolves and matches the entity.",
            )
        candidates = [profile for entity in entities for profile in entity.candidate_external_profiles]
        if candidates and not any(entity.verified_external_profiles for entity in entities):
            add(
                "Official external profiles are unverified",
                f"{len(candidates)} candidate external profile(s) are recorded but none is verified.",
                "Check each profile really represents this entity, then record it as sameAs.",
                "Priority external profiles",
                "MEDIUM",
                60,
                "A sameAs set that contains only genuinely owned profiles.",
                "Open each URL and confirm it names the same entity.",
            )
        if not any(entity.verified_external_profiles for entity in entities):
            add(
                "No verified third-party corroboration",
                "No externally owned, verified profile backs the entity.",
                "Build credible, citable assets (documentation, case study) others can reference.",
                "Documentation / case studies",
                "MEDIUM",
                55,
                "Third-party material that corroborates the entity.",
                "Look for independent references to the entity over time.",
            )
        if any(entity.entity_type == "product" and not entity.description for entity in entities):
            add(
                "A product entity has no clear definition",
                "At least one product entity has no description.",
                "Write a precise, consistent product definition and capability summary.",
                "Product page",
                "MEDIUM",
                60,
                "A product definition that matches the official description.",
                "Compare the product page copy with the recorded definition.",
            )
        return items

    def brand_entity_recommendations(self, *, workspace_id: str, brand_id: str) -> dict[str, Any]:
        workspace_id = self._workspace(workspace_id)
        profile = self.store.get(brand_id, workspace_id=workspace_id, object_type="brand_profile")
        assert isinstance(profile, BrandProfile)
        entities = self._brand_entities(workspace_id=workspace_id, brand_id=brand_id)
        return {
            "brand_id": brand_id,
            "brand_entity_id": profile.entity_id,
            "entity_count": len(entities),
            "recommendations": self._brand_entity_recommendations(profile, entities),
        }

    # ------------------------------------------------------------ connection

    def get_data_provider_status(self, *, workspace_id: str) -> dict[str, Any]:
        """Expose readiness without testing credentials or making a paid call."""
        self._workspace(workspace_id)
        provider = self.keyword_provider
        can_connect = self.connection is not None
        if provider is None:
            return {
                "provider": "dataforseo_mcp" if can_connect else "unavailable",
                "status": "AUTH_REQUIRED",
                "data_mode": "NOT_CONNECTED",
                "is_live": False,
                "can_connect": can_connect,
                "next_action": "CONNECT_DATAFORSEO",
            }
        status = getattr(provider, "connection_status", None)
        if callable(status):
            value = status()
            if isinstance(value, dict):
                state = str(value.get("status") or "UNKNOWN")
                live = state == "READY"
                return {
                    "provider": str(value.get("provider") or getattr(provider, "name", "unknown")),
                    "status": state,
                    "data_mode": "LIVE" if live else "NOT_CONNECTED",
                    "is_live": live,
                    "can_connect": can_connect,
                    "next_action": "START_RESEARCH" if live else "CONNECT_DATAFORSEO",
                }
        # An explicitly injected provider (offline test double or a controlled
        # runtime adapter) is usable but is not by definition live market data.
        return {
            "provider": self._provider_name(),
            "status": "READY",
            "data_mode": "TEST",
            "is_live": False,
            "can_connect": can_connect,
            "next_action": "START_RESEARCH",
        }

    def connect_dataforseo(self, *, workspace_id: str) -> dict[str, Any]:
        """Start the local DataForSEO OAuth flow.

        Registration and the browser/loopback steps happen server-side; the tool
        only returns a URL and status.  No credential is accepted as an argument.
        """
        self._workspace(workspace_id)
        if self.connection is None:
            raise StateError("this runtime does not support a local DataForSEO connection")
        return self.connection.begin_authorization()

    def complete_dataforseo_connection(
        self, *, workspace_id: str, timeout_seconds: float = 300.0
    ) -> dict[str, Any]:
        """Wait for the authorization callback, then adopt the live provider."""
        self._workspace(workspace_id)
        if self.connection is None:
            raise StateError("this runtime does not support a local DataForSEO connection")
        result = self.connection.finish_authorization(timeout_seconds)
        if result.get("status") == "READY":
            self._use_live_provider()
            result = {**result, "data_mode": "LIVE", "is_live": True, "next_action": "START_RESEARCH"}
        else:
            result = {
                **result,
                "data_mode": "NOT_CONNECTED",
                "is_live": False,
                "next_action": "CONNECT_DATAFORSEO",
            }
        return result

    def disconnect_dataforseo(self, *, workspace_id: str) -> dict[str, Any]:
        """Forget the stored DataForSEO authorization.

        After disconnecting the runtime has no data provider, so every
        data-dependent action reports AUTH_REQUIRED until the user reconnects.
        """
        self._workspace(workspace_id)
        if self.connection is None:
            raise StateError("this runtime does not support a local DataForSEO connection")
        self.connection.disconnect()
        self.keyword_provider = None
        self.serp_provider = None
        self.page_provider = None
        return {"disconnected": True, **self.get_data_provider_status(workspace_id=workspace_id)}

    # ---------------------------------------------------------------- research

    def estimate_topic_research(
        self,
        *,
        workspace_id: str,
        brand_id: str,
        seed_topics: list[str] | None = None,
        market: str | None = None,
        language: str | None = None,
        device: str = "desktop",
        budget_cap_usd: float = 0.0,
    ) -> dict[str, Any]:
        workspace_id = self._workspace(workspace_id)
        self._require_research_provider()
        brand = self.store.get(brand_id, workspace_id=workspace_id, object_type="brand_profile")
        assert isinstance(brand, BrandProfile)
        if brand.context_status != "CONFIRMED":
            raise StateError("brand context must be explicitly confirmed before estimating research")
        topics = self._topics(seed_topics or brand.seed_topics)
        if budget_cap_usd < 0:
            raise ValidationError("budget_cap_usd must be non-negative")
        # Expansion happens per seed, while the deduplicated candidate set has one
        # bulk metrics call and at most fifteen SERP calls.  Competitor evidence is
        # derived from the sampled SERP result metadata; the plugin never fetches
        # competitor-page bodies itself.
        calls = {"keyword_expansion": len(topics), "keyword_metrics": 1, "serp": 15, "competitor_page": 0}
        provider = self._provider_name()
        estimate_value = round(sum(calls[key] * self.LIVE_ESTIMATE_COSTS[key] for key in calls), 4)
        run = ResearchRun(
            id=new_id("research"),
            workspace_id=workspace_id,
            brand_id=brand_id,
            source="estimate",
            status=ResearchStatus.ESTIMATED.value,
            lineage=[brand_id],
            seed_topics=topics,
            market=market or brand.target_market,
            language=language or brand.language,
            device=device,
            provider=provider,
            estimate_id="pending",
            estimated_cost_usd=estimate_value,
        )
        estimate = ResearchEstimate(
            id=new_id("estimate"),
            workspace_id=workspace_id,
            brand_id=brand_id,
            source="cost_policy",
            status=ResearchStatus.AWAITING_CONFIRMATION.value,
            lineage=[brand_id, run.id],
            research_run_id=run.id,
            seed_topics=topics,
            market=run.market,
            language=run.language,
            device=device,
            provider=provider,
            provider_calls=calls,
            estimated_cost_usd=estimate_value,
            budget_cap_usd=round(budget_cap_usd, 4),
            confirmation_token=new_id("confirm"),
        )
        run.estimate_id = estimate.id
        run.status = ResearchStatus.AWAITING_CONFIRMATION.value
        self.store.save(run)
        self.store.save(estimate)
        provider_status = self.get_data_provider_status(workspace_id=workspace_id)
        return {
            "research_run": run.to_dict(),
            "research_estimate": estimate.to_dict(),
            "confirmation_required": True,
            "estimate_context": {
                "data_mode": provider_status["data_mode"],
                "is_live_data": provider_status["is_live"],
                "estimate_source": "provider_policy_ceiling",
                "budget_cap_usd": estimate.budget_cap_usd,
                "price_note": (
                    "This is a conservative approval ceiling, not a provider quote. Actual cost "
                    "is read from provider responses, and the run aborts if it exceeds the "
                    "approved budget."
                ),
                "unbudgeted_collection_note": (
                    "Browser / Computer Use / yt-dlp collection performed by the host agent is "
                    "not billed through this plugin budget."
                ),
            },
        }

    def _evidence_from_provider(
        self,
        *,
        workspace_id: str,
        brand_id: str,
        run: ResearchRun,
        provider: str,
        operation: str,
        material: object,
        evidence_type: str,
        fetch: Callable[[], ProviderResponse],
    ) -> tuple[ResearchEvidence, bool]:
        """Return fresh-enough evidence before making a provider request.

        Cache lookup happens before ``fetch`` specifically so a cache hit cannot
        quietly repeat a paid provider call.
        """
        cache_key = self._cache_key(provider, operation, material, run.market, run.language, run.device)
        cached = self.store.get_cached_evidence(workspace_id=workspace_id, cache_key=cache_key)
        if cached:
            return cached, True
        response = fetch()
        evidence = ResearchEvidence(
            id=new_id("evidence"),
            workspace_id=workspace_id,
            brand_id=brand_id,
            source="provider",
            status="COMPLETED",
            lineage=[run.id, brand_id],
            provider=response.provider,
            provider_request_id=response.request_id,
            market=run.market,
            language=run.language,
            device=run.device,
            cost_usd=response.cost_usd,
            freshness="fresh",
            expires_at=self._expiry(),
            cache_key=cache_key,
            evidence_type=evidence_type,
            payload=response.payload,
        )
        self.store.cache_evidence(evidence)
        return evidence, False

    def _competitor_evidence_from_serp(
        self, *, workspace_id: str, brand_id: str, run: ResearchRun, serp_evidence: ResearchEvidence
    ) -> ResearchEvidence | None:
        """Persist a bounded, clearly-labelled competitor observation.

        DataForSEO's remote MCP flow provides the result URL and title in the
        SERP response.  We retain that evidence rather than fabricating a page
        analysis or failing the entire research run because a separate page-crawl
        product is not configured.
        """
        top = (serp_evidence.payload.get("organic") or [{}])[0]
        url = top.get("url") if isinstance(top, dict) else None
        if not isinstance(url, str) or not url:
            return None
        evidence = ResearchEvidence(
            id=new_id("evidence"),
            workspace_id=workspace_id,
            brand_id=brand_id,
            source="derived_serp",
            status="COMPLETED",
            lineage=[run.id, brand_id, serp_evidence.id],
            provider=serp_evidence.provider,
            provider_request_id=f"derived_{serp_evidence.provider_request_id}",
            market=run.market,
            language=run.language,
            device=run.device,
            cost_usd=0.0,
            freshness=serp_evidence.freshness,
            expires_at=serp_evidence.expires_at,
            cache_key=self._cache_key(
                serp_evidence.provider, "competitor_from_serp", url, run.market, run.language, run.device
            ),
            evidence_type="competitor_page",
            payload={
                "url": url,
                "title": top.get("title") or "",
                "page_type": top.get("type") or "organic_result",
                "coverage_gap": "Page content was not fetched; this observation is limited to SERP metadata.",
                "derived_from_evidence_id": serp_evidence.id,
            },
        )
        self.store.save(evidence)
        return evidence

    def _coverage(self, keyword: str, snapshot: SiteSnapshot) -> tuple[int, str | None]:
        wanted = _tokens(keyword) or {str(keyword).lower()}
        best_score, best_url = 0.0, None
        needle = str(keyword or "").lower().strip()
        for page in snapshot.pages:
            haystack = " ".join(
                str(page.get(key, "")) for key in ("title", "h1", "summary", "meta_description", "url")
            )
            page_tokens = _tokens(haystack)
            score = len(wanted & page_tokens) / len(wanted)
            if needle and (
                needle in str(page.get("title", "")).lower() or needle in str(page.get("h1", "")).lower()
            ):
                score = min(1.0, score + 0.25)
            if score > best_score:
                best_score, best_url = score, page.get("url")
        return round(best_score * 100), best_url

    @staticmethod
    def _score(
        *,
        demand: int,
        business: int,
        brand: int,
        feasibility: int,
        intent_alignment: int,
        structural_signal: int,
        information_gain: int,
        evidence_readiness: int,
        coverage: int,
        aio_penalty: int = 0,
    ) -> tuple[int, dict[str, int]]:
        """Transparent v2 priority score.

        The components answer different questions and are not re-derived from
        each other: demand and feasibility are external, business/brand fit are
        brand-relative, the SERP structural signal only describes the result
        surface, and information-gain/evidence-readiness answer whether the
        brand can actually add something.  Missing data lowers readiness rather
        than silently scoring zero.
        """
        parts = {
            "demand": round(demand * 0.15),
            "business_relevance": round(business * 0.18),
            "brand_fit": round(brand * 0.15),
            "ranking_feasibility": round(feasibility * 0.15),
            "intent_alignment": round(intent_alignment * 0.07),
            "serp_structural_signal": round(structural_signal * 0.08),
            "information_gain_feasibility": round(information_gain * 0.12),
            "evidence_readiness": round(evidence_readiness * 0.10),
            "existing_coverage_penalty": -round(coverage * 0.20),
            # AI overview presence is a saturation caveat evaluated separately
            # from demand.  It downweights but never removes an opportunity.
            "aio_saturation": aio_penalty,
        }
        return max(0, min(100, sum(parts.values()))), parts

    @staticmethod
    def _intent(keyword: str, metric_intent: Any) -> str:
        intent = str(metric_intent or "").strip().lower()
        if intent in {"informational", "commercial", "transactional", "navigational"}:
            return intent
        text = f" {keyword.lower()} "
        if any(word in text for word in (" vs ", " versus ", " compare", " comparison", " best ", " top ")):
            return "commercial"
        if any(word in text for word in (" buy ", " pricing", " price ", " cost ", " demo ", " trial ")):
            return "transactional"
        return "informational"

    def _serp_signals(self, serp: dict[str, Any], snapshot: SiteSnapshot) -> dict[str, Any]:
        """Structural SERP signals only.

        These describe the result surface.  They are deliberately *not*
        interpreted as "weak competition" or "high information gain": domain
        diversity is a structural fact, not evidence that a brand can add value.
        """
        organic = [row for row in (serp.get("organic") or []) if isinstance(row, dict)]
        paa = [row for row in (serp.get("paa") or []) if isinstance(row, dict)]
        related = [str(item) for item in (serp.get("related") or []) if str(item).strip()]
        aio = serp.get("aio") or None
        domains = [str(row.get("domain") or _url_domain(row.get("url"))) for row in organic]
        domains = [item for item in domains if item]
        unique_domains = sorted(set(domains))
        diversity = round(100 * len(unique_domains) / len(domains)) if domains else 0
        top_share = (max(Counter(domains).values()) / len(domains)) if domains else 0.0
        content_types = sorted({str(row.get("type")) for row in organic if row.get("type")})
        type_variety = min(100, round(len(content_types) / 4 * 100)) if content_types else 0
        unanswered_questions: list[str] = []
        for row in paa:
            question = str(row.get("question") or "")
            score, _ = self._coverage(question, snapshot)
            if score < 60:
                unanswered_questions.append(question)
        paa_gap = round(100 * len(unanswered_questions) / len(paa)) if paa else 0
        aio_covered = bool(aio and aio.get("present"))
        # A pure structural reading of the surface; a higher number means more
        # distinct domains and content types, not "weaker competition".
        structural_signal = round(0.5 * diversity + 0.5 * type_variety)
        return {
            "organic": organic,
            "paa": paa,
            "related": related,
            "aio": aio,
            "aio_covered": aio_covered,
            "domains": domains,
            "unique_domains": unique_domains,
            "content_types": content_types,
            "unanswered_paa": len(unanswered_questions),
            "unanswered_questions": unanswered_questions,
            "domain_diversity": diversity,
            "top_domain_share": round(top_share * 100),
            "content_type_variety": type_variety,
            "paa_gap": paa_gap,
            "serp_structural_signal": structural_signal,
        }

    @staticmethod
    def _observation_gaps(observations: list[SourceObservation]) -> list[dict[str, Any]]:
        gaps: list[dict[str, Any]] = []
        for observation in observations:
            if observation.research_purpose not in {"competitor_gap", "information_gain"}:
                continue
            if not observation.viewpoint.strip():
                continue
            gaps.append(
                {
                    "source_type": observation.source_type,
                    "url": observation.url,
                    "viewpoint": observation.viewpoint,
                    "observation_id": observation.id,
                    "evidence_ids": list(observation.evidence_ids),
                }
            )
        return gaps

    @staticmethod
    def _brand_evidence(
        brand_entities: list[BrandEntity], observations: list[SourceObservation]
    ) -> tuple[list[str], list[str]]:
        """Evidence ids and reference URLs the brand can legitimately cite."""

        evidence_ids: list[str] = []
        urls: list[str] = []
        for entity in brand_entities:
            evidence_ids.extend(entity.evidence_ids)
            urls.extend(entity.official_product_urls)
            for profile in entity.verified_external_profiles:
                evidence_ids.extend(str(item) for item in (profile.get("evidence_ids") or []))
                if profile.get("url"):
                    urls.append(str(profile["url"]))
        for observation in observations:
            if observation.research_purpose in {"brand_evidence", "case_study"} or (
                observation.source_type
                in {
                    "case_study",
                    "official_documentation",
                    "product_documentation",
                    "github_issue",
                    "brand_website",
                }
            ):
                evidence_ids.append(observation.id)
        return sorted(dict.fromkeys(item for item in evidence_ids if item)), list(
            dict.fromkeys(item for item in urls if item)
        )

    def _information_gain(
        self,
        *,
        brand: BrandProfile,
        brand_entities: list[BrandEntity],
        signals: dict[str, Any],
        observations: list[SourceObservation],
    ) -> dict[str, Any]:
        """Structured information-gain opportunities plus an aggregate readiness.

        A competitor gap is only an opportunity if the brand can plausibly fill
        it.  When a gap needs original data or a first-hand case the brand has
        not supplied, the item is ``NEEDS_EVIDENCE`` and the aggregate readiness
        drops, so a high-potential-but-unevidenced topic never gets a false high
        score.
        """
        brand_evidence_ids, _ = self._brand_evidence(brand_entities, observations)
        gaps = self._observation_gaps(observations)
        items: list[dict[str, Any]] = []
        missing: list[str] = []

        def add(
            gap_type: str,
            competitor_coverage: str,
            contribution: str,
            evidence_ids: list[str],
            missing_evidence: list[str],
            readiness: str,
            confidence: int,
        ) -> None:
            items.append(
                {
                    "gap_type": gap_type,
                    "competitor_coverage": competitor_coverage,
                    "proposed_unique_contribution": contribution,
                    "brand_evidence_ids": [item for item in evidence_ids if item],
                    "missing_evidence": list(missing_evidence),
                    "readiness": readiness,
                    "confidence": confidence,
                }
            )
            missing.extend(missing_evidence)

        for question in signals.get("unanswered_questions", [])[:3]:
            ready = bool(brand_evidence_ids)
            add(
                "question_coverage",
                f"The PAA question '{question}' is not answered by the current site snapshot.",
                f"Answer '{question}' directly with the brand's real product context.",
                brand_evidence_ids,
                [] if ready else [f"A documented, first-party answer to '{question}'"],
                "READY" if ready else "NEEDS_EVIDENCE",
                55,
            )

        if signals["organic"] and "comparison" not in signals["content_types"]:
            ready = bool(brand_evidence_ids)
            add(
                "decision_framework",
                "Competing results do not lead with a comparison or decision framework.",
                "A brand-grounded comparison and explicit selection criteria.",
                brand_evidence_ids,
                [] if ready else ["Product capability facts suitable for a comparison"],
                "READY" if ready else "NEEDS_EVIDENCE",
                50,
            )

        data_gaps = [
            gap
            for gap in gaps
            if any(
                word in gap["viewpoint"].lower()
                for word in ("data", "statistic", "research", "benchmark", "original")
            )
        ]
        if data_gaps:
            gap_ids = [gap["observation_id"] for gap in data_gaps]
            add(
                "original_data",
                "Observed competitor coverage reports no original data or benchmarks.",
                "Original measurements, benchmarks or study results the brand can publish.",
                gap_ids,
                ["Original measurements, benchmark data or study results"],
                "NEEDS_EVIDENCE",
                45,
            )

        technical_gaps = [
            gap
            for gap in gaps
            if gap["source_type"] in {"official_documentation", "github_issue", "technical_article"}
            or "implementation" in gap["viewpoint"].lower()
            or "how to" in gap["viewpoint"].lower()
        ]
        if technical_gaps:
            gap_ids = [gap["observation_id"] for gap in technical_gaps]
            add(
                "technical_depth",
                "Observed sources show thin implementation detail.",
                "Concrete implementation mechanics, constraints and engineering trade-offs.",
                gap_ids + brand_evidence_ids,
                []
                if (gap_ids or brand_evidence_ids)
                else ["Engineering detail the brand can share publicly"],
                "READY" if (gap_ids or brand_evidence_ids) else "NEEDS_EVIDENCE",
                55,
            )

        case_evidence = [
            observation
            for observation in observations
            if observation.source_type == "case_study" or observation.research_purpose == "case_study"
        ]
        if case_evidence:
            add(
                "first_hand_experience",
                "Competitor coverage lacks a documented first-hand deployment.",
                "A real project story with verifiable details and outcomes.",
                [observation.id for observation in case_evidence],
                [],
                "READY",
                65,
            )

        insight_gaps = [gap for gap in gaps if "gap" in gap["viewpoint"].lower()]
        if insight_gaps:
            add(
                "novel_insight",
                "Practitioner observations point at an angle competitors have not covered.",
                insight_gaps[0]["viewpoint"],
                [gap["observation_id"] for gap in insight_gaps],
                [],
                "READY",
                60,
            )

        for item in items:
            if item["readiness"] == "NEEDS_EVIDENCE":
                missing.extend(item["missing_evidence"])
        readiness = (
            "READY"
            if any(item["readiness"] == "READY" for item in items)
            else ("NEEDS_EVIDENCE" if items else "UNVERIFIED")
        )
        return {
            "items": items,
            "readiness": readiness,
            "missing_evidence": list(dict.fromkeys(item for item in missing if item)),
        }

    @staticmethod
    def _brand_fit(keyword: str, core_features: list[str], brand_entities: list[BrandEntity]) -> int:
        wanted = _tokens(keyword)
        if not wanted:
            return 60
        named = any(
            entity.canonical_name and (_tokens(entity.canonical_name) & wanted)
            for entity in brand_entities
        )
        if named:
            return 90
        feature_tokens = _tokens(" ".join(core_features))
        overlap = len(wanted & feature_tokens) / len(wanted) if feature_tokens else 0.0
        if overlap >= 0.5:
            return 85
        if overlap > 0:
            return 70
        confirmed = [entity for entity in brand_entities if entity.identity_status == "USER_CONFIRMED"]
        return 65 if confirmed else 55

    @staticmethod
    def _intent_alignment(intent: str) -> int:
        return 100 if intent in {"commercial", "transactional"} else 60

    @staticmethod
    def _default_semantic_coverage() -> dict[str, Any]:
        return {
            "status": "UNKNOWN",
            "basis": "no_page_body_evidence",
            "intent_match": None,
            "entity_coverage": None,
            "relation_coverage": None,
            "question_coverage": None,
            "explanation_depth": None,
            "evidence_ids": [],
            "note": (
                "Without page-body evidence the planner does not assume the topic is or is not "
                "covered. The host agent can record a semantic judgement."
            ),
        }

    @classmethod
    def _decision_for(cls, coverage: int, semantic_status: str) -> str:
        if semantic_status == "COVERED":
            return DecisionType.IGNORE.value
        if semantic_status in {"GAP", "PARTIAL"}:
            return DecisionType.CREATE.value
        # UNKNOWN: the lexical recall score is only a weak prior.
        return DecisionType.IGNORE.value if coverage >= 85 else DecisionType.CREATE.value

    @staticmethod
    def _overlap_risk(coverage: int, semantic_status: str) -> str:
        if semantic_status == "COVERED":
            return "high"
        if semantic_status == "PARTIAL":
            return "medium"
        if semantic_status == "GAP":
            return "low"
        if coverage >= 85:
            return "high"
        return "medium" if coverage >= 50 else "low"

    @staticmethod
    def _next_action(decision: str, semantic_status: str) -> str:
        if decision == DecisionType.IGNORE.value:
            return "Do not create a competing page"
        if semantic_status == "PARTIAL":
            return "Extend the existing page rather than create a competing one"
        return "Create a brief"

    @staticmethod
    def _content_gap(signals: dict[str, Any]) -> str:
        parts: list[str] = []
        if signals["content_types"]:
            parts.append("Competing content types observed: " + ", ".join(signals["content_types"]))
        if signals["paa"]:
            parts.append(
                f"{signals['unanswered_paa']} of {len(signals['paa'])} People-Also-Ask questions are "
                "not answered by the current site snapshot"
            )
        if signals["related"]:
            parts.append("Related searches not yet covered: " + ", ".join(signals["related"][:3]))
        parts.append(
            "an AI overview is present (a saturation caveat, not a proven demand ceiling)"
            if signals["aio_covered"]
            else "no AI overview was observed for this query"
        )
        return "; ".join(parts) + "."

    def run_topic_research(
        self, *, workspace_id: str, research_run_id: str, confirmation_token: str
    ) -> dict[str, Any]:
        workspace_id = self._workspace(workspace_id)
        self._require_research_provider()
        run = self.store.get(research_run_id, workspace_id=workspace_id, object_type="research_run")
        assert isinstance(run, ResearchRun)
        if run.status != ResearchStatus.AWAITING_CONFIRMATION.value:
            raise StateError("research run is not awaiting confirmation")
        estimate = self.store.get(run.estimate_id, workspace_id=workspace_id, object_type="research_estimate")
        assert isinstance(estimate, ResearchEstimate)
        if confirmation_token != estimate.confirmation_token:
            raise StateError("a matching explicit research confirmation is required")
        if estimate.estimated_cost_usd > estimate.budget_cap_usd:
            run.status = ResearchStatus.BUDGET_EXCEEDED.value
            self.store.save(run)
            raise BudgetError("estimated research cost exceeds the approved budget")
        brand = self.store.get(run.brand_id or "", workspace_id=workspace_id, object_type="brand_profile")
        assert isinstance(brand, BrandProfile)
        if brand.context_status != "CONFIRMED":
            raise StateError("brand context must be explicitly confirmed before running research")
        snapshot = self._latest_snapshot(workspace_id, brand.id) or SiteSnapshot(
            id="none",
            workspace_id=workspace_id,
            brand_id=brand.id,
            source="none",
            status="COMPLETED",
            lineage=[],
            website="",
        )
        observations = [
            item
            for item in self.store.list(
                workspace_id=workspace_id, object_type="source_observation", lineage_contains=run.id
            )
            if isinstance(item, SourceObservation)
        ]
        brand_entities = self._brand_entities(workspace_id=workspace_id, brand_id=brand.id)
        run.status = ResearchStatus.RUNNING.value
        self.store.save(run)
        evidences: list[ResearchEvidence] = []
        cache_hits = 0
        actual_cost = 0.0
        candidates: list[str] = []
        try:
            for seed in run.seed_topics:
                evidence, hit = self._evidence_from_provider(
                    workspace_id=workspace_id,
                    brand_id=brand.id,
                    run=run,
                    provider=self._provider_name(),
                    operation="expand",
                    material=seed,
                    evidence_type="keyword",
                    fetch=lambda seed=seed: self.keyword_provider.expand(
                        seed, market=run.market, language=run.language
                    ),
                )
                evidences.append(evidence)
                cache_hits += int(hit)
                actual_cost += 0 if hit else evidence.cost_usd
                self._enforce_budget(estimate, actual_cost)
                candidates.extend(evidence.payload.get("keywords", []))
            candidates = list(dict.fromkeys(str(item) for item in candidates if item))[:15]
            metric_evidence, hit = self._evidence_from_provider(
                workspace_id=workspace_id,
                brand_id=brand.id,
                run=run,
                provider=self._provider_name(),
                operation="metrics",
                material=candidates,
                evidence_type="keyword",
                fetch=lambda: self.keyword_provider.keyword_metrics(
                    candidates, market=run.market, language=run.language
                ),
            )
            evidences.append(metric_evidence)
            cache_hits += int(hit)
            actual_cost += 0 if hit else metric_evidence.cost_usd
            self._enforce_budget(estimate, actual_cost)
            metrics = {
                row["keyword"]: row
                for row in metric_evidence.payload.get("keywords", [])
                if row.get("keyword")
            }
            opportunities: list[TopicOpportunity] = []
            for keyword in candidates[:15]:
                serp_evidence, hit = self._evidence_from_provider(
                    workspace_id=workspace_id,
                    brand_id=brand.id,
                    run=run,
                    provider=self._provider_name(),
                    operation="serp",
                    material=keyword,
                    evidence_type="serp",
                    fetch=lambda keyword=keyword: self.serp_provider.search(
                        keyword, market=run.market, language=run.language, device=run.device
                    ),
                )
                evidences.append(serp_evidence)
                cache_hits += int(hit)
                actual_cost += 0 if hit else serp_evidence.cost_usd
                self._enforce_budget(estimate, actual_cost)
                signals = self._serp_signals(serp_evidence.payload, snapshot)
                page_evidence_ids: list[str] = []
                page_evidence = self._competitor_evidence_from_serp(
                    workspace_id=workspace_id,
                    brand_id=brand.id,
                    run=run,
                    serp_evidence=serp_evidence,
                )
                if page_evidence:
                    evidences.append(page_evidence)
                    page_evidence_ids.append(page_evidence.id)
                matched = self._relevant_observations(keyword, observations)
                competitor_summary = self._competitor_summary(page_evidence, matched)
                metric = metrics.get(keyword, {})
                volume, difficulty = int(metric.get("volume") or 0), int(metric.get("difficulty") or 50)
                demand = min(100, round(math.log10(max(volume, 1)) / 4 * 100))
                coverage, closest = self._coverage(keyword, snapshot)
                wanted = _tokens(keyword)
                seeds_tokens = set()
                for topic in run.seed_topics:
                    seeds_tokens |= _tokens(topic)
                # The user-confirmed core features are part of the relevance
                # baseline, so a topic that matches the product's real
                # capabilities ranks higher than an unrelated one.
                seeds_tokens |= _tokens(" ".join(brand.core_features))
                overlap = len(wanted & seeds_tokens) / len(wanted) if wanted else 0
                business = 85 if overlap >= 0.5 else 65
                brand_fit = self._brand_fit(keyword, brand.core_features, brand_entities)
                feasibility = max(5, min(95, 100 - difficulty))
                intent = self._intent(keyword, metric.get("intent"))
                information_gain = self._information_gain(
                    brand=brand,
                    brand_entities=brand_entities,
                    signals=signals,
                    observations=matched,
                )
                readiness = information_gain["readiness"]
                ready_items = sum(
                    1 for item in information_gain["items"] if item["readiness"] == "READY"
                )
                info_gain_score = (
                    round(100 * ready_items / len(information_gain["items"]))
                    if information_gain["items"]
                    else 0
                )
                semantic = self._default_semantic_coverage()
                score, breakdown = self._score(
                    demand=demand,
                    business=business,
                    brand=brand_fit,
                    feasibility=feasibility,
                    intent_alignment=self._intent_alignment(intent),
                    structural_signal=signals["serp_structural_signal"],
                    information_gain=info_gain_score,
                    evidence_readiness=_READINESS_SCORE.get(readiness, 20),
                    coverage=coverage,
                    aio_penalty=-5 if signals["aio_covered"] else 0,
                )
                decision = self._decision_for(coverage, semantic["status"])
                opportunity = TopicOpportunity(
                    id=new_id("opportunity"),
                    workspace_id=workspace_id,
                    brand_id=brand.id,
                    source="decision_engine",
                    status="COMPLETED",
                    lineage=[
                        run.id,
                        brand.id,
                        metric_evidence.id,
                        serp_evidence.id,
                        *page_evidence_ids,
                        *[item.id for item in matched],
                    ],
                    research_run_id=run.id,
                    topic=keyword,
                    primary_keyword=keyword,
                    secondary_keywords=[item for item in candidates if item != keyword][:3],
                    decision_type=decision,
                    search_intent=intent,
                    business_relevance=business,
                    brand_fit=brand_fit,
                    search_demand=demand,
                    competition_level=difficulty,
                    ranking_feasibility=feasibility,
                    existing_site_coverage=coverage,
                    closest_existing_url=closest,
                    overlap_risk=self._overlap_risk(coverage, semantic["status"]),
                    serp_summary=(
                        f"{serp_evidence.payload.get('dominant_page_type', 'unknown')} SERP with "
                        f"{len(signals['organic'])} organic results across "
                        f"{len(signals['unique_domains'])} domains; AI overview "
                        f"{'present' if signals['aio_covered'] else 'absent'}, "
                        f"{len(signals['paa'])} PAA, {len(signals['related'])} related."
                    ),
                    competitor_summary=competitor_summary,
                    content_gap=self._content_gap(signals),
                    recommended_angle=(
                        f"A practical {brand.primary_scenario or 'implementation'} guide for "
                        f"{brand.target_customer}: {keyword}."
                    ),
                    why_now=(
                        "Observable demand and a traceable content gap support a focused page; "
                        "AI overview presence is treated as a saturation caveat."
                    ),
                    expected_value="Qualified discovery demand with a differentiated implementation angle.",
                    aio_saturation="covered" if signals["aio_covered"] else "unobserved",
                    confidence=self._confidence(volume, signals, bool(matched), readiness),
                    score=score,
                    score_breakdown=breakdown,
                    evidence_ids=[
                        metric_evidence.id,
                        serp_evidence.id,
                        *page_evidence_ids,
                        *[item.id for item in matched],
                    ],
                    recommended_next_action=self._next_action(decision, semantic["status"]),
                    brand_entity_ids=[entity.id for entity in brand_entities],
                    information_gain=information_gain["items"],
                    evidence_readiness=readiness,
                    semantic_coverage=semantic,
                    missing_evidence=information_gain["missing_evidence"],
                    scoring_version=SCORING_VERSION,
                )
                self.store.save(opportunity)
                opportunities.append(opportunity)
        except BudgetError:
            run.status = ResearchStatus.BUDGET_EXCEEDED.value
            self.store.save(run)
            raise
        except Exception:
            run.status = (
                ResearchStatus.PARTIAL_RESULT.value if evidences else ResearchStatus.PROVIDER_FAILED.value
            )
            self.store.save(run)
            raise
        pack = ResearchEvidencePack(
            id=new_id("evidence_pack"),
            workspace_id=workspace_id,
            brand_id=brand.id,
            source="research_orchestrator",
            status="COMPLETED",
            lineage=[run.id, brand.id],
            research_run_id=run.id,
            evidence_ids=list(dict.fromkeys(item.id for item in evidences)),
            cache_hits=cache_hits,
            actual_cost_usd=round(actual_cost, 4),
        )
        self.store.save(pack)
        run.status = ResearchStatus.COMPLETED.value
        run.evidence_pack_id = pack.id
        run.actual_cost_usd = pack.actual_cost_usd
        self.store.save(run)
        ranked = sorted(opportunities, key=lambda item: item.score, reverse=True)[:15]
        provider_status = self.get_data_provider_status(workspace_id=workspace_id)
        return {
            "research_run": run.to_dict(),
            "research_evidence_pack": pack.to_dict(),
            "opportunities": [item.to_dict() for item in ranked],
            "brand_entity_actions": self._brand_entity_recommendations(brand, brand_entities),
            "cost_context": {
                "data_mode": provider_status["data_mode"],
                "estimated_cost_usd": run.estimated_cost_usd,
                "actual_cost_usd": run.actual_cost_usd,
                "budget_cap_usd": estimate.budget_cap_usd,
                "unbudgeted_collection_note": (
                    "Host-agent browser / yt-dlp collection is not billed through this plugin budget."
                ),
            },
        }

    @staticmethod
    def _relevant_observations(
        keyword: str, observations: list[SourceObservation]
    ) -> list[SourceObservation]:
        needle = keyword.lower().strip()
        tokens = _tokens(keyword)
        primary = max(tokens, key=len) if tokens else needle
        matched: list[SourceObservation] = []
        for observation in observations:
            blob = f"{observation.title} {observation.viewpoint} {observation.url}".lower()
            if needle in blob or (primary and primary in blob):
                matched.append(observation)
        return matched[:5]

    @staticmethod
    def _competitor_summary(
        page_evidence: ResearchEvidence | None, observations: list[SourceObservation]
    ) -> str:
        parts: list[str] = []
        if page_evidence is not None:
            parts.append(
                f"Top result is a {page_evidence.payload.get('page_type', 'page')}; "
                f"{page_evidence.payload.get('coverage_gap', 'coverage needs review')}"
            )
        for observation in observations[:2]:
            if observation.viewpoint:
                parts.append(f"{observation.source_type}: {observation.viewpoint}")
        return " ".join(parts)

    @staticmethod
    def _confidence(
        volume: int, signals: dict[str, Any], has_observations: bool, evidence_readiness: str = "UNVERIFIED"
    ) -> int:
        confidence = 45
        if volume > 0:
            confidence += 10
        if signals["organic"]:
            confidence += 10
        if signals["paa"] or signals["related"]:
            confidence += 10
        if has_observations:
            confidence += 5
        # A gap the brand cannot yet evidence must not read as high confidence.
        if evidence_readiness == "READY":
            confidence += 15
        elif evidence_readiness == "NEEDS_EVIDENCE":
            confidence -= 10
        return max(0, min(90, confidence))

    # ---------------------------------------------------------- observations

    def record_source_observations(
        self,
        *,
        workspace_id: str,
        observations: list[dict[str, Any]],
        research_run_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist host-agent viewpoints with strict, idempotent validation.

        Observations are keyed by a deterministic hash so re-submitting the same
        viewpoint does not create a duplicate record.  Full source text,
        usernames and comment bodies are never accepted.
        """
        workspace_id = self._workspace(workspace_id)
        if not isinstance(observations, list) or not observations:
            raise ValidationError("at least one observation is required")
        brand_id: str | None = None
        if research_run_id:
            run = self.store.get(research_run_id, workspace_id=workspace_id, object_type="research_run")
            assert isinstance(run, ResearchRun)
            brand_id = run.brand_id
        saved: list[dict[str, Any]] = []
        for raw in observations:
            if not isinstance(raw, dict):
                raise ValidationError("each observation must be an object")
            source_type = str(raw.get("source_type") or "").strip()
            url = str(raw.get("url") or "").strip()
            viewpoint = str(raw.get("viewpoint") or "").strip()
            if source_type not in OBSERVATION_SOURCE_TYPES:
                # Legacy podcast records remain readable, but no new podcast
                # observation can be submitted.
                raise ValidationError("unsupported observation source type")
            try:
                confidence = int(raw.get("confidence") or 0)
            except (TypeError, ValueError) as exc:
                raise ValidationError("observation confidence must be an integer from 0 to 100") from exc
            try:
                credibility = int(raw.get("credibility") or 0)
                relevance = int(raw.get("relevance") or 0)
            except (TypeError, ValueError) as exc:
                raise ValidationError("observation credibility/relevance must be integers") from exc
            quotes = [str(item).strip() for item in (raw.get("quotes") or []) if str(item).strip()]
            cache_key = hashlib.sha256(
                repr(("observation", workspace_id, source_type, url, viewpoint)).encode("utf-8")
            ).hexdigest()
            observation_id = f"observation_{cache_key[:32]}"
            observation = SourceObservation(
                id=observation_id,
                workspace_id=workspace_id,
                brand_id=brand_id,
                source="host_agent",
                status="COMPLETED",
                lineage=[item for item in [research_run_id] if item],
                research_run_id=research_run_id,
                source_type=source_type,
                url=url,
                title=str(raw.get("title") or ""),
                fetched_at=str(raw.get("fetched_at") or "").strip(),
                quotes=quotes,
                viewpoint=viewpoint,
                confidence=confidence,
                evidence_ids=[str(item) for item in (raw.get("evidence_ids") or [])],
                aio_saturation=str(raw.get("aio_saturation") or "unobserved"),
                credibility=credibility,
                relevance=relevance,
                research_purpose=str(raw.get("research_purpose") or "").strip(),
                cache_key=cache_key,
                expires_at=self._expiry(),
            )
            self.store.save(observation)
            saved.append(observation.to_dict())
        return {"observations": saved, "count": len(saved)}

    def list_source_observations(
        self, *, workspace_id: str, research_run_id: str | None = None
    ) -> list[dict[str, Any]]:
        workspace_id = self._workspace(workspace_id)
        if research_run_id:
            self.store.get(research_run_id, workspace_id=workspace_id, object_type="research_run")
            rows = self.store.list(
                workspace_id=workspace_id,
                object_type="source_observation",
                lineage_contains=research_run_id,
            )
        else:
            rows = self.store.list(workspace_id=workspace_id, object_type="source_observation")
        return [item.to_dict() for item in rows]

    def record_source_attempts(
        self,
        *,
        workspace_id: str,
        attempts: list[dict[str, Any]],
        research_run_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist external sources the host agent tried but could not read.

        The gate requires external evidence.  A blocked or unavailable source is
        recorded here with a method, an outcome and a reason so the gap stays
        visible instead of being silently skipped or fabricated.  Attempts are
        keyed deterministically, so re-submitting the same attempt is idempotent
        and an attempt never counts as research evidence.
        """
        workspace_id = self._workspace(workspace_id)
        if not isinstance(attempts, list) or not attempts:
            raise ValidationError("at least one source attempt is required")
        brand_id: str | None = None
        if research_run_id:
            run = self.store.get(
                research_run_id, workspace_id=workspace_id, object_type="research_run"
            )
            assert isinstance(run, ResearchRun)
            brand_id = run.brand_id
        saved: list[dict[str, Any]] = []
        for raw in attempts:
            if not isinstance(raw, dict):
                raise ValidationError("each source attempt must be an object")
            source_type = str(raw.get("source_type") or "").strip()
            if source_type not in OBSERVATION_SOURCE_TYPES:
                raise ValidationError("unsupported source_attempt source type")
            method = str(raw.get("method") or "browser").strip()
            if method not in SOURCE_ATTEMPT_METHODS:
                raise ValidationError("unsupported source_attempt method")
            outcome = str(raw.get("outcome") or "blocked").strip()
            if outcome not in SOURCE_ATTEMPT_OUTCOMES:
                raise ValidationError("unsupported source_attempt outcome")
            url = str(raw.get("url") or "").strip()
            reason = str(raw.get("reason") or "").strip()
            attempted_at = str(raw.get("attempted_at") or "").strip()
            if not url or not reason or not attempted_at:
                raise ValidationError("source_attempt requires url, reason and attempted_at")
            key = hashlib.sha256(
                repr(("attempt", workspace_id, source_type, url, method, outcome)).encode("utf-8")
            ).hexdigest()
            record = SourceAttempt(
                id=f"attempt_{key[:32]}",
                workspace_id=workspace_id,
                brand_id=brand_id,
                source="host_agent",
                status=outcome.upper(),
                lineage=[item for item in [research_run_id] if item],
                research_run_id=research_run_id,
                source_type=source_type,
                url=url,
                method=method,
                outcome=outcome,
                reason=reason,
                attempted_at=attempted_at,
            )
            self.store.save(record)
            saved.append(record.to_dict())
        return {"attempts": saved, "count": len(saved)}

    def list_source_attempts(
        self, *, workspace_id: str, research_run_id: str | None = None
    ) -> list[dict[str, Any]]:
        workspace_id = self._workspace(workspace_id)
        if research_run_id:
            rows = self._run_attempts(workspace_id=workspace_id, run_id=research_run_id)
        else:
            rows = [
                item
                for item in self.store.list(workspace_id=workspace_id, object_type="source_attempt")
                if isinstance(item, SourceAttempt)
            ]
        return [item.to_dict() for item in rows]

    # --------------------------------------------- topical entities / analysis

    def record_topical_entities(
        self,
        *,
        workspace_id: str,
        research_run_id: str,
        entities: list[dict[str, Any]],
        relations: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Persist topic entities and their relationships from agent research.

        Entities are idempotent by canonical name; a re-submitted entity updates
        in place.  Relations reference entities by canonical name and are only
        created when both endpoints exist.  Inferred relationships stay
        ``INFERRED`` unless the agent marks evidence-backed ones ``VERIFIED``.
        """
        workspace_id = self._workspace(workspace_id)
        run = self.store.get(research_run_id, workspace_id=workspace_id, object_type="research_run")
        assert isinstance(run, ResearchRun)
        if not isinstance(entities, list) or not entities:
            raise ValidationError("at least one topical entity is required")
        saved: dict[str, TopicalEntity] = {}
        for raw in entities:
            if not isinstance(raw, dict):
                raise ValidationError("each topical entity must be an object")
            name = str(raw.get("canonical_name") or "").strip()
            if not name:
                raise ValidationError("each topical entity requires a canonical_name")
            entity_type = str(raw.get("entity_type") or "Concept")
            if entity_type not in TOPICAL_ENTITY_TYPES:
                raise ValidationError(f"unsupported topical entity type: {entity_type}")
            try:
                confidence = int(raw.get("confidence") or 0)
            except (TypeError, ValueError) as exc:
                raise ValidationError("topical entity confidence must be an integer") from exc
            status = str(raw.get("verification_status") or "INFERRED")
            entity_id = self._entity_id(workspace_id, research_run_id, name)
            existing = None
            try:
                existing = self.store.get(
                    entity_id, workspace_id=workspace_id, object_type="topical_entity"
                )
            except NotFoundError:
                existing = None
            entity = existing if isinstance(existing, TopicalEntity) else TopicalEntity(
                id=entity_id,
                workspace_id=workspace_id,
                brand_id=run.brand_id,
                source="host_agent",
                status="COMPLETED",
                lineage=[research_run_id],
                research_run_id=research_run_id,
            )
            entity.canonical_name = name
            entity.aliases = self._clean_aliases(raw.get("aliases"))
            entity.entity_type = entity_type
            entity.description = str(raw.get("description") or "")
            entity.evidence_ids = [str(item) for item in (raw.get("evidence_ids") or [])]
            entity.confidence = max(0, min(100, confidence))
            entity.verification_status = status
            entity.touch()
            self.store.save(entity)
            saved[name.lower()] = entity

        saved_relations: list[EntityRelation] = []
        for raw in relations or []:
            if not isinstance(raw, dict):
                raise ValidationError("each relation must be an object")
            subject_name = str(raw.get("subject") or "").strip().lower()
            object_name = str(raw.get("object") or "").strip().lower()
            relation_type = str(raw.get("relation") or "").strip()
            if subject_name not in saved or object_name not in saved:
                raise ValidationError("a relation must reference two recorded entities by name")
            if relation_type not in RELATION_TYPES:
                raise ValidationError(f"unsupported relation type: {relation_type}")
            subject = saved[subject_name]
            object_entity = saved[object_name]
            relation_id = self._entity_id(
                workspace_id, research_run_id, f"{subject.id}:{relation_type}:{object_entity.id}"
            )
            existing_relation = None
            try:
                existing_relation = self.store.get(
                    relation_id, workspace_id=workspace_id, object_type="entity_relation"
                )
            except NotFoundError:
                existing_relation = None
            relation = existing_relation if isinstance(existing_relation, EntityRelation) else EntityRelation(
                id=relation_id,
                workspace_id=workspace_id,
                brand_id=run.brand_id,
                source="host_agent",
                status="COMPLETED",
                lineage=[research_run_id, subject.id, object_entity.id],
                research_run_id=research_run_id,
            )
            relation.subject_entity_id = subject.id
            relation.object_entity_id = object_entity.id
            relation.relation = relation_type
            relation.evidence_ids = [str(item) for item in (raw.get("evidence_ids") or [])]
            try:
                relation.confidence = max(0, min(100, int(raw.get("confidence") or 0)))
            except (TypeError, ValueError) as exc:
                raise ValidationError("relation confidence must be an integer") from exc
            relation.verification_status = str(raw.get("verification_status") or "INFERRED")
            relation.touch()
            self.store.save(relation)
            saved_relations.append(relation)

        return {
            "entities": [entity.to_dict() for entity in saved.values()],
            "relations": [relation.to_dict() for relation in saved_relations],
            "count": {"entities": len(saved), "relations": len(saved_relations)},
        }

    @staticmethod
    def _entity_id(workspace_id: str, research_run_id: str, material: str) -> str:
        value = repr(("entity", workspace_id, research_run_id, material.lower())).encode("utf-8")
        return f"entity_{hashlib.sha256(value).hexdigest()[:32]}"

    def list_topical_entities(self, *, workspace_id: str, research_run_id: str) -> dict[str, Any]:
        workspace_id = self._workspace(workspace_id)
        self.store.get(research_run_id, workspace_id=workspace_id, object_type="research_run")
        entities = self._run_topical_entities(workspace_id=workspace_id, run_id=research_run_id)
        relations = [
            item
            for item in self.store.list(
                workspace_id=workspace_id, object_type="entity_relation", lineage_contains=research_run_id
            )
            if isinstance(item, EntityRelation)
        ]
        return {
            "research_run_id": research_run_id,
            "entities": [entity.to_dict() for entity in entities],
            "relations": [relation.to_dict() for relation in relations],
            "count": {"entities": len(entities), "relations": len(relations)},
        }

    def record_opportunity_analysis(
        self,
        *,
        workspace_id: str,
        opportunity_id: str,
        semantic_coverage: dict[str, Any] | None = None,
        information_gain: list[dict[str, Any]] | None = None,
        entity_ids: list[str] | None = None,
        evidence_readiness: str | None = None,
        missing_evidence: list[str] | None = None,
    ) -> dict[str, Any]:
        """Write back the host agent's semantic judgement for one opportunity.

        The planner computes a baseline; the host agent, which can read page
        bodies and reason semantically, refines it.  A semantic COVERED
        judgement flips the decision to IGNORE; a GAP/PARTIAL judgement keeps it
        a CREATE (a PARTIAL is explicitly a page to extend, not to duplicate).
        This is the only place an inferred entity or coverage judgement becomes
        recorded, and it stays marked per the supplied verification status.
        """
        workspace_id = self._workspace(workspace_id)
        opportunity = self.store.get(
            opportunity_id, workspace_id=workspace_id, object_type="topic_opportunity"
        )
        assert isinstance(opportunity, TopicOpportunity)
        if semantic_coverage is not None:
            if not isinstance(semantic_coverage, dict):
                raise ValidationError("semantic_coverage must be an object")
            status = str(semantic_coverage.get("status") or "UNKNOWN")
            if status not in SEMANTIC_COVERAGE_STATUSES:
                raise ValidationError("unsupported semantic coverage status")
            coverage_record = {**self._default_semantic_coverage(), **semantic_coverage}
            coverage_record["status"] = status
            coverage_record["evidence_ids"] = [
                str(item) for item in (semantic_coverage.get("evidence_ids") or [])
            ]
            opportunity.semantic_coverage = coverage_record
            opportunity.decision_type = self._decision_for(
                opportunity.existing_site_coverage, status
            )
            opportunity.overlap_risk = self._overlap_risk(opportunity.existing_site_coverage, status)
            opportunity.recommended_next_action = self._next_action(
                opportunity.decision_type, status
            )
        if information_gain is not None:
            if not isinstance(information_gain, list):
                raise ValidationError("information_gain must be a list")
            normalized: list[dict[str, Any]] = []
            for item in information_gain:
                if not isinstance(item, dict) or not str(item.get("gap_type") or "").strip():
                    raise ValidationError("each information gain item requires a gap_type")
                readiness = str(item.get("readiness") or "UNVERIFIED")
                if readiness not in EVIDENCE_READINESS:
                    raise ValidationError("unsupported information gain readiness")
                normalized.append(
                    {
                        "gap_type": str(item["gap_type"]),
                        "competitor_coverage": str(item.get("competitor_coverage") or ""),
                        "proposed_unique_contribution": str(
                            item.get("proposed_unique_contribution") or ""
                        ),
                        "brand_evidence_ids": [str(x) for x in (item.get("brand_evidence_ids") or [])],
                        "missing_evidence": [str(x) for x in (item.get("missing_evidence") or [])],
                        "readiness": readiness,
                        "confidence": int(item.get("confidence") or 0),
                    }
                )
            opportunity.information_gain = normalized
        if entity_ids is not None:
            opportunity.entity_ids = [str(item) for item in entity_ids if str(item).strip()]
        if evidence_readiness is not None:
            if evidence_readiness not in EVIDENCE_READINESS:
                raise ValidationError("unsupported evidence readiness")
            opportunity.evidence_readiness = evidence_readiness
        if missing_evidence is not None:
            opportunity.missing_evidence = [str(item) for item in missing_evidence if str(item).strip()]
        opportunity.touch()
        self.store.save(opportunity)
        return opportunity.to_dict()

    # --------------------------------------------------------------- reads

    def list_topic_opportunities(self, *, workspace_id: str, research_run_id: str) -> list[dict[str, Any]]:
        self.store.get(
            research_run_id, workspace_id=self._workspace(workspace_id), object_type="research_run"
        )
        rows = self.store.list(
            workspace_id=workspace_id, object_type="topic_opportunity", lineage_contains=research_run_id
        )
        return [
            item.to_dict() for item in sorted(rows, key=lambda item: getattr(item, "score", 0), reverse=True)
        ]

    def get_topic_opportunity(self, *, workspace_id: str, opportunity_id: str) -> dict[str, Any]:
        return self.store.get(
            opportunity_id, workspace_id=self._workspace(workspace_id), object_type="topic_opportunity"
        ).to_dict()

    def get_research_evidence(self, *, workspace_id: str, evidence_id: str) -> dict[str, Any]:
        return self.store.get(
            evidence_id, workspace_id=self._workspace(workspace_id), object_type="research_evidence"
        ).to_dict()

    # ------------------------------------------------ research gate / cluster

    @staticmethod
    def _observation_counts(
        observations: list[SourceObservation],
    ) -> tuple[dict[str, int], dict[str, int]]:
        counts = {"serp": 0, **{family: 0 for family in EVIDENCE_FAMILIES}}
        community = {family: 0 for family in COMMUNITY_SOURCE_FAMILIES}
        for observation in observations:
            if observation.source_type in SERP_SOURCE_TYPES:
                counts["serp"] += 1
            for family, source_types in EVIDENCE_FAMILIES.items():
                if observation.source_type in source_types:
                    counts[family] += 1
            for family, source_types in COMMUNITY_SOURCE_FAMILIES.items():
                if observation.source_type in source_types:
                    community[family] += 1
        return counts, community

    def _run_observations(self, *, workspace_id: str, run_id: str) -> list[SourceObservation]:
        return [
            item
            for item in self.store.list(
                workspace_id=workspace_id, object_type="source_observation", lineage_contains=run_id
            )
            if isinstance(item, SourceObservation)
        ]

    def _run_attempts(self, *, workspace_id: str, run_id: str) -> list[SourceAttempt]:
        return [
            item
            for item in self.store.list(
                workspace_id=workspace_id, object_type="source_attempt", lineage_contains=run_id
            )
            if isinstance(item, SourceAttempt)
        ]

    def _cluster_readiness(
        self,
        *,
        workspace_id: str,
        run: ResearchRun,
        observations: list[SourceObservation] | None = None,
    ) -> dict[str, Any]:
        """Report whether the external-research gate is satisfied.

        The gate requires the rendered SERP to have been read (at least one
        ``serp_result`` observation) and at least one *external* evidence family
        the host agent actually read: ``community``, ``technical``, ``business``
        or ``competitor``.  The first-party ``brand`` family (the brand's own
        site, accounts or profiles) never satisfies the requirement on its own.
        Covering two or more external families is ``FULL_COVERAGE``; one is
        ``DEGRADED_MINIMUM``; none blocks the cluster.
        """
        rows = (
            observations
            if observations is not None
            else self._run_observations(workspace_id=workspace_id, run_id=run.id)
        )
        counts, community = self._observation_counts(rows)
        covered = [family for family in EVIDENCE_FAMILIES if counts[family] > 0]
        missing = [family for family in EVIDENCE_FAMILIES if counts[family] == 0]
        external_covered = [family for family in EXTERNAL_EVIDENCE_FAMILIES if counts[family] > 0]
        external_missing = [family for family in EXTERNAL_EVIDENCE_FAMILIES if counts[family] == 0]
        serp_read = counts["serp"] > 0
        ready = serp_read and bool(external_covered)
        if not serp_read:
            gate_status = "BLOCKED_NO_SERP"
        elif not external_covered:
            gate_status = "BLOCKED_NO_EXTERNAL"
        elif len(external_covered) >= 2:
            gate_status = "FULL_COVERAGE"
        else:
            gate_status = "DEGRADED_MINIMUM"
        attempts = self._run_attempts(workspace_id=workspace_id, run_id=run.id)
        blocked_attempts = [attempt for attempt in attempts if attempt.outcome != "not_available"]
        return {
            "research_run_id": run.id,
            "serp_read": serp_read,
            "serp_observation_count": counts["serp"],
            "evidence_families": {family: counts[family] for family in EVIDENCE_FAMILIES},
            "families_covered": covered,
            "families_missing": missing,
            "external_families_covered": external_covered,
            "external_families_missing": external_missing,
            "community_counts": {family: community[family] for family in COMMUNITY_SOURCE_FAMILIES},
            "community_families_covered": [f for f in COMMUNITY_SOURCE_FAMILIES if community[f] > 0],
            "community_families_missing": [f for f in COMMUNITY_SOURCE_FAMILIES if community[f] == 0],
            "gate_status": gate_status,
            "attempts": [attempt.to_dict() for attempt in attempts],
            "blocked_attempts": [attempt.to_dict() for attempt in blocked_attempts],
            "required": {"serp_read": serp_read, "external_source_evidence": bool(external_covered)},
            "degraded": bool(ready and len(external_covered) < 2),
            "ready": ready,
        }

    def get_cluster_readiness(self, *, workspace_id: str, research_run_id: str) -> dict[str, Any]:
        workspace_id = self._workspace(workspace_id)
        run = self.store.get(research_run_id, workspace_id=workspace_id, object_type="research_run")
        assert isinstance(run, ResearchRun)
        return self._cluster_readiness(workspace_id=workspace_id, run=run)

    # Funnel heuristics.  BOFU markers win over MOFU over TOFU so a query like
    # "best crm software" lands at the decision stage rather than reading as a
    # generic "best" listicle.
    _BOFU_MARKERS = (
        "pricing",
        "price",
        "cost",
        "buy",
        "purchase",
        "software",
        "tool",
        "tools",
        "platform",
        "vendor",
        "solution",
        "demo",
        "trial",
        "review",
        "roi",
        "procurement",
        "integration",
        "integrations",
    )
    _MOFU_MARKERS = (
        "strategy",
        "use case",
        "use cases",
        "best practice",
        "best practices",
        "comparison",
        "compare",
        " versus ",
        " vs ",
        "alternative",
        "alternatives",
        "framework",
        "how to choose",
        "implementation",
        "migration",
        "guide",
    )
    # Question forms are awareness queries even when they embed a commercial
    # keyword, so they win over the MOFU markers above.
    _TOFU_QUESTION_MARKERS = (
        "what is",
        "what are",
        "how to",
        "how do",
        "how does",
        "why ",
    )
    _TOFU_MARKERS = (
        "introduction",
        "basics",
        "definition",
        "meaning",
        "examples",
        "tips",
        "benefits",
        "overview",
        "tutorial",
    )

    @classmethod
    def _funnel_stage(cls, query: str, intent: str = "") -> str:
        text = f" {str(query or '').lower().strip()} "
        if any(marker in text for marker in cls._BOFU_MARKERS) or intent == "commercial":
            return "BOFU"
        if any(marker in text for marker in cls._TOFU_QUESTION_MARKERS):
            return "TOFU"
        if any(marker in text for marker in cls._MOFU_MARKERS):
            return "MOFU"
        if any(marker in text for marker in cls._TOFU_MARKERS):
            return "TOFU"
        return "MOFU"

    def _pillar_serp(
        self, *, workspace_id: str, pillar: TopicOpportunity
    ) -> tuple[dict[str, Any], str | None]:
        for evidence_id in pillar.evidence_ids:
            evidence = self.store.get(
                evidence_id, workspace_id=workspace_id, object_type="research_evidence"
            )
            if isinstance(evidence, ResearchEvidence) and evidence.evidence_type == "serp":
                return evidence.payload, evidence.id
        return {}, None

    def _cluster_candidates(
        self, *, workspace_id: str, run: ResearchRun, pillar: TopicOpportunity
    ) -> list[dict[str, Any]]:
        """Ordered supporting-page candidates with funnel stages.

        Candidates come from the sibling CREATE opportunities in the same run
        first, then the pillar SERP's People-Also-Ask questions and related
        searches.  Every candidate keeps the evidence ids it was derived from.
        """
        candidates: list[dict[str, Any]] = []
        seen: set[str] = {pillar.primary_keyword.strip().lower()}

        def add(
            query: str, *, evidence_ids: list[str], angle: str = "", intent: str = ""
        ) -> None:
            key = str(query or "").strip().lower()
            if not key or key in seen:
                return
            seen.add(key)
            candidates.append(
                {
                    "target_query": str(query).strip(),
                    "funnel_stage": self._funnel_stage(query, intent),
                    "evidence_ids": [item for item in evidence_ids if item],
                    "recommended_angle": angle,
                    "search_intent": intent,
                }
            )

        rows = self.store.list(
            workspace_id=workspace_id, object_type="topic_opportunity", lineage_contains=run.id
        )
        siblings = [
            item
            for item in rows
            if isinstance(item, TopicOpportunity)
            and item.id != pillar.id
            and item.decision_type == DecisionType.CREATE.value
        ]
        siblings.sort(key=lambda item: item.score, reverse=True)
        for item in siblings:
            add(
                item.primary_keyword,
                evidence_ids=item.evidence_ids,
                angle=item.recommended_angle,
                intent=item.search_intent,
            )

        payload, serp_evidence_id = self._pillar_serp(workspace_id=workspace_id, pillar=pillar)
        serp_ids = [serp_evidence_id] if serp_evidence_id else []
        for row in payload.get("paa") or []:
            question = str(row.get("question") or "") if isinstance(row, dict) else str(row)
            add(question, evidence_ids=serp_ids)
        for label in payload.get("related") or []:
            add(str(label), evidence_ids=serp_ids)
        return candidates

    MAX_CLUSTER_SUPPORTING = 6

    def _select_cluster_items(
        self, *, pillar: TopicOpportunity, candidates: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Select supporting pages by evidence value, not by a funnel mandate.

        A cluster spans whatever the research justifies.  When the evidence has
        no independent supporting query, the cluster is a single pillar page
        rather than a set of mechanically generated pages.
        """
        selected: list[dict[str, Any]] = []
        used: set[str] = {pillar.primary_keyword.strip().lower()}
        for candidate in candidates:
            if len(selected) >= self.MAX_CLUSTER_SUPPORTING:
                break
            key = candidate["target_query"].strip().lower()
            if not key or key in used:
                continue
            if not candidate.get("evidence_ids"):
                # No evidence behind it: not a page we can justify yet.
                continue
            used.add(key)
            selected.append(candidate)
        return selected

    @staticmethod
    def _cluster_objective(funnel_stage: str) -> str:
        return {
            "TOFU": "Build awareness and fully answer the top-of-funnel question.",
            "MOFU": "Support evaluation and comparison for readers choosing an approach.",
            "BOFU": "Help decision-stage readers select, justify and implement the topic.",
        }.get(funnel_stage, "Help qualified readers evaluate and implement the topic.")

    def _paa_questions(self, *, workspace_id: str, evidence_ids: list[str]) -> list[str]:
        for evidence_id in evidence_ids:
            try:
                evidence = self.store.get(
                    evidence_id, workspace_id=workspace_id, object_type="research_evidence"
                )
            except NotFoundError:
                continue
            if isinstance(evidence, ResearchEvidence) and evidence.evidence_type == "serp":
                return [
                    str(row.get("question"))
                    for row in (evidence.payload.get("paa") or [])
                    if isinstance(row, dict) and str(row.get("question") or "").strip()
                ]
        return []

    def _run_topical_entities(self, *, workspace_id: str, run_id: str) -> list[TopicalEntity]:
        return [
            item
            for item in self.store.list(
                workspace_id=workspace_id, object_type="topical_entity", lineage_contains=run_id
            )
            if isinstance(item, TopicalEntity)
        ]

    def _relevant_entity_names(
        self, *, workspace_id: str, run_id: str, target_query: str
    ) -> list[str]:
        wanted = _tokens(target_query)
        entities = self._run_topical_entities(workspace_id=workspace_id, run_id=run_id)
        matched = [
            entity.canonical_name
            for entity in entities
            if wanted & _tokens(" ".join([entity.canonical_name, *entity.aliases]))
        ]
        if not matched:
            matched = [entity.canonical_name for entity in entities[:5]]
        return list(dict.fromkeys(matched))[:8]

    @staticmethod
    def _evidence_sections(
        *,
        target_query: str,
        audience: str,
        questions: list[str],
        information_gain: list[dict[str, Any]],
        entity_names: list[str],
        required_evidence_ids: list[str],
        missing_evidence: list[str],
        internal_links: list[str],
    ) -> list[dict[str, Any]]:
        """Build outline sections from research evidence, not a fixed template.

        Every section states the reader question it answers, the entities it
        involves, where the information gain comes from, and which evidence it
        needs.  Generic "Definition / Benefits / Implementation" modules are not
        emitted when the evidence does not call for them.
        """

        gain_types = [str(item.get("gap_type")) for item in information_gain]

        def section(h2: str, reader_question: str, key_claim: str, gain: list[str]) -> dict[str, Any]:
            return {
                "h2": h2,
                "reader_question": reader_question,
                "key_claim": key_claim,
                "relevant_entities": entity_names,
                "information_gain": [item for item in gain if item],
                "required_evidence_ids": list(required_evidence_ids),
                "missing_evidence": list(missing_evidence),
                "internal_links": list(internal_links),
            }

        sections: list[dict[str, Any]] = []
        for question in questions[:3]:
            sections.append(
                section(
                    question,
                    question,
                    f"Answer '{question}' with verified first-party information.",
                    gain_types,
                )
            )
        if "decision_framework" in gain_types:
            sections.append(
                section(
                    f"Choosing {target_query}: criteria and trade-offs",
                    f"How should {audience} evaluate and choose among the options?",
                    "Explain the selection criteria and trade-offs with brand-grounded evidence.",
                    ["decision_framework"],
                )
            )
        if "technical_depth" in gain_types:
            sections.append(
                section(
                    "Implementation mechanics and constraints",
                    "How is this actually implemented, and what constrains it?",
                    "Describe concrete mechanics and constraints the brand can support.",
                    ["technical_depth"],
                )
            )
        if "first_hand_experience" in gain_types:
            sections.append(
                section(
                    "What a real deployment looks like",
                    "What does a real project or deployment look like in practice?",
                    "Tell a verifiable first-hand story with outcomes.",
                    ["first_hand_experience"],
                )
            )
        if not sections:
            sections.append(
                section(
                    f"{target_query} for {audience}",
                    f"What does {audience} need to understand about {target_query}?",
                    "Answer the primary query with the evidence on hand.",
                    gain_types,
                )
            )
        return sections

    def _outline_content_status(self, opportunity: TopicOpportunity) -> str:
        semantic = str(opportunity.semantic_coverage.get("status") or "UNKNOWN")
        if semantic == "COVERED":
            return "LOW_VALUE"
        if opportunity.evidence_readiness == "READY":
            return "READY_TO_BRIEF"
        if opportunity.evidence_readiness == "NEEDS_EVIDENCE":
            return "NEEDS_BRAND_EVIDENCE"
        return "NEEDS_RESEARCH"

    def _cluster_outline(
        self,
        *,
        workspace_id: str,
        brand: BrandProfile,
        run: ResearchRun,
        pillar: TopicOpportunity,
        cluster_id: str,
        decision_id: str,
        role: str,
        funnel_stage: str,
        target_query: str,
        evidence_ids: list[str],
        recommended_angle: str,
        search_intent: str,
        internal_links: list[str],
    ) -> Outline:
        evidence = [item for item in evidence_ids if item]
        intent = search_intent or self._intent(target_query, "")
        questions = self._paa_questions(workspace_id=workspace_id, evidence_ids=evidence)
        entity_names = self._relevant_entity_names(
            workspace_id=workspace_id, run_id=run.id, target_query=target_query
        )
        information_gain = list(pillar.information_gain)
        missing_evidence = list(pillar.missing_evidence)
        sections = self._evidence_sections(
            target_query=target_query,
            audience=brand.target_customer,
            questions=questions,
            information_gain=information_gain,
            entity_names=entity_names,
            required_evidence_ids=evidence,
            missing_evidence=missing_evidence,
            internal_links=internal_links,
        )
        return Outline(
            id=new_id("outline"),
            workspace_id=workspace_id,
            brand_id=brand.id,
            source="cluster_outline_generator",
            status="COMPLETED",
            lineage=[run.id, pillar.id, cluster_id, decision_id, *evidence],
            opportunity_id=pillar.id,
            cluster_id=cluster_id,
            role=role,
            funnel_stage=funnel_stage,
            target_query=target_query,
            working_title=f"{target_query.title()}: an evidence-backed guide",
            primary_query=target_query,
            search_intent=intent,
            target_audience=brand.target_customer,
            content_objective=self._cluster_objective(funnel_stage),
            recommended_angle=recommended_angle
            or (
                f"A practical {brand.primary_scenario or 'implementation'} guide for "
                f"{brand.target_customer}: {target_query}."
            ),
            h1=f"{target_query.title()}: how to evaluate and implement it",
            sections=sections,
            questions_to_answer=questions[:5]
            or [
                f"What does {brand.target_customer or 'the reader'} need to know about {target_query}?"
            ],
            required_facts=["Use only approved first-party product facts and cited external research."],
            required_evidence_ids=evidence,
            competitor_gaps_to_cover=[pillar.content_gap],
            internal_link_suggestions=internal_links,
            things_not_to_claim=[
                "Do not claim guaranteed rankings, savings, or outcomes without approved evidence."
            ],
            content_status=self._outline_content_status(pillar),
            missing_evidence=missing_evidence,
        )

    def create_content_cluster(self, *, workspace_id: str, opportunity_id: str) -> dict[str, Any]:
        """Turn one selected CREATE opportunity into an evidence-backed cluster.

        The external-research gate is enforced here, not just in the prompt: the
        rendered SERP must have been read and at least one external evidence
        family (community, technical, business or competitor) recorded; the
        first-party ``brand`` family does not satisfy the requirement.  The
        pillar is the selected
        opportunity; supporting pages come from sibling opportunities and the
        pillar SERP's PAA / related searches.  The cluster includes only the
        pages the evidence justifies, so it does not have to contain a BOFU page.
        """
        workspace_id = self._workspace(workspace_id)
        opportunity = self.store.get(
            opportunity_id, workspace_id=workspace_id, object_type="topic_opportunity"
        )
        assert isinstance(opportunity, TopicOpportunity)
        if opportunity.decision_type != DecisionType.CREATE.value:
            raise StateError("a cluster can only be created for a CREATE opportunity")
        run = self.store.get(
            opportunity.research_run_id, workspace_id=workspace_id, object_type="research_run"
        )
        assert isinstance(run, ResearchRun)
        if run.status not in {ResearchStatus.COMPLETED.value, ResearchStatus.OPPORTUNITY_SELECTED.value}:
            raise StateError("research must complete before an opportunity can be selected")
        brand = self.store.get(
            opportunity.brand_id or "", workspace_id=workspace_id, object_type="brand_profile"
        )
        assert isinstance(brand, BrandProfile)

        observations = self._run_observations(workspace_id=workspace_id, run_id=run.id)
        readiness = self._cluster_readiness(
            workspace_id=workspace_id, run=run, observations=observations
        )
        if not readiness["serp_read"]:
            raise StateError(
                "read the rendered SERP and record at least one serp_result observation before "
                "creating a content cluster"
            )
        if not readiness["external_families_covered"]:
            blocked = readiness.get("blocked_attempts") or []
            detail = ""
            if blocked:
                detail = " Blocked sources: " + "; ".join(
                    f"{item.get('source_type')} {item.get('url')} ({item.get('reason')})"
                    for item in blocked[:5]
                )
            raise StateError(
                "research at least one external third-party source (community, technical, business "
                "or competitor) and record its viewpoint before creating a content cluster; the "
                "brand's own site does not satisfy this gate." + detail
            )

        candidates = self._cluster_candidates(workspace_id=workspace_id, run=run, pillar=opportunity)
        supporting = self._select_cluster_items(pillar=opportunity, candidates=candidates)

        decision = TopicDecision(
            id=new_id("decision"),
            workspace_id=workspace_id,
            brand_id=brand.id,
            source="user_selected_opportunity",
            status="CONFIRMED",
            lineage=[run.id, opportunity.id, *opportunity.evidence_ids],
            opportunity_id=opportunity.id,
            decision_type=opportunity.decision_type,
            rationale="User selected this evidence-backed opportunity for a content cluster.",
            evidence_ids=opportunity.evidence_ids,
        )
        cluster_id = new_id("cluster")
        pillar_stage = self._funnel_stage(opportunity.primary_keyword, opportunity.search_intent)
        pillar_url = "/" + opportunity.primary_keyword.strip().lower().replace(" ", "-")

        outlines: list[Outline] = []
        items: list[dict[str, Any]] = []

        pillar_outline = self._cluster_outline(
            workspace_id=workspace_id,
            brand=brand,
            run=run,
            pillar=opportunity,
            cluster_id=cluster_id,
            decision_id=decision.id,
            role="pillar",
            funnel_stage=pillar_stage,
            target_query=opportunity.primary_keyword,
            evidence_ids=opportunity.evidence_ids,
            recommended_angle=opportunity.recommended_angle,
            search_intent=opportunity.search_intent,
            internal_links=[opportunity.closest_existing_url] if opportunity.closest_existing_url else [],
        )
        outlines.append(pillar_outline)
        items.append(
            {
                "outline_id": pillar_outline.id,
                "role": "pillar",
                "funnel_stage": pillar_stage,
                "target_query": opportunity.primary_keyword,
                "title": pillar_outline.working_title,
                "evidence_ids": opportunity.evidence_ids,
                "links_to": "",
            }
        )

        for candidate in supporting:
            outline = self._cluster_outline(
                workspace_id=workspace_id,
                brand=brand,
                run=run,
                pillar=opportunity,
                cluster_id=cluster_id,
                decision_id=decision.id,
                role="supporting",
                funnel_stage=candidate["funnel_stage"],
                target_query=candidate["target_query"],
                evidence_ids=candidate["evidence_ids"],
                recommended_angle=candidate["recommended_angle"],
                search_intent=candidate["search_intent"],
                internal_links=[pillar_url],
            )
            outlines.append(outline)
            items.append(
                {
                    "outline_id": outline.id,
                    "role": "supporting",
                    "funnel_stage": candidate["funnel_stage"],
                    "target_query": candidate["target_query"],
                    "title": outline.working_title,
                    "evidence_ids": candidate["evidence_ids"],
                    "links_to": pillar_url,
                }
            )

        distribution = {stage: 0 for stage in FUNNEL_STAGES}
        for item in items:
            distribution[item["funnel_stage"]] += 1
        cluster = TopicCluster(
            id=cluster_id,
            workspace_id=workspace_id,
            brand_id=brand.id,
            source="cluster_planner",
            status="COMPLETED",
            lineage=[run.id, opportunity.id, decision.id, *[item["outline_id"] for item in items]],
            research_run_id=run.id,
            pillar_opportunity_id=opportunity.id,
            pillar_keyword=opportunity.primary_keyword,
            items=items,
            funnel_distribution=distribution,
            source_coverage=readiness,
            gate_status="FULL_COVERAGE" if not readiness["degraded"] else "DEGRADED_MINIMUM",
        )
        self.store.save(decision)
        for outline in outlines:
            self.store.save(outline)
        self.store.save(cluster)
        run.status = ResearchStatus.OUTLINE_CREATED.value
        run.selected_opportunity_id = opportunity.id
        self.store.save(run)
        return {
            "topic_decision": decision.to_dict(),
            "topic_cluster": cluster.to_dict(),
            "outlines": [outline.to_dict() for outline in outlines],
            "readiness": readiness,
        }

    def get_topic_cluster(self, *, workspace_id: str, cluster_id: str) -> dict[str, Any]:
        return self.store.get(
            cluster_id, workspace_id=self._workspace(workspace_id), object_type="topic_cluster"
        ).to_dict()

    def get_outline(self, *, workspace_id: str, outline_id: str) -> dict[str, Any]:
        return self.store.get(
            outline_id, workspace_id=self._workspace(workspace_id), object_type="outline"
        ).to_dict()

    def contracts(self) -> dict[str, Any]:
        return {
            "schemas": json_schemas(),
            "states": [item.value for item in ResearchStatus],
            "decision_types": [item.value for item in DecisionType],
        }
