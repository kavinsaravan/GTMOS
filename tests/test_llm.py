import json

import pytest
from pydantic import BaseModel

from models.common import ApiError
from integrations import llm


class Out(BaseModel):
    name: str
    score: int


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in ("LLM_PROVIDER", "BEDROCK_MODEL_ID", "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL", "PROVIDERS_SECRET_ID"):
        monkeypatch.delenv(k, raising=False)


def fake_bedrock(text="bedrock-reply", fail=None):
    def _f(system, user, max_tokens, temperature, timeout):
        if fail:
            raise fail
        return text, "bedrock-model"
    return _f


def fake_anthropic(text="anthropic-reply"):
    def _f(system, user, max_tokens, temperature, timeout):
        return text, "claude-test"
    return _f


def test_no_provider_configured(monkeypatch):
    with pytest.raises(ApiError) as e:
        llm.complete("s", "u")
    assert e.value.status == 503 and e.value.code == "llm_not_configured"
    assert llm.active_provider() == "none"


def test_auto_prefers_bedrock_when_configured(monkeypatch):
    monkeypatch.setenv("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-5")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setattr(llm, "_call_bedrock", fake_bedrock())
    monkeypatch.setattr(llm, "_call_anthropic", fake_anthropic())
    r = llm.complete("s", "u")
    assert (r.provider, r.fallback_reason) == ("bedrock", None)


def test_auto_falls_back_to_anthropic_on_bedrock_error(monkeypatch):
    monkeypatch.setenv("BEDROCK_MODEL_ID", "x")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setattr(llm, "_call_bedrock", fake_bedrock(fail=RuntimeError("Error 002")))
    monkeypatch.setattr(llm, "_call_anthropic", fake_anthropic())
    r = llm.complete("s", "u")
    assert r.provider == "anthropic" and r.fallback_reason == "bedrock_error:RuntimeError"


def test_auto_without_bedrock_id_uses_anthropic(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setattr(llm, "_call_anthropic", fake_anthropic())
    assert llm.complete("s", "u").provider == "anthropic"
    assert llm.active_provider() == "anthropic"


def test_bedrock_error_without_key_is_clean_502(monkeypatch):
    monkeypatch.setenv("BEDROCK_MODEL_ID", "x")
    monkeypatch.setattr(llm, "_call_bedrock", fake_bedrock(fail=RuntimeError("secret detail")))
    with pytest.raises(ApiError) as e:
        llm.complete("s", "u")
    assert e.value.status == 502 and "secret detail" not in e.value.message


def test_bedrock_only_mode_never_falls_back(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "bedrock")
    monkeypatch.setenv("BEDROCK_MODEL_ID", "x")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setattr(llm, "_call_bedrock", fake_bedrock(fail=RuntimeError("x")))
    monkeypatch.setattr(llm, "_call_anthropic", fake_anthropic())
    with pytest.raises(ApiError) as e:
        llm.complete("s", "u")
    assert e.value.code == "llm_unavailable"


def test_invalid_provider_setting(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "nope")
    with pytest.raises(ApiError):
        llm.complete("s", "u")


def test_anthropic_request_shape(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    seen = {}

    def fake_post(url, headers, body, timeout):
        seen.update(url=url, headers=headers, body=body, timeout=timeout)
        return {"content": [{"type": "text", "text": "hi"}]}

    monkeypatch.setattr(llm, "_http_post", fake_post)
    r = llm.complete("SYS", "USER", max_tokens=50)
    assert r.text == "hi" and r.provider == "anthropic"
    assert seen["url"] == "https://api.anthropic.com/v1/messages"
    assert seen["headers"]["x-api-key"] == "sk-test"
    assert seen["body"]["system"] == "SYS"
    assert seen["body"]["messages"] == [{"role": "user", "content": "USER"}]
    assert seen["timeout"] <= 25


def test_anthropic_http_error_does_not_leak(monkeypatch):
    import urllib.error

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    def boom(*a, **k):
        raise urllib.error.HTTPError("u", 401, "bad key sk-test", {}, None)

    monkeypatch.setattr(llm, "_http_post", boom)
    with pytest.raises(ApiError) as e:
        llm.complete("s", "u")
    assert e.value.status == 502 and "sk-test" not in e.value.message


@pytest.mark.parametrize("raw", [
    '{"name": "a", "score": 1}',
    '```json\n{"name": "a", "score": 1}\n```',
    'Sure! Here you go: {"name": "a", "score": 1} Hope that helps.',
])
def test_generate_json_tolerates_fences_and_prose(monkeypatch, raw):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setattr(llm, "_call_anthropic", fake_anthropic(raw))
    obj, res = llm.generate_json("s", "u", Out)
    assert obj == Out(name="a", score=1) and res.provider == "anthropic"


def test_generate_json_retries_once_then_succeeds(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    replies = iter(["not json", json.dumps({"name": "ok", "score": 2})])
    prompts = []

    def f(system, user, max_tokens, temperature, timeout):
        prompts.append(user)
        return next(replies), "m"

    monkeypatch.setattr(llm, "_call_anthropic", f)
    obj, _ = llm.generate_json("s", "u", Out)
    assert obj.score == 2 and len(prompts) == 2 and "invalid" in prompts[1]


def test_generate_json_fails_closed_after_retries(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setattr(llm, "_call_anthropic", fake_anthropic('{"name": "a", "score": "high"}'))
    with pytest.raises(ApiError) as e:
        llm.generate_json("s", "u", Out)
    assert e.value.status == 502 and e.value.code == "llm_invalid_output"


def test_llm_status_endpoint_requires_auth_and_hides_keys(table, monkeypatch):
    from conftest import call

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    assert call("GET", "/api/system/llm", token=None)[0] == 401
    status, body = call("GET", "/api/system/llm")
    assert status == 200
    assert body["data"] == {
        "active_provider": "anthropic", "mode": "auto",
        "bedrock_configured": False, "anthropic_configured": True,
    }
    assert "sk-secret" not in json.dumps(body)
