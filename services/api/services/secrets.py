"""Server-side credential access. Secrets never leave Lambda / never reach the browser.

Resolution order for each value: environment variable (local dev / tests), then
AWS Secrets Manager. Values are cached per execution environment.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache

import boto3


@lru_cache(maxsize=8)
def _secret_json(secret_id: str) -> dict:
    client = boto3.client("secretsmanager")
    raw = client.get_secret_value(SecretId=secret_id)["SecretString"]
    return json.loads(raw)


def clear_cache() -> None:  # used by tests
    _secret_json.cache_clear()


def api_token() -> str | None:
    env = os.environ.get("GTMOS_API_TOKEN")
    if env:
        return env
    secret_id = os.environ.get("APP_SECRET_ID")
    if not secret_id:
        return None
    return _secret_json(secret_id).get("api_token")


def provider_key(name: str) -> str | None:
    """Return a provider credential (e.g. 'TAVILY_API_KEY') or None if not configured."""
    env = os.environ.get(name)
    if env:
        return env
    secret_id = os.environ.get("PROVIDERS_SECRET_ID")
    if not secret_id:
        return None
    try:
        return _secret_json(secret_id).get(name) or None
    except Exception:  # secret absent => provider reported as not_configured
        return None
