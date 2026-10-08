"""Strategy-enhancement acceptance tests (brand entities, semantic coverage,
information gain, relevance gate, evidence-driven outlines).

These tests target the iteration-3 behaviour on top of the iteration-2 core.
Every claim is exercised against an offline provider double; no live call is
made.
"""

import tempfile
import unittest
from pathlib import Path

from seo_topic_planner.errors import AuthError, NotFoundError, StateError, ValidationError
from seo_topic_planner.models import BrandProfile, SourceObservation
from seo_topic_planner.providers import FakeSeoProvider
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


def observation(**overrides):
    base = dict(
        id=overrides.pop("id", "observation_unit"),
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


class StrategyEnhancementTest(unittest.TestCase):
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

    def _confirmed(
        self,
        workspace_id="w",
        website="https://acme.test",
        name="Acme",
        seed_topics=None,
        site_pages=None,
    ):
        created = self.service.create_brand_profile(
            workspace_id=workspace_id,
            name=name,
            website=website,
            product="AI workflow software",
            target_customer="enterprise operations teams",
            seed_topics=seed_topics or ["enterprise AI agents"],
            site_pages=site_pages
            or [{"url": f"{website}/agents", "title": "AI agent platform", "h1": "AI agents"}],
        )
        brand_id = created["brand_profile"]["id"]
        self.service.confirm_brand_context(workspace_id=workspace_id, brand_id=brand_id, **CONFIRM)
        return created, brand_id

    def _run(self, brand_id, workspace_id="w"):
        estimate = self.service.estimate_topic_research(
            workspace_id=workspace_id, brand_id=brand_id, budget_cap_usd=5.0
        )
        return self.service.run_topic_research(
            workspace_id=workspace_id,
            research_run_id=estimate["research_run"]["id"],
            confirmation_token=estimate["research_estimate"]["confirmation_token"],
        )

    def _gate(self, run_id, observations, workspace_id="w"):
        return self.service.record_source_observations(
            workspace_id=workspace_id,
            research_run_id=run_id,
            observations=observations,
        )

    # ------------------------------------------------------------- brand entity

    def test_1_brand_name_differs_from_legal_name_keeps_relationship(self):
        created, brand_id = self._confirmed()
        default = created["brand_entity"]
        organization = self.service.create_brand_entity(
            workspace_id="w",
            brand_id=brand_id,
            entity_type="organization",
            canonical_name="Acme Holdings Inc.",
            legal_name="Acme Holdings Incorporated",
            official_website="https://acme.test",
        )["brand_entity"]
        # The brand is parented to the company; the two names differ and neither
        # is overwritten.
        updated = self.service.update_brand_entity(
            workspace_id="w",
            entity_id=default["id"],
            parent_entity_id=organization["id"],
        )["brand_entity"]
        self.assertEqual(updated["parent_entity_id"], organization["id"])
        self.assertNotEqual(updated["canonical_name"], organization["legal_name"])
        entities = self.service.list_brand_entities(workspace_id="w", brand_id=brand_id)["entities"]
        self.assertEqual({entity["entity_type"] for entity in entities}, {"brand", "organization"})

    def test_2_same_named_brands_do_not_merge(self):
        first, _ = self._confirmed(website="https://one.test", name="Nimbus")
        second, _ = self._confirmed(website="https://two.test", name="Nimbus")
        self.assertNotEqual(first["brand_entity"]["id"], second["brand_entity"]["id"])
        self.assertNotEqual(first["brand_profile"]["id"], second["brand_profile"]["id"])

    def test_3_one_brand_multiple_products_are_representable(self):
        created, brand_id = self._confirmed()
        brand_entity_id = created["brand_entity"]["id"]
        products = []
        for name in ("Acme Agents", "Acme Workflows"):
            products.append(
                self.service.create_brand_entity(
                    workspace_id="w",
                    brand_id=brand_id,
                    entity_type="product",
                    canonical_name=name,
                    parent_entity_id=brand_entity_id,
                    official_product_urls=[f"https://acme.test/{name.split()[-1].lower()}"],
                )["brand_entity"]
            )
        listed = self.service.list_brand_entities(workspace_id="w", brand_id=brand_id)["entities"]
        product_names = {entity["canonical_name"] for entity in listed if entity["entity_type"] == "product"}
        self.assertEqual(product_names, {"Acme Agents", "Acme Workflows"})
        self.assertTrue(all(entity["parent_entity_id"] == brand_entity_id for entity in products))

    def test_4_official_and_third_party_conflict_stays_pending(self):
        created, brand_id = self._confirmed()
        entity = self.service.create_brand_entity(
            workspace_id="w",
            brand_id=brand_id,
            entity_type="brand",
            canonical_name="Acme",
            official_website="https://acme.test",
            candidate_external_profiles=[
                {
                    "url": "https://other-company.test/acme",
                    "profile_type": "company_profile",
                    "source": "serp",
                    "status": "CANDIDATE",
                }
            ],
            update_existing=True,
        )["brand_entity"]
        # The conflicting third-party lead is kept as a candidate, never asserted.
        self.assertEqual(entity["verified_external_profiles"], [])
        self.assertEqual(len(entity["candidate_external_profiles"]), 1)
        self.assertEqual(entity["identity_status"], "DRAFT")

    def test_5_no_verified_account_means_empty_same_as(self):
        created, brand_id = self._confirmed()
        entity = self.service.get_brand_entity(
            workspace_id="w", entity_id=created["brand_entity"]["id"]
        )
        self.assertEqual(entity["verified_external_profiles"], [])
        self.assertNotIn("sameAs", entity)

    def test_6_brand_entities_do_not_cross_workspaces(self):
        created, brand_id = self._confirmed(workspace_id="w1")
        with self.assertRaises(NotFoundError):
            self.service.list_brand_entities(workspace_id="w2", brand_id=brand_id)
        with self.assertRaises(NotFoundError):
            self.service.get_brand_entity(
                workspace_id="w2", entity_id=created["brand_entity"]["id"]
            )

    def test_7_confirmed_brand_entity_is_reused_by_topic_research(self):
        created, brand_id = self._confirmed()
        entity = self.service.create_brand_entity(
            workspace_id="w",
            brand_id=brand_id,
            entity_type="product",
            canonical_name="Acme Agents",
            official_product_urls=["https://acme.test/agents"],
        )["brand_entity"]
        self.service.confirm_brand_entity(workspace_id="w", entity_id=entity["id"])
        result = self._run(brand_id)
        opportunities = result["opportunities"]
        self.assertTrue(opportunities)
        all_brand_entity_ids = {item for row in opportunities for item in row["brand_entity_ids"]}
        self.assertIn(created["brand_entity"]["id"], all_brand_entity_ids)

    # ---------------------------------------------------------- semantic coverage

    def test_8_synonym_different_words_same_intent_can_be_marked_covered(self):
        _, brand_id = self._confirmed()
        result = self._run(brand_id)
        opportunity = result["opportunities"][0]
        updated = self.service.record_opportunity_analysis(
            workspace_id="w",
            opportunity_id=opportunity["id"],
            semantic_coverage={
                "status": "COVERED",
                "basis": "page_body_read",
                "intent_match": 90,
                "entity_coverage": 85,
                "explanation_depth": 80,
                "evidence_ids": ["evidence_site"],
            },
        )
        self.assertEqual(updated["semantic_coverage"]["status"], "COVERED")
        self.assertEqual(updated["decision_type"], "IGNORE")
        self.assertEqual(updated["overlap_risk"], "high")

    def test_9_same_keyword_different_intent_is_not_merged(self):
        # A site page already ranks lexically for the exact query, so the
        # default decision would be IGNORE; the agent's different-intent
        # judgement must override it.
        created, brand_id = self._confirmed(
            site_pages=[
                {
                    "url": "https://acme.test/enterprise-ai-agents",
                    "title": "Enterprise AI agents",
                    "h1": "Enterprise AI agents",
                    "summary": "enterprise ai agents",
                }
            ]
        )
        result = self._run(brand_id)
        covered = next(
            row for row in result["opportunities"] if row["existing_site_coverage"] >= 85
        )
        self.assertEqual(covered["decision_type"], "IGNORE")
        updated = self.service.record_opportunity_analysis(
            workspace_id="w",
            opportunity_id=covered["id"],
            semantic_coverage={
                "status": "GAP",
                "basis": "page_body_read",
                "intent_match": 20,
                "note": "The existing page targets a different intent.",
            },
        )
        self.assertEqual(updated["decision_type"], "CREATE")
        self.assertEqual(updated["overlap_risk"], "low")

    def test_10_mention_without_explanation_is_not_full_coverage(self):
        _, brand_id = self._confirmed()
        result = self._run(brand_id)
        opportunity = result["opportunities"][0]
        updated = self.service.record_opportunity_analysis(
            workspace_id="w",
            opportunity_id=opportunity["id"],
            semantic_coverage={
                "status": "PARTIAL",
                "basis": "page_body_read",
                "entity_coverage": 40,
                "explanation_depth": 10,
                "note": "The entity is named once but never explained.",
            },
        )
        self.assertEqual(updated["semantic_coverage"]["status"], "PARTIAL")
        self.assertEqual(updated["semantic_coverage"]["explanation_depth"], 10)
        self.assertNotEqual(updated["decision_type"], "IGNORE")
        self.assertIn("Extend", updated["recommended_next_action"])

    def test_11_missing_page_body_yields_unknown(self):
        _, brand_id = self._confirmed()
        result = self._run(brand_id)
        for row in result["opportunities"]:
            self.assertEqual(row["semantic_coverage"]["status"], "UNKNOWN")
            self.assertEqual(row["semantic_coverage"]["basis"], "no_page_body_evidence")

    # ------------------------------------------------------------ information gain

    def _information_gain(self, brand_entities=None, observations=None, signals=None):
        brand = BrandProfile(
            id="b",
            workspace_id="w",
            brand_id="b",
            source="input",
            status="CONFIRMED",
            lineage=[],
            name="Acme",
            website="https://acme.test",
            context_status="CONFIRMED",
            target_market="United States",
            language="en",
            target_customer="ops teams",
            positioning="pos",
            primary_scenario="scenario",
            main_business="software",
            core_features=["AI agents"],
        )
        return self.service._information_gain(
            brand=brand,
            brand_entities=brand_entities or [],
            signals=signals
            or {
                "organic": [{"url": "https://x.test"}],
                "content_types": ["guide"],
                "unanswered_questions": [],
                "paa": [],
            },
            observations=observations or [],
        )

    def test_12_high_domain_diversity_does_not_imply_information_gain(self):
        # Five distinct competitor domains (the fake SERP) with no brand evidence
        # must not read as high information gain.
        gain = self._information_gain(
            signals={
                "organic": [{"url": f"https://dom{i}.test"} for i in range(5)],
                "content_types": ["guide", "article", "comparison", "video", "forum"],
                "unanswered_questions": [],
                "paa": [],
            }
        )
        self.assertNotEqual(gain["readiness"], "READY")

    def test_13_competitor_gap_without_brand_evidence_needs_evidence(self):
        gain = self._information_gain(
            observations=[
                observation(
                    source_type="competitor_page",
                    research_purpose="competitor_gap",
                    viewpoint="Competitors publish no original data or benchmarks.",
                )
            ]
        )
        self.assertEqual(gain["readiness"], "NEEDS_EVIDENCE")
        self.assertTrue(any(item["gap_type"] == "original_data" for item in gain["items"]))

    def test_14_brand_case_study_enables_first_hand_information_gain(self):
        gain = self._information_gain(
            observations=[
                observation(
                    id="observation_case",
                    source_type="case_study",
                    research_purpose="case_study",
                    viewpoint="Our rollout cut manual review time.",
                    url="https://acme.test/case-study",
                    quotes=["We cut review time in half"],
                )
            ]
        )
        self.assertEqual(gain["readiness"], "READY")
        first_hand = next(item for item in gain["items"] if item["gap_type"] == "first_hand_experience")
        self.assertEqual(first_hand["readiness"], "READY")
        self.assertIn("observation_case", first_hand["brand_evidence_ids"])

    def test_15_every_information_gain_item_is_traceable_or_flagged(self):
        gain = self._information_gain(
            signals={
                "organic": [{"url": "https://x.test"}],
                "content_types": ["guide"],
                "unanswered_questions": ["What is enterprise AI agent governance?"],
                "paa": [{"question": "What is enterprise AI agent governance?"}],
            },
            observations=[
                observation(
                    source_type="competitor_page",
                    research_purpose="competitor_gap",
                    viewpoint="Competitors publish no original data.",
                )
            ],
        )
        self.assertTrue(gain["items"])
        for item in gain["items"]:
            self.assertTrue(item["brand_evidence_ids"] or item["missing_evidence"], item)

    # ------------------------------------------------------------- cluster/outline

    def test_16_tofu_only_topic_creates_a_cluster_without_bofu(self):
        _, brand_id = self._confirmed()
        result = self._run(brand_id)
        opportunity = next(
            row for row in result["opportunities"] if row["decision_type"] == "CREATE"
        )
        candidates = [
            {
                "target_query": "what is enterprise ai agents",
                "funnel_stage": "TOFU",
                "evidence_ids": ["evidence_x"],
                "recommended_angle": "",
                "search_intent": "informational",
            },
            {
                "target_query": "enterprise ai agents overview",
                "funnel_stage": "TOFU",
                "evidence_ids": ["evidence_y"],
                "recommended_angle": "",
                "search_intent": "informational",
            },
        ]
        opportunity_record = self.service.store.get(
            opportunity["id"], workspace_id="w", object_type="topic_opportunity"
        )
        selected = self.service._select_cluster_items(
            pillar=opportunity_record, candidates=candidates
        )
        self.assertTrue(selected)
        self.assertEqual({item["funnel_stage"] for item in selected}, {"TOFU"})

    def test_17_different_topics_do_not_share_the_same_outline(self):
        first_created, first_brand = self._confirmed(
            workspace_id="w", website="https://one.test", name="One", seed_topics=["enterprise AI agents"]
        )
        second_created, second_brand = self._confirmed(
            workspace_id="w",
            website="https://two.test",
            name="Two",
            seed_topics=["warehouse robotics picking"],
        )
        first_run = self._run(first_brand)
        second_run = self._run(second_brand)
        first_opp = next(row for row in first_run["opportunities"] if row["decision_type"] == "CREATE")
        second_opp = next(row for row in second_run["opportunities"] if row["decision_type"] == "CREATE")
        first_h2 = self._pillar_sections(first_opp["id"], first_brand)
        second_h2 = self._pillar_sections(second_opp["id"], second_brand)
        self.assertNotEqual(first_h2, second_h2)

    def _pillar_sections(self, opportunity_id, brand_id):
        run_id = self.service.get_topic_opportunity(
            workspace_id="w", opportunity_id=opportunity_id
        )["research_run_id"]
        for observation in [
            {
                "source_type": "serp_result",
                "url": "https://google.test/s",
                "fetched_at": "2026-10-07T00:00:00Z",
                "quotes": ["q"],
                "viewpoint": "serp read",
            },
            {
                "source_type": "reddit_thread",
                "url": "https://reddit.test/x",
                "fetched_at": "2026-10-07T00:00:00Z",
                "quotes": ["q"],
                "viewpoint": "community view",
            },
        ]:
            self._gate(run_id, [observation])
        cluster = self.service.create_content_cluster(workspace_id="w", opportunity_id=opportunity_id)
        pillar = next(outline for outline in cluster["outlines"] if outline["role"] == "pillar")
        return [section["h2"] for section in pillar["sections"]]

    def test_18_outline_sections_map_to_research_findings(self):
        _, brand_id = self._confirmed()
        result = self._run(brand_id)
        opportunity = next(row for row in result["opportunities"] if row["decision_type"] == "CREATE")
        run_id = result["research_run"]["id"]
        self._gate(
            run_id,
            [
                {
                    "source_type": "serp_result",
                    "url": "https://google.test/s",
                    "fetched_at": "2026-10-07T00:00:00Z",
                    "quotes": ["q"],
                    "viewpoint": "serp read",
                },
                {
                    "source_type": "reddit_thread",
                    "url": "https://reddit.test/x",
                    "fetched_at": "2026-10-07T00:00:00Z",
                    "quotes": ["q"],
                    "viewpoint": "community view",
                },
            ],
        )
        cluster = self.service.create_content_cluster(workspace_id="w", opportunity_id=opportunity["id"])
        pillar = next(outline for outline in cluster["outlines"] if outline["role"] == "pillar")
        opportunity_record = self.service.store.get(
            opportunity["id"], workspace_id="w", object_type="topic_opportunity"
        )
        payload, _ = self.service._pillar_serp(workspace_id="w", pillar=opportunity_record)
        paa_questions = [row["question"] for row in payload.get("paa", [])]
        self.assertTrue(paa_questions)
        section_h2 = {section["h2"] for section in pillar["sections"]}
        self.assertTrue(section_h2 & set(paa_questions))
        for section in pillar["sections"]:
            self.assertTrue(section["required_evidence_ids"] or section["missing_evidence"])

    def test_19_non_community_evidence_satisfies_the_gate(self):
        _, brand_id = self._confirmed()
        result = self._run(brand_id)
        run_id = result["research_run"]["id"]
        selected = next(row for row in result["opportunities"] if row["decision_type"] == "CREATE")
        self._gate(
            run_id,
            [
                {
                    "source_type": "serp_result",
                    "url": "https://google.test/s",
                    "fetched_at": "2026-10-07T00:00:00Z",
                    "quotes": ["q"],
                    "viewpoint": "serp read",
                },
                {
                    "source_type": "official_documentation",
                    "url": "https://docs.vendor.test/agents",
                    "fetched_at": "2026-10-07T00:00:00Z",
                    "quotes": ["Runtime limits are documented here"],
                    "viewpoint": "Official docs define the runtime model.",
                    "research_purpose": "technical_depth",
                },
            ],
        )
        readiness = self.service.get_cluster_readiness(workspace_id="w", research_run_id=run_id)
        self.assertTrue(readiness["ready"])
        self.assertEqual(readiness["families_covered"], ["technical"])
        self.assertEqual(readiness["community_families_covered"], [])
        cluster = self.service.create_content_cluster(workspace_id="w", opportunity_id=selected["id"])
        self.assertTrue(cluster["topic_cluster"]["items"])

    def test_20a_brand_only_evidence_does_not_satisfy_the_gate(self):
        _, brand_id = self._confirmed()
        result = self._run(brand_id)
        run_id = result["research_run"]["id"]
        selected = next(row for row in result["opportunities"] if row["decision_type"] == "CREATE")
        self._gate(
            run_id,
            [
                {
                    "source_type": "serp_result",
                    "url": "https://google.test/s",
                    "fetched_at": "2026-10-07T00:00:00Z",
                    "quotes": ["q"],
                    "viewpoint": "serp read",
                },
                {
                    "source_type": "brand_website",
                    "url": "https://acme.test/blog/agents",
                    "fetched_at": "2026-10-07T00:00:00Z",
                    "quotes": ["Our enterprise deployment guide"],
                    "viewpoint": "The brand's own site already covers deployment.",
                },
            ],
        )
        readiness = self.service.get_cluster_readiness(workspace_id="w", research_run_id=run_id)
        self.assertFalse(readiness["ready"])
        self.assertEqual(readiness["gate_status"], "BLOCKED_NO_EXTERNAL")
        self.assertEqual(readiness["families_covered"], ["brand"])
        self.assertEqual(readiness["external_families_covered"], [])
        with self.assertRaises(StateError):
            self.service.create_content_cluster(workspace_id="w", opportunity_id=selected["id"])

    def test_20b_blocked_source_attempts_are_visible_and_do_not_satisfy_the_gate(self):
        _, brand_id = self._confirmed()
        result = self._run(brand_id)
        run_id = result["research_run"]["id"]
        selected = next(row for row in result["opportunities"] if row["decision_type"] == "CREATE")
        self._gate(
            run_id,
            [
                {
                    "source_type": "serp_result",
                    "url": "https://google.test/s",
                    "fetched_at": "2026-10-07T00:00:00Z",
                    "quotes": ["q"],
                    "viewpoint": "serp read",
                }
            ],
        )
        attempt = {
            "source_type": "reddit_thread",
            "url": "https://www.reddit.com/r/SEO/",
            "method": "http",
            "outcome": "blocked",
            "reason": "HTTP 403 from Reddit network security",
            "attempted_at": "2026-10-07T00:00:00Z",
        }
        stored = self.service.record_source_attempts(
            workspace_id="w", research_run_id=run_id, attempts=[attempt]
        )
        self.assertEqual(stored["count"], 1)
        # Re-submitting the same attempt is idempotent.
        again = self.service.record_source_attempts(
            workspace_id="w", research_run_id=run_id, attempts=[attempt]
        )
        self.assertEqual(again["attempts"][0]["id"], stored["attempts"][0]["id"])

        readiness = self.service.get_cluster_readiness(workspace_id="w", research_run_id=run_id)
        self.assertFalse(readiness["ready"])
        self.assertEqual(readiness["gate_status"], "BLOCKED_NO_EXTERNAL")
        self.assertEqual(len(readiness["attempts"]), 1)
        self.assertEqual(len(readiness["blocked_attempts"]), 1)
        with self.assertRaises(StateError):
            self.service.create_content_cluster(workspace_id="w", opportunity_id=selected["id"])

    def test_20c_source_attempt_validation(self):
        _, brand_id = self._confirmed()
        result = self._run(brand_id)
        run_id = result["research_run"]["id"]
        with self.assertRaises(ValidationError):
            self.service.record_source_attempts(
                workspace_id="w",
                research_run_id=run_id,
                attempts=[
                    {
                        "source_type": "reddit_thread",
                        "url": "https://www.reddit.com/r/SEO/",
                        "method": "carrier-pigeon",
                        "outcome": "blocked",
                        "reason": "x",
                        "attempted_at": "2026-10-07T00:00:00Z",
                    }
                ],
            )
        with self.assertRaises(ValidationError):
            self.service.record_source_attempts(
                workspace_id="w",
                research_run_id=run_id,
                attempts=[
                    {
                        "source_type": "reddit_thread",
                        "url": "",
                        "method": "http",
                        "outcome": "blocked",
                        "reason": "",
                        "attempted_at": "",
                    }
                ],
            )

    # ---------------------------------------------------------------- regression

    def test_20_core_guardrails_do_not_regress(self):
        # Unconnected runtime refuses research.
        offline = TopicPlannerService(Path(self.temp.name) / "offline")
        self.assertEqual(offline.get_data_provider_status(workspace_id="w")["status"], "AUTH_REQUIRED")
        with self.assertRaises(AuthError):
            offline.estimate_topic_research(workspace_id="w", brand_id="x")

        created, brand_id = self._confirmed()
        estimate = self.service.estimate_topic_research(
            workspace_id="w", brand_id=brand_id, budget_cap_usd=5.0
        )
        self.assertTrue(estimate["confirmation_required"])
        self.assertTrue(estimate["research_estimate"]["confirmation_token"])
        with self.assertRaises(StateError):
            self.service.run_topic_research(
                workspace_id="w",
                research_run_id=estimate["research_run"]["id"],
                confirmation_token="wrong",
            )
        result = self.service.run_topic_research(
            workspace_id="w",
            research_run_id=estimate["research_run"]["id"],
            confirmation_token=estimate["research_estimate"]["confirmation_token"],
        )
        # Evidence is persisted and traced.
        pack = self.service.store.get(
            result["research_evidence_pack"]["id"],
            workspace_id="w",
            object_type="research_evidence_pack",
        )
        self.assertTrue(pack.evidence_ids)
        for row in result["opportunities"]:
            self.assertEqual(row["scoring_version"], "v2")
            self.assertTrue(row["evidence_ids"])
            self.assertEqual(
                row["score"], max(0, min(100, sum(row["score_breakdown"].values())))
            )
        # Workspace isolation still holds for opportunities and clusters.
        with self.assertRaises(NotFoundError):
            self.service.get_topic_opportunity(
                workspace_id="other", opportunity_id=result["opportunities"][0]["id"]
            )


if __name__ == "__main__":
    unittest.main()
