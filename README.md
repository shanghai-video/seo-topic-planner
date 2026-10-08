# SEO Topic Planner

An Agent Plugins 1.0 local Codex plugin that turns a confirmed brand brief into
ranked SEO content decisions, structured evidence, and selected-opportunity
outlines. It is deliberately not a wrapper around individual DataForSEO API
endpoints.

## Included

- Agent Plugins 1.0 manifest with an installation-time onboarding skill and a
  portable stdio Planner binding.
- A small, action-oriented MCP surface: website-keyed brand memory, explicit
  brand-context confirmation, a typed brand entity registry, DataForSEO
  connection, research estimate, confirmed research, viewpoint write-back,
  topical entity and semantic-coverage write-back, a relevance research gate,
  opportunity/evidence reads, and evidence-backed content clusters.
- A brand-context MCP App card (`open_brand_context_card`) that asks the user to
  confirm the target market, language and the product's core features. The empty
  first-run context is offered as scrape-derived, editable suggestions; market
  and language are never defaulted by the Planner.
- A brand-entity MCP App card (`open_brand_entity_card`) that separates
  Organization, Brand, Product and concept entities and records only verified
  external profiles, so a `sameAs` set stays empty until a real URL is confirmed.
- A Planner-owned DataForSEO OAuth 2.1 connection (dynamic client registration,
  authorization code + PKCE, loopback redirect, refresh) that stores a
  short-lived token in a user-only local file.
- Versioned JSON-serializable Core records, state machine, workspace checks,
  cost/budget guard, structured SERP evidence (organic + AI overview + PAA +
  related searches), viewpoint observations, evidence lineage, and a
  per-workspace cache.
- Real, explainable content-gap scoring from stored signals instead of
  hardcoded values.

## Local runtime

The plugin uses only the Python standard library and needs Python 3.9 or newer.
Its MCP entry point is `python3 ${PLUGIN_ROOT}/server/run.py`.

Codex supplies `PLUGIN_DATA` for installed copies; otherwise data is persisted
at `~/.seo-topic-planner/`. Set `SEO_TOPIC_PLANNER_DATA_DIR` to explicitly
override either location. That directory can contain research inputs/outputs and
the DataForSEO token, so never put it under version control.

## Connect live DataForSEO data

The Planner owns the connection. It does not rely on a host injecting a peer
MCP token, and it never asks for a DataForSEO login or password.

```text
connect_dataforseo            -> registers a public OAuth client, opens the
                                 DataForSEO consent page, returns the URL
complete_dataforseo_connection -> waits for the browser callback, exchanges the
                                 authorization code, stores the token locally
get_data_provider_status      -> AUTH_REQUIRED | READY | MISCONFIGURED
```

After `READY`, the Planner uses live data. The MCP connection tools are:

```text
connect_dataforseo, complete_dataforseo_connection, disconnect_dataforseo
```

`disconnect_dataforseo` deletes the local authorization and returns the Planner
to `AUTH_REQUIRED`. Tokens never appear in tool arguments, research records,
cache keys, logs, or chat. There is no demo or fake data mode: an unconnected
runtime refuses research rather than returning sample data.

## Evidence and gap realism

- The SERP adapter keeps structured `organic`, `ai_overview`, `people_also_ask`
  and `related_searches` items, and stores them in the SERP evidence payload.
- Viewpoint observations submitted by the host agent carry only a source type,
  URL, fetch time, 1-3 short quotes and a summarized viewpoint, plus optional
  credibility, relevance and research-purpose tags. Full page bodies, thread
  contents and usernames are rejected; caching uses a 14-day expiry.
- SERP structural signals (domain spread, content-type variety) describe the
  result surface only. They are never interpreted as "weak competition" or as
  information gain.
- `content_gap` is derived from the stored SERP signals and the site snapshot,
  and the score keeps a transparent `score_breakdown` with a recorded
  `scoring_version`. AI-overview presence is a saturation caveat evaluated on its
  own, not a proxy for demand.

## Information gain and evidence readiness

- Every opportunity carries structured `information_gain` items
  (`original_data`, `first_hand_experience`, `technical_depth`,
  `decision_framework`, `novel_insight`, `question_coverage`). Each item states
  the competitor coverage, the proposed unique contribution, the brand evidence
  behind it, missing evidence, readiness and confidence.
- A competitor gap is not a licence to write. When a gap needs original data or a
  first-hand case the brand has not supplied, the item is `NEEDS_EVIDENCE` and
  the aggregate `evidence_readiness` drops, so the opportunity does not receive a
  false high score or confidence.
- The host agent can refine the baseline through `record_opportunity_analysis`.

## Brand entities

- `brand_entity` records model the Organization, Brand, Product and concept
  subjects separately and link them with `parent_entity_id`/`related_entity_ids`.
  Same-named entities under different brands are never merged.
- First-party details (`legal_name`, `official_website`, `official_product_urls`)
  and externally owned profiles are stored separately. Candidate profiles are
  leads; only a user-verified profile becomes a `verified_external_profile`.
- `get_brand_entity_recommendations` returns evidence-carrying brand building
  actions (`issue`, `observed_evidence`, `recommended_action`,
  `target_page_or_channel`, `priority`, `confidence`, `expected_objective`,
  `validation_method`). These are entity tasks, not keyword opportunities, and
  they promise no rankings, knowledge-graph inclusion or AI citations.

## Research gate and content clusters

The research gate is external-evidence-based, not platform-based. It is enforced
server-side:

- `create_content_cluster` refuses unless the run has at least one `serp_result`
  observation and at least one **external third-party** family the host agent
  actually read: community, technical, business or competitor. The first-party
  `brand` family (the brand's own site, accounts and profiles) never satisfies
  the minimum on its own. One external family is `DEGRADED_MINIMUM`; two or more
  is `FULL_COVERAGE`. `get_cluster_readiness` reports the exact `gate_status`
  (`BLOCKED_NO_SERP` / `BLOCKED_NO_EXTERNAL` / `DEGRADED_MINIMUM` /
  `FULL_COVERAGE`), the families reached and any blocked source attempts.
- A source the host agent cannot read is recorded through
  `record_source_attempts` with a method, an outcome and a reason. Attempts stay
  visible (in `get_cluster_readiness` and in the cluster refusal) but never count
  as evidence, so a blocked run cannot silently pass and nothing is fabricated.
- The output is an evidence-backed plan, not a single topic: one pillar page plus
  only the supporting pages the evidence justifies, drawn from sibling
  opportunities and the pillar SERP's PAA and related searches. The funnel is
  descriptive, so a plan is not required to contain a BOFU page.
- Outline sections are generated from research findings. Each section records the
  reader question, key claim, relevant entities, information gain, required
  evidence and any missing evidence. Each page carries a `content_status`
  (`READY_TO_BRIEF` / `NEEDS_RESEARCH` / `NEEDS_BRAND_EVIDENCE` / `LOW_VALUE`);
  `READY_TO_BRIEF` means the planning is trustworthy enough to brief, not that
  the article is ready to publish.

## Cost and data honesty

- A live estimate is a conservative server policy ceiling, not a DataForSEO
  quote. Actual cost is read from the provider response.
- `budget_cap_usd` defaults to `0`, so a paid run needs an explicit, non-zero
  cap. `run_topic_research` aborts and records `BUDGET_EXCEEDED` if observed
  cost exceeds the cap.
- Only DataForSEO calls are billed through the plugin budget. Browser /
  Computer Use / yt-dlp collection performed by the host agent is not.
- Competitor evidence retains only SERP-derived URL/title metadata; the plugin
  does not claim it fetched competitor-page content itself.

## Scope intentionally excluded

Server-side LLM summarization, the Reddit API, full site audits, GSC sync,
article drafting, CMS publishing, generic browser scraping, and public account
upload are separate products or explicit future work.

External research is collected by the host agent, not by the plugin: browser /
Computer Use first, then a direct HTTP fetch, then Jina Reader, and `yt-dlp` for
YouTube (search plus subtitles). Reddit is often blocked to plain HTTP, so it is
read in the browser or replaced with another accessible community source. What
the host agent actually read is written back as short cited observations; the
plugin never calls those platforms' APIs itself and never stores their full
content. Podcasts are out of scope.
