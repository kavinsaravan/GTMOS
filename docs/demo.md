# Demo script (about 5 minutes)

Setup: API deployed, `apps/web` running locally, agents running (`agents/start.sh`), Flexprice wallet funded.

1. **Campaign and ICP.** Open a campaign. Show the ICP and its confidence per field; approve it.
2. **Discovery and leads.** Leads page: real companies with scored evidence quotes and source URLs. Point out the
   credit badge in the header dropping after discovery.
3. **Graph decision.** Lead detail → graph. Show the one relationship query that changes a lead from `contact_now`
   to `hold_rival_active` (or similar) with the reasons.
4. **Band run.** Command center → Band agent runs → pick a lead with no draft → Run agents. Watch the trace:
   Planner claims and opens the room, @Researcher, enrichment (3 credits), @Reviewer, draft (2 credits), @Planner,
   `Awaiting human approval`. Optionally open the room in Band and show the same messages.
5. **Human gate.** Open the draft: evidence facts, reviewer verdict. Approve it; a Gmail *draft* is created. State
   clearly that nothing is sent.
6. **402.** With a low wallet, repeat an operation and show the `Out of credits` message.

## Acceptance checklist

- [ ] `pytest` passes; CI and Snyk are green
- [ ] Unconfigured provider returns `not_configured`, not data
- [ ] Insufficient credits returns 402 and does not run the operation
- [ ] Replaying an `Idempotency-Key` does not charge twice
- [ ] Band trace ids match the messages in the Band room
- [ ] A run ends `awaiting_approval`; the draft is still `pending_review` or `blocked`
- [ ] Nothing is emailed; approval only creates a Gmail draft

## Caveats to say out loud

Band agents run locally. Explicit send is not built. Test and demo runs consume real Hunter, Tavily and Flexprice
credits.
