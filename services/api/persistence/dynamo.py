"""DynamoDB access. Single table, tenant-scoped keys.

Key design (documented in docs/architecture.md):
  PK = ORG#<org>#CAMPAIGN#<campaign>     (every campaign-owned item shares this PK)
  SK = META | ICP#<ver> | LEAD#<id> | DRAFT#<id> | EVT#<iso>#<id> | RUN#<id> | USAGE#<op>
  GSI1PK = ORG#<org>, GSI1SK = CAMPAIGN#<created_at>   (list campaigns for an org)
Every item carries org_id so tenant ownership is explicit on every write.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key

_table = None


def table():
    global _table
    if _table is None:
        _table = boto3.resource("dynamodb").Table(os.environ["DYNAMODB_TABLE_NAME"])
    return _table


def reset_table_cache() -> None:  # used by tests
    global _table
    _table = None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def pk(org_id: str, campaign_id: str) -> str:
    return f"ORG#{org_id}#CAMPAIGN#{campaign_id}"


def clean(value: Any) -> Any:
    """Convert DynamoDB Decimals to int/float so items are JSON-serialisable."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [clean(v) for v in value]
    return value


def to_ddb(value: Any) -> Any:
    """DynamoDB rejects Python floats; convert them to Decimal before writing."""
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: to_ddb(v) for k, v in value.items()}
    if isinstance(value, list):
        return [to_ddb(v) for v in value]
    return value


def public(item: dict | None) -> dict | None:
    """Strip storage keys before returning an item to callers."""
    if item is None:
        return None
    hidden = {"PK", "SK", "GSI1PK", "GSI1SK"}
    return clean({k: v for k, v in item.items() if k not in hidden})


def put_campaign(item: dict) -> None:
    # Conditional put: a retried create with the same id can never overwrite.
    table().put_item(Item=item, ConditionExpression="attribute_not_exists(PK)")


def get_campaign(org_id: str, campaign_id: str) -> dict | None:
    resp = table().get_item(Key={"PK": pk(org_id, campaign_id), "SK": "META"})
    return resp.get("Item")


def list_campaigns(org_id: str, limit: int = 50) -> list[dict]:
    resp = table().query(
        IndexName="GSI1",
        KeyConditionExpression=Key("GSI1PK").eq(f"ORG#{org_id}"),
        ScanIndexForward=False,
        Limit=limit,
    )
    return resp.get("Items", [])


def put_event(item: dict) -> None:
    table().put_item(Item=item)


def list_events(org_id: str, campaign_id: str, limit: int = 100) -> list[dict]:
    resp = table().query(
        KeyConditionExpression=Key("PK").eq(pk(org_id, campaign_id)) & Key("SK").begins_with("EVT#"),
        ScanIndexForward=False,
        Limit=limit,
    )
    return resp.get("Items", [])


# ---------------------------------------------------------------- ICP
def icp_sk(version: int) -> str:
    return f"ICP#{version:04d}"


def put_icp(item: dict) -> None:
    # Versions are immutable; a concurrent writer of the same version loses.
    table().put_item(Item=to_ddb(item), ConditionExpression="attribute_not_exists(PK)")


def latest_icp(org_id: str, campaign_id: str) -> dict | None:
    resp = table().query(
        KeyConditionExpression=Key("PK").eq(pk(org_id, campaign_id)) & Key("SK").begins_with("ICP#"),
        ScanIndexForward=False,
        Limit=1,
    )
    items = resp.get("Items", [])
    return items[0] if items else None


def approve_icp(org_id: str, campaign_id: str, version: int, approved_at: str) -> None:
    table().update_item(
        Key={"PK": pk(org_id, campaign_id), "SK": icp_sk(version)},
        UpdateExpression="SET approved_at = :t",
        ConditionExpression="attribute_exists(PK)",
        ExpressionAttributeValues={":t": approved_at},
    )


def set_stage(org_id: str, campaign_id: str, stage: str) -> None:
    table().update_item(
        Key={"PK": pk(org_id, campaign_id), "SK": "META"},
        UpdateExpression="SET stage = :s",
        ConditionExpression="attribute_exists(PK)",
        ExpressionAttributeValues={":s": stage},
    )


# ---------------------------------------------------------------- leads / runs
def put_lead(item: dict) -> bool:
    """Create a lead keyed by normalized domain. Returns False if it already exists (dedupe)."""
    try:
        table().put_item(Item=to_ddb(item), ConditionExpression="attribute_not_exists(PK)")
        return True
    except Exception as exc:
        if type(exc).__name__ == "ConditionalCheckFailedException":
            return False
        raise


def lead_sk(domain: str) -> str:
    return f"LEAD#{domain}"


def list_leads(org_id: str, campaign_id: str, limit: int = 200) -> list[dict]:
    resp = table().query(
        KeyConditionExpression=Key("PK").eq(pk(org_id, campaign_id)) & Key("SK").begins_with("LEAD#"),
        Limit=limit,
    )
    return resp.get("Items", [])


def get_lead_by_id(org_id: str, campaign_id: str, lead_id: str) -> dict | None:
    for item in list_leads(org_id, campaign_id):
        if item.get("id") == lead_id:
            return item
    return None


def update_lead(org_id: str, campaign_id: str, domain: str, fields: dict) -> None:
    """Set top-level attributes on an existing lead (never creates one)."""
    names = {f"#f{i}": k for i, k in enumerate(fields)}
    values = {f":v{i}": to_ddb(v) for i, v in enumerate(fields.values())}
    expr = "SET " + ", ".join(f"#f{i} = :v{i}" for i in range(len(fields)))
    table().update_item(
        Key={"PK": pk(org_id, campaign_id), "SK": lead_sk(domain)},
        UpdateExpression=expr,
        ConditionExpression="attribute_exists(PK)",
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
    )


# ---------------------------------------------------------------- drafts
def draft_sk(lead_id: str, version: int) -> str:
    return f"DRAFT#{lead_id}#{version:04d}"


def put_draft(item: dict) -> None:
    """Draft versions are immutable records; a concurrent writer of the same version loses."""
    table().put_item(Item=to_ddb(item), ConditionExpression="attribute_not_exists(PK)")


def latest_draft(org_id: str, campaign_id: str, lead_id: str) -> dict | None:
    resp = table().query(
        KeyConditionExpression=Key("PK").eq(pk(org_id, campaign_id)) & Key("SK").begins_with(f"DRAFT#{lead_id}#"),
        ScanIndexForward=False,
        Limit=1,
    )
    items = resp.get("Items", [])
    return items[0] if items else None


def list_drafts(org_id: str, campaign_id: str, lead_id: str | None = None, limit: int = 200) -> list[dict]:
    prefix = f"DRAFT#{lead_id}#" if lead_id else "DRAFT#"
    resp = table().query(
        KeyConditionExpression=Key("PK").eq(pk(org_id, campaign_id)) & Key("SK").begins_with(prefix),
        Limit=limit,
    )
    return resp.get("Items", [])


def set_draft_fields(org_id: str, campaign_id: str, sk: str, fields: dict, *,
                     only_if_status: tuple[str, ...] | None = None) -> bool:
    """Update top-level attributes of a draft version. When `only_if_status` is given the
    update applies only if the current status is one of those, which makes transitions
    race-safe and retry-safe. Returns False if the condition failed."""
    names = {f"#f{i}": k for i, k in enumerate(fields)}
    values = {f":v{i}": to_ddb(v) for i, v in enumerate(fields.values())}
    cond = "attribute_exists(PK)"
    if only_if_status:
        names["#st"] = "status"
        cond += " AND #st IN (" + ", ".join(f":s{i}" for i in range(len(only_if_status))) + ")"
        values.update({f":s{i}": s for i, s in enumerate(only_if_status)})
    try:
        table().update_item(
            Key={"PK": pk(org_id, campaign_id), "SK": sk},
            UpdateExpression="SET " + ", ".join(f"#f{i} = :v{i}" for i in range(len(fields))),
            ConditionExpression=cond,
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )
        return True
    except Exception as exc:
        if type(exc).__name__ == "ConditionalCheckFailedException":
            return False
        raise


def put_run(item: dict) -> None:
    table().put_item(Item=to_ddb(item))


# ------------------------------------------------- Agent orchestration runs
def orch_sk(run_id: str) -> str:
    return f"ORCH#{run_id}"


def orch_gsi(org_id: str) -> str:
    return f"ORG#{org_id}#ORCH"


def put_orch(item: dict) -> None:
    table().put_item(Item=to_ddb(item), ConditionExpression="attribute_not_exists(PK)")


def get_orch(org_id: str, campaign_id: str, run_id: str) -> dict | None:
    return table().get_item(Key={"PK": pk(org_id, campaign_id), "SK": orch_sk(run_id)}).get("Item")


def list_orch(org_id: str, campaign_id: str, limit: int = 50) -> list[dict]:
    resp = table().query(
        KeyConditionExpression=Key("PK").eq(pk(org_id, campaign_id)) & Key("SK").begins_with("ORCH#"),
        Limit=limit,
    )
    return resp.get("Items", [])


def list_orch_by_status(org_id: str, status: str, limit: int = 20) -> list[dict]:
    resp = table().query(
        IndexName="GSI1",
        KeyConditionExpression=Key("GSI1PK").eq(orch_gsi(org_id)),
        FilterExpression="#st = :s",
        ExpressionAttributeNames={"#st": "status"},
        ExpressionAttributeValues={":s": status},
    )
    return resp.get("Items", [])[:limit]


def transition_orch(org_id: str, campaign_id: str, run_id: str, *, new_status: str | None = None,
                    only_from: tuple[str, ...] | None = None, event: dict | None = None,
                    fields: dict | None = None) -> bool:
    """Race-safe update of a run: optional status change guarded by `only_from`, optional trace append."""
    sets, names, values = ["#upd = :upd"], {"#upd": "updated_at"}, {":upd": now_iso()}
    if new_status:
        sets.append("#st = :new")
        names["#st"] = "status"
        values[":new"] = new_status
    if event is not None:
        sets.append("#tr = list_append(if_not_exists(#tr, :empty), :ev)")
        names["#tr"] = "trace"
        values[":empty"] = []
        values[":ev"] = [to_ddb(event)]
    for i, (k, v) in enumerate((fields or {}).items()):
        sets.append(f"#x{i} = :x{i}")
        names[f"#x{i}"] = k
        values[f":x{i}"] = to_ddb(v)
    cond = "attribute_exists(PK)"
    if only_from:
        names["#st"] = "status"
        cond += " AND #st IN (" + ", ".join(f":f{i}" for i in range(len(only_from))) + ")"
        values.update({f":f{i}": s for i, s in enumerate(only_from)})
    try:
        table().update_item(
            Key={"PK": pk(org_id, campaign_id), "SK": orch_sk(run_id)},
            UpdateExpression="SET " + ", ".join(sets), ConditionExpression=cond,
            ExpressionAttributeNames=names, ExpressionAttributeValues=values)
        return True
    except Exception as exc:
        if type(exc).__name__ == "ConditionalCheckFailedException":
            return False
        raise
