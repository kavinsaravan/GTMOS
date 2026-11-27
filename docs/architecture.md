# Architecture

```
Browser ──> Next.js (apps/web) ──proxy (adds x-gtmos-token)──> API Gateway ──> Lambda (services/api)
                                                                                 │
              ┌──────────────┬───────────────┬───────────────┬──────────────────┼──────────────┐
           DynamoDB          S3         Anthropic / Tavily / Hunter       Neo4j Aura        Flexprice
        (single table)   (evidence)                                      (graph decisions)  (wallet, 402)
                                                                                 ▲
Local LangGraph worker (agents/)  ── REST polling + run events ──────────────────┘
   planner_start ──> researcher ──> reviewer ──> planner_finish
                         └─ abstain / fail ─> END ─┘
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

## Agent orchestration

Run status: `queued → planning → researching → reviewing → awaiting_approval | failed | abstained`. State changes are
conditional DynamoDB updates (single claim winner; no writes after a run finishes). The Planner, Researcher and
Reviewer are nodes of one LangGraph (`agents/graph.py`) run by a single worker process (`agents/worker.py`) that polls
`/api/orchestrations/queued` and claims runs. The LangGraph thread id is the run id, and a node's name is the step id;
both are stored on each trace event. A node that abstains or fails reports the final status and the conditional edge
ends the graph; a crash inside a node is caught and recorded as `failed`. The graph uses an in-memory checkpointer, so
state does not survive a worker restart. The Lambda does not run the agents; the worker calls the API for the real work.

## Uncertainty resolution

`services/resolution.py` handles `verify_first` leads. It rebuilds the decision row from DynamoDB, searches for the
missing fact (at most two targeted Tavily calls), appends verbatim-quoted evidence or marks the domain verified, and
re-runs `graph.decide`. Outcome `resolved` or `abstain`, with a known/uncertain/needed brief. Metered as `resolution`
(2 credits); max 2 attempts per lead. The Researcher agent runs it first; `abstain` ends the run as `abstained`.

## Not built

Explicit send (allowlist plus second confirmation), kanban moves from recorded dispatch, deployed frontend,
API token in Secrets Manager.
