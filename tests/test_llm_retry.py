# rate limits are common on free tiers: short waits should be retried quietly,
# long or zero quotas should fail fast with a message that says what to change.

import pytest
from browser_use.llm.exceptions import ModelProviderError, ModelRateLimitError

import llm
from conftest import FakeLLM


@pytest.mark.parametrize("msg,seconds", [
    ("Please try again in 5m55.968s", 355.968),
    ("retry in 14s", 14.0),
    ("{'retryDelay': '16s'}", 16.0),
    ("no hint at all", None),
])
def test_retry_hint_parsing(msg, seconds):
    got = llm._parse_retry_seconds(msg)
    assert got == pytest.approx(seconds) if seconds is not None else got is None


@pytest.fixture
def no_sleep(monkeypatch):
    waits = []

    async def fake_sleep(s):
        waits.append(s)

    monkeypatch.setattr(llm.asyncio, "sleep", fake_sleep)
    return waits


async def test_short_rate_limit_is_retried(no_sleep):
    fake = FakeLLM(ModelRateLimitError("rate_limit: try again in 2s"), "ok")
    r = await llm._ainvoke_with_retry(fake, [], str)
    assert r.completion == "ok"
    assert no_sleep == [3.0]


async def test_long_rate_limit_fails_fast_with_advice(no_sleep):
    fake = FakeLLM(ModelRateLimitError("429 try again in 30m0s"))
    with pytest.raises(RuntimeError, match="PLANNER_PROVIDER=ollama"):
        await llm._ainvoke_with_retry(fake, [], str)
    assert no_sleep == []


async def test_zero_quota_explains_the_key_problem(no_sleep):
    fake = FakeLLM(ModelRateLimitError("RESOURCE_EXHAUSTED limit: 0, retry in 5s"))
    with pytest.raises(RuntimeError, match="No quota"):
        await llm._ainvoke_with_retry(fake, [], str)


async def test_non_rate_limit_errors_are_not_swallowed(no_sleep):
    fake = FakeLLM(ModelProviderError("invalid api key", status_code=401))
    with pytest.raises(ModelProviderError):
        await llm._ainvoke_with_retry(fake, [], str)


def test_claude_alias_maps_to_anthropic():
    assert llm._provider("claude") == "anthropic"
    assert llm._provider("") == "groq"
