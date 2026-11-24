"""Append-only audit trail (AuditEvent). Never pass secrets or raw PII in `details`."""
from __future__ import annotations

from typing import Any

from models.common import new_id
from persistence import dynamo


def record(org_id: str, campaign_id: str, actor: str, action: str,
           entity_id: str | None = None, details: dict[str, Any] | None = None,
           operation_id: str | None = None) -> dict:
    ts = dynamo.now_iso()
    eid = new_id("evt")
    item = {
        "PK": dynamo.pk(org_id, campaign_id),
        "SK": f"EVT#{ts}#{eid}",
        "id": eid,
        "org_id": org_id,
        "campaign_id": campaign_id,
        "actor": actor,            # "human:<id>" | "agent:<role>" | "system"
        "action": action,
        "entity_id": entity_id,
        "details": details or {},
        "operation_id": operation_id,
        "occurred_at": ts,
    }
    dynamo.put_event(item)
    return item
