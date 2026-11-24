"""One-time, idempotent Flexprice setup: one metered feature (COUNT meter) per billable operation.

Usage: AWS_PROFILE=gtmos AWS_REGION=us-west-2 PROVIDERS_SECRET_ID=gtmos/providers \
       python infra/flexprice_setup.py
Wallet/customer creation is lazy and happens in the API on first billable request.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "api"))
os.environ.setdefault("AWS_DEFAULT_REGION", "us-west-2")

from integrations import flexprice as fp  # noqa: E402
from services import billing  # noqa: E402


def main() -> None:
    if not fp.configured():
        sys.exit("Flexprice is not configured (set PROVIDERS_SECRET_ID or FLEXPRICE_API_KEY/URL)")
    for op, event_name in billing.EVENT_NAMES.items():
        status, body = fp._request("GET", f"/features?lookup_key={event_name}")
        items = body.get("items", []) if isinstance(body, dict) else []
        if any(i.get("lookup_key") == event_name for i in items):
            print(f"{op}: feature exists")
            continue
        status, body = fp._request("POST", "/features", {
            "name": f"GTMOS {op.replace('_', ' ')}", "type": "metered", "lookup_key": event_name,
            "description": f"{billing.COSTS[op]} credit(s) per {op.replace('_', ' ')}",
            "meter": {"name": f"GTMOS {op}", "event_name": event_name,
                      "aggregation": {"type": "COUNT"}, "reset_usage": "NEVER"}})
        print(f"{op}: HTTP {status}", "created" if status in (200, 201) else body)


if __name__ == "__main__":
    main()
