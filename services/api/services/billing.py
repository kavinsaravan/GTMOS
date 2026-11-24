"""Credit gating + usage metering for billable operations (Flexprice).

Flow for every billable request:
  1. check  - wallet balance >= cost, else 402 insufficient_credits (nothing runs, nothing is charged)
  2. run    - the operation itself
  3. charge - idempotent wallet debit + usage event, keyed by the caller's Idempotency-Key
              (falls back to the operation id), so a retried request is never charged twice.

If Flexprice is not configured the operation runs unmetered and `status()` says so; it is never
reported as connected. If Flexprice is configured but unreachable the check fails closed (502).
Illustrative credit prices come from the PRD.
"""
from __future__ import annotations

import logging
import os
from decimal import Decimal
from typing import Any, Callable

from integrations import flexprice
from models.common import ApiError
from services import audit

log = logging.getLogger("gtmos.billing")

COSTS: dict[str, int] = {"discovery": 1, "enrichment": 3, "graph_query": 1, "draft": 2, "resolution": 2}
EVENT_NAMES: dict[str, str] = {k: f"gtmos_{k}" for k in COSTS}
INITIAL_CREDITS = int(os.environ.get("FLEXPRICE_INITIAL_CREDITS", "100"))

_wallet_cache: dict[str, str] = {}   # external_id -> wallet id (per warm container)


def status(org_id: str) -> dict:
    if not flexprice.configured():
        return {"configured": False, "costs": COSTS}
    wallet = flexprice.get_wallet(flexprice.customer_external_id(org_id))
    return {"configured": True, "costs": COSTS,
            "wallet": None if not wallet else {
                "balance": str(flexprice.balance_of(wallet)), "status": wallet.get("wallet_status")}}


def _idempotency_key(ctx: dict, operation: str) -> str:
    supplied = (ctx.get("headers") or {}).get("idempotency-key", "").strip()
    base = supplied if supplied and len(supplied) <= 100 else ctx["operation_id"]
    return f"gtmos:{operation}:{base}"


def metered(operation: str, org_id: str, campaign_id: str | None, ctx: dict,
            fn: Callable[[], Any]) -> Any:
    cost = COSTS[operation]
    if not flexprice.configured():
        return fn()

    external_id = flexprice.customer_external_id(org_id)
    wallet = flexprice.ensure_wallet(external_id, f"GTMOS org {org_id}", INITIAL_CREDITS)
    balance = flexprice.balance_of(wallet)
    if wallet.get("wallet_status") not in (None, "active") or balance < Decimal(cost):
        raise ApiError(402, "insufficient_credits", "Not enough credits for this operation",
                       [{"operation": operation, "required": cost, "balance": str(balance)}])

    result = fn()

    key = _idempotency_key(ctx, operation)
    try:
        charged = flexprice.debit(wallet["id"], cost, key, f"GTMOS {operation}")
        if charged:   # a replayed Idempotency-Key must not be metered twice
            flexprice.send_event(EVENT_NAMES[operation], external_id, key,
                                 {"campaign_id": campaign_id or "", "credits": cost})
        if campaign_id:
            audit.record(org_id, campaign_id, "system", "billing.charged", key,
                         {"operation": operation, "credits": cost, "new_charge": charged},
                         ctx.get("operation_id"))
    except ApiError as exc:
        # The work already happened; do not fail the request, but leave a trace for reconciliation.
        log.warning("charge failed for %s after success: %s", operation, exc.code)
        if campaign_id:
            audit.record(org_id, campaign_id, "system", "billing.charge_failed", key,
                         {"operation": operation, "credits": cost, "code": exc.code}, ctx.get("operation_id"))
    return result
