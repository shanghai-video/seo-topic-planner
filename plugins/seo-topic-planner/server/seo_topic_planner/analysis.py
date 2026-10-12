"""Deterministic checks on the agent's gap analysis and page plan.

The agent does the reading and judging.  This module checks what code can
check honestly: that every quoted answer exists in text the planner captured,
that a "missing" claim was made against a page whose body was actually read,
how much independent evidence stands behind each gap, whether the brand's
contribution is proven by its own pages, and whether the outline is anchored to
those gaps.  Problems are returned as issues/warnings; nothing is silently
upgraded, and unsupported claims are downgraded rather than rejected outright.
"""

from __future__ import annotations

import re
from typing import Any

from .fetch import normalize_for_match, quote_in

COVERAGE_LEVELS = {"DEEP", "SHALLOW", "MISSING", "CONTRADICTORY", "UNKNOWN"}
QUOTED_LEVELS = {"DEEP", "SHALLOW", "CONTRADICTORY"}
GAP_TYPES = {"MISSING_ANSWER", "SHALLOW_ANSWER", "WEAK_EVIDENCE", "OUTDATED", "CONTRADICTORY"}
GAIN_TYPES = {
    "original_data",
    "first_hand_experience",
    "technical_depth",
    "decision_framework",
    "novel_insight",
    "question_coverage",
    # The reader's task needs a calculation, a verdict for their own situation or a
    # generated configuration — something text cannot hand them.
    "utility",
}
# Gain types whose value IS the brand's own material; without proof or a
# concrete collection task they are just a promise.
PROOF_REQUIRED_GAINS = {"original_data", "first_hand_experience"}
QUESTION_SOURCES = {"paa", "related", "serp_heading", "keyword", "job", "external"}
MIN_READ_WORDS = 250
PAGE_ACTIONS = {"CREATE", "UPDATE", "MERGE", "SKIP"}
SITE_VERDICTS = {"SAME_JOB", "PARTIAL", "DIFFERENT_JOB"}

# page_type stays free text; these words let the planner recognise what it means.
TOOL_PAGE = re.compile(
    r"\b(calculator|template|generator|configurator|estimator|interactive checklist|tool)s?\b", re.I
)
# Words that make a page a content page even when it mentions a tool ("guide with a calculator").
CONTENT_PAGE = re.compile(
    r"\b(guide|pillar|explainer|definition|glossary|comparison|versus|vs|listicle|list|alternatives|reviews?|"
    r"roundup|tutorial|how[- ]to|walkthrough|case stud(y|ies)|landing|product|feature|solution|docs|"
    r"documentation|faq|hub|report|research|benchmark|playbook|article|blog|post)\b",
    re.I,
)
# Query modifiers that show people search for the tool itself.
TOOL_QUERY = re.compile(r"\b(calculator|template|generator|checklist|estimator|configurator|tool)s?\b")
# Words an angle uses when it promises an embedded tool.
EMBED_WORDS = re.compile(
    r"\b(calculator|template|generator|configurator|estimator|checklist|interactive|tool|embedded)\b", re.I
)

GENERIC_HEADING = re.compile(
    r"^(introduction|intro|conclusion|final thoughts|summary|overview|key takeaways|faqs?|"
    r"frequently asked questions|what is\b|what are\b|why .+ matters?|benefits of|"
    r"the importance of|getting started|best practices|tips and tricks|wrapping up)",
    re.I,
)
# Phrases that signal filler rather than information.
FILLER = re.compile(
    r"\b(in today'?s (fast-paced|digital|ever-changing)|game[- ]changer|unlock the (power|potential)|"
    r"seamless(ly)?|leverage the power|revolutioniz|cutting-edge|delve into|it'?s important to note|"
    r"in conclusion|robust solution|elevate your)\b",
    re.I,
)


def _page_readable(page: dict[str, Any] | None) -> bool:
    return bool(page) and page.get("status") == "OK" and int(page.get("word_count") or 0) >= MIN_READ_WORDS


def _question_match(question: str, candidates: list[str]) -> bool:
    target = normalize_for_match(question)
    if not target:
        return False
    target_words = set(target.split())
    for candidate in candidates:
        other = normalize_for_match(candidate)
        if not other:
            continue
        if target in other or other in target:
            return True
        other_words = set(other.split())
        if other_words and len(target_words & other_words) / len(target_words | other_words) >= 0.6:
            return True
    return False


def check_questions(
    questions: list[dict[str, Any]], serp_questions: list[str]
) -> tuple[list[dict[str, Any]], list[str]]:
    issues: list[str] = []
    checked: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in questions:
        qid = str(raw.get("id") or "").strip()
        text = str(raw.get("question") or "").strip()
        if not qid or not text or qid in seen:
            issues.append(f"question {qid or '?'}: needs a unique id and text")
            continue
        seen.add(qid)
        source = str(raw.get("source") or "").lower()
        if source not in QUESTION_SOURCES:
            issues.append(f"question {qid}: source must be one of {sorted(QUESTION_SOURCES)}")
            source = "job"
        observed = _question_match(text, serp_questions)
        if source in {"paa", "related"} and not observed:
            issues.append(
                f"question {qid}: marked {source} but does not match any stored People Also Ask / "
                "related search; recorded as source=job"
            )
            source = "job"
        checked.append(
            {
                "id": qid,
                "question": text,
                "source": source,
                "importance": "core" if str(raw.get("importance") or "").lower() == "core" else "supporting",
                "search_observed": observed,
            }
        )
    return checked, issues


def check_coverage(
    rows: list[dict[str, Any]], question_ids: set[str], pages: dict[str, dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Verify each coverage claim against the captured page text."""

    issues: list[str] = []
    checked: list[dict[str, Any]] = []
    for raw in rows:
        qid = str(raw.get("question_id") or "")
        page_id = str(raw.get("page_id") or "")
        level = str(raw.get("level") or "").upper()
        quote = str(raw.get("quote") or "").strip()
        label = f"coverage {qid}×{page_id}"
        if qid not in question_ids:
            issues.append(f"{label}: unknown question id")
            continue
        page = pages.get(page_id)
        if page is None:
            issues.append(f"{label}: unknown page id (fetch or submit the page first)")
            continue
        if level not in COVERAGE_LEVELS:
            issues.append(f"{label}: level must be one of {sorted(COVERAGE_LEVELS)}")
            level = "UNKNOWN"
        verified = False
        if level in QUOTED_LEVELS:
            if quote and quote_in(quote, page.get("text") or ""):
                verified = True
            else:
                issues.append(
                    f"{label}: {level} needs a verbatim quote from the page body; downgraded to UNKNOWN"
                )
                level = "UNKNOWN"
        elif level == "MISSING":
            if _page_readable(page):
                verified = True
            else:
                issues.append(
                    f"{label}: MISSING requires a fully read body (status OK, ≥{MIN_READ_WORDS} words); "
                    "downgraded to UNKNOWN"
                )
                level = "UNKNOWN"
        checked.append(
            {
                "question_id": qid,
                "page_id": page_id,
                "level": level,
                "quote": quote if verified and level in QUOTED_LEVELS else "",
                "note": str(raw.get("note") or "")[:400],
                "verified": verified,
                "agent_read": page.get("method") == "agent",
            }
        )
    return checked, issues


def check_external(
    items: list[dict[str, Any]], pages: dict[str, dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[str]]:
    issues: list[str] = []
    checked: list[dict[str, Any]] = []
    for raw in items:
        eid = str(raw.get("id") or "").strip()
        page = pages.get(str(raw.get("page_id") or ""))
        quote = str(raw.get("quote") or "").strip()
        stance = str(raw.get("stance") or "").lower()
        if not eid or page is None:
            issues.append(f"external evidence {eid or '?'}: needs an id and a fetched/submitted page_id")
            continue
        if stance not in {"supports_need", "against_need"}:
            issues.append(f"external evidence {eid}: stance must be supports_need or against_need")
            continue
        verified = bool(quote) and quote_in(quote, page.get("text") or "")
        if not verified:
            issues.append(
                f"external evidence {eid}: quote not found in the captured page; kept as unverified"
            )
        checked.append(
            {
                "id": eid,
                "page_id": page["id"],
                "url": page["url"],
                "quote": quote[:500],
                "finding": str(raw.get("finding") or "")[:500],
                "stance": stance,
                "verified": verified,
                "agent_read": page.get("method") == "agent",
            }
        )
    return checked, issues


def check_tasks(raw_tasks: list[Any]) -> list[dict[str, str]]:
    return [
        {
            "ask": str(task.get("ask") or "").strip(),
            "from": str(task.get("from") or "").strip(),
            "deliverable": str(task.get("deliverable") or "").strip(),
        }
        for task in raw_tasks
        if isinstance(task, dict) and str(task.get("ask") or "").strip()
    ]


def demand_status(
    questions: list[dict[str, Any]], external: list[dict[str, Any]], tasks: list[dict[str, str]]
) -> str:
    """Is there proof that anyone needs a page nobody searches for (measurably)?"""

    observed = any(q["search_observed"] and q["source"] in {"paa", "related"} for q in questions)
    quoted = any(item["verified"] and item["stance"] == "supports_need" for item in external)
    if observed or quoted:
        return "VERIFIED"
    return "NEEDS_CLIENT_INPUT" if tasks else "MISSING"


def page_format(page_type: str) -> str | None:
    """TOOL, CONTENT, or None when the free-text page type is not recognised."""

    if CONTENT_PAGE.search(page_type) or re.search(r"\b(checklist|page)\b", page_type, re.I):
        return "TOOL" if TOOL_PAGE.search(page_type) and not CONTENT_PAGE.search(page_type) else "CONTENT"
    return "TOOL" if TOOL_PAGE.search(page_type) else None


def assess_gap(
    raw: dict[str, Any],
    *,
    questions: dict[str, dict[str, Any]],
    coverage: list[dict[str, Any]],
    external: dict[str, dict[str, Any]],
    site_pages: dict[str, dict[str, Any]],
    depth: str = "FULL",
) -> tuple[dict[str, Any], list[str]]:
    gid = str(raw.get("id") or "").strip()
    issues: list[str] = []
    label = f"gap {gid or '?'}"
    question_ids = [str(item) for item in raw.get("question_ids") or [] if str(item) in questions]
    if not question_ids:
        issues.append(f"{label}: must reference at least one known question id")
    gap_type = str(raw.get("gap_type") or "").upper()
    if gap_type not in GAP_TYPES:
        issues.append(f"{label}: gap_type must be one of {sorted(GAP_TYPES)}")
    gain_type = str(raw.get("gain_type") or "").lower()
    if gain_type not in GAIN_TYPES:
        issues.append(f"{label}: gain_type must be one of {sorted(GAIN_TYPES)}")
    for field_name in ("statement", "why_it_matters", "counter_argument", "brand_contribution"):
        if len(str(raw.get(field_name) or "").strip()) < 20:
            issues.append(f"{label}: {field_name} must be a specific sentence")

    rows = [row for row in coverage if row["question_id"] in question_ids and row["level"] != "UNKNOWN"]
    pages_checked = {row["page_id"] for row in rows}
    deep_pages = {row["page_id"] for row in rows if row["level"] == "DEEP"}
    weak_pages = {row["page_id"] for row in rows if row["level"] in {"MISSING", "SHALLOW", "CONTRADICTORY"}}
    demand_ids = [str(item) for item in raw.get("demand_evidence_ids") or []]
    unknown_demand = [item for item in demand_ids if item not in external]
    if unknown_demand:
        issues.append(f"{label}: unknown demand evidence ids {unknown_demand}")
    supporting = [
        external[item]
        for item in demand_ids
        if item in external and external[item]["stance"] == "supports_need"
    ]
    counter_ids = [str(item) for item in raw.get("counter_evidence_ids") or []]
    unknown_counter = [item for item in counter_ids if item not in external]
    if unknown_counter:
        issues.append(f"{label}: unknown counter evidence ids {unknown_counter}")
    against = [
        external[item]
        for item in counter_ids
        if item in external and external[item]["stance"] == "against_need" and external[item]["verified"]
    ]
    search_demand = any(questions[qid]["search_observed"] for qid in question_ids)
    verified_support = [item for item in supporting if item["verified"]]
    demand_signals = len(verified_support) + (1 if search_demand else 0)

    if len(deep_pages) >= 2 and gap_type in {"MISSING_ANSWER", "SHALLOW_ANSWER"}:
        strength = "REFUTED"
        issues.append(
            f"{label}: {len(deep_pages)} audited pages already answer this deeply — this is table stakes, "
            "not a gap (use it as a BASELINE section or drop it)"
        )
    elif len(pages_checked) >= 3 and len(weak_pages) >= 2 and len(deep_pages) <= 1 and demand_signals >= 1:
        strength = "STRONG"
    elif (
        len(pages_checked) >= 2
        and weak_pages
        and len(deep_pages) < len(pages_checked)
        and demand_signals >= 1
    ):
        strength = "MODERATE"
    else:
        strength = "WEAK"
    if strength == "STRONG" and against:
        # Verified evidence that the need is already served elsewhere lowers confidence.
        strength = "MODERATE"
    if depth == "EVIDENCE":
        # A page nobody wrote is not the same as pages that miss the answer: with no
        # SERP of its own the gain rests on demand evidence and is never STRONG.
        if not pages_checked:
            strength = "MODERATE" if demand_signals >= 2 else "WEAK"
        elif strength == "STRONG":
            strength = "MODERATE"

    proofs: list[dict[str, Any]] = []
    for proof in raw.get("brand_proof") or []:
        page = site_pages.get(str((proof or {}).get("page_id") or ""))
        quote = str((proof or {}).get("quote") or "").strip()
        ok = page is not None and bool(quote) and quote_in(quote, page.get("text") or "")
        if not ok:
            issues.append(f"{label}: brand proof quote is not on a captured page of the client's site")
        proofs.append(
            {
                "page_id": (proof or {}).get("page_id"),
                "url": page["url"] if page else "",
                "quote": quote[:400],
                "verified": ok,
            }
        )
    tasks = check_tasks(raw.get("collection_tasks") or [])
    if (
        proofs
        and all(item["verified"] for item in proofs)
        and not (gain_type in PROOF_REQUIRED_GAINS and tasks)
    ):
        # For data and first-hand gains the material itself is the gain: a quote proving
        # the capability does not replace data the client still has to collect.
        proof_status = "VERIFIED"
    elif tasks:
        proof_status = "NEEDS_CLIENT_INPUT"
    else:
        proof_status = "UNSUPPORTED"
        issues.append(
            f"{label}: brand contribution has neither verified proof from the client's pages nor "
            "concrete collection tasks (what to ask, from whom, what deliverable)"
        )
    if gain_type in PROOF_REQUIRED_GAINS and proof_status == "UNSUPPORTED":
        strength = "WEAK" if strength != "REFUTED" else strength
    if gain_type == "utility":
        unmet = []
        if demand_signals < 1:
            unmet.append(
                "verified reader demand (a matched People Also Ask question or a quoted external source) "
                "showing readers ask how to calculate, choose or generate this"
            )
        if not any(task["from"] and task["deliverable"] for task in tasks):
            unmet.append(
                "a collection task naming the data the tool needs (ask), who builds and maintains it (from) "
                "and the deliverable"
            )
        if not any(proof["verified"] for proof in proofs):
            unmet.append("a verified quote from the client's site or docs tying the tool to the product")
        for item in unmet:
            issues.append(f"{label}: a utility gain needs {item}")
        if unmet and strength != "REFUTED":
            strength = "WEAK"

    return (
        {
            "id": gid,
            "question_ids": question_ids,
            "gap_type": gap_type,
            "gain_type": gain_type,
            "statement": str(raw.get("statement") or "").strip(),
            "why_it_matters": str(raw.get("why_it_matters") or "").strip(),
            "counter_argument": str(raw.get("counter_argument") or "").strip(),
            "brand_contribution": str(raw.get("brand_contribution") or "").strip(),
            "demand_evidence_ids": [item for item in demand_ids if item in external],
            "counter_evidence_ids": [item for item in counter_ids if item in external],
            "brand_proof": proofs,
            "collection_tasks": tasks,
            "strength": strength,
            "proof_status": proof_status,
            "evidence": {
                "pages_checked": len(pages_checked),
                "pages_deep": len(deep_pages),
                "pages_weak": len(weak_pages),
                "search_demand": search_demand,
                "external_support_verified": len(verified_support),
                "external_against": len(against),
            },
        },
        issues,
    )


def lint_sections(sections: list[dict[str, Any]]) -> list[str]:
    warnings: list[str] = []
    generic = 0
    for index, section in enumerate(sections, 1):
        heading = section["heading"]
        if GENERIC_HEADING.match(heading):
            generic += 1
            if section["kind"] == "GAIN":
                warnings.append(f"section {index} '{heading}': a GAIN section needs a specific heading")
        points = section["points"]
        if len(points) < 2:
            warnings.append(f"section {index} '{heading}': list at least two concrete points to cover")
        vague = [point for point in points if len(point) < 30]
        if vague:
            warnings.append(f"section {index} '{heading}': points too vague to brief a writer: {vague[:3]}")
        filler = [text for text in [heading, *points] if FILLER.search(text)]
        if filler:
            warnings.append(f"section {index} '{heading}': filler phrasing: {filler[:2]}")
    if sections and generic / len(sections) > 0.4:
        warnings.append("more than 40% of headings are generic; the outline reads like a template")
    return warnings


def topic_status(analysis: dict[str, Any] | None, plan: dict[str, Any] | None) -> str:
    if not analysis:
        return "NOT_ANALYZED"
    if not plan:
        return "NEEDS_PAGE_PLAN"
    if plan["action"] == "SKIP":
        return "NOT_RECOMMENDED"
    # Pages without search demand stand on demand evidence (absent for older analyses).
    demand = analysis.get("demand_status")
    if demand == "MISSING":
        return "NO_CLEAR_GAIN"
    gains = {gap["id"]: gap for gap in analysis.get("gaps") or []}
    used = [gains[gid] for gid in plan.get("gain_gap_ids") or [] if gid in gains]
    usable = [gap for gap in used if gap["strength"] in {"STRONG", "MODERATE"}]
    if not usable:
        return "NO_CLEAR_GAIN"
    # One proven gain is enough to write a differentiated page now; sections that wait
    # for client material are listed separately (pending_sections) instead of blocking it.
    if any(gap["proof_status"] == "VERIFIED" for gap in usable) and demand != "NEEDS_CLIENT_INPUT":
        return "READY_TO_WRITE"
    return "NEEDS_CLIENT_INPUT"


def pending_sections(analysis: dict[str, Any] | None, plan: dict[str, Any] | None) -> list[dict[str, Any]]:
    """GAIN sections whose gaps still wait for client material."""

    if not analysis or not plan or plan.get("action") == "SKIP":
        return []
    gaps = {gap["id"]: gap for gap in analysis.get("gaps") or []}
    pending = []
    for section in plan.get("sections") or []:
        waiting = [
            gid for gid in section.get("gap_ids") or [] if gaps.get(gid, {}).get("proof_status") != "VERIFIED"
        ]
        if section.get("kind") == "GAIN" and waiting:
            pending.append({"heading": section["heading"], "gap_ids": waiting})
    return pending
