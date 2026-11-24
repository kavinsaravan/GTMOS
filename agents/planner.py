"""Planner agent: picks up queued orchestration runs, opens a real Band room, adds the Researcher
and Reviewer, and starts the chain with an @mention to the Researcher. When the Reviewer reports
back it marks the run `awaiting_approval` (a human still has to approve; the agents never do).
Run: python -m agents.planner"""
from __future__ import annotations

import asyncio
import logging

from band.client.rest import (DEFAULT_REQUEST_OPTIONS, AsyncRestClient, ChatMessageRequest, ChatRoomRequest,
                              ParticipantRequest)
from band.client.rest import ChatMessageRequestMentionsItem

from agents.common import RoleAdapter, band_credential, band_rest_url, marker, message_id, serve

log = logging.getLogger("gtmos.planner")
POLL_SECONDS = 5


class Planner(RoleAdapter):
    role = "planner"
    accepts_from = "reviewer"

    def __init__(self) -> None:
        super().__init__()
        self.rest = AsyncRestClient(api_key=band_credential("planner", "KEY"), base_url=band_rest_url())

    # ---- Reviewer replied: close the loop (a human approval is still required)
    async def handle(self, msg, tools, room_id, run, campaign, lead) -> None:
        await self.report(campaign, run["id"], "message_received", band_room_id=room_id,
                          band_message_id=msg.id, summary="Planner received the Reviewer's report")
        if run["status"] != "reviewing":
            return
        await self.report(campaign, run["id"], "completed", status="awaiting_approval", band_room_id=room_id,
                          summary="Chain complete. A human reviews the draft; nothing is sent automatically.")

    # ---- poll for queued runs and start them
    async def poll_forever(self) -> None:
        while True:
            try:
                status, body = await asyncio.to_thread(self.api.call, "GET", "/api/orchestrations/queued")
                for run in (body.get("data", []) if status == 200 else []):
                    await self.start(run)
            except Exception as exc:
                log.warning("poll failed: %s", type(exc).__name__)
            await asyncio.sleep(POLL_SECONDS)

    async def start(self, run: dict) -> None:
        cid, rid, lead = run["campaign_id"], run["id"], run["lead_id"]
        status, _ = await asyncio.to_thread(self.api.call, "POST", f"/api/campaigns/{cid}/orchestrations/{rid}/claim", {})
        if status >= 400:
            return                                  # someone else (or an earlier poll) has it
        try:
            room = (await self.rest.agent_api_chats.create_agent_chat(
                chat=ChatRoomRequest(title=f"GTMOS: {run['lead_name']}"[:120]),
                request_options=DEFAULT_REQUEST_OPTIONS)).data.id
            for role in ("researcher", "reviewer"):
                await self.rest.agent_api_participants.add_agent_chat_participant(
                    room, participant=ParticipantRequest(participant_id=self.ids[role], role="member"),
                    request_options=DEFAULT_REQUEST_OPTIONS)
            parts = (await self.rest.agent_api_participants.list_agent_chat_participants(
                room, request_options=DEFAULT_REQUEST_OPTIONS)).data
            researcher = next(p for p in parts if p.id == self.ids["researcher"])
            await self.report(cid, rid, "room_created", band_room_id=room,
                              summary="Planner opened a Band room with the Researcher and Reviewer")
            text = (f"@{researcher.handle} please enrich {run['lead_name']} (find decision-maker contacts). "
                    f"{marker(rid, cid, lead)}")
            sent = await self.rest.agent_api_messages.create_agent_chat_message(
                room, message=ChatMessageRequest(
                    content=text, mentions=[ChatMessageRequestMentionsItem(id=researcher.id, handle=researcher.handle)]),
                request_options=DEFAULT_REQUEST_OPTIONS)
            await self.report(cid, rid, "handoff_sent", status="researching", band_room_id=room,
                              band_message_id=message_id(sent), mentioned="researcher",
                              summary=f"Planner handed {run['lead_name']} to the Researcher")
        except Exception as exc:
            log.exception("could not start run %s", rid)
            await self.report(cid, rid, "work_failed", status="failed",
                              summary=f"Planner could not set up the Band room: {type(exc).__name__}")


async def main() -> None:
    planner = Planner()
    await asyncio.gather(serve(planner), planner.poll_forever())


if __name__ == "__main__":
    asyncio.run(main())
