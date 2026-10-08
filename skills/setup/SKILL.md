---
name: seo-topic-planner-setup
description: Run after SEO Topic Planner is installed to connect live DataForSEO research through the Planner's own browser authorization, without collecting credentials in chat.
---

# SEO Topic Planner setup

Use this skill after the plugin is installed or when the user asks to connect
live DataForSEO research. The Planner owns the DataForSEO authorization: it
registers a public OAuth client, opens the DataForSEO consent page in the user's
browser, and stores the resulting short-lived token in a user-only local file.
Never ask the user for an API login, password, or token in chat.

There is no demo or fake data mode. Until the connection is complete, the
Planner reports `AUTH_REQUIRED` and refuses research rather than returning
sample data.

## First-run flow

1. Tell the user the Planner needs their own DataForSEO account and that
   research costs are billed by DataForSEO. Do not describe any sample output as
   market data.
2. Call `connect_dataforseo`. It returns an `authorization_url` and reports
   `status: AWAITING_AUTHORIZATION`. Ask the user to approve the DataForSEO
   consent screen in the browser tab that opens. If `browser_opened` is false,
   share the returned `authorization_url` for the user to open manually.
3. Call `complete_dataforseo_connection`. It waits for the browser callback,
   exchanges the code, stores the token locally, and reports `LIVE / READY`.
4. Verify with `get_data_provider_status`. Treat only `data_mode: LIVE` and
   `status: READY` as proof that live data is available. `AUTH_REQUIRED`,
   `MISCONFIGURED`, and `UNKNOWN` all mean the user cannot research yet.
5. Before any paid research, help the user build and confirm a brand context
   (`create_brand_profile` then `confirm_brand_context`) and discover the brand's
   entities (`open_brand_entity_card`, `create_brand_entity`,
   `confirm_brand_entity`), then call `estimate_topic_research` and show the
   scope, policy ceiling, and budget cap. Do not spend until the user explicitly
   confirms the exact estimate.

## Spending and safety boundaries

- A live estimate is a conservative approval ceiling, not a DataForSEO quote.
  `run_topic_research` aborts if observed cost exceeds the approved budget cap.
- `budget_cap_usd` defaults to 0 for safety: a paid run needs an explicit,
  non-zero budget cap.
- Browser / Computer Use / yt-dlp collection performed by the host agent is not
  billed through the Planner budget; DataForSEO is the only billed provider.
- `disconnect_dataforseo` deletes the local authorization and returns the
  Planner to `AUTH_REQUIRED`; use it if the user wants to revoke access from
  this machine.
- Never place DataForSEO credentials or tokens in tool arguments, plugin files,
  records, logs, or chat.
