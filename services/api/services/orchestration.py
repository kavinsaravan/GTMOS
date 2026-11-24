"""Band multi-agent orchestration runs: the durable record and trace of an agent handoff chain.

The Lambda cannot host a Band websocket listener, so the three agents (Planner, Researcher,
Reviewer) run as separate long-lived processes (see /agents). This module only stores the run and
the trace those agents report: each trace entry carries the Band room id and the Band message id
of a real @mention handoff, so it can be checked against the room transcript on Band.
Agents never approve or send anything: a finished run ends at `awaiting_approval` (human review).
"""
from __future__ import annotations

from models.common import ApiError, new_id
from persistence import dynamo
from services import audit

ROLES = ("planner", "researcher", "reviewer")
KINDS = ("claimed", "room_created", "handoff_sent", "message_received", "work_done", "work_failed", "completed")
STATUSES = ("queued", "planning", "researching", "reviewing", "awaiting_approval", "failed")
ACTIVE = ("planning", "researching", "reviewing")


def create(org_id: str, campaign: dict, lead_id: str, operation_id: str) -> dict:
    if not isinstance(lead_id, str) or not lead_id:
        raise ApiError(422, "validation_error", "lead_id is required")
    lead = dynamo.get_lead_by_id(org_id, campaign["id"], lead_id)
    if not lead:
        raise ApiError(404, "not_found", "Lead not found")
    for run in dynamo.list_orch(org_id, campaign["id"]):
        if run.get("lead_id") == lead_id and run.get("status") in ("queued",) + ACTIVE:
            raise ApiError(409, "run_in_progress", "An orchestration run for this lead is already in progress")
    rid, ts = new_id("orc"), dynamo.now_iso()
    item = {
        "PK": dynamo.pk(org_id, campaign["id"]), "SK": dynamo.orch_sk(rid),
        "GSI1PK": dynamo.orch_gsi(org_id), "GSI1SK": ts,
        "id": rid, "org_id": org_id, "campaign_id": campaign["id"], "lead_id": lead_id,
        "lead_name": lead.get("company") or lead.get("name") or lead.get("domain") or lead_id,
        "status": "queued", "trace": [], "created_at": ts, "updated_at": ts,
    }
    dynamo.put_orch(item)
    audit.record(org_id, campaign["id"], "human:founder", "orchestration.requested", rid,
                 {"lead_id": lead_id}, operation_id)
    return dynamo.public(item)


def list_runs(org_id: str, campaign_id: str) -> list[dict]:
    runs = [dynamo.public(i) for i in dynamo.list_orch(org_id, campaign_id)]
    return sorted(runs, key=lambda r: r["created_at"], reverse=True)


def queued(org_id: str) -> list[dict]:
    return [dynamo.public(i) for i in dynamo.list_orch_by_status(org_id, "queued")]


def _get(org_id: str, campaign_id: str, run_id: str) -> dict:
    run = dynamo.get_orch(org_id, campaign_id, run_id)
    if not run:
        raise ApiError(404, "not_found", "Orchestration run not found")
    return run


def claim(org_id: str, campaign_id: str, run_id: str, operation_id: str) -> dict:
    """The Planner takes a queued run. Race-safe: a second claim gets 409."""
    _get(org_id, campaign_id, run_id)
    event = {"at": dynamo.now_iso(), "agent": "planner", "kind": "claimed", "summary": "Planner claimed the run"}
    if not dynamo.transition_orch(org_id, campaign_id, run_id, new_status="planning",
                                  only_from=("queued",), event=event):
        raise ApiError(409, "already_claimed", "Run is not queued")
    audit.record(org_id, campaign_id, "agent:planner", "orchestration.claimed", run_id, {}, operation_id)
    return dynamo.public(_get(org_id, campaign_id, run_id))


def add_event(org_id: str, campaign_id: str, run_id: str, body: dict, operation_id: str) -> dict:
    if not isinstance(body, dict):
        raise ApiError(422, "validation_error", "Body must be a JSON object")
    agent, kind = body.get("agent"), body.get("kind")
    if agent not in ROLES:
        raise ApiError(422, "validation_error", f"agent must be one of {ROLES}")
    if kind not in KINDS:
        raise ApiError(422, "validation_error", f"kind must be one of {KINDS}")
    status = body.get("status")
    if status is not None and status not in STATUSES:
        raise ApiError(422, "validation_error", f"status must be one of {STATUSES}")
    run = _get(org_id, campaign_id, run_id)
    event = {
        "at": dynamo.now_iso(), "agent": agent, "kind": kind,
        "summary": str(body.get("summary", ""))[:500],
        "band_room_id": str(body.get("band_room_id", ""))[:80] or None,
        "band_message_id": str(body.get("band_message_id", ""))[:80] or None,
        "mentioned": str(body.get("mentioned", ""))[:80] or None,
    }
    fields = {}
    if body.get("band_room_id"):
        fields["band_room_id"] = event["band_room_id"]
    if body.get("draft_id"):
        fields["draft_id"] = str(body["draft_id"])[:80]
    if status and status != run["status"] and run["status"] in ("failed", "awaiting_approval"):
        raise ApiError(409, "run_finished", "Run is already finished")
    dynamo.transition_orch(org_id, campaign_id, run_id, new_status=status, event=event, fields=fields)
    audit.record(org_id, campaign_id, f"agent:{agent}", f"orchestration.{kind}", run_id,
                 {"band_message_id": event["band_message_id"], "mentioned": event["mentioned"]}, operation_id)
    return dynamo.public(_get(org_id, campaign_id, run_id))
