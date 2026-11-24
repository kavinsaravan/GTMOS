import pytest
from conftest import call

VALID = {
    "product_name": "Pipeline Guard",
    "description": "AI code review for platform engineering teams.",
    "geography": "US",
}


def test_health_needs_no_auth(table):
    status, body = call("GET", "/api/health", token=None)
    assert status == 200 and body["data"] == {"ok": True}
    assert body["operation_id"].startswith("op_")


@pytest.mark.parametrize("token", [None, "", "wrong"])
def test_campaign_routes_reject_bad_token(table, token):
    status, body = call("POST", "/api/campaigns", VALID, token=token)
    assert status == 401 and body["status"] == "error"
    assert call("GET", "/api/campaigns", token=token)[0] == 401


def test_fails_closed_when_no_token_configured(table, monkeypatch):
    monkeypatch.delenv("GTMOS_API_TOKEN")
    status, _ = call("GET", "/api/campaigns", token="anything")
    assert status == 401


def test_create_and_read_roundtrip(table):
    status, body = call("POST", "/api/campaigns", VALID)
    assert status == 201
    cid = body["data"]["id"]
    assert "PK" not in body["data"] and "SK" not in body["data"]

    status, body = call("GET", f"/api/campaigns/{cid}")
    assert status == 200
    assert body["data"]["criteria"]["product_name"] == "Pipeline Guard"
    assert body["data"]["org_id"] == "demo"

    status, body = call("GET", "/api/campaigns")
    assert [c["id"] for c in body["data"]] == [cid]


@pytest.mark.parametrize("patch", [
    {"product_name": ""},
    {"product_name": "   "},
    {"description": ""},
    {"description": "x" * 4001},
    {"unexpected": "field"},
])
def test_validation_rejects_bad_input(table, patch):
    status, body = call("POST", "/api/campaigns", {**VALID, **patch})
    assert status == 422 and body["error"]["code"] == "validation_error"


def test_invalid_json_and_oversize(table):
    assert call("POST", "/api/campaigns", raw_body="{not json")[0] == 400
    assert call("POST", "/api/campaigns", raw_body='{"a":"' + "x" * 70000 + '"}')[0] == 413


def test_unknown_campaign_404_and_unknown_route(table):
    assert call("GET", "/api/campaigns/cmp_missing")[0] == 404
    assert call("GET", "/api/nope")[0] == 404
    assert call("DELETE", "/api/campaigns")[0] == 405


def test_audit_event_written(table):
    _, body = call("POST", "/api/campaigns", VALID)
    cid = body["data"]["id"]
    status, body = call("GET", f"/api/campaigns/{cid}/events")
    assert status == 200
    ev = body["data"][0]
    assert ev["action"] == "campaign.created" and ev["campaign_id"] == cid
    assert ev["actor"] == "human:founder" and ev["occurred_at"]


def test_tenant_scoped_keys(table):
    from persistence import dynamo

    _, body = call("POST", "/api/campaigns", VALID)
    cid = body["data"]["id"]
    assert dynamo.get_campaign("demo", cid) is not None
    assert dynamo.get_campaign("other-org", cid) is None


def test_named_stage_prefix_is_stripped(table):
    import app

    event = {
        "rawPath": "/prod/api/health",
        "headers": {},
        "requestContext": {"stage": "prod", "http": {"method": "GET"}},
    }
    assert app.handler(event)["statusCode"] == 200
