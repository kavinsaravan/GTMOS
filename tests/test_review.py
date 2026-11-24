import pytest
from conftest import call
from test_discovery import world  # noqa: F401  (world is a fixture)
from test_outreach import actions, approved_campaign, lead_for, make_draft, ow  # noqa: F401

from persistence import dynamo


def drafts(cid):
    return {d["version"]: d for d in call("GET", f"/api/campaigns/{cid}/drafts")[1]["data"]}


def act(cid, did, verb, body=None):
    return call("POST", f"/api/campaigns/{cid}/drafts/{did}/{verb}", body)


@pytest.fixture
def pending(table, ow):  # noqa: F811
    cid = approved_campaign()
    lead = lead_for(cid)
    d = make_draft(cid, lead)[1]["data"]
    return cid, lead, d


# ------------------------------------------------------------------ approve
def test_approve_records_actor_and_time_and_audits(pending):
    cid, _, d = pending
    status, body = act(cid, d["id"], "approve")
    out = body["data"]
    assert status == 201 or status == 200
    assert out["status"] == "approved" and out["approved_by"] == "human:founder" and out["approved_at"]
    ev = [e for e in call("GET", f"/api/campaigns/{cid}/events")[1]["data"] if e["action"] == "draft.approved"]
    assert len(ev) == 1 and ev[0]["actor"] == "human:founder"


def test_approve_is_idempotent_and_audited_once(pending):
    cid, _, d = pending
    first = act(cid, d["id"], "approve")[1]["data"]
    again = act(cid, d["id"], "approve")[1]["data"]
    assert again["approved_at"] == first["approved_at"]
    assert actions(cid).count("draft.approved") == 1


def test_blocked_draft_cannot_be_approved(table, ow):  # noqa: F811
    ow["writer"]["fact_ids"] = [9]
    cid = approved_campaign()
    d = make_draft(cid, lead_for(cid))[1]["data"]
    status, body = act(cid, d["id"], "approve")
    assert status == 409 and body["error"]["code"] == "invalid_transition"
    assert drafts(cid)[1]["status"] == "blocked"


def test_superseded_draft_cannot_be_approved(pending):
    cid, lead, d = pending
    make_draft(cid, lead)                           # creates v2, supersedes v1
    status, body = act(cid, d["id"], "approve")
    assert status == 409 and body["error"]["code"] == "invalid_transition"


# ------------------------------------------------------------------ reject
def test_reject_is_terminal_and_prevents_approval(pending):
    cid, _, d = pending
    out = act(cid, d["id"], "reject", {"reason": "wrong persona"})[1]["data"]
    assert out["status"] == "rejected" and out["rejection_reason"] == "wrong persona"
    assert out["rejected_by"] == "human:founder"
    status, body = act(cid, d["id"], "approve")
    assert status == 409 and body["error"]["code"] == "invalid_transition"
    assert call("PATCH", f"/api/campaigns/{cid}/drafts/{d['id']}", {"subject": "New subject"})[0] == 409
    assert drafts(cid)[1]["status"] == "rejected"


def test_reject_idempotent_and_reason_optional_and_not_in_audit(pending):
    cid, _, d = pending
    act(cid, d["id"], "reject", {"reason": "contains secret-ish reason"})
    assert act(cid, d["id"], "reject")[0] in (200, 201)
    ev = [e for e in call("GET", f"/api/campaigns/{cid}/events")[1]["data"] if e["action"] == "draft.rejected"]
    assert len(ev) == 1 and "secret-ish" not in str(ev[0]["details"])


def test_approved_draft_cannot_be_rejected(pending):
    cid, _, d = pending
    act(cid, d["id"], "approve")
    assert act(cid, d["id"], "reject")[0] == 409


def test_blocked_draft_can_be_rejected(table, ow):  # noqa: F811
    ow["writer"]["fact_ids"] = [9]
    cid = approved_campaign()
    d = make_draft(cid, lead_for(cid))[1]["data"]
    assert act(cid, d["id"], "reject", {"reason": "bad"})[1]["data"]["status"] == "rejected"


# ------------------------------------------------------------------ edit
def test_edit_creates_new_version_keeps_history(pending):
    cid, _, d = pending
    status, body = call("PATCH", f"/api/campaigns/{cid}/drafts/{d['id']}",
                        {"body": "Hi there,\n\nAcme is hiring five platform engineers. Open to a chat?"})
    v2 = body["data"]
    assert v2["version"] == 2 and v2["source"] == "human" and v2["parent_version"] == 1
    assert v2["status"] == "pending_review" and v2["edited_by"] == "human:founder"
    assert v2["id"] != d["id"] and v2["subject"] == d["subject"] and v2["reviewer"] == d["reviewer"]
    all_ = drafts(cid)
    assert all_[1]["status"] == "superseded" and all_[1]["body"] == d["body"]      # original untouched
    assert "draft.edited" in actions(cid)


def test_edited_version_can_be_approved_and_old_one_cannot(pending):
    cid, _, d = pending
    v2 = call("PATCH", f"/api/campaigns/{cid}/drafts/{d['id']}", {"subject": "Hiring at Acme"})[1]["data"]
    assert act(cid, d["id"], "approve")[0] == 409
    assert act(cid, v2["id"], "approve")[1]["data"]["status"] == "approved"


def test_edit_surfaces_rule_warnings_but_does_not_block(pending):
    cid, _, d = pending
    v2 = call("PATCH", f"/api/campaigns/{cid}/drafts/{d['id']}",
              {"body": "Hi there,\n\nWe guarantee results. Would you be open to a chat?"})[1]["data"]
    assert v2["status"] == "pending_review"
    assert [w["reason"] for w in v2["edit_warnings"]] == ["makes an unsubstantiated guarantee"]


def test_noop_edit_creates_no_version(pending):
    cid, _, d = pending
    out = call("PATCH", f"/api/campaigns/{cid}/drafts/{d['id']}", {"subject": d["subject"]})[1]["data"]
    assert out["id"] == d["id"] and list(drafts(cid)) == [1]


def test_edit_validation(pending):
    cid, _, d = pending
    url = f"/api/campaigns/{cid}/drafts/{d['id']}"
    assert call("PATCH", url, {})[0] == 422
    assert call("PATCH", url, {"subject": "x"})[0] == 422
    assert call("PATCH", url, {"recipient": {"email": "evil@x.io"}})[0] == 422   # recipient is not editable
    assert call("PATCH", url, {"status": "approved"})[0] == 422                  # cannot self-approve via edit


def test_cannot_edit_approved_draft(pending):
    cid, _, d = pending
    act(cid, d["id"], "approve")
    status, body = call("PATCH", f"/api/campaigns/{cid}/drafts/{d['id']}", {"subject": "Changed after approval"})
    assert status == 409 and body["error"]["code"] == "invalid_transition"
    assert drafts(cid)[1]["subject"] == d["subject"]


def test_blocked_draft_can_be_edited_into_review(table, ow):  # noqa: F811
    ow["writer"]["fact_ids"] = [9]
    cid = approved_campaign()
    d = make_draft(cid, lead_for(cid))[1]["data"]
    v2 = call("PATCH", f"/api/campaigns/{cid}/drafts/{d['id']}", {"subject": "Platform team growth"})[1]["data"]
    assert v2["status"] == "pending_review"


# ------------------------------------------------------------------ get / errors / regeneration lock
def test_get_draft_and_404s_and_auth(pending):
    cid, _, d = pending
    assert call("GET", f"/api/campaigns/{cid}/drafts/{d['id']}")[1]["data"]["id"] == d["id"]
    assert call("GET", f"/api/campaigns/{cid}/drafts/drf_nope")[0] == 404
    assert call("POST", f"/api/campaigns/{cid}/drafts/drf_nope/approve")[0] == 404
    assert call("POST", f"/api/campaigns/{cid}/drafts/{d['id']}/approve", token=None)[0] == 401


def test_cannot_regenerate_after_approval(pending):
    cid, lead, d = pending
    act(cid, d["id"], "approve")
    status, body = make_draft(cid, lead)
    assert status == 409 and body["error"]["code"] == "draft_locked"


def test_storage_keys_are_not_leaked(pending):
    _, _, d = pending
    assert not {"PK", "SK", "GSI1PK", "GSI1SK"} & set(d)
    assert dynamo.draft_sk("lead_x", 3) == "DRAFT#lead_x#0003"
