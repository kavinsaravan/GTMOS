"""Single local worker: polls the GTMOS API for queued orchestration runs, claims each one and runs it
through the LangGraph chain (graph.py). Run: python -m agents.worker"""
from __future__ import annotations

import logging
import time

from agents.common import Gtmos, log
from agents.graph import build_graph, run_chain

POLL_SECONDS = 5


def process(api, graph, run: dict) -> bool:
    """Claim and run one queued run. Returns False if someone else already claimed it."""
    cid, rid = run["campaign_id"], run["id"]
    status, _ = api.call("POST", f"/api/campaigns/{cid}/orchestrations/{rid}/claim", {})
    if status >= 400:
        return False                                # another worker (or an earlier poll) has it
    run_chain(graph, run)
    return True


def poll_forever(api, graph) -> None:
    while True:
        try:
            status, body = api.call("GET", "/api/orchestrations/queued")
            for run in (body.get("data", []) if status == 200 else []):
                process(api, graph, run)
        except Exception as exc:
            log.warning("poll failed: %s", type(exc).__name__)
        time.sleep(POLL_SECONDS)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    api = Gtmos()
    poll_forever(api, build_graph(api))


if __name__ == "__main__":
    main()
