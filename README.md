# GTMOS

An AI go-to-market engine: describe a product, get an ICP, discover and score real companies, enrich contacts,
run one graph query that changes an outreach decision, and produce evidence-bound email drafts that a human
approves before anything touches Gmail. Built for HackwithBay 4.0.

Rules the system follows: live data only, no faked integrations (an unconfigured provider reports
`not_configured`), and **nothing is sent without human approval**. Gmail integration creates drafts only.

## Layout

| Path | What |
| --- | --- |
| `services/api/` | Python 3.12 Lambda behind API Gateway (routes in `app.py`) |
| `apps/web/` | Next.js / TypeScript / Tailwind / Radix UI. Server-side proxy keeps the API token off the browser |
| `agents/` | Three local Band agents: Planner, Researcher, Reviewer |
| `infra/` | `template.yaml` (SAM) and `flexprice_setup.py` (creates the metered features, idempotent) |
| `tests/` | pytest suite (providers are faked; no network) |
| `docs/` | `architecture.md` |

## Integrations

| Provider | Use | When missing |
| --- | --- | --- |
| Anthropic | ICP, drafting, review | rule-based fallback, labelled in the response |
| Tavily, Hunter.io | discovery evidence, contacts | `not_configured` |
| Neo4j Aura | relationship graph and decision query | `not_configured` |
| Gmail API | draft creation after approval | draft recorded as `not_connected` |
| Band | multi-agent handoffs via `band-sdk` | agents simply are not running |
| Flexprice | per-operation credits, 402 gating | runs unmetered, reports `configured: false` |

Secrets live in AWS Secrets Manager (`gtmos/providers`), never in the repo.

## Run it

```bash
# tests
python -m venv .venv && .venv/bin/pip install -r services/api/requirements.txt -r services/api/requirements-dev.txt
.venv/bin/python -m pytest tests -q

# deploy the API
sam build -t infra/template.yaml
sam deploy --stack-name gtmos --resolve-s3 --capabilities CAPABILITY_IAM --parameter-overrides ApiToken=<token>

# one-time Flexprice setup (creates the metered features)
AWS_PROFILE=gtmos PROVIDERS_SECRET_ID=gtmos/providers .venv/bin/python infra/flexprice_setup.py

# web app (needs GTMOS_API_URL and GTMOS_API_TOKEN in apps/web/.env.local)
cd apps/web && npm install && npm run dev

# Band agents (local processes; need AWS profile `gtmos` or BAND_* env vars)
python -m venv agents/.venv && agents/.venv/bin/pip install -r agents/requirements.txt
GTMOS_API_URL=https://<api-id>.execute-api.us-west-2.amazonaws.com/prod agents/start.sh agents/.venv/bin/python
```

## Billing (Flexprice)

Each metered call checks the wallet first and returns **402 `insufficient_credits`** if the balance is below the
cost, then runs, then debits idempotently (`Idempotency-Key` header, else the operation id). Costs: discovery 1,
enrichment 3, graph query 1, draft 2, uncertainty resolution 2. If Flexprice is configured but unreachable the call fails closed (502
`billing_unavailable`). Flexprice does not reject overdrafts itself, so the pre-check is ours.
`GET /api/system/billing` returns the live balance; the web app shows it in the campaign header.

## Band orchestration

`POST /api/campaigns/{id}/orchestrations {"lead_id": ...}` queues a run. The Planner (polling) claims it, opens a
real Band room with the other two agents, and @mentions the Researcher. The Researcher enriches the lead through the
API and @mentions the Reviewer, who generates the draft and @mentions the Planner. The run ends `awaiting_approval`.
Agents only receive @mentioned messages, so every handoff is a real Band message; the trace stores Band's room and
message ids. Agents never approve or send.

## Uncertainty resolution

When the graph says `verify_first`, `POST /api/campaigns/{id}/leads/{lead}/resolve` (2 credits) looks for the one
missing fact: a buying signal stated by a non-job-board source, or a page hosted on the lead's domain. It runs at
most two targeted Tavily searches, accepts only a sentence copied verbatim from a result that names the company,
and recomputes the decision with the same `graph.decide`. Found: the evidence is stored and the lead becomes
`contact_now`. Not found: it abstains and the response carries a brief (what we know, what was uncertain, what would
make it actionable). A lead can be researched twice at most. The Researcher agent calls it before enrichment; on
abstain the run ends `abstained` and no enrichment or draft credits are spent.

## Known limits

- Band agents hold websockets, so they run as local processes, not in Lambda.
- Credit check and debit are not atomic, so concurrent requests can overspend slightly. A replayed `Idempotency-Key`
  is not charged twice but the work still reruns.
- Exclusivity is a fixed rule (`hold_rival_active`), not a per-user policy; there is no per-campaign research budget
  beyond the wallet balance.
- Explicit send is not built; approval creates a Gmail draft only.
- Frontend is not deployed; run it locally against the live API.
