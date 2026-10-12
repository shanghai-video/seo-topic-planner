"""Client report rendering (Markdown, English or Chinese labels).

The report reads like an analyst's memo: each topic leads with the decision,
then the data behind it, what already ranks, the information gain with its
evidence, the outline, and what the client must supply.  Evidence labels are
plain words (verified quote, agent-read page, unverified) instead of internal
ids, and every unknown is said out loud.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from .analysis import pending_sections, topic_status
from .domains import normalize_url
from .domains import page_id as _page_id
from .hubs import ROLES, hub_link_warnings, topic_volume

LABELS = {
    "en": {
        "title": "SEO Topic Research",
        "meta": "Market: {market} · Search language: {language} · Generated: {date} · Data: DataForSEO (live/cached) + pages read by the planner",
        "summary": "1. Summary",
        "summary_intro": "{n} topics researched. Each row is one page decision.",
        "cols": "| Topic | Decision | Status | Primary keyword | Cluster volume | KD | Strongest information gain |",
        "business": "2. Business & audience",
        "verified": "verified on site",
        "claimed": "claimed, not found on a captured page",
        "keywords": "3. Keyword research",
        "opportunities": "4. Opportunity landscape",
        "opp_cols": "| Cluster (primary query) | Volume | KD | SERP intent | AI Overview | Keywords | Client ranks | Selected |",
        "hubs": "Topic hubs",
        "hub_intro": "Hubs: {n}. Each hub is one pillar page for the broadest query plus the pages that go deeper; pages link to each other inside the hub.",
        "hub_cols": "| # | Hub | Priority | Job / ICP | Reachable monthly volume | Funnel | Pages | Why |",
        "upper_note": "\\* Includes synonyms Google answers with separate results; one page ranking for all of them is not certain, so treat the total as an upper bound.",
        "no_search_note": "No-search pages have no measurable search volume (readers come from sales, links, social or AI answers); they are excluded from every volume total.",
        "page_map": "Page map",
        "map_cols": "| Role | Topic | Target keyword (volume / KD) | SERP intent · page type | Demand | AI Overview | Status |",
        "links": "Internal links",
        "link_cols": "| From | To | Anchor promise | Section |",
        "hub_checks": "Open structure checks",
        "standalone": "Standalone topics",
        "no_search": "No measurable search volume — not counted in any total.",
        "page_role": "Role in hub",
        "depth": {
            "FULL": "full gap analysis",
            "LIGHT": "light gap analysis",
            "EVIDENCE": "evidence check (no SERP of its own)",
        },
        "demand_status": {
            "VERIFIED": "demand verified",
            "NEEDS_CLIENT_INPUT": "demand waits for client material",
            "MISSING": "no demand evidence",
        },
        "roles": {
            "PILLAR": "Pillar",
            "SUPPORT": "Support",
            "COMMERCIAL": "Commercial",
            "NO_SEARCH": "No-search page",
        },
        "funnel": {"AWARENESS": "awareness", "CONSIDERATION": "consideration", "DECISION": "decision"},
        "intents": {
            "INFORMATIONAL": "informational",
            "COMMERCIAL": "commercial investigation",
            "TRANSACTIONAL": "transactional",
            "MIXED": "mixed",
        },
        "sources": {"SEARCH": "search", "PAA": "People Also Ask", "EVIDENCE": "evidence"},
        "formats": {
            "TEXT": "text page",
            "HYBRID": "content page with an embedded tool",
            "TOOL": "standalone tool page",
        },
        "supplements": "Seed supplements after the main research",
        "no_data": "Seeds DataForSEO has no data for (unmeasured, not zero demand)",
        "topic": "Topic",
        "decision": "Decision",
        "demand": "Search demand",
        "ranking": "What ranks today",
        "baseline": "What competitors already cover (table stakes)",
        "gains": "Information gain",
        "site": "Existing site coverage",
        "outline": "Page outline",
        "next": "Next actions",
        "method": "Method, coverage and limits",
        "cost": "Data cost",
        "short": {
            "READY_TO_WRITE": "Ready to write",
            "NEEDS_CLIENT_INPUT": "Needs client material",
            "NO_CLEAR_GAIN": "No proven gain",
            "NOT_RECOMMENDED": "Skip",
            "NEEDS_PAGE_PLAN": "Plan missing",
            "NOT_ANALYZED": "Not analyzed",
        },
        "order": "Recommended order",
        "order_line": "{i}. **{name}** — {action}, {short}; {pending} section(s) waiting for client material, {tasks} client task(s)",
        "thin_serp": "No reader question is answered in depth by two or more audited pages — the ranking content is thin.",
        "status": {
            "READY_TO_WRITE": "Ready to write — at least one information gain is proven by the client's own pages",
            "NEEDS_CLIENT_INPUT": "Worth doing once the client supplies the listed material — no gain is proven yet",
            "NO_CLEAR_GAIN": "No proven information gain yet — do not produce a me-too page",
            "NOT_RECOMMENDED": "Not recommended",
            "NEEDS_PAGE_PLAN": "Analysis done, page plan missing",
            "NOT_ANALYZED": "Not analyzed",
        },
    },
    "zh": {
        "title": "SEO 选题研究报告",
        "meta": "市场：{market} · 搜索语言：{language} · 生成时间：{date} · 数据：DataForSEO（实时/缓存）+ 系统实际抓取的页面正文",
        "summary": "1. 结论摘要",
        "summary_intro": "共研究 {n} 个选题，每行对应一个页面决策。",
        "cols": "| 选题 | 页面动作 | 状态 | 主关键词 | 簇总搜索量 | KD | 最强信息增量 |",
        "business": "2. 业务与目标用户",
        "verified": "已在官网找到原文",
        "claimed": "未在已抓取页面找到原文",
        "keywords": "3. 关键词研究",
        "opportunities": "4. 机会全景",
        "opp_cols": "| 关键词簇（主查询） | 搜索量 | KD | SERP 意图 | AI 概览 | 关键词数 | 客户已有排名 | 是否入选 |",
        "hubs": "主题集群",
        "hub_intro": "共 {n} 个集群。每个集群由一篇覆盖最宽查询的 pillar 页和若干深入页组成，集群内页面互相链接。",
        "hub_cols": "| # | 集群 | 优先级 | 任务 / ICP | 可争取的月搜索量 | 漏斗覆盖 | 页面数 | 理由 |",
        "upper_note": "\\* 含多个在 Google 里各自独立 SERP 的同义词，一篇页面未必能同时排上，合计值应视为上限。",
        "no_search_note": "无搜索量页面没有可测的搜索需求（读者来自销售分发、站内链接、社媒或 AI 搜索引用），不计入任何搜索量合计。",
        "page_map": "页面地图",
        "map_cols": "| 角色 | 选题 | 目标词（搜索量 / KD） | SERP 意图 · 页面类型 | 需求来源 | AI 概览 | 状态 |",
        "links": "内链",
        "link_cols": "| 来源页 | 目标 | 锚文本意图 | 位置 |",
        "hub_checks": "待处理的结构检查",
        "standalone": "独立选题",
        "no_search": "没有可测搜索量——不计入任何合计。",
        "page_role": "集群角色",
        "depth": {
            "FULL": "完整信息增量分析",
            "LIGHT": "轻量信息增量分析",
            "EVIDENCE": "证据验证（没有自己的 SERP）",
        },
        "demand_status": {
            "VERIFIED": "需求已核验",
            "NEEDS_CLIENT_INPUT": "需求待客户材料证明",
            "MISSING": "无需求证据",
        },
        "roles": {
            "PILLAR": "Pillar",
            "SUPPORT": "Support",
            "COMMERCIAL": "商业页",
            "NO_SEARCH": "无搜索量页面",
        },
        "funnel": {"AWARENESS": "认知", "CONSIDERATION": "评估", "DECISION": "决策"},
        "intents": {
            "INFORMATIONAL": "信息型",
            "COMMERCIAL": "商业调研型",
            "TRANSACTIONAL": "交易型",
            "MIXED": "混合",
        },
        "sources": {"SEARCH": "搜索", "PAA": "PAA", "EVIDENCE": "证据"},
        "formats": {"TEXT": "文字页", "HYBRID": "信息页 + 嵌入工具", "TOOL": "独立工具页"},
        "supplements": "研究结束后补充的种子词",
        "no_data": "DataForSEO 没有数据的种子词（无法测量，不等于零需求）",
        "topic": "选题",
        "decision": "结论",
        "demand": "搜索需求",
        "ranking": "当前排名内容",
        "baseline": "竞品已充分覆盖的内容（基础项）",
        "gains": "信息增量",
        "site": "站内现有内容",
        "outline": "页面提纲",
        "next": "下一步",
        "method": "方法、覆盖范围与局限",
        "cost": "数据费用",
        "short": {
            "READY_TO_WRITE": "可直接撰写",
            "NEEDS_CLIENT_INPUT": "需客户材料",
            "NO_CLEAR_GAIN": "无已证实增量",
            "NOT_RECOMMENDED": "不建议",
            "NEEDS_PAGE_PLAN": "缺页面规划",
            "NOT_ANALYZED": "未分析",
        },
        "order": "建议执行顺序",
        "order_line": "{i}. **{name}** —— {action}，{short}；{pending} 个章节待客户材料，{tasks} 项客户任务",
        "thin_serp": "没有任何读者问题被两个以上已审计页面深入回答——当前排名内容整体偏薄。",
        "status": {
            "READY_TO_WRITE": "可直接撰写——至少一项信息增量已由客户自有页面证实",
            "NEEDS_CLIENT_INPUT": "值得做，但尚无已证实的信息增量，需客户先提供下列材料",
            "NO_CLEAR_GAIN": "尚无可证实的信息增量——不要写同质化内容",
            "NOT_RECOMMENDED": "不建议投入",
            "NEEDS_PAGE_PLAN": "已完成分析，缺页面规划",
            "NOT_ANALYZED": "未分析",
        },
    },
}

STRENGTH_ORDER = {"STRONG": 0, "MODERATE": 1, "WEAK": 2, "REFUTED": 3}


def _fmt(value: Any) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def _clip(text: str, limit: int) -> str:
    """Shorten at a word boundary so tables never show half words."""

    text = str(text or "")
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(",;:—-") + "…"


def _cell(text: Any) -> str:
    return _fmt(text).replace("|", "/").replace("\n", " ")


ROLE_ORDER = {role: index for index, role in enumerate(ROLES)}
PRIORITY = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


def _volume(topic: dict[str, Any], clusters: dict[str, dict[str, Any]]) -> str:
    """A topic's volume as the report shows it: none for no-search pages, * for upper bounds."""

    if topic.get("role") == "NO_SEARCH":
        return "—"
    volume = topic_volume(topic, clusters)
    return f"{volume:,}\\*" if topic.get("volume_upper_bound") else f"{volume:,}"


def _ordered_hubs(bundle: dict[str, Any]) -> list[tuple[dict[str, Any] | None, list[dict[str, Any]]]]:
    """Topics grouped by hub (by hub priority, pillar first); standalone topics last."""

    hubs = sorted(bundle.get("hubs") or [], key=lambda hub: (PRIORITY.get(hub.get("priority"), 1), hub["id"]))
    groups: list[tuple[dict[str, Any] | None, list[dict[str, Any]]]] = []
    placed: set[str] = set()
    for hub in hubs:
        members = [topic for topic in bundle["topics"] if topic.get("hub_id") == hub["id"]]
        members.sort(
            key=lambda topic: (ROLE_ORDER.get(topic.get("role"), 9), PRIORITY.get(topic.get("priority"), 1))
        )
        placed.update(topic["id"] for topic in members)
        if members:
            groups.append((hub, members))
    rest = [topic for topic in bundle["topics"] if topic["id"] not in placed]
    if rest:
        groups.append((None, rest))
    return groups


def render_report(bundle: dict[str, Any]) -> str:
    project = bundle["project"]
    lang = project.get("report_language") if project.get("report_language") in LABELS else "en"
    t = LABELS[lang]
    keywords = {item["keyword"]: item for item in bundle["keywords"]}
    pages = bundle["pages"]
    topics = bundle["topics"]
    clusters = {item["id"]: item for item in bundle["clusters"]}
    groups = _ordered_hubs(bundle)
    hubbed = [(hub, members) for hub, members in groups if hub]
    out: list[str] = []
    add = out.append
    dates = sorted(s.get("captured_at", "") for s in bundle["serps"].values() if s.get("captured_at"))
    add(f"# {t['title']} — {project['website']}")
    add("")
    add(
        t["meta"].format(
            market=project["market"],
            language=project["language"],
            date=(dates[-1][:10] if dates else project.get("updated_at", "")[:10]),
        )
    )
    add("")
    add(f"> {project['business_goal']}")
    add("")
    number = 0

    def section(title: str) -> None:
        nonlocal number
        number += 1
        add(f"## {number}. {title}")
        add("")

    # summary
    section(t["summary"].split(". ", 1)[-1])
    add(t["summary_intro"].format(n=len(topics)))
    add("")
    add(t["cols"])
    add("|---|---|---|---|---|---|---|")
    ordered_by_group = [topic for _, members in groups for topic in members]
    for topic in ordered_by_group:
        analysis = bundle["gap_analyses"].get(topic["id"])
        plan = bundle["page_plans"].get(topic["id"])
        status = topic_status(analysis, plan)
        best = _best_gain(analysis, plan)
        primary = keywords.get(topic["primary_keyword"], {})
        add(
            f"| {_cell(topic['name'])} | {_cell(plan['action'] if plan else '—')} | {_cell(t['short'][status])} "
            f"| {_cell(topic['primary_keyword'])} | {_volume(topic, clusters)} | {_cell(primary.get('kd'))} "
            f"| {_cell(_clip(best['statement'], 140) + ' [' + best['strength'] + ']' if best else '—')} |"
        )
    add("")
    if any(topic.get("volume_upper_bound") for topic in topics):
        add(t["upper_note"])
        add("")
    rank = {
        "READY_TO_WRITE": 0,
        "NEEDS_CLIENT_INPUT": 1,
        "NO_CLEAR_GAIN": 2,
        "NEEDS_PAGE_PLAN": 3,
        "NOT_ANALYZED": 4,
        "NOT_RECOMMENDED": 5,
    }
    ordered_topics = sorted(
        topics,
        key=lambda tp: (
            rank[topic_status(bundle["gap_analyses"].get(tp["id"]), bundle["page_plans"].get(tp["id"]))],
            PRIORITY.get(tp.get("priority"), 1),
        ),
    )
    add(f"**{t['order']}**")
    add("")
    for index, topic in enumerate(ordered_topics, 1):
        analysis = bundle["gap_analyses"].get(topic["id"])
        plan = bundle["page_plans"].get(topic["id"])
        status = topic_status(analysis, plan)
        tasks = sum(
            len(gap["collection_tasks"])
            for gap in (analysis or {}).get("gaps", [])
            if gap["strength"] in {"STRONG", "MODERATE"}
        )
        add(
            t["order_line"].format(
                i=index,
                name=topic["name"],
                action=plan["action"] if plan else "—",
                short=t["short"][status],
                pending=len(pending_sections(analysis, plan)),
                tasks=tasks,
            )
        )
    add("")

    # hubs
    if hubbed:
        section(t["hubs"])
        _render_hubs(out, hubbed, bundle, keywords, clusters, t)

    # business
    brief = project.get("brief") or {}
    section(t["business"].split(". ", 1)[-1])
    add(brief.get("product_summary", "—"))
    add("")
    for cap in brief.get("capabilities", []):
        mark = f"✓ {t['verified']}" if cap["verified"] else f"? {t['claimed']}"
        add(f"- **{cap['name']}** — {cap.get('description') or ''} ({mark})")
    add("")
    for icp in brief.get("icps", []):
        add(f"- ICP **{icp['role']}**: {icp.get('context') or ''}")
        for job in brief.get("jobs", []):
            if job["icp_id"] == icp["id"]:
                add(
                    f"  - Job: {job['job']}" + (f" (trigger: {job['trigger']})" if job.get("trigger") else "")
                )
    if brief.get("open_questions"):
        add("")
        add("Open questions: " + "; ".join(brief["open_questions"]))
    add("")

    # keyword research
    section(t["keywords"].split(". ", 1)[-1])
    plan_doc = bundle["plan"]
    status_counts = Counter(item["status"] for item in bundle["keywords"])
    source_counts = Counter(src["op"] for item in bundle["keywords"] for src in item.get("sources", []))
    add(
        f"- Seeds: {len(plan_doc.get('seeds', []))} "
        f"({', '.join(s['keyword'] for s in plan_doc.get('seeds', []))})"
    )
    add(f"- Competitors reverse-researched: {', '.join(plan_doc.get('competitors', [])) or '—'}")
    add(
        f"- Keywords discovered: {len(bundle['keywords'])} — kept {status_counts.get('KEPT', 0)}, "
        f"excluded {status_counts.get('EXCLUDED', 0)}, unreviewed {status_counts.get('UNREVIEWED', 0)}"
    )
    add("- Found via: " + ", ".join(f"{op} {n}" for op, n in source_counts.most_common()))
    reasons = Counter(
        (item.get("reason") or "")[:60] for item in bundle["keywords"] if item["status"] == "EXCLUDED"
    )
    if reasons:
        add("- Main exclusion reasons: " + "; ".join(f"{r} ({n})" for r, n in reasons.most_common(5)))
    ranked = [item for item in bundle["keywords"] if item.get("site_rank")]
    add(f"- Keywords the client already ranks for (top 20): {len(ranked)}")
    add(
        f"- SERPs analysed: {len(bundle['serps'])}; clusters formed by SERP overlap: {len(bundle['clusters'])}"
    )
    supplements = [item for item in bundle.get("supplements") or [] if item.get("approved")]
    if supplements:
        add(
            f"- {t['supplements']}: "
            + "; ".join(
                f"{item['id']} ({', '.join(s['keyword'] for s in item['seeds'])})" for item in supplements
            )
        )
    progress = [doc for doc in bundle.get("progress") or [] if doc]
    no_data = sorted(
        {
            seed
            for doc in progress
            for seed, stats in (doc.get("seeds") or {}).items()
            if stats["calls"] >= 2 and not stats["rows"] and stats["volume"] is None
        }
    )
    if no_data:
        add(f"- {t['no_data']}: {', '.join(no_data)}")
    failed = [f for doc in progress for f in doc.get("failed", [])]
    if failed:
        add(f"- Data requests that failed (not retried): {len(failed)}")
    add("")

    # opportunity landscape
    section(t["opportunities"].split(". ", 1)[-1])
    add(t["opp_cols"])
    add("|---|---|---|---|---|---|---|---|")
    selected = {cid for topic in topics for cid in topic["cluster_ids"]}
    for cluster in sorted(bundle["clusters"], key=lambda c: -c["total_volume"])[:20]:
        add(
            f"| {_cell(cluster['primary'])} | {_cell(cluster['total_volume'])} | {_cell(cluster.get('primary_kd'))} "
            f"| {_cell(t['intents'].get(cluster.get('serp_intent'), cluster.get('serp_intent')))} "
            f"| {'✓' if cluster.get('has_ai_overview') else ''} "
            f"| {len(cluster['members'])} | {_cell(cluster.get('best_site_rank'))} "
            f"| {'✓' if cluster['id'] in selected else ''} |"
        )
    add("")

    # topics, grouped by hub
    for hub, members in groups:
        for topic in members:
            number += 1
            _render_topic(out, number, topic, bundle, keywords, pages, t, hub)

    # method & limits
    section(t["method"])
    by_role = Counter((p["role"], p["status"], p.get("method")) for p in pages.values())
    for (role, status, method), n in sorted(by_role.items()):
        add(f"- {role} pages {status} ({method}): {n}")
    add(
        "- Coverage claims count only when the quoted passage was found in the captured page text; "
        "'missing' claims count only for pages whose full body was read."
    )
    add(
        "- Pages marked agent-read were read by the research agent in a browser; their text was stored but not fetched by the server."
    )
    add(
        "- Search volume and KD are DataForSEO estimates for the chosen market; missing values mean no data, not zero demand."
    )
    add(
        "- SERP intent is derived from the page types ranking in Google's top 10 (rules reviewed by the analyst); "
        "the keyword tool's intent label is kept for reference only."
    )
    add("- No traffic, ranking or revenue forecast is made.")
    add("")
    requests = bundle["requests"]
    spent = sum(float(r["actual_usd"] or 0) for r in requests if r["status"] == "COMPLETED")
    add(
        f"**{t['cost']}:** {sum(1 for r in requests if r['status'] == 'COMPLETED')} paid requests, "
        f"${spent:.3f}; unknown-cost requests: {sum(1 for r in requests if r['status'] == 'UNKNOWN_COST')}."
    )
    add("")
    return "\n".join(out)


def _render_hubs(
    out: list[str],
    hubbed: list[tuple[dict[str, Any], list[dict[str, Any]]]],
    bundle: dict[str, Any],
    keywords: dict[str, dict[str, Any]],
    clusters: dict[str, dict[str, Any]],
    t: dict[str, Any],
) -> None:
    add = out.append
    brief = bundle["project"].get("brief") or {}
    jobs = {job["id"]: job for job in brief.get("jobs", [])}
    icps = {icp["id"]: icp for icp in brief.get("icps", [])}
    add(t["hub_intro"].format(n=len(hubbed)))
    add("")
    add(t["hub_cols"])
    add("|---|---|---|---|---|---|---|---|")
    for index, (hub, members) in enumerate(hubbed, 1):
        searchable = [topic for topic in members if topic.get("role") != "NO_SEARCH"]
        total = sum(topic_volume(topic, clusters) for topic in searchable)
        upper = any(topic.get("volume_upper_bound") for topic in searchable)
        job = jobs.get(hub.get("job_id"), {})
        icp = icps.get(job.get("icp_id"), {})
        funnel = " → ".join(t["funnel"].get(item, item) for item in hub.get("funnel_coverage") or []) or "—"
        no_search = len(members) - len(searchable)
        pages_cell = f"{len(searchable)}" + (f" + {no_search} {t['roles']['NO_SEARCH']}" if no_search else "")
        star = "\\*" if upper else ""
        add(
            f"| {index} | {_cell(hub['name'])} | {_cell(hub.get('priority'))} "
            f"| {_cell(_clip(job.get('job', ''), 60))} / {_cell(icp.get('role'))} "
            f"| {total:,}{star} | {funnel} | {pages_cell} | {_cell(_clip(hub['why'], 160))} |"
        )
    add("")
    add(t["upper_note"])
    add(t["no_search_note"])
    add("")
    for hub, members in hubbed:
        add(f"### {hub['name']}")
        add("")
        add(f"**{t['page_map']}**")
        add("")
        add(t["map_cols"])
        add("|---|---|---|---|---|---|---|")
        for topic in members:
            analysis = bundle["gap_analyses"].get(topic["id"])
            plan = bundle["page_plans"].get(topic["id"])
            primary = keywords.get(topic["primary_keyword"], {})
            if topic.get("role") == "NO_SEARCH":
                target = f"{topic['primary_keyword']} (—)"
            else:
                target = (
                    f"{topic['primary_keyword']} ({_fmt(primary.get('volume'))} / {_fmt(primary.get('kd'))})"
                )
                secondary = len(topic["cluster_ids"]) - 1
                if secondary > 0:
                    target += f"; +{secondary} synonym SERP(s), {_volume(topic, clusters)}"
            serp = " · ".join(
                item
                for item in (
                    t["intents"].get(topic.get("serp_intent"), topic.get("serp_intent") or ""),
                    topic.get("dominant_page_type") or "",
                )
                if item
            )
            add(
                f"| {t['roles'].get(topic.get('role'), _fmt(topic.get('role')))} | {_cell(topic['name'])} "
                f"| {_cell(target)} | {_cell(serp)} "
                f"| {_cell(t['sources'].get(topic.get('demand_source'), topic.get('demand_source')))} "
                f"| {'✓' if topic.get('has_ai_overview') else ''} "
                f"| {_cell(t['short'][topic_status(analysis, plan)])} |"
            )
        add("")
        names = {topic["id"]: topic["name"] for topic in bundle["topics"]}
        rows = [
            (topic["name"], link)
            for topic in members
            for link in (bundle["page_plans"].get(topic["id"]) or {}).get("internal_links") or []
        ]
        if rows:
            add(f"**{t['links']}**")
            add("")
            add(t["link_cols"])
            add("|---|---|---|---|")
            for source, link in rows:
                target = (
                    names.get(link.get("topic_id"), link.get("topic_id"))
                    if link.get("topic_id")
                    else link["url"]
                )
                add(
                    f"| {_cell(source)} | {_cell(target)} | {_cell(link.get('anchor_intent'))} "
                    f"| {_cell(link.get('section'))} |"
                )
            add("")
        topics = {topic["id"]: topic for topic in bundle["topics"]}
        checks = hub_link_warnings(hub, topics, {k: v for k, v in bundle["page_plans"].items() if v})
        link_issues = [
            issue
            for topic in members
            for issue in (bundle["page_plans"].get(topic["id"]) or {}).get("issues", [])
            if issue.startswith("internal link")
        ]
        if checks or link_issues:
            add(f"**{t['hub_checks']}**")
            add("")
            for item in [*checks, *link_issues]:
                add(f"- {item}")
            add("")


def _best_gain(analysis: dict[str, Any] | None, plan: dict[str, Any] | None) -> dict[str, Any] | None:
    if not analysis or not plan:
        return None
    used = [g for g in analysis.get("gaps", []) if g["id"] in (plan.get("gain_gap_ids") or [])]
    used.sort(key=lambda g: STRENGTH_ORDER.get(g["strength"], 9))
    return used[0] if used else None


def _render_topic(
    out: list[str],
    number: int,
    topic: dict[str, Any],
    bundle: dict[str, Any],
    keywords: dict[str, dict[str, Any]],
    pages: dict[str, dict[str, Any]],
    t: dict[str, Any],
    hub: dict[str, Any] | None = None,
) -> None:
    add = out.append
    analysis = bundle["gap_analyses"].get(topic["id"])
    plan = bundle["page_plans"].get(topic["id"])
    status = topic_status(analysis, plan)
    add(f"## {number}. {t['topic']}: {topic['name']}")
    add("")
    # decision
    add(f"**{t['decision']}:** {plan['action'] if plan else '—'} — {t['status'][status]}")
    if plan and plan["action"] == "SKIP":
        add("")
        add(plan["skip_reason"])
    elif plan:
        add("")
        add(f"*{plan['angle']}*")
        if plan.get("target_url"):
            add(f"Target page: {plan['target_url']}")
    add("")
    add(f"Why this topic: {topic['why']}")
    add("")
    facts = []
    if hub:
        facts.append(
            f"{t['page_role']}: {t['roles'].get(topic.get('role'), topic.get('role'))} · {hub['name']}"
        )
    if topic.get("funnel"):
        facts.append(t["funnel"].get(topic["funnel"], topic["funnel"]))
    if analysis and analysis.get("depth"):
        facts.append(t["depth"][analysis["depth"]])
    if topic.get("serp_intent"):
        intent_text = f"SERP: {t['intents'].get(topic['serp_intent'], topic['serp_intent'])}"
        if topic.get("dominant_page_type"):
            intent_text += f", {topic['dominant_page_type']}"
        if topic.get("intent_override_reason"):
            rules = t["intents"].get(topic.get("serp_intent_rules"), topic.get("serp_intent_rules"))
            intent_text += f" (rules said {rules}; {topic['intent_override_reason']})"
        facts.append(intent_text)
    if topic.get("has_ai_overview"):
        facts.append("AI Overview ✓")
    if facts:
        add(" · ".join(facts))
        add("")
    # demand
    add(f"### {t['demand']}")
    add("")
    if topic.get("role") == "NO_SEARCH":
        add(t["no_search"])
        add("")
        add(f"- {topic.get('demand_note') or '—'}")
        if analysis and analysis.get("demand_status"):
            add(f"- {t['demand_status'][analysis['demand_status']]}")
            for task in analysis.get("demand_tasks") or []:
                add(f"  - {task['from'] or 'client'}: {task['ask']} → {task['deliverable'] or '—'}")
        if topic.get("nearest_keyword"):
            add(f"- Audited against the nearest query: “{topic['nearest_keyword']}”")
        add("")
    add("| Keyword | Volume | KD | Intent | YoY | Client rank |")
    add("|---|---|---|---|---|---|")
    ordered = sorted(topic["keywords"], key=lambda k: -(keywords.get(k, {}).get("volume") or 0))
    for keyword in ordered[:10]:
        item = keywords.get(keyword, {})
        trend = item.get("trend_yoy")
        add(
            f"| {_cell(keyword)} | {_cell(item.get('volume'))} | {_cell(item.get('kd'))} | {_cell(item.get('intent'))} "
            f"| {_cell(f'{trend:+.0%}' if isinstance(trend, (int, float)) else None)} | {_cell(item.get('site_rank'))} |"
        )
    if len(ordered) > 10:
        add(f"| … {len(ordered) - 10} more | | | | | |")
    add("")
    # ranking
    audited_query = topic.get("nearest_keyword") or topic["primary_keyword"]
    serp = bundle["serps"].get(audited_query) or {}
    add(f"### {t['ranking']}")
    add("")
    add(f"SERP features for “{audited_query}”: {', '.join(serp.get('features', [])) or '—'}")
    add("")
    for row in serp.get("organic", [])[:8]:
        page = pages.get(_page_id(normalize_url(row["url"])))
        detail = ""
        if page:
            detail = f" — {page['status']}"
            if page.get("word_count"):
                detail += f", {page['word_count']} words"
            if page.get("modified_at") or page.get("published_at"):
                detail += f", updated {(page.get('modified_at') or page.get('published_at'))[:10]}"
        add(f"{row['rank']}. {row['title']} ({row['domain']}){detail}")
    if serp.get("paa"):
        add("")
        add("People also ask: " + " · ".join(serp["paa"][:8]))
    add("")
    if not analysis:
        add("_Gap analysis not done._")
        add("")
        return
    questions = {q["id"]: q for q in analysis["questions"]}
    # baseline
    deep_by_question: dict[str, int] = Counter(
        row["question_id"] for row in analysis["coverage"] if row["level"] == "DEEP"
    )
    audited = {row["page_id"] for row in analysis["coverage"] if row["level"] != "UNKNOWN"}
    add(f"### {t['baseline']}")
    add("")
    add(f"Audited pages: {len(audited)}.")
    if not any(count >= 2 for count in deep_by_question.values()):
        add(t["thin_serp"])
    for qid, question in questions.items():
        if deep_by_question.get(qid, 0) >= 2:
            add(f"- {question['question']} — answered in depth by {deep_by_question[qid]} audited pages")
    add("")
    # gains
    add(f"### {t['gains']}")
    add("")
    gaps = sorted(analysis["gaps"], key=lambda g: STRENGTH_ORDER.get(g["strength"], 9))
    if not gaps:
        add(analysis.get("no_gap_reason") or "—")
    external = {item["id"]: item for item in analysis.get("external_evidence", [])}
    for gap in gaps:
        ev = gap["evidence"]
        add(f"**[{gap['strength']}] {gap['statement']}** ({gap['gap_type']}, {gap['gain_type']})")
        add("")
        add(
            "- Reader questions: "
            + "; ".join(questions[q]["question"] for q in gap["question_ids"] if q in questions)
        )
        add(
            f"- Evidence: {ev['pages_weak']} of {ev['pages_checked']} audited pages miss or under-answer it; "
            f"{ev['pages_deep']} answer it in depth; search demand observed: {'yes' if ev['search_demand'] else 'no'}; "
            f"verified external signals: {ev['external_support_verified']}"
        )
        rows = [
            row for row in analysis["coverage"] if row["question_id"] in gap["question_ids"] and row["quote"]
        ]
        for row in rows[:2]:
            page = pages.get(row["page_id"], {})
            add(f"  - {page.get('url', row['page_id'])} ({row['level']}): “{row['quote'][:200]}”")
        for eid in gap["demand_evidence_ids"][:3]:
            item = external.get(eid)
            if item:
                tag = "verified quote" if item["verified"] else "unverified"
                add(f"  - Demand: {item['finding']} — {item['url']} ({tag}): “{item['quote'][:200]}”")
        add(f"- Why it matters: {gap['why_it_matters']}")
        add(f"- Counter-argument: {gap['counter_argument']}")
        add(f"- What the client adds: {gap['brand_contribution']} — proof: {gap['proof_status']}")
        for proof in gap["brand_proof"]:
            if proof["verified"]:
                add(f"  - {proof['url']}: “{proof['quote'][:200]}”")
        add("")
    # site
    if plan and plan.get("site_checks"):
        add(f"### {t['site']}")
        add("")
        for check in plan["site_checks"]:
            read = "" if check["body_read"] else " (body not read)"
            add(f"- {check['url']}: {check['verdict']}{read} — {check['reason']}")
        add("")
    # outline
    if plan and plan["action"] != "SKIP":
        add(f"### {t['outline']}")
        add("")
        add(f"- Page type: {plan['page_type']}")
        if plan.get("page_format"):
            add(f"- Format: {t['formats'][plan['page_format']]}")
        add(f"- Title: {plan['title']}")
        add(f"- H1: {plan['h1']}")
        add("")
        gap_names = {g["id"]: g["statement"] for g in analysis["gaps"]}
        for section in plan["sections"]:
            marker = "★ GAIN" if section["kind"] == "GAIN" else "BASELINE"
            add(f"**H2 {section['heading']}** `{marker}`")
            if section["reader_question"]:
                add(f"_Answers: {section['reader_question']}_")
            for point in section["points"]:
                add(f"- {point}")
            for sub in section["subheadings"]:
                add(f"  - H3 {sub}")
            if section["gap_ids"]:
                add(f"  - Gain: {'; '.join(_clip(gap_names.get(g, g), 110) for g in section['gap_ids'])}")
            add("")
        if plan.get("cta"):
            add(f"CTA: {plan['cta']}")
            add("")
        if plan.get("internal_links"):
            names = {item["id"]: item["name"] for item in bundle["topics"]}
            add(f"**{t['links']}**")
            add("")
            for link in plan["internal_links"]:
                target = (
                    names.get(link.get("topic_id"), link.get("topic_id"))
                    if link.get("topic_id")
                    else link["url"]
                )
                where = f" ({link['section']})" if link.get("section") else ""
                add(f"- → {target}: {link.get('anchor_intent') or '—'}{where}")
            add("")
    # next actions
    tasks = [
        task
        for gap in analysis["gaps"]
        if gap["strength"] in {"STRONG", "MODERATE"}
        for task in gap["collection_tasks"]
    ]
    add(f"### {t['next']}")
    add("")
    waiting = pending_sections(analysis, plan)
    if status == "READY_TO_WRITE":
        add("- Brief a writer with the outline above; sections marked ★ GAIN with proof can be written now.")
    for section in waiting:
        add(
            f"- Section “{section['heading']}” waits for client material (below); publish without it or add it later."
        )
    for task in tasks:
        add(
            f"- Ask {task['from'] or 'the client'}: {task['ask']} → deliverable: {task['deliverable'] or '—'}"
        )
    if status == "NO_CLEAR_GAIN":
        add(
            "- Do not produce this page until a gain is proven; revisit if the client can supply first-hand material."
        )
    unknown = sum(1 for row in analysis["coverage"] if row["level"] == "UNKNOWN")
    if unknown:
        add(f"- {unknown} coverage judgements remain UNKNOWN (page not readable or quote not verified).")
    add("")
