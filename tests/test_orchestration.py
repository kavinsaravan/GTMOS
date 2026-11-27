import pytest
from conftest import call
from test_discovery import approved_campaign, world  # noqa: F401  (world is a fixture)


@pytest.fixture
def run(table, world):  # noqa: F811
    cid = approved_campaign()
    call("POST", f"/api/campaigns/{cid}/discover")
    lead = call("GET", f"/api/campaigns/{cid}/leads")[1]["data"][0]
    status, body = call("POST", f"/api/campaigns/{cid}/orchestrations", {"lead_id": lead["id"]})
    assert status == 201
    return cid, lead, body["data"]


def ev(cid, rid, **kw):
    return call("POST", f"/api/campaigns/{cid}/orchestrations/{rid}/events", kw)


def test_run_is_queued_and_listed(run):
    cid, lead, r = run
    assert r["status"] == "queued" and r["trace"] == [] and r["lead_id"] == lead["id"]
    assert [x["id"] for x in call("GET", f"/api/campaigns/{cid}/orchestrations")[1]["data"]] == [r["id"]]
    assert [x["id"] for x in call("GET", "/api/orchestrations/queued")[1]["data"]] == [r["id"]]


def test_unknown_lead_is_404_and_missing_lead_id_is_422(table, world):  # noqa: F811
    cid = approved_campaign()
    assert call("POST", f"/api/campaigns/{cid}/orchestrations", {"lead_id": "lead_nope"})[0] == 404
    assert call("POST", f"/api/campaigns/{cid}/orchestrations", {})[0] == 422


def test_duplicate_active_run_for_same_lead_is_rejected(run):
    cid, lead, _ = run
    status, body = call("POST", f"/api/campaigns/{cid}/orchestrations", {"lead_id": lead["id"]})
    assert status == 409 and body["error"]["code"] == "run_in_progress"


def test_claim_is_single_winner(run):
    cid, _, r = run
    path = f"/api/campaigns/{cid}/orchestrations/{r['id']}/claim"
    status, body = call("POST", path, {})
    assert status == 201 and body["data"]["status"] == "planning"
    assert body["data"]["trace"][0]["kind"] == "claimed"
    status, body = call("POST", path, {})
    assert status == 409 and body["error"]["code"] == "already_claimed"
    assert call("GET", "/api/orchestrations/queued")[1]["data"] == []


def test_handoff_trace_records_thread_and_step_ids_and_status(run):
    cid, _, r = run
    status, body = ev(cid, r["id"], agent="planner", kind="handoff_sent", status="researching",
                      thread_id=r["id"], step_id="planner_start", mentioned="researcher",
                      summary="Planner handed off to Researcher")
    assert status == 201
    d = body["data"]
    assert d["status"] == "researching" and d["thread_id"] == r["id"]
    t = d["trace"][-1]
    assert (t["agent"], t["kind"], t["step_id"], t["mentioned"]) == ("planner", "handoff_sent", "planner_start", "researcher")


def test_runs_recorded_with_old_band_ids_still_display(run):
    from persistence import dynamo

    cid, _, r = run
    dynamo.transition_orch("demo", cid, r["id"], event={
        "at": dynamo.now_iso(), "agent": "planner", "kind": "room_created", "summary": "old run",
        "band_room_id": "room-1", "band_message_id": "msg-1"}, fields={"band_room_id": "room-1"})
    d = call("GET", f"/api/campaigns/{cid}/orchestrations")[1]["data"][0]
    assert d["thread_id"] == "room-1" and d["trace"][-1]["thread_id"] == "room-1"
    assert d["trace"][-1]["step_id"] == "msg-1"


def test_event_validation(run):
    cid, _, r = run
    assert ev(cid, r["id"], agent="hacker", kind="completed")[0] == 422
    assert ev(cid, r["id"], agent="planner", kind="nonsense")[0] == 422
    assert ev(cid, r["id"], agent="planner", kind="completed", status="approved")[0] == 422
    assert ev(cid, "orc_missing", agent="planner", kind="completed")[0] == 404


def test_finished_run_cannot_be_reopened(run):
    cid, _, r = run
    assert ev(cid, r["id"], agent="reviewer", kind="completed", status="awaiting_approval")[0] == 201
    status, body = ev(cid, r["id"], agent="planner", kind="handoff_sent", status="researching")
    assert status == 409 and body["error"]["code"] == "run_finished"


def test_abstained_run_is_terminal(run):
    cid, _, r = run
    status, body = ev(cid, r["id"], agent="researcher", kind="abstained", status="abstained")
    assert status == 201 and body["data"]["status"] == "abstained"
    assert ev(cid, r["id"], agent="researcher", kind="uncertainty_resolved")[0] == 201   # trace-only events still fine
    status, body = ev(cid, r["id"], agent="reviewer", kind="completed", status="awaiting_approval")
    assert status == 409 and body["error"]["code"] == "run_finished"


def test_requires_auth(run):
    cid, _, r = run
    assert call("GET", "/api/orchestrations/queued", token=None)[0] == 401
    assert call("POST", f"/api/campaigns/{cid}/orchestrations/{r['id']}/claim", {}, token="bad")[0] == 401
