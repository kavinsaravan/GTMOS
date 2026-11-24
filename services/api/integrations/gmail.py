"""Gmail API client: creates a *draft* in the connected mailbox. It never sends.

Auth: OAuth2 refresh-token flow (scope gmail.compose). Required secrets:
  GMAIL_OAUTH_CLIENT_ID, GMAIL_OAUTH_CLIENT_SECRET, GMAIL_OAUTH_REFRESH_TOKEN
drafts.create: POST https://gmail.googleapis.com/gmail/v1/users/me/drafts
               {"message": {"raw": <base64url RFC 2822>}}  ->  {"id": "...", "message": {...}}
Only the sole `drafts.create` call is made; there is deliberately no send path in this module.
"""
from __future__ import annotations

import base64
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage

from models.common import ApiError
from services import secrets

log = logging.getLogger("gtmos.gmail")

TOKEN_URL = "https://oauth2.googleapis.com/token"
DRAFTS_URL = "https://gmail.googleapis.com/gmail/v1/users/me/drafts"
KEYS = ("GMAIL_OAUTH_CLIENT_ID", "GMAIL_OAUTH_CLIENT_SECRET", "GMAIL_OAUTH_REFRESH_TOKEN")


def configured() -> bool:
    return all(secrets.provider_key(k) for k in KEYS)


def _http_post(url: str, headers: dict, data: bytes, timeout: float) -> dict:
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec - fixed https hosts
        return json.loads(resp.read())


def _access_token(timeout: float) -> str:
    form = urllib.parse.urlencode({
        "client_id": secrets.provider_key("GMAIL_OAUTH_CLIENT_ID"),
        "client_secret": secrets.provider_key("GMAIL_OAUTH_CLIENT_SECRET"),
        "refresh_token": secrets.provider_key("GMAIL_OAUTH_REFRESH_TOKEN"),
        "grant_type": "refresh_token"}).encode()
    try:
        data = _http_post(TOKEN_URL, {"content-type": "application/x-www-form-urlencoded"}, form, timeout)
    except urllib.error.HTTPError as exc:
        log.warning("gmail token refresh failed: HTTP %s", exc.code)
        if exc.code in (400, 401):   # revoked / invalid refresh token or client
            raise ApiError(503, "mail_not_configured", "Gmail rejected the configured OAuth credentials") from None
        raise ApiError(502, "mail_unavailable", "Gmail is unavailable") from None
    except Exception as exc:
        log.warning("gmail token refresh failed: %s", type(exc).__name__)
        raise ApiError(502, "mail_unavailable", "Gmail is unavailable") from None
    token = data.get("access_token")
    if not token:
        raise ApiError(502, "mail_unavailable", "Gmail returned no access token")
    return token


def build_raw(to: str, subject: str, body: str) -> str:
    msg = EmailMessage()
    msg["To"] = to              # EmailMessage rejects header values containing newlines
    msg["Subject"] = " ".join(subject.split())
    msg.set_content(body)
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


def create_draft(to: str, subject: str, body: str, *, timeout: float = 10) -> str:
    """Create a Gmail draft and return its draft id."""
    if not configured():
        raise ApiError(503, "mail_not_configured", "Gmail is not connected (OAuth credentials missing)")
    try:
        raw = build_raw(to, subject, body)
    except Exception:
        raise ApiError(422, "invalid_message", "The draft could not be formatted as an email") from None
    token = _access_token(timeout)
    try:
        data = _http_post(DRAFTS_URL, {"authorization": f"Bearer {token}", "content-type": "application/json"},
                          json.dumps({"message": {"raw": raw}}).encode(), timeout)
    except urllib.error.HTTPError as exc:
        log.warning("gmail drafts.create failed: HTTP %s", exc.code)
        if exc.code in (401, 403):
            raise ApiError(503, "mail_not_configured", "Gmail denied draft creation (check OAuth scope)") from None
        if exc.code == 429:
            raise ApiError(502, "mail_rate_limited", "Gmail rate limit reached") from None
        raise ApiError(502, "mail_unavailable", "Gmail is unavailable") from None
    except Exception as exc:
        log.warning("gmail drafts.create failed: %s", type(exc).__name__)
        raise ApiError(502, "mail_unavailable", "Gmail is unavailable") from None
    draft_id = data.get("id")
    if not draft_id:
        raise ApiError(502, "mail_unavailable", "Gmail did not return a draft id")
    return str(draft_id)
