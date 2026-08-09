# planner llm: pick the provider/model with .env and call it with retries.

import asyncio
import os
from dotenv import load_dotenv
from browser_use import ChatAnthropic, ChatGoogle, ChatGroq, ChatOllama
from browser_use.llm.exceptions import ModelProviderError, ModelRateLimitError

load_dotenv()

# ── planner model ─────────────────────────────────────────────────────────────
# pick the provider/model with .env (PLANNER_PROVIDER, and OLLAMA/GROQ/CLAUDE_MODEL)
PLANNER_PROVIDER = os.getenv("PLANNER_PROVIDER", "groq").lower()

PLANNER_MODELS = {
    "ollama": os.getenv("OLLAMA_MODEL", "qwen2.5:7b"),
    "groq": os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"),
    "anthropic": os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001"),
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
        return ChatAnthropic(
            model=model,
            api_key=os.getenv("CLAUDE_API_KEY") or os.getenv("ANTHROPIC_API_KEY"),
        )
    if provider == "gemini":
        return ChatGoogle(
            model=model,
            api_key=os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"),
        )
    raise ValueError(
        f"Unknown provider {provider!r}; use ollama | groq | anthropic | gemini"
    )


def make_planner_llm():
    # the planner / DOM-verifier model (text). one place to choose it
    return _build_llm(PLANNER_PROVIDER, PLANNER_MODELS.get(PLANNER_PROVIDER))


# ── vision model  ──────────
# set USE_VISION=false to turn the consensus off and fall back to DOM-only verification
USE_VISION = os.getenv("USE_VISION", "true").lower() in ("1", "true", "yes")
VISION_PROVIDER = os.getenv("VISION_PROVIDER", "gemini").lower()
_VISION_DEFAULTS = {
    "gemini": "gemini-3.5-flash",
    "anthropic": "claude-haiku-4-5-20251001",
    "ollama": "qwen2.5vl:7b",
    "groq": "meta-llama/llama-4-scout-17b-16e-instruct",
}
VISION_MODEL = os.getenv("VISION_MODEL", _VISION_DEFAULTS.get(VISION_PROVIDER, "gemini-3.5-flash"))


def make_vision_llm():
    # returns None when vision is disabled -> analyse uses DOM-only verification
    if not USE_VISION:
        return None
    return _build_llm(VISION_PROVIDER, VISION_MODEL)


# ── extraction model (for the `extract` action) ──────────────────────────────
# extract sends the WHOLE page to the model, so it needs a big context + generous
# rate limit. the planner model can be too small. defaults to the vision model
# (gemini), independently overridable via EXTRACTION_PROVIDER / EXTRACTION_MODEL.
EXTRACTION_PROVIDER = os.getenv("EXTRACTION_PROVIDER", VISION_PROVIDER).lower()
EXTRACTION_MODEL = os.getenv("EXTRACTION_MODEL", VISION_MODEL)


def make_extraction_llm():
    return _build_llm(EXTRACTION_PROVIDER, EXTRACTION_MODEL)


def _parse_retry_seconds(msg: str) -> float | None:
    # pull a suggested wait out of a rate-limit message. handles groq's
    # 'try again in 5m55.968s', gemini's 'retry in 14s' and 'retryDelay: 16s'.
    import re
    m = re.search(r"in\s+(?:(\d+)m)?([\d.]+)\s*s", msg)
    if m:
        return float(m.group(1) or 0) * 60 + float(m.group(2))
    m = re.search(r"'retryDelay':\s*'(\d+)s'", msg)
    return float(m.group(1)) if m else None


async def _ainvoke_with_retry(llm, messages, output_format, attempts: int = 4):
    # call an llm for structured output. backs off on short (per-minute) rate
    # limits; on a long/daily cap it raises a clear error instead of a stack
    # trace. works across providers 
    for i in range(attempts):
        try:
            return await llm.ainvoke(messages, output_format=output_format)
        except (ModelRateLimitError, ModelProviderError) as e:
            msg = str(e)
            is_rate = (isinstance(e, ModelRateLimitError)
                       or "429" in msg or "rate_limit" in msg or "RESOURCE_EXHAUSTED" in msg)
            if not is_rate:
                raise
            wait = _parse_retry_seconds(msg)
            hard_cap = "limit: 0" in msg

            # a long wait also means a per-day / big-window cap; backing off won't help
            if hard_cap or wait is None or wait > 120 or i == attempts - 1:
                model = getattr(llm, "model", "the model")
                if hard_cap:
                    raise RuntimeError(
                        f"No quota for {model} on this key (free-tier limit is 0). "
                        f"Try a different model (e.g. GEMINI_MODEL=gemini-2.5-flash), a "
                        f"fresh key from https://aistudio.google.com/apikey, or set "
                        f"PLANNER_PROVIDER=ollama (local, unlimited) in .env."
                    ) from e
                hint = f" (resets in ~{wait / 60:.0f}m)" if wait else ""
                raise RuntimeError(
                    f"Rate/token limit hit on {model}{hint}. Likely the free daily "
                    f"budget is spent. Options: set PLANNER_PROVIDER=ollama (local, "
                    f"unlimited) in .env, switch GROQ_MODEL (e.g. openai/gpt-oss-20b), "
                    f"or wait for the daily reset."
                ) from e
            print(f"  rate-limited; waiting {wait:.0f}s then retrying…")
            await asyncio.sleep(wait + 1)
