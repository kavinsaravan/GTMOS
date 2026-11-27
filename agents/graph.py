"""The GTMOS agent chain as a LangGraph state graph.

    planner_start -> researcher -> reviewer -> planner_finish -> END
                         |             |
                         +--> END      +--> END      (abstained / failed: the run ends there)

Each node is one role. A handoff is an edge: the node that hands off records a `handoff_sent` trace
event and the next node records `message_received`, so the run trace in the GTMOS API still shows who
passed what to whom. The LangGraph thread id is the run id; the step id is the node name.

No node approves or sends anything. A finished run ends at `awaiting_approval` (a human reviews the
draft) or, when a lead's missing fact cannot be found, `abstained` (no contact, no draft).
"""
from __future__ import annotations

import logging
from typing import Any, Callable, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

log = logging.getLogger("gtmos.graph")


class RunState(TypedDict, total=False):
    campaign: str
    run_id: str
    lead: str
    lead_name: str
    summary: str     # last handoff text, passed along to the next node
    draft_id: str
    stop: bool       # set when the run ended inside a node (abstained or failed)


def _next_or_end(state: RunState) -> str:
    return "end" if state.get("stop") else "next"


def build_graph(api: Any):
    """Compile the chain. `api` is anything with `call(method, path, body, headers)` and
    `event(campaign, run_id, agent, kind, **fields)` (agents.common.Gtmos in production)."""

    def report(state: RunState, agent: str, kind: str, step: str, **fields) -> None:
        api.event(state["campaign"], state["run_id"], agent, kind,
                  thread_id=state["run_id"], step_id=step, **fields)

    def fail(state: RunState, agent: str, step: str, text: str, status: str = "failed",
             kind: str = "work_failed") -> RunState:
        report(state, agent, kind, step, status=status, summary=text)
        return {"stop": True}

    def guarded(agent: str, step: str, fn: Callable[[RunState], RunState]) -> Callable[[RunState], RunState]:
        """A crash inside a node must not leave the run stuck in an active status."""
        def run(state: RunState) -> RunState:
            try:
                return fn(state)
            except Exception as exc:
                log.exception("%s failed for run %s", step, state.get("run_id"))
                return fail(state, agent, step, f"{agent.capitalize()} crashed: {type(exc).__name__}")
        return run

    # ---- planner: open the thread and hand the lead to the Researcher
    def planner_start(state: RunState) -> RunState:
        report(state, "planner", "thread_started", "planner_start",
               summary="Planner started a LangGraph thread with the Researcher and Reviewer")
        text = f"Planner handed {state['lead_name']} to the Researcher: enrich and find decision-maker contacts."
        report(state, "planner", "handoff_sent", "planner_start", status="researching", mentioned="researcher",
               summary=text)
        return {}

    # ---- researcher: resolve any uncertainty, then enrich
    def researcher(state: RunState) -> RunState:
        cid, rid, lead = state["campaign"], state["run_id"], state["lead"]
        report(state, "researcher", "message_received", "researcher",
               summary="Researcher received the Planner's handoff")

        status, body = api.call("POST", f"/api/campaigns/{cid}/leads/{lead}/resolve", {},
                                {"Idempotency-Key": f"{rid}-resolve"})
        if not (status == 409 and body.get("error", {}).get("code") == "nothing_to_resolve"):
            if status >= 400:
                err = body.get("error", {})
                return fail(state, "researcher", "researcher",
                            f"Uncertainty check failed ({err.get('code')}): {err.get('message')}")
            r = body["data"]
            brief = r["brief"]
            if r["outcome"] == "abstain":
                text = ("Abstaining: " + " ".join(brief["uncertain"]) + " Still needed: "
                        + "; ".join(brief["would_make_actionable"])
                        + ". Not contacting; no enrichment or draft credits spent.")
                return fail(state, "researcher", "researcher", text, status="abstained", kind="abstained")
            found = "; ".join(s["detail"] for s in r["steps"] if s["found"])
            report(state, "researcher", "uncertainty_resolved", "researcher",
                   summary=f"Found the missing fact ({found}); lead moves from verify_first to contact_now.")

        status, body = api.call("POST", f"/api/campaigns/{cid}/leads/{lead}/enrich", {},
                                {"Idempotency-Key": f"{rid}-enrich"})
        if status >= 400:
            err = body.get("error", {})
            return fail(state, "researcher", "researcher",
                        f"Enrichment failed ({err.get('code')}): {err.get('message')}")
        data = body["data"]["enrichment"]
        summary = (f"Enriched {body['data']['domain']}: {data['usable_contacts']} usable contact(s), "
                   f"status {data['status']}.")
        report(state, "researcher", "work_done", "researcher", summary=summary)
        if data["usable_contacts"] == 0:
            return fail(state, "researcher", "researcher", "No usable contacts; nothing to draft")
        text = f"{summary} Please draft and review the outreach."
        report(state, "researcher", "handoff_sent", "researcher", status="reviewing", mentioned="reviewer",
               summary=text)
        return {"summary": text}

    # ---- reviewer: evidence-bound draft + built-in reviewer pass
    def reviewer(state: RunState) -> RunState:
        cid, rid, lead = state["campaign"], state["run_id"], state["lead"]
        report(state, "reviewer", "message_received", "reviewer",
               summary="Reviewer received the Researcher's handoff")
        status, body = api.call("POST", f"/api/campaigns/{cid}/leads/{lead}/draft", {},
                                {"Idempotency-Key": f"{rid}-draft"})
        if status >= 400:
            err = body.get("error", {})
            return fail(state, "reviewer", "reviewer", f"Drafting failed ({err.get('code')}): {err.get('message')}")
        draft = body["data"]
        verdict = draft.get("reviewer", {}).get("status", "unknown")
        if draft["status"] == "blocked":
            follow_up = ("The reviewer pass blocked it; a human must edit and re-review it before anything "
                         "is created in Gmail.")
        else:
            follow_up = "It needs human approval before anything is created in Gmail."
        summary = f"Draft {draft['id']} is {draft['status']} (reviewer verdict: {verdict}). {follow_up}"
        report(state, "reviewer", "work_done", "reviewer", draft_id=draft["id"], summary=summary)
        report(state, "reviewer", "handoff_sent", "reviewer", mentioned="planner", draft_id=draft["id"],
               summary=summary[:300])
        return {"summary": summary, "draft_id": draft["id"]}

    # ---- planner: close the loop (a human approval is still required)
    def planner_finish(state: RunState) -> RunState:
        report(state, "planner", "message_received", "planner_finish",
               summary="Planner received the Reviewer's report")
        report(state, "planner", "completed", "planner_finish", status="awaiting_approval",
               summary="Chain complete. A human reviews the draft; nothing is sent automatically.")
        return {}

    g = StateGraph(RunState)
    g.add_node("planner_start", guarded("planner", "planner_start", planner_start))
    g.add_node("researcher", guarded("researcher", "researcher", researcher))
    g.add_node("reviewer", guarded("reviewer", "reviewer", reviewer))
    g.add_node("planner_finish", guarded("planner", "planner_finish", planner_finish))
    g.add_edge(START, "planner_start")
    g.add_conditional_edges("planner_start", _next_or_end, {"next": "researcher", "end": END})
    g.add_conditional_edges("researcher", _next_or_end, {"next": "reviewer", "end": END})
    g.add_conditional_edges("reviewer", _next_or_end, {"next": "planner_finish", "end": END})
    g.add_edge("planner_finish", END)
    return g.compile(checkpointer=InMemorySaver())


def run_chain(graph, run: dict) -> RunState:
    """Run one claimed run through the graph. The LangGraph thread id is the run id."""
    state: RunState = {"campaign": run["campaign_id"], "run_id": run["id"], "lead": run["lead_id"],
                       "lead_name": run.get("lead_name") or run["lead_id"]}
    return graph.invoke(state, {"configurable": {"thread_id": run["id"]}})
