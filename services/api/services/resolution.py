"""Uncertainty resolution for `verify_first` leads.

The graph decision says `verify_first` when the one thing standing between a lead and outreach is a
missing fact: no buying signal backed by a non-job-board source, or a domain no retrieved page
confirmed. This service goes and looks for exactly that fact, with at most two targeted searches,
then recomputes the decision with the same pure `graph.decide`:

  * the fact is found (verbatim quote naming the company, or a page hosted on its domain) -> the
    evidence is stored on the lead and the recommendation becomes `contact_now`
  * it is not found                                                                         -> abstain:
    the recommendation stays `verify_first`, nothing is added, and the brief says what would help

Nothing is invented: a found fact is a sentence copied from a search result, with its URL.
Each lead may be researched at most MAX_ATTEMPTS times so credits cannot be burned in a loop.
"""
from __future__ import annotations

import re

from integrations import tavily
from models.common import ApiError
from persistence import dynamo
from services import audit, discovery, firmographics, graph

MAX_ATTEMPTS = 2
MAX_QUOTE_CHARS = 300
MIN_OVERLAP_WORDS = 2
_WORD = re.compile(r"[a-z]{4,}")
_STOP = {"that", "with", "this", "from", "have", "been", "will", "their", "which", "into", "more", "than"}
_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


def _words(text: str) -> set[str]:
    return set(_WORD.findall(text.casefold())) - _STOP


def decision_row(org_id: str, campaign: dict, lead: dict, all_leads: list[dict]) -> dict:
    """The row DECISION_CYPHER would return for this lead, built from DynamoDB alone. Rivals come from
    competitor quotes that name another lead in the same campaign."""
    projected = graph.project(org_id, campaign, [lead])["leads"][0]
    signals = [{"type": s["type"], "strength": s["strength"], "url": s["url"], "quote": s["quote"],
                "source_type": s["source_type"]} for s in projected["signals"]]
    by_name = {graph._name_key(l["company_name"]): l for l in all_leads if l["id"] != lead["id"]}
    rivals = []
    for r in lead.get("competitors", []):
        other = by_name.get(graph._name_key(r["name"]))
        if other:
            rivals.append({"name": other["company_name"], "domain": other["domain"],
                           "outreach_status": other.get("status"), "tier": other.get("tier")})
    return {"company_id": projected["company_id"], "name": lead["company_name"], "domain": lead["domain"],
            "lead_id": lead["id"], "tier": lead["tier"], "fit_score": lead["fit_score"],
            "verification_status": lead.get("verification_status", "unverified"),
            "signals": signals, "rivals": rivals, "peers": []}


def missing_facts(decision: dict, lead: dict) -> list[str]:
    facts = []
    if not any(e.get("scored") and e.get("signal") and e.get("source_type") != "aggregator"
               for e in lead.get("evidence", [])):
        facts.append("buying_signal")
    if lead.get("verification_status") != "verified":
        facts.append("domain")
    return facts


def _signal_target(lead: dict, icp_signals: list[str]) -> str | None:
    """Prefer the signal we only saw on a job board (corroborate it); else the first unsupported one."""
    strong = {e["signal"] for e in lead.get("evidence", [])
              if e.get("scored") and e.get("signal") and e.get("source_type") != "aggregator"}
    weak = [e["signal"] for e in lead.get("evidence", []) if e.get("scored") and e.get("signal")
            and e.get("source_type") == "aggregator" and e["signal"] not in strong]
    unsupported = [s for s in icp_signals if s not in strong]
    return (weak or unsupported or [None])[0]


def find_signal(lead: dict, signal: str) -> tuple[dict | None, int]:
    """One targeted search. Returns (evidence item or None, results seen)."""
    name = lead["company_name"]
    results = tavily.search(f'"{name}" {signal}'[:300], max_results=6, timeout=8)
    used = {e["url"] for e in lead.get("evidence", []) if e.get("scored")}
    pat = firmographics._name_pattern(name)
    target = _words(signal)
    need = min(MIN_OVERLAP_WORDS, len(target))
    for r in results:
        if r["url"] in used:
            continue
        kind = firmographics.classify_source(r["url"], lead["domain"])
        if kind == "aggregator" or not pat:
            continue
        for sentence in _SPLIT.split(r["content"]):
            s = sentence.strip()
            if (pat.search(s) and discovery.substantial(s) and len(s) <= MAX_QUOTE_CHARS
                    and len(_words(s) & target) >= need):
                return ({"source_id": None, "url": r["url"], "title": r["title"], "quote": s,
                         "signal": signal, "scored": True, "source_type": kind,
                         "found_by": "resolution"}, len(results))
    return None, len(results)


def find_domain(lead: dict) -> tuple[str | None, int]:
    results = tavily.search(f"{lead['company_name']} about company", max_results=3, timeout=8,
                            include_domains=[lead["domain"]])
    official = next((r["url"] for r in results if firmographics.host_matches(r["url"], lead["domain"])), None)
    return official, len(results)


def brief(before: dict, after: dict, lead: dict, steps: list[dict]) -> dict:
    """What we know / what was uncertain / what would make this actionable."""
    know = [f"{lead['company_name']} scored {lead['fit_score']} (tier {lead['tier']})"]
    know += [f"Signal '{e['signal']}' ({'weak' if e.get('source_type') == 'aggregator' else 'strong'}) - {e['url']}"
             for e in lead.get("evidence", []) if e.get("scored") and e.get("signal")]
    if lead.get("verification_status") == "verified":
        know.append(f"Domain {lead['domain']} confirmed by a retrieved page")
    uncertain = list(before["reasons"])
    still = [s for s in steps if not s["found"]]
    if after["recommendation"] == "contact_now":
        needed = ["Nothing further: the missing fact was found. A human still approves the draft."]
    else:
        needed = [f"A {'non-job-board source stating the buying signal' if s['fact'] == 'buying_signal' else 'page hosted on ' + lead['domain']}"
                  for s in still] or ["A human decision"]
    return {"known": know, "uncertain": uncertain, "would_make_actionable": needed}


def resolve(org_id: str, campaign: dict, lead_id: str, operation_id: str) -> dict:
    cid = campaign["id"]
    lead = dynamo.get_lead_by_id(org_id, cid, lead_id)
    if not lead:
        raise ApiError(404, "not_found", "Lead not found")
    lead = dynamo.clean(lead)
    all_leads = [dynamo.clean(i) for i in dynamo.list_leads(org_id, cid)]
    before = graph.decide(decision_row(org_id, campaign, lead, all_leads))
    if before["recommendation"] != "verify_first":
        raise ApiError(409, "nothing_to_resolve",
                       f"Recommendation is {before['recommendation']}; only verify_first leads can be resolved")
    attempts = int((lead.get("resolution") or {}).get("attempts", 0))
    if attempts >= MAX_ATTEMPTS:
        raise ApiError(409, "resolution_exhausted", f"This lead was already researched {attempts} times")
    if not tavily.configured():
        raise ApiError(503, "search_not_configured", "Tavily is not configured (TAVILY_API_KEY missing)")

    icp = dynamo.latest_icp(org_id, cid) or {}
    icp_signals = list((dynamo.clean(icp.get("fields") or {})).get("buying_signals", []))
    steps: list[dict] = []
    evidence = list(lead.get("evidence", []))
    updates: dict = {}
    errors: list[ApiError] = []

    for fact in missing_facts(before, lead):
        try:
            if fact == "buying_signal":
                target = _signal_target(lead, icp_signals)
                if not target:
                    steps.append({"fact": fact, "searched": False, "found": False, "detail": "no ICP signal to look for"})
                    continue
                item, seen = find_signal(lead, target)
                if item:
                    evidence.append(item)
                steps.append({"fact": fact, "searched": True, "results": seen, "found": bool(item),
                              "detail": item["url"] if item else f"no non-job-board source stated: {target}"})
            else:
                url, seen = find_domain(lead)
                if url:
                    updates["verification_status"] = "verified"
                    updates["domain_check"] = {"basis": "search_result", "url": url}
                steps.append({"fact": fact, "searched": True, "results": seen, "found": bool(url),
                              "detail": url or f"no page hosted on {lead['domain']} was returned"})
        except ApiError as exc:
            errors.append(exc)
            steps.append({"fact": fact, "searched": True, "found": False, "detail": exc.code})
    if errors and len(errors) == len(steps):
        raise errors[0]          # nothing could be searched: report it, charge nothing

    updated = {**lead, **updates, "evidence": evidence}
    after = graph.decide(decision_row(org_id, campaign, updated, all_leads))
    outcome = "resolved" if after["recommendation"] == "contact_now" else "abstain"
    record = {"attempts": attempts + 1, "outcome": outcome, "at": dynamo.now_iso(), "steps": steps}
    dynamo.update_lead(org_id, cid, lead["domain"], {**updates, "evidence": evidence, "resolution": record})
    audit.record(org_id, cid, "agent:researcher", f"lead.resolution_{outcome}", lead["id"],
                 {"fact_steps": len(steps), "found": sum(s["found"] for s in steps),
                  "recommendation": after["recommendation"]}, operation_id)
    return {"lead_id": lead["id"], "outcome": outcome, "before": before["recommendation"],
            "after": after["recommendation"], "steps": steps, "reasons": after["reasons"],
            "attempts": attempts + 1, "brief": brief(before, after, updated, steps)}
