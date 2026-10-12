"""SERP-overlap keyword clustering.

Two queries belong on one page when Google already serves them with the same
URLs.  Keywords are visited from highest to lowest volume; each joins the
existing cluster whose primary query shares the most top-10 URLs with it (at
least ``threshold``), otherwise it starts a new cluster.  This is the common
"hard" SERP clustering used by commercial tools, and it is deterministic.

Spelling variants (hyphens, plurals, word order) are one query to a reader even
when Google shuffles a few results between them, so they are merged into the
highest-volume variant's cluster before SERP overlap is considered.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

DEFAULT_THRESHOLD = 3


def _id(primary: str) -> str:
    return "cl_" + hashlib.sha256(primary.encode("utf-8")).hexdigest()[:10]


def variant_key(keyword: str) -> str:
    tokens = re.findall(r"[a-z0-9]+", keyword.lower().replace("-", " "))
    stems = [t[:-1] if len(t) > 3 and t.endswith("s") and not t.endswith("ss") else t for t in tokens]
    return " ".join(sorted(stems))


def serp_clusters(
    keywords: list[dict[str, Any]], *, threshold: int = DEFAULT_THRESHOLD
) -> list[dict[str, Any]]:
    """``keywords``: ``{"keyword", "volume", "urls": [normalized top-10 urls]}``."""

    ordered = sorted(keywords, key=lambda item: (-(item.get("volume") or 0), item["keyword"]))
    clusters: list[dict[str, Any]] = []
    by_variant: dict[str, dict[str, Any]] = {}
    for item in ordered:
        urls = set(item.get("urls") or [])
        variant = by_variant.get(variant_key(item["keyword"]))
        if variant is not None:
            shared = len(urls & variant["_urls"])
            variant["members"].append({"keyword": item["keyword"], "shared_urls": shared, "variant": True})
            continue
        best: dict[str, Any] | None = None
        best_shared = 0
        if urls:
            for cluster in clusters:
                shared = len(urls & cluster["_urls"])
                if shared >= threshold and shared > best_shared:
                    best, best_shared = cluster, shared
        if best is None:
            best = {
                "id": _id(item["keyword"]),
                "primary": item["keyword"],
                "_urls": urls,
                "members": [{"keyword": item["keyword"], "shared_urls": len(urls)}],
                "has_serp": bool(urls),
            }
            clusters.append(best)
        else:
            best["members"].append({"keyword": item["keyword"], "shared_urls": best_shared})
        by_variant[variant_key(item["keyword"])] = best
    for cluster in clusters:
        cluster["top_urls"] = sorted(cluster.pop("_urls"))
    return clusters


def one_per_variant(keywords: list[str]) -> list[str]:
    """Keep the first keyword of each spelling-variant group, preserving order."""

    seen: set[str] = set()
    distinct = []
    for keyword in keywords:
        key = variant_key(keyword)
        if key not in seen:
            seen.add(key)
            distinct.append(keyword)
    return distinct


def cluster_volume(members: list[dict[str, Any]]) -> int:
    """Total search volume, counting each spelling-variant group once.

    Google reports the same volume for every close variant of a query, so adding
    "human in the loop ai" and "human-in-the-loop ai" counts one demand twice.
    """

    by_variant: dict[str, int] = {}
    for member in members:
        key = variant_key(member["keyword"])
        by_variant[key] = max(by_variant.get(key, 0), member.get("volume") or 0)
    return sum(by_variant.values())


def shared_urls(left: list[str], right: list[str]) -> int:
    return len(set(left) & set(right))
