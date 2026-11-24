"""verify_first resolution: one targeted search for the missing fact, recompute, or abstain."""
import pytest
from conftest import call
from test_billing import FakeFlexprice
from test_discovery import approved_campaign, world  # noqa: F401  (world is a fixture)

from integrations import tavily
from persistence import dynamo

SIGNAL = "Hiring platform engineers"   # ICP buying signal 0 in MODEL_ICP


def lead(domain, name, *, evidence, verified=True, tier="A", status="new"):
    return {"PK": None, "SK": dynamo.lead_sk(domain), "id": f"lead_{name.lower()}", "org_id": None,
            "company_name": name, "domain": domain, "fit_score": 80, "tier": tier, "evidence": evidence,
            "competitors": [], "verification_status": "verified" if verified else "unverified",
            "source_urls": [e["url"] for e in evidence], "status": status, "created_at": "2025-01-01T00:00:00Z"}


def ev(url, source_type, signal=SIGNAL):
    return {"source_id": "S1", "url": url, "title": "t", "signal": signal, "scored": True, "source_type": source_type,
            "quote": "The company is hiring several platform engineers right now for its team."}


@pytest.fixture
def env(table, world, monkeypatch):   # noqa: F811
    import app

    cid = approved_campaign()
    state = {"searches": [], "results": {}}

    def put(item):
        item["PK"], item["org_id"] = dynamo.pk(app.ORG_ID, cid), app.ORG_ID
        item["campaign_id"] = cid
        assert dynamo.put_lead(item)
        return item["id"]

    def fake_post(url, headers, body, timeout):
        state["searches"].append(body)
        key = "domain" if body.get("include_domains") else "signal"
        return {"results": [{**r, "score": 0.9} for r in state["results"].get(key, [])]}

    monkeypatch.setattr(tavily, "_http_post", fake_post)
    state.update(cid=cid, put=put)
    return state


def resolve(env, lead_id, **kw):
    return call("POST", f"/api/campaigns/{env['cid']}/leads/{lead_id}/resolve", {}, **kw)


def test_signal_found_flips_verify_first_to_contact_now(env):
    lid = env["put"](lead("nimbus.io", "Nimbus", evidence=[ev("https://www.indeed.com/cmp/nimbus", "aggregator")]))
    env["results"]["signal"] = [{"title": "News", "url": "https://news.example.com/nimbus",
                                 "content": "Nimbus is hiring five platform engineers to scale its infrastructure team."}]
    status, body = resolve(env, lid)
    d = body["data"]
    assert status == 201 and d["before"] == "verify_first" and d["after"] == "contact_now"
    assert d["outcome"] == "resolved" and d["steps"][0]["found"] is True
    assert "Nothing further" in d["brief"]["would_make_actionable"][0]
    stored = [l for l in call("GET", f"/api/campaigns/{env['cid']}/leads")[1]["data"] if l["id"] == lid][0]
    added = [e for e in stored["evidence"] if e.get("found_by") == "resolution"]
    assert added and added[0]["url"] == "https://news.example.com/nimbus" and added[0]["source_type"] == "third_party"
    assert stored["resolution"]["outcome"] == "resolved"
    assert "nimbus is hiring five platform engineers" in added[0]["quote"].casefold()   # verbatim sentence


def test_signal_not_found_abstains_and_adds_nothing(env):
    lid = env["put"](lead("orbit.io", "Orbit", evidence=[ev("https://www.indeed.com/cmp/orbit", "aggregator")]))
    env["results"]["signal"] = [
        {"title": "Jobs", "url": "https://www.linkedin.com/jobs/orbit",           # job board: not accepted
         "content": "Orbit is hiring five platform engineers to scale its infrastructure team."},
        {"title": "Other", "url": "https://news.example.com/other",              # does not name the company
         "content": "A different firm is hiring five platform engineers to scale its infrastructure."}]
    d = resolve(env, lid)[1]["data"]
    assert d["outcome"] == "abstain" and d["after"] == "verify_first"
    assert d["brief"]["would_make_actionable"] and "non-job-board" in d["brief"]["would_make_actionable"][0]
    stored = [l for l in call("GET", f"/api/campaigns/{env['cid']}/leads")[1]["data"] if l["id"] == lid][0]
    assert not [e for e in stored["evidence"] if e.get("found_by")]


def test_domain_confirmation_resolves_unverified_lead(env):
    lid = env["put"](lead("vega.io", "Vega", verified=False, evidence=[ev("https://vega.io/careers", "primary")]))
    env["results"]["domain"] = [{"title": "About Vega", "url": "https://vega.io/about", "content": "About Vega."}]
    d = resolve(env, lid)[1]["data"]
    assert d["outcome"] == "resolved" and d["steps"][0]["fact"] == "domain"
    stored = [l for l in call("GET", f"/api/campaigns/{env['cid']}/leads")[1]["data"] if l["id"] == lid][0]
    assert stored["verification_status"] == "verified"


def test_only_verify_first_leads_can_be_resolved_and_nothing_is_charged(env, monkeypatch):
    fp = FakeFlexprice(balance=10).install(monkeypatch)
    lid = env["put"](lead("ok.io", "Okay", evidence=[ev("https://ok.io/blog", "primary")]))
    status, body = resolve(env, lid)
    assert status == 409 and body["error"]["code"] == "nothing_to_resolve"
    assert fp.balance == 10 and env["searches"] == []


def test_resolution_is_metered_and_gated(env, monkeypatch):
    lid = env["put"](lead("nimbus.io", "Nimbus", evidence=[ev("https://www.indeed.com/cmp/nimbus", "aggregator")]))
    fp = FakeFlexprice(balance=1).install(monkeypatch)
    status, body = resolve(env, lid)
    assert status == 402 and body["error"]["code"] == "insufficient_credits" and env["searches"] == []
    fp.balance = 10
    assert resolve(env, lid)[0] == 201
    assert fp.balance == 8 and [e["name"] for e in fp.events] == ["gtmos_resolution"]


def test_attempts_are_capped(env):
    lid = env["put"](lead("orbit.io", "Orbit", evidence=[ev("https://www.indeed.com/cmp/orbit", "aggregator")]))
    assert [resolve(env, lid)[0] for _ in range(2)] == [201, 201]
    status, body = resolve(env, lid)
    assert status == 409 and body["error"]["code"] == "resolution_exhausted"


def test_search_outage_reports_error_and_charges_nothing(env, monkeypatch):
    import urllib.error

    lid = env["put"](lead("orbit.io", "Orbit", evidence=[ev("https://www.indeed.com/cmp/orbit", "aggregator")]))
    fp = FakeFlexprice(balance=10).install(monkeypatch)

    def boom(url, headers, body, timeout):
        raise urllib.error.HTTPError(url, 500, "boom", {}, None)

    monkeypatch.setattr(tavily, "_http_post", boom)
    status, body = resolve(env, lid)
    assert status == 502 and body["error"]["code"] == "search_unavailable" and fp.balance == 10
