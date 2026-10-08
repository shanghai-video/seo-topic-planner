import json
import tempfile
import unittest
import urllib.request
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

from seo_topic_planner.errors import BudgetError, NotFoundError, ProviderError, StateError, ValidationError
from seo_topic_planner.mcp_server import TOOLS, handle
from seo_topic_planner.models import BrandProfile, json_schemas
from seo_topic_planner.oauth import DataForSeoConnection, DataForSeoOAuth, TokenStore
from seo_topic_planner.providers import (
    DataForSeoMcpProvider,
    DataForSeoProvider,
    FakeSeoProvider,
    ProviderResponse,
)
from seo_topic_planner.service import TopicPlannerService


class CountingFake(FakeSeoProvider):
    def __init__(self):
        self.calls = 0

    def expand(self, *args, **kwargs):
        self.calls += 1
        return super().expand(*args, **kwargs)

    def keyword_metrics(self, *args, **kwargs):
        self.calls += 1
        return super().keyword_metrics(*args, **kwargs)

    def search(self, *args, **kwargs):
        self.calls += 1
        return super().search(*args, **kwargs)

    def inspect(self, *args, **kwargs):
        self.calls += 1
        return super().inspect(*args, **kwargs)


class CostedFake(CountingFake):
    """Exercises the service's live-provider estimate policy without network I/O."""

    name = "live_policy_test"


class PricedFake(CountingFake):
    """A deterministic provider that reports a real per-call cost."""

    name = "priced_live_test"

    def __init__(self, unit_cost=0.5):
        super().__init__()
        self.unit_cost = unit_cost

    @staticmethod
    def _price(response, cost):
        return ProviderResponse(response.provider, response.request_id, cost, response.payload)

    def expand(self, *args, **kwargs):
        return self._price(super().expand(*args, **kwargs), self.unit_cost)

    def keyword_metrics(self, *args, **kwargs):
        return self._price(super().keyword_metrics(*args, **kwargs), self.unit_cost)

    def search(self, *args, **kwargs):
        return self._price(super().search(*args, **kwargs), self.unit_cost)


class TopicPlannerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.provider = CountingFake()
        self.service = TopicPlannerService(
            Path(self.temp.name),
            keyword_provider=self.provider,
            serp_provider=self.provider,
            page_provider=self.provider,
        )
        created = self.service.create_brand_profile(
            workspace_id="workspace-a",
            name="Acme",
            website="https://acme.test",
            product="AI workflow software",
            target_customer="enterprise operations teams",
            target_market="US",
            language="en",
            seed_topics=["enterprise AI agents"],
            site_pages=[{"url": "https://acme.test/agents", "title": "AI agent platform", "h1": "AI agents"}],
        )
        self.brand_id = created["brand_profile"]["id"]
        self.service.confirm_brand_context(
            workspace_id="workspace-a",
            brand_id=self.brand_id,
            target_market="United States",
            language="en",
            target_customer="enterprise operations teams",
            positioning="An AI workflow platform for enterprise operations teams",
            primary_scenario="automating enterprise workflows with AI agents",
            main_business="AI workflow software",
            core_features=["AI agents", "workflow automation"],
        )

    def tearDown(self):
        self.temp.cleanup()

    def estimate(self, budget=2.0):
        return self.service.estimate_topic_research(
            workspace_id="workspace-a", brand_id=self.brand_id, budget_cap_usd=budget
        )

    def run_research(self):
        estimate = self.estimate()
        return self.service.run_topic_research(
            workspace_id="workspace-a",
            research_run_id=estimate["research_run"]["id"],
            confirmation_token=estimate["research_estimate"]["confirmation_token"],
        )

    def _record_gate_observations(self, run_id):
        self.service.record_source_observations(
            workspace_id="workspace-a",
            research_run_id=run_id,
            observations=[
                {
                    "source_type": "serp_result",
                    "url": "https://www.google.com/search?q=enterprise+AI+agents",
                    "title": "Rendered SERP for enterprise AI agents",
                    "fetched_at": "2026-10-07T00:00:00Z",
                    "quotes": ["AI overview present", "PAA asks what it is"],
                    "viewpoint": "Rendered SERP: AI overview present with PAA and related searches.",
                    "confidence": 80,
                },
                {
                    "source_type": "reddit_thread",
                    "url": "https://reddit.example.test/r/x/comments/1",
                    "title": "enterprise AI agents in practice",
                    "fetched_at": "2026-10-07T00:00:00Z",
                    "quotes": ["Governance was the blocker"],
                    "viewpoint": "Practitioners complain about governance, not model quality.",
                    "confidence": 70,
                },
            ],
        )

    def test_fake_end_to_end_creates_evidence_backed_cluster(self):
        result = self.run_research()
        self.assertGreaterEqual(len(result["opportunities"]), 5)
        self.assertLessEqual(len(result["opportunities"]), 15)
        top = result["opportunities"][0]
        self.assertTrue(top["evidence_ids"])
        run_id = result["research_run"]["id"]
        selected = next(row for row in result["opportunities"] if row["decision_type"] == "CREATE")

        # The gate must block a cluster until the SERP and relevant evidence exist.
        readiness = self.service.get_cluster_readiness(
            workspace_id="workspace-a", research_run_id=run_id
        )
        self.assertFalse(readiness["ready"])
        with self.assertRaises(StateError):
            self.service.create_content_cluster(
                workspace_id="workspace-a", opportunity_id=selected["id"]
            )

        self._record_gate_observations(run_id)
        readiness = self.service.get_cluster_readiness(
            workspace_id="workspace-a", research_run_id=run_id
        )
        self.assertTrue(readiness["serp_read"])
        self.assertEqual(readiness["families_covered"], ["community"])
        self.assertTrue(readiness["ready"])

        cluster = self.service.create_content_cluster(
            workspace_id="workspace-a", opportunity_id=selected["id"]
        )
        record = cluster["topic_cluster"]
        self.assertEqual(record["pillar_opportunity_id"], selected["id"])
        self.assertEqual(record["gate_status"], "DEGRADED_MINIMUM")
        # The funnel is descriptive: every item has a stage and the distribution
        # sums to the item count, but no TOFU/BOFU pair is mandated.
        self.assertEqual(sum(record["funnel_distribution"].values()), len(record["items"]))
        self.assertEqual(len(record["items"]), len(cluster["outlines"]))
        self.assertEqual(cluster["topic_decision"]["decision_type"], "CREATE")
        for outline in cluster["outlines"]:
            self.assertEqual(outline["cluster_id"], record["id"])
            self.assertIn(outline["funnel_stage"], ("TOFU", "MOFU", "BOFU"))
            self.assertTrue(outline["required_evidence_ids"])
            self.assertIn(
                outline["content_status"],
                ("READY_TO_BRIEF", "NEEDS_RESEARCH", "NEEDS_BRAND_EVIDENCE", "LOW_VALUE"),
            )
            for section in outline["sections"]:
                self.assertIn("reader_question", section)
                self.assertIn("key_claim", section)
                self.assertIn("required_evidence_ids", section)

        fetched = self.service.get_topic_cluster(
            workspace_id="workspace-a", cluster_id=record["id"]
        )
        self.assertEqual(fetched["id"], record["id"])
        self.assertEqual(fetched["funnel_distribution"], record["funnel_distribution"])

    def test_confirmation_and_budget_are_server_enforced(self):
        provider = CostedFake()
        service = TopicPlannerService(
            Path(self.temp.name) / "costed",
            keyword_provider=provider,
            serp_provider=provider,
            page_provider=provider,
        )
        created = service.create_brand_profile(
            workspace_id="workspace-a",
            name="Costed Acme",
            website="https://costed.test",
            product="AI workflow software",
            target_customer="enterprise operations teams",
            seed_topics=["enterprise AI agents"],
        )
        service.confirm_brand_context(
            workspace_id="workspace-a",
            brand_id=created["brand_profile"]["id"],
            target_market="United States",
            language="en",
            target_customer="enterprise operations teams",
            positioning="Costed platform",
            primary_scenario="automating workflows",
            main_business="AI software",
            core_features=["workflow automation"],
        )
        estimate = service.estimate_topic_research(
            workspace_id="workspace-a", brand_id=created["brand_profile"]["id"], budget_cap_usd=0
        )
        with self.assertRaises(StateError):
            service.run_topic_research(
                workspace_id="workspace-a",
                research_run_id=estimate["research_run"]["id"],
                confirmation_token="wrong",
            )
        with self.assertRaises(BudgetError):
            service.run_topic_research(
                workspace_id="workspace-a",
                research_run_id=estimate["research_run"]["id"],
                confirmation_token=estimate["research_estimate"]["confirmation_token"],
            )
        self.assertEqual(provider.calls, 0)

    def test_estimate_uses_provider_policy_ceiling_and_matches_call_scope(self):
        estimate = self.estimate()
        value = estimate["research_estimate"]
        self.assertEqual(value["estimated_cost_usd"], 0.27)
        self.assertEqual(
            value["provider_calls"],
            {
                "keyword_expansion": 1,
                "keyword_metrics": 1,
                "serp": 15,
                "competitor_page": 0,
            },
        )
        context = estimate["estimate_context"]
        self.assertEqual(context["estimate_source"], "provider_policy_ceiling")
        self.assertEqual(context["budget_cap_usd"], 2.0)
        self.assertIn("conservative approval ceiling", context["price_note"])
        self.assertIn("not billed through this plugin budget", context["unbudgeted_collection_note"])

    def test_cached_research_does_not_repeat_provider_calls(self):
        self.run_research()
        first_calls = self.provider.calls
        second = self.run_research()
        self.assertGreater(first_calls, 0)
        self.assertEqual(self.provider.calls, first_calls)
        self.assertGreater(second["research_evidence_pack"]["cache_hits"], 0)
        self.assertEqual(second["research_evidence_pack"]["actual_cost_usd"], 0)

    def test_workspace_isolation_hides_objects(self):
        result = self.run_research()
        with self.assertRaises(NotFoundError):
            self.service.get_topic_opportunity(
                workspace_id="workspace-b", opportunity_id=result["opportunities"][0]["id"]
            )

    def test_invalid_model_and_live_provider_do_not_silently_fallback(self):
        with self.assertRaises(ValidationError):
            BrandProfile(
                id="brand",
                workspace_id="w",
                brand_id="brand",
                source="input",
                status="ok",
                lineage=[],
                name="",
                website="",
            ).validate()
        with self.assertRaises(ProviderError):
            DataForSeoProvider().expand("enterprise agents", market="US", language="en")

    def test_official_mcp_provider_uses_session_and_never_serializes_token(self):
        requests = []

        def transport(payload, session_id):
            requests.append((payload, session_id))
            if payload.get("method") == "initialize":
                return {"result": {"protocolVersion": "2025-06-18"}}, {"Mcp-Session-Id": "session-test"}
            if payload.get("method") == "notifications/initialized":
                return {}, {}
            return {
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {
                                    "status_code": 20000,
                                    "tasks": [
                                        {
                                            "status_code": 20000,
                                            "cost": 0.01,
                                            "result": [{"items": [{"keyword": "enterprise AI agents"}]}],
                                        }
                                    ],
                                }
                            ),
                        }
                    ]
                },
            }, {}

        provider = DataForSeoMcpProvider(
            endpoint="https://mcp.example.test/mcp", access_token="not-for-records", transport=transport
        )
        response = provider.expand("enterprise AI agents", market="US", language="en")
        self.assertEqual(response.provider, "dataforseo_mcp")
        self.assertEqual(response.cost_usd, 0.01)
        self.assertEqual(response.payload["keywords"], ["enterprise AI agents"])
        self.assertEqual(
            [item[0].get("method") for item in requests],
            ["initialize", "notifications/initialized", "tools/call"],
        )
        self.assertEqual(requests[-1][1], "session-test")
        self.assertNotIn("not-for-records", json.dumps(requests))

    def test_provider_status_reports_auth_required_without_a_provider(self):
        self.assertEqual(
            self.service.get_data_provider_status(workspace_id="workspace-a"),
            {
                "provider": "fake",
                "status": "READY",
                "data_mode": "TEST",
                "is_live": False,
                "can_connect": False,
                "next_action": "START_RESEARCH",
            },
        )
        empty = TopicPlannerService(Path(self.temp.name) / "status")
        self.assertEqual(
            empty.get_data_provider_status(workspace_id="workspace-a"),
            {
                "provider": "unavailable",
                "status": "AUTH_REQUIRED",
                "data_mode": "NOT_CONNECTED",
                "is_live": False,
                "can_connect": False,
                "next_action": "CONNECT_DATAFORSEO",
            },
        )
        provider = DataForSeoMcpProvider(endpoint="https://mcp.example.test/mcp", access_token="")
        service = TopicPlannerService(
            Path(self.temp.name) / "status-dfs",
            keyword_provider=provider,
            serp_provider=provider,
            page_provider=provider,
        )
        self.assertEqual(
            service.get_data_provider_status(workspace_id="workspace-a"),
            {
                "provider": "dataforseo_mcp",
                "status": "AUTH_REQUIRED",
                "data_mode": "NOT_CONNECTED",
                "is_live": False,
                "can_connect": False,
                "next_action": "CONNECT_DATAFORSEO",
            },
        )

    def test_live_status_and_estimate_context_require_ready_connection(self):
        provider = DataForSeoMcpProvider(
            endpoint="https://mcp.example.test/mcp", access_token="ephemeral-token"
        )
        service = TopicPlannerService(
            Path(self.temp.name) / "live",
            keyword_provider=provider,
            serp_provider=provider,
            page_provider=provider,
        )
        created = service.create_brand_profile(
            workspace_id="workspace-a",
            name="Live Acme",
            website="https://live.test",
            product="Planning software",
            target_customer="marketing teams",
            seed_topics=["enterprise AI agents"],
        )
        service.confirm_brand_context(
            workspace_id="workspace-a",
            brand_id=created["brand_profile"]["id"],
            target_market="United States",
            language="en",
            target_customer="marketing teams",
            positioning="Planning software",
            primary_scenario="planning campaigns",
            main_business="planning software",
            core_features=["campaign planning"],
        )
        self.assertEqual(
            service.get_data_provider_status(workspace_id="workspace-a"),
            {
                "provider": "dataforseo_mcp",
                "status": "READY",
                "data_mode": "LIVE",
                "is_live": True,
                "can_connect": False,
                "next_action": "START_RESEARCH",
            },
        )
        estimate = service.estimate_topic_research(
            workspace_id="workspace-a", brand_id=created["brand_profile"]["id"]
        )
        self.assertTrue(estimate["estimate_context"]["is_live_data"])
        self.assertEqual(estimate["estimate_context"]["estimate_source"], "provider_policy_ceiling")
        self.assertIn("conservative approval ceiling", estimate["estimate_context"]["price_note"])

    def test_run_aborts_when_actual_cost_exceeds_budget(self):
        provider = PricedFake(unit_cost=0.5)
        service = TopicPlannerService(
            Path(self.temp.name) / "priced",
            keyword_provider=provider,
            serp_provider=provider,
            page_provider=provider,
        )
        created = service.create_brand_profile(
            workspace_id="workspace-a",
            name="Priced Acme",
            website="https://priced.test",
            product="AI workflow software",
            target_customer="enterprise operations teams",
            seed_topics=["enterprise AI agents"],
        )
        service.confirm_brand_context(
            workspace_id="workspace-a",
            brand_id=created["brand_profile"]["id"],
            target_market="United States",
            language="en",
            target_customer="enterprise operations teams",
            positioning="Priced platform",
            primary_scenario="automating workflows",
            main_business="AI software",
            core_features=["workflow automation"],
        )
        estimate = service.estimate_topic_research(
            workspace_id="workspace-a", brand_id=created["brand_profile"]["id"], budget_cap_usd=1.9
        )
        with self.assertRaises(BudgetError):
            service.run_topic_research(
                workspace_id="workspace-a",
                research_run_id=estimate["research_run"]["id"],
                confirmation_token=estimate["research_estimate"]["confirmation_token"],
            )
        run = service.store.get(
            estimate["research_run"]["id"], workspace_id="workspace-a", object_type="research_run"
        )
        self.assertEqual(run.status, "BUDGET_EXCEEDED")
        self.assertLess(provider.calls, 17)

    def test_mcp_connection_flow_enables_live_and_disconnects(self):
        token_path = Path(self.temp.name) / "oauth.json"

        def transport(url, body, headers):
            if url.endswith("/oauth/clients/register"):
                return 201, {"client_id": "client-offline"}
            if url.endswith("/oauth/token"):
                return 200, {
                    "access_token": "offline-access",
                    "refresh_token": "offline-refresh",
                    "expires_in": 3600,
                }
            return 404, {}

        def opener(url):
            params = dict(parse_qsl(urlparse(url).query))
            with urllib.request.urlopen(
                f"{params['redirect_uri']}?code=offline-code&state={params['state']}", timeout=5
            ) as response:
                response.read()
            return True

        connection = DataForSeoConnection(
            TokenStore(token_path), oauth=DataForSeoOAuth(transport=transport), opener=opener
        )
        service = TopicPlannerService(Path(self.temp.name) / "mcp-conn", connection=connection)
        self.assertEqual(service.get_data_provider_status(workspace_id="w")["status"], "AUTH_REQUIRED")

        begun = handle(
            service,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "connect_dataforseo", "arguments": {"workspace_id": "w"}},
            },
        )
        begun_payload = json.loads(begun["result"]["content"][0]["text"])
        self.assertEqual(begun_payload["status"], "AWAITING_AUTHORIZATION")

        done = handle(
            service,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "complete_dataforseo_connection", "arguments": {"workspace_id": "w"}},
            },
        )
        done_text = done["result"]["content"][0]["text"]
        self.assertEqual(json.loads(done_text)["status"], "READY")
        self.assertNotIn("offline-access", done_text)
        self.assertTrue(service.get_data_provider_status(workspace_id="w")["is_live"])

        off = handle(
            service,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "disconnect_dataforseo", "arguments": {"workspace_id": "w"}},
            },
        )
        self.assertTrue(json.loads(off["result"]["content"][0]["text"])["disconnected"])
        self.assertEqual(service.get_data_provider_status(workspace_id="w")["status"], "AUTH_REQUIRED")

    def test_live_provider_normalizes_market_and_reports_status(self):
        import os
        from unittest import mock

        from seo_topic_planner.providers import _location_name

        self.assertEqual(_location_name("US"), "United States")
        self.assertEqual(_location_name("gb"), "United Kingdom")
        self.assertEqual(_location_name("Germany"), "Germany")
        self.assertEqual(_location_name(""), "")
        with mock.patch.dict(
            os.environ,
            {
                "TOPIC_PLANNER_ENABLE_LIVE_DATAFORSEO": "1",
                "DATAFORSEO_LOGIN": "x",
                "DATAFORSEO_PASSWORD": "y",
            },
        ):
            self.assertEqual(
                DataForSeoProvider(enabled=True).connection_status(),
                {"provider": "dataforseo", "status": "READY"},
            )
        with mock.patch.dict(os.environ, {"TOPIC_PLANNER_ENABLE_LIVE_DATAFORSEO": "0"}):
            self.assertEqual(
                DataForSeoProvider(enabled=True).connection_status(),
                {"provider": "dataforseo", "status": "DISABLED"},
            )

    def test_live_provider_retries_transient_error_but_not_client_error(self):
        import os
        import urllib.error
        from unittest import mock

        body = json.dumps(
            {
                "status_code": 20000,
                "tasks": [{"status_code": 20000, "cost": 0.0, "result": [{"items": [{"keyword": "x"}]}]}],
            }
        ).encode()

        class FakeResponse:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return body

        environment = {
            "TOPIC_PLANNER_ENABLE_LIVE_DATAFORSEO": "1",
            "DATAFORSEO_LOGIN": "x",
            "DATAFORSEO_PASSWORD": "y",
        }
        with mock.patch.dict(os.environ, environment):
            provider = DataForSeoProvider(enabled=True, retries=1, backoff=0.0)
            calls = {"n": 0}

            def transient(request, timeout=None):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise urllib.error.URLError("transient")
                return FakeResponse()

            with mock.patch("urllib.request.urlopen", transient):
                response = provider.expand("seed", market="US", language="en")
            self.assertEqual(calls["n"], 2)
            self.assertEqual(response.payload["keywords"], ["x"])

            def client_error(request, timeout=None):
                calls["n"] += 1
                raise urllib.error.HTTPError("u", 400, "bad", {}, None)

            calls["n"] = 0
            with mock.patch("urllib.request.urlopen", client_error), self.assertRaises(ProviderError):
                provider.expand("seed", market="US", language="en")
            self.assertEqual(calls["n"], 1)

    def test_official_mcp_provider_handles_sse_and_stateless_server(self):
        from unittest import mock

        from seo_topic_planner.providers import DataForSeoMcpProvider

        def sse(payload):
            return ("event: message\ndata: " + json.dumps(payload) + "\n\n").encode()

        class Response:
            status = 200

            def __init__(self, body):
                self._body = body
                self.headers = {}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return self._body

        seen = []

        def fake_urlopen(request, timeout=None):
            payload = json.loads(request.data.decode())
            seen.append((payload.get("method"), (payload.get("params") or {}).get("name")))
            if payload.get("method") == "initialize":
                return Response(
                    sse(
                        {
                            "jsonrpc": "2.0",
                            "id": 1,
                            "result": {"protocolVersion": "2025-06-18", "capabilities": {}},
                        }
                    )
                )
            if payload.get("method") == "notifications/initialized":
                return Response(b"")
            text = json.dumps(
                {
                    "status_code": 20000,
                    "tasks": [
                        {"status_code": 20000, "cost": 0.013, "result": [{"items": [{"keyword": "x"}]}]}
                    ],
                }
            )
            return Response(
                sse({"jsonrpc": "2.0", "id": 2, "result": {"content": [{"type": "text", "text": text}]}})
            )

        provider = DataForSeoMcpProvider(
            endpoint="https://mcp.example.test/mcp", access_token="tok", retries=0
        )
        with mock.patch("urllib.request.urlopen", fake_urlopen):
            response = provider.expand("seed", market="United States", language="en")
        self.assertEqual([m for m, _ in seen], ["initialize", "notifications/initialized", "tools/call"])
        self.assertEqual(seen[-1][1], "api_request")
        self.assertEqual(response.payload["keywords"], ["x"])
        self.assertEqual(response.cost_usd, 0.013)

    def test_tools_expose_security_annotations(self):
        tools = {tool["name"]: tool for tool in TOOLS}
        for tool in TOOLS:
            annotations = tool.get("annotations")
            self.assertIsNotNone(annotations, tool["name"])
            self.assertTrue(annotations.get("title"), tool["name"])
            self.assertIsInstance(annotations["readOnlyHint"], bool)
            self.assertIsInstance(annotations["openWorldHint"], bool)
            self.assertIsInstance(annotations["idempotentHint"], bool)
            if annotations["readOnlyHint"]:
                self.assertNotIn("destructiveHint", annotations, tool["name"])
            else:
                self.assertIsInstance(annotations["destructiveHint"], bool, tool["name"])
        self.assertTrue(tools["run_topic_research"]["annotations"]["openWorldHint"])
        self.assertFalse(tools["run_topic_research"]["annotations"]["readOnlyHint"])
        self.assertTrue(tools["get_data_provider_status"]["annotations"]["readOnlyHint"])
        self.assertFalse(tools["get_data_provider_status"]["annotations"]["openWorldHint"])
        self.assertTrue(tools["connect_dataforseo"]["annotations"]["openWorldHint"])
        self.assertTrue(tools["disconnect_dataforseo"]["annotations"]["destructiveHint"])
        self.assertTrue(tools["estimate_topic_research"]["annotations"]["idempotentHint"] is False)
        self.assertTrue(tools["record_source_observations"]["annotations"]["idempotentHint"])
        self.assertFalse(tools["record_source_observations"]["annotations"]["openWorldHint"])
        self.assertTrue(tools["list_source_observations"]["annotations"]["readOnlyHint"])
        self.assertTrue(tools["record_source_attempts"]["annotations"]["idempotentHint"])
        self.assertFalse(tools["record_source_attempts"]["annotations"]["openWorldHint"])
        self.assertTrue(tools["list_source_attempts"]["annotations"]["readOnlyHint"])
        self.assertTrue(tools["get_brand_profile_by_website"]["annotations"]["readOnlyHint"])
        self.assertFalse(tools["confirm_brand_context"]["annotations"]["readOnlyHint"])
        self.assertFalse(tools["record_site_pages"]["annotations"]["readOnlyHint"])
        self.assertTrue(tools["open_brand_context_card"]["annotations"]["readOnlyHint"])
        self.assertFalse(tools["open_brand_context_card"]["annotations"]["openWorldHint"])
        self.assertEqual(
            tools["open_brand_context_card"]["_meta"]["ui"]["resourceUri"],
            "ui://seo-topic-planner/brand-context",
        )

    def test_schema_contract_and_mcp_surface(self):
        schemas = json_schemas()
        self.assertIn("topic_opportunity.v1", schemas)
        self.assertIn("topic_cluster.v1", schemas)
        self.assertIn("workspace_id", schemas["outline.v1"]["required"])
        self.assertEqual(
            {
                "create_brand_profile",
                "estimate_topic_research",
                "run_topic_research",
                "create_content_cluster",
                "get_data_provider_status",
                "open_connection_settings",
            },
            {row["name"] for row in TOOLS}
            & {
                "create_brand_profile",
                "estimate_topic_research",
                "run_topic_research",
                "create_content_cluster",
                "get_data_provider_status",
                "open_connection_settings",
            },
        )
        response = handle(self.service, {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
        self.assertIn("create_brand_profile", {tool["name"] for tool in response["result"]["tools"]})
        created = handle(
            self.service,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "create_brand_profile",
                    "arguments": {
                        "workspace_id": "mcp-workspace",
                        "name": "MCP Acme",
                        "website": "https://mcp.test",
                        "product": "Planning software",
                        "target_customer": "marketing teams",
                    },
                },
            },
        )
        payload = json.loads(created["result"]["content"][0]["text"])
        self.assertEqual(payload["brand_profile"]["workspace_id"], "mcp-workspace")
        resources = handle(
            self.service, {"jsonrpc": "2.0", "id": 3, "method": "resources/list", "params": {}}
        )
        self.assertEqual(
            resources["result"]["resources"][0]["uri"], "ui://seo-topic-planner/connection-settings"
        )
        app = handle(
            self.service,
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "resources/read",
                "params": {"uri": "ui://seo-topic-planner/connection-settings"},
            },
        )
        self.assertIn("DataForSEO connection", app["result"]["contents"][0]["text"])
        self.assertIn("official DataForSEO MCP connection", app["result"]["contents"][0]["text"])
        opened = handle(
            self.service,
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {
                    "name": "open_connection_settings",
                    "arguments": {},
                },
            },
        )
        self.assertIn('"status": "READY"', opened["result"]["content"][0]["text"])

    def test_brand_context_card_resource_and_tool(self):
        listed = handle(
            self.service, {"jsonrpc": "2.0", "id": 10, "method": "resources/list", "params": {}}
        )
        uris = [item["uri"] for item in listed["result"]["resources"]]
        self.assertIn("ui://seo-topic-planner/brand-context", uris)
        self.assertEqual(
            listed["result"]["resources"][0]["uri"], "ui://seo-topic-planner/connection-settings"
        )
        resource = handle(
            self.service,
            {
                "jsonrpc": "2.0",
                "id": 11,
                "method": "resources/read",
                "params": {"uri": "ui://seo-topic-planner/brand-context"},
            },
        )
        self.assertIn("Confirm brand context", resource["result"]["contents"][0]["text"])
        card = handle(
            self.service,
            {
                "jsonrpc": "2.0",
                "id": 12,
                "method": "tools/call",
                "params": {
                    "name": "open_brand_context_card",
                    "arguments": {
                        "workspace_id": "workspace-a",
                        "website": "https://fresh.test",
                        "suggestions": {"core_features": ["automation"], "name": "Fresh"},
                    },
                },
            },
        )
        payload = card["result"]["structuredContent"]
        self.assertFalse(payload["found"])
        self.assertEqual(payload["mode"], "first_run")
        self.assertEqual(payload["suggestions"]["core_features"], ["automation"])
        self.assertTrue(payload["market_options"])
        self.assertTrue(payload["language_options"])
        self.assertEqual(payload["suggestions"]["target_market"], "")


if __name__ == "__main__":
    unittest.main()
