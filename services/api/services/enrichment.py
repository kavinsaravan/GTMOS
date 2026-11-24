"""Contact enrichment for a lead via Hunter.

Contacts are stored exactly as the provider returned them (with provider-reported
verification status and source pages). We never invent or pattern-guess addresses.
Ranking only decides which real contacts to show first.
"""
from __future__ import annotations

import re

from integrations import hunter
from models.common import ApiError
from persistence import dynamo
from services import audit

MAX_CONTACTS = 5
# Provider verdicts that make an address unusable for outreach.
UNUSABLE = {"invalid", "disposable", "webmail"}
SENIORITY_POINTS = {"executive": 3, "senior": 2, "junior": 0}
VERIFICATION_POINTS = {"valid": 3, "accept_all": 1, "unknown": 0}
_WORD = re.compile(r"[a-z]{3,}")


def _words(text: str) -> set[str]:
    return set(_WORD.findall((text or "").lower()))


def _persona_titles(org_id: str, campaign_id: str) -> list[str]:
    latest = dynamo.latest_icp(org_id, campaign_id) or {}
    personas = (latest.get("fields") or {}).get("buyer_personas") or latest.get("buyer_personas") or []
    return [str(p.get("title", "")) for p in personas if isinstance(p, dict) and p.get("title")]


def best_persona(position: str, personas: list[str]) -> tuple[str | None, int]:
    """Persona title sharing the most words with the contact's position."""
    pos = _words(position)
    best, hits = None, 0
    for title in personas:
        n = len(pos & _words(title))
        if n > hits:
            best, hits = title, n
    return best, hits


def rank(contacts: list[dict], personas: list[str]) -> list[dict]:
    ranked = []
    for c in contacts:
        if c["verification_status"] in UNUSABLE:
            continue
        persona, hits = best_persona(c["position"], personas)
        score = (hits * 4 + SENIORITY_POINTS.get(c["seniority"], 1)
                 + VERIFICATION_POINTS.get(c["verification_status"], 0) + c["confidence"] / 50)
        ranked.append({**c, "matched_persona": persona, "rank_score": round(score, 2)})
    ranked.sort(key=lambda c: c["rank_score"], reverse=True)
    return ranked[:MAX_CONTACTS]


def enrich(org_id: str, campaign: dict, lead_id: str, operation_id: str) -> dict:
    cid = campaign["id"]
    lead = dynamo.get_lead_by_id(org_id, cid, lead_id)
    if not lead:
        raise ApiError(404, "not_found", "Lead not found")
    found = hunter.domain_search(lead["domain"])  # raises typed ApiError when unavailable
    contacts = rank(found, _persona_titles(org_id, cid))
    enrichment = {
        "provider": "hunter", "enriched_at": dynamo.now_iso(),
        "status": "enriched" if contacts else "no_contacts",
        "provider_results": len(found), "usable_contacts": len(contacts),
    }
    dynamo.update_lead(org_id, cid, lead["domain"], {"contacts": contacts, "enrichment": enrichment})
    audit.record(org_id, cid, "system", "lead.enriched", lead["id"],
                 {"domain": lead["domain"], "status": enrichment["status"],
                  "usable_contacts": len(contacts)}, operation_id)
    return {"lead_id": lead["id"], "domain": lead["domain"], "enrichment": enrichment, "contacts": contacts}
