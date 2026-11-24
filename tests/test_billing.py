"""Credit gating / metering against an in-memory fake of the Flexprice wallet API.

The fake mirrors behaviour observed on the live sandbox: debits never reject an overdraw
(the 402 gate is ours) and a repeated idempotency key is reported as already charged.
"""
import pytest
from conftest import call

VALID = {"product_name": "Pipeline Guard", "description": "AI code review for platform teams.", "geography": "US"}


class FakeFlexprice:
    def __init__(self, balance=100):
        self.balance = balance
        self.keys: set[str] = set()
        self.events: list[dict] = []
        self.down = False

    def install(self, monkeypatch):
        from integrations import flexprice as fp

        def guard():
            if self.down:
                from models.common import ApiError
                raise ApiError(502, "billing_unavailable", "Flexprice is unavailable")

        def ensure_wallet(ext, name, initial):
            guard()
            return {"id": "wallet_1", "wallet_status": "active", "real_time_credit_balance": str(self.balance)}

        def debit(wallet_id, credits, key, desc):
            guard()
            if key in self.keys:
                return False
            self.keys.add(key)
            self.balance -= credits
            return True

        def send_event(name, ext, event_id, props):
            guard()
            self.events.append({"name": name, "id": event_id, **props})

        monkeypatch.setattr(fp, "configured", lambda: True)
        monkeypatch.setattr(fp, "ensure_wallet", ensure_wallet)
        monkeypatch.setattr(fp, "debit", debit)
        monkeypatch.setattr(fp, "send_event", send_event)
        monkeypatch.setattr(fp, "customer_external_id", lambda org: f"gtmos-org-{org}")
        return self


@pytest.fixture
def campaign(table):
    return call("POST", "/api/campaigns", VALID)[1]["data"]["id"]


@pytest.fixture
def stub_graph(monkeypatch):
    from services import graph

    calls = []
    monkeypatch.setattr(graph, "sync", lambda org, campaign, leads=None: calls.append(1) or {"synced": True})
    return calls


def test_unconfigured_runs_unmetered_and_reports_it(campaign, stub_graph):
    status, body = call("GET", "/api/system/billing")
    assert status == 200 and body["data"]["configured"] is False
    assert call("POST", f"/api/campaigns/{campaign}/graph/sync", {})[0] == 201
    assert stub_graph == [1]


def test_charges_and_meters_once_per_operation(campaign, stub_graph, monkeypatch):
    fp = FakeFlexprice(balance=10).install(monkeypatch)
    status, _ = call("POST", f"/api/campaigns/{campaign}/graph/sync", {})
    assert status == 201
    assert fp.balance == 9                       # graph_query costs 1
    assert [e["name"] for e in fp.events] == ["gtmos_graph_query"]


def test_insufficient_credits_returns_402_and_runs_nothing(campaign, stub_graph, monkeypatch):
    fp = FakeFlexprice(balance=0).install(monkeypatch)
    status, body = call("POST", f"/api/campaigns/{campaign}/graph/sync", {})
    assert status == 402 and body["error"]["code"] == "insufficient_credits"
    assert body["error"]["details"][0]["required"] == 1
    assert stub_graph == [] and fp.keys == set() and fp.events == []


def test_billing_outage_fails_closed(campaign, stub_graph, monkeypatch):
    fp = FakeFlexprice().install(monkeypatch)
    fp.down = True
    status, body = call("POST", f"/api/campaigns/{campaign}/graph/sync", {})
    assert status == 502 and body["error"]["code"] == "billing_unavailable"
    assert stub_graph == []


def test_charge_failure_after_success_does_not_fail_the_request(campaign, stub_graph, monkeypatch):
    from integrations import flexprice as fp_mod
    from models.common import ApiError

    FakeFlexprice().install(monkeypatch)

    def boom(*a, **k):
        raise ApiError(502, "billing_unavailable", "down")

    monkeypatch.setattr(fp_mod, "debit", boom)
    assert call("POST", f"/api/campaigns/{campaign}/graph/sync", {})[0] == 201
    actions = [e["action"] for e in call("GET", f"/api/campaigns/{campaign}/events")[1]["data"]]
    assert "billing.charge_failed" in actions


def test_replayed_idempotency_key_is_not_charged_twice(campaign, stub_graph, monkeypatch):
    import app

    fp = FakeFlexprice(balance=10).install(monkeypatch)

    def post():
        import json
        event = {"rawPath": f"/api/campaigns/{campaign}/graph/sync",
                 "headers": {"x-gtmos-token": "test-token-123", "idempotency-key": "retry-1"},
                 "requestContext": {"http": {"method": "POST"}}, "body": "{}"}
        return app.handler(event)["statusCode"]

    assert post() == 201 and post() == 201
    assert fp.balance == 9 and len(fp.events) == 1


def test_costs_match_the_prd():
    from services import billing

    assert billing.COSTS == {"discovery": 1, "enrichment": 3, "graph_query": 1, "draft": 2, "resolution": 2}


def test_flexprice_url_must_be_flexprice_https(monkeypatch):
    from integrations import flexprice as fp
    from models.common import ApiError

    monkeypatch.setenv("FLEXPRICE_API_KEY", "sk_test")
    for bad in ("http://us.api.flexprice.io/v1", "https://evil.example.com/v1", "https://flexprice.io.evil.com/v1"):
        monkeypatch.setenv("FLEXPRICE_API_URL", bad)
        with pytest.raises(ApiError):
            fp._base()
    monkeypatch.setenv("FLEXPRICE_API_URL", "https://us.api.flexprice.io/v1")
    assert fp._base() == "https://us.api.flexprice.io/v1"
