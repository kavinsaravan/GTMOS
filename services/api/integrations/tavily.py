"""Tavily Search API client (live public web search).

POST https://api.tavily.com/search  with  Authorization: Bearer <key>.
Results are untrusted web content. Nothing here fabricates data: if the key is
missing or the call fails, a typed ApiError is raised so callers can report it.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from urllib.parse import urlparse

from models.common import ApiError
from services import secrets

log = logging.getLogger("gtmos.tavily")

TAVILY_URL = "https://api.tavily.com/search"
MAX_CONTENT_CHARS = 800


def configured() -> bool:
    return bool(secrets.provider_key("TAVILY_API_KEY"))


def _http_post(url: str, headers: dict, body: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec - fixed https URL
        return json.loads(resp.read())


def search(query: str, *, max_results: int = 6, timeout: float = 8,
           include_domains: list[str] | None = None) -> list[dict]:
    """Return [{title, url, content}] for public http(s) pages only."""
    key = secrets.provider_key("TAVILY_API_KEY")
    if not key:
        raise ApiError(503, "search_not_configured", "Tavily is not configured (TAVILY_API_KEY missing)")
    payload = {"query": query, "search_depth": "basic", "max_results": max_results,
               "include_answer": False, "include_raw_content": False}
    if include_domains:
        payload["include_domains"] = include_domains
    try:
        data = _http_post(
            TAVILY_URL,
            {"authorization": f"Bearer {key}", "content-type": "application/json"},
            payload,
            timeout,
        )
    except urllib.error.HTTPError as exc:
        log.warning("tavily call failed: HTTP %s", exc.code)
        if exc.code == 401:
            raise ApiError(503, "search_not_configured", "Tavily rejected the configured API key") from None
        if exc.code in (429, 432, 433):
            raise ApiError(502, "search_rate_limited", "Tavily rate or plan limit reached") from None
        raise ApiError(502, "search_unavailable", "The search provider is unavailable") from None
    except Exception as exc:
        log.warning("tavily call failed: %s", type(exc).__name__)
        raise ApiError(502, "search_unavailable", "The search provider is unavailable") from None

    out = []
    for r in data.get("results", []):
        url = str(r.get("url", ""))
        if urlparse(url).scheme not in ("http", "https") or not urlparse(url).hostname:
            continue
        out.append({"title": str(r.get("title", ""))[:200], "url": url,
                    "content": str(r.get("content", ""))[:MAX_CONTENT_CHARS]})
    return out
