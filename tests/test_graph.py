import io
import json
import urllib.error

import pytest
from conftest import call
from test_discovery import SOURCES, approved_campaign, run_single, world  # noqa: F401  (world is a fixture)

from integrations import neo4j
from models.common import ApiError
from services import graph


@pytest.fixture
def neo(monkeypatch):
    state = {"calls": [], "rows": {}, "fail": None}
    monkeypatch.setenv("NEO4J_URI", "neo4j+s://abc123.databases.neo4j.io")
    monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", "pw-secret")
    monkeypatch.setattr(graph, "_constraints_done", False)
    monkeypatch.setattr(neo4j, "_resolved_db", {})

    def fake(url, headers, body, timeout):
        state["calls"].append((url, headers, body))
        if state["fail"]:
            raise state["fail"]
        for key, (fields, values) in state["rows"].items():
            if key in body["statement"]:
                return {"data": {"fields": fields, "values": values}}
        return {"data": {"fields": [], "values": []}}

    monkeypatch.setattr(neo4j, "_http_post", fake)
    return state


def http_error(code, body=None):
    return urllib.error.HTTPError("https://x", code, "err", {}, io.BytesIO(json.dumps(body or {}).encode()))


# ------------------------------------------------------------------ client
@pytest.mark.parametrize("uri,expected", [
    ("neo4j+s://abc.databases.neo4j.io", "https://abc.databases.neo4j.io/db/neo4j/query/v2"),
    ("neo4j://abc.databases.neo4j.io:7687/", "https://abc.databases.neo4j.io/db/neo4j/query/v2"),
    ("bolt+s://abc.databases.neo4j.io", "https://abc.databases.neo4j.io/db/neo4j/query/v2"),
    ("https://abc.databases.neo4j.io", "https://abc.databases.neo4j.io/db/neo4j/query/v2"),
])
def test_endpoint(uri, expected):
    assert neo4j.endpoint(uri, "neo4j") == expected


def test_endpoint_rejects_garbage():
    with pytest.raises(ApiError) as e:
        neo4j.endpoint("not a uri", "neo4j")
    assert e.value.code == "graph_not_configured"


def test_run_sends_basic_auth_parameters_and_collapses_whitespace(neo):
    neo["rows"]["RETURN 1"] = (["n", "name"], [[1, "a"], [2, "b"]])
    rows = neo4j.run("MATCH (n)\n   RETURN 1 AS n, $x AS name", {"x": "a"})
    url, headers, body = neo["calls"][0]
    assert url == "https://abc123.databases.neo4j.io/db/neo4j/query/v2"
    assert headers["authorization"].startswith("Basic ") and "pw-secret" not in json.dumps(headers)
    assert "\n" not in body["statement"] and body["parameters"] == {"x": "a"}
    assert rows == [{"n": 1, "name": "a"}, {"n": 2, "name": "b"}]


def test_run_not_configured(table):
    with pytest.raises(ApiError) as e:
        neo4j.run("RETURN 1")
    assert e.value.status == 503 and e.value.code == "graph_not_configured"


@pytest.mark.parametrize("exc,status,code", [
    (http_error(401), 503, "graph_not_configured"),
    (http_error(400, {"errors": [{"code": "Neo.ClientError.Statement.SyntaxError"}]}), 502, "graph_query_failed"),
    (http_error(503), 502, "graph_unavailable"),
    (TimeoutError("slow pw-secret"), 502, "graph_unavailable"),
])
def test_run_maps_errors_without_leaking(neo, exc, status, code):
    neo["fail"] = exc
    with pytest.raises(ApiError) as e:
        neo4j.run("RETURN 1")
    assert (e.value.status, e.value.code) == (status, code)
    assert "pw-secret" not in e.value.message


def test_database_falls_back_to_aura_instance_id_and_is_cached(neo, monkeypatch):
    seen = []

    def fake(url, headers, body, timeout):
        seen.append(url)
        if "/db/neo4j/" in url:
            raise http_error(404, {"errors": [{"code": "Neo.ClientError.Database.DatabaseNotFound"}]})
        return {"data": {"fields": ["ok"], "values": [[1]]}}

    monkeypatch.setattr(neo4j, "_http_post", fake)
    assert neo4j.run("RETURN 1 AS ok") == [{"ok": 1}]
    assert neo4j.run("RETURN 1 AS ok") == [{"ok": 1}]
    assert seen == ["https://abc123.databases.neo4j.io/db/neo4j/query/v2",
                    "https://abc123.databases.neo4j.io/db/abc123/query/v2",
                    "https://abc123.databases.neo4j.io/db/abc123/query/v2"]


def test_explicit_database_is_authoritative_no_fallback(neo, monkeypatch):
    monkeypatch.setenv("NEO4J_DATABASE", "mydb")
    neo["fail"] = http_error(404, {"errors": [{"code": "Neo.ClientError.Database.DatabaseNotFound"}]})
    with pytest.raises(ApiError) as e:
        neo4j.run("RETURN 1")
    assert e.value.code == "graph_unavailable"
    assert [c[0] for c in neo["calls"]] == ["https://abc123.databases.neo4j.io/db/mydb/query/v2"]


def test_non_aura_host_has_no_fallback(neo, monkeypatch):
    monkeypatch.setenv("NEO4J_URI", "neo4j://graph.internal.example:7687")
    neo["fail"] = http_error(404, {"errors": [{"code": "Neo.ClientError.Database.DatabaseNotFound"}]})
    with pytest.raises(ApiError):
        neo4j.run("RETURN 1")
    assert len(neo["calls"]) == 1


# ------------------------------------------------------------------ projection
def lead(i, name, domain, signals=(), competitors=(), tier="A", score=85):
    ev = [{"signal": s, "scored": True, "url": f"https://news.example.com/{domain}/{n}",
           "title": "t", "quote": f"{name} did something notable with {s} recently", "source_type": st}
          for n, (s, st) in enumerate(signals)]
    return {"id": f"lead_{i}", "company_name": name, "domain": domain, "evidence": ev, "tier": tier,
            "fit_score": score, "verification_status": "verified", "employees": 100, "icp_version": 1,
            "competitors": [{"name": c, "url": "https://x.example/c", "quote": f"{name} competes with {c} daily"}
                            for c in competitors]}


CAMPAIGN = {"id": "cmp_1", "product_name": "Pipeline Guard", "criteria": {"description": "AI code review"}}


def test_projection_only_includes_scored_signals_and_marks_aggregator_weak():
    a = lead(1, "Acme", "acme.com", [("Hiring", "third_party"), ("K8s", "aggregator")])
    a["evidence"].append({"signal": "Hiring", "scored": False, "url": "https://dup", "title": "", "quote": "dup dup dup dup dup dup dup dup"})
    a["evidence"].append({"signal": None, "scored": False, "url": "https://u", "title": "", "quote": "no signal here at all in this one"})
    p = graph.project("demo", CAMPAIGN, [a])
    assert [(s["type"], s["strength"]) for s in p["leads"][0]["signals"]] == [("Hiring", "strong"), ("K8s", "weak")]
    assert p["product"] == {"id": "cmp_1", "org_id": "demo", "name": "Pipeline Guard", "description": "AI code review"}


def test_projection_is_deterministic_so_sync_is_retry_safe():
    leads = [lead(1, "Acme", "acme.com", [("Hiring", "primary")])]
    assert graph.project("demo", CAMPAIGN, leads) == graph.project("demo", CAMPAIGN, leads)
    assert graph.company_id("demo", "acme.com") != graph.company_id("other", "acme.com")


def test_competitor_edges_resolve_to_leads_or_competitor_nodes_never_self():
    a = lead(1, "Acme", "acme.com", competitors=["Globex", "Initrode", "acme"])
    g = lead(2, "Globex", "globex.io")
    p = graph.project("demo", CAMPAIGN, [a, g])
    assert [r["target_id"] for r in p["rivals_in"]] == [graph.company_id("demo", "globex.io")]
    assert [r["name"] for r in p["rivals_out"]] == ["Initrode"]


# ------------------------------------------------------------------ sync
def test_sync_requires_configuration(table):
    with pytest.raises(ApiError) as e:
        graph.sync("demo", CAMPAIGN, [lead(1, "Acme", "acme.com")])
    assert e.value.code == "graph_not_configured"


def test_sync_writes_parameterized_merges_and_creates_constraints_once(table, neo):
    neo["rows"]["count(DISTINCT sig)"] = (["signals"], [[2]])
    leads = [lead(1, "Acme", "acme.com", [("Hiring", "primary"), ("K8s", "third_party")])]
    out = graph.sync("demo", CAMPAIGN, leads)
    assert out == {"companies": 1, "signals": 2, "competitor_edges": 0}
    statements = [c[2]["statement"] for c in neo["calls"]]
    assert sum("CREATE CONSTRAINT" in s for s in statements) == 5
    write = [c[2] for c in neo["calls"] if "UNWIND $leads" in c[2]["statement"]][0]
    assert "MERGE" in write["statement"] and "Acme" not in write["statement"]   # values only via parameters
    assert write["parameters"]["leads"][0]["name"] == "Acme"
    graph.sync("demo", CAMPAIGN, leads)
    assert sum("CREATE CONSTRAINT" in c[2]["statement"] for c in neo["calls"]) == 5   # not repeated


def test_sync_writes_competitor_edges(table, neo):
    neo["rows"]["count(x)"] = (["edges"], [[1]])
    leads = [lead(1, "Acme", "acme.com", competitors=["Globex", "Initrode"]), lead(2, "Globex", "globex.io")]
    assert graph.sync("demo", CAMPAIGN, leads)["competitor_edges"] == 2


def test_sync_endpoint_reads_leads_from_dynamodb(table, world, neo):  # noqa: F811
    cid = approved_campaign()
    call("POST", f"/api/campaigns/{cid}/discover")
    neo["calls"].clear()
    graph._constraints_done = False
    status, body = call("POST", f"/api/campaigns/{cid}/graph/sync")
    assert status == 201 and body["data"]["companies"] == 3
    write = [c[2] for c in neo["calls"] if "UNWIND $leads" in c[2]["statement"]][0]
    assert {l["domain"] for l in write["parameters"]["leads"]} == {"acme.com", "initech.com", "hooli.example"}
    assert write["parameters"]["product"]["id"] == cid


def test_sync_endpoint_503_when_not_configured(table, world):  # noqa: F811
    cid = approved_campaign()
    status, body = call("POST", f"/api/campaigns/{cid}/graph/sync")
    assert status == 503 and body["error"]["code"] == "graph_not_configured"


# ------------------------------------------------------------------ discovery integration
def test_discovery_reports_graph_status_not_configured(table, world):  # noqa: F811
    cid = approved_campaign()
    _, body = call("POST", f"/api/campaigns/{cid}/discover")
    assert body["data"]["run"]["graph"] == {"status": "not_configured"}


def test_discovery_syncs_graph_and_audits(table, world, neo):  # noqa: F811
    neo["rows"]["count(DISTINCT sig)"] = (["signals"], [[1]])
    cid = approved_campaign()
    _, body = call("POST", f"/api/campaigns/{cid}/discover")
    assert body["data"]["run"]["graph"] == {"status": "synced", "companies": 3, "signals": 1, "competitor_edges": 0}
    assert "graph.synced" in [e["action"] for e in call("GET", f"/api/campaigns/{cid}/events")[1]["data"]]


def test_graph_outage_never_fails_discovery(table, world, neo):  # noqa: F811
    neo["fail"] = http_error(503)
    cid = approved_campaign()
    status, body = call("POST", f"/api/campaigns/{cid}/discover")
    assert status == 201 and len(body["data"]["leads"]) == 3
    assert body["data"]["run"]["graph"] == {"status": "failed", "code": "graph_unavailable"}
    assert "graph.sync_failed" in [e["action"] for e in call("GET", f"/api/campaigns/{cid}/events")[1]["data"]]


def test_competitor_needs_verbatim_quote_naming_the_rival(table, world, monkeypatch):  # noqa: F811
    src = {"title": "Market", "url": "https://news.example.com/market",
           "content": "Acme Corp competes head to head with Globex in the platform tooling market."}
    company = {"company_name": "Acme Corp", "domain": "acme.com", "industry_match": "exact",
               "evidence": [{"source_id": "S1", "signal_index": None, "quote": "Acme Corp competes head to head"}],
               "competitors": [
                   {"name": "Globex", "source_id": "S1", "quote": "Acme Corp competes head to head with Globex in the platform"},
                   {"name": "Initech", "source_id": "S1", "quote": "Acme Corp competes head to head with Globex in the platform"},
                   {"name": "Hooli", "source_id": "S1", "quote": "Hooli is the biggest rival in the entire market"},
                   {"name": "Acme Corp", "source_id": "S1", "quote": "Acme Corp competes head to head"}]}
    lead_ = run_single(world, monkeypatch, company, [src])["leads"][0]
    assert lead_["competitors"] == [{"name": "Globex", "url": src["url"],
                                     "quote": "Acme Corp competes head to head with Globex in the platform"}]


# ------------------------------------------------------------------ the decision
def row(tier="A", verified="verified", signals=None, rivals=None, peers=None, score=85):
    return {"company_id": "co_1", "lead_id": "lead_1", "name": "Acme", "domain": "acme.com", "tier": tier,
            "fit_score": score, "verification_status": verified, "outreach_status": "none",
            "signals": signals if signals is not None else [STRONG], "rivals": rivals or [], "peers": peers or []}


STRONG = {"type": "Hiring", "strength": "strong", "url": "https://news.example.com/a", "quote": "q1",
          "source_type": "third_party"}
PRIMARY = {**STRONG, "type": "K8s", "url": "https://acme.com/blog", "quote": "q2", "source_type": "primary"}
WEAK = {"type": "Hiring", "strength": "weak", "url": "https://indeed.com/x", "quote": "q3", "source_type": "aggregator"}


@pytest.mark.parametrize("kwargs,rec,changed", [
    (dict(), "contact_now", False),
    (dict(signals=[WEAK]), "verify_first", True),                       # only job-board evidence
    (dict(signals=[]), "verify_first", True),
    (dict(verified="unverified"), "verify_first", True),
    (dict(rivals=[{"name": "Globex", "outreach_status": "replied"}]), "hold_rival_active", True),
    (dict(rivals=[{"name": "Globex", "outreach_status": "none"}]), "contact_now", False),
    (dict(tier="C", score=50, peers=["A", "B"]), "contact_now", True),   # promoted by peer momentum
    (dict(tier="C", score=50, peers=["A"]), "skip", False),
    (dict(tier="C", score=50, peers=["A", "B"], verified="unverified"), "skip", False),
    (dict(tier="C", score=50, peers=["A", "B"], signals=[WEAK]), "skip", False),
    (dict(tier="C", score=50), "skip", False),
])
def test_decide(kwargs, rec, changed):
    d = graph.decide(row(**kwargs))
    assert d["recommendation"] == rec and d["changed"] is changed and d["reasons"]


def test_hook_prefers_primary_evidence_and_only_exists_when_contacting():
    d = graph.decide(row(signals=[STRONG, PRIMARY]))
    assert d["hook"]["url"] == "https://acme.com/blog" and d["hook"]["signal"] == "K8s"
    assert graph.decide(row(signals=[WEAK]))["hook"] is None
    assert graph.decide(row(rivals=[{"name": "G", "outreach_status": "won"}]))["hook"] is None


def test_decisions_endpoint_runs_the_cypher_and_summarizes(table, world, neo):  # noqa: F811
    fields = ["company_id", "name", "domain", "lead_id", "tier", "fit_score", "verification_status",
              "outreach_status", "signals", "rivals", "peers"]
    neo["rows"]["OPTIONAL MATCH (c)-[:COMPETES_WITH]"] = (fields, [
        ["co_1", "Acme", "acme.com", "l1", "A", 85, "verified", "none", [STRONG], [], []],
        ["co_2", "Globex", "globex.io", "l2", "B", 70, "unverified", "none", [STRONG], [], []],
        ["co_3", "Hooli", "hooli.example", "l3", "C", 50, "verified", "none", [STRONG], [], ["Acme", "Globex"]],
    ])
    cid = approved_campaign()
    status, body = call("GET", f"/api/campaigns/{cid}/graph/decisions")
    assert status == 200
    d = body["data"]
    assert d["source"] == "neo4j"
    assert [x["recommendation"] for x in d["decisions"]] == ["contact_now", "verify_first", "contact_now"]
    assert d["summary"] == {"leads": 3, "changed": 2, "contact_now": 2, "verify_first": 1,
                            "hold_rival_active": 0, "skip": 0}
    sent = [c[2] for c in neo["calls"] if "COMPETES_WITH" in c[2]["statement"] and "peer" in c[2]["statement"]][0]
    assert sent["parameters"] == {"campaign_id": cid, "org_id": "demo"}


def test_graph_endpoint_assembles_nodes_and_edges_from_neo4j_rows(table, world, neo):  # noqa: F811
    cols = ["rel", "source", "source_label", "source_name", "target", "target_label", "target_name",
            "score", "tier", "url"]
    neo["rows"]["UNION"] = (cols, [
        ["MATCHED_TO", "co_1", "Company", "Acme", "cmp_1", "Product", "Pipeline Guard", 85, "A", None],
        ["HAS_SIGNAL", "co_1", "Company", "Acme", "sg_1", "Signal", "Hiring", None, "strong", None],
        ["SUPPORTED_BY", "sg_1", "Signal", "Hiring", "ev_1", "Evidence", "Post", None, "third_party", "https://n/x"],
        ["COMPETES_WITH", "co_1", "Company", "Acme", "cp_1", "Competitor", "Initrode", None, None, "https://n/y"],
    ])
    cid = approved_campaign()
    status, body = call("GET", f"/api/campaigns/{cid}/graph")
    g = body["data"]
    assert status == 200 and g["source"] == "neo4j" and g["counts"] == {"nodes": 5, "edges": 4}
    nodes = {n["id"]: n for n in g["nodes"]}
    assert nodes["co_1"]["tier"] == "A" and nodes["sg_1"]["strength"] == "strong"
    assert nodes["ev_1"]["url"] == "https://n/x" and nodes["cp_1"]["label"] == "Competitor"
    assert {e["type"] for e in g["edges"]} == {"MATCHED_TO", "HAS_SIGNAL", "SUPPORTED_BY", "COMPETES_WITH"}


def test_graph_routes_need_auth_campaign_and_configuration(table, world):  # noqa: F811
    cid = approved_campaign()
    for path in (f"/api/campaigns/{cid}/graph", f"/api/campaigns/{cid}/graph/decisions", "/api/system/graph"):
        assert call("GET", path, token=None)[0] == 401
    assert call("GET", "/api/campaigns/cmp_nope/graph")[0] == 404
    assert call("GET", f"/api/campaigns/{cid}/graph")[1]["error"]["code"] == "graph_not_configured"
    assert call("GET", "/api/system/graph")[1]["data"] == {"configured": False}


def test_cypher_is_parameterized_and_single_statement():
    for q in (graph.WRITE_LEADS, graph.GRAPH_CYPHER, graph.DECISION_CYPHER):
        assert "$" in q and ";" not in q
