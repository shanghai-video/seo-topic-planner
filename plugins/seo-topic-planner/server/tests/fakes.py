"""Offline stand-ins for DataForSEO and the web.

The fake world has a client (``acme-agents.com``) and two search intents with
distinct SERPs, so clustering, page capture and quote checks run on realistic
shapes without any network access.
"""

from __future__ import annotations

from typing import Any

from seo_topic_planner.fetch import FetchError

CLIENT = "https://acme-agents.com"

DEPLOY = ["ai agent deployment", "deploy ai agents", "ai agent deployment checklist"]
GOVERN = ["ai agent governance", "agent governance framework"]
JUNK = ["meta ai", "ai character generator"]
# Seeds DataForSEO knows nothing about: no seed metrics and no related keywords.
NO_DATA = ["approval fatigue"]
VOLUMES = {
    "ai agent deployment": 880,
    "deploy ai agents": 320,
    "ai agent deployment checklist": 90,
    "ai agent governance": 590,
    "agent governance framework": 170,
    "meta ai": 450000,
    "ai character generator": 22000,
    "ai agents": 40500,
}


def _metrics(keyword: str) -> dict[str, Any]:
    return {
        "keyword": keyword,
        "keyword_info": {"search_volume": VOLUMES.get(keyword), "cpc": 4.2, "competition_index": 60},
        "keyword_properties": {"keyword_difficulty": 35 if keyword in VOLUMES else None},
        "search_intent_info": {"main_intent": "informational"},
    }


def _envelope(result: list[Any], cost: float = 0.02) -> dict[str, Any]:
    return {"status_code": 20000, "tasks": [{"status_code": 20000, "cost": cost, "result": result}]}


def deploy_urls() -> list[str]:
    return [f"https://site{i}.example.org/agent-deployment" for i in range(1, 11)]


def govern_urls() -> list[str]:
    return [f"https://gov{i}.example.net/governance" for i in range(1, 11)]


class FakeTransport:
    name = "fake"

    def __init__(self, *, cost: float = 0.02, fail_ops: dict[str, Exception] | None = None) -> None:
        self.cost = cost
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail_ops = fail_ops or {}
        self.ready = True

    def status(self) -> str:
        return "READY" if self.ready else "AUTH_REQUIRED"

    def post(self, path: str, task: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((path, task))
        for fragment, error in self.fail_ops.items():
            if fragment in path:
                raise error
        if "keyword_suggestions" in path or "related_keywords" in path:
            seed = task["keyword"]
            if seed in NO_DATA:
                return _envelope([{"seed_keyword": seed, "seed_keyword_data": None, "items": []}], self.cost)
            family = DEPLOY + GOVERN if "agent" in seed else []
            items = [_metrics(k) for k in family + JUNK]
            if "related_keywords" in path:
                items = [{"keyword_data": item} for item in items]
            return _envelope(
                [{"seed_keyword": seed, "seed_keyword_data": _metrics(seed), "items": items}], self.cost
            )
        if "ranked_keywords" in path:
            target = task["target"]
            rank = 14 if "acme" in target else 3
            return _envelope(
                [
                    {
                        "items": [
                            {
                                "keyword_data": _metrics("ai agent governance"),
                                "ranked_serp_element": {
                                    "serp_item": {"rank_group": rank, "url": f"https://{target}/governance"}
                                },
                            }
                        ]
                    }
                ],
                self.cost,
            )
        if "/serp/" in path:
            keyword = task["keyword"]
            urls = deploy_urls() if keyword in DEPLOY else govern_urls() if keyword in GOVERN else []
            items: list[dict[str, Any]] = [
                {"type": "organic", "rank_group": i + 1, "url": url, "title": f"Result {i + 1} for {keyword}"}
                for i, url in enumerate(urls)
            ]
            if keyword in GOVERN:
                items.insert(
                    3,
                    {
                        "type": "organic",
                        "rank_group": 4,
                        "url": f"{CLIENT}/governance",
                        "title": "Acme governance",
                    },
                )
            items.append(
                {
                    "type": "people_also_ask",
                    "items": [
                        {"title": "How do you roll back a failed AI agent deployment?"},
                        {"title": "What approvals does an AI agent need in production?"},
                    ],
                }
            )
            items.append({"type": "related_searches", "items": ["ai agent deployment tools"]})
            if keyword in GOVERN:
                items.insert(0, {"type": "ai_overview", "items": []})
            return _envelope([{"items": items}], 0.002)
        raise AssertionError(f"unexpected path {path}")


def _html(title: str, headings: list[tuple[str, str]], extra: str = "") -> str:
    body = "".join(f"<h2>{h}</h2><p>{p}</p>" for h, p in headings)
    filler = " ".join(["Teams evaluate agents against their workflow constraints and controls."] * 30)
    return (
        f"<html><head><title>{title}</title>"
        '<meta name="description" content="desc">'
        '<script type="application/ld+json">{"@type":"Article","dateModified":"2024-03-01"}</script>'
        f"</head><body><nav>Home Pricing Login</nav><main><h1>{title}</h1>{body}<p>{filler}</p>{extra}</main>"
        "<footer>Copyright footer links</footer></body></html>"
    )


def fake_web() -> dict[str, str]:
    pages = {
        f"{CLIENT}/robots.txt": "User-agent: *\nSitemap: https://acme-agents.com/sitemap.xml",
        f"{CLIENT}/sitemap.xml": (
            "<urlset><url><loc>https://acme-agents.com/</loc></url>"
            "<url><loc>https://acme-agents.com/product/agent-runtime</loc></url>"
            "<url><loc>https://acme-agents.com/blog/ai-agent-deployment-guide</loc></url>"
            "<url><loc>https://acme-agents.com/governance</loc></url>"
            "<url><loc>https://acme-agents.com/de/blog/ai-agent-deployment-guide</loc></url>"
            "<url><loc>https://acme-agents.com/agents/catalog</loc></url>"
            "<url><loc>https://acme-agents.com/agents/pricing</loc></url>"
            "<url><loc>https://acme-agents.com/agents/teams</loc></url>"
            "<url><loc>https://acme-agents.com/agents/support</loc></url></urlset>"
        ),
        f"{CLIENT}/": _html(
            "Acme — run AI agents in production",
            [
                (
                    "Agent runtime",
                    "Acme Runtime executes agents with step-level approvals and full audit logs.",
                ),
            ],
        ),
        f"{CLIENT}/product/agent-runtime": _html(
            "Agent Runtime",
            [
                (
                    "Rollback",
                    "Every deployment keeps a versioned snapshot so operators can roll back an agent in one click.",
                ),
                (
                    "Approvals",
                    "Human approval gates can be required for any tool call that writes to a system of record.",
                ),
            ],
        ),
        f"{CLIENT}/blog/ai-agent-deployment-guide": _html(
            "Our old deployment guide",
            [
                ("Steps", "Install the runtime, connect your tools and invite reviewers."),
            ],
        ),
        f"{CLIENT}/governance": _html(
            "Governance at Acme",
            [
                ("Policies", "Policies define which agent actions need sign-off."),
            ],
        ),
        "https://forum.example.com/thread/1": _html(
            "Rollback horror stories",
            [
                (
                    "Thread",
                    "We had no way to roll back the agent after a bad prompt change and it kept emailing customers.",
                ),
            ],
        ),
    }
    for index, url in enumerate(deploy_urls(), 1):
        sections = [
            (
                "What is AI agent deployment",
                "Deployment means moving an agent from a notebook into production systems.",
            ),
            ("Deployment steps", "Package the agent, connect tools, add monitoring and ship it to users."),
        ]
        if index <= 2:
            sections.append(
                ("Approvals", "Production agents should require human approval for sensitive actions.")
            )
        pages[url] = _html(f"Deploying AI agents guide {index}", sections)
    for index, url in enumerate(govern_urls(), 1):
        pages[url] = _html(
            f"Agent governance {index}",
            [
                ("Governance", "An agent registry, audit logs and policy controls keep agents governed."),
            ],
        )
    return pages


class FakeFetcher:
    def __init__(self, pages: dict[str, str] | None = None) -> None:
        self.pages = pages if pages is not None else fake_web()
        self.requests: list[str] = []

    def __call__(self, url: str) -> tuple[str, str, str]:
        self.requests.append(url)
        key = url.rstrip("/") if not url.endswith(".com/") else url
        for candidate in (url, key, url.replace("https://www.", "https://")):
            if candidate in self.pages:
                content_type = (
                    "text/xml"
                    if candidate.endswith(".xml")
                    else "text/plain"
                    if candidate.endswith(".txt")
                    else "text/html; charset=utf-8"
                )
                return candidate, content_type, self.pages[candidate]
        if url.rstrip("/") + "/" in self.pages:
            return url, "text/html", self.pages[url.rstrip("/") + "/"]
        raise FetchError("HTTP 404")
