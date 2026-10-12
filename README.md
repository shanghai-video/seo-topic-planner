# SEO Topic Planner

**Tell it your website and your business goal. It tells you which SEO pages to create or update — and what
to put in them that the pages already ranking on Google don't give the reader.**

A plugin for **Codex** and **Claude Code**, by [Lingyi Labs](https://lingyilabs.com). [中文说明](#中文说明)

## The problem it solves

Most SEO content plans are a keyword list plus a guess. You end up writing the eleventh page that says the
same thing as the ten already on page one of Google — and it doesn't rank.

SEO Topic Planner does the research a senior SEO strategist would do, and shows its evidence:

1. **What people search for** — keyword data from your seeds, your competitors' rankings and your own.
2. **What Google already ranks** — the top 10 results for each query, grouped into topics by the pages
   they share.
3. **What those pages say** — it actually reads the ranking pages, not just their titles.
4. **What they fail to give the reader** — every "this is missing" claim is backed by a quote or a fully
   read page; gaps that competitors already cover well are thrown out.
5. **What only you can add** — proven from your own site, or listed as concrete material to collect
   (data, examples, screenshots, customer stories).
6. **The page to write** — create / update / merge / skip, with an outline built around that difference.

## What you get

- A **page map**: pillar pages, supporting pages and how they link to each other.
- For each page: the search demand, what the top results already cover, the content gaps with their
  evidence, and a section-by-section **outline**.
- A **to-do list** of what your team must supply before writing.
- A **client-ready report** in English or Chinese.

It does **not** write the full articles, audit your site's technical SEO or track rankings.

## Who it's for

Marketing teams, SEO consultants and agencies planning content for a B2B or product website — anyone who
needs to justify *why* a page is worth writing.

## Cost and data

- Keyword and Google results data come from **[DataForSEO](https://dataforseo.com)**, billed to your own
  DataForSEO account. A typical research run costs **about $1–3**.
- It shows a cost estimate first and **never spends more than the budget you approve**. Cached results
  are reused for free.
- You connect DataForSEO through its own sign-in page in your browser — you never paste a password into
  the chat.
- Everything runs **on your computer**. Research data and your DataForSEO authorization stay in a local
  folder. The plugin only talks to DataForSEO and the web pages it reads — nothing is sent to Lingyi Labs.

## Install

Requires **Python 3.9+** (available as `python3`) and a **DataForSEO account**.

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

## Get started

After installing, just ask, for example:

> Research SEO topics for my website and tell me which pages to create or update, with the evidence.

It will connect DataForSEO, ask for your website, target market, language and business goal, show you a
cost estimate, and then work through the research step by step.

Technical details — how each claim is checked, the 18 tools, running the tests — are in the
[plugin README](plugins/seo-topic-planner/README.md).

## 中文说明

**告诉它你的网站和业务目标，它告诉你该新建或更新哪些 SEO 页面，以及每个页面应该写哪些"排在 Google 前面的页面没给读者的内容"。**

适用于 Codex 和 Claude Code，由 [Lingyi Labs](https://lingyilabs.com) 发布。

- **它做什么**：用 DataForSEO 拿关键词和 Google 前 10 结果 → 按共享的排名页面把关键词归成主题 → 真正读完排名页面 → 找出它们没讲好的地方（每条都要有原文引用作证据）→ 结合你网站能证明的能力，给出"新建 / 更新 / 合并 / 跳过"的建议和页面大纲。
- **你会拿到**：页面地图（支柱页 + 支撑页 + 内链）、每个页面的需求数据、竞品覆盖情况、内容缺口和证据、分段大纲、需要你团队补充的素材清单，以及中文或英文的客户报告。
- **它不做**：写完整文章、技术 SEO 审计、排名追踪。
- **费用**：DataForSEO 的数据费用记在你自己的账户上，一次典型研究约 1–3 美元；先给预估，不会超过你批准的预算。
- **数据**：全部在本机运行，研究数据和 DataForSEO 授权只保存在本地。插件只访问 DataForSEO 和它要读取的网页，不会把数据发给 Lingyi Labs。
- **安装**：见上方 [Install](#install)，需要 Python 3.9+ 和 DataForSEO 账号。

## License

MIT — see [LICENSE](LICENSE).
