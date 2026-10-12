---
name: seo-topic-planner-setup
description: Run after SEO Topic Planner is installed to connect DataForSEO through the Planner's own browser authorization, without collecting credentials in chat.
---

# SEO Topic Planner setup

The Planner needs the user's own DataForSEO account; DataForSEO bills research directly to it. The
Planner owns the authorization: it opens DataForSEO's consent page in the browser and stores a short-lived
token in a user-only local file. Never ask for a DataForSEO login, password or token in chat.

1. Call `dataforseo_connection` with `action: "status"`. If `ready` is true, setup is done.
2. Otherwise call `action: "connect"`. Ask the user to approve the consent page that opens; if
   `browser_opened` is false, give them the returned `authorization_url`.
3. Call `action: "complete"`; it waits for the browser callback and reports the status.
4. Confirm `ready: true`. `action: "disconnect"` removes the local token.

Costs: a typical research run uses $1–3 of DataForSEO credit. Every run shows an estimate first and stops
at the cap the user approves. Page reading done by the Planner or in the browser is not billed.
