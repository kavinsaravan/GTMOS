"""Knowledge graph (Neo4j): projection of the DynamoDB leads + the outreach decision query.

DynamoDB stays the source of truth for pipeline state. The graph holds relationships:

  (Company)-[:MATCHED_TO {fit_score, tier, verification_status, outreach_status}]->(Product)
  (Company)-[:HAS_SIGNAL]->(Signal {type, strength})-[:SUPPORTED_BY]->(Evidence {url, quote})
  (Company)-[:COMPETES_WITH {url, quote}]->(Company | Competitor)      # only when a source says so

Every node id is derived from stable DynamoDB identifiers, and every write is a MERGE, so
syncing the same campaign twice is a no-op (retry-safe). Nothing is invented: signals and
competitor edges exist only where the lead carries a verified quote.

THE DECISION QUERY (DECISION_CYPHER) traverses those relationships to answer, per lead, a
question DynamoDB alone cannot: is it safe and smart to contact this company *now*?
  * a competitor of this lead is already in an active conversation        -> hold_rival_active
  * the lead's buying signal rests only on job-board pages / domain unverified -> verify_first
  * a low-tier lead shares a strong signal with >= 2 other strong leads       -> promoted (peer momentum)
and it picks the evidence-backed hook the outreach draft should open with.
"""
from __future__ import annotations

import hashlib
import re

from integrations import neo4j
from models.common import ApiError
from persistence import dynamo
from services import audit

ACTIVE_RIVAL_STATUSES = {"contacted", "replied", "won"}
PEER_THRESHOLD = 2
BASELINE = {"A": "contact_now", "B": "contact_now", "C": "skip"}   # what the score alone says

CONSTRAINTS = [f"CREATE CONSTRAINT {n}_id IF NOT EXISTS FOR (n:{l}) REQUIRE n.id IS UNIQUE"
               for n, l in (("company", "Company"), ("product", "Product"), ("signal", "Signal"),
                            ("evidence", "Evidence"), ("competitor", "Competitor"))]
_constraints_done = False

WRITE_LEADS = """
MERGE (p:Product {id: $product.id})
SET p.org_id = $product.org_id, p.name = $product.name, p.description = $product.description
WITH p
UNWIND $leads AS l
MERGE (c:Company {id: l.company_id})
SET c.domain = l.domain, c.name = l.name, c.org_id = l.org_id
MERGE (c)-[m:MATCHED_TO]->(p)
SET m.lead_id = l.lead_id, m.fit_score = l.fit_score, m.tier = l.tier,
    m.verification_status = l.verification_status, m.employees = l.employees,
    m.icp_version = l.icp_version, m.outreach_status = coalesce(m.outreach_status, 'none')
WITH c, p, l
UNWIND l.signals AS s
MERGE (sig:Signal {id: s.id})
SET sig.type = s.type, sig.strength = s.strength, sig.campaign_id = p.id, sig.org_id = l.org_id
MERGE (c)-[:HAS_SIGNAL]->(sig)
MERGE (e:Evidence {id: s.evidence_id})
SET e.url = s.url, e.title = s.title, e.quote = s.quote, e.source_type = s.source_type
MERGE (sig)-[:SUPPORTED_BY]->(e)
RETURN count(DISTINCT sig) AS signals
"""

WRITE_RIVAL_COMPANIES = """
UNWIND $rivals AS r
MATCH (c:Company {id: r.company_id})
MATCH (k:Company {id: r.target_id})
MERGE (c)-[x:COMPETES_WITH]->(k)
SET x.url = r.url, x.quote = r.quote
RETURN count(x) AS edges
"""

WRITE_RIVAL_OUTSIDERS = """
UNWIND $rivals AS r
MATCH (c:Company {id: r.company_id})
MERGE (k:Competitor {id: r.target_id})
SET k.name = r.name
MERGE (c)-[x:COMPETES_WITH]->(k)
SET x.url = r.url, x.quote = r.quote
RETURN count(x) AS edges
"""

# Parameterized traversal behind GET /graph: every edge in this campaign, as rows.
GRAPH_CYPHER = """
MATCH (c:Company)-[m:MATCHED_TO]->(p:Product {id: $campaign_id, org_id: $org_id})
RETURN 'MATCHED_TO' AS rel, c.id AS source, 'Company' AS source_label, c.name AS source_name,
       p.id AS target, 'Product' AS target_label, p.name AS target_name,
       m.fit_score AS score, m.tier AS tier, null AS url
UNION
MATCH (c:Company)-[:MATCHED_TO]->(:Product {id: $campaign_id, org_id: $org_id})
MATCH (c)-[:HAS_SIGNAL]->(s:Signal {campaign_id: $campaign_id})
RETURN 'HAS_SIGNAL' AS rel, c.id AS source, 'Company' AS source_label, c.name AS source_name,
       s.id AS target, 'Signal' AS target_label, s.type AS target_name,
       null AS score, s.strength AS tier, null AS url
UNION
MATCH (:Company)-[:HAS_SIGNAL]->(s:Signal {campaign_id: $campaign_id, org_id: $org_id})-[:SUPPORTED_BY]->(e:Evidence)
RETURN 'SUPPORTED_BY' AS rel, s.id AS source, 'Signal' AS source_label, s.type AS source_name,
       e.id AS target, 'Evidence' AS target_label, e.title AS target_name,
       null AS score, e.source_type AS tier, e.url AS url
UNION
MATCH (c:Company)-[:MATCHED_TO]->(:Product {id: $campaign_id, org_id: $org_id})
MATCH (c)-[x:COMPETES_WITH]->(k)
RETURN 'COMPETES_WITH' AS rel, c.id AS source, 'Company' AS source_label, c.name AS source_name,
       k.id AS target, head(labels(k)) AS target_label, k.name AS target_name,
       null AS score, null AS tier, x.url AS url
"""

# THE decision query. One parameterized traversal; the decision logic over its rows is decide().
DECISION_CYPHER = """
MATCH (c:Company)-[m:MATCHED_TO]->(p:Product {id: $campaign_id, org_id: $org_id})
OPTIONAL MATCH (c)-[:HAS_SIGNAL]->(s:Signal {campaign_id: $campaign_id})-[:SUPPORTED_BY]->(e:Evidence)
WITH c, m, collect(DISTINCT CASE WHEN s IS NULL THEN null ELSE
     {type: s.type, strength: s.strength, url: e.url, quote: e.quote, source_type: e.source_type} END) AS signals
OPTIONAL MATCH (c)-[:COMPETES_WITH]-(r:Company)-[rm:MATCHED_TO]->(:Product {id: $campaign_id, org_id: $org_id})
WITH c, m, signals, collect(DISTINCT CASE WHEN r IS NULL THEN null ELSE
     {name: r.name, domain: r.domain, outreach_status: rm.outreach_status, tier: rm.tier} END) AS rivals
OPTIONAL MATCH (c)-[:HAS_SIGNAL]->(mine:Signal {campaign_id: $campaign_id, strength: 'strong'})
OPTIONAL MATCH (peer:Company)-[:HAS_SIGNAL]->(theirs:Signal {campaign_id: $campaign_id, strength: 'strong'})
  WHERE peer <> c AND theirs.type = mine.type
WITH c, m, signals, rivals, collect(DISTINCT peer.name) AS peers
RETURN c.id AS company_id, c.name AS name, c.domain AS domain, m.lead_id AS lead_id,
       m.tier AS tier, m.fit_score AS fit_score, m.verification_status AS verification_status,
       m.outreach_status AS outreach_status, signals, rivals, peers
ORDER BY fit_score DESC
"""


# ------------------------------------------------------------------ ids / projection
def _h(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]


def company_id(org_id: str, domain: str) -> str:
    return f"co_{_h(org_id, domain)}"


def _name_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.casefold())


def project(org_id: str, campaign: dict, leads: list[dict]) -> dict:
    """Pure: DynamoDB leads -> parameters for the write statements."""
    cid = campaign["id"]
    by_name = {_name_key(l["company_name"]): l for l in leads}
    out_leads, rivals_in, rivals_out = [], [], []
    for l in leads:
        co = company_id(org_id, l["domain"])
        signals = []
        for e in l.get("evidence", []):
            if not (e.get("signal") and e.get("scored")):
                continue
            strength = "weak" if e.get("source_type") == "aggregator" else "strong"
            signals.append({
                "id": f"sg_{_h(l['id'], e['signal'])}", "type": e["signal"], "strength": strength,
                "evidence_id": f"ev_{_h(l['id'], e['url'], e['quote'])}", "url": e["url"],
                "title": e.get("title", ""), "quote": e["quote"], "source_type": e.get("source_type")})
        out_leads.append({
            "company_id": co, "lead_id": l["id"], "domain": l["domain"], "name": l["company_name"],
            "org_id": org_id, "fit_score": l["fit_score"], "tier": l["tier"],
            "verification_status": l.get("verification_status", "unverified"),
            "employees": l.get("employees"), "icp_version": l.get("icp_version"), "signals": signals})
        for r in l.get("competitors", []):
            other = by_name.get(_name_key(r["name"]))
            if other and other["id"] != l["id"]:
                rivals_in.append({"company_id": co, "target_id": company_id(org_id, other["domain"]),
                                  "url": r["url"], "quote": r["quote"]})
            elif not other:
                rivals_out.append({"company_id": co, "target_id": f"cp_{_h(org_id, _name_key(r['name']))}",
                                   "name": r["name"], "url": r["url"], "quote": r["quote"]})
    return {"product": {"id": cid, "org_id": org_id, "name": campaign.get("product_name", ""),
                        "description": (campaign.get("criteria") or {}).get("description", "")},
            "leads": out_leads, "rivals_in": rivals_in, "rivals_out": rivals_out}


# ------------------------------------------------------------------ writes
def _ensure_constraints() -> None:
    global _constraints_done
    if not _constraints_done:
        for stmt in CONSTRAINTS:
            neo4j.run(stmt)
        _constraints_done = True


def sync(org_id: str, campaign: dict, leads: list[dict] | None = None) -> dict:
    """Idempotent upsert of the campaign's leads, signals, evidence and competitor edges."""
    if not neo4j.configured():
        raise ApiError(503, "graph_not_configured", "Neo4j is not configured (NEO4J_URI / NEO4J_PASSWORD missing)")
    leads = leads if leads is not None else [dynamo.clean(i) for i in dynamo.list_leads(org_id, campaign["id"])]
    if not leads:
        return {"companies": 0, "signals": 0, "competitor_edges": 0}
    p = project(org_id, campaign, leads)
    _ensure_constraints()
    rows = neo4j.run(WRITE_LEADS, {"product": p["product"], "leads": p["leads"]})
    edges = 0
    for stmt, key in ((WRITE_RIVAL_COMPANIES, "rivals_in"), (WRITE_RIVAL_OUTSIDERS, "rivals_out")):
        if p[key]:
            edges += sum(r.get("edges", 0) for r in neo4j.run(stmt, {"rivals": p[key]}))
    return {"companies": len(p["leads"]), "signals": sum(r.get("signals", 0) for r in rows),
            "competitor_edges": edges}


def sync_best_effort(org_id: str, campaign: dict, leads: list[dict], operation_id: str | None) -> dict:
    """Used by discovery: the graph must never fail a run, but its status is always visible."""
    cid = campaign["id"]
    if not neo4j.configured():
        return {"status": "not_configured"}
    try:
        result = sync(org_id, campaign, leads)
    except ApiError as e:
        audit.record(org_id, cid, "agent:builder", "graph.sync_failed", cid, {"code": e.code}, operation_id)
        return {"status": "failed", "code": e.code}
    audit.record(org_id, cid, "agent:builder", "graph.synced", cid, result, operation_id)
    return {"status": "synced", **result}


# ------------------------------------------------------------------ reads
def graph(org_id: str, campaign_id: str) -> dict:
    """Nodes and edges exactly as Neo4j returns them. Nothing here is hardcoded."""
    rows = neo4j.run(GRAPH_CYPHER, {"campaign_id": campaign_id, "org_id": org_id})
    nodes: dict[str, dict] = {}
    edges = []
    for r in rows:
        nodes.setdefault(r["source"], {"id": r["source"], "label": r["source_label"], "name": r["source_name"]})
        node = nodes.setdefault(r["target"], {"id": r["target"], "label": r["target_label"], "name": r["target_name"]})
        if r["rel"] == "MATCHED_TO":
            nodes[r["source"]].update({"fit_score": r["score"], "tier": r["tier"]})
        if r["rel"] == "SUPPORTED_BY":
            node["url"], node["source_type"] = r["url"], r["tier"]
        if r["rel"] == "HAS_SIGNAL":
            node["strength"] = r["tier"]
        edges.append({"source": r["source"], "target": r["target"], "type": r["rel"],
                      **({"url": r["url"]} if r["rel"] == "COMPETES_WITH" else {})})
    return {"nodes": list(nodes.values()), "edges": edges,
            "counts": {"nodes": len(nodes), "edges": len(edges)}, "source": "neo4j"}


def decide(row: dict) -> dict:
    """Pure decision over one row of DECISION_CYPHER."""
    tier = row.get("tier") or "C"
    signals = [s for s in row.get("signals", []) if s and s.get("type")]
    strong = [s for s in signals if s.get("strength") == "strong"]
    rivals = [r for r in row.get("rivals", []) if r]
    peers = sorted(set(row.get("peers", [])))
    active = [r for r in rivals if r.get("outreach_status") in ACTIVE_RIVAL_STATUSES]
    baseline = BASELINE.get(tier, "skip")
    verified = row.get("verification_status") == "verified"
    reasons: list[str] = []

    if active and baseline == "contact_now":
        rec = "hold_rival_active"
        reasons.append(f"Competitor {active[0]['name']} is already {active[0]['outreach_status']}; "
                       "don't run two conversations in one market segment at once")
    elif baseline == "contact_now" and not strong:
        rec = "verify_first"
        reasons.append("No buying signal backed by a primary or third-party source; the only evidence is a "
                       "job-board or list page" if signals else "No buying signal evidence in the graph")
    elif baseline == "contact_now" and not verified:
        rec = "verify_first"
        reasons.append("The company's domain was not confirmed by any retrieved page")
    elif baseline == "contact_now":
        rec = "contact_now"
        reasons.append(f"{len(strong)} strong signal(s) backed by cited sources and a verified domain")
    elif strong and verified and len(peers) >= PEER_THRESHOLD and not active:
        rec = "contact_now"
        reasons.append(f"Low score, but it shares a strong signal with {len(peers)} other leads "
                       f"({', '.join(peers[:3])}): promoted on peer momentum")
    else:
        rec = "skip"
        reasons.append("Score is below the contact threshold and the graph found nothing to change that")

    hook = None
    if rec == "contact_now" and strong:
        order = {"primary": 0, "third_party": 1}
        best = sorted(strong, key=lambda s: order.get(s.get("source_type"), 2))[0]
        hook = {"signal": best["type"], "quote": best["quote"], "url": best["url"],
                "source_type": best.get("source_type")}
    return {"company_id": row["company_id"], "lead_id": row.get("lead_id"), "name": row["name"],
            "domain": row["domain"], "tier": tier, "fit_score": row.get("fit_score"),
            "baseline": baseline, "recommendation": rec, "changed": rec != baseline,
            "reasons": reasons, "hook": hook, "peers": peers,
            "rivals": [{"name": r["name"], "outreach_status": r.get("outreach_status")} for r in rivals]}


def decisions(org_id: str, campaign_id: str) -> dict:
    rows = neo4j.run(DECISION_CYPHER, {"campaign_id": campaign_id, "org_id": org_id})
    items = [decide(r) for r in rows]
    return {"decisions": items, "source": "neo4j",
            "summary": {"leads": len(items), "changed": sum(i["changed"] for i in items),
                        **{k: sum(i["recommendation"] == k for i in items)
                           for k in ("contact_now", "verify_first", "hold_rival_active", "skip")}}}
