"""Unit tests for the pure helpers: domains, extraction, ledger, clustering, lint."""

from __future__ import annotations

import tempfile
import unittest
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from seo_topic_planner import dataforseo
from seo_topic_planner.analysis import lint_sections
from seo_topic_planner.clustering import cluster_volume, one_per_variant, serp_clusters
from seo_topic_planner.domains import normalize_url, registrable_domain, same_site
from seo_topic_planner.errors import BudgetError, StateError
from seo_topic_planner.fetch import extract, quote_in, sitemap_urls
from seo_topic_planner.metrics import labs_keyword_metrics
from seo_topic_planner.store import Store, iso, utcnow


class DomainTest(unittest.TestCase):
    def test_registrable_domain_uses_public_suffix_list(self) -> None:
        self.assertEqual(registrable_domain("https://blog.example.co.uk/x"), "example.co.uk")
        self.assertEqual(registrable_domain("alice.github.io"), "alice.github.io")
        self.assertFalse(same_site("https://bob.github.io", "alice.github.io"))
        self.assertTrue(same_site("https://docs.acme.com/a", "acme.com"))
        self.assertEqual(registrable_domain("github.io"), "")
        self.assertEqual(registrable_domain("http://127.0.0.1"), "")

    def test_normalize_url(self) -> None:
        self.assertEqual(normalize_url("http://www.Acme.com/Blog/#top"), "https://acme.com/Blog")
        self.assertEqual(normalize_url("https://acme.com"), "https://acme.com/")
        self.assertEqual(normalize_url("ftp://acme.com"), "")


class ExtractTest(unittest.TestCase):
    HTML = """<html><head><title>T</title><meta property="article:modified_time" content="2023-05-01">
    </head><body><nav><h2>Menu</h2>Products Pricing</nav>
    <main><h1>Guide</h1><p>Intro text here.</p><h2>Step one</h2><p>Do the first thing &amp; check it.</p>
    <script>var x = 1;</script><h3>Detail</h3><ul><li>Item A</li><li>Item B</li></ul></main>
    <footer>Footer junk</footer></body></html>"""

    def test_main_content_headings_and_sections(self) -> None:
        page = extract(self.HTML)
        self.assertEqual(page.h1, "Guide")
        self.assertEqual([h["text"] for h in page.headings], ["Guide", "Step one", "Detail"])
        self.assertIn("Do the first thing & check it.", page.text)
        self.assertNotIn("Footer junk", page.text)
        self.assertNotIn("Menu", page.text)
        self.assertNotIn("var x", page.text)
        self.assertEqual(page.modified_at, "2023-05-01")
        self.assertEqual(page.sections[-1]["heading"], "Detail")

    def test_quote_matching_is_whitespace_and_punctuation_tolerant(self) -> None:
        text = extract(self.HTML).text
        self.assertTrue(quote_in("do the first  thing — check it", text))
        self.assertFalse(quote_in("do the second thing", text))
        self.assertFalse(quote_in("Guide", text))  # too short to prove anything

    def test_sitemap_index(self) -> None:
        pages, children = sitemap_urls(
            "<sitemapindex><sitemap><loc>https://a.com/s1.xml</loc></sitemap></sitemapindex>"
        )
        self.assertEqual((pages, children), ([], ["https://a.com/s1.xml"]))


class MetricsTest(unittest.TestCase):
    def test_missing_values_stay_none_and_trend(self) -> None:
        monthly = [{"year": 2025, "month": m, "search_volume": 200} for m in range(1, 13)]
        monthly += [{"year": 2024, "month": m, "search_volume": 100} for m in range(1, 13)]
        row = labs_keyword_metrics({"keyword": "x", "keyword_info": {"monthly_searches": monthly}})
        self.assertIsNone(row["volume"])
        self.assertIsNone(row["kd"])
        self.assertEqual(row["trend_yoy"], 1.0)

    def test_serp_snapshot_normalizes_features(self) -> None:
        snapshot = dataforseo.serp_snapshot(
            "q",
            [
                {
                    "items": [
                        {"type": "organic", "rank_group": 1, "url": "https://www.a.com/x", "title": "A"},
                        {"type": "people_also_ask", "items": [{"title": "Why?"}]},
                        {"type": "ai_overview", "items": [{"references": [{"url": "https://b.com"}]}]},
                    ]
                }
            ],
        )
        self.assertEqual(snapshot["organic"][0]["domain"], "a.com")
        self.assertEqual(snapshot["paa"], ["Why?"])
        self.assertEqual(snapshot["ai_overview_refs"], ["https://b.com"])


class LedgerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def reserve(self, request_hash: str, cap: str = "0.1") -> str:
        return self.store.reserve(
            project_id="p",
            operation="serp",
            request_hash=request_hash,
            ceiling=Decimal("0.04"),
            cap=Decimal(cap),
        )

    def test_reservations_count_against_cap_until_settled(self) -> None:
        first = self.reserve("a")
        self.reserve("b")
        with self.assertRaises(BudgetError):
            self.reserve("c")
        self.store.settle(first, Decimal("0.01"))
        self.reserve("c")
        self.assertEqual(self.store.committed_usd("p"), Decimal("0.09"))

    def test_unknown_cost_blocks_identical_request(self) -> None:
        request = self.reserve("a")
        self.store.fail(request, cost_uncertain=True, error="timeout")
        with self.assertRaises(StateError):
            self.reserve("a")
        failed = self.reserve("b")
        self.store.fail(failed, cost_uncertain=False, error="400")
        self.reserve("b")  # a rejected request was not billed and may be retried

    def test_stale_reservation_becomes_unknown_cost(self) -> None:
        request = self.reserve("a")
        with self.store._connect() as connection:
            connection.execute(
                "UPDATE requests SET created_at=? WHERE id=?", (iso(utcnow() - timedelta(hours=1)), request)
            )
        self.assertEqual(self.store.requests("p")[0]["status"], "UNKNOWN_COST")

    def test_cache_expiry(self) -> None:
        self.store.cache_put(
            request_hash="h", operation="serp", payload={"x": 1}, cost=Decimal("0"), ttl_days=0
        )
        self.assertIsNone(self.store.cache_get("h"))
        self.store.cache_put(
            request_hash="h", operation="serp", payload={"x": 1}, cost=Decimal("0"), ttl_days=7
        )
        self.assertEqual(self.store.cache_get("h")["payload"], {"x": 1})


class ClusteringTest(unittest.TestCase):
    def test_threshold_and_volume_order(self) -> None:
        a = [f"u{i}" for i in range(10)]
        clusters = serp_clusters(
            [
                {"keyword": "small", "volume": 10, "urls": a[:3] + ["x", "y"]},
                {"keyword": "big", "volume": 1000, "urls": a},
                {"keyword": "near", "volume": 50, "urls": a[:2] + ["z"]},
                {"keyword": "no serp", "volume": 5, "urls": []},
            ]
        )
        self.assertEqual([c["primary"] for c in clusters], ["big", "near", "no serp"])
        self.assertEqual([m["keyword"] for m in clusters[0]["members"]], ["big", "small"])
        self.assertFalse(clusters[2]["has_serp"])


class VariantTest(unittest.TestCase):
    def test_spelling_variants_share_a_cluster_even_with_different_serps(self) -> None:
        clusters = serp_clusters([
            {"keyword": "no-code ai agent builder", "volume": 3600, "urls": ["a", "b", "c"]},
            {"keyword": "no code ai agent builders", "volume": 300, "urls": ["x", "y", "z"]},
            {"keyword": "ai agent platform", "volume": 2400, "urls": ["p", "q", "r"]},
        ])
        self.assertEqual(len(clusters), 2)
        self.assertEqual(len(clusters[0]["members"]), 2)

    def test_variant_volume_is_counted_once(self) -> None:
        # DataForSEO repeats one volume for every close variant of a query.
        members = [
            {"keyword": "human in the loop ai", "volume": 1000},
            {"keyword": "human-in-the-loop ai", "volume": 1000},
            {"keyword": "human-in the loop ai", "volume": 1000},
            {"keyword": "ai human in the loop", "volume": 140},
            {"keyword": "hitl ai", "volume": 590},
        ]
        self.assertEqual(cluster_volume(members), 1590)

    def test_serp_is_fetched_once_per_variant_group(self) -> None:
        ordered = ["human in the loop ai", "hitl ai", "human-in-the-loop ai", "ai human in the loop"]
        self.assertEqual(one_per_variant(ordered), ["human in the loop ai", "hitl ai"])


class ProofStatusTest(unittest.TestCase):
    def test_data_gain_with_pending_tasks_needs_client_input(self) -> None:
        from seo_topic_planner.analysis import assess_gap

        site = {"pg_site": {"id": "pg_site", "url": "https://a.com/p", "text": "We meter every agent run in credits."}}
        gap = {
            "id": "g", "question_ids": ["q"], "gap_type": "WEAK_EVIDENCE", "gain_type": "original_data",
            "statement": "No page publishes cost per task for agents.", "why_it_matters": "Buyers cannot budget without it.",
            "counter_argument": "Costs vary widely between workflows.", "brand_contribution": "The client meters runs and can publish ranges.",
            "brand_proof": [{"page_id": "pg_site", "quote": "We meter every agent run in credits."}],
            "collection_tasks": [{"ask": "Export cost per task", "from": "billing", "deliverable": "table"}],
        }
        questions = {"q": {"id": "q", "question": "?", "search_observed": False}}
        checked, _ = assess_gap(gap, questions=questions, coverage=[], external={}, site_pages=site)
        self.assertEqual(checked["proof_status"], "NEEDS_CLIENT_INPUT")
        gap["gain_type"] = "technical_depth"
        checked, _ = assess_gap(gap, questions=questions, coverage=[], external={}, site_pages=site)
        self.assertEqual(checked["proof_status"], "VERIFIED")


class LintTest(unittest.TestCase):
    def test_flags_template_outlines(self) -> None:
        sections = [
            {"heading": "Introduction", "kind": "BASELINE", "points": ["Set the scene"]},
            {
                "heading": "What is agent deployment",
                "kind": "GAIN",
                "points": ["Unlock the power of seamless agents"],
            },
            {
                "heading": "Conclusion",
                "kind": "BASELINE",
                "points": ["Wrap up the post with a summary of points"],
            },
        ]
        warnings = "\n".join(lint_sections(sections))
        self.assertIn("GAIN section needs a specific heading", warnings)
        self.assertIn("filler phrasing", warnings)
        self.assertIn("generic", warnings)
        self.assertIn("too vague", warnings)


if __name__ == "__main__":
    unittest.main()
