"""Flexprice client: usage events + prepaid-credit wallets.

Verified against the live sandbox (US region):
  base URL   FLEXPRICE_API_URL (e.g. https://us.api.flexprice.io/v1), header `x-api-key`
  customers  POST /customers {external_id,name}            -> 201 | 409 already_exists
  wallets    GET  /customers/wallets?lookup_key=<ext>&include_real_time_balance=true
                                                           -> 200 [wallet] | 404 customer unknown
             POST /wallets {customer_id,currency,initial_credits_to_load,conversion_rate}
             POST /wallets/{id}/debit {credits,transaction_reason,idempotency_key}
                                                           -> 200 | 409 duplicate idempotency key
  events     POST /events {event_name,external_customer_id,event_id,timestamp,properties,source} -> 202
Observed behaviour that shapes the design:
  * Events do NOT deduct wallet credits on their own (no subscription/price attached).
  * A debit is NOT rejected when it overdraws the wallet, so the 402 gate is our own pre-check.
Required secrets: FLEXPRICE_API_KEY, FLEXPRICE_API_URL.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal

from models.common import ApiError
from services import secrets

log = logging.getLogger("gtmos.flexprice")

KEYS = ("FLEXPRICE_API_KEY", "FLEXPRICE_API_URL")
ALLOWED_HOST_SUFFIX = ".flexprice.io"


def configured() -> bool:
    return all(secrets.provider_key(k) for k in KEYS)


def _base() -> str:
    url = (secrets.provider_key("FLEXPRICE_API_URL") or "").rstrip("/")
    host = urllib.parse.urlparse(url).hostname or ""
    # The API key is only ever sent to https hosts under flexprice.io.
    if not url.startswith("https://") or not host.endswith(ALLOWED_HOST_SUFFIX):
        raise ApiError(503, "billing_not_configured", "Flexprice URL is invalid")
    return url


def _request(method: str, path: str, body: dict | None = None, *, timeout: float = 10) -> tuple[int, object]:
    """Return (status, parsed_json). 4xx are returned to the caller; 5xx/transport raise billing_unavailable."""
    if not configured():
        raise ApiError(503, "billing_not_configured", "Flexprice is not connected")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        _base() + path, data=data, method=method,
        headers={"x-api-key": secrets.provider_key("FLEXPRICE_API_KEY") or "",
                 "content-type": "application/json", "accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec - https flexprice.io only
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read())
        except Exception:
            payload = None
        if exc.code in (401, 403):
            log.warning("flexprice rejected credentials: HTTP %s", exc.code)
            raise ApiError(503, "billing_not_configured", "Flexprice rejected the configured API key") from None
        if exc.code >= 500 or exc.code == 429:
            log.warning("flexprice unavailable: HTTP %s", exc.code)
            raise ApiError(502, "billing_unavailable", "Flexprice is unavailable") from None
        return exc.code, payload
    except Exception as exc:
        log.warning("flexprice request failed: %s", type(exc).__name__)
        raise ApiError(502, "billing_unavailable", "Flexprice is unavailable") from None


def customer_external_id(org_id: str) -> str:
    return f"gtmos-org-{org_id}"


def get_wallet(external_id: str) -> dict | None:
    """Return the customer's first wallet incl. real-time balance, or None if no customer/wallet."""
    q = urllib.parse.urlencode({"lookup_key": external_id, "include_real_time_balance": "true"})
    status, body = _request("GET", f"/customers/wallets?{q}")
    if status == 404:
        return None
    if status != 200 or not isinstance(body, list):
        raise ApiError(502, "billing_unavailable", "Flexprice returned an unexpected response")
    return body[0] if body else None


def ensure_wallet(external_id: str, name: str, initial_credits: int) -> dict:
    """Idempotently create the customer and a prepaid wallet; returns the wallet."""
    wallet = get_wallet(external_id)
    if wallet:
        return wallet
    status, body = _request("POST", "/customers", {"external_id": external_id, "name": name})
    if status not in (200, 201, 409):
        raise ApiError(502, "billing_unavailable", "Flexprice could not create the customer")
    status, cust = _request("GET", f"/customers/lookup/{urllib.parse.quote(external_id)}")
    customer_id = cust.get("id") if status == 200 and isinstance(cust, dict) else None
    if not customer_id and isinstance(body, dict):
        customer_id = body.get("id")
    if not customer_id:
        raise ApiError(502, "billing_unavailable", "Flexprice customer could not be resolved")
    status, _ = _request("POST", "/wallets", {
        "customer_id": customer_id, "currency": "usd", "name": "GTMOS credits",
        "initial_credits_to_load": str(initial_credits), "conversion_rate": "1"})
    if status not in (200, 201):
        raise ApiError(502, "billing_unavailable", "Flexprice could not create the wallet")
    wallet = get_wallet(external_id)
    if not wallet:
        raise ApiError(502, "billing_unavailable", "Flexprice wallet could not be resolved")
    return wallet


def balance_of(wallet: dict) -> Decimal:
    raw = wallet.get("real_time_credit_balance", wallet.get("credit_balance", wallet.get("balance", "0")))
    return Decimal(str(raw))


def debit(wallet_id: str, credits: int, idempotency_key: str, description: str) -> bool:
    """Debit credits. Returns True if newly charged, False if this idempotency key was already charged."""
    status, _ = _request("POST", f"/wallets/{wallet_id}/debit", {
        "credits": str(credits), "transaction_reason": "MANUAL_BALANCE_DEBIT",
        "idempotency_key": idempotency_key, "description": description[:200]})
    if status == 409:
        return False
    if status not in (200, 201):
        raise ApiError(502, "billing_unavailable", "Flexprice could not record the charge")
    return True


def send_event(event_name: str, external_id: str, event_id: str, properties: dict) -> None:
    """Meter one usage event. event_id is deterministic, so a retry is collapsed by Flexprice."""
    status, _ = _request("POST", "/events", {
        "event_name": event_name, "external_customer_id": external_id, "event_id": event_id,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "properties": {k: str(v) for k, v in properties.items()}, "source": "gtmos"})
    if status not in (200, 201, 202):
        raise ApiError(502, "billing_unavailable", "Flexprice could not record the usage event")
