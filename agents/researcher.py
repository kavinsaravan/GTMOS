"""Researcher agent: on a Planner @mention, enriches the lead (Hunter, billed via Flexprice) and
hands off to the Reviewer. Run: python -m agents.researcher"""
from __future__ import annotations

import asyncio

from agents.common import RoleAdapter, serve


class Researcher(RoleAdapter):
    role = "researcher"
    accepts_from = "planner"

    async def handle(self, msg, tools, room_id, run, campaign, lead) -> None:
        if run["status"] != "researching" or any(
                t["agent"] == "researcher" and t["kind"] == "work_done" for t in run["trace"]):
            return
        await self.report(campaign, run["id"], "message_received", band_room_id=room_id,
                          band_message_id=msg.id, summary="Researcher received the Planner's handoff")
        if not await self.resolve_uncertainty(tools, room_id, run, campaign, lead):
            return
        status, body = await asyncio.to_thread(
            self.api.call, "POST", f"/api/campaigns/{campaign}/leads/{lead}/enrich", {},
            {"Idempotency-Key": f"{run['id']}-enrich"})
        if status >= 400:
            err = body.get("error", {})
            text = f"Enrichment failed ({err.get('code')}): {err.get('message')}"
            await self.report(campaign, run["id"], "work_failed", status="failed", band_room_id=room_id, summary=text)
            await tools.send_message(f"{text}. Stopping this run.", mentions=[self.ids["planner"]])
            return
        data = body["data"]["enrichment"]
        summary = f"Enriched {body['data']['domain']}: {data['usable_contacts']} usable contact(s), status {data['status']}."
        await self.report(campaign, run["id"], "work_done", band_room_id=room_id, summary=summary)
        if data["usable_contacts"] == 0:
            await self.report(campaign, run["id"], "work_failed", status="failed", band_room_id=room_id,
                              summary="No usable contacts; nothing to draft")
            await tools.send_message("No usable contacts were found, so there is nothing to draft. Stopping this run.",
                                     mentions=[self.ids["planner"]])
            return
        await self.hand_off(tools, room_id, run, campaign, lead, "reviewer",
                            f"{summary} Please draft and review the outreach.", status="reviewing")

    async def resolve_uncertainty(self, tools, room_id, run, campaign, lead) -> bool:
        """If the graph says verify_first, look for the one missing fact before spending enrichment and
        draft credits. Returns False when the run ended here (abstained or failed)."""
        status, body = await asyncio.to_thread(
            self.api.call, "POST", f"/api/campaigns/{campaign}/leads/{lead}/resolve", {},
            {"Idempotency-Key": f"{run['id']}-resolve"})
        if status == 409 and body.get("error", {}).get("code") == "nothing_to_resolve":
            return True
        if status >= 400:
            err = body.get("error", {})
            text = f"Uncertainty check failed ({err.get('code')}): {err.get('message')}"
            await self.report(campaign, run["id"], "work_failed", status="failed", band_room_id=room_id, summary=text)
            await tools.send_message(f"{text}. Stopping this run.", mentions=[self.ids["planner"]])
            return False
        r = body["data"]
        brief = r["brief"]
        if r["outcome"] == "abstain":
            text = ("Abstaining: " + " ".join(brief["uncertain"]) + " Still needed: "
                    + "; ".join(brief["would_make_actionable"]) + ". Not contacting; no enrichment or draft credits spent.")
            await self.report(campaign, run["id"], "abstained", status="abstained", band_room_id=room_id, summary=text)
            await tools.send_message(text, mentions=[self.ids["planner"]])
            return False
        found = "; ".join(s["detail"] for s in r["steps"] if s["found"])
        await self.report(campaign, run["id"], "uncertainty_resolved", band_room_id=room_id,
                          summary=f"Found the missing fact ({found}); lead moves from verify_first to contact_now.")
        return True


if __name__ == "__main__":
    asyncio.run(serve(Researcher()))
