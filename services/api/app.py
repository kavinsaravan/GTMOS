"""Lambda entrypoint for API Gateway (HTTP API, payload v2).

Routes are registered in ROUTES. Every response uses the uniform envelope and
carries an operation_id that is also written to CloudWatch for tracing.
"""
from __future__ import annotations

import base64
import hmac
import json
import os
import re
from typing import Any, Callable

from pydantic import ValidationError

from models.campaign import CampaignCreate
from models.common import ApiError, err, new_id, ok
from persistence import dynamo
from services import audit, secrets

ORG_ID = os.environ.get("ORG_ID", "demo")  # single-tenant demo; every write is still org-scoped
MAX_BODY_BYTES = 64 * 1024
ID_RE = r"(?P<id>[A-Za-z0-9_\-]{1,64})"
DRAFT_RE = r"(?P<draft>[A-Za-z0-9_\-]{1,64})"

Ctx = dict[str, Any]


# ---------------------------------------------------------------- handlers
def health(ctx: Ctx):
    return {"ok": True}


def llm_status(ctx: Ctx):
    """Which LLM provider would serve a request. Reports configuration only, never keys."""
    from integrations import llm

    return {
        "active_provider": llm.active_provider(),
        "mode": os.environ.get("LLM_PROVIDER", "auto"),
        "bedrock_configured": bool(os.environ.get("BEDROCK_MODEL_ID")),
        "anthropic_configured": bool(secrets.provider_key("ANTHROPIC_API_KEY")),
    }


def create_campaign(ctx: Ctx):
    try:
        body = CampaignCreate.model_validate(ctx["body"])
    except ValidationError as e:
        raise ApiError(422, "validation_error", "Invalid campaign input",
                       [{"field": ".".join(map(str, x["loc"])), "msg": x["msg"]} for x in e.errors()])
    cid = new_id("cmp")
    ts = dynamo.now_iso()
    item = {
        "PK": dynamo.pk(ORG_ID, cid),
        "SK": "META",
        "GSI1PK": f"ORG#{ORG_ID}",
        "GSI1SK": f"CAMPAIGN#{ts}",
        "id": cid,
        "org_id": ORG_ID,
        "criteria": body.model_dump(),
        "product_name": body.product_name,
        "stage": "intake",
        "created_at": ts,
    }
    dynamo.put_campaign(item)
    audit.record(ORG_ID, cid, "human:founder", "campaign.created", cid,
                 {"product_name": body.product_name}, ctx["operation_id"])
    return dynamo.public(item)


def list_campaigns(ctx: Ctx):
    return [dynamo.public(i) for i in dynamo.list_campaigns(ORG_ID)]


def get_campaign(ctx: Ctx):
    item = dynamo.get_campaign(ORG_ID, ctx["params"]["id"])
    if not item:
        raise ApiError(404, "not_found", "Campaign not found")
    return dynamo.public(item)


def campaign_events(ctx: Ctx):
    cid = ctx["params"]["id"]
    if not dynamo.get_campaign(ORG_ID, cid):
        raise ApiError(404, "not_found", "Campaign not found")
    return [dynamo.public(i) for i in dynamo.list_events(ORG_ID, cid)]


def _campaign_or_404(cid: str) -> dict:
    item = dynamo.get_campaign(ORG_ID, cid)
    if not item:
        raise ApiError(404, "not_found", "Campaign not found")
    return item


def generate_icp(ctx: Ctx):
    from services import icp

    return icp.generate(ORG_ID, _campaign_or_404(ctx["params"]["id"]), ctx["operation_id"])


def get_icp(ctx: Ctx):
    from services import icp

    _campaign_or_404(ctx["params"]["id"])
    return icp.get_latest(ORG_ID, ctx["params"]["id"])


def patch_icp(ctx: Ctx):
    from services import icp

    body = ctx["body"]
    if not isinstance(body, dict):
        raise ApiError(422, "validation_error", "Body must be a JSON object")
    return icp.update(ORG_ID, _campaign_or_404(ctx["params"]["id"]), body, ctx["operation_id"])


def _metered(operation: str, ctx: Ctx, campaign: dict, fn: Callable[[], Any]):
    from services import billing

    return billing.metered(operation, ORG_ID, campaign["id"], ctx, fn)


def billing_status(ctx: Ctx):
    from services import billing

    return billing.status(ORG_ID)


def discover(ctx: Ctx):
    from services import discovery

    campaign = _campaign_or_404(ctx["params"]["id"])
    return _metered("discovery", ctx, campaign,
                    lambda: discovery.discover(ORG_ID, campaign, ctx["operation_id"]))


def get_leads(ctx: Ctx):
    from services import discovery

    _campaign_or_404(ctx["params"]["id"])
    return discovery.list_leads(ORG_ID, ctx["params"]["id"])


def enrich_lead(ctx: Ctx):
    from services import enrichment

    campaign = _campaign_or_404(ctx["params"]["id"])
    return _metered("enrichment", ctx, campaign,
                    lambda: enrichment.enrich(ORG_ID, campaign, ctx["params"]["lead"], ctx["operation_id"]))


def draft_lead(ctx: Ctx):
    from services import outreach

    campaign = _campaign_or_404(ctx["params"]["id"])
    return _metered("draft", ctx, campaign,
                    lambda: outreach.generate(ORG_ID, campaign, ctx["params"]["lead"], ctx["operation_id"]))


def start_orchestration(ctx: Ctx):
    from services import orchestration

    body = ctx["body"] if isinstance(ctx["body"], dict) else {}
    return orchestration.create(ORG_ID, _campaign_or_404(ctx["params"]["id"]),
                                body.get("lead_id", ""), ctx["operation_id"])


def list_orchestrations(ctx: Ctx):
    from services import orchestration

    _campaign_or_404(ctx["params"]["id"])
    return orchestration.list_runs(ORG_ID, ctx["params"]["id"])


def queued_orchestrations(ctx: Ctx):
    from services import orchestration

    return orchestration.queued(ORG_ID)


def claim_orchestration(ctx: Ctx):
    from services import orchestration

    _campaign_or_404(ctx["params"]["id"])
    return orchestration.claim(ORG_ID, ctx["params"]["id"], ctx["params"]["run"], ctx["operation_id"])


def orchestration_event(ctx: Ctx):
    from services import orchestration

    _campaign_or_404(ctx["params"]["id"])
    return orchestration.add_event(ORG_ID, ctx["params"]["id"], ctx["params"]["run"],
                                   ctx["body"], ctx["operation_id"])


def list_drafts(ctx: Ctx):
    from services import outreach

    _campaign_or_404(ctx["params"]["id"])
    return outreach.list_drafts(ORG_ID, ctx["params"]["id"])


def get_draft(ctx: Ctx):
    from services import review

    _campaign_or_404(ctx["params"]["id"])
    return review.get(ORG_ID, ctx["params"]["id"], ctx["params"]["draft"])


def edit_draft(ctx: Ctx):
    from services import review

    _campaign_or_404(ctx["params"]["id"])
    return review.edit(ORG_ID, ctx["params"]["id"], ctx["params"]["draft"], ctx["body"], ctx["operation_id"])


def approve_draft(ctx: Ctx):
    from services import review

    _campaign_or_404(ctx["params"]["id"])
    return review.approve(ORG_ID, ctx["params"]["id"], ctx["params"]["draft"], ctx["operation_id"])


def reject_draft(ctx: Ctx):
    from services import review

    _campaign_or_404(ctx["params"]["id"])
    body = ctx["body"] if isinstance(ctx["body"], dict) else {}
    return review.reject(ORG_ID, ctx["params"]["id"], ctx["params"]["draft"],
                         str(body.get("reason", "")), ctx["operation_id"])


def graph_sync(ctx: Ctx):
    from services import graph

    campaign = _campaign_or_404(ctx["params"]["id"])
    return _metered("graph_query", ctx, campaign, lambda: graph.sync(ORG_ID, campaign))


def graph_view(ctx: Ctx):
    from services import graph

    _campaign_or_404(ctx["params"]["id"])
    return graph.graph(ORG_ID, ctx["params"]["id"])


def graph_decisions(ctx: Ctx):
    from services import graph

    _campaign_or_404(ctx["params"]["id"])
    return graph.decisions(ORG_ID, ctx["params"]["id"])


def graph_status(ctx: Ctx):
    from integrations import neo4j

    return {"configured": neo4j.configured()}


# ------------------------------------------------------------------ routing
# (method, regex, handler, requires_auth)
ROUTES: list[tuple[str, re.Pattern, Callable[[Ctx], Any], bool]] = [
    ("GET", re.compile(r"^/api/health$"), health, False),
    ("GET", re.compile(r"^/api/system/llm$"), llm_status, True),
    ("POST", re.compile(r"^/api/campaigns$"), create_campaign, True),
    ("GET", re.compile(r"^/api/campaigns$"), list_campaigns, True),
    ("GET", re.compile(rf"^/api/campaigns/{ID_RE}$"), get_campaign, True),
    ("GET", re.compile(rf"^/api/campaigns/{ID_RE}/events$"), campaign_events, True),
    ("POST", re.compile(rf"^/api/campaigns/{ID_RE}/generate-icp$"), generate_icp, True),
    ("GET", re.compile(rf"^/api/campaigns/{ID_RE}/icp$"), get_icp, True),
    ("PATCH", re.compile(rf"^/api/campaigns/{ID_RE}/icp$"), patch_icp, True),
    ("POST", re.compile(rf"^/api/campaigns/{ID_RE}/discover$"), discover, True),
    ("GET", re.compile(rf"^/api/campaigns/{ID_RE}/leads$"), get_leads, True),
    ("POST", re.compile(rf"^/api/campaigns/{ID_RE}/leads/(?P<lead>[A-Za-z0-9_\-]{{1,64}})/enrich$"),
     enrich_lead, True),
    ("POST", re.compile(rf"^/api/campaigns/{ID_RE}/leads/(?P<lead>[A-Za-z0-9_\-]{{1,64}})/draft$"),
     draft_lead, True),
    ("GET", re.compile(rf"^/api/campaigns/{ID_RE}/drafts$"), list_drafts, True),
    ("GET", re.compile(rf"^/api/campaigns/{ID_RE}/drafts/{DRAFT_RE}$"), get_draft, True),
    ("PATCH", re.compile(rf"^/api/campaigns/{ID_RE}/drafts/{DRAFT_RE}$"), edit_draft, True),
    ("POST", re.compile(rf"^/api/campaigns/{ID_RE}/drafts/{DRAFT_RE}/approve$"), approve_draft, True),
    ("POST", re.compile(rf"^/api/campaigns/{ID_RE}/drafts/{DRAFT_RE}/reject$"), reject_draft, True),
    ("POST", re.compile(rf"^/api/campaigns/{ID_RE}/orchestrations$"), start_orchestration, True),
    ("GET", re.compile(rf"^/api/campaigns/{ID_RE}/orchestrations$"), list_orchestrations, True),
    ("GET", re.compile(r"^/api/orchestrations/queued$"), queued_orchestrations, True),
    ("POST", re.compile(rf"^/api/campaigns/{ID_RE}/orchestrations/(?P<run>[A-Za-z0-9_\-]{{1,64}})/claim$"),
     claim_orchestration, True),
    ("POST", re.compile(rf"^/api/campaigns/{ID_RE}/orchestrations/(?P<run>[A-Za-z0-9_\-]{{1,64}})/events$"),
     orchestration_event, True),
    ("GET", re.compile(r"^/api/system/graph$"), graph_status, True),
    ("GET", re.compile(r"^/api/system/billing$"), billing_status, True),
    ("POST", re.compile(rf"^/api/campaigns/{ID_RE}/graph/sync$"), graph_sync, True),
    ("GET", re.compile(rf"^/api/campaigns/{ID_RE}/graph$"), graph_view, True),
    ("GET", re.compile(rf"^/api/campaigns/{ID_RE}/graph/decisions$"), graph_decisions, True),
]


def _authorized(headers: dict[str, str]) -> bool:
    expected = secrets.api_token()
    provided = headers.get("x-gtmos-token", "")
    # Fail closed: no configured token means nothing authenticated is reachable.
    return bool(expected) and hmac.compare_digest(provided.encode(), expected.encode())


def _parse_body(event: dict) -> Any:
    raw = event.get("body")
    if not raw:
        return {}
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8", errors="replace")
    if len(raw.encode()) > MAX_BODY_BYTES:
        raise ApiError(413, "payload_too_large", "Request body too large")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise ApiError(400, "invalid_json", "Body must be valid JSON")


def _response(status: int, payload: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json", "cache-control": "no-store"},
        "body": json.dumps(payload),
    }


def handler(event: dict, context: Any = None) -> dict:
    op_id = new_id("op")
    method = event.get("requestContext", {}).get("http", {}).get("method", "")
    path = event.get("rawPath", "")
    # Named stages (e.g. /prod) are included in rawPath by API Gateway; strip it.
    stage = event.get("requestContext", {}).get("stage", "")
    if stage and stage != "$default" and path.startswith(f"/{stage}/"):
        path = path[len(stage) + 1:]
    status = 500
    try:
        headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
        matched_path = False
        for m, pattern, fn, needs_auth in ROUTES:
            match = pattern.match(path)
            if not match:
                continue
            matched_path = True
            if m != method:
                continue
            if needs_auth and not _authorized(headers):
                raise ApiError(401, "unauthorized", "Missing or invalid credentials")
            ctx: Ctx = {
                "operation_id": op_id,
                "params": match.groupdict(),
                "body": _parse_body(event) if method in ("POST", "PUT", "PATCH") else {},
                "headers": headers,
            }
            status = 200 if method != "POST" else 201
            return _response(status, ok(fn(ctx), op_id))
        status = 405 if matched_path else 404
        raise ApiError(status, "method_not_allowed" if matched_path else "not_found", "No such route")
    except ApiError as e:
        status = e.status
        return _response(e.status, err(e.code, e.message, op_id, e.details))
    except Exception as e:  # never leak internals to the caller
        status = 500
        print(json.dumps({"level": "error", "operation_id": op_id, "error": type(e).__name__}))
        return _response(500, err("internal_error", "Unexpected server error", op_id))
    finally:
        # Sanitized execution event for CloudWatch: no bodies, headers or secrets.
        print(json.dumps({"level": "info", "operation_id": op_id, "method": method,
                          "path": path, "status": status}))
