"""Evidence packs in S3: the raw public sources a run retrieved, kept so every
citation on a lead can be traced back to the exact excerpt the model saw."""
from __future__ import annotations

import json
import os

import boto3


def put_pack(org_id: str, campaign_id: str, run_id: str, pack: dict) -> str:
    key = f"evidence/{org_id}/{campaign_id}/{run_id}.json"
    boto3.client("s3").put_object(
        Bucket=os.environ["S3_EVIDENCE_BUCKET"], Key=key,
        Body=json.dumps(pack).encode(), ContentType="application/json",
    )
    return key
