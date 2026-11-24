"""LLM access with Amazon Bedrock as primary and the Anthropic API as fallback.

LLM_PROVIDER controls routing:
  auto       Bedrock if BEDROCK_MODEL_ID is set, falling back to the Anthropic API
             when Bedrock errors (access blocked, throttled, timeout) and a key exists.
             If Bedrock isn't configured, the Anthropic API is used directly.
  bedrock    Bedrock only (no fallback).
  anthropic  Anthropic API only.

Callers should keep untrusted text (web pages, search snippets) in the `user`
argument, never in `system`, and treat model output as untrusted until validated.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from models.common import ApiError
from services import secrets

log = logging.getLogger("gtmos.llm")

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"
REQUEST_TIMEOUT_S = 12   # per provider attempt
TOTAL_BUDGET_S = 24      # whole operation; Lambda is 29s and HTTP API cuts off at 30s
MIN_ATTEMPT_S = 3        # don't start an attempt with less time than this

T = TypeVar("T", bound=BaseModel)


@dataclass
class LLMResult:
    text: str
    provider: str          # "bedrock" | "anthropic"
    model: str
    fallback_reason: str | None = None  # set when Bedrock failed and Anthropic answered


# --- providers -------------------------------------------------------------

def _call_bedrock(system: str, user: str, max_tokens: int, temperature: float,
                  timeout: float) -> tuple[str, str]:
    import boto3
    from botocore.config import Config

    model_id = os.environ["BEDROCK_MODEL_ID"]
    client = boto3.client(
        "bedrock-runtime",
        config=Config(read_timeout=timeout, connect_timeout=min(5, timeout), retries={"max_attempts": 1}),
    )
    resp = client.converse(
        modelId=model_id,
        system=[{"text": system}],
        messages=[{"role": "user", "content": [{"text": user}]}],
        inferenceConfig={"maxTokens": max_tokens, "temperature": temperature},
    )
    parts = resp["output"]["message"]["content"]
    return "".join(p.get("text", "") for p in parts), model_id


def _http_post(url: str, headers: dict, body: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec - fixed https URL
        return json.loads(resp.read())


def _call_anthropic(system: str, user: str, max_tokens: int, temperature: float,
                    timeout: float) -> tuple[str, str]:
    key = secrets.provider_key("ANTHROPIC_API_KEY")
    if not key:
        raise ApiError(503, "llm_not_configured", "No LLM provider is configured")
    model = os.environ.get("ANTHROPIC_MODEL") or DEFAULT_ANTHROPIC_MODEL
    data = _http_post(
        ANTHROPIC_URL,
        {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION, "content-type": "application/json"},
        {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        },
        timeout,
    )
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    return text, model


# --- routing ---------------------------------------------------------------

def _anthropic_available() -> bool:
    return bool(secrets.provider_key("ANTHROPIC_API_KEY"))


def active_provider() -> str:
    """Which provider a call would try first (for health/diagnostics; never exposes keys)."""
    mode = os.environ.get("LLM_PROVIDER", "auto").lower()
    if mode == "anthropic":
        return "anthropic"
    if mode == "bedrock":
        return "bedrock"
    if os.environ.get("BEDROCK_MODEL_ID"):
        return "bedrock"
    return "anthropic" if _anthropic_available() else "none"


def complete(system: str, user: str, *, max_tokens: int = 1024, temperature: float = 0.2,
             budget_s: float = TOTAL_BUDGET_S) -> LLMResult:
    deadline = time.monotonic() + budget_s

    def attempt_timeout() -> float:
        t = min(REQUEST_TIMEOUT_S, deadline - time.monotonic())
        if t < MIN_ATTEMPT_S:
            raise ApiError(502, "llm_unavailable", "The language model provider timed out")
        return t

    mode = os.environ.get("LLM_PROVIDER", "auto").lower()
    if mode not in {"auto", "bedrock", "anthropic"}:
        raise ApiError(500, "misconfigured", "Invalid LLM_PROVIDER")

    use_bedrock = mode == "bedrock" or (mode == "auto" and bool(os.environ.get("BEDROCK_MODEL_ID")))
    fallback_reason: str | None = None

    if use_bedrock:
        if not os.environ.get("BEDROCK_MODEL_ID"):
            raise ApiError(503, "llm_not_configured", "BEDROCK_MODEL_ID is not set")
        try:
            text, model = _call_bedrock(system, user, max_tokens, temperature, attempt_timeout())
            return LLMResult(text, "bedrock", model)
        except Exception as exc:  # botocore ClientError, timeouts, etc.
            code = getattr(exc, "response", {}).get("Error", {}).get("Code") or type(exc).__name__
            log.warning("bedrock call failed: %s", code)
            if mode == "bedrock" or not _anthropic_available():
                raise ApiError(502, "llm_unavailable", "The language model provider is unavailable") from None
            fallback_reason = f"bedrock_error:{code}"

    try:
        text, model = _call_anthropic(system, user, max_tokens, temperature, attempt_timeout())
    except ApiError:
        raise
    except urllib.error.HTTPError as exc:
        log.warning("anthropic call failed: HTTP %s", exc.code)
        raise ApiError(502, "llm_unavailable", "The language model provider is unavailable") from None
    except Exception as exc:
        log.warning("anthropic call failed: %s", type(exc).__name__)
        raise ApiError(502, "llm_unavailable", "The language model provider is unavailable") from None
    return LLMResult(text, "anthropic", model, fallback_reason)


# --- validated JSON --------------------------------------------------------

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def extract_json(text: str) -> object:
    """Parse model output as JSON, tolerating code fences and surrounding prose."""
    cleaned = _FENCE.sub("", text.strip())
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            raise
        return json.loads(cleaned[start:end + 1])


def generate_json(system: str, user: str, model_cls: type[T], *, max_tokens: int = 1500,
                  retries: int = 1, budget_s: float = TOTAL_BUDGET_S) -> tuple[T, LLMResult]:
    """Call the model and validate the output against a Pydantic schema.

    On malformed or schema-invalid output, re-prompt up to `retries` times with the
    validation error. After that, fail with a 502 rather than passing bad data on.
    """
    schema_hint = json.dumps(model_cls.model_json_schema())
    sys_prompt = (
        f"{system}\n\nRespond with a single JSON object only, no prose and no code fences, "
        f"matching this JSON Schema:\n{schema_hint}"
    )
    prompt = user
    last_error = ""
    deadline = time.monotonic() + budget_s
    for attempt in range(retries + 1):
        remaining = deadline - time.monotonic()
        if attempt > 0 and remaining < 2 * MIN_ATTEMPT_S:
            break  # not enough time for a useful retry
        result = complete(sys_prompt, prompt, max_tokens=max_tokens, budget_s=remaining)
        try:
            return model_cls.model_validate(extract_json(result.text)), result
        except (json.JSONDecodeError, ValidationError) as exc:
            last_error = str(exc)[:500]
            prompt = (
                f"{user}\n\nYour previous reply was invalid ({last_error}). "
                "Reply again with only a valid JSON object that matches the schema."
            )
    log.warning("llm output failed validation: %s", last_error)
    raise ApiError(502, "llm_invalid_output", "The model returned output that failed validation")
