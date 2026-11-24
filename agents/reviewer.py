"""Reviewer agent: on a Researcher @mention, generates the evidence-bound draft (its built-in
reviewer pass strips unsupported claims) and reports back to the Planner. It never approves or
sends: the draft waits in `pending_review` for a human. Run: python -m agents.reviewer"""
from __future__ import annotations

import asyncio

from agents.common import RoleAdapter, serve


class Reviewer(RoleAdapter):
    role = "reviewer"
    accepts_from = "researcher"

    async def handle(self, msg, tools, room_id, run, campaign, lead) -> None:
        if run["status"] != "reviewing" or any(
                t["agent"] == "reviewer" and t["kind"] == "work_done" for t in run["trace"]):
            return
        await self.report(campaign, run["id"], "message_received", band_room_id=room_id,
                          band_message_id=msg.id, summary="Reviewer received the Researcher's handoff")
        status, body = await asyncio.to_thread(
            self.api.call, "POST", f"/api/campaigns/{campaign}/leads/{lead}/draft", {},
            {"Idempotency-Key": f"{run['id']}-draft"})
        if status >= 400:
            err = body.get("error", {})
            text = f"Drafting failed ({err.get('code')}): {err.get('message')}"
            await self.report(campaign, run["id"], "work_failed", status="failed", band_room_id=room_id, summary=text)
            await tools.send_message(f"{text}. Stopping this run.", mentions=[self.ids["planner"]])
            return
        draft = body["data"]
        verdict = draft.get("reviewer", {}).get("status", "unknown")
        if draft["status"] == "blocked":
            follow_up = "The reviewer pass blocked it; a human must edit and re-review it before anything is created in Gmail."
        else:
            follow_up = "It needs human approval before anything is created in Gmail."
        summary = f"Draft {draft['id']} is {draft['status']} (reviewer verdict: {verdict}). {follow_up}"
        await self.report(campaign, run["id"], "work_done", band_room_id=room_id, draft_id=draft["id"], summary=summary)
        await self.hand_off(tools, room_id, run, campaign, lead, "planner", summary)


if __name__ == "__main__":
    asyncio.run(serve(Reviewer()))
