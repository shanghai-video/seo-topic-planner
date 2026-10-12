# Lingyi Labs Codex plugins

Codex and Claude Code plugin marketplace published by [Lingyi Labs](https://lingyilabs.com).

| Plugin | What it does |
| --- | --- |
| [SEO Topic Planner](plugins/seo-topic-planner/README.md) | Evidence-led SEO topic research on DataForSEO: keyword discovery, SERP-overlap clustering, verified content gaps and page outlines built around a proven information gain. |

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

## License

MIT — see [LICENSE](LICENSE).
