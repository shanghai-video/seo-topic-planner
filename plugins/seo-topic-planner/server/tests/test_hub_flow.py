"""2.1 flow on the offline fake world: hubs, analysis depth, no-search pages, tools, links, supplements."""

from __future__ import annotations

import tempfile
import unittest
from typing import Any

from fakes import CLIENT
from seo_topic_planner.domains import normalize_url, page_id
from seo_topic_planner.errors import ValidationError
from test_flow import SEEDS, brief, new_planner, to_clusters

FORUM = "https://forum.example.com/thread/1"
RUNTIME = f"{CLIENT}/product/agent-runtime"


def pid_of(url: str) -> str:
    return page_id(normalize_url(url))


class HubFlowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.planner, self.transport, _ = new_planner(self.tmp.name)
        self.pid = self.planner.create_project(CLIENT, "us", "en", "Win qualified demo requests")[
            "project_id"
        ]
        brief(self.planner, self.pid)
        to_clusters(self.planner, self.pid)
        listed = self.planner.list_clusters(self.pid)["clusters"]
        self.clusters = {c["primary"]: c for c in listed}
        self.deploy = self.clusters["ai agent deployment"]["cluster_id"]
        self.govern = self.clusters["ai agent governance"]["cluster_id"]

    def tearDown(self) -> None:
        self.tmp.cleanup()

    HUB = {
        "id": "h1",
        "name": "Shipping agents safely",
        "job_id": "j1",
        "priority": "HIGH",
        "why": "1,290 searches/month on deployment plus 760 on governance; KD 35; maps to the product.",
    }

    def pillar(self, **overrides: Any) -> dict[str, Any]:
        topic = {
            "id": "pillar",
            "name": "Deploying AI agents",
            "cluster_ids": [self.deploy],
            "primary_keyword": "ai agent deployment",
            "job_id": "j1",
            "intent": "learn how to ship an agent",
            "why": "1,290 monthly searches across 3 queries, informational, KD 35.",
            "hub_id": "h1",
            "role": "PILLAR",
            "funnel": "AWARENESS",
        }
        topic.update(overrides)
        return topic

    def support(self, **overrides: Any) -> dict[str, Any]:
        topic = {
            "id": "support",
            "name": "Governing production agents",
            "cluster_ids": [self.govern],
            "primary_keyword": "ai agent governance",
            "job_id": "j2",
            "intent": "set controls on agents",
            "why": "760 monthly searches, KD 35, client already ranks #4.",
            "hub_id": "h1",
            "role": "SUPPORT",
            "funnel": "CONSIDERATION",
        }
        topic.update(overrides)
        return topic

    def no_search(self, **overrides: Any) -> dict[str, Any]:
        topic = {
            "id": "runbook",
            "name": "Agent rollback runbook",
            "primary_keyword": "agent rollback runbook",
            "job_id": "j1",
            "intent": "restore a misbehaving agent",
            "why": "0 measurable searches; sales hears rollback questions in 3 of 5 enterprise calls.",
            "hub_id": "h1",
            "role": "NO_SEARCH",
            "demand_source": "EVIDENCE",
            "demand_note": "Prospects ask how to undo a bad agent release; shared by sales and linked from the pillar.",
            "nearest_keyword": "ai agent deployment",
            "funnel": "DECISION",
        }
        topic.update(overrides)
        return topic

    def save_hub(self, **kwargs: Any) -> dict[str, Any]:
        return self.planner.save_topics(
            self.pid, [self.pillar(), self.support(), self.no_search()], hubs=[self.HUB], **kwargs
        )

    # ------------------------------------------------------------ topics & hubs

    def test_hub_with_pillar_support_and_no_search_page(self) -> None:
        result = self.save_hub()
        self.assertEqual(result["warnings"], [])
        hub = result["hubs"][0]
        self.assertEqual(hub["pillar_topic_id"], "pillar")
        self.assertEqual(sorted(hub["member_topic_ids"]), ["pillar", "runbook", "support"])
        self.assertEqual(hub["funnel_coverage"], ["AWARENESS", "CONSIDERATION", "DECISION"])
        saved = {t["topic_id"]: t for t in result["topics"]}
        self.assertEqual(saved["support"]["analysis_depth"], "FULL")  # 760 ≥ 100
        self.assertEqual(saved["runbook"]["analysis_depth"], "EVIDENCE")
        self.assertEqual(saved["runbook"]["search_volume"], 0)
        clusters = {c["primary"]: c for c in self.planner.list_clusters(self.pid)["clusters"]}
        self.assertEqual(clusters["ai agent governance"]["topic_id"], "support")
        self.assertTrue(clusters["ai agent governance"]["has_ai_overview"])
        self.assertIn("serp_intent", clusters["ai agent deployment"])

    def test_cluster_in_two_topics_is_rejected_and_nothing_is_written(self) -> None:
        clash = self.support(id="other", cluster_ids=[self.deploy], primary_keyword="ai agent deployment")
        with self.assertRaises(ValidationError) as caught:
            self.planner.save_topics(self.pid, [self.pillar(), clash], hubs=[self.HUB])
        self.assertIn("one topic only", str(caught.exception))
        self.assertEqual(self.planner.store.list(self.pid, "topic"), [])
        self.save_hub()
        # Changing a saved topic goes through its id, not a second topic.
        self.planner.save_topics(self.pid, [self.support(name="Governing agents in production")])

    def test_hub_needs_exactly_one_pillar_and_the_broadest_query(self) -> None:
        with self.assertRaises(ValidationError):
            self.planner.save_topics(self.pid, [self.pillar(), self.support(role="PILLAR")], hubs=[self.HUB])
        swapped = self.planner.save_topics(
            self.pid, [self.pillar(role="SUPPORT"), self.support(role="PILLAR")], hubs=[self.HUB]
        )
        self.assertTrue(any("narrower" in w for w in swapped["warnings"]))
        with self.assertRaises(ValidationError):  # hub why without numbers
            self.planner.save_topics(self.pid, [self.pillar()], hubs=[{**self.HUB, "why": "x" * 40}])

    def test_no_search_page_validation(self) -> None:
        for bad in (
            {"cluster_ids": [self.deploy]},
            {"demand_note": ""},
            {"demand_source": "SEARCH"},
            {"nearest_keyword": "never fetched"},
        ):
            with self.assertRaises(ValidationError, msg=bad):
                self.planner.save_topics(self.pid, [self.no_search(**bad, hub_id=None, role="NO_SEARCH")])

    def test_nearest_keyword_resolves_to_its_variant_groups_serp(self) -> None:
        saved = self.planner.save_topics(
            self.pid, [self.no_search(hub_id=None, nearest_keyword="ai agents deployment")]
        )
        self.assertEqual(saved["topics"][0]["analysis_depth"], "EVIDENCE")
        topic = self.planner.store.get(self.pid, "topic", "runbook")
        self.assertEqual(topic["nearest_keyword"], "ai agent deployment")

    def test_intent_override_needs_a_reason(self) -> None:
        with self.assertRaises(ValidationError):
            self.planner.save_topics(
                self.pid, [self.pillar(hub_id=None, role=None, intent_override="COMMERCIAL")]
            )
        saved = self.planner.save_topics(
            self.pid,
            [
                self.pillar(
                    hub_id=None,
                    role=None,
                    intent_override="INFORMATIONAL",
                    intent_override_reason="Titles are tutorials the rules could not label.",
                )
            ],
        )
        self.assertEqual(saved["topics"][0]["serp_intent"], "INFORMATIONAL")

    def test_light_analysis_threshold_is_configurable(self) -> None:
        result = self.save_hub(light_analysis_max_volume=1000)
        depths = {t["topic_id"]: t["analysis_depth"] for t in result["topics"]}
        self.assertEqual(depths["support"], "LIGHT")
        workspace = self.planner.get_topic_workspace(self.pid, "support")
        self.assertEqual(workspace["analysis_depth"]["depth"], "LIGHT")
        fetched = [
            p for p in workspace["competitor_pages"] if p.get("role") == "competitor" and p["status"] == "OK"
        ]
        self.assertEqual(len(fetched), 3)
        self.assertEqual(workspace["hub"]["pillar_topic_id"], "pillar")

    # ------------------------------------------------------------ gap analysis

    def analysis_args(self, urls: list[str], **overrides: Any) -> dict[str, Any]:
        args: dict[str, Any] = {
            "questions": [
                {
                    "id": "q1",
                    "question": "How do you roll back a failed AI agent deployment?",
                    "source": "paa",
                },
                {"id": "q2", "question": "Who approves an agent release?", "source": "job"},
                {
                    "id": "q3",
                    "question": "What approvals does an AI agent need in production?",
                    "source": "paa",
                },
            ],
            "coverage": [{"question_id": "q1", "page_id": pid_of(url), "level": "MISSING"} for url in urls],
            "gaps": [
                {
                    "id": "g1",
                    "question_ids": ["q1"],
                    "gap_type": "MISSING_ANSWER",
                    "gain_type": "technical_depth",
                    "statement": "None of the audited pages explain how to roll back a misbehaving agent.",
                    "why_it_matters": "Without rollback a bad prompt change keeps acting on customers.",
                    "counter_argument": "Rollback may be considered an ops topic outside this query.",
                    "brand_contribution": "Acme ships one-click versioned rollback and can show the procedure.",
                    "brand_proof": [{"page_id": pid_of(RUNTIME), "quote": "roll back an agent in one click"}],
                }
            ],
        }
        args.update(overrides)
        return args

    def test_no_search_page_needs_verified_demand(self) -> None:
        self.save_hub()
        workspace = self.planner.get_topic_workspace(self.pid, "runbook")
        self.assertEqual(workspace["analysis_depth"]["depth"], "EVIDENCE")
        self.assertTrue(workspace["competitor_pages"])  # the nearest query's SERP
        questions = [
            {"id": "q1", "question": "Who approves an agent release?", "source": "job"},
            {"id": "q2", "question": "How long does a rollback take?", "source": "job"},
            {"id": "q3", "question": "What breaks when an agent is rolled back?", "source": "job"},
        ]
        args = self.analysis_args([], questions=questions)
        args["gaps"][0]["question_ids"] = ["q2"]
        missing = self.planner.save_gap_analysis(self.pid, "runbook", **args)
        self.assertEqual(missing["demand_status"], "MISSING")
        self.assertTrue(any("verified demand signal" in i for i in missing["issues"]))
        self.assertTrue(any("no_auditable_pages_reason" in i for i in missing["issues"]))
        forum = self.planner.fetch_pages(self.pid, [FORUM], "source")["pages"][0]["page_id"]
        args["external_evidence"] = [
            {
                "id": "e1",
                "page_id": forum,
                "stance": "supports_need",
                "quote": "no way to roll back the agent after a bad prompt change",
                "finding": "Practitioners got burned by missing rollback",
            }
        ]
        args["gaps"][0]["demand_evidence_ids"] = ["e1"]
        args["no_auditable_pages_reason"] = (
            "Nobody publishes rollback runbooks; nearest SERP is generic guides."
        )
        verified = self.planner.save_gap_analysis(self.pid, "runbook", **args)
        self.assertEqual(verified["demand_status"], "VERIFIED")
        self.assertNotIn("STRONG", {g["strength"] for g in verified["gaps"]})

    # ------------------------------------------------------------ page plans

    def plan(self, topic_id: str, **overrides: Any) -> dict[str, Any]:
        workspace = self.planner.get_topic_workspace(self.pid, topic_id)
        args: dict[str, Any] = {
            "action": "CREATE",
            "site_checks": [
                {"url": c["url"], "verdict": "DIFFERENT_JOB", "reason": "unrelated"}
                for c in workspace["site_candidates"]
            ],
            "page_type": "guide",
            "title": "How to roll back an AI agent release",
            "h1": "Rolling back AI agents in production",
            "angle": "The only page that shows how to roll back a misbehaving agent, with Acme's procedure.",
            "sections": [
                {
                    "heading": "Deployment steps at a glance",
                    "kind": "BASELINE",
                    "points": [
                        "Package, connect tools, monitor and ship the agent",
                        "Link each step to docs",
                    ],
                },
                {
                    "heading": "Rolling back a misbehaving agent in production",
                    "kind": "GAIN",
                    "gap_ids": ["g1"],
                    "points": [
                        "Versioned snapshot restore in one click",
                        "Forum case of an agent emailing customers",
                    ],
                },
                {
                    "heading": "Who signs off on an agent release",
                    "kind": "BASELINE",
                    "points": [
                        "Platform lead approves tool scopes",
                        "Security reviews write access to systems",
                    ],
                },
            ],
        }
        args.update(overrides)
        return self.planner.save_page_plan(self.pid, topic_id, **args)

    def analysed(self, topic_id: str, pages: int = 3, **overrides: Any) -> None:
        workspace = self.planner.get_topic_workspace(self.pid, topic_id)
        urls = [p["url"] for p in workspace["competitor_pages"] if p.get("role") == "competitor"][:pages]
        self.planner.save_gap_analysis(self.pid, topic_id, **self.analysis_args(urls, **overrides))

    def test_internal_links_and_hub_checks(self) -> None:
        self.save_hub()
        self.analysed("pillar")
        self.analysed("support")
        support = self.plan("support", internal_links=[{"target": "nowhere", "anchor_intent": "x"}])
        self.assertTrue(any("link back to its pillar" in i for i in support["issues"]))
        self.assertTrue(any("target must be another topic id" in i for i in support["issues"]))
        support = self.plan("support", internal_links=[{"target": "pillar", "anchor_intent": "start here"}])
        self.assertFalse(any(i.startswith("internal link") for i in support["issues"]))
        self.assertTrue(any("conversion" in w for w in support["hub_checks"]))
        pillar = self.plan(
            "pillar", internal_links=[{"target": "support", "anchor_intent": "governance controls"}]
        )
        self.assertTrue(any("runbook" in i for i in pillar["issues"]))
        self.assertTrue(any("overlaps" in w for w in pillar["warnings"]))  # same rollback gain on both pages
        pillar = self.plan(
            "pillar",
            internal_links=[
                {"target": "support", "anchor_intent": "governance controls", "section": "Who signs off"},
                {"target": "runbook", "anchor_intent": "the full rollback runbook"},
                {"target": f"{CLIENT}/product/agent-runtime", "anchor_intent": "see rollback in the product"},
            ],
        )
        self.assertFalse(any(i.startswith("internal link") for i in pillar["issues"]))
        self.assertFalse(any("conversion" in w for w in pillar["hub_checks"]))
        markdown = self.planner.get_report(self.pid)["markdown"]
        self.assertIn("Topic hubs", markdown)
        self.assertIn("Shipping agents safely", markdown)
        self.assertIn("| No-search page | Agent rollback runbook |", markdown)
        self.assertIn(
            "| Deploying AI agents | Agent rollback runbook | the full rollback runbook |", markdown
        )
        # Reachable volume excludes the no-search page: 1,290 + 760.
        self.assertIn("| 2,050 |", markdown)

    def test_tool_pages_and_ai_overview_risk(self) -> None:
        self.planner.save_topics(self.pid, [self.support(hub_id=None, role=None)])
        self.analysed("support", pages=2)  # two audited pages: a MODERATE gain at best
        text_only = self.plan("support", page_type="guide")
        self.assertEqual(text_only["page_format"], "TEXT")
        self.assertTrue(any("AI Overview" in w for w in text_only["warnings"]))
        standalone = self.plan("support", page_type="calculator")
        self.assertTrue(any("standalone tool page needs a tool query" in i for i in standalone["issues"]))
        utility = {
            **self.analysis_args([])["gaps"][0],
            "gain_type": "utility",
            "question_ids": ["q3"],
            "collection_tasks": [
                {
                    "ask": "Which tool calls write to systems of record",
                    "from": "Platform lead",
                    "deliverable": "List",
                }
            ],
        }
        workspace = self.planner.get_topic_workspace(self.pid, "support")
        urls = [p["url"] for p in workspace["competitor_pages"] if p.get("role") == "competitor"][:3]
        args = self.analysis_args(urls)
        args["gaps"] = [{**args["gaps"][0]}, {**utility, "id": "g2"}]
        saved = self.planner.save_gap_analysis(self.pid, "support", **args)
        self.assertFalse([i for i in saved["issues"] if "utility gain needs" in i])
        sections = [
            {
                "heading": "Approval policy generator for your agent's tools",
                "kind": "GAIN",
                "gap_ids": ["g2"],
                "points": [
                    "Reader lists the tools; the page returns always_allow or always_ask per tool",
                    "Defaults come from the client's tool permission model",
                ],
            },
            {
                "heading": "Deployment steps at a glance",
                "kind": "BASELINE",
                "points": ["Package, connect tools, monitor and ship the agent", "Link each step to docs"],
            },
            {
                "heading": "Who signs off on an agent release",
                "kind": "BASELINE",
                "points": ["Platform lead approves tool scopes", "Security reviews write access to systems"],
            },
        ]
        hybrid = self.plan("support", sections=sections)
        self.assertEqual(hybrid["page_format"], "HYBRID")
        self.assertTrue(any("say so in the angle" in w for w in hybrid["warnings"]))
        self.assertFalse(any("AI Overview" in w for w in hybrid["warnings"]))


class SupplementTest(unittest.TestCase):
    NEW_SEEDS = [
        {"keyword": "approval fatigue", "job_id": "j2", "breadth": "specific"},
        {"keyword": "agent rollback", "job_id": "j1", "breadth": "core"},
    ]

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.planner, self.transport, _ = new_planner(self.tmp.name)
        self.pid = self.planner.create_project(CLIENT, "us", "en", "Win qualified demo requests")[
            "project_id"
        ]
        brief(self.planner, self.pid)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_supplement_only_after_serp(self) -> None:
        estimate = self.planner.plan_research(self.pid, SEEDS, 3)
        self.planner.approve_budget(self.pid, estimate["approval_token"])
        with self.assertRaises(ValidationError):
            self.planner.plan_research(self.pid, self.NEW_SEEDS, 3, supplement=True)

    def test_supplement_round_goes_through_approval_and_the_same_ledger(self) -> None:
        to_clusters(self.planner, self.pid)
        spent = float(self.planner.get_project(self.pid)["budget"]["committed_usd"])
        serps_before = sum(1 for path, _ in self.transport.calls if "/serp/" in path)
        with self.assertRaises(ValidationError):  # the new cap must cover what was spent
            self.planner.plan_research(self.pid, self.NEW_SEEDS, spent / 2, supplement=True)
        estimate = self.planner.plan_research(self.pid, self.NEW_SEEDS, 3.5, supplement=True)
        self.assertEqual(estimate["already_spent_usd"], f"{spent:.4f}")
        with self.assertRaises(ValidationError):  # nothing runs before approval
            self.planner.run_research_step(self.pid, "supplement_discover")
        with self.assertRaises(ValidationError):
            self.planner.approve_budget(self.pid, "wrong-token")
        approved = self.planner.approve_budget(self.pid, estimate["approval_token"])
        self.assertEqual(approved["budget"]["cap_usd"], "3.50")
        self.assertIn("supplement_discover", approved["next_step"])
        found = self.planner.run_research_step(self.pid, "supplement_discover")
        self.assertEqual([s["seed"] for s in found["seeds_without_data"]], ["approval fatigue"])
        self.assertEqual(found["new_keywords"], 1)
        with self.assertRaises(ValidationError):  # the new keyword is unreviewed
            self.planner.run_research_step(self.pid, "supplement_serp")
        self.planner.review_keywords(
            self.pid, decisions=[{"keyword": "agent rollback", "keep": True, "job_id": "j1"}]
        )
        done = self.planner.run_research_step(self.pid, "supplement_serp")
        self.assertEqual(done["status"], "DONE")
        new_serps = [task["keyword"] for path, task in self.transport.calls if "/serp/" in path][
            serps_before:
        ]
        self.assertEqual(new_serps, ["agent rollback"])  # existing variant groups are not re-bought
        self.assertIn(
            "agent rollback", [c["primary"] for c in self.planner.list_clusters(self.pid)["clusters"]]
        )
        state = self.planner.get_project(self.pid)
        self.assertGreater(float(state["budget"]["committed_usd"]), spent)
        self.assertIn("save_topics", state["next_step"])
        report = self.planner.get_report(self.pid)["markdown"]
        self.assertIn("approval fatigue", report.split("Seeds DataForSEO has no data for")[1][:80])


if __name__ == "__main__":
    unittest.main()
