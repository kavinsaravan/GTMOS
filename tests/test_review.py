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


# ------------------------------------------------------------------ Gmail draft after approval
import base64
import email as email_lib
import io
import json
import urllib.error

from integrations import gmail


@pytest.fixture
def gm(monkeypatch):
    state = {"calls": [], "fail_token": None, "fail_draft": None}
    for k in gmail.KEYS:
        monkeypatch.setenv(k, f"{k.lower()}-secret")

    def fake(url, headers, data, timeout):
        state["calls"].append((url, headers, data))
        if url == gmail.TOKEN_URL:
            if state["fail_token"]:
                raise state["fail_token"]
            return {"access_token": "ya29.token"}
        if state["fail_draft"]:
            raise state["fail_draft"]
        return {"id": "r-123", "message": {"id": "m1"}}

    monkeypatch.setattr(gmail, "_http_post", fake)
    return state


def with_contact(cid, lead, addr="ann@acme.com"):
    dynamo.update_lead("demo", cid, lead["domain"], {"contacts": [
        {"email": addr, "first_name": "Ann", "last_name": "Lee", "position": "VP Platform",
         "verification_status": "valid", "sources": []}]})


def herr(code):
    return urllib.error.HTTPError("https://x", code, "err", {}, io.BytesIO(b"{}"))


def test_approve_without_gmail_is_truthfully_not_connected(pending):
    cid, lead, _ = pending
    with_contact(cid, lead)
    d = make_draft(cid, lead)[1]["data"]
    out = act(cid, d["id"], "approve")[1]["data"]
    assert out["status"] == "approved" and out["gmail"]["status"] == "not_connected"
    assert "draft_id" not in out["gmail"]


def test_approve_without_recipient_email_is_no_recipient_and_never_calls_gmail(pending, gm):
    cid, _, d = pending                          # persona-only recipient: no email
    out = act(cid, d["id"], "approve")[1]["data"]
    assert out["gmail"]["status"] == "no_recipient" and gm["calls"] == []


def test_approve_creates_gmail_draft_with_stored_recipient_only(table, ow, gm):  # noqa: F811
    cid = approved_campaign()
    lead = lead_for(cid)
    with_contact(cid, lead)
    d = make_draft(cid, lead)[1]["data"]
    out = act(cid, d["id"], "approve")[1]["data"]
    assert out["gmail"]["status"] == "created" and out["gmail"]["draft_id"] == "r-123"
    token_call, draft_call = gm["calls"]
    assert b"refresh_token=" in token_call[2] and draft_call[1]["authorization"] == "Bearer ya29.token"
    assert draft_call[0].endswith("/drafts") and "/send" not in draft_call[0]
    raw = json.loads(draft_call[2])["message"]["raw"]
    msg = email_lib.message_from_bytes(base64.urlsafe_b64decode(raw))
    assert msg["To"] == "ann@acme.com" and msg["Subject"] == d["subject"]
    assert "Acme is hiring five platform engineers" in msg.get_payload(decode=True).decode()
    assert "draft.gmail_created" in actions(cid)
    ev = [e for e in call("GET", f"/api/campaigns/{cid}/events")[1]["data"] if e["action"] == "draft.gmail_created"]
    assert "ann@acme.com" not in json.dumps(ev[0]["details"])


def test_approve_twice_creates_only_one_gmail_draft(table, ow, gm):  # noqa: F811
    cid = approved_campaign()
    lead = lead_for(cid)
    with_contact(cid, lead)
    d = make_draft(cid, lead)[1]["data"]
    act(cid, d["id"], "approve")
    again = act(cid, d["id"], "approve")[1]["data"]
    assert again["gmail"]["draft_id"] == "r-123"
    assert sum(1 for c in gm["calls"] if c[0] == gmail.DRAFTS_URL) == 1


def test_gmail_failure_keeps_approval_and_retry_succeeds(table, ow, gm):  # noqa: F811
    cid = approved_campaign()
    lead = lead_for(cid)
    with_contact(cid, lead)
    d = make_draft(cid, lead)[1]["data"]
    gm["fail_draft"] = herr(500)
    out = act(cid, d["id"], "approve")[1]["data"]
    assert out["status"] == "approved" and out["gmail"] == {**out["gmail"], "status": "failed",
                                                          "error_code": "mail_unavailable"}
    assert "draft.gmail_failed" in actions(cid)
    gm["fail_draft"] = None
    retried = act(cid, d["id"], "approve")[1]["data"]
    assert retried["gmail"]["status"] == "created"


@pytest.mark.parametrize("fail,code", [("token400", "mail_not_configured"), ("draft403", "mail_not_configured"),
                                       ("draft429", "mail_rate_limited"), ("token500", "mail_unavailable")])
def test_gmail_error_mapping(gm, fail, code):
    if fail.startswith("token"):
        gm["fail_token"] = herr(int(fail[5:]))
    else:
        gm["fail_draft"] = herr(int(fail[5:]))
    with pytest.raises(Exception) as e:
        gmail.create_draft("a@b.io", "s", "b")
    assert e.value.code == code and "secret" not in e.value.message


def test_rejected_draft_never_reaches_gmail(table, ow, gm):  # noqa: F811
    cid = approved_campaign()
    lead = lead_for(cid)
    with_contact(cid, lead)
    d = make_draft(cid, lead)[1]["data"]
    act(cid, d["id"], "reject", {"reason": "no"})
    assert act(cid, d["id"], "approve")[0] == 409 and gm["calls"] == []


def test_header_injection_in_subject_cannot_add_headers():
    raw = gmail.build_raw("a@b.io", "Hello\r\nBcc: evil@x.io", "body")
    msg = email_lib.message_from_bytes(base64.urlsafe_b64decode(raw))
    assert msg["Bcc"] is None and msg["Subject"] == "Hello Bcc: evil@x.io"


def test_module_has_no_send_path():
    src = open(gmail.__file__).read()
    assert "/messages/send" not in src and "drafts/send" not in src and "def send" not in src
