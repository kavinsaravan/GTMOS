"""Shared plumbing for the three local Band agents (Planner, Researcher, Reviewer).

Each agent is its own OS process holding its own Band websocket (band-sdk). The agents do not
call an LLM here; the real work (Hunter enrichment, draft generation + review) runs in the GTMOS
API, which is the only thing they call. Handoffs between them are real Band @mention messages.

Credentials (never printed): BAND_<ROLE>_ID / BAND_<ROLE>_KEY and the GTMOS token come from the
environment, else from AWS Secrets Manager (`gtmos/providers`) / `.data/api_token`.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

from band.core.protocols import AgentToolsProtocol
from band.core.simple_adapter import SimpleAdapter
from band.core.types import PlatformMessage

ROOT = Path(__file__).resolve().parents[1]
ROLES = ("planner", "researcher", "reviewer")
MARKER = re.compile(r"\[gtmos run=(?P<run>[\w\-]+) campaign=(?P<campaign>[\w\-]+) lead=(?P<lead>[\w\-]+)\]")
log = logging.getLogger("gtmos.agents")


def marker(run: str, campaign: str, lead: str) -> str:
    return f"[gtmos run={run} campaign={campaign} lead={lead}]"


_secret_cache: dict | None = None


def _providers() -> dict:
    global _secret_cache
    if _secret_cache is None:
        import boto3

        session = boto3.Session(profile_name=os.environ.get("AWS_PROFILE", "gtmos"),
                                region_name=os.environ.get("AWS_REGION", "us-west-2"))
        _secret_cache = json.loads(session.client("secretsmanager").get_secret_value(
            SecretId=os.environ.get("PROVIDERS_SECRET_ID", "gtmos/providers"))["SecretString"])
    return _secret_cache


def band_credential(role: str, field: str) -> str:
    name = f"BAND_{role.upper()}_{field}"
    return os.environ.get(name) or _providers()[name]


def band_rest_url() -> str:
    return os.environ.get("BAND_REST_URL", "https://api.band.ai")


class Gtmos:
    """Minimal client for the GTMOS API. Blocking; call through `asyncio.to_thread`."""

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

    def run(self, campaign: str, run_id: str) -> dict | None:
        status, body = self.call("GET", f"/api/campaigns/{campaign}/orchestrations")
        return next((r for r in body.get("data", []) if r["id"] == run_id), None) if status == 200 else None

    def event(self, campaign: str, run_id: str, agent: str, kind: str, **fields) -> dict:
        status, body = self.call("POST", f"/api/campaigns/{campaign}/orchestrations/{run_id}/events",
                                 {"agent": agent, "kind": kind, **fields})
        if status >= 400:
            log.warning("trace event %s/%s rejected: %s", agent, kind, body.get("error", {}).get("code"))
        return body


def message_id(sent) -> str | None:
    """Best-effort extraction of the Band message id from whatever send_message returned."""
    for getter in (lambda r: r.data.id, lambda r: r["data"]["id"], lambda r: r.id, lambda r: r["id"]):
        try:
            value = getter(sent)
            if value:
                return str(value)
        except Exception:
            continue
    return None


class RoleAdapter(SimpleAdapter[list]):
    """Base: accept only @mentioned messages from the expected upstream agent, then run `handle`."""

    role = ""
    accepts_from: str | None = None   # role whose messages we act on

    def __init__(self) -> None:
        super().__init__()
        self.api = Gtmos()
        self.ids = {r: band_credential(r, "ID") for r in ROLES}
        self.seen: set[str] = set()      # redelivered Band messages are processed once
        self.replied: set[str] = set()   # rooms where the current turn already sent a message

    async def on_message(self, msg: PlatformMessage, tools: AgentToolsProtocol, history: list,
                         participants_msg: str | None, contacts_msg: str | None, *,
                         is_session_bootstrap: bool, room_id: str) -> None:
        self.replied.discard(room_id)
        try:
            await self._dispatch(msg, tools, room_id)
        finally:
            # Band judges every turn: it must reply (an @mention) or explicitly decline, otherwise
            # the platform posts a "turn ended without replying" error into the room.
            if room_id not in self.replied:
                try:
                    await tools.no_reply("nothing further for this agent to do")
                except Exception:
                    log.warning("%s: no_reply failed", self.role, exc_info=True)

    async def _dispatch(self, msg, tools, room_id: str) -> None:
        if msg.id in self.seen:
            return
        self.seen.add(msg.id)
        if self.accepts_from is None or msg.sender_id != self.ids[self.accepts_from]:
            log.info("%s ignoring message from non-%s sender", self.role, self.accepts_from)
            return
        m = MARKER.search(msg.content or "")
        if not m:
            return
        run = await asyncio.to_thread(self.api.run, m["campaign"], m["run"])
        if not run:
            log.warning("%s: unknown run %s", self.role, m["run"])
            return
        await self.handle(msg, tools, room_id, run, m["campaign"], m["lead"])

    async def handle(self, msg, tools, room_id, run, campaign, lead) -> None:  # pragma: no cover
        raise NotImplementedError

    async def report(self, campaign: str, run_id: str, kind: str, **fields) -> None:
        await asyncio.to_thread(self.api.event, campaign, run_id, self.role, kind, **fields)

    async def hand_off(self, tools, room_id, run, campaign, lead, to_role: str, text: str, **fields) -> None:
        sent = await tools.send_message(f"{text}\n{marker(run['id'], campaign, lead)}",
                                        mentions=[self.ids[to_role]])
        self.replied.add(room_id)
        await self.report(campaign, run["id"], "handoff_sent", band_room_id=room_id,
                          band_message_id=message_id(sent), mentioned=to_role, summary=text[:300], **fields)


async def serve(adapter: RoleAdapter) -> None:
    from band import Agent, configure_logging

    configure_logging(logging.INFO)
    agent = Agent.create(adapter=adapter, agent_id=band_credential(adapter.role, "ID"),
                         api_key=band_credential(adapter.role, "KEY"))
    await agent.run()
