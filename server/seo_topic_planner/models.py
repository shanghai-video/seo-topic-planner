"""Versioned, JSON-serializable Topic Planner records and schema contracts.

The package deliberately uses only the Python standard library.  ``validate``
enforces the invariants needed by the local core; ``json_schemas`` exposes
portable JSON Schema documents for clients and future remote Core migrations.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, ClassVar
from urllib.parse import urlparse
from uuid import uuid4

from .errors import ValidationError

SCHEMA_VERSION = "v1"

# Evidence types the planner may persist.  The SERP adapter stores ``serp``
# records whose payload carries the structured sub-items, while host-agent
# observations use the more specific source types below.
EVIDENCE_TYPES = {
    "keyword",
    "serp",
    "competitor_page",
    "site_snapshot",
    "aio",
    "paa",
    "related_search",
    "serp_result",
    "reddit_thread",
    "youtube_video",
    "youtube_transcript",
    "official_documentation",
    "product_documentation",
    "github_issue",
    "technical_article",
    "industry_analysis",
    "case_study",
    "company_profile",
    "official_account",
    "third_party_review",
    "brand_website",
}

# Source types the host agent may report as a viewpoint observation.  Podcasts
# are deliberately out of scope: this plugin only consumes yt-dlp (YouTube),
# rendered SERP and browser-reachable sources.
OBSERVATION_SOURCE_TYPES = {
    "serp_result",
    "competitor_page",
    "reddit_thread",
    "youtube_video",
    "youtube_transcript",
    "aio",
    "paa",
    "related_search",
    "official_documentation",
    "product_documentation",
    "github_issue",
    "technical_article",
    "industry_analysis",
    "case_study",
    "company_profile",
    "official_account",
    "third_party_review",
    "brand_website",
}

# Read-only tolerance for records written before podcasts were removed.  These
# rehydrate so an existing local store does not fail to load, but they are not
# in ``OBSERVATION_SOURCE_TYPES`` (so they cannot be submitted) and not in any
# evidence family (so they never count toward the research gate).
LEGACY_OBSERVATION_SOURCE_TYPES = {"podcast_episode", "podcast_transcript"}

# Sources whose observation must carry at least one supporting quote.
QUOTE_REQUIRED_SOURCE_TYPES = {
    "serp_result",
    "competitor_page",
    "reddit_thread",
    "youtube_video",
    "youtube_transcript",
    "official_documentation",
    "product_documentation",
    "github_issue",
    "technical_article",
    "industry_analysis",
    "case_study",
    "company_profile",
    "official_account",
    "third_party_review",
    "brand_website",
}

# Evidence families used by the relevance-based research gate.  The gate no
# longer mandates one platform: a run needs the rendered SERP plus at least one
# non-SERP family that actually fits the topic.  A technical query can be
# researched from documentation and GitHub issues, a commercial one from
# product docs and industry analysis, and so on.
EVIDENCE_FAMILIES: dict[str, set[str]] = {
    "community": {
        "reddit_thread",
        "youtube_video",
        "youtube_transcript",
    },
    "technical": {"official_documentation", "github_issue", "technical_article"},
    "business": {
        "product_documentation",
        "industry_analysis",
        "case_study",
        "third_party_review",
    },
    "brand": {"company_profile", "official_account", "brand_website"},
    "competitor": {"competitor_page"},
}

# Retained for reporting how much community research was done.  The host agent
# should still cover community sources when they are the right evidence, but
# this is no longer a hard, platform-specific gate.
COMMUNITY_SOURCE_FAMILIES: dict[str, set[str]] = {
    "reddit": {"reddit_thread"},
    "youtube": {"youtube_video", "youtube_transcript"},
}

# A rendered-SERP observation proves the host agent actually read the SERP.
SERP_SOURCE_TYPES = {"serp_result"}

# Funnel stages remain descriptive analysis fields, not a generation mandate.
FUNNEL_STAGES = ("TOFU", "MOFU", "BOFU")
CLUSTER_ROLES = ("pillar", "supporting")

# Brand entity taxonomy.  A website can involve several distinct objects; they
# are modelled separately and linked by ``parent_entity_id``.
ENTITY_TYPES = {"organization", "brand", "product", "sub_brand", "technology", "concept"}
IDENTITY_STATUSES = {"UNKNOWN", "DRAFT", "USER_CONFIRMED"}
BRAND_VERIFICATION_STATUSES = {"UNVERIFIED", "PARTIAL", "VERIFIED"}

# Topical entity taxonomy and the relationships the planner tracks.
TOPICAL_ENTITY_TYPES = {
    "Product",
    "Capability",
    "Technology",
    "Concept",
    "Problem",
    "UseCase",
    "Industry",
    "Organization",
    "Competitor",
}
RELATION_TYPES = {
    "related_to",
    "part_of",
    "requires",
    "enables",
    "used_for",
    "alternative_to",
    "competes_with",
    "provided_by",
}
ENTITY_VERIFICATION_STATUSES = {"UNKNOWN", "INFERRED", "VERIFIED"}

# Information-gain taxonomy and readiness states.  A gap in competitor content
# does not by itself mean the brand can fill it: readiness tracks whether the
# brand actually has the evidence.
INFORMATION_GAIN_TYPES = {
    "original_data",
    "first_hand_experience",
    "technical_depth",
    "decision_framework",
    "novel_insight",
    "question_coverage",
}
EVIDENCE_READINESS = {"READY", "NEEDS_EVIDENCE", "UNVERIFIED"}
CONTENT_STATUSES = {"READY_TO_BRIEF", "NEEDS_RESEARCH", "NEEDS_BRAND_EVIDENCE", "LOW_VALUE"}

MAX_QUOTE_CHARS = 400
MAX_VIEWPOINT_CHARS = 1200


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def website_key(website: str) -> str:
    """Normalize a website to a stable host key.

    Scheme, leading ``www.``, port, path, query and case are removed so the
    same site always resolves to one memory entry, while different sites never
    collide.
    """

    value = str(website or "").strip().lower()
    if not value:
        return ""
    if "://" not in value:
        value = "//" + value
    host = (urlparse(value).hostname or "").strip().lower()
    if host.startswith("www."):
        host = host[4:]
    return host


class DecisionType(str, Enum):
    CREATE = "CREATE"
    UPDATE = "UPDATE"
    MERGE = "MERGE"
    DEFEND = "DEFEND"
    IGNORE = "IGNORE"


class ResearchStatus(str, Enum):
    DRAFT_SCOPE = "DRAFT_SCOPE"
    ESTIMATED = "ESTIMATED"
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    OPPORTUNITY_SELECTED = "OPPORTUNITY_SELECTED"
    OUTLINE_CREATED = "OUTLINE_CREATED"
    AUTH_FAILED = "AUTH_FAILED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    PROVIDER_FAILED = "PROVIDER_FAILED"
    PARTIAL_RESULT = "PARTIAL_RESULT"
    CANCELLED = "CANCELLED"


@dataclass
class Record:
    """Fields required on every durable domain object."""

    id: str
    workspace_id: str
    brand_id: str | None
    source: str
    status: str
    lineage: list[str]
    schema_version: str = SCHEMA_VERSION
    revision: int = 1
    created_at: str = field(default_factory=now)
    updated_at: str = field(default_factory=now)

    object_type: ClassVar[str] = "record"

    def touch(self) -> None:
        self.revision += 1
        self.updated_at = now()

    def validate_base(self) -> None:
        required = {
            "id": self.id,
            "workspace_id": self.workspace_id,
            "source": self.source,
            "status": self.status,
        }
        absent = [name for name, value in required.items() if not isinstance(value, str) or not value.strip()]
        if absent:
            raise ValidationError(f"{self.object_type} missing required fields: {', '.join(absent)}")
        if self.schema_version != SCHEMA_VERSION:
            raise ValidationError(f"unsupported schema version: {self.schema_version}")
        if self.revision < 1:
            raise ValidationError("revision must be positive")

    def validate(self) -> None:
        self.validate_base()

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {"object_type": self.object_type, **asdict(self)}


@dataclass
class BrandProfile(Record):
    object_type: ClassVar[str] = "brand_profile"
    name: str = ""
    website: str = ""
    product: str = ""
    target_customer: str = ""
    # Market and language start empty on purpose: the Planner must never assume
    # a market or language.  They are only filled when the user confirms them.
    target_market: str = ""
    language: str = ""
    positioning: str = ""
    primary_scenario: str = ""
    main_business: str = ""
    # The user-confirmed core capabilities of the product, used for relevance
    # scoring.  Populated from the brand-context card (scrape-informed, editable).
    core_features: list[str] = field(default_factory=list)
    context_status: str = "DRAFT"
    confirmed_at: str = ""
    competitors: list[str] = field(default_factory=list)
    business_goals: list[str] = field(default_factory=list)
    seed_topics: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    # The default Brand entity for this profile.  Products, sub-brands and
    # concepts are separate ``brand_entity`` records linked by parent id.
    entity_id: str = ""

    def validate(self) -> None:
        self.validate_base()
        if not self.name or not self.website:
            raise ValidationError("brand_profile requires name and website")
        if self.context_status not in {"DRAFT", "CONFIRMED"}:
            raise ValidationError("brand_profile context_status must be DRAFT or CONFIRMED")
        if self.context_status == "CONFIRMED":
            confirmed = (
                "target_market",
                "language",
                "target_customer",
                "positioning",
                "primary_scenario",
                "main_business",
            )
            missing = [name for name in confirmed if not str(getattr(self, name)).strip()]
            if not [item for item in self.core_features if str(item).strip()]:
                missing.append("core_features")
            if missing:
                raise ValidationError("confirmed brand_profile requires explicit " + ", ".join(missing))


@dataclass
class SitePage:
    url: str
    title: str = ""
    meta_description: str = ""
    h1: str = ""
    summary: str = ""
    canonical: str = ""
    inferred_topics: list[str] = field(default_factory=list)


@dataclass
class SiteSnapshot(Record):
    object_type: ClassVar[str] = "site_snapshot"
    website: str = ""
    pages: list[dict[str, Any]] = field(default_factory=list)
    capture_scope: str = "manual_brand_brief"

    def validate(self) -> None:
        self.validate_base()
        if not self.website:
            raise ValidationError("site_snapshot requires website")
        for page in self.pages:
            if not isinstance(page, dict) or not page.get("url"):
                raise ValidationError("every site_snapshot page requires url")


@dataclass
class ResearchEstimate(Record):
    object_type: ClassVar[str] = "research_estimate"
    research_run_id: str = ""
    seed_topics: list[str] = field(default_factory=list)
    market: str = ""
    language: str = ""
    device: str = "desktop"
    provider: str = "fake"
    provider_calls: dict[str, int] = field(default_factory=dict)
    estimated_cost_usd: float = 0.0
    budget_cap_usd: float = 0.0
    confirmation_token: str = ""

    def validate(self) -> None:
        self.validate_base()
        if not self.research_run_id or not self.seed_topics or not self.confirmation_token:
            raise ValidationError("research_estimate requires run, seed_topics and confirmation token")
        if self.estimated_cost_usd < 0 or self.budget_cap_usd < 0:
            raise ValidationError("research costs cannot be negative")


@dataclass
class ResearchRun(Record):
    object_type: ClassVar[str] = "research_run"
    seed_topics: list[str] = field(default_factory=list)
    market: str = ""
    language: str = ""
    device: str = "desktop"
    provider: str = "fake"
    estimate_id: str = ""
    estimated_cost_usd: float = 0.0
    actual_cost_usd: float = 0.0
    evidence_pack_id: str | None = None
    selected_opportunity_id: str | None = None

    def validate(self) -> None:
        self.validate_base()
        if not self.seed_topics or not self.estimate_id:
            raise ValidationError("research_run requires seed_topics and estimate")


@dataclass
class ResearchEvidence(Record):
    object_type: ClassVar[str] = "research_evidence"
    provider: str = "fake"
    provider_request_id: str = ""
    requested_at: str = field(default_factory=now)
    market: str = ""
    language: str = ""
    device: str = "desktop"
    cost_usd: float = 0.0
    freshness: str = "fresh"
    expires_at: str = ""
    cache_key: str = ""
    evidence_type: str = "keyword"
    payload: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        self.validate_base()
        if not self.provider or not self.provider_request_id or not self.cache_key:
            raise ValidationError("research_evidence requires provider, request id and cache key")
        if self.evidence_type not in EVIDENCE_TYPES:
            raise ValidationError("unsupported evidence type")


@dataclass
class SourceObservation(Record):
    """A host-agent viewpoint grounded in quotes from a fetched source.

    The record deliberately stores only a short viewpoint and 1-3 supporting
    quotes: never a full article, thread dump, transcript or username.
    """

    object_type: ClassVar[str] = "source_observation"
    research_run_id: str | None = None
    source_type: str = ""
    url: str = ""
    title: str = ""
    fetched_at: str = ""
    quotes: list[str] = field(default_factory=list)
    viewpoint: str = ""
    confidence: int = 0
    evidence_ids: list[str] = field(default_factory=list)
    aio_saturation: str = "unobserved"
    cache_key: str = ""
    expires_at: str = ""
    # Optional evidence-quality signals the host agent may add.  They are never
    # required and never inferred by the planner.
    credibility: int = 0
    relevance: int = 0
    research_purpose: str = ""

    def validate(self) -> None:
        self.validate_base()
        if self.source_type not in OBSERVATION_SOURCE_TYPES and (
            self.source_type not in LEGACY_OBSERVATION_SOURCE_TYPES
        ):
            raise ValidationError("unsupported observation source type")
        if not self.fetched_at.strip():
            raise ValidationError("source_observation requires fetched_at")
        if not self.cache_key or not self.expires_at:
            raise ValidationError("source_observation requires cache key and expiry")
        if not self.viewpoint.strip():
            raise ValidationError("source_observation requires a viewpoint")
        if len(self.viewpoint) > MAX_VIEWPOINT_CHARS:
            raise ValidationError("viewpoint is too long to be a summary")
        if len(self.quotes) > 3:
            raise ValidationError("source_observation accepts at most 3 quotes")
        if self.source_type in QUOTE_REQUIRED_SOURCE_TYPES and not self.quotes:
            raise ValidationError(f"{self.source_type} observations require at least one quote")
        for quote in self.quotes:
            if not isinstance(quote, str) or not quote.strip():
                raise ValidationError("observation quotes must be non-empty strings")
            if len(quote) > MAX_QUOTE_CHARS:
                raise ValidationError("observation quote is too long to be a citation")
        if not isinstance(self.confidence, int) or not 0 <= self.confidence <= 100:
            raise ValidationError("observation confidence must be an integer from 0 to 100")
        for name in ("credibility", "relevance"):
            value = getattr(self, name)
            if not isinstance(value, int) or not 0 <= value <= 100:
                raise ValidationError(f"observation {name} must be an integer from 0 to 100")
        if self.aio_saturation not in {"covered", "unobserved"}:
            raise ValidationError("aio_saturation must be covered or unobserved")


@dataclass
class ResearchEvidencePack(Record):
    object_type: ClassVar[str] = "research_evidence_pack"
    research_run_id: str = ""
    evidence_ids: list[str] = field(default_factory=list)
    cache_hits: int = 0
    actual_cost_usd: float = 0.0

    def validate(self) -> None:
        self.validate_base()
        if not self.research_run_id:
            raise ValidationError("evidence pack requires research run")


@dataclass
class TopicOpportunity(Record):
    object_type: ClassVar[str] = "topic_opportunity"
    research_run_id: str = ""
    topic: str = ""
    primary_keyword: str = ""
    secondary_keywords: list[str] = field(default_factory=list)
    decision_type: str = DecisionType.CREATE.value
    search_intent: str = "informational"
    business_relevance: int = 0
    brand_fit: int = 0
    search_demand: int = 0
    competition_level: int = 0
    ranking_feasibility: int = 0
    existing_site_coverage: int = 0
    closest_existing_url: str | None = None
    overlap_risk: str = "low"
    serp_summary: str = ""
    competitor_summary: str = ""
    content_gap: str = ""
    recommended_angle: str = ""
    why_now: str = ""
    expected_value: str = ""
    aio_saturation: str = "unobserved"
    confidence: int = 0
    score: int = 0
    score_breakdown: dict[str, int] = field(default_factory=dict)
    evidence_ids: list[str] = field(default_factory=list)
    recommended_next_action: str = ""
    # Strategy-enhancement fields.  All are additive and optional so records
    # written before this iteration still rehydrate with their defaults.
    entity_ids: list[str] = field(default_factory=list)
    brand_entity_ids: list[str] = field(default_factory=list)
    information_gain: list[dict[str, Any]] = field(default_factory=list)
    evidence_readiness: str = "UNVERIFIED"
    semantic_coverage: dict[str, Any] = field(default_factory=dict)
    missing_evidence: list[str] = field(default_factory=list)
    scoring_version: str = "v1"

    def validate(self) -> None:
        self.validate_base()
        if not self.research_run_id or not self.topic or not self.primary_keyword:
            raise ValidationError("topic opportunity requires run, topic and primary keyword")
        if self.decision_type not in {item.value for item in DecisionType}:
            raise ValidationError("unsupported decision type")
        if self.aio_saturation not in {"covered", "unobserved"}:
            raise ValidationError("aio_saturation must be covered or unobserved")
        if self.evidence_readiness not in EVIDENCE_READINESS:
            raise ValidationError("unsupported evidence_readiness")
        if not isinstance(self.scoring_version, str) or not self.scoring_version.strip():
            raise ValidationError("topic opportunity requires a scoring_version")
        for item in self.information_gain:
            if not isinstance(item, dict) or not str(item.get("gap_type") or "").strip():
                raise ValidationError("every information_gain item requires a gap_type")
            readiness = str(item.get("readiness") or "UNVERIFIED")
            if readiness not in EVIDENCE_READINESS:
                raise ValidationError("unsupported information_gain readiness")
        for name in (
            "business_relevance",
            "brand_fit",
            "search_demand",
            "competition_level",
            "ranking_feasibility",
            "existing_site_coverage",
            "confidence",
            "score",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or not 0 <= value <= 100:
                raise ValidationError(f"{name} must be an integer from 0 to 100")


@dataclass
class TopicDecision(Record):
    object_type: ClassVar[str] = "topic_decision"
    opportunity_id: str = ""
    decision_type: str = DecisionType.CREATE.value
    rationale: str = ""
    evidence_ids: list[str] = field(default_factory=list)


@dataclass
class Outline(Record):
    object_type: ClassVar[str] = "outline"
    opportunity_id: str = ""
    cluster_id: str = ""
    role: str = "pillar"
    funnel_stage: str = "MOFU"
    target_query: str = ""
    working_title: str = ""
    primary_query: str = ""
    search_intent: str = ""
    target_audience: str = ""
    content_objective: str = ""
    recommended_angle: str = ""
    h1: str = ""
    sections: list[dict[str, Any]] = field(default_factory=list)
    questions_to_answer: list[str] = field(default_factory=list)
    required_facts: list[str] = field(default_factory=list)
    required_evidence_ids: list[str] = field(default_factory=list)
    competitor_gaps_to_cover: list[str] = field(default_factory=list)
    internal_link_suggestions: list[str] = field(default_factory=list)
    things_not_to_claim: list[str] = field(default_factory=list)
    # READY_TO_BRIEF means the planning is trustworthy enough to brief; it is not
    # a claim that the article can be published.
    content_status: str = ""
    missing_evidence: list[str] = field(default_factory=list)

    def validate(self) -> None:
        self.validate_base()
        if not self.opportunity_id or not self.working_title or not self.h1:
            raise ValidationError("outline requires opportunity, working title and H1")
        if self.role not in CLUSTER_ROLES:
            raise ValidationError("outline role must be pillar or supporting")
        if self.funnel_stage not in FUNNEL_STAGES:
            raise ValidationError("outline funnel_stage must be TOFU, MOFU or BOFU")
        if self.content_status and self.content_status not in CONTENT_STATUSES:
            raise ValidationError("unsupported outline content_status")


@dataclass
class TopicCluster(Record):
    """One content cluster: a pillar page plus evidence-driven supporting pages.

    The funnel stages (TOFU/MOFU/BOFU) are descriptive analysis fields, not a
    generation mandate: a cluster only includes the supporting pages the evidence
    justifies.
    """

    object_type: ClassVar[str] = "topic_cluster"
    research_run_id: str = ""
    pillar_opportunity_id: str = ""
    pillar_keyword: str = ""
    items: list[dict[str, Any]] = field(default_factory=list)
    funnel_distribution: dict[str, int] = field(default_factory=dict)
    source_coverage: dict[str, Any] = field(default_factory=dict)
    gate_status: str = ""

    def validate(self) -> None:
        self.validate_base()
        if not self.research_run_id or not self.pillar_keyword or not self.items:
            raise ValidationError("topic_cluster requires run, pillar keyword and items")
        for item in self.items:
            if item.get("role") not in CLUSTER_ROLES:
                raise ValidationError("cluster item role must be pillar or supporting")
            if str(item.get("funnel_stage")) not in FUNNEL_STAGES:
                raise ValidationError("cluster item funnel_stage must be TOFU, MOFU or BOFU")
            if not str(item.get("target_query") or "").strip():
                raise ValidationError("cluster item requires a target query")
        if self.gate_status not in {"FULL_COVERAGE", "DEGRADED_MINIMUM"}:
            raise ValidationError("topic_cluster gate_status must be FULL_COVERAGE or DEGRADED_MINIMUM")


@dataclass
class BrandEntity(Record):
    """A typed brand identity object: organization, brand, product or concept.

    Relationships between entities (a company operates a brand that offers a
    product) are expressed with ``parent_entity_id`` and ``related_entity_ids``.
    Real external profiles are kept apart from candidates so an unverified URL
    never becomes an asserted ``sameAs`` link.
    """

    object_type: ClassVar[str] = "brand_entity"
    entity_type: str = "brand"
    canonical_name: str = ""
    aliases: list[str] = field(default_factory=list)
    legal_name: str = ""
    official_website: str = ""
    official_product_urls: list[str] = field(default_factory=list)
    description: str = ""
    disambiguation: str = ""
    parent_entity_id: str = ""
    related_entity_ids: list[str] = field(default_factory=list)
    # {url, profile_type, verified_by, evidence_ids}
    verified_external_profiles: list[dict[str, Any]] = field(default_factory=list)
    # {url, profile_type, source, status, evidence_ids}
    candidate_external_profiles: list[dict[str, Any]] = field(default_factory=list)
    identity_status: str = "UNKNOWN"
    verification_status: str = "UNVERIFIED"
    evidence_ids: list[str] = field(default_factory=list)
    # Actionable brand-entity building advice, not search-volume opportunities.
    recommendations: list[dict[str, Any]] = field(default_factory=list)
    notes: str = ""

    def validate(self) -> None:
        self.validate_base()
        if not self.canonical_name.strip():
            raise ValidationError("brand_entity requires a canonical_name")
        if self.entity_type not in ENTITY_TYPES:
            raise ValidationError("unsupported brand_entity entity_type")
        if self.identity_status not in IDENTITY_STATUSES:
            raise ValidationError("unsupported brand_entity identity_status")
        if self.verification_status not in BRAND_VERIFICATION_STATUSES:
            raise ValidationError("unsupported brand_entity verification_status")
        if self.parent_entity_id and self.parent_entity_id == self.id:
            raise ValidationError("brand_entity cannot be its own parent")
        for profile in self.verified_external_profiles:
            if not isinstance(profile, dict) or not str(profile.get("url") or "").strip():
                raise ValidationError("verified external profiles require a url")
        for profile in self.candidate_external_profiles:
            if not isinstance(profile, dict) or not str(profile.get("url") or "").strip():
                raise ValidationError("candidate external profiles require a url")


@dataclass
class TopicalEntity(Record):
    """A topic-scoped entity discovered from SERP, competitor or site evidence."""

    object_type: ClassVar[str] = "topical_entity"
    research_run_id: str = ""
    canonical_name: str = ""
    aliases: list[str] = field(default_factory=list)
    entity_type: str = "Concept"
    description: str = ""
    evidence_ids: list[str] = field(default_factory=list)
    confidence: int = 0
    verification_status: str = "INFERRED"

    def validate(self) -> None:
        self.validate_base()
        if not self.research_run_id or not self.canonical_name.strip():
            raise ValidationError("topical_entity requires a research run and canonical name")
        if self.entity_type not in TOPICAL_ENTITY_TYPES:
            raise ValidationError("unsupported topical_entity entity_type")
        if self.verification_status not in ENTITY_VERIFICATION_STATUSES:
            raise ValidationError("unsupported topical_entity verification_status")
        if not isinstance(self.confidence, int) or not 0 <= self.confidence <= 100:
            raise ValidationError("topical_entity confidence must be an integer from 0 to 100")


@dataclass
class EntityRelation(Record):
    """A typed, evidence-backed relationship between two topical entities."""

    object_type: ClassVar[str] = "entity_relation"
    research_run_id: str = ""
    subject_entity_id: str = ""
    object_entity_id: str = ""
    relation: str = ""
    evidence_ids: list[str] = field(default_factory=list)
    confidence: int = 0
    verification_status: str = "INFERRED"

    def validate(self) -> None:
        self.validate_base()
        if (
            not self.research_run_id
            or not self.subject_entity_id
            or not self.object_entity_id
            or not self.relation
        ):
            raise ValidationError("entity_relation requires run, subject, object and relation")
        if self.relation not in RELATION_TYPES:
            raise ValidationError("unsupported entity_relation relation")
        if self.verification_status not in ENTITY_VERIFICATION_STATUSES:
            raise ValidationError("unsupported entity_relation verification_status")
        if not isinstance(self.confidence, int) or not 0 <= self.confidence <= 100:
            raise ValidationError("entity_relation confidence must be an integer from 0 to 100")


RECORD_TYPES: dict[str, type[Record]] = {
    item.object_type: item
    for item in (
        BrandProfile,
        BrandEntity,
        SiteSnapshot,
        ResearchEstimate,
        ResearchRun,
        ResearchEvidence,
        SourceObservation,
        ResearchEvidencePack,
        TopicOpportunity,
        TopicDecision,
        Outline,
        TopicCluster,
        TopicalEntity,
        EntityRelation,
    )
}


def record_from_dict(payload: dict[str, Any]) -> Record:
    """Rehydrate and validate a stored record without accepting unknown types."""

    object_type = payload.get("object_type")
    cls = RECORD_TYPES.get(str(object_type))
    if cls is None:
        raise ValidationError(f"unknown object type: {object_type}")
    fields = {key: value for key, value in payload.items() if key != "object_type"}
    try:
        result = cls(**fields)
    except TypeError as exc:
        raise ValidationError(f"invalid {object_type} payload") from exc
    result.validate()
    return result


def json_schemas() -> dict[str, dict[str, Any]]:
    """Portable v1 schemas for contract tests and external clients.

    The schema keeps common lineage/version requirements explicit while detailed
    semantic checks remain in ``Record.validate`` so the plugin has no hidden
    runtime dependency on a particular JSON Schema implementation.
    """

    common_required = [
        "id",
        "schema_version",
        "revision",
        "workspace_id",
        "created_at",
        "updated_at",
        "source",
        "status",
        "lineage",
    ]
    result: dict[str, dict[str, Any]] = {}
    for object_type, cls in RECORD_TYPES.items():
        fields = {name: {} for name in cls.__dataclass_fields__ if name != "object_type"}
        fields["object_type"] = {"const": object_type}
        result[f"{object_type}.{SCHEMA_VERSION}"] = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": f"{object_type}.{SCHEMA_VERSION}",
            "type": "object",
            "required": ["object_type", *common_required],
            "properties": fields,
            "additionalProperties": False,
        }
    return result
