# planner llm: pick the provider/model with .env and call it with retries.

import asyncio
import os
from typing import TypeVar
from dotenv import load_dotenv
from pydantic import BaseModel
from browser_use import ChatAnthropic, ChatGoogle, ChatGroq, ChatOllama
from browser_use.llm.base import BaseChatModel
from browser_use.llm.exceptions import ModelProviderError, ModelRateLimitError
from browser_use.llm.messages import BaseMessage
from browser_use.llm.views import ChatInvokeCompletion

T = TypeVar("T", bound=BaseModel)

load_dotenv()

# accept "claude" as an alias for "anthropic" in .env
def _provider(name: str) -> str:
    p = (name or "groq").lower()
    return "anthropic" if p == "claude" else p

# ── planner model ─────────────────────────────────────────────────────────────
# pick the provider/model with .env (PLANNER_PROVIDER, and OLLAMA/GROQ/CLAUDE_MODEL)
# anthropic / claude-sonnet-5-5 is the baseline model: every evaluation run from
# the baseline on uses it, so results are comparable. other providers are dev-only
PLANNER_PROVIDER = _provider(os.getenv("PLANNER_PROVIDER", "anthropic"))

PLANNER_MODELS = {
    "ollama": os.getenv("OLLAMA_MODEL", "qwen2.5:7b"),
    "groq": os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"),
    "anthropic": os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5"),
    "gemini": os.getenv("GEMINI_MODEL", "gemini-3.5-flash"),
}

def _build_llm(provider: str, model: str):
    # turn a provider name + model into a browser-use chat object with the right key
    if provider == "ollama":
        # local ollama server (defaults to http://localhost:11434). no key, no limits
        return ChatOllama(model=model, host=os.getenv("OLLAMA_HOST"))
    if provider == "groq":
        return ChatGroq(model=model, api_key=os.getenv("GROQ_API_KEY"))
    if provider == "anthropic":
        # thinking must be set: without it browser_use forces the structured-output
        # tool, which sonnet 5.5 rejects (400). effort is fixed so runs are comparable
        # (thinking is billed as output). no temperature: sonnet 5.5 rejects it
        return ChatAnthropic(
            model=model,
            api_key=os.getenv("CLAUDE_API_KEY") or os.getenv("ANTHROPIC_API_KEY"),
            thinking={"type": "adaptive"},
            output_config={"effort": os.getenv("CLAUDE_EFFORT", "medium")},
        )
    if provider == "gemini":
        return ChatGoogle(
            model=model,
            api_key=os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"),
        )
    raise ValueError(
        f"Unknown PLANNER_PROVIDER {provider!r}; use ollama | groq | anthropic | gemini"
    )

def make_planner_llm():
    # the planner / DOM-verifier model (text). one place to choose it
    # unknown provider -> "" here, and _build_llm raises a clear error for it
    return _build_llm(PLANNER_PROVIDER, PLANNER_MODELS.get(PLANNER_PROVIDER, ""))


# ── vision model  ──────────
# set USE_VISION=false to turn the consensus off and fall back to DOM-only verification
USE_VISION = os.getenv("USE_VISION", "true").lower() in ("1", "true", "yes")

# price-lookup tuning for the glasses demo: the price rules in the planner and
# verifier prompts, plus the early exits once a $ price is found (nodes.py).
# on by default for the demo; test runs set PRICE_TUNING=false so general tasks
# get the original prompts and no price shortcuts
PRICE_TUNING = os.getenv("PRICE_TUNING", "true").lower() in ("1", "true", "yes")
VISION_PROVIDER = _provider(os.getenv("VISION_PROVIDER", "anthropic"))
_VISION_DEFAULTS = {
    "gemini": "gemini-3.5-flash",
    # same as the planner: _build_llm turns on adaptive thinking for anthropic,
    # which older claude models reject (400), and one model keeps evaluation comparable
    "anthropic": "claude-sonnet-5-5",
    "ollama": "qwen2.5vl:7b",
    "groq": "meta-llama/llama-4-scout-17b-16e-instruct",
}
VISION_MODEL = os.getenv("VISION_MODEL", _VISION_DEFAULTS.get(VISION_PROVIDER, "gemini-3.5-flash"))


def make_vision_llm():
    # returns None when vision is disabled -> analyse uses DOM-only verification.
    # vision only (PERCEPTION=vision) always has it: there it's the only verifier
    if not USE_VISION and PERCEPTION != "vision":
        return None
    return _build_llm(VISION_PROVIDER, VISION_MODEL)


# ── perception: what the planner sees of the page (suggestions #4, #10) ─────
# the four versions in the comparison: dom (the element list, the agent as it
# was), vision (a screenshot with numbered boxes instead), hybrid (both), and
# vision with VERIFIER=off (no model checks). the defaults are the dom agent
PERCEPTION = os.getenv("PERCEPTION", "dom").strip().lower()
if PERCEPTION not in ("dom", "vision", "hybrid"):
    raise ValueError(f"Unknown PERCEPTION {PERCEPTION!r}; use dom | vision | hybrid")
VERIFIER = os.getenv("VERIFIER", "on").strip().lower() not in ("0", "off", "false", "no")
# vision only: also send a clean copy of the screenshot, before the numbered one.
# the number labels cover some of the page's text (Kilimanjaro's "19,341 ft" on
# Wikipedia, 1 Nov), and vision only has nothing else to read it from. off = the
# numbered screenshot alone, as in WebVoyager. the pilot runs both (game plan)
VISION_CLEAN = os.getenv("VISION_CLEAN", "off").strip().lower() in ("1", "on", "true", "yes")


# ── glasses POV input (independent of USE_VISION analyse gate) ───────────────
GLASSES_VISION_PROVIDER = _provider(os.getenv("GLASSES_VISION_PROVIDER", "anthropic"))
GLASSES_VISION_MODEL = os.getenv(
    "GLASSES_VISION_MODEL",
    _VISION_DEFAULTS.get(GLASSES_VISION_PROVIDER, "claude-sonnet-5-5"),
)


def make_glasses_vision_llm():
    # always vision-capable — used to read the photo the glasses send in
    return _build_llm(GLASSES_VISION_PROVIDER, GLASSES_VISION_MODEL)


# ── extraction model (for the `extract` action) ──────────────────────────────
# extract sends the WHOLE page to the model, so it needs a big context + generous
# rate limit. the planner model can be too small. defaults to the vision model
# (claude-sonnet-5-5 by default), independently overridable via EXTRACTION_PROVIDER / EXTRACTION_MODEL.
EXTRACTION_PROVIDER = _provider(os.getenv("EXTRACTION_PROVIDER", VISION_PROVIDER))
EXTRACTION_MODEL = os.getenv("EXTRACTION_MODEL", VISION_MODEL)


def make_extraction_llm():
    return _build_llm(EXTRACTION_PROVIDER, EXTRACTION_MODEL)

# longest rate-limit wait _ainvoke_with_retry backs off for before giving up.
# keep it under the node timeout in graph.py (30s)
MAX_RATE_LIMIT_WAIT = 20


class LLMLimitError(RuntimeError):
    # rate/quota limit that backing off won't fix. its own class so the graph's
    # error handler can tell the user "usage limit" instead of a generic failure.
    # a RuntimeError, so langgraph's default retry_on doesn't retry it
    pass


def _parse_retry_seconds(msg: str) -> float | None:
    # pull a suggested wait out of a rate-limit message. handles groq's
    # 'try again in 5m55.968s', gemini's 'retry in 14s' and 'retryDelay: 16s'.
    import re
    m = re.search(r"in\s+(?:(\d+)m)?([\d.]+)\s*s", msg)
    if m:
        return float(m.group(1) or 0) * 60 + float(m.group(2))
    m = re.search(r"'retryDelay':\s*'(\d+)s'", msg)
    return float(m.group(1)) if m else None


async def _ainvoke_with_retry(llm: BaseChatModel, messages: list[BaseMessage],
                              output_format: type[T], attempts: int = 4) -> ChatInvokeCompletion[T]:
    # call an llm for structured output. backs off on short (per-minute) rate
    # limits; on a long/daily cap it raises a clear error instead of a stack
    # trace. works across providers 
    for i in range(attempts):
        try:
            return await llm.ainvoke(messages, output_format=output_format)
        except (ModelRateLimitError, ModelProviderError) as e:
            msg = str(e)
            # a reply missing required fields is a bad draw, not a limit: ask
            # again. the planner sent only `text` or `answer`, without `action`
            # or `reasoning`, 5 times in versions B and C (31 Oct – 1 Nov); each
            # ended the task as llm_failed, and the task's re-run passed every time
            if "validation error" in msg and i < attempts - 1:
                print("  model reply was missing fields; asking again…")
                continue
            is_rate = (isinstance(e, ModelRateLimitError)
                       or "429" in msg or "rate_limit" in msg or "RESOURCE_EXHAUSTED" in msg)
            if not is_rate:
                raise
            wait = _parse_retry_seconds(msg)
            hard_cap = "limit: 0" in msg

            # a long wait also means a per-day / big-window cap; backing off won't help.
            # capped at 20s so the wait fits inside the 30s node timeout (graph.py):
            # a longer one would be cut off and reported as timed_out, not rate_limited
            if hard_cap or wait is None or wait > MAX_RATE_LIMIT_WAIT or i == attempts - 1:
                model = getattr(llm, "model", "the model")
                if hard_cap:
                    raise LLMLimitError(
                        f"No quota for {model} on this key (free-tier limit is 0). "
                        f"Try a different model (e.g. GEMINI_MODEL=gemini-2.5-flash), a "
                        f"fresh key from https://aistudio.google.com/apikey, or set "
                        f"PLANNER_PROVIDER=ollama (local, unlimited) in .env."
                    ) from e
                hint = (f" (resets in ~{wait:.0f}s)" if wait and wait < 120
                        else f" (resets in ~{wait / 60:.0f}m)" if wait else "")
                raise LLMLimitError(
                    f"Rate/token limit hit on {model}{hint}. Likely the free daily "
                    f"budget is spent. Options: set PLANNER_PROVIDER=ollama (local, "
                    f"unlimited) in .env, switch GROQ_MODEL (e.g. openai/gpt-oss-20b), "
                    f"or wait for the daily reset."
                ) from e
            print(f"  rate-limited; waiting {wait:.0f}s then retrying…")
            await asyncio.sleep(wait + 1)
    # unreachable: the last attempt either returns or raises above
    raise RuntimeError("_ainvoke_with_retry: no attempts made")
