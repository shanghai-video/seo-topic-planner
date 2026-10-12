# SEO Topic Planner

A Codex and Claude Code plugin that does SEO topic research the way a senior strategist would: find what people search
for, see what Google ranks, read those pages, find what they fail to give the reader, check what the client
can add, and outline pages around that information gain — with evidence for every claim.

Published by [Lingyi Labs](https://lingyilabs.com).

## Install

Requires Python 3.9+ available as `python3` and a DataForSEO account.

**Codex**

```bash
codex plugin marketplace add shanghai-video/seo-topic-planner
codex plugin add seo-topic-planner@lingyi-labs
```

Update with `codex plugin marketplace upgrade`.

**Claude Code**

```
/plugin install seo-topic-planner --marketplace shanghai-video/seo-topic-planner
```

On Claude Code older than 2.1.275, add the marketplace first:

```
/plugin marketplace add shanghai-video/seo-topic-planner
/plugin install seo-topic-planner@lingyi-labs
```

Update with `/plugin marketplace update lingyi-labs`.

After installing, the setup skill connects DataForSEO through its browser consent page; no API password is
entered in chat.

## What a run produces

- **Keyword universe** from 15–30 seeds (suggestions + related keywords), competitor rankings and the
  client's own rankings, each keyword reviewed and tied to a reader job (bulk exclusion by pattern or by
  noisy source keeps review practical at 2,000+ candidates).
- **Clusters by SERP overlap**: queries whose Google top 10 share URLs belong on one page; spelling
  variants (hyphens, plurals, word order) are always merged and their volume counted once. Each cluster's
  intent is read from the page types that actually rank (the keyword tool's label is reference only), with
  an AI Overview flag.
- **Topic hubs**: one pillar for the broadest query, support pages for the narrower questions, optional
  commercial and no-search pages (worth writing without measurable volume, backed by demand evidence and
  never counted as traffic), with internal-link rules and a check that two pages never chase one query or
  deliver the same gain.
- **Seed supplements** after the research: new seeds go through the same estimate and approval, spend on
  the same ledger, fetch SERPs only for new variant groups, and report seeds DataForSEO has no data for.
- **Per topic**, at a depth set by its role (full, light for small support pages, evidence check for
  no-search pages): the ranking pages read in full, a question × page coverage matrix with verbatim quotes,
  demand and counter-evidence, gaps scored STRONG / MODERATE / WEAK / REFUTED from that evidence, the
  client's proof (quotes from its own pages) or concrete collection tasks, and a page action (CREATE /
  UPDATE / MERGE / SKIP) with an outline whose sections are marked BASELINE or GAIN. Utility gains
  (calculations, decisions for the reader's own case, generated configs) become tools embedded in the page,
  or a standalone tool page when a tool query has search volume.
- **A client report** (English or Chinese) that leads with decisions, then the hubs (page map, links,
  reachable volume with upper bounds marked) and the evidence behind each page.

## How quality is enforced

The host agent does the reading and judging, guided by the skill and its references
(`skills/seo-topic-planner/references/`). The server checks what can be checked:

- a DEEP/SHALLOW/CONTRADICTORY judgement needs a quote that exists in the captured page text;
- a MISSING judgement needs a page whose full body was read;
- People-Also-Ask questions must match stored SERP data;
- client capabilities and brand proof must be quotes from the client's own pages;
- gap strength is computed from counts of verified evidence, not taken from the agent;
- a gap that ≥2 ranking pages already answer in depth is REFUTED (table stakes), and verified
  counter-evidence (the need is already served elsewhere) caps a gap at MODERATE;
- for original-data and first-hand gains, the client's material is the gain: until it is collected the
  gain stays NEEDS_CLIENT_INPUT even when the capability itself is proven;
- outlines are linted for generic headings, vague points and filler phrases.

Unsupported claims are downgraded and reported back to the agent, not silently accepted. A topic is
READY_TO_WRITE once at least one STRONG/MODERATE gain is proven by the client's own pages; sections that
still wait for client material are listed with concrete collection tasks instead of blocking the page.

## Tools (18)

`dataforseo_connection` · `create_project` · `get_project` · `fetch_pages` · `read_page` ·
`submit_page_text` · `save_brief` · `plan_research` · `approve_budget` · `run_research_step` ·
`list_keywords` · `review_keywords` · `list_clusters` · `save_topics` · `get_topic_workspace` ·
`save_gap_analysis` · `save_page_plan` · `get_report`

## Data, cost and safety

- DataForSEO is the only paid source. `plan_research` shows a maximum cost; nothing is spent until the
  user approves a cap with `approve_budget`. Each request reserves its ceiling against the cap before it is
  sent and settles at the provider-reported cost. A request whose outcome is unknown (timeout) is never
  re-sent automatically. Responses are cached (keywords 30 days, SERPs 7 days), so resumes are free.
- Page reading is done by the planner itself: public http(s) only, every hop pinned to a public IP
  (no private networks, no DNS rebinding), size and time limits, no cookies or JavaScript. Pages it cannot
  read can be read by the agent in a browser and submitted; the report labels them as agent-read.
- Fetched page text is treated as untrusted data, never as instructions.

## Running locally

Python 3.9+, standard library only. MCP entry point: `python3 server/run.py`.

- Data directory: `PLUGIN_DATA` (set by Codex), else `~/.seo-topic-planner/`; override with
  `SEO_TOPIC_PLANNER_DATA_DIR`. It holds the research database and the DataForSEO token — never commit it.
- DataForSEO access: by default the planner connects through DataForSEO's OAuth (`dataforseo_connection`).
  For local/CI use set `SEO_TOPIC_PLANNER_PROVIDER=rest` with `DATAFORSEO_LOGIN` / `DATAFORSEO_PASSWORD`.

```bash
PYTHONPATH=server:server/tests python3 -m unittest discover -s server/tests
ruff check .
python3 validate_package.py
python3 build_package.py --output-dir /tmp/stp-build
```

2.0 is a rewrite: it uses a new database file and does not read 1.x research records. 2.1 reads 2.0
projects as they are; topics saved before 2.1 simply have no hub or role.

## License

MIT — see [LICENSE](LICENSE).
