# The seven-step gap analysis

A content gap is not "competitors didn't write X". It is a chain: a real reader question → ranking pages
answer it badly (verified) → readers actually need it (evidence) → the client can answer it better
(proof) → it fits on a page. Each link can break; when one does, say so instead of skipping ahead.

## Depth by role

Every page in a hub gets a gap analysis, but not the same depth. `get_topic_workspace` returns the
topic's `analysis_depth`; the server checks against it.

| Depth | For | What changes |
|---|---|---|
| FULL | PILLAR, COMMERCIAL, SUPPORT at or above the light threshold (default 100/month), topics outside a hub | All seven steps below. Read at least 4 ranking pages, ideally 5–6; at least 3 must have verified coverage rows. |
| LIGHT | SUPPORT below the threshold | Read 2–3 ranking pages (at least 2 with verified rows). Step 5's external demand search is optional. Strength rules are unchanged, so fewer pages simply make STRONG harder to reach. |
| EVIDENCE | NO_SEARCH | There is no SERP of its own. **Prove the need first** (below). Audit the `nearest_keyword` SERP the workspace fetched, or state in `no_auditable_pages_reason` why nothing comparable exists. |

For a page in a hub, the workspace lists its siblings. A gain that is really another page's subject goes
there; this page links to it instead of covering it twice.

### Proving demand for a no-search page

At least one of these must be verified by the server, otherwise the topic cannot be better than
NO_CLEAR_GAIN:

- an `external_evidence` quote (forum, community, review, GitHub issue) with `stance: supports_need`;
- a `paa` / `related` question that matches a stored People Also Ask entry from a nearby query;
- client material — sales call notes, support tickets — registered as `demand_tasks` (ask / from /
  deliverable). Until it arrives the topic stays NEEDS_CLIENT_INPUT.

"Nobody has written this" is not a gain by itself: *no page* is not the same as *pages that miss the
answer*. A no-search page's gaps are capped at MODERATE, and with no audited page at all a gap needs two
verified demand signals to reach MODERATE.

## Three kinds of gap

Keep three different gaps apart:

| Gap | Question | Proven by |
|---|---|---|
| Coverage gap | Does the client lack a page competitors have? | SERP + site check. Not a reason to write by itself. |
| Answer gap | Which reader question do ranking pages answer poorly? | Coverage matrix with quotes + demand evidence. |
| Information gain | What true, useful information can *this client* add? | Client's own pages (proof) or concrete inputs to collect. |

## Step 1 — Lock the reader

From the workspace `reader` block write down (in your head, then in the gap fields): who searches, what
triggered the search, what they must decide or do next, what constraint they work under, and what they use
today. A platform engineer shipping to production and a student learning "what is an agent" share words
but not needs. Every later judgement ("is this answered well?") is relative to this reader.

## Step 2 — Map queries to intent

Look at the topic's keywords and the SERP. The workspace gives the SERP-based `serp_intent`,
`dominant_page_type` and `has_ai_overview`; check them against the titles. What page type wins (guide,
list, comparison, docs, tool, product page)? Is the intent mixed — e.g. half definitions, half vendor
pages? If the dominant intent is not something the client can serve, stop here and plan a SKIP. If the
SERP serves a different intent than the gain you have in mind (definitions vs. how it works), the gain
belongs on another page in the hub.

## Step 3 — Read what ranks

The workspace fetched the top ranking pages and shows each outline. That is not enough to judge
coverage: **read the bodies** with `read_page` (at least 4 pages, ideally 5–6, from different
domains). Pages with status THIN/BLOCKED/FAILED: open them in the browser if you can and
`submit_page_text` with what you actually read; otherwise leave them out of MISSING judgements.

While reading, note: what every page says (the table stakes), where they disagree, what they assert
without evidence, how current they are (`modified_at`), and what a practitioner would still have to
figure out alone after reading all of them.

## Step 4 — Questions and the coverage matrix

Write 5–10 reader questions:

- from People Also Ask / related searches (`source: paa` / `related` — must match the stored text),
- from headings that recur across ranking pages (`serp_heading`),
- from the reader job (`job`) — the questions a practitioner asks that SERP features don't surface,
- from external discussions (`external`).

Mark 2–4 as `core`: the questions the reader must have answered to complete the job.

Then judge **each question × each audited page**:

| Level | Meaning | Required |
|---|---|---|
| DEEP | Answers it with specifics: steps, conditions, numbers, trade-offs, examples. | verbatim quote of the strongest passage |
| SHALLOW | Mentions or defines it, generic advice, no conditions or specifics. | verbatim quote showing the shallow treatment |
| CONTRADICTORY | Says something wrong or conflicting with other pages. | verbatim quote + note on the conflict |
| MISSING | Not addressed anywhere in a body you read fully. | the page must be fully read |
| UNKNOWN | Not readable, partially read, or you are unsure. | — |

Quotes must be copied exactly from `read_page` output (12+ characters; the check ignores punctuation and
case, not wording). Navigation, footers and "related posts" are not answers.

## Step 5 — Demand and counter-evidence

For each candidate gap ask: do real readers hit this, and when? Look for evidence outside the SERP:
forums (Reddit, Hacker News, Stack Overflow, community Slack archives), reviews (G2, Capterra), GitHub
issues, YouTube comments, practitioner blogs. `fetch_pages role=source`; when blocked (Reddit often is),
read it in the browser and `submit_page_text`. Record what you found as `external_evidence` with the exact
quote and `stance: supports_need`.

Then argue against yourself and write it as `counter_argument`:

- Maybe competitors skip it because it doesn't matter to this query's intent.
- Maybe it is answered elsewhere (docs, tools, vendor support) and readers don't expect it here.
- Maybe it's too niche, too hard to source, or out of date quickly.

If you find evidence against the need — including a strong page that already answers it outside this
SERP — record it with `stance: against_need` and cite it in the gap's `counter_evidence_ids`. Verified
counter-evidence caps the gap at MODERATE; that is the honest outcome, not a penalty to avoid. A gap that survives its
counter-argument is worth something; one that doesn't should be dropped.

## Step 6 — What the client can add

Separate *there is a gap* from *we can fill it*. For each gap choose a gain type and meet its proof
standard:

| gain_type | What it is | Proof that counts |
|---|---|---|
| original_data | Numbers nobody else has: benchmarks, survey, usage stats | the data itself on a client page, or a task to obtain it (dataset, owner, method) |
| first_hand_experience | What actually happened when doing it: incidents, rollouts, customer cases | a published case/doc, or a task: interview whom, about what |
| technical_depth | How it really works: mechanisms, configs, failure modes, limits | client docs/product pages describing the mechanism |
| decision_framework | A way to choose: criteria, thresholds, trade-offs, when NOT to | can be built from evidence; cite the inputs |
| novel_insight | A conclusion others miss, reasoned from evidence | the reasoning + sources; label it as inference |
| question_coverage | A question nobody answers, that the client can answer correctly | client page or expert input |
| utility | The reader must calculate, decide for their own situation or generate a configuration — text cannot do it for them | all three: verified demand, a build task, product proof (below) |

**Utility gains** are what an AI summary cannot replace, so they matter most where the SERP shows an AI
Overview. They count only when all three hold; otherwise the server lists what is missing and rates the
gap WEAK:

1. **Readers need it:** a matched People Also Ask question or a verified external quote where readers ask
   "how do I work out / which should I pick / generate this for me".
2. **The client can build and maintain it:** a `collection_tasks` entry naming the data the tool needs
   (`ask`), who builds and maintains it (`from`) and the `deliverable`.
3. **It belongs to the product:** a verified `brand_proof` quote from the client's site or docs.

Examples: a per-tool approval policy generator (enter the tools an agent calls, get `always_allow` /
`always_ask` per tool); an end-to-end cost calculator for one agent task (model tokens, sandbox compute,
human review time); an interactive platform evaluation checklist.

`brand_proof` = page_id + verbatim quote from the client's captured pages. Marketing claims ("best-in-class
security") prove nothing; feature descriptions, docs, case studies and published numbers do.

When proof is missing, write `collection_tasks` that a project manager could send tomorrow:
- ✗ "Need more case studies."
- ✓ ask: "Pull rollback events from the last 90 days: how many, time-to-rollback, root cause category" ·
  from: "Platform team lead" · deliverable: "Anonymised table, 10–30 rows".
- ✓ ask: "30-minute interview: the worst production incident with an agent and how it was contained" ·
  from: "a customer's platform engineer (via CSM)" · deliverable: "Call notes + approval to quote".

## Writing a gap

- `statement`: specific and checkable. ✗ "Competitors lack depth on deployment." ✓ "None of the 5 audited
  guides explain how to roll back an agent after a bad prompt or tool change; 3 don't mention rollback."
- `why_it_matters`: the consequence for the reader's job. ✓ "Without rollback, a faulty agent keeps acting
  on customers while the team rebuilds a fix."
- `brand_contribution`: what exactly the client adds, not what the product is. ✓ "Acme's versioned
  snapshots make rollback one step; the page can show the procedure and the trade-offs vs. blue-green."

## How the server scores you

- Rows without valid quotes/full reads become UNKNOWN.
- STRONG: ≥3 pages checked, ≥2 miss or under-answer, ≤1 answers deeply, plus search or verified external
  demand, and no verified counter-evidence cited. MODERATE: ≥2 pages checked, at least one weak, not all deep, plus demand. Otherwise WEAK.
- EVIDENCE depth (no-search pages): never STRONG; with no audited pages, MODERATE needs two verified demand
  signals.
- utility gains missing any of their three conditions are WEAK.
- REFUTED: ≥2 audited pages already answer it deeply — it is table stakes. Use it as BASELINE.
- Proof: VERIFIED (quotes found on the client's pages) / NEEDS_CLIENT_INPUT (concrete tasks) /
  UNSUPPORTED.

A topic where every gap is WEAK or REFUTED is telling you the SERP already serves the reader. Accept it
(`no_gap_reason` + SKIP) or go find better evidence — don't write around it.
