# Page action, outline and self-review

## Step 7 — Choose the page action

First judge every `site_candidates` page from the workspace (read the body if it was fetched):

- `SAME_JOB` — it already serves this reader and intent. → **UPDATE** it (target_url), not CREATE.
- `PARTIAL` — overlaps (e.g. a product page that mentions the topic). Usually CREATE + internal link, or
  UPDATE if it is a content page with the same intent.
- `DIFFERENT_JOB` — shares words only.

Then:

| Action | When |
|---|---|
| UPDATE | A client page serves the same job; the gains go into it. Strongest when it already ranks 5–20. |
| CREATE | No client page serves the job, the SERP intent is something the client can serve, and at least one STRONG/MODERATE gain exists. |
| MERGE | Two client pages compete for the same cluster; consolidate into target_url. |
| SKIP | No real gain, intent the client can't serve, or demand too weak to justify the work. Say which. |

## Writing the outline

The outline is a brief for a writer, not a table of contents. Two kinds of sections:

- **BASELINE** — what every ranking page covers. Readers expect it; keep it short and precise, and note
  which pages cover it well. Do not pad it to look comprehensive.
- **GAIN** — delivers a STRONG/MODERATE gap. This is where the page wins. Cite `gap_ids`.

Order sections by the reader's path through the job, not by gain/baseline. Each section needs:

- a heading that states the specific thing ("Rolling back a misbehaving agent in production"), never a
  generic label ("Best practices", "Benefits of X", "Introduction", "Conclusion");
- `reader_question` it answers;
- 2–6 `points` a writer can execute without further research: the claim, the number, the step, the
  condition, the example, the source to use. Each point should contain at least one of: a number, a named
  tool/standard/role, a procedure step, a condition ("only when…"), a trade-off, or a source.

Title and H1 promise the gain, not the keyword alone. The `angle` is one sentence: why this page is better
for the reader than the current top results. If you cannot write it without "comprehensive", "ultimate"
or "everything you need", you do not have an angle yet.

Match `page_type` to what ranks (a SERP of comparisons wants a comparison), unless the gain is precisely
a better format — then say so in the angle. `page_type` is free text; use a common name (guide,
comparison, tutorial, glossary, case study, product page, calculator, template, generator, configurator,
interactive checklist) so the planner recognises it.

## Page format: text, hybrid or tool

The server derives `page_format` from the page type and the gains the outline uses:

| Format | When | Rule |
|---|---|---|
| TEXT | Every gain is information | With an AI Overview on the query and no STRONG gain, expect a warning: Google's summary may replace the visit. Consider a utility gain, or SKIP. |
| HYBRID | A GAIN section delivers a `utility` gap inside a content page | The default for tools. Say in the angle what the reader enters and gets back. |
| TOOL | `page_type` is a tool (calculator, generator…) | Only when a tool query (`... calculator`, `... template`, `... generator`, `... checklist`) with search volume is among the topic's keywords. Otherwise CREATE is flagged — build a hybrid page instead. |

When the SERP is informational and the gain is a utility, the page is an informational page with the tool
embedded, not a tool page. The planner plans and argues for the tool; building it is the client's work
(the collection task names who).

## Internal links (pages in a hub)

`internal_links`: each with `target` (another topic id, or a URL on the client's site), `anchor_intent`
(what the reader gets by clicking) and `section` (where it sits). The server checks:

- every SUPPORT, COMMERCIAL and NO_SEARCH page links back to its pillar;
- the pillar links to every page in its hub;
- somewhere in the hub a page links to the commercial page or to the client's product pages
  (`hub_checks` until it does) — otherwise readers have no path to conversion;
- targets are real topics or client URLs. Links to other hubs are welcome but not required.

## No overlap between pages

If a GAIN section repeats one another planned page already delivers (same subject in heading and gap
statement), the plan gets an overlap warning. Pick one owner page for that gain — usually the narrower
support page — and turn the other into a short mention plus a link.

### Bad vs. good

✗ H2 "What is AI agent deployment" · points: "Define deployment", "Explain why it matters"
✓ H2 "What changes when an agent goes from notebook to production" (BASELINE) · points: "Three things the
  top 5 guides all list: packaging, tool credentials, monitoring — one paragraph each, link out to docs";
  "Add the one they skip: who is on call when the agent misbehaves (from gap g1)"

✗ H2 "Best practices for safe deployment" · points: "Use monitoring", "Test thoroughly"
✓ H2 "Rollback: the 3 ways to undo a bad agent release and when each fails" (GAIN g1) · points:
  "Versioned snapshot restore — Acme one-click (quote product page), RTO under a minute";
  "Feature-flag kill switch — fast but leaves state half-applied; use when the agent writes to queues";
  "Forum case (e1): agent kept emailing customers for 2 hours because there was no kill switch"

## Self-review before the report

Answer honestly for each topic; fix or downgrade what fails.

1. Could a writer produce every GAIN section from the top 5 results alone? → then it is not a gain.
2. Does every GAIN cite a gap the server rated STRONG/MODERATE, and does the evidence line make sense
   to a sceptical client?
3. Is any claim about the client's product not backed by a quote from its own pages? → move it to a
   collection task.
4. Is any user pain invented? Is every "readers struggle with" backed by a quote or PAA?
5. Did you treat volume, KD, CPC or "competitors don't mention it" as proof of a gap?
6. Would UPDATE, MERGE or SKIP be more honest than CREATE?
7. Any filler ("in today's fast-paced", "unlock", "seamless", "comprehensive guide") in headings or
   points? Rewrite.
8. Are the collection tasks specific enough to send to the client as-is?
9. In a hub: does each page stay in its role (the pillar answers the broad intent and routes on; supports
   go deep), are the links in place, and does each gain live on exactly one page?
10. For a tool: is it embedded unless a tool query with volume justifies a standalone page, and are the
    data, maintainer and product proof named?
