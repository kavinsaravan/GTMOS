import os
import sys
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "api"))

TOKEN = "test-token-123"


@pytest.fixture(autouse=True)
def aws_env(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-west-2")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("DYNAMODB_TABLE_NAME", "gtmos-test")
    monkeypatch.setenv("GTMOS_API_TOKEN", TOKEN)
    monkeypatch.setenv("S3_EVIDENCE_BUCKET", "gtmos-evidence-test")
    for k in ("TAVILY_API_KEY", "ANTHROPIC_API_KEY", "BEDROCK_MODEL_ID", "PROVIDERS_SECRET_ID",
              "NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD", "NEO4J_DATABASE",
              "FLEXPRICE_API_KEY", "FLEXPRICE_API_URL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.delenv("APP_SECRET_ID", raising=False)


@pytest.fixture
def table(aws_env):
    from persistence import dynamo

    with mock_aws():
        dynamo.reset_table_cache()
        boto3.client("s3", region_name="us-west-2").create_bucket(
            Bucket="gtmos-evidence-test",
            CreateBucketConfiguration={"LocationConstraint": "us-west-2"})
        ddb = boto3.resource("dynamodb", region_name="us-west-2")
        ddb.create_table(
            TableName="gtmos-test",
            BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[
                {"AttributeName": n, "AttributeType": "S"} for n in ("PK", "SK", "GSI1PK", "GSI1SK")
            ],
            KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}],
            GlobalSecondaryIndexes=[{
                "IndexName": "GSI1",
                "KeySchema": [{"AttributeName": "GSI1PK", "KeyType": "HASH"},
                              {"AttributeName": "GSI1SK", "KeyType": "RANGE"}],
                "Projection": {"ProjectionType": "ALL"},
            }],
        )
        yield
        dynamo.reset_table_cache()


def call(method: str, path: str, body=None, token: str | None = TOKEN, raw_body: str | None = None):
    """Invoke the Lambda handler with an API Gateway v2-shaped event."""
    import json

    import app

    headers = {"x-gtmos-token": token} if token is not None else {}
    event = {
        "rawPath": path,
        "headers": headers,
        "requestContext": {"http": {"method": method}},
        "body": raw_body if raw_body is not None else (json.dumps(body) if body is not None else None),
    }
    resp = app.handler(event)
    return resp["statusCode"], json.loads(resp["body"])
