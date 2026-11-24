"""Human-in-the-loop review of outreach drafts (FR-6).

State machine (per draft version):
    pending_review -> approved | rejected | superseded
    blocked        -> rejected | superseded        (Reviewer found too little supported content)
    approved       -> a Gmail *draft* is created in the connected mailbox (never sent)

* Editing never mutates a version: it creates a new, higher version (full history kept).
* Every transition is a conditional write, so retries and double clicks are safe.
* Approval records who approved and when. Rejection is terminal and blocks dispatch.
* Nothing here sends mail. After approval a Gmail draft is created if Gmail is connected and the
  recipient address came from the enrichment provider; otherwise the draft records a truthful
  `not_connected` / `no_recipient` / `failed` state. Leads stay "Matched": only a recorded dispatch
  may move a lead to Contacted.
"""
from __future__ import annotations

from pydantic import ValidationError

from integrations import gmail
from models.common import ApiError, new_id
from models.draft import DraftEdit
from persistence import dynamo
from services import audit, outreach

ACTOR = "human:founder"        # single-tenant demo identity; the audit trail still records it
EDITABLE = ("pending_review", "blocked")


def _find(org_id: str, cid: str, draft_id: str) -> dict:
    for d in dynamo.list_drafts(org_id, cid):
        if d.get("id") == draft_id:
            return d
    raise ApiError(404, "not_found", "Draft not found")


def _is_latest(org_id: str, d: dict) -> bool:
    latest = dynamo.latest_draft(org_id, d["campaign_id"], d["lead_id"])
    return bool(latest) and latest["SK"] == d["SK"]


def get(org_id: str, cid: str, draft_id: str) -> dict:
    return dynamo.public(_find(org_id, cid, draft_id))


def approve(org_id: str, cid: str, draft_id: str, operation_id: str, actor: str = ACTOR) -> dict:
    d = _find(org_id, cid, draft_id)
    if d["status"] == "approved":                    # idempotent retry; also retries a failed Gmail draft
        return dynamo.public(_ensure_gmail_draft(org_id, cid, d, operation_id))
    if d["status"] != "pending_review":
        raise ApiError(409, "invalid_transition", f"A {d['status']} draft cannot be approved")
    if not _is_latest(org_id, d):
        raise ApiError(409, "stale_version", "A newer version of this draft exists")
    now = dynamo.now_iso()
    if not dynamo.set_draft_fields(org_id, cid, d["SK"],
                                   {"status": "approved", "approved_by": actor, "approved_at": now},
                                   only_if_status=("pending_review",)):
        return _after_lost_race(org_id, cid, draft_id, "approved")
    audit.record(org_id, cid, actor, "draft.approved", d["id"],
                 {"lead_id": d["lead_id"], "version": int(d["version"])}, operation_id)
    return dynamo.public(_ensure_gmail_draft(org_id, cid, _find(org_id, cid, draft_id), operation_id))


def _ensure_gmail_draft(org_id: str, cid: str, d: dict, operation_id: str) -> dict:
    """Create the Gmail draft for an approved draft unless it already exists. Never sends."""
    if (d.get("gmail") or {}).get("status") in ("created", "no_recipient"):
        return d
    to = (d.get("recipient") or {}).get("email")
    state: dict = {"attempted_at": dynamo.now_iso()}
    if not to:
        state["status"] = "no_recipient"
    elif not gmail.configured():
        state["status"] = "not_connected"
    else:
        try:
            state["draft_id"] = gmail.create_draft(to, d["subject"], d["body"])
            state["status"] = "created"
        except ApiError as e:
            state.update({"status": "failed", "error_code": e.code})
    dynamo.set_draft_fields(org_id, cid, d["SK"], {"gmail": state}, only_if_status=("approved",))
    if state["status"] in ("created", "failed"):
        audit.record(org_id, cid, "system", f"draft.gmail_{state['status']}", d["id"],
                     {"lead_id": d["lead_id"], "version": int(d["version"]),
                      **({"error_code": state["error_code"]} if "error_code" in state else {})}, operation_id)
    return _find(org_id, cid, d["id"])


def reject(org_id: str, cid: str, draft_id: str, reason: str, operation_id: str, actor: str = ACTOR) -> dict:
    d = _find(org_id, cid, draft_id)
    if d["status"] == "rejected":
        return dynamo.public(d)
    if d["status"] not in EDITABLE:
        raise ApiError(409, "invalid_transition", f"A {d['status']} draft cannot be rejected")
    now = dynamo.now_iso()
    reason = (reason or "").strip()[:500]
    if not dynamo.set_draft_fields(org_id, cid, d["SK"],
                                   {"status": "rejected", "rejected_by": actor, "rejected_at": now,
                                    "rejection_reason": reason},
                                   only_if_status=EDITABLE):
        return _after_lost_race(org_id, cid, draft_id, "rejected")
    audit.record(org_id, cid, actor, "draft.rejected", d["id"],
                 {"lead_id": d["lead_id"], "version": int(d["version"]), "has_reason": bool(reason)}, operation_id)
    return dynamo.public(_find(org_id, cid, draft_id))


def edit(org_id: str, cid: str, draft_id: str, changes: dict, operation_id: str, actor: str = ACTOR) -> dict:
    try:
        upd = DraftEdit.model_validate(changes)
    except ValidationError as exc:
        raise ApiError(422, "validation_error", "Invalid draft edit",
                       [{"field": ".".join(map(str, e["loc"])), "msg": e["msg"]} for e in exc.errors()]) from None
    fields = {k: v.strip() for k, v in upd.model_dump(exclude_none=True).items()}
    if not fields:
        raise ApiError(422, "validation_error", "Provide a subject and/or body to change")
    d = _find(org_id, cid, draft_id)
    if d["status"] not in EDITABLE:
        raise ApiError(409, "invalid_transition", f"A {d['status']} draft cannot be edited")
    if not _is_latest(org_id, d):
        raise ApiError(409, "stale_version", "A newer version of this draft exists")

    subject = fields.get("subject", d["subject"])
    body = fields.get("body", d["body"])
    if subject == d["subject"] and body == d["body"]:
        return dynamo.public(d)                      # no-op edit creates no version

    # Human edits are not LLM-reviewed again, but the deterministic rules still run and are surfaced.
    allowed = outreach._allowed_numbers(" ".join(f["quote"] for f in d.get("facts", [])),
                                        f"{d['company_name']}")
    warnings = []
    for label, text in (("subject", subject), *[("body", s["text"]) for s in outreach.segments(body)]):
        why = outreach.rule_violation(text, allowed)
        if why:
            warnings.append({"where": label, "sentence": text[:200], "reason": why})

    version = int(d["version"]) + 1
    item = {
        **{k: v for k, v in d.items() if k not in ("approved_by", "approved_at", "rejected_by", "rejected_at",
                                                    "rejection_reason")},
        "SK": dynamo.draft_sk(d["lead_id"], version), "id": new_id("drf"), "version": version,
        "status": "pending_review", "subject": subject, "body": body, "source": "human",
        "parent_version": int(d["version"]), "edit_warnings": warnings,
        "edited_by": actor, "created_by": actor, "created_at": dynamo.now_iso(),
    }
    try:
        dynamo.put_draft(item)
    except Exception as exc:
        if type(exc).__name__ == "ConditionalCheckFailedException":
            raise ApiError(409, "conflict", "The draft changed at the same time; reload and retry") from None
        raise
    dynamo.set_draft_fields(org_id, cid, d["SK"], {"status": "superseded"}, only_if_status=EDITABLE)
    audit.record(org_id, cid, actor, "draft.edited", item["id"],
                 {"lead_id": d["lead_id"], "version": version, "fields": sorted(fields),
                  "rule_warnings": len(warnings)}, operation_id)
    return dynamo.public(item)


def _after_lost_race(org_id: str, cid: str, draft_id: str, wanted: str) -> dict:
    cur = _find(org_id, cid, draft_id)
    if cur["status"] == wanted:
        return dynamo.public(cur)
    raise ApiError(409, "invalid_transition", f"The draft is now {cur['status']}")
