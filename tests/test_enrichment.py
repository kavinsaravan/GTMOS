import io
import json
import urllib.error

import pytest
from conftest import call
from test_discovery import approved_campaign, world  # noqa: F401  (world is a fixture)

from integrations import hunter
from models.common import ApiError
from services import enrichment


def email(value, **kw):
    base = {"value": value, "type": "personal", "confidence": 90, "first_name": "Ann", "last_name": "Lee",
            "position": "Engineer", "seniority": "senior", "department": "it",
            "verification": {"status": "valid", "date": "2025-01-01"}, "sources": [{"uri": "https://x.io/team"}]}
    base.update(kw)
    return base


@pytest.fixture
def hn(monkeypatch):
    state = {"calls": [], "emails": [], "fail": None}
    monkeypatch.setenv("HUNTER_API_KEY", "hk-secret")

    def fake(url, headers, timeout):
        state["calls"].append((url, headers))
        if state["fail"]:
            raise state["fail"]
        return {"data": {"emails": state["emails"]}}

    monkeypatch.setattr(hunter, "_http_get", fake)
    return state


def http_error(code):
    return urllib.error.HTTPError("https://x", code, "err", {}, io.BytesIO(b"{}"))


def first_lead(cid):
    call("POST", f"/api/campaigns/{cid}/discover")
    return call("GET", f"/api/campaigns/{cid}/leads")[1]["data"][0]


# ------------------------------------------------------------------ client
def test_domain_search_uses_header_auth_and_personal_filter(hn):
    hn["emails"] = [email("ann@x.io")]
    out = hunter.domain_search("x.io")
    url, headers = hn["calls"][0]
    assert headers["X-API-KEY"] == "hk-secret" and "hk-secret" not in url
    assert "domain=x.io" in url and "type=personal" in url
    assert out[0]["email"] == "ann@x.io" and out[0]["verification_status"] == "valid"
    assert out[0]["sources"] == ["https://x.io/team"]


def test_domain_search_drops_entries_without_an_address(hn):
    hn["emails"] = [email(""), email(None), {"type": "personal"}, email("ok@x.io")]
    assert [c["email"] for c in hunter.domain_search("x.io")] == ["ok@x.io"]


def test_domain_search_not_configured(monkeypatch):
    monkeypatch.delenv("HUNTER_API_KEY", raising=False)
    with pytest.raises(ApiError) as e:
        hunter.domain_search("x.io")
    assert e.value.status == 503 and e.value.code == "enrichment_not_configured"


@pytest.mark.parametrize("code,status,err", [
    (401, 503, "enrichment_not_configured"), (429, 502, "enrichment_rate_limited"),
    (403, 502, "enrichment_rate_limited"), (451, 502, "enrichment_unavailable_for_domain"),
    (500, 502, "enrichment_unavailable")])
def test_domain_search_error_mapping(hn, code, status, err):
    hn["fail"] = http_error(code)
    with pytest.raises(ApiError) as e:
        hunter.domain_search("x.io")
    assert (e.value.status, e.value.code) == (status, err)
    assert "hk-secret" not in e.value.message


def test_domain_search_network_failure(hn):
    hn["fail"] = TimeoutError()
    with pytest.raises(ApiError) as e:
        hunter.domain_search("x.io")
    assert e.value.code == "enrichment_unavailable"


# ------------------------------------------------------------------ ranking
def c(addr, **kw):
    return hunter._contact(email(addr, **kw))


def test_unusable_verdicts_are_excluded():
    contacts = [c("a@x.io", verification={"status": "invalid"}), c("b@x.io", verification={"status": "disposable"}),
                c("d@x.io", verification={"status": "webmail"}), c("e@x.io")]
    assert [x["email"] for x in enrichment.rank(contacts, [])] == ["e@x.io"]


def test_persona_match_outranks_seniority():
    cto = c("cto@x.io", position="Chief Technology Officer", seniority="executive")
    sec = c("sec@x.io", position="Head of Security Engineering", seniority="senior")
    ranked = enrichment.rank([cto, sec], ["Security Engineering Lead"])
    assert ranked[0]["email"] == "sec@x.io" and ranked[0]["matched_persona"] == "Security Engineering Lead"


def test_valid_beats_accept_all_and_unknown_status_is_kept_honestly():
    a = c("a@x.io", verification={"status": "accept_all"})
    b = c("b@x.io", verification={"status": "valid"})
    u = c("u@x.io", verification={})
    ranked = enrichment.rank([a, u, b], [])
    assert ranked[0]["email"] == "b@x.io"
    assert [x for x in ranked if x["email"] == "u@x.io"][0]["verification_status"] == "unknown"


def test_ranking_caps_contacts():
    many = [c(f"p{i}@x.io") for i in range(9)]
    assert len(enrichment.rank(many, [])) == enrichment.MAX_CONTACTS


# ------------------------------------------------------------------ endpoint
def test_enrich_stores_contacts_and_audits(table, world, hn):  # noqa: F811
    cid = approved_campaign()
    lead = first_lead(cid)
    hn["emails"] = [email("ann@" + lead["domain"], position="VP Engineering", seniority="executive")]
    status, body = call("POST", f"/api/campaigns/{cid}/leads/{lead['id']}/enrich")
    assert status == 201 or status == 200
    data = body["data"]
    assert data["enrichment"]["status"] == "enriched" and data["contacts"][0]["email"] == "ann@" + lead["domain"]
    assert f"domain={lead['domain']}" in hn["calls"][0][0]
    stored = [l for l in call("GET", f"/api/campaigns/{cid}/leads")[1]["data"] if l["id"] == lead["id"]][0]
    assert stored["contacts"][0]["verification_status"] == "valid" and stored["enrichment"]["provider"] == "hunter"
    events = call("GET", f"/api/campaigns/{cid}/events")[1]["data"]
    ev = [e for e in events if e["action"] == "lead.enriched"][0]
    assert "@" not in json.dumps(ev["details"])  # no PII in the audit trail


def test_enrich_with_no_results_is_reported_not_invented(table, world, hn):  # noqa: F811
    cid = approved_campaign()
    lead = first_lead(cid)
    _, body = call("POST", f"/api/campaigns/{cid}/leads/{lead['id']}/enrich")
    assert body["data"]["enrichment"]["status"] == "no_contacts" and body["data"]["contacts"] == []


def test_enrich_is_idempotent_and_replaces_contacts(table, world, hn):  # noqa: F811
    cid = approved_campaign()
    lead = first_lead(cid)
    hn["emails"] = [email("a@x.io"), email("b@x.io")]
    call("POST", f"/api/campaigns/{cid}/leads/{lead['id']}/enrich")
    hn["emails"] = [email("a@x.io")]
    call("POST", f"/api/campaigns/{cid}/leads/{lead['id']}/enrich")
    stored = [l for l in call("GET", f"/api/campaigns/{cid}/leads")[1]["data"] if l["id"] == lead["id"]][0]
    assert [x["email"] for x in stored["contacts"]] == ["a@x.io"]


def test_enrich_not_configured_503_and_lead_untouched(table, world, monkeypatch):  # noqa: F811
    monkeypatch.delenv("HUNTER_API_KEY", raising=False)
    cid = approved_campaign()
    lead = first_lead(cid)
    status, body = call("POST", f"/api/campaigns/{cid}/leads/{lead['id']}/enrich")
    assert status == 503 and body["error"]["code"] == "enrichment_not_configured"
    stored = call("GET", f"/api/campaigns/{cid}/leads")[1]["data"][0]
    assert "contacts" not in stored


def test_enrich_unknown_lead_and_campaign_404(table, world, hn):  # noqa: F811
    cid = approved_campaign()
    assert call("POST", f"/api/campaigns/{cid}/leads/lead_nope/enrich")[0] == 404
    assert call("POST", "/api/campaigns/cmp_nope/leads/lead_x/enrich")[0] == 404


def test_enrich_requires_auth(table, world):  # noqa: F811
    cid = approved_campaign()
    assert call("POST", f"/api/campaigns/{cid}/leads/lead_x/enrich", token=None)[0] == 401
