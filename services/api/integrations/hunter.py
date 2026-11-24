"""Hunter.io client (contact discovery by company domain).

GET https://api.hunter.io/v2/domain-search  with  X-API-KEY.
Only addresses Hunter returns are ever used; nothing here guesses or constructs an
email. If the key is missing or the call fails, a typed ApiError is raised.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from urllib.parse import urlencode

from models.common import ApiError
from services import secrets

log = logging.getLogger("gtmos.hunter")

BASE_URL = "https://api.hunter.io/v2"
MAX_LIMIT = 10


def configured() -> bool:
    return bool(secrets.provider_key("HUNTER_API_KEY"))


def _http_get(url: str, headers: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec - fixed https host
        return json.loads(resp.read())


def _get(path: str, params: dict, timeout: float) -> dict:
    key = secrets.provider_key("HUNTER_API_KEY")
    if not key:
        raise ApiError(503, "enrichment_not_configured", "Hunter is not configured (HUNTER_API_KEY missing)")
    url = f"{BASE_URL}/{path}?{urlencode({k: v for k, v in params.items() if v not in (None, '')})}"
    try:
        return _http_get(url, {"X-API-KEY": key, "accept": "application/json"}, timeout)
    except urllib.error.HTTPError as exc:
        log.warning("hunter call failed: HTTP %s", exc.code)
        if exc.code == 401:
            raise ApiError(503, "enrichment_not_configured", "Hunter rejected the configured API key") from None
        if exc.code in (403, 429):
            raise ApiError(502, "enrichment_rate_limited", "Hunter rate or plan limit reached") from None
        if exc.code == 451:
            raise ApiError(502, "enrichment_unavailable_for_domain",
                           "Hunter cannot return data for this domain") from None
        raise ApiError(502, "enrichment_unavailable", "The enrichment provider is unavailable") from None
    except Exception as exc:
        log.warning("hunter call failed: %s", type(exc).__name__)
        raise ApiError(502, "enrichment_unavailable", "The enrichment provider is unavailable") from None


def _contact(e: dict) -> dict | None:
    email = str(e.get("value") or "").strip().lower()
    if "@" not in email:
        return None
    ver = e.get("verification") or {}
    return {
        "email": email,
        "first_name": str(e.get("first_name") or "")[:80],
        "last_name": str(e.get("last_name") or "")[:80],
        "position": str(e.get("position") or "")[:120],
        "seniority": str(e.get("seniority") or "")[:40],
        "department": str(e.get("department") or "")[:40],
        "linkedin": str(e.get("linkedin") or "")[:200],
        "type": str(e.get("type") or "")[:20],
        "confidence": int(e.get("confidence") or 0),
        "verification_status": str(ver.get("status") or "unknown")[:20],
        "verification_date": str(ver.get("date") or "")[:20],
        "sources": [str(s.get("uri"))[:300] for s in (e.get("sources") or [])[:3] if s.get("uri")],
    }


def domain_search(domain: str, *, limit: int = MAX_LIMIT, timeout: float = 10) -> list[dict]:
    """Return normalized personal contacts Hunter has found for the domain."""
    data = _get("domain-search",
                {"domain": domain, "limit": min(limit, MAX_LIMIT), "type": "personal"}, timeout)
    out = []
    for e in (data.get("data") or {}).get("emails") or []:
        c = _contact(e)
        if c:
            out.append(c)
    return out
