"""Unit tests for 2.1 planning rules: SERP intent, hubs, depth, utility gains, page formats.

SERP titles are real Google top results captured in 2026-10 (US, English).
"""

from __future__ import annotations

import unittest
from typing import Any

from seo_topic_planner import analysis, hubs
from seo_topic_planner.intent import analyse_serp, page_type, serp_intent


def serp(rows: list[tuple[str, str]], features: list[str] | None = None) -> dict[str, Any]:
    return {
        "organic": [{"rank": i, "url": url, "title": title} for i, (url, title) in enumerate(rows, 1)],
        "features": features or ["organic"],
    }


# "ai agents vs agentic ai": concept comparisons, informational despite "vs".
CONCEPT_VS = serp(
    [
        (
            "https://testrigor.com/blog/agentic-ai-vs-ai-agents/",
            "Agentic AI vs. AI Agents vs. Autonomous AI: Key Differences",
        ),
        ("https://www.youtube.com/watch?v=x", "Differences | AI Agent Tutorial | 2026 | Amit Thinks"),
        (
            "https://beam.ai/agentic-insights/ai-agents-vs-agentic-ai",
            "AI Agents vs. Agentic AI: What B2B Leaders Need to Know",
        ),
        ("https://www.linkedin.com/posts/x", "Dafni Daravigka's Post - AI Agent vs Agentic AI"),
        ("https://learningnetwork.cisco.com/s/blogs/a", "AI Agent vs. Agentic AI"),
        ("https://www.jetbrains.com/pages/llm-vs-agents/", "LLMs vs. AI Agents: How They Differ"),
        ("https://dust.tt/blog/ai-agent-vs-llm", "AI Agent vs LLM: What is the best fit for you?"),
    ],
    ["ai_overview", "organic"],
)
# "what is the best ai agent platform": listicles, commercial investigation.
LISTICLES = serp(
    [
        ("https://nexos.ai/blog/best-ai-agents/", "Best AI agents in 2026: 7 business solutions"),
        (
            "https://www.revechat.com/blog/ai-agent-platforms/",
            "8 Best AI Agent Platforms in 2026: Tested & Reviewed",
        ),
        ("https://www.reddit.com/r/AutoGenAI/x", "Top 5 Platforms for Building AI Agents : r/AutoGenAI"),
        ("https://upsun.com/blog/ai-agent-platforms/", "Best Platforms for Running AI Agents in 2026"),
        ("https://blog.n8n.io/best-ai-agents/", "Beyond the Hype: 10 Best AI Agents That Truly Work"),
        (
            "https://www.gartner.com/reviews/market/ai-agents",
            "Best AI Agents for Application Developers Reviews 2026",
        ),
    ]
)
# "human in the loop ai": definition pages.
DEFINITIONS = serp(
    [
        ("https://www.ibm.com/think/topics/human-in-the-loop", "What Is Human In The Loop (HITL)?"),
        ("https://hai.stanford.edu/news/humans-loop", "What is Human-in-the-Loop? - Stanford HAI"),
        (
            "https://cloud.google.com/discover/human-in-the-loop",
            "What is Human-in-the-Loop (HITL) in AI and ML?",
        ),
        ("https://www.databricks.com/glossary/human-in-the-loop", "What is Human-in-the-Loop (HITL)?"),
        ("https://www.youtube.com/watch?v=y", "What is Human In The Loop with AI? How HITL Shapes AI ..."),
    ]
)


class SerpIntentTest(unittest.TestCase):
    def test_concept_comparison_is_informational(self) -> None:
        result = analyse_serp(CONCEPT_VS)
        self.assertEqual(result["serp_intent"], "INFORMATIONAL")
        self.assertEqual(result["dominant_page_type"], "guide")
        self.assertTrue(result["has_ai_overview"])
        self.assertIn("[guide]", result["labelled_results"][0])

    def test_listicles_are_commercial(self) -> None:
        result = analyse_serp(LISTICLES)
        self.assertEqual(result["serp_intent"], "COMMERCIAL")
        self.assertEqual(result["page_types"]["listicle"], 5)
        self.assertFalse(result["has_ai_overview"])

    def test_definitions_are_informational(self) -> None:
        self.assertEqual(analyse_serp(DEFINITIONS)["serp_intent"], "INFORMATIONAL")

    def test_product_comparison_needs_list_markers_or_a_brand(self) -> None:
        brands = {"zapier", "make"}
        self.assertEqual(
            page_type({"url": "https://a.com/b", "title": "Zapier vs Make for AI agents"}, brands), "listicle"
        )
        self.assertEqual(
            page_type({"url": "https://a.com/b", "title": "Zapier vs Make: pricing compared"}), "listicle"
        )
        # A site-name suffix is not one of the compared things.
        self.assertEqual(
            page_type(
                {"url": "https://make.com/x", "title": "Human in the loop vs human on the loop | Make"},
                brands,
            ),
            "guide",
        )

    def test_tool_pages_and_empty_serp(self) -> None:
        self.assertEqual(
            page_type({"url": "https://x.io/llm-cost-calculator", "title": "LLM Cost Calculator"}), "tool"
        )
        self.assertIsNone(analyse_serp({})["serp_intent"])
        self.assertEqual(serp_intent(["product", "product", "tool", "guide", "article"]), "TRANSACTIONAL")


class HubRulesTest(unittest.TestCase):
    CLUSTERS = {"c1": {"total_volume": 1000}, "c2": {"total_volume": 400}, "c3": {"total_volume": 60}}

    def topic(self, tid: str, role: str, clusters: list[str], keyword: str = "") -> dict[str, Any]:
        return {
            "id": tid,
            "role": role,
            "hub_id": "h1",
            "cluster_ids": clusters,
            "primary_keyword": keyword or tid,
        }

    def test_depth_follows_role_and_volume(self) -> None:
        support = self.topic("s", "SUPPORT", ["c3"])
        self.assertEqual(hubs.analysis_depth(support, 60, 100), "LIGHT")
        self.assertEqual(hubs.analysis_depth(support, 60, 50), "FULL")
        self.assertEqual(hubs.analysis_depth(self.topic("p", "PILLAR", ["c3"]), 60, 100), "FULL")
        self.assertEqual(hubs.analysis_depth(self.topic("n", "NO_SEARCH", []), 0, 100), "EVIDENCE")
        self.assertEqual(hubs.analysis_depth({"cluster_ids": ["c3"]}, 60, 100), "FULL")  # pre-2.1 topic

    def test_hub_structure(self) -> None:
        topics = [self.topic("p", "PILLAR", ["c2"]), self.topic("s", "SUPPORT", ["c1"])]
        hub = {"h1": {"id": "h1", "name": "Hub"}}
        derived, errors, warnings = hubs.hub_summaries(hub, topics, self.CLUSTERS, {}, str)
        self.assertEqual(errors, [])
        self.assertEqual(derived["h1"]["pillar_topic_id"], "p")
        self.assertTrue(any("narrower" in w for w in warnings))
        topics.append(self.topic("p2", "PILLAR", ["c3"]))
        self.assertTrue(hubs.hub_summaries(hub, topics, self.CLUSTERS, {}, str)[1])

    def test_link_rules(self) -> None:
        hub = {"id": "h1", "pillar_topic_id": "p", "member_topic_ids": ["p", "s1", "s2"]}
        support = self.topic("s1", "SUPPORT", [])
        self.assertTrue(hubs.link_issues(support, [], hub))
        self.assertEqual(hubs.link_issues(support, [{"topic_id": "p"}], hub), [])
        pillar = self.topic("p", "PILLAR", [])
        issues = hubs.link_issues(pillar, [{"topic_id": "s1"}], hub)
        self.assertIn("s2", issues[0])
        topics = {tid: self.topic(tid, "SUPPORT", []) for tid in ("p", "s1", "s2")}
        plans = {tid: {"action": "CREATE", "internal_links": [{"topic_id": "p"}]} for tid in topics}
        self.assertTrue(any("conversion" in w for w in hubs.hub_link_warnings(hub, topics, plans)))
        plans["p"]["internal_links"] = [{"url": "https://acme.com/product"}]
        self.assertFalse(any("conversion" in w for w in hubs.hub_link_warnings(hub, topics, plans)))

    def test_gain_overlap_between_pages(self) -> None:
        mine = [{"heading": "Approval gates for agent tool calls", "kind": "GAIN", "gap_ids": ["g1"]}]
        gaps = {"g1": {"statement": "No guide explains approval gates on agent tool calls"}}
        others = {
            "t2": (
                "AI agent security",
                [{"heading": "Approval gates on agent tool calls", "kind": "GAIN", "gap_ids": ["x"]}],
                {"x": {"statement": "Approval gates for agent tool calls are missing from guides"}},
            )
        }
        self.assertTrue(hubs.gain_overlaps("t1", mine, gaps, others))
        self.assertEqual(hubs.gain_overlaps("t2", mine, gaps, others), [])


class GainRulesTest(unittest.TestCase):
    QUESTIONS = {
        "q1": {
            "id": "q1",
            "question": "How much does an agent task cost?",
            "source": "paa",
            "search_observed": True,
        }
    }
    SITE = {
        "site1": {
            "url": "https://acme.com/docs/runs",
            "text": "Every run reports model tokens and sandbox compute per task.",
        }
    }

    def gap(self, **overrides: Any) -> dict[str, Any]:
        raw = {
            "id": "g1",
            "question_ids": ["q1"],
            "gap_type": "MISSING_ANSWER",
            "gain_type": "utility",
            "statement": "No ranking page lets a reader compute the cost of one agent task end to end.",
            "why_it_matters": "Budget owners must estimate the cost per task before approving a pilot.",
            "counter_argument": "Costs vary so much that a calculator may give false precision.",
            "brand_contribution": "The client meters tokens and sandbox compute per run and can seed defaults.",
            "brand_proof": [
                {"page_id": "site1", "quote": "reports model tokens and sandbox compute per task"}
            ],
            "collection_tasks": [
                {"ask": "Median tokens per task by type", "from": "Platform lead", "deliverable": "Table"}
            ],
        }
        raw.update(overrides)
        return raw

    def coverage(self, n: int) -> list[dict[str, Any]]:
        return [{"question_id": "q1", "page_id": f"p{i}", "level": "MISSING"} for i in range(n)]

    def test_utility_gain_meets_its_three_conditions(self) -> None:
        gap, issues = analysis.assess_gap(
            self.gap(), questions=self.QUESTIONS, coverage=self.coverage(3), external={}, site_pages=self.SITE
        )
        self.assertEqual((gap["strength"], gap["proof_status"]), ("STRONG", "VERIFIED"))
        self.assertEqual(issues, [])

    def test_utility_gain_without_maintainer_or_proof_is_weak(self) -> None:
        gap, issues = analysis.assess_gap(
            self.gap(brand_proof=[], collection_tasks=[{"ask": "Build it"}]),
            questions=self.QUESTIONS,
            coverage=self.coverage(3),
            external={},
            site_pages=self.SITE,
        )
        self.assertEqual(gap["strength"], "WEAK")
        self.assertEqual(sum("utility gain needs" in issue for issue in issues), 2)

    def test_evidence_depth_never_reaches_strong(self) -> None:
        kwargs = {"questions": self.QUESTIONS, "external": {}, "site_pages": self.SITE, "depth": "EVIDENCE"}
        audited, _ = analysis.assess_gap(
            self.gap(gain_type="technical_depth"), coverage=self.coverage(3), **kwargs
        )
        self.assertEqual(audited["strength"], "MODERATE")
        # No page to audit and a single demand signal: "nobody wrote it" alone is not a gain.
        unaudited, _ = analysis.assess_gap(self.gap(gain_type="technical_depth"), coverage=[], **kwargs)
        self.assertEqual(unaudited["strength"], "WEAK")

    def test_demand_status(self) -> None:
        question = [{"source": "paa", "search_observed": True}]
        self.assertEqual(analysis.demand_status(question, [], []), "VERIFIED")
        self.assertEqual(analysis.demand_status([], [], [{"ask": "a"}]), "NEEDS_CLIENT_INPUT")
        self.assertEqual(
            analysis.demand_status([], [{"verified": False, "stance": "supports_need"}], []), "MISSING"
        )

    def test_page_format(self) -> None:
        self.assertEqual(analysis.page_format("ROI calculator"), "TOOL")
        self.assertEqual(analysis.page_format("interactive checklist"), "TOOL")
        self.assertEqual(analysis.page_format("guide with embedded calculator"), "CONTENT")
        self.assertEqual(analysis.page_format("tool comparison"), "CONTENT")
        self.assertIsNone(analysis.page_format("widget"))

    def test_status_respects_no_search_demand(self) -> None:
        gaps = {"gaps": [{"id": "g1", "strength": "MODERATE", "proof_status": "VERIFIED"}]}
        plan = {"action": "CREATE", "gain_gap_ids": ["g1"]}
        self.assertEqual(analysis.topic_status(gaps, plan), "READY_TO_WRITE")
        self.assertEqual(
            analysis.topic_status({**gaps, "demand_status": "NEEDS_CLIENT_INPUT"}, plan), "NEEDS_CLIENT_INPUT"
        )
        self.assertEqual(analysis.topic_status({**gaps, "demand_status": "MISSING"}, plan), "NO_CLEAR_GAIN")


if __name__ == "__main__":
    unittest.main()
