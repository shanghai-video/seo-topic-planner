"""Normalize DataForSEO Labs keyword metrics without inventing values.

A missing metric stays ``None`` (never ``0`` or a mid-range default), and the
ad-auction ``competition_index`` is kept as ``paid_competition`` so it is never
mistaken for organic keyword difficulty.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return int(round(number))


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _trend(monthly: Any) -> float | None:
    """Last-3-months vs. the same months a year earlier, as a ratio change."""

    if not isinstance(monthly, list):
        return None
    points = []
    for item in monthly:
        if isinstance(item, Mapping):
            year, month, volume = (
                _as_int(item.get("year")),
                _as_int(item.get("month")),
                _as_int(item.get("search_volume")),
            )
            if year and month and volume is not None:
                points.append((year * 12 + month, volume))
    points.sort(reverse=True)
    if len(points) < 15:
        return None
    recent = sum(volume for _, volume in points[:3])
    previous = sum(volume for _, volume in points[12:15])
    if previous <= 0:
        return None
    return round(recent / previous - 1, 2)


def labs_keyword_metrics(item: Mapping[str, Any]) -> dict[str, Any]:
    info = item.get("keyword_info") if isinstance(item.get("keyword_info"), Mapping) else {}
    properties = item.get("keyword_properties") if isinstance(item.get("keyword_properties"), Mapping) else {}
    intent_info = (
        item.get("search_intent_info") if isinstance(item.get("search_intent_info"), Mapping) else {}
    )
    difficulty = _as_int(properties.get("keyword_difficulty"))
    if difficulty is None:
        difficulty = _as_int(item.get("keyword_difficulty"))
    intent = intent_info.get("main_intent") if isinstance(intent_info.get("main_intent"), str) else None
    return {
        "keyword": str(item.get("keyword") or ""),
        "volume": _as_int(info.get("search_volume")),
        "kd": difficulty,
        "cpc": _as_float(info.get("cpc")),
        "paid_competition": _as_int(info.get("competition_index")),
        "intent": intent,
        "trend_yoy": _trend(info.get("monthly_searches")),
    }
