# Keyword strategy: seeds, review, hubs

The keyword universe is only as good as the seeds and the review. A run that starts from one long-tail
phrase finds almost nothing; a run that keeps everything ends up clustering noise.

## Designing seeds (plan_research)

Build seeds from the brief, not from the product's marketing name. For **each job**, write seeds from
four angles, then trim to 15–30 total:

| Angle | Breadth | What it captures | Example (job: "ship an AI agent to production safely") |
|---|---|---|---|
| Category | broad (1–2 words) | How people name the solution space | `ai agents`, `agent platform` |
| Problem | core (2–3 words) | The pain or task in the searcher's words | `deploy ai agents`, `agent monitoring` |
| Approach / alternative | core | What they do today instead | `langchain deployment`, `agent orchestration` |
| Decision | specific (3–4 words) | Comparison and evaluation queries | `ai agent platform comparison` |

Rules:

- Use the searcher's vocabulary. Read the ICP's pains and the competitors' pages for wording; product
  feature names are rarely searched.
- 1–3 words for most seeds. Labs suggestions only return phrases *containing* the seed, so a 5-word seed
  returns a handful of results.
- At least 2 broad seeds, and every job gets seeds.
- Add competitor domains (3–5 real competitors that publish content). Their ranked keywords are the
  fastest route to queries that already bring them traffic. The client's own domain is researched
  automatically to show what it already ranks for.
- A budget of $2–3 normally covers 20 seeds, a second round and ~120 SERPs.

## Reviewing keywords (review_keywords)

Decide every keyword. For each one ask, in order:

1. **Who is searching?** Same role as the ICP? A student, job seeker, hobbyist or consumer is a different
   person even when the words overlap ("ai agent course", "ai agent salary").
2. **What are they trying to do?** Learn, implement, compare, buy, troubleshoot, navigate. Map it to a
   brief job. If no job fits, exclude. Intent is a label, not a filter: see "Definition queries" below.
3. **Is it about someone else's brand?** `meta ai`, `scale ai`, a competitor's product name — exclude,
   unless the job is explicitly "evaluate alternatives to X" (then keep it for a comparison topic, never as
   a generic topic's primary query).
4. **Geography / language / format mismatch?** Local-service, other-country or non-target-language
   queries are excluded.
5. **Could the client credibly rank and convert?** A query the product has nothing to do with is not
   "awareness" — exclude it.

Practical routine:

1. Skim the first pages and spot noisy origins in `found_from`. Competitor domains often rank for
   consumer or unrelated terms (a scheduling tool ranks for "2 weeks notice letter"). Decide the few
   relevant keywords from such a source explicitly, then `exclude_sources` the rest of that source in one
   call — only keywords found *solely* from that source are touched.
2. `exclude_patterns` for obvious families (other brands, `jobs`, `salary`, `course`, `free download`,
   `character`, `image generator`...).
3. Go page by page from highest volume and decide the remainder. Keep the
reason short and specific ("consumer chatbot, not enterprise"). A batch where you kept everything is a
sign you did not review it.

Low or missing volume is not a reason to exclude a clearly relevant B2B query; volume tools under-count
niche professional searches. Keep it, and let topic selection weigh it.

### Definition and concept queries belong to pillars

"what is X", "X meaning", "X definition", "X vs Y" (two concepts) are where a hub's pillar gets its
traffic (`hitl meaning` 1,300/month, `what is an ai agent` 18,100). Keep them when X is the client's
subject, assign the job the hub serves, and treat them as pillar candidates. **Never write "learner
intent" or "informational" as an exclusion reason.**

Exclude only:

- generic AI common knowledge with no tie to the client's subject (`what is ai`, `what is chatgpt`);
- definitions of things the client has nothing to do with;
- the person filters above (courses, salaries, homework).

Intent decides the label, the weight and the page type — never whether a relevant query stays. How much
weight each intent gets comes from the brief's business goal, not from the tool: a client that needs
sales conversations leans commercial; a client building brand awareness takes more informational pages.

**Expansion:** mark `expand: true` on 5–12 kept keywords that are central to a job and are *not*
already seeds (seeds were expanded in round 1). Good picks are relevant phrasings you did not think of,
e.g. a competitor's ranking query or a strong suggestion. Do not expand broad head terms — they explode
into noise.

## Building hubs (save_topics)

A cluster is a set of queries Google answers with the same pages, so one cluster ≈ one page. Clusters
that serve one subject belong together: a **hub** is one pillar page plus the pages that go deeper.
Every page in a hub is still a topic with its own gap analysis and page plan.

| Role | What it is | Typical query |
|---|---|---|
| `PILLAR` | The broadest query of the subject; satisfies the definition / overview intent and routes readers on | `human in the loop ai`, `ai guardrails` |
| `SUPPORT` | One narrower question the pillar can only touch; this is where most information gains live | `human in the loop vs human on the loop`, `ai agent guardrails` |
| `COMMERCIAL` | Evaluation or selection page closest to the purchase | `ai agent platform`, `best ai agent builder` |
| `NO_SEARCH` | Worth writing with no measurable volume: readers come from sales, links, social or AI answers | `approval fatigue` (0 in DataForSEO) |

### Read the SERP intent first

`list_clusters` gives each cluster a `serp_intent` (INFORMATIONAL / COMMERCIAL / TRANSACTIONAL / MIXED)
computed from the page types that actually rank, the page-type mix, the dominant page type, and
`has_ai_overview`. `dataforseo_intents` is the keyword tool's guess from the words and is often wrong
(in one real run it agreed with the SERP for 38 of 96 clusters). Read `top_results` before trusting
either: the rules can misread titles. When you disagree, pass `intent_override` with an
`intent_override_reason` saying what the rules misread.

A cluster whose SERP is definitions should not carry a how-it-works page; let the pillar meet the
definition intent and move the mechanism to a support page. One page serving two intents serves neither.

### Rules the server enforces

- One `PILLAR` per hub, and it should be the broadest query (a warning if a support has more volume).
- A cluster belongs to one topic only — otherwise the client's own pages compete for the query. To change
  a saved topic, pass its `id` again.
- Two topics in a hub whose primary SERPs share ≥3 URLs are one page to Google: merge them.
- A pillar may take synonym clusters as secondary targets (`human in the loop ai` + `hitl meaning` +
  `hitl ai`). Their SERPs differ, so the summed volume is reported as an **upper bound**.
- `NO_SEARCH` topics have no `cluster_ids`, need `demand_source` PAA or EVIDENCE and a `demand_note`
  (who needs it, how they will reach it), and may set `nearest_keyword` — a query with a stored SERP that
  the page will be audited against. They never count toward volume.
- The hub `why` must carry the numbers.

### Choosing what goes in

For each candidate hub and page look at:

- **Demand:** corrected volume (spelling variants counted once), trend, how many distinct phrasings.
- **Fit:** which job; how directly the product helps; where it sits in the funnel (`AWARENESS` /
  `CONSIDERATION` / `DECISION`). Aim for each hub to reach at least one page near the decision.
- **Feasibility:** KD of the primary query, who ranks (big publishers vs. niche blogs vs. vendors), whether
  the client already ranks (`best_site_rank` — positions 5–20 are often the best UPDATE opportunities).
- **AI Overview:** with `has_ai_overview`, a text-only page competes with Google's own summary. Prefer
  pages where the reader needs to compute, decide for their own case or generate something (a utility
  gain, see `page-plan.md`).
- **Gain potential:** do you already suspect the client has something others lack (data, product insight,
  a method)? If you cannot imagine any gain, the page will likely end in SKIP — pick another.

Pick 2–5 hubs and 3–15 pages in total. Write `why` with numbers: "1,290 searches/month across 3 queries,
KD 35, client ranks #14 for the governance variant, directly maps to job j2."

`light_analysis_max_volume` (default 100 searches/month) sets which `SUPPORT` pages get a light gap
analysis; raise it for a high-volume market, lower it for a niche one.

## Adding seeds after the research (supplement)

When topic work shows a missing angle (e.g. the client's core differentiator has no seeds yet), add seeds
without restarting: `plan_research` with `supplement: true`, 1–40 seeds and `budget_usd` = the project's
**new total cap** (it must cover what was already spent). Show the estimate, `approve_budget` only after
the user agrees, then `run_research_step step=supplement_discover`, review the new keywords, and
`step=supplement_serp` (SERPs only for new spelling-variant groups; clusters are rebuilt). Seeds that come
back in `seeds_without_data` have no DataForSEO data at all — that is unmeasured demand, not zero demand;
such subjects are candidates for `NO_SEARCH` pages. If re-clustering reports `stale_topics`, re-save them.
