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
