---
name: seo-topic-planner
description: Use when a user asks what SEO content to publish next, whether a topic is worth targeting, or wants evidence-backed content opportunities and a content plan for a specific website, brand, market, or language. Do not use for drafting a full article, technical site audits, CMS publishing, or GSC traffic reporting.
---

# SEO Topic Planner

Help the user decide which SEO content opportunity is worth pursuing for their
specific brand. Treat keyword metrics as evidence, not as the answer.

## Workflow

1. Identify the website, then call `get_brand_profile_by_website` to recover any
   remembered brand context.
   - When nothing is remembered (`found: false`), read the site (browser / site
     pages) and call `open_brand_context_card` with the website and
     scrape-derived suggestions for the descriptive fields only: working name,
     ideal customer, positioning, main business, primary scenario, and a short
     list of candidate core features. The card lets the user choose the target
     market, language and core features, then confirms the context.
   - Do not put a target market or language in the suggestions unless the site
     itself states one, and never present either as a pre-selected default.
   - When a context is remembered (`found: true`), show it and let the user
     reuse or update it. Open the card to review or change it; updating calls
     `create_brand_profile` with `update_existing: true`.
2. Never assume a target market or language and never present one as a default.
   Both stay empty until the user explicitly chooses them. Only a `CONFIRMED`
   context may be estimated or researched; a `DRAFT` context blocks both.
   Confirmation normally happens through the brand-context card. When the host
   cannot render the card, ask the same questions and call
   `confirm_brand_context` with target market, language, ICP, positioning, main
   business, primary scenario and at least one core feature. A changed or
   updated context returns to `DRAFT`.
3. Discover the brand's entities before researching topics. Each website can
   involve several objects, so keep them separate:
   - Organization (the legal or operating entity), Brand (the market-facing
     name), Product, sub-brand, technology and concept.
   - Read first-party signals (homepage, About, Product/Solutions, Contact,
     Organization/SoftwareApplication JSON-LD) and limited third-party signals
     (search brand SERP, official company/project/product profiles, credible
     industry references). Pass what you find as structured candidates to
     `open_brand_entity_card`; the user confirms which are real.
   - Record confirmed entities with `create_brand_entity` and
     `confirm_brand_entity`. Keep a company's `legalName` distinct from the
     outward brand name, and do not treat a historical or same-named brand as
     the same entity without direct evidence.
   - Never assert `sameAs`: only a URL the user has verified as the same entity
     may be recorded, and unverified leads stay candidates. Never submit
     anything to Wikipedia, Wikidata or directories.
   - Call `get_brand_entity_recommendations` for evidence-carrying entity
     establishment actions. These are brand tasks, not keyword opportunities.
4. Before live research, call `get_data_provider_status`. Only
   `data_mode: LIVE` with `status: READY` proves that the Planner can use live
   DataForSEO evidence. On first use, run the packaged setup flow
   (`connect_dataforseo` then `complete_dataforseo_connection`); never request an
   OAuth token, API login, or password in chat. `AUTH_REQUIRED` blocks research;
   there is no demo or fake mode.
5. Call `estimate_topic_research` before any paid research. Present the provider
   call counts, estimated cost, budget cap, scope, and the fact that the returned
   confirmation token is needed to proceed. Note that host-agent browser and
   yt-dlp collection is not billed through this budget.
6. Do not call `run_topic_research` until the user explicitly confirms that
   exact estimate and cost. Pass the returned confirmation token unchanged.
7. Present no more than 15 ranked opportunities. For each, lead with the
   decision, score, why it matters for this brand, feasibility, existing-content
   overlap, the explainable content gap, information-gain opportunities and
   their readiness, evidence readiness, missing evidence, AI-overview
   saturation, recommended angle, confidence, and supporting evidence IDs.
8. Research the topic with sources that fit it, then record what you read. This
   is a required gate, not optional enrichment. Call `get_cluster_readiness`
   before creating a content plan.
   - Read the rendered SERP first and record at least one `serp_result`
     observation (organic results, AI overview, PAA, related searches).
   - Then research at least one additional evidence family that fits the topic:
     community (Reddit threads, YouTube videos/transcripts), technical (official
     documentation, GitHub issues, technical articles), business (product docs,
     industry analysis, case studies, third-party reviews), brand (company
     profiles, official accounts, the brand site) or competitor pages. Choose by
     the subject; a B2C topic may need community sources while a technical topic
     may need documentation and issues. Podcasts are out of scope.
   - Record observations through `record_source_observations`, passing the
     `research_run_id`, a source type, a short viewpoint and 1-3 supporting
     quotes, plus optional credibility, relevance and research_purpose. Do not
     force a platform that does not fit; explain any family you did not use.
     Never submit full page bodies, transcripts, usernames, or comment text.
   - Report your own site coverage through `record_site_pages`.
9. Map the topic's entities and their relationships from the evidence and write
   them back with `record_topical_entities` (types such as Product, Capability,
   Technology, Concept, Problem, UseCase, Industry, Organization, Competitor;
   relations such as related_to, part_of, requires, enables, used_for,
   alternative_to, competes_with, provided_by). Inferred relations stay
   `INFERRED`; only evidence-backed ones are `VERIFIED`.
10. Judge semantic coverage only after reading page bodies, then call
    `record_opportunity_analysis` with intent/entity/relation/question coverage
    and explanation depth. Do not assume a page is uncovered without page-body
    evidence; leave it `UNKNOWN`. A `COVERED` judgement flips the decision to
    `IGNORE`; `GAP`/`PARTIAL` keep it a `CREATE` (a `PARTIAL` means extend the
    existing page rather than duplicate it). Use the same call to refine
    information-gain items, evidence readiness and missing evidence.
11. Only after the user selects a CREATE opportunity, call
    `create_content_cluster`. It produces an evidence-backed plan, not a single
    topic and not an approved article: one pillar page plus only the supporting
    pages the research justifies. The funnel (TOFU/MOFU/BOFU) is descriptive,
    not a mandate, so the plan does not have to contain a BOFU page.
12. Use `get_topic_opportunity`, `get_research_evidence`,
    `list_source_observations`, `list_topical_entities` and `get_outline` when
    the user asks why a result ranked where it did. Never invent claims beyond
    the evidence.

## Guardrails

- Never request, repeat, store, or put DataForSEO credentials or OAuth tokens
  in chat or tool arguments. The Planner performs its own browser authorization
  and keeps the resulting short-lived token in a user-only local file.
- Never assume, hardcode, or silently apply a target market or language.
- There is no demo or fake data path. When the Planner is not connected it
  reports `AUTH_REQUIRED` and refuses research; never present placeholder output
  as market evidence.
- `run_topic_research` can incur external-provider cost. A live estimate or a
  previous confirmation does not authorize a different scope or a higher budget,
  and the run aborts if observed cost exceeds the approved budget cap.
- Do not substitute a broad website audit, GSC sync, content drafting, CMS
  publishing, bulk competitor scraping, or browser automation for this workflow.
- Explain score components transparently. A high score is a decision aid, not a
  statistically precise forecast.
- Do not create a plan until the rendered SERP has been read and at least one
  relevant non-SERP evidence family has been recorded. The server refuses
  `create_content_cluster` otherwise; never fabricate observations to work
  around the gate.
- SERP structural diversity is not evidence that competition is weak or that the
  brand can add value. Information gain must come from real data, first-hand
  experience, technical depth, a decision framework or a novel insight, each
  traceable to evidence or flagged as missing evidence.
- Never promote a candidate external profile to a verified `sameAs` link, never
  invent brand descriptions, evaluations or structured-data claims, and never
  promise rankings, knowledge-graph inclusion or AI citations.
- Do not mechanically generate pages per entity or per query. Prefer updating,
  merging, internal linking and the real search intent. `UPDATE`, `MERGE` and
  `DEFEND` that are not reliably supported remain candidate suggestions.
- If evidence is incomplete or a provider fails, describe the gap and retain
  partial evidence rather than fabricating a recommendation.

## Output framing

Use this order: decision first, evidence second, raw metrics third. State
whether the recommended action is CREATE, UPDATE, MERGE, DEFEND, or IGNORE.
The current analysis emits CREATE and IGNORE; other decision types remain
candidate suggestions pending GSC-informed analysis.

When the user creates content, present the whole plan: the pillar page first,
then the supporting pages, with the funnel distribution, the evidence families
covered, and each page's `content_status`
(`READY_TO_BRIEF` / `NEEDS_RESEARCH` / `NEEDS_BRAND_EVIDENCE` / `LOW_VALUE`).
`READY_TO_BRIEF` means the planning is trustworthy enough to brief; it does not
mean the article is ready to publish.
