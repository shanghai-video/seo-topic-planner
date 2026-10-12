"""Topic hubs: one pillar, its support pages, and the links between them.

A hub is a structure over topics, not a replacement for them: every page is
still a topic with its own gap analysis and page plan.  This module holds the
pure checks (structure, analysis depth, internal links, overlap); the service
loads and stores the documents.
"""

from __future__ import annotations

import re
from typing import Any

from .clustering import DEFAULT_THRESHOLD, shared_urls

ROLES = ("PILLAR", "SUPPORT", "COMMERCIAL", "NO_SEARCH")
# Roles that need a hub to make sense; commercial and no-search pages may stand alone.
HUB_ONLY_ROLES = {"PILLAR", "SUPPORT"}
DEMAND_SOURCES = ("SEARCH", "PAA", "EVIDENCE")
FUNNEL = ("AWARENESS", "CONSIDERATION", "DECISION")
DEPTHS = ("FULL", "LIGHT", "EVIDENCE")
DEFAULT_LIGHT_ANALYSIS_VOLUME = 100
# Audited ranking pages each depth needs before coverage counts as checked.
MIN_AUDITED = {"FULL": 3, "LIGHT": 2, "EVIDENCE": 0}
COMPETITOR_PAGES = {"FULL": 6, "LIGHT": 3, "EVIDENCE": 3}
OVERLAP_JACCARD = 0.5

_STOP = {
    "the", "and", "for", "with", "how", "what", "why", "when", "your", "you", "are", "does", "from", "that",
    "this", "into", "its", "can", "not", "who", "which", "vs", "per", "their", "they", "them", "about",
}  # fmt: skip


def topic_volume(topic: dict[str, Any], clusters: dict[str, dict[str, Any]]) -> int:
    """Corrected search volume of a topic; no-search pages carry none by definition."""

    if topic.get("role") == "NO_SEARCH":
        return 0
    return sum(clusters[cid]["total_volume"] for cid in topic.get("cluster_ids") or [] if cid in clusters)


def analysis_depth(topic: dict[str, Any], volume: int, light_max_volume: int) -> str:
    role = topic.get("role")
    if role == "NO_SEARCH":
        return "EVIDENCE"
    if role == "SUPPORT" and volume < light_max_volume:
        return "LIGHT"
    return "FULL"


def _serp_urls(topic: dict[str, Any], serps: dict[str, dict[str, Any]], normalize: Any) -> list[str]:
    snapshot = serps.get(topic.get("primary_keyword") or "") or {}
    return [normalize(row["url"]) for row in snapshot.get("organic", [])]


def hub_summaries(
    hubs: dict[str, dict[str, Any]],
    topics: list[dict[str, Any]],
    clusters: dict[str, dict[str, Any]],
    serps: dict[str, dict[str, Any]],
    normalize: Any,
) -> tuple[dict[str, dict[str, Any]], list[str], list[str]]:
    """Derive each hub's pillar and members; return ``(hubs, errors, warnings)``."""

    errors: list[str] = []
    warnings: list[str] = []
    derived: dict[str, dict[str, Any]] = {}
    for hub_id, hub in hubs.items():
        members = [topic for topic in topics if topic.get("hub_id") == hub_id]
        pillars = [topic for topic in members if topic.get("role") == "PILLAR"]
        if len(pillars) > 1:
            errors.append(
                f"hub {hub_id}: has {len(pillars)} pillars ({[p['id'] for p in pillars]}); "
                "a hub has exactly one"
            )
        elif not pillars and members:
            warnings.append(f"hub {hub_id}: no PILLAR topic yet — every hub needs exactly one")
        pillar = pillars[0] if pillars else None
        if pillar:
            pillar_volume = topic_volume(pillar, clusters)
            wider = [
                f"{t['id']} ({topic_volume(t, clusters)})"
                for t in members
                if t["role"] == "SUPPORT" and topic_volume(t, clusters) > pillar_volume
            ]
            if wider:
                warnings.append(
                    f"hub {hub_id}: pillar '{pillar['primary_keyword']}' ({pillar_volume}/month) is narrower "
                    f"than support {wider}; the pillar should target the broadest query"
                )
        searchable = [t for t in members if t.get("role") != "NO_SEARCH"]
        for index, left in enumerate(searchable):
            for right in searchable[index + 1 :]:
                overlap = shared_urls(_serp_urls(left, serps, normalize), _serp_urls(right, serps, normalize))
                if overlap >= DEFAULT_THRESHOLD:
                    warnings.append(
                        f"hub {hub_id}: '{left['primary_keyword']}' and '{right['primary_keyword']}' share "
                        f"{overlap} top-10 URLs — Google treats them as one page; consider merging the topics"
                    )
        derived[hub_id] = {
            **hub,
            "pillar_topic_id": pillar["id"] if pillar else None,
            "member_topic_ids": [t["id"] for t in members],
            "funnel_coverage": sorted({t["funnel"] for t in members if t.get("funnel")}, key=FUNNEL.index),
        }
    return derived, errors, warnings


# ---------------------------------------------------------------- internal links


def link_issues(topic: dict[str, Any], links: list[dict[str, Any]], hub: dict[str, Any] | None) -> list[str]:
    """Rules one page plan must meet on its own."""

    if not hub or not topic.get("hub_id"):
        return []
    issues: list[str] = []
    targets = {link.get("topic_id") for link in links if link.get("topic_id")}
    pillar = hub.get("pillar_topic_id")
    if topic.get("role") == "PILLAR":
        missing = [tid for tid in hub["member_topic_ids"] if tid != topic["id"] and tid not in targets]
        if missing:
            issues.append(f"internal links: the pillar must link to every page in its hub; missing {missing}")
    elif pillar and pillar not in targets:
        issues.append(f"internal links: a {topic.get('role')} page must link back to its pillar ({pillar})")
    return issues


def hub_link_warnings(
    hub: dict[str, Any], topics: dict[str, dict[str, Any]], plans: dict[str, dict[str, Any]]
) -> list[str]:
    """Rules that only hold across the whole hub (checked once its pages are planned)."""

    members = [tid for tid in hub["member_topic_ids"] if tid in topics]
    planned = [tid for tid in members if plans.get(tid) and plans[tid].get("action") != "SKIP"]
    if not planned:
        return []
    commercial = {tid for tid in members if topics[tid].get("role") == "COMMERCIAL"}
    reaches_money = any(
        link.get("url") or link.get("topic_id") in commercial
        for tid in planned
        for link in plans[tid].get("internal_links") or []
    )
    warnings = []
    if not reaches_money:
        warnings.append(
            f"hub {hub['id']}: no page links to a commercial page or to the client's product pages — "
            "readers have no path to conversion"
        )
    unplanned = [tid for tid in members if tid not in plans]
    if unplanned:
        warnings.append(
            f"hub {hub['id']}: link rules are final only after these pages are planned: {unplanned}"
        )
    return warnings


# --------------------------------------------------------------------- overlap


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2 and w not in _STOP}


def gain_overlaps(
    topic_id: str,
    sections: list[dict[str, Any]],
    gaps: dict[str, dict[str, Any]],
    others: dict[str, tuple[str, list[dict[str, Any]], dict[str, dict[str, Any]]]],
) -> list[str]:
    """GAIN sections that another planned page already delivers.

    ``others`` maps topic id -> (topic name, its sections, its gaps).  Two pages
    writing the same gain compete with each other and dilute both; one page
    should own it and the other should link to it.
    """

    def signature(section: dict[str, Any], gap_map: dict[str, dict[str, Any]]) -> set[str]:
        text = " ".join(
            [section["heading"], *(gap_map.get(gid, {}).get("statement", "") for gid in section["gap_ids"])]
        )
        return _words(text)

    warnings = []
    for section in (s for s in sections if s["kind"] == "GAIN"):
        mine = signature(section, gaps)
        if len(mine) < 3:
            continue
        for other_id, (name, other_sections, other_gaps) in others.items():
            if other_id == topic_id:
                continue
            for other in (s for s in other_sections if s.get("kind") == "GAIN"):
                theirs = signature(other, other_gaps)
                if theirs and len(mine & theirs) / len(mine | theirs) >= OVERLAP_JACCARD:
                    warnings.append(
                        f"section '{section['heading']}' overlaps '{other['heading']}' on topic {other_id} "
                        f"({name}); let one page own this gain and link to it from the other"
                    )
    return warnings
