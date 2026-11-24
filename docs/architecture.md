# Architecture

```
Browser ──> Next.js (apps/web) ──proxy (adds x-gtmos-token)──> API Gateway ──> Lambda (services/api)
                                                                                 │
              ┌──────────────┬───────────────┬───────────────┬──────────────────┼──────────────┐
           DynamoDB          S3         Anthropic / Tavily / Hunter       Neo4j Aura        Flexprice
        (single table)   (evidence)                                      (graph decisions)  (wallet, 402)
                                                                                 ▲
Local Band agents (agents/)  ── REST polling + run events ───────────────────────┘
   Planner ──@mention──> Researcher ──@mention──> Reviewer ──@mention──> Planner
        └────────── real Band room (api.band.ai / band-sdk websocket) ──────────┘
```

## Backend

- One Lambda, route table in `services/api/app.py`. Every response uses the envelope
  `{status, data, warnings, source_ids, operation_id}` or `{status:"error", error:{code,message,details}}`.
- DynamoDB single table. PK `ORG#<org>#CAMPAIGN#<id>`; sort keys `META`, `ICP#`, `LEAD#`, `RUN#`, `EVT#`, `DRAFT#`,
  `ORCH#<run>`. Draft versions are immutable. Every state change writes an audit event (`EVT#`).
- Provider keys are read from Secrets Manager (`gtmos/providers`) at call time. A missing key gives a truthful
  `not_configured`, never a fake result.

## Human approval

Drafts are `pending_review` or `blocked` (the reviewer pass strips claims without evidence). Only a human
`POST .../approve` moves a draft on, and only then is a Gmail draft created. Agents have no approve or send path.

## Billing

`services/billing.metered(operation, ...)` wraps discover, enrich, draft and graph sync:
check balance (402 if short) → run → idempotent debit + usage event. The wallet is created lazily per org
(`gtmos-org-<org>`). Unconfigured: unmetered. Configured but unreachable: 502, fail closed. A debit failure after a
successful operation is logged as `billing.charge_failed` and does not fail the request.

## Band orchestration

Run status: `queued → planning → researching → reviewing → awaiting_approval | failed | abstained`. State changes are
conditional DynamoDB updates (single claim winner; no writes after a run finishes). Each agent is its own process
with its own Band websocket. A message carries `[gtmos run=… campaign=… lead=…]`, which links the Band message to
the GTMOS run. An agent only acts on a message from its expected upstream agent, dedupes by message id, and always
replies or calls `no_reply` so Band does not post a turn error. Lambda cannot hold a websocket, so the agents are
local processes that call the API for the real work.

## Uncertainty resolution

`services/resolution.py` handles `verify_first` leads. It rebuilds the decision row from DynamoDB, searches for the
missing fact (at most two targeted Tavily calls), appends verbatim-quoted evidence or marks the domain verified, and
re-runs `graph.decide`. Outcome `resolved` or `abstain`, with a known/uncertain/needed brief. Metered as `resolution`
(2 credits); max 2 attempts per lead. The Researcher agent runs it first; `abstain` ends the run as `abstained`.

## Not built

Explicit send (allowlist plus second confirmation), kanban moves from recorded dispatch, deployed frontend,
API token in Secrets Manager.
