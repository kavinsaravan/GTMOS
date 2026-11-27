"""The LangGraph agent chain, driven against a fake GTMOS API (no network, no LangGraph services)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.graph import build_graph, run_chain  # noqa: E402
from agents.worker import process  # noqa: E402

RUN = {"id": "orc_1", "campaign_id": "cmp_1", "lead_id": "lead_1", "lead_name": "Acme"}


class FakeApi:
    """Records trace events and answers the three work endpoints from a canned table."""

    def __init__(self, resolve=(409, {"error": {"code": "nothing_to_resolve"}}), enrich=None, draft=None):
        self.events, self.calls = [], []
        self.responses = {
            "resolve": resolve,
            "enrich": enrich or (200, {"data": {"domain": "acme.com",
                                                "enrichment": {"usable_contacts": 2, "status": "ok"}}}),
            "draft": draft or (201, {"data": {"id": "drf_1", "status": "pending_review",
                                              "reviewer": {"status": "pass"}}}),
        }

    def call(self, method, path, body=None, headers=None, timeout=90):
        self.calls.append((method, path, (headers or {}).get("Idempotency-Key")))
        if path.endswith("/claim"):
            return self.responses.get("claim", (201, {}))
        return self.responses[path.rsplit("/", 1)[1]]

    def event(self, campaign, run_id, agent, kind, **fields):
        self.events.append({"agent": agent, "kind": kind, **fields})
        return {}

    def kinds(self):
        return [(e["agent"], e["kind"]) for e in self.events]

    def statuses(self):
        return [e["status"] for e in self.events if "status" in e]


def run(api):
    return run_chain(build_graph(api), RUN)


def test_happy_path_ends_awaiting_approval_after_each_handoff():
    api = FakeApi()
    run(api)
    assert api.kinds() == [
        ("planner", "thread_started"), ("planner", "handoff_sent"),
        ("researcher", "message_received"), ("researcher", "work_done"), ("researcher", "handoff_sent"),
        ("reviewer", "message_received"), ("reviewer", "work_done"), ("reviewer", "handoff_sent"),
        ("planner", "message_received"), ("planner", "completed")]
    assert api.statuses() == ["researching", "reviewing", "awaiting_approval"]
    assert all(e["thread_id"] == "orc_1" and e["step_id"] for e in api.events)
    assert api.events[-3]["draft_id"] == "drf_1"


def test_work_calls_are_idempotent_per_run_and_never_approve_or_send():
    api = FakeApi()
    run(api)
    assert [(m, p.rsplit("/", 1)[1], k) for m, p, k in api.calls] == [
        ("POST", "resolve", "orc_1-resolve"), ("POST", "enrich", "orc_1-enrich"), ("POST", "draft", "orc_1-draft")]
    assert not any("approve" in p or "send" in p for _, p, _ in api.calls)


def test_abstain_ends_the_run_before_enrichment_or_draft():
    resolve = (201, {"data": {"outcome": "abstain", "steps": [],
                              "brief": {"uncertain": ["No buying signal."], "would_make_actionable": ["A hiring post"]}}})
    api = FakeApi(resolve=resolve)
    run(api)
    assert api.statuses() == ["researching", "abstained"]
    assert api.events[-1]["kind"] == "abstained" and "no enrichment or draft credits" in api.events[-1]["summary"]
    assert [p.rsplit("/", 1)[1] for _, p, _ in api.calls] == ["resolve"]


def test_resolved_uncertainty_continues_the_chain():
    resolve = (201, {"data": {"outcome": "resolved", "brief": {},
                              "steps": [{"found": True, "detail": "hiring SDRs"}]}})
    api = FakeApi(resolve=resolve)
    run(api)
    assert ("researcher", "uncertainty_resolved") in api.kinds()
    assert api.statuses()[-1] == "awaiting_approval"


def test_enrichment_failure_fails_the_run_without_drafting():
    api = FakeApi(enrich=(402, {"error": {"code": "insufficient_credits", "message": "low balance"}}))
    run(api)
    assert api.statuses() == ["researching", "failed"]
    assert "insufficient_credits" in api.events[-1]["summary"]
    assert all(not p.endswith("/draft") for _, p, _ in api.calls)


def test_no_usable_contacts_fails_the_run_without_drafting():
    api = FakeApi(enrich=(200, {"data": {"domain": "acme.com", "enrichment": {"usable_contacts": 0, "status": "ok"}}}))
    run(api)
    assert api.statuses() == ["researching", "failed"]
    assert all(not p.endswith("/draft") for _, p, _ in api.calls)


def test_blocked_draft_still_goes_to_human_review():
    api = FakeApi(draft=(201, {"data": {"id": "drf_1", "status": "blocked", "reviewer": {"status": "fail"}}}))
    run(api)
    assert api.statuses()[-1] == "awaiting_approval"
    assert "must edit and re-review" in next(e for e in api.events if e["agent"] == "reviewer"
                                             and e["kind"] == "work_done")["summary"]


def test_a_crash_in_a_node_marks_the_run_failed_instead_of_leaving_it_active():
    api = FakeApi(draft=(201, {"data": {}}))      # missing keys -> KeyError inside the reviewer node
    run(api)
    assert api.statuses()[-1] == "failed"
    assert "crashed" in api.events[-1]["summary"]


def test_worker_skips_a_run_it_could_not_claim():
    api = FakeApi()
    api.responses["claim"] = (409, {"error": {"code": "already_claimed"}})
    assert process(api, build_graph(api), RUN) is False
    assert api.events == []


def test_worker_claims_then_runs_the_chain():
    api = FakeApi()
    assert process(api, build_graph(api), RUN) is True
    assert api.calls[0][1].endswith("/orchestrations/orc_1/claim")
    assert api.statuses()[-1] == "awaiting_approval"
