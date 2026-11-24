import json

import pytest
from conftest import call
from test_discovery import approved_campaign, world  # noqa: F401  (world is a fixture)

from integrations import llm
from persistence import dynamo
from services import outreach

GOOD_BODY = ("Hi there,\n\nI noticed Acme is hiring five platform engineers as the team grows. "
             "Pipeline Guard cuts pull request review time for platform teams. "
             "Would you be open to a 15 minute chat next week?")


@pytest.fixture
def ow(world, monkeypatch):  # noqa: F811
    """Discovery fakes plus Writer/Reviewer fakes."""
    base = llm._call_anthropic
    state = {"writer": {"subject": "Platform hiring at Acme", "body": GOOD_BODY, "fact_ids": [1]},
             "remove": [], "prompts": [], "fail_reviewer": False, "fail_writer": False}

    def fake(system, user, max_tokens, temperature, timeout):
        if "Writer agent" in system:
            state["prompts"].append(("writer", system, user))
            if state["fail_writer"]:
                raise RuntimeError("down")
            return json.dumps(state["writer"]), "claude-test"
        if "Reviewer agent" in system:
            state["prompts"].append(("reviewer", system, user))
            if state["fail_reviewer"]:
                raise RuntimeError("down")
            return json.dumps({"remove": state["remove"]}), "claude-test"
        return base(system, user, max_tokens, temperature, timeout)

    monkeypatch.setattr(llm, "_call_anthropic", fake)
    return state


def lead_for(cid, domain="acme.com"):
    call("POST", f"/api/campaigns/{cid}/discover")
    return [l for l in call("GET", f"/api/campaigns/{cid}/leads")[1]["data"] if l["domain"] == domain][0]


def make_draft(cid, lead):
    return call("POST", f"/api/campaigns/{cid}/leads/{lead['id']}/draft")


def actions(cid):
    return [e["action"] for e in call("GET", f"/api/campaigns/{cid}/events")[1]["data"]]


# ------------------------------------------------------------------ select_facts
def test_facts_prefer_scored_primary_and_skip_company_size():
    lead = {"evidence": [
        {"url": "https://a.example/1", "quote": "Q size", "supports": "company_size", "scored": False},
        {"url": "https://agg.example/2", "quote": "Q agg", "scored": True, "source_type": "aggregator"},
        {"url": "https://acme.com/3", "quote": "Q primary", "scored": True, "source_type": "primary"},
        {"url": "https://acme.com/3", "quote": "Q dup url", "scored": True, "source_type": "primary"},
        {"url": "https://n.example/4", "quote": "Q unscored", "scored": False, "source_type": "news"}]}
    facts = outreach.select_facts(lead)
    assert [f["quote"] for f in facts] == ["Q primary", "Q agg", "Q unscored"]
    assert [f["id"] for f in facts] == [1, 2, 3]


# ------------------------------------------------------------------ rules
@pytest.mark.parametrize("text,fragment", [
    ("See https://evil.example/x for more.", "link"),
    ("Email me at ceo@acme.com today.", "link"),
    ("Visit acme.com now.", "link"),
    ("Hi [First Name], quick question.", "placeholder"),
    ("We guarantee a 2x faster review.", "guarantee"),
    ("As we discussed last week, this fits.", "prior relationship"),
    ("Teams save 40% on review time.", "number"),
    ("You raised 50 million dollars.", "number"),
])
def test_rules_flag_unsupported_content(text, fragment):
    assert fragment in outreach.rule_violation(text, {"5", "300"})


@pytest.mark.parametrize("text", [
    "Would you be open to a 15 minute chat?", "Acme is hiring five platform engineers.",
    "The team grows to 300 employees.", "Pipeline Guard cuts review time."])
def test_rules_allow_supported_content(text):
    assert outreach.rule_violation(text, {"300"}) is None


def test_segments_keep_paragraphs_and_split_sentences():
    segs = outreach.segments("Hi Ann,\n\nFirst point. Second point? Third!\nLast.")
    assert [s["text"] for s in segs] == ["Hi Ann,", "First point.", "Second point?", "Third!", "Last."]
    assert [s["para"] for s in segs] == [0, 2, 2, 2, 3]


# ------------------------------------------------------------------ generate
def test_generate_draft_grounded_reviewed_and_audited(table, ow):
    cid = approved_campaign()
    lead = lead_for(cid)
    status, body = make_draft(cid, lead)
    d = body["data"]
    assert status == 201 and d["status"] == "pending_review" and d["version"] == 1
    assert d["reviewer"]["status"] == "passed" and d["reviewer"]["removed"] == []
    assert d["facts"][0]["url"] == "https://news.example.com/acme-hiring" and d["fact_ids"] == [1]
    assert d["recipient"]["email"] is None and d["recipient"]["source"] == "icp_persona"
    assert d["created_by"] == "agent:writer" and d["lead_id"] == lead["id"]
    assert {"draft.generated", "draft.reviewed"} <= set(actions(cid))
    ev = call("GET", f"/api/campaigns/{cid}/events")[1]["data"]
    assert "Hi there" not in json.dumps([e["details"] for e in ev])  # no draft text in audit trail


def test_prompts_treat_evidence_as_untrusted_data(table, ow):
    cid = approved_campaign()
    make_draft(cid, lead_for(cid))
    kinds = {p[0]: p for p in ow["prompts"]}
    assert "untrusted" in kinds["writer"][1] and "untrusted" in kinds["reviewer"][1]
    assert "<facts>" in kinds["writer"][2] and "hiring five platform engineers" in kinds["writer"][2]


def test_recipient_comes_from_stored_hunter_contact_never_the_model(table, ow):
    cid = approved_campaign()
    lead = lead_for(cid)
    dynamo.update_lead("demo", cid, lead["domain"], {"contacts": [
        {"email": "ann@acme.com", "first_name": "Ann", "last_name": "Lee", "position": "VP Platform",
         "verification_status": "valid", "sources": ["https://acme.com/team"]}]})
    d = make_draft(cid, lead)[1]["data"]
    assert d["recipient"] == {"name": "Ann Lee", "email": "ann@acme.com", "role": "VP Platform",
                              "verification_status": "valid", "source": "hunter",
                              "sources": ["https://acme.com/team"]}
    assert "recipient_first_name: Ann" in [p for p in ow["prompts"] if p[0] == "writer"][0][2]


def test_deterministic_rules_remove_unsupported_sentences(table, ow):
    ow["writer"]["body"] = ("Hi there,\n\nAcme is hiring five platform engineers as the team grows. "
                            "Teams like yours cut review time by 63%. We guarantee better code. "
                            "Details at https://pipelineguard.example/case. Would you be open to a chat?")
    cid = approved_campaign()
    d = make_draft(cid, lead_for(cid))[1]["data"]
    assert d["reviewer"]["status"] == "edited"
    reasons = {r["sentence"][:12]: r["by"] for r in d["reviewer"]["removed"]}
    assert set(reasons.values()) == {"rule"} and len(reasons) == 3
    for gone in ("63%", "guarantee", "https://"):
        assert gone not in d["body"]
    assert "Acme is hiring five platform engineers" in d["body"] and d["body"].startswith("Hi there,")
    assert d["original"]["body"] == ow["writer"]["body"]       # pre-review text is preserved


def test_reviewer_model_removes_unsupported_claim_and_is_audited(table, ow):
    ow["writer"]["body"] = ("Hi there,\n\nAcme is hiring five platform engineers as the team grows. "
                            "Your CTO recently praised our approach. Would you be open to a chat?")
    ow["remove"] = [{"index": 2, "reason": "no evidence of CTO statement"}, {"index": 0, "reason": "greeting"},
                    {"index": 99, "reason": "out of range"}]
    cid = approved_campaign()
    d = make_draft(cid, lead_for(cid))[1]["data"]
    assert d["reviewer"]["status"] == "edited"
    assert [(r["by"], r["reason"]) for r in d["reviewer"]["removed"]] == [("reviewer", "no evidence of CTO statement")]
    assert "CTO" not in d["body"] and d["body"].startswith("Hi there,")   # greeting never removed
    numbered = [p for p in ow["prompts"] if p[0] == "reviewer"][0][2]
    assert "2: Your CTO recently praised" in numbered


def test_blocked_when_too_little_survives(table, ow):
    ow["writer"]["body"] = ("Hi there,\n\nWe guarantee Acme will love it. Visit https://x.example now. "
                            "Teams save 70% immediately.")
    cid = approved_campaign()
    d = make_draft(cid, lead_for(cid))[1]["data"]
    assert d["status"] == "blocked" and d["reviewer"]["status"] == "blocked"
    assert d["reviewer"]["flags"] == ["too little supported content remained after review"]


def test_blocked_when_fact_id_is_unknown(table, ow):
    ow["writer"]["fact_ids"] = [7]
    cid = approved_campaign()
    d = make_draft(cid, lead_for(cid))[1]["data"]
    assert d["status"] == "blocked" and "draft cites a fact that does not exist" in d["reviewer"]["flags"]


def test_blocked_when_subject_has_unsupported_number(table, ow):
    ow["writer"]["subject"] = "Cut review time 80% at Acme"
    cid = approved_campaign()
    d = make_draft(cid, lead_for(cid))[1]["data"]
    assert d["status"] == "blocked" and d["reviewer"]["flags"][0].startswith("subject")


def test_reviewer_outage_fails_closed_and_stores_nothing(table, ow):
    ow["fail_reviewer"] = True
    cid = approved_campaign()
    lead = lead_for(cid)
    status, body = make_draft(cid, lead)
    assert status == 502
    assert call("GET", f"/api/campaigns/{cid}/drafts")[1]["data"] == []


def test_writer_outage_returns_typed_error(table, ow):
    ow["fail_writer"] = True
    cid = approved_campaign()
    status, body = make_draft(cid, lead_for(cid))
    assert status == 502 and body["status"] == "error"


def test_no_evidence_means_no_draft(table, ow):
    cid = approved_campaign()
    lead = lead_for(cid)
    dynamo.update_lead("demo", cid, lead["domain"], {"evidence": []})
    status, body = make_draft(cid, lead)
    assert status == 409 and body["error"]["code"] == "insufficient_evidence"
    assert ow["prompts"] == []                      # the model is never asked to improvise


def test_regenerate_creates_new_version_and_supersedes_previous(table, ow):
    cid = approved_campaign()
    lead = lead_for(cid)
    first = make_draft(cid, lead)[1]["data"]
    second = make_draft(cid, lead)[1]["data"]
    assert (first["version"], second["version"]) == (1, 2) and second["parent_version"] == 1
    drafts = call("GET", f"/api/campaigns/{cid}/drafts")[1]["data"]
    assert {d["version"]: d["status"] for d in drafts} == {1: "superseded", 2: "pending_review"}


def test_unknown_lead_and_campaign_404_and_auth(table, ow):
    cid = approved_campaign()
    assert call("POST", f"/api/campaigns/{cid}/leads/lead_nope/draft")[0] == 404
    assert call("POST", "/api/campaigns/cmp_nope/leads/lead_x/draft")[0] == 404
    assert call("POST", f"/api/campaigns/{cid}/leads/lead_x/draft", token=None)[0] == 401
    assert call("GET", f"/api/campaigns/{cid}/drafts", token=None)[0] == 401
