"""Iteration 2 acceptance tests: no fake path, brand memory, evidence, real gap."""

import json
import tempfile
import unittest
from pathlib import Path

from seo_topic_planner.errors import AuthError, StateError, ValidationError
from seo_topic_planner.mcp_server import TOOLS
from seo_topic_planner.models import BrandProfile, SourceObservation, TopicCluster, website_key
from seo_topic_planner.providers import DataForSeoMcpProvider, FakeSeoProvider
from seo_topic_planner.service import TopicPlannerService

CONFIRM = {
    "target_market": "United States",
    "language": "en",
    "target_customer": "enterprise operations teams",
    "positioning": "An AI workflow platform for enterprise operations",
    "primary_scenario": "automating enterprise workflows with AI agents",
    "main_business": "AI workflow software",
    "core_features": ["AI agents", "workflow automation"],
}


class Iteration2Test(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.provider = FakeSeoProvider()
        self.service = TopicPlannerService(
            Path(self.temp.name),
            keyword_provider=self.provider,
            serp_provider=self.provider,
            page_provider=self.provider,
        )

    def tearDown(self):
        self.temp.cleanup()

    def _brand(self, website="https://acme.test", **kwargs):
        created = self.service.create_brand_profile(
            workspace_id="w",
            name=kwargs.pop("name", "Acme"),
            website=website,
            product="AI workflow software",
            target_customer="enterprise operations teams",
            seed_topics=kwargs.pop("seed_topics", ["enterprise AI agents"]),
            site_pages=kwargs.pop(
                "site_pages",
                [{"url": f"{website}/agents", "title": "AI agent platform", "h1": "AI agents"}],
            ),
            **kwargs,
        )
        return created

    def _confirmed(self, website="https://acme.test", **kwargs):
        created = self._brand(website, **kwargs)
        return self.service.confirm_brand_context(
            workspace_id="w", brand_id=created["brand_profile"]["id"], **CONFIRM
        )

    def _run(self, brand_id):
        estimate = self.service.estimate_topic_research(
            workspace_id="w", brand_id=brand_id, budget_cap_usd=5.0
        )
        return self.service.run_topic_research(
            workspace_id="w",
            research_run_id=estimate["research_run"]["id"],
            confirmation_token=estimate["research_estimate"]["confirmation_token"],
        )

    # ------------------------------------------------------------------ task A

    def test_task_a_unconnected_runtime_has_no_provider_and_refuses_research(self):
        service = TopicPlannerService(Path(self.temp.name) / "offline")
        self.assertIsNone(service.keyword_provider)
        self.assertEqual(service.get_data_provider_status(workspace_id="w")["status"], "AUTH_REQUIRED")
        with self.assertRaises(AuthError):
            service.estimate_topic_research(workspace_id="w", brand_id="whatever")
        with self.assertRaises(AuthError):
            service.run_topic_research(workspace_id="w", research_run_id="run", confirmation_token="token")

    def test_task_a_disconnect_returns_to_auth_required_without_fake(self):
        from seo_topic_planner.oauth import DataForSeoConnection, TokenStore

        connection = DataForSeoConnection(TokenStore(Path(self.temp.name) / "oauth.json"))
        service = TopicPlannerService(Path(self.temp.name) / "conn", connection=connection)
        self.assertIsNone(service.keyword_provider)
        status = service.disconnect_dataforseo(workspace_id="w")
        self.assertTrue(status["disconnected"])
        self.assertEqual(status["status"], "AUTH_REQUIRED")
        self.assertIsNone(service.keyword_provider)

    def test_task_a_tool_descriptions_expose_no_demo_path(self):
        for tool in TOOLS:
            self.assertNotIn("demo", tool["description"].lower(), tool["name"])
            self.assertNotIn("fake", tool["description"].lower(), tool["name"])

    # ------------------------------------------------------------- tasks B / C

    def test_task_b_brand_starts_draft_and_blocks_research_until_confirmed(self):
        created = self._brand()
        profile = created["brand_profile"]
        self.assertEqual(profile["context_status"], "DRAFT")
        self.assertTrue(created["message"])
        with self.assertRaises(StateError):
            self.service.estimate_topic_research(workspace_id="w", brand_id=profile["id"])
        confirmed = self.service.confirm_brand_context(workspace_id="w", brand_id=profile["id"], **CONFIRM)
        self.assertEqual(confirmed["context_status"], "CONFIRMED")
        self.assertTrue(confirmed["confirmed_at"])
        estimate = self.service.estimate_topic_research(
            workspace_id="w", brand_id=profile["id"], budget_cap_usd=1.0
        )
        self.assertIn("research_run", estimate)

    def test_task_b_confirmation_requires_every_anchor(self):
        created = self._brand()
        with self.assertRaises(ValidationError):
            self.service.confirm_brand_context(
                workspace_id="w",
                brand_id=created["brand_profile"]["id"],
                target_market="US",
                language="en",
                target_customer="ops",
                positioning="",
                primary_scenario="scenario",
                main_business="business",
            )
        self.assertEqual(
            self.service.get_brand_profile(workspace_id="w", brand_id=created["brand_profile"]["id"])[
                "context_status"
            ],
            "DRAFT",
        )

    def test_task_b_website_memory_reuses_and_isolates_sites(self):
        first = self._confirmed("https://www.Acme.test/path?x=1")
        remembered = self.service.get_brand_profile_by_website(workspace_id="w", website="acme.test")
        self.assertEqual(remembered["brand_profile"]["context_status"], "CONFIRMED")
        self.assertEqual(remembered["brand_profile"]["primary_scenario"], CONFIRM["primary_scenario"])
        self.assertEqual(remembered["brand_profile"]["main_business"], CONFIRM["main_business"])
        second = self.service.create_brand_profile(
            workspace_id="w",
            name="Acme again",
            website="https://acme.test",
            product="AI workflow software",
            target_customer="enterprise operations teams",
        )
        self.assertTrue(second["reused"])
        self.assertEqual(second["brand_profile"]["id"], first["id"])
        self.assertEqual(website_key("https://www.Acme.test/path"), "acme.test")

        other = self._confirmed("https://other.test", name="Other")
        self.assertNotEqual(other["id"], first["id"])
        lookup = self.service.get_brand_profile_by_website(workspace_id="w", website="acme.test")
        self.assertTrue(lookup["found"])
        self.assertEqual(lookup["brand_profile"]["id"], first["id"])
        self.assertNotEqual(lookup["brand_profile"]["id"], other["id"])
        self.assertFalse(
            self.service.get_brand_profile_by_website(workspace_id="w", website="none.test")["found"]
        )

    def test_task_b_update_existing_resets_confirmation(self):
        first = self._confirmed()
        updated = self.service.create_brand_profile(
            workspace_id="w",
            name="Acme v2",
            website="https://acme.test",
            product="AI workflow software",
            target_customer="enterprise operations teams",
            update_existing=True,
        )
        self.assertTrue(updated.get("updated"))
        self.assertEqual(updated["brand_profile"]["context_status"], "DRAFT")
        with self.assertRaises(StateError):
            self.service.estimate_topic_research(workspace_id="w", brand_id=first["id"])

    def test_task_b_no_market_or_language_default_is_applied(self):
        created = self._brand()
        profile = created["brand_profile"]
        self.assertEqual(profile["target_market"], "")
        self.assertEqual(profile["language"], "")
        self.assertEqual(profile["core_features"], [])

    def test_task_b_draft_needs_only_name_and_website(self):
        created = self.service.create_brand_profile(
            workspace_id="w", name="Minimal", website="https://minimal.test"
        )
        profile = created["brand_profile"]
        self.assertEqual(profile["context_status"], "DRAFT")
        self.assertEqual(profile["target_market"], "")
        self.assertEqual(profile["seed_topics"], [])
        # A confirmed profile still requires the explicit anchors.
        with self.assertRaises(ValidationError):
            self.service.confirm_brand_context(
                workspace_id="w", brand_id=profile["id"], **{**CONFIRM, "core_features": []}
            )

    def test_task_c_confirmation_requires_core_features(self):
        created = self._brand()
        brand_id = created["brand_profile"]["id"]
        without = {key: value for key, value in CONFIRM.items() if key != "core_features"}
        with self.assertRaises(ValidationError):
            self.service.confirm_brand_context(workspace_id="w", brand_id=brand_id, **without)
        confirmed = self.service.confirm_brand_context(workspace_id="w", brand_id=brand_id, **CONFIRM)
        self.assertEqual(confirmed["context_status"], "CONFIRMED")
        self.assertEqual(confirmed["core_features"], CONFIRM["core_features"])

    def test_task_c_brand_context_card_first_run_then_review(self):
        card = self.service.brand_context_card(
            workspace_id="w",
            website="https://new.test",
            suggestions={
                "name": "New Site",
                "target_customer": "ops teams",
                "core_features": ["automation", "analytics"],
                "target_market": "",
                "language": "",
            },
        )
        self.assertFalse(card["found"])
        self.assertEqual(card["mode"], "first_run")
        self.assertEqual(card["suggestions"]["core_features"], ["automation", "analytics"])
        self.assertEqual(card["suggestions"]["target_market"], "")
        self.assertTrue(card["market_options"])
        self.assertTrue(card["language_options"])
        self.assertEqual(card["workspace_id"], "w")

        created = self.service.create_brand_profile(
            workspace_id="w", name="New Site", website="https://new.test"
        )
        self.service.confirm_brand_context(
            workspace_id="w", brand_id=created["brand_profile"]["id"], **CONFIRM
        )
        reviewed = self.service.brand_context_card(workspace_id="w", website="new.test")
        self.assertTrue(reviewed["found"])
        self.assertEqual(reviewed["mode"], "review")
        self.assertEqual(reviewed["suggestions"]["target_market"], CONFIRM["target_market"])
        self.assertEqual(reviewed["suggestions"]["core_features"], CONFIRM["core_features"])

    def test_task_c_multiple_target_markets_keep_a_primary(self):
        created = self._brand()
        confirmed = self.service.confirm_brand_context(
            workspace_id="w",
            brand_id=created["brand_profile"]["id"],
            **{**CONFIRM, "target_markets": ["United States", "Germany", "France"]},
        )
        self.assertEqual(confirmed["target_market"], "United States")
        self.assertEqual(confirmed["target_markets"], ["United States", "Germany", "France"])
        card = self.service.brand_context_card(workspace_id="w", website="acme.test")
        self.assertEqual(
            card["suggestions"]["target_markets"], ["United States", "Germany", "France"]
        )

    def test_task_c_create_accepts_multiple_markets(self):
        created = self.service.create_brand_profile(
            workspace_id="w",
            name="Multi",
            website="https://multi.test",
            target_markets=["United States", "Germany"],
        )
        profile = created["brand_profile"]
        self.assertEqual(profile["target_market"], "United States")
        self.assertEqual(profile["target_markets"], ["United States", "Germany"])

    # ---------------------------------------------------------------- task D

    def test_task_d_serp_evidence_keeps_aio_paa_and_related(self):
        brand = self._confirmed()
        result = self._run(brand["id"])
        pack = self.service.store.get(
            result["research_evidence_pack"]["id"],
            workspace_id="w",
            object_type="research_evidence_pack",
        )
        serp_payloads = []
        for evidence_id in pack.evidence_ids:
            evidence = self.service.get_research_evidence(workspace_id="w", evidence_id=evidence_id)
            if evidence["evidence_type"] == "serp":
                serp_payloads.append(evidence["payload"])
        self.assertTrue(serp_payloads)
        for payload in serp_payloads:
            self.assertIn("organic", payload)
            self.assertIn("aio", payload)
            self.assertIn("paa", payload)
            self.assertIn("related", payload)
            self.assertTrue(payload["serp_features"])

    def test_task_d_observations_validate_persist_and_stay_idempotent(self):
        brand = self._confirmed()
        result = self._run(brand["id"])
        run_id = result["research_run"]["id"]
        payload = {
            "workspace_id": "w",
            "research_run_id": run_id,
            "observations": [
                {
                    "source_type": "reddit_thread",
                    "url": "https://reddit.example.test/r/x/comments/1",
                    "title": "enterprise AI agents in practice",
                    "fetched_at": "2026-10-07T00:00:00Z",
                    "quotes": ["We tried agents and rolled them back", "Governance was the blocker"],
                    "viewpoint": "Practitioners complain about governance, not model quality.",
                    "confidence": 70,
                    "evidence_ids": ["evidence_x"],
                    "aio_saturation": "unobserved",
                }
            ],
        }
        first = self.service.record_source_observations(**payload)
        self.assertEqual(first["count"], 1)
        second = self.service.record_source_observations(**payload)
        self.assertEqual(second["observations"][0]["id"], first["observations"][0]["id"])
        listed = self.service.list_source_observations(workspace_id="w", research_run_id=run_id)
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["brand_id"], brand["id"])
        self.assertTrue(listed[0]["cache_key"])
        self.assertTrue(listed[0]["expires_at"])

    def test_task_d_rejects_full_text_and_missing_quotes(self):
        with self.assertRaises(ValidationError):
            self.service.record_source_observations(
                workspace_id="w",
                observations=[
                    {
                        "source_type": "competitor_page",
                        "url": "https://competitor.test/a",
                        "fetched_at": "2026-10-07T00:00:00Z",
                        "quotes": [],
                        "viewpoint": "no citation",
                    }
                ],
            )
        with self.assertRaises(ValidationError):
            self.service.record_source_observations(
                workspace_id="w",
                observations=[
                    {
                        "source_type": "youtube_transcript",
                        "url": "https://youtube.test/watch?v=1",
                        "fetched_at": "2026-10-07T00:00:00Z",
                        "quotes": ["x" * 900],
                        "viewpoint": "too long a quote is a full-text dump",
                    }
                ],
            )
        with self.assertRaises(ValidationError):
            self.service.record_source_observations(
                workspace_id="w",
                observations=[
                    {
                        "source_type": "unknown_source",
                        "url": "https://x.test",
                        "fetched_at": "2026-10-07T00:00:00Z",
                        "quotes": ["ok"],
                        "viewpoint": "unsupported source",
                    }
                ],
            )

    def test_podcast_observations_are_no_longer_accepted(self):
        with self.assertRaises(ValidationError):
            self.service.record_source_observations(
                workspace_id="w",
                observations=[
                    {
                        "source_type": "podcast_episode",
                        "url": "https://podcast.test/1",
                        "fetched_at": "2026-10-07T00:00:00Z",
                        "quotes": ["q"],
                        "viewpoint": "podcast view",
                    }
                ],
            )

    def test_task_d_record_site_pages_updates_coverage_snapshot(self):
        brand = self._confirmed("https://site.test")
        snapshot = self.service.record_site_pages(
            workspace_id="w",
            brand_id=brand["id"],
            pages=[
                {
                    "url": "https://site.test/enterprise-ai-agents",
                    "title": "Enterprise AI agents",
                    "h1": "Enterprise AI agents",
                    "summary": "How enterprise teams deploy AI agents.",
                }
            ],
        )
        self.assertEqual(snapshot["pages"][0]["url"], "https://site.test/enterprise-ai-agents")
        latest = self.service.get_brand_profile_by_website(workspace_id="w", website="site.test")
        self.assertEqual(latest["site_snapshot"]["id"], snapshot["id"])

    def test_task_d_mcp_provider_search_preserves_structured_items(self):
        raw_items = [
            {
                "type": "ai_overview",
                "text": "AI says hi",
                "items": [
                    {
                        "type": "ai_overview_element",
                        "text": "more",
                        "references": [
                            {"type": "ai_overview_reference", "url": "https://a.test/x", "title": "A"}
                        ],
                    }
                ],
            },
            {"type": "organic", "rank_group": 1, "url": "https://a.test/x", "title": "A"},
            {"type": "people_also_ask", "items": [{"title": "What is it?", "answer": ""}]},
            {"type": "related_searches", "items": [{"title": "related one"}]},
        ]

        def transport(payload, session_id):
            if payload.get("method") == "initialize":
                return {"result": {"protocolVersion": "2025-06-18"}}, {}
            if payload.get("method") == "notifications/initialized":
                return {}, {}
            body = json.dumps(
                {
                    "status_code": 20000,
                    "tasks": [{"status_code": 20000, "cost": 0.002, "result": [{"items": raw_items}]}],
                }
            )
            return {"result": {"content": [{"type": "text", "text": body}]}}, {}

        provider = DataForSeoMcpProvider(
            endpoint="https://mcp.example.test/mcp", access_token="tok", transport=transport
        )
        response = provider.search("enterprise AI agents", market="US", language="en", device="desktop")
        payload = response.payload
        self.assertTrue(payload["aio"]["present"])
        self.assertEqual(payload["aio"]["references"][0]["url"], "https://a.test/x")
        self.assertEqual(payload["paa"][0]["question"], "What is it?")
        self.assertEqual(payload["related"], ["related one"])
        self.assertIn("ai_overview", payload["serp_features"])
        self.assertNotIn("raw_items", payload)

    # ---------------------------------------------------------------- task E

    def test_task_e_content_gap_and_scores_come_from_real_signals(self):
        brand = self._confirmed()
        result = self._run(brand["id"])
        opportunities = result["opportunities"]
        self.assertTrue(opportunities)
        for row in opportunities:
            breakdown = row["score_breakdown"]
            for key in (
                "demand",
                "business_relevance",
                "brand_fit",
                "ranking_feasibility",
                "intent_alignment",
                "serp_structural_signal",
                "information_gain_feasibility",
                "evidence_readiness",
                "existing_coverage_penalty",
                "aio_saturation",
            ):
                self.assertIn(key, breakdown)
            # The breakdown is transparent: it sums to the clamped score.
            self.assertEqual(row["score"], max(0, min(100, sum(breakdown.values()))))
            self.assertIn(row["aio_saturation"], {"covered", "unobserved"})
            self.assertIn("AI overview", row["content_gap"])
            self.assertEqual(breakdown["aio_saturation"], -5 if row["aio_saturation"] == "covered" else 0)
            self.assertEqual(row["scoring_version"], "v2")
            self.assertIn(row["evidence_readiness"], {"READY", "NEEDS_EVIDENCE", "UNVERIFIED"})
            # Structural SERP diversity is no longer folded into a
            # differentiation component.
            self.assertNotIn("differentiation", breakdown)
            self.assertNotIn("serp_weakness", breakdown)

    def test_task_e_coverage_uses_the_site_snapshot(self):
        created = self.service.create_brand_profile(
            workspace_id="w",
            name="Covered",
            website="https://covered.test",
            product="AI workflow software",
            target_customer="enterprise operations teams",
            seed_topics=["enterprise AI agents"],
            site_pages=[
                {
                    "url": "https://covered.test/enterprise-ai-agents",
                    "title": "Enterprise AI agents strategy",
                    "h1": "Enterprise AI agents",
                    "summary": "enterprise ai agents implementation and strategy",
                }
            ],
        )
        brand_id = created["brand_profile"]["id"]
        self.service.confirm_brand_context(workspace_id="w", brand_id=brand_id, **CONFIRM)
        result = self._run(brand_id)
        coverages = [row["existing_site_coverage"] for row in result["opportunities"]]
        self.assertTrue(any(value > 0 for value in coverages))

    # ---------------------------------------------------------------- clusters

    def test_cluster_gate_requires_serp_then_community(self):
        brand = self._confirmed()
        result = self._run(brand["id"])
        run_id = result["research_run"]["id"]
        selected = next(row for row in result["opportunities"] if row["decision_type"] == "CREATE")

        # Nothing observed yet: the cluster is blocked.
        readiness = self.service.get_cluster_readiness(workspace_id="w", research_run_id=run_id)
        self.assertFalse(readiness["ready"])
        with self.assertRaises(StateError):
            self.service.create_content_cluster(workspace_id="w", opportunity_id=selected["id"])

        # A SERP observation alone is not enough.
        self.service.record_source_observations(
            workspace_id="w",
            research_run_id=run_id,
            observations=[
                {
                    "source_type": "serp_result",
                    "url": "https://www.google.com/search?q=enterprise+AI+agents",
                    "fetched_at": "2026-10-07T00:00:00Z",
                    "quotes": ["AI overview present"],
                    "viewpoint": "Rendered SERP read: AI overview and PAA present.",
                }
            ],
        )
        readiness = self.service.get_cluster_readiness(workspace_id="w", research_run_id=run_id)
        self.assertTrue(readiness["serp_read"])
        self.assertEqual(readiness["families_covered"], [])
        with self.assertRaises(StateError):
            self.service.create_content_cluster(workspace_id="w", opportunity_id=selected["id"])

        # One community family is enough and is recorded as degraded.
        self.service.record_source_observations(
            workspace_id="w",
            research_run_id=run_id,
            observations=[
                {
                    "source_type": "youtube_video",
                    "url": "https://youtube.example.test/watch?v=1",
                    "fetched_at": "2026-10-07T00:00:00Z",
                    "quotes": ["Governance is the real blocker"],
                    "viewpoint": "Practitioners say governance, not model choice, blocks rollout.",
                }
            ],
        )
        readiness = self.service.get_cluster_readiness(workspace_id="w", research_run_id=run_id)
        self.assertTrue(readiness["ready"])
        self.assertTrue(readiness["degraded"])
        self.assertEqual(readiness["families_covered"], ["community"])
        self.assertEqual(readiness["community_families_covered"], ["youtube"])
        cluster = self.service.create_content_cluster(workspace_id="w", opportunity_id=selected["id"])
        record = cluster["topic_cluster"]
        self.assertEqual(record["gate_status"], "DEGRADED_MINIMUM")
        # The funnel is descriptive, not a mandate.
        self.assertEqual(sum(record["funnel_distribution"].values()), len(record["items"]))

    def test_cluster_gate_full_coverage_marks_families(self):
        brand = self._confirmed()
        result = self._run(brand["id"])
        run_id = result["research_run"]["id"]
        selected = next(row for row in result["opportunities"] if row["decision_type"] == "CREATE")
        self.service.record_source_observations(
            workspace_id="w",
            research_run_id=run_id,
            observations=[
                {
                    "source_type": "serp_result",
                    "url": "https://google.test/s?q=x",
                    "fetched_at": "2026-10-07T00:00:00Z",
                    "quotes": ["q"],
                    "viewpoint": "serp read",
                },
                {
                    "source_type": "reddit_thread",
                    "url": "https://reddit.test/x",
                    "fetched_at": "2026-10-07T00:00:00Z",
                    "quotes": ["q"],
                    "viewpoint": "reddit view",
                },
                {
                    "source_type": "youtube_video",
                    "url": "https://youtube.test/watch",
                    "fetched_at": "2026-10-07T00:00:00Z",
                    "quotes": ["q"],
                    "viewpoint": "youtube view",
                },
                {
                    "source_type": "official_documentation",
                    "url": "https://docs.vendor.test/agents",
                    "fetched_at": "2026-10-07T00:00:00Z",
                    "quotes": ["q"],
                    "viewpoint": "official docs describe runtime limits",
                    "research_purpose": "competitor_gap",
                    "relevance": 80,
                },
            ],
        )
        readiness = self.service.get_cluster_readiness(workspace_id="w", research_run_id=run_id)
        self.assertEqual(readiness["families_covered"], ["community", "technical"])
        self.assertEqual(readiness["community_families_covered"], ["reddit", "youtube"])
        self.assertFalse(readiness["degraded"])
        cluster = self.service.create_content_cluster(workspace_id="w", opportunity_id=selected["id"])
        self.assertEqual(cluster["topic_cluster"]["gate_status"], "FULL_COVERAGE")

    def test_task_e_observations_feed_the_opportunity(self):
        brand = self._confirmed()
        estimate = self.service.estimate_topic_research(
            workspace_id="w", brand_id=brand["id"], budget_cap_usd=5.0
        )
        run_id = estimate["research_run"]["id"]
        self.service.record_source_observations(
            workspace_id="w",
            research_run_id=run_id,
            observations=[
                {
                    "source_type": "competitor_page",
                    "url": "https://example-1.test/enterprise-AI-agents",
                    "fetched_at": "2026-10-07T00:00:00Z",
                    "quotes": ["Most guides skip governance"],
                    "viewpoint": "Competitors cover setup but ignore governance.",
                    "confidence": 80,
                }
            ],
        )
        result = self.service.run_topic_research(
            workspace_id="w",
            research_run_id=run_id,
            confirmation_token=estimate["research_estimate"]["confirmation_token"],
        )
        summaries = " ".join(row["competitor_summary"] for row in result["opportunities"])
        self.assertIn("governance", summaries)


class SourceObservationModelTest(unittest.TestCase):
    @staticmethod
    def _observation(**overrides):
        base = dict(
            id="obs",
            workspace_id="w",
            brand_id="b",
            source="host_agent",
            status="COMPLETED",
            lineage=[],
            source_type="competitor_page",
            url="https://c.test/x",
            fetched_at="2026-10-07T00:00:00Z",
            quotes=["a quote"],
            viewpoint="a viewpoint",
            confidence=50,
            cache_key="cache-key",
            expires_at="2026-10-21T00:00:00Z",
        )
        base.update(overrides)
        return SourceObservation(**base)

    def test_valid_observation(self):
        self._observation().validate()

    def test_quote_required_for_page_like_sources(self):
        with self.assertRaises(ValidationError):
            self._observation(quotes=[]).validate()

    def test_rejects_unknown_source_and_bad_confidence(self):
        with self.assertRaises(ValidationError):
            self._observation(source_type="twitter").validate()
        with self.assertRaises(ValidationError):
            self._observation(confidence=101).validate()

    def test_requires_cache_key_and_expiry(self):
        with self.assertRaises(ValidationError):
            self._observation(cache_key="").validate()

    def test_youtube_sources_are_supported_and_quote_required(self):
        self._observation(source_type="youtube_video").validate()
        self._observation(source_type="youtube_transcript").validate()
        with self.assertRaises(ValidationError):
            self._observation(source_type="youtube_video", quotes=[]).validate()

    def test_legacy_podcast_records_still_rehydrate(self):
        # Read tolerance for stores written before podcasts were removed.
        self._observation(source_type="podcast_episode").validate()
        self._observation(source_type="podcast_transcript").validate()

    def test_brand_draft_cannot_reuse_confirmed_fields(self):
        profile = BrandProfile(
            id="b",
            workspace_id="w",
            brand_id="b",
            source="input",
            status="DRAFT",
            lineage=[],
            name="Acme",
            website="https://acme.test",
            context_status="CONFIRMED",
        )
        with self.assertRaises(ValidationError):
            profile.validate()


class TopicClusterModelTest(unittest.TestCase):
    def _cluster(self, items, **overrides):
        fields = dict(
            id="c",
            workspace_id="w",
            brand_id="b",
            source="cluster_planner",
            status="COMPLETED",
            lineage=[],
            research_run_id="run",
            pillar_opportunity_id="opp",
            pillar_keyword="enterprise AI agents",
            items=items,
            funnel_distribution={"TOFU": 1, "MOFU": 0, "BOFU": 1},
            source_coverage={},
            gate_status="FULL_COVERAGE",
        )
        fields.update(overrides)
        return TopicCluster(**fields)

    def test_cluster_does_not_require_tofu_and_bofu(self):
        tofu = {"role": "pillar", "target_query": "a", "outline_id": "o1", "funnel_stage": "TOFU"}
        bofu = {"role": "supporting", "target_query": "b", "outline_id": "o2", "funnel_stage": "BOFU"}
        self._cluster([tofu, bofu]).validate()
        # A single-intent topic is a valid cluster; the funnel is descriptive,
        # not a generation mandate.
        self._cluster([tofu]).validate()
        self._cluster([bofu]).validate()
        mofu_only = {"role": "pillar", "target_query": "c", "outline_id": "o3", "funnel_stage": "MOFU"}
        self._cluster([mofu_only]).validate()

    def test_cluster_rejects_unknown_stage_role_and_gate(self):
        tofu = {"role": "pillar", "target_query": "a", "outline_id": "o1", "funnel_stage": "TOFU"}
        bofu = {"role": "supporting", "target_query": "b", "outline_id": "o2", "funnel_stage": "BOFU"}
        with self.assertRaises(ValidationError):
            self._cluster([tofu, dict(bofu, funnel_stage="AIDA")]).validate()
        with self.assertRaises(ValidationError):
            self._cluster([tofu, dict(bofu, role="orphan")]).validate()
        with self.assertRaises(ValidationError):
            self._cluster([tofu, bofu], gate_status="MAYBE").validate()


if __name__ == "__main__":
    unittest.main()
