"""Shared plumbing for the GTMOS LangGraph worker.

The Planner, Researcher and Reviewer are nodes in one LangGraph (see graph.py) run by a single local
worker process (worker.py). They do not call an LLM here: the real work (Hunter enrichment, draft
generation + review) runs in the GTMOS API, which is the only thing they call.

The GTMOS token comes from the environment, else from `.data/api_token`. Nothing is printed.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROLES = ("planner", "researcher", "reviewer")
log = logging.getLogger("gtmos.agents")


class Gtmos:
    """Minimal client for the GTMOS API. Blocking."""

    def __init__(self) -> None:
        self.base = os.environ["GTMOS_API_URL"].rstrip("/")
        self.token = os.environ.get("GTMOS_API_TOKEN") or (ROOT / ".data" / "api_token").read_text().strip()

    def call(self, method: str, path: str, body: dict | None = None, headers: dict | None = None,
             timeout: float = 90) -> tuple[int, dict]:
        req = urllib.request.Request(
            self.base + path, method=method, data=json.dumps(body).encode() if body is not None else None,
            headers={"x-gtmos-token": self.token, "content-type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec - operator-configured https URL
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, json.loads(exc.read())
            except Exception:
                return exc.code, {"status": "error", "error": {"code": "http_error", "message": str(exc.code)}}

    def event(self, campaign: str, run_id: str, agent: str, kind: str, **fields) -> dict:
        status, body = self.call("POST", f"/api/campaigns/{campaign}/orchestrations/{run_id}/events",
                                 {"agent": agent, "kind": kind, **fields})
        if status >= 400:
            log.warning("trace event %s/%s rejected: %s", agent, kind, body.get("error", {}).get("code"))
        return body
