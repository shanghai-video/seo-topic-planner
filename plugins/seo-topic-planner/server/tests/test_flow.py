"""End-to-end research flow on the offline fake world."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from fakes import CLIENT, DEPLOY, GOVERN, FakeFetcher, FakeTransport
from seo_topic_planner import mcp_server
from seo_topic_planner.domains import normalize_url, page_id
from seo_topic_planner.errors import ProviderError, StateError, ValidationError
from seo_topic_planner.service import Planner

SEEDS = [
    {"keyword": "ai agents", "job_id": "j1", "breadth": "broad"},
    {"keyword": "ai agent deployment", "job_id": "j1", "breadth": "core"},
    {"keyword": "ai agent governance", "job_id": "j2", "breadth": "core"},
    {"keyword": "deploy ai agents", "job_id": "j1", "breadth": "core"},
    {"keyword": "agent approval workflow", "job_id": "j2", "breadth": "specific"},
]


def new_planner(tmp: str, **kwargs: Any) -> tuple[Planner, FakeTransport, FakeFetcher]:
    transport = kwargs.pop("transport", None) or FakeTransport()
    fetcher = FakeFetcher()
    return Planner(Path(tmp), transport=transport, fetcher=fetcher), transport, fetcher


def brief(planner: Planner, project_id: str, confirmed: bool = True) -> dict[str, Any]:
    runtime = page_id(normalize_url(f"{CLIENT}/product/agent-runtime"))
    planner.fetch_pages(project_id, [f"{CLIENT}/product/agent-runtime"], "site")
    return planner.save_brief(
        project_id,
        product_summary="Acme runs AI agents in production with approvals, audit logs and rollback.",
        capabilities=[
            {
                "name": "Rollback",
                "page_id": runtime,
                "quote": "operators can roll back an agent in one click",
            },
            {"name": "Magic autoscaling", "page_id": runtime, "quote": "scales to a billion agents"},
        ],
        icps=[{"id": "i1", "role": "Platform engineer", "context": "Mid-size SaaS"}],
        jobs=[
            {"id": "j1", "icp_id": "i1", "job": "Ship an AI agent to production safely"},
            {"id": "j2", "icp_id": "i1", "job": "Keep production agents governed and auditable"},
        ],
        competitors=[{"name": "Rival", "domain": "rival.io"}, {"name": "No domain", "domain": ""}],
        confirmed_by_user=confirmed,
    )


def to_clusters(planner: Planner, project_id: str, budget: float = 3.0) -> None:
    estimate = planner.plan_research(project_id, SEEDS, budget)
    planner.approve_budget(project_id, estimate["approval_token"])
    while planner.run_research_step(project_id, "discover")["status"] == "IN_PROGRESS":
        pass
    planner.review_keywords(
        project_id,
        decisions=[
            *(
                {"keyword": k, "keep": True, "job_id": "j1", "expand": k == "ai agent deployment"}
                for k in DEPLOY
            ),
            *({"keyword": k, "keep": True, "job_id": "j2"} for k in GOVERN),
        ],
        exclude_patterns=[
            {"pattern": "meta", "reason": "other vendor"},
            {"pattern": "character", "reason": "consumer entertainment"},
        ],
    )
    remaining = planner.list_keywords(project_id)["rows"]
    planner.review_keywords(
        project_id,
        decisions=[
            {"keyword": row[0], "keep": False, "reason": "too broad, no deployment task"} for row in remaining
        ],
    )
    planner.run_research_step(project_id, "expand")
    remaining = planner.list_keywords(project_id)["rows"]
    if remaining:
        planner.review_keywords(
            project_id,
            decisions=[{"keyword": row[0], "keep": False, "reason": "off-topic"} for row in remaining],
        )
    while planner.run_research_step(project_id, "serp")["status"] == "IN_PROGRESS":
        pass


class FlowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.planner, self.transport, self.fetcher = new_planner(self.tmp.name)
        created = self.planner.create_project(
            CLIENT, "us", "en", "Win qualified demo requests from platform teams"
        )
        self.pid = created["project_id"]
        self.created = created

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_create_project_reads_homepage_and_sitemap(self) -> None:
        self.assertEqual(self.created["domain"], "acme-agents.com")
        self.assertEqual(self.created["homepage"]["status"], "OK")
        self.assertEqual(self.created["site_inventory"]["url_count"], 9)
        self.assertIn("Agent runtime", "\n".join(self.created["homepage"]["outline"]))

    def test_market_and_goal_must_come_from_user(self) -> None:
        with self.assertRaises(ValidationError):
            self.planner.create_project(CLIENT, "", "en", "Win qualified demo requests")
        with self.assertRaises(ValidationError):
            self.planner.create_project(CLIENT, "us", "en", "")

    def test_brief_verifies_capabilities_against_site_text(self) -> None:
        result = brief(self.planner, self.pid)
        caps = {cap["name"]: cap["verified"] for cap in result["brief"]["capabilities"]}
        self.assertEqual(caps, {"Rollback": True, "Magic autoscaling": False})
        self.assertEqual([c["domain"] for c in result["brief"]["competitors"]], ["rival.io"])

    def test_no_research_before_brief_confirmation_and_budget_approval(self) -> None:
        brief(self.planner, self.pid, confirmed=False)
        with self.assertRaises(ValidationError):
            self.planner.plan_research(self.pid, SEEDS, 3)
        brief(self.planner, self.pid)
        estimate = self.planner.plan_research(self.pid, SEEDS, 3)
        with self.assertRaises(ValidationError):
            self.planner.run_research_step(self.pid, "discover")
        with self.assertRaises(ValidationError):
            self.planner.approve_budget(self.pid, "wrong-token")
        self.assertEqual(self.transport.calls, [])
        self.assertTrue(self.planner.approve_budget(self.pid, estimate["approval_token"]))
        with self.assertRaises(ValidationError):
            self.planner.approve_budget(self.pid, estimate["approval_token"])  # single use

    def test_seed_validation(self) -> None:
        brief(self.planner, self.pid)
        with self.assertRaises(ValidationError):
            self.planner.plan_research(self.pid, SEEDS[:2], 3)
        bad = [dict(seed, job_id="nope") for seed in SEEDS]
        with self.assertRaises(ValidationError):
            self.planner.plan_research(self.pid, bad, 3)
        long_seed = [
            *SEEDS,
            {"keyword": "how to deploy ai agents in regulated banks", "job_id": "j1", "breadth": "specific"},
        ]
        warnings = self.planner.plan_research(self.pid, long_seed, 3)["warnings"]
        self.assertTrue(any("long seeds" in w for w in warnings))

    def test_discovery_collects_multi_path_candidates_with_metrics(self) -> None:
        brief(self.planner, self.pid)
        # 6 seeds x 2 paths + rival + the client's own domain = 14 calls, at most 12 per round.
        seeds = [*SEEDS, {"keyword": "agent runtime", "job_id": "j1", "breadth": "core"}]
        estimate = self.planner.plan_research(self.pid, seeds, 3)
        self.planner.approve_budget(self.pid, estimate["approval_token"])
        first = self.planner.run_research_step(self.pid, "discover")
        self.assertEqual((first["status"], first["remaining_calls"]), ("IN_PROGRESS", 2))
        second = self.planner.run_research_step(self.pid, "discover")
        self.assertEqual((second["status"], second["paid_calls_this_round"]), ("DONE", 2))
        rows = {row[0]: row for row in self.planner.list_keywords(self.pid, "ALL", limit=300)["rows"]}
        self.assertEqual(rows["ai agent deployment"][1], 880)
        self.assertEqual(rows["ai agent governance"][5], 14)  # client already ranks #14
        ops = {path.split("/")[-2] for path, _ in self.transport.calls}
        self.assertEqual(ops, {"keyword_suggestions", "related_keywords", "ranked_keywords"})

    def test_review_requires_job_and_reason(self) -> None:
        brief(self.planner, self.pid)
        estimate = self.planner.plan_research(self.pid, SEEDS, 3)
        self.planner.approve_budget(self.pid, estimate["approval_token"])
        while self.planner.run_research_step(self.pid, "discover")["status"] == "IN_PROGRESS":
            pass
        result = self.planner.review_keywords(
            self.pid,
            decisions=[
                {"keyword": "ai agent deployment", "keep": True},
                {"keyword": "meta ai", "keep": False},
            ],
        )
        self.assertEqual(result["updated"], 0)
        self.assertEqual(len(result["issues"]), 2)
        with self.assertRaises(ValidationError):
            self.planner.run_research_step(self.pid, "serp")  # unreviewed keywords remain

    def test_exclude_sources_only_touches_single_source_unreviewed(self) -> None:
        brief(self.planner, self.pid)
        estimate = self.planner.plan_research(self.pid, SEEDS, 3)
        self.planner.approve_budget(self.pid, estimate["approval_token"])
        self.planner.run_research_step(self.pid, "discover")
        result = self.planner.review_keywords(
            self.pid,
            decisions=[{"keyword": "agent approval workflow", "keep": True, "job_id": "j2"}],
            exclude_sources=[{"source": "agent approval workflow", "reason": "seed drifted into consumer terms"}],
        )
        rows = {row[0]: row for row in self.planner.list_keywords(self.pid, "ALL", limit=300)["rows"]}
        self.assertEqual(rows["agent approval workflow"][6], "KEPT")  # explicit decision wins
        self.assertEqual(rows["meta ai"][6], "UNREVIEWED")  # also found from other seeds
        self.assertEqual(result["issues"], [])

    def test_serp_clustering_groups_by_shared_urls(self) -> None:
        brief(self.planner, self.pid)
        to_clusters(self.planner, self.pid)
        clusters = self.planner.list_clusters(self.pid)["clusters"]
        groups = sorted(sorted(m.split(" (")[0] for m in c["members"]) for c in clusters)
        self.assertEqual(groups, [sorted(GOVERN), sorted(DEPLOY)])
        governance = next(c for c in clusters if c["primary"] == "ai agent governance")
        self.assertEqual(governance["best_site_rank"], 4)

    def test_cached_requests_are_free_on_resume(self) -> None:
        brief(self.planner, self.pid)
        to_clusters(self.planner, self.pid)
        sent = len(self.transport.calls)
        # A second project for the same site reuses every cached response.
        other = self.planner.create_project(CLIENT, "us", "en", "Same research, second project")["project_id"]
        brief(self.planner, other)
        to_clusters(self.planner, other)
        self.assertEqual(len(self.transport.calls), sent)
        self.assertEqual(self.planner.get_project(other)["budget"]["committed_usd"], "0.0000")

    def test_budget_cap_stops_spending(self) -> None:
        brief(self.planner, self.pid)
        estimate = self.planner.plan_research(self.pid, SEEDS, 0.1)
        self.assertFalse(estimate["fits_cap"])
        self.planner.approve_budget(self.pid, estimate["approval_token"])
        result = self.planner.run_research_step(self.pid, "discover")
        self.assertEqual(result["status"], "BUDGET_REACHED")
        self.assertLessEqual(float(result["budget"]["committed_usd"]), 0.1)

    def test_uncertain_cost_request_is_never_resent(self) -> None:
        transport = FakeTransport(fail_ops={"ranked_keywords": ProviderError("timeout", cost_uncertain=True)})
        planner, transport, _ = new_planner(self.tmp.name + "/b", transport=transport)
        pid = planner.create_project(CLIENT, "us", "en", "Win qualified demo requests")["project_id"]
        brief(planner, pid)
        estimate = planner.plan_research(pid, SEEDS, 3)
        planner.approve_budget(pid, estimate["approval_token"])
        while planner.run_research_step(pid, "discover")["status"] == "IN_PROGRESS":
            pass
        ranked_calls = [p for p, _ in transport.calls if "ranked_keywords" in p]
        self.assertEqual(len(ranked_calls), 2)  # rival + the client's own domain, each tried once
        self.assertEqual(planner.get_project(pid)["budget"]["unknown_cost_requests"], 2)
        transport.fail_ops = {}
        planner.store.delete_kind(pid, "progress")  # even a forced re-run must not resend it
        planner.run_research_step(pid, "discover")
        self.assertEqual(len([p for p, _ in transport.calls if "ranked_keywords" in p]), 2)

    def test_not_connected_refuses_paid_calls(self) -> None:
        brief(self.planner, self.pid)
        estimate = self.planner.plan_research(self.pid, SEEDS, 3)
        self.planner.approve_budget(self.pid, estimate["approval_token"])
        self.transport.ready = False
        result = self.planner.run_research_step(self.pid, "discover")
        self.assertTrue(result["errors"])
        self.assertEqual(self.transport.calls, [])


class TopicResearchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.planner, _, _ = new_planner(self.tmp.name)
        self.pid = self.planner.create_project(CLIENT, "us", "en", "Win qualified demo requests")[
            "project_id"
        ]
        brief(self.planner, self.pid)
        to_clusters(self.planner, self.pid)
        clusters = {c["primary"]: c["cluster_id"] for c in self.planner.list_clusters(self.pid)["clusters"]}
        saved = self.planner.save_topics(
            self.pid,
            [
                {
                    "name": "Deploying AI agents",
                    "cluster_ids": [clusters["ai agent deployment"]],
                    "primary_keyword": "ai agent deployment",
                    "job_id": "j1",
                    "intent": "learn how to ship safely",
                    "priority": "HIGH",
                    "why": "1,290 monthly searches across 3 queries, informational, KD 35.",
                },
            ],
        )
        self.topic = saved["topics"][0]["topic_id"]
        self.workspace = self.planner.get_topic_workspace(self.pid, self.topic)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _page(self, url: str) -> str:
        return page_id(normalize_url(url))

    def test_workspace_fetches_ranking_pages_and_site_candidates(self) -> None:
        fetched = [p for p in self.workspace["competitor_pages"] if p.get("status") == "OK"]
        self.assertEqual(len(fetched), 6)
        self.assertIn("H2 Deployment steps", "\n".join(fetched[0]["outline"]))
        self.assertNotIn("Copyright footer", fetched[0]["untrusted_excerpt"])
        candidates = [c["url"] for c in self.workspace["site_candidates"]]
        self.assertIn(normalize_url(f"{CLIENT}/blog/ai-agent-deployment-guide"), candidates)
        # one candidate per page across languages; a site-wide word ("agents") alone is no match
        self.assertNotIn(normalize_url(f"{CLIENT}/de/blog/ai-agent-deployment-guide"), candidates)
        self.assertFalse([url for url in candidates if "/agents/" in url])
        self.assertIn(
            "How do you roll back a failed AI agent deployment?", self.workspace["serp"]["people_also_ask"]
        )

    def _analysis(self, **overrides: Any) -> dict[str, Any]:
        args = self._analysis_args()
        args.update(overrides)
        return self.planner.save_gap_analysis(self.pid, self.topic, **args)

    def _analysis_args(self) -> dict[str, Any]:
        site = "https://site{}.example.org/agent-deployment"
        pages = [self._page(site.format(i)) for i in range(1, 5)]
        forum = self.planner.fetch_pages(self.pid, ["https://forum.example.com/thread/1"], "source")
        runtime = self._page(f"{CLIENT}/product/agent-runtime")
        args = {
            "questions": [
                {
                    "id": "q1",
                    "question": "How do you roll back a failed AI agent deployment?",
                    "source": "paa",
                    "importance": "core",
                },
                {"id": "q2", "question": "What are the steps to deploy an agent?", "source": "job"},
                {
                    "id": "q3",
                    "question": "What approvals does an AI agent need in production?",
                    "source": "paa",
                },
            ],
            "coverage": [
                *({"question_id": "q1", "page_id": p, "level": "MISSING"} for p in pages),
                *(
                    {
                        "question_id": "q2",
                        "page_id": p,
                        "level": "DEEP",
                        "quote": "Package the agent, connect tools, add monitoring",
                    }
                    for p in pages
                ),
                {
                    "question_id": "q3",
                    "page_id": pages[0],
                    "level": "SHALLOW",
                    "quote": "require human approval for sensitive actions",
                },
                {
                    "question_id": "q3",
                    "page_id": pages[1],
                    "level": "DEEP",
                    "quote": "invented sentence not on page",
                },
            ],
            "external_evidence": [
                {
                    "id": "e1",
                    "page_id": forum["pages"][0]["page_id"],
                    "stance": "supports_need",
                    "quote": "no way to roll back the agent after a bad prompt change",
                    "finding": "Practitioners got burned by missing rollback",
                },
            ],
            "gaps": [
                {
                    "id": "g1",
                    "question_ids": ["q1"],
                    "gap_type": "MISSING_ANSWER",
                    "gain_type": "technical_depth",
                    "statement": "None of the audited guides explain how to roll back a misbehaving agent.",
                    "why_it_matters": "Without rollback a bad prompt change keeps acting on customers.",
                    "counter_argument": "Rollback may be considered an ops topic outside a deployment guide.",
                    "demand_evidence_ids": ["e1"],
                    "brand_contribution": "Acme ships one-click versioned rollback and can show the procedure.",
                    "brand_proof": [{"page_id": runtime, "quote": "roll back an agent in one click"}],
                },
                {
                    "id": "g2",
                    "question_ids": ["q2"],
                    "gap_type": "SHALLOW_ANSWER",
                    "gain_type": "question_coverage",
                    "statement": "Competitors cover deployment steps only briefly in their guides.",
                    "why_it_matters": "Readers need the full sequence to ship the agent at all.",
                    "counter_argument": "The audited pages already list the core steps in order.",
                    "brand_contribution": "Acme can document its own deployment sequence in detail.",
                    "collection_tasks": [
                        {
                            "ask": "Share the internal deployment runbook",
                            "from": "Solutions team",
                            "deliverable": "Runbook PDF",
                        }
                    ],
                },
            ],
        }
        return args

    def test_gap_strength_is_computed_from_verified_evidence(self) -> None:
        result = self._analysis()
        gaps = {g["id"]: g for g in result["gaps"]}
        self.assertEqual(gaps["g1"]["strength"], "STRONG")
        self.assertEqual(gaps["g1"]["proof_status"], "VERIFIED")
        self.assertEqual(gaps["g2"]["strength"], "REFUTED")  # every audited page answers it deeply
        self.assertTrue(any("downgraded to UNKNOWN" in issue for issue in result["issues"]))

    def test_verified_counter_evidence_caps_strength(self) -> None:
        baseline = self._analysis()
        forum = self._page("https://forum.example.com/thread/1")
        external = [
            {"id": "e1", "page_id": forum, "stance": "supports_need",
             "quote": "no way to roll back the agent after a bad prompt change", "finding": "need"},
            {"id": "e9", "page_id": forum, "stance": "against_need",
             "quote": "it kept emailing customers", "finding": "already discussed at length elsewhere"},
        ]
        args = self._analysis_args()
        args["external_evidence"] = external
        args["gaps"][0]["counter_evidence_ids"] = ["e9"]
        result = self.planner.save_gap_analysis(self.pid, self.topic, **args)
        gaps = {g["id"]: g for g in result["gaps"]}
        self.assertEqual(gaps["g1"]["strength"], "MODERATE")
        self.assertEqual({g["id"]: g for g in baseline["gaps"]}["g1"]["strength"], "STRONG")

    def test_paa_source_must_match_stored_serp(self) -> None:
        result = self._analysis(
            questions=[
                {"id": "q1", "question": "Is deployment fun for the whole family?", "source": "paa"},
                {"id": "q2", "question": "What are the steps to deploy an agent?", "source": "job"},
                {
                    "id": "q3",
                    "question": "What approvals does an AI agent need in production?",
                    "source": "paa",
                },
            ]
        )
        self.assertTrue(any("does not match any stored People Also Ask" in i for i in result["issues"]))

    def test_missing_requires_fully_read_body(self) -> None:
        thin = self.planner.submit_page_text(
            self.pid,
            "https://site9.example.org/agent-deployment",
            "competitor",
            text=" ".join(["agent"] * 130),
            how_read="browser",
        )
        result = self._analysis(
            coverage=[
                {"question_id": "q1", "page_id": thin["page"]["page_id"], "level": "MISSING"},
            ]
        )
        self.assertTrue(any("MISSING requires a fully read body" in i for i in result["issues"]))

    def test_page_plan_and_report(self) -> None:
        self._analysis()
        plan = self.planner.save_page_plan(
            self.pid,
            self.topic,
            action="CREATE",
            site_checks=[
                {"url": c["url"], "verdict": "DIFFERENT_JOB", "reason": "old product announcement"}
                for c in self.workspace["site_candidates"]
            ],
            page_type="guide",
            title="How to deploy AI agents without losing control",
            h1="Deploying AI agents: the steps guides skip",
            angle="The only guide that shows how to roll back a misbehaving agent, with Acme's real procedure.",
            sections=[
                {
                    "heading": "Deployment steps at a glance",
                    "kind": "BASELINE",
                    "points": [
                        "Package, connect tools, monitor, ship — summarised from the ranking guides",
                        "Link to each step's detailed doc so the page stays scannable",
                    ],
                },
                {
                    "heading": "Rolling back a misbehaving agent in production",
                    "kind": "GAIN",
                    "gap_ids": ["g1"],
                    "points": [
                        "Versioned snapshot per deploy and how to restore it in one click",
                        "Forum case: agent kept emailing customers after a prompt change",
                    ],
                },
                {
                    "heading": "Approval gates for write actions",
                    "kind": "GAIN",
                    "gap_ids": ["g2"],
                    "points": [
                        "Which tool calls need a human gate and why it matters here",
                        "How Acme enforces approvals on systems of record",
                    ],
                },
            ],
            cta="Book a rollback walkthrough",
        )
        self.assertEqual(plan["status"], "READY_TO_WRITE")
        self.assertTrue(any("GAIN must cite non-refuted gap ids" in i for i in plan["issues"]))
        report = self.planner.get_report(self.pid, output_path=str(Path(self.tmp.name) / "r.md"))
        markdown = report["markdown"]
        self.assertIn("[STRONG] None of the audited guides explain how to roll back", markdown)
        self.assertIn("★ GAIN", markdown)
        self.assertIn("roll back an agent in one click", markdown)
        self.assertTrue(Path(report["written_to"]).exists())

    def test_create_while_site_page_serves_same_job_is_flagged(self) -> None:
        self._analysis()
        candidate = self.workspace["site_candidates"][0]["url"]
        plan = self.planner.save_page_plan(
            self.pid,
            self.topic,
            action="CREATE",
            site_checks=[{"url": candidate, "verdict": "SAME_JOB", "reason": "covers deployment already"}],
            page_type="guide",
            title="How to deploy AI agents safely",
            h1="Deploying AI agents safely",
            angle="A guide built around rollback, which no ranking guide explains.",
            sections=[
                {"heading": f"Section {i}", "kind": "BASELINE", "points": ["a", "b"]} for i in range(3)
            ],
        )
        self.assertTrue(any("already serves the same job" in i for i in plan["issues"]))
        self.assertEqual(plan["status"], "NO_CLEAR_GAIN")
        self.assertTrue(plan["warnings"])

    def test_skip_needs_reason(self) -> None:
        self._analysis()
        with self.assertRaises(ValidationError):
            self.planner.save_page_plan(self.pid, self.topic, action="SKIP")
        plan = self.planner.save_page_plan(
            self.pid,
            self.topic,
            action="SKIP",
            skip_reason="Every audited page answers the core questions in depth and the client has no proof.",
        )
        self.assertEqual(plan["status"], "NOT_RECOMMENDED")


class McpTest(unittest.TestCase):
    def test_tools_list_and_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            planner, _, _ = new_planner(tmp)
            listing = mcp_server.handle(planner, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
            names = [tool["name"] for tool in listing["result"]["tools"]]
            self.assertEqual(len(names), 18)
            for tool in listing["result"]["tools"]:
                self.assertTrue(hasattr(planner, tool["name"]), tool["name"])
            ok = mcp_server.handle(
                planner,
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "create_project",
                        "arguments": {
                            "website": CLIENT,
                            "market": "us",
                            "search_language": "en",
                            "business_goal": "Win qualified demo requests",
                        },
                    },
                },
            )
            self.assertFalse(ok["result"]["isError"])
            bad = mcp_server.handle(
                planner,
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "get_project", "arguments": {"project_id": "missing"}},
                },
            )
            self.assertTrue(bad["result"]["isError"])
            self.assertIn("unknown project", json.loads(bad["result"]["content"][0]["text"])["error"])

    def test_every_tool_declares_boolean_review_hints(self) -> None:
        hints = ("readOnlyHint", "openWorldHint", "idempotentHint", "destructiveHint")
        for tool in mcp_server.TOOLS:
            annotations = tool["annotations"]
            for key in hints:
                self.assertIsInstance(annotations.get(key), bool, f"{tool['name']} {key}")
            if annotations["readOnlyHint"]:
                self.assertFalse(annotations["destructiveHint"], tool["name"])
        destructive = {tool["name"] for tool in mcp_server.TOOLS if tool["annotations"]["destructiveHint"]}
        # Only disconnect removes user state (the stored DataForSEO authorization).
        self.assertEqual(destructive, {"dataforseo_connection"})

    def test_state_error_type_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            planner, _, _ = new_planner(tmp)
            with self.assertRaises(StateError):
                planner.dataforseo_connection("connect")


if __name__ == "__main__":
    unittest.main()
