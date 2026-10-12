"""SERP-based search intent.

A keyword tool's intent label is guessed from the query's words; Google's top 10
shows what searchers actually get.  Each organic result is labelled with a page
type from its title and URL, and the cluster intent follows from the mix.

The rules are a first pass for the analyst, not a verdict: ``list_clusters``
shows the page-type mix and titles so the analyst can review it, and a topic
may override the intent with a written reason.

"X vs Y" titles are the known trap.  A comparison of products is commercial
investigation; a comparison of concepts ("agent vs llm", "human in the loop vs
human on the loop") is informational.  A vs-title counts as a product
comparison only when it also carries list markers (best / top N /
alternatives / pricing / review) or one side names a brand seen in the SERP.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from .domains import registrable_domain

PAGE_TYPES = (
    "guide",
    "listicle",
    "product",
    "tutorial",
    "tool",
    "docs",
    "article",
    "forum",
    "video",
    "other",
)
INTENTS = ("INFORMATIONAL", "COMMERCIAL", "TRANSACTIONAL", "MIXED")
INFORMATIONAL_TYPES = {"guide", "tutorial", "article", "docs"}

_FORUMS = {
    "reddit.com",
    "quora.com",
    "ycombinator.com",
    "stackoverflow.com",
    "stackexchange.com",
    "linkedin.com",
    "facebook.com",
    "x.com",
    "twitter.com",
}
_VIDEO = {"youtube.com", "vimeo.com", "tiktok.com"}
_LIST = re.compile(
    r"^\s*(top\s+)?\d+\s|\b(top|best)\s+\d+\b|\b\d+\s+(best|top)\b|\bbest of\b|\balternatives?\b|"
    r"\breviews?\b|\bpricing\b|\bcompared\b|\bcomparison\b|"
    r"\bbest\s+(?:[\w-]+\s+){0,3}(tools?|platforms?|software|apps?|agents?|solutions|frameworks|builders?|"
    r"vendors|providers|companies|gateways)\b",
    re.I,
)
_VERSUS = re.compile(r"\s(vs\.?|versus)\s", re.I)
_CONCEPT_DIFF = re.compile(r"\bdifferences?\b|\bdifference between\b", re.I)
_TOOL = re.compile(r"\b(calculator|generator|template|estimator|configurator|checker)s?\b", re.I)
_GUIDE_TITLE = re.compile(
    r"\bwhat (is|are)\b|\bmeaning\b|\bdefinition\b|\bglossary\b|\bexplained\b|\bguide\b|\bintroduction\b|"
    r"\b101\b|\boverview\b|\bunderstanding\b",
    re.I,
)
_GUIDE_URL = re.compile(r"glossary|/topics/|/learn|/what-is|/discover/|/think/|wiki")
_TUTORIAL = re.compile(
    r"\bhow to\b|\btutorial\b|step.by.step|\bbuild (an?|your)\b|\bcreate (an?|your)\b", re.I
)
_PRODUCT_PATH = re.compile(
    r"^(platform|product|products|solutions?|features?|pricing|ai-agents?|agents?|use-cases)(/|$)"
)
_GENERIC_LABELS = {"www", "blog", "docs", "app", "ai", "the", "get", "try", "use", "go", "my"}


def brand_tokens(domains: list[str], extra: list[str] | None = None) -> set[str]:
    """Brand names visible in a SERP: the name part of each result's domain."""

    tokens: set[str] = set()
    for domain in domains:
        name = (registrable_domain(domain) or "").split(".")[0]
        if len(name) >= 3 and name not in _GENERIC_LABELS:
            tokens.add(name)
    for name in extra or []:
        cleaned = re.sub(r"[^a-z0-9]", "", str(name).lower())
        if len(cleaned) >= 3 and cleaned not in _GENERIC_LABELS:
            tokens.add(cleaned)
    return tokens


def _compared_names_brand(title: str, brands: set[str]) -> bool:
    """Does the compared part of the title name a brand?

    Only the title segment holding the comparison is checked, so a site-name
    suffix ("... | Make") does not turn a concept comparison into a product one.
    """

    segments = re.split(r"\s[|–—:-]\s|:\s", title)
    compared = next((s for s in segments if _VERSUS.search(f" {s} ") or _CONCEPT_DIFF.search(s)), title)
    return bool(set(re.findall(r"[a-z0-9]+", compared.lower())) & brands)


def page_type(row: dict[str, Any], brands: set[str] | None = None) -> str:
    """Label one organic result (``title`` + ``url``)."""

    title = str(row.get("title") or "")
    url = str(row.get("url") or "").lower()
    domain = registrable_domain(url) or ""
    path = urlparse(url).path.strip("/")
    if domain in _FORUMS:
        return "forum"
    if domain in _VIDEO:
        return "video"
    if (
        domain == "github.com"
        or "/docs" in url
        or url.startswith("https://docs.")
        or "learn.microsoft" in url
    ):
        return "docs"
    if _VERSUS.search(title) or _CONCEPT_DIFF.search(title):
        if _LIST.search(title) or _compared_names_brand(title, brands or set()):
            return "listicle"
        return "guide"
    if _LIST.search(title):
        return "listicle"
    if _TOOL.search(title) or _TOOL.search(path):
        return "tool"
    if _GUIDE_TITLE.search(title) or _GUIDE_URL.search(url):
        return "guide"
    if _TUTORIAL.search(title):
        return "tutorial"
    if not path or _PRODUCT_PATH.search(path) or re.search(r"\| .*(platform|software)$", title, re.I):
        return "product"
    if "/blog" in url or "medium.com" in url or "substack" in url:
        return "article"
    return "other"


def serp_intent(types: list[str]) -> str:
    """Cluster intent from the page-type mix of its primary SERP."""

    if not types:
        return "MIXED"
    share = {kind: types.count(kind) / len(types) for kind in set(types)}
    listicle = share.get("listicle", 0)
    product = share.get("product", 0) + share.get("tool", 0)
    informational = sum(share.get(kind, 0) for kind in INFORMATIONAL_TYPES)
    if product >= 0.4:
        return "TRANSACTIONAL"
    if listicle >= 0.4:
        return "COMMERCIAL"
    if listicle + product >= 0.4 and informational >= 0.3:
        return "MIXED"
    if informational >= 0.5:
        return "INFORMATIONAL"
    return "MIXED"


def analyse_serp(snapshot: dict[str, Any] | None, extra_brands: list[str] | None = None) -> dict[str, Any]:
    """Page types, intent, the page type to build, and whether an AI Overview shows."""

    snapshot = snapshot or {}
    organic = snapshot.get("organic") or []
    if not organic:
        return {
            "serp_intent": None,
            "page_types": {},
            "dominant_page_type": None,
            "has_ai_overview": "ai_overview" in (snapshot.get("features") or []),
            "labelled_results": [],
        }
    brands = brand_tokens([row.get("url") or "" for row in organic], extra_brands)
    types = [page_type(row, brands) for row in organic[:10]]
    counts = {kind: types.count(kind) for kind in PAGE_TYPES if kind in types}
    # The page to build is what wins among results one could publish; forums,
    # videos and unclassified pages say little about the format to write.
    buildable = {kind: n for kind, n in counts.items() if kind not in {"forum", "video", "other"}}
    dominant = (
        max(buildable, key=lambda kind: (buildable[kind], -PAGE_TYPES.index(kind))) if buildable else None
    )
    return {
        "serp_intent": serp_intent(types),
        "page_types": counts,
        "dominant_page_type": dominant,
        "has_ai_overview": "ai_overview" in (snapshot.get("features") or []),
        "labelled_results": [
            f"{row.get('rank')}. {row.get('domain') or registrable_domain(row.get('url') or '')} — "
            f"{row.get('title')} [{kind}]"
            for row, kind in zip(organic[:6], types)
        ],
    }
