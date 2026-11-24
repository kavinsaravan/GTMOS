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

Ctx = dict[str, Any]


# ---------------------------------------------------------------- handlers
def health(ctx: Ctx):
    return {"ok": True}


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


# ------------------------------------------------------------------ routing
# (method, regex, handler, requires_auth)
ROUTES: list[tuple[str, re.Pattern, Callable[[Ctx], Any], bool]] = [
    ("GET", re.compile(r"^/api/health$"), health, False),
    ("POST", re.compile(r"^/api/campaigns$"), create_campaign, True),
    ("GET", re.compile(r"^/api/campaigns$"), list_campaigns, True),
    ("GET", re.compile(rf"^/api/campaigns/{ID_RE}$"), get_campaign, True),
    ("GET", re.compile(rf"^/api/campaigns/{ID_RE}/events$"), campaign_events, True),
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
