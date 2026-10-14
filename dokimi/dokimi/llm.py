# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Thin LLM wrapper using litellm — one interface for all providers.

Usage:
    from dokimi.llm import llm_call
    text = llm_call("Your prompt", model="gpt-4o-mini", provider="openai")
"""
import os
import re
import time
import logging
from typing import Callable, Optional

from dokimi import caching
from dokimi.metrics.usage import UsageRecord

logger = logging.getLogger(__name__)

# Models for which litellm has no pricing — logged once each, then suppressed.
_COST_UNKNOWN_LOGGED: set = set()

# Backward-compatibility shims for existing tests/modules.
LANGCHAIN_AVAILABLE = True

# Errors that are worth retrying (rate limits, timeouts, transient network)
_RETRYABLE_SUBSTRINGS = (
    "rate_limit", "ratelimit", "rate limit",
    "timeout", "timed out",
    "connection", "server error",
    "overloaded",
    # Bedrock's transient 5xx arrives as ServiceUnavailableError with no status
    # code in the message ("The system encountered an unexpected error during
    # processing. Try your request again."). Matched on the exception class
    # name litellm puts in the message, not the bare word "unavailable", which
    # also appears in permanent errors ("model is unavailable in this region").
    "serviceunavailable", "service unavailable",
    "throttl",
    "try your request again",
    "internalserver", "internal server",
)

# Status codes only next to a status word: a bare "500" also matches
# "context length 1500 exceeded", which is permanent.
_RETRYABLE_STATUS = re.compile(r"\b(?:status|code|http|error)\W{0,3}(?:500|502|503|529)\b")

# litellm's typed exceptions are a more reliable signal than prose. Matched by
# class name so the check works whether or not litellm is importable here.
_RETRYABLE_EXC_NAMES = frozenset({
    "ratelimiterror",
    "serviceunavailableerror",
    "internalservererror",
    "timeout",
    "apiconnectionerror",
    "apierror",
})

# Checked before the substrings: these are permanent, and their messages can
# contain retryable-looking words ("connection" in an auth error's URL, say).
_NON_RETRYABLE_EXC_NAMES = frozenset({
    "authenticationerror",
    "permissiondeniederror",
    "notfounderror",
    "badrequesterror",
    "unsupportedparamserror",
    "contentpolicyviolationerror",
})


class _Resp:
    def __init__(self, content: str, usage=None):
        self.content = content
        self.usage = usage   # UsageRecord | None — lets callers (judge) track cost


class ChatOpenAI:
    """LangChain-compatible shim backed by llm_call."""

    def __init__(self, model: str, api_key: Optional[str] = None,
                 temperature: Optional[float] = None, seed: Optional[int] = None,
                 **kwargs):
        self.model = model
        self.api_key = api_key
        self.temperature = temperature
        self.seed = seed

    def invoke(self, prompt: str, draw: int = 0,
               cache_if: Optional[Callable[[str], bool]] = None):
        """Invoke the model with a prompt; response carries a UsageRecord."""
        text, usage = llm_call_with_usage(
            prompt, self.model, provider="openai", api_key=self.api_key,
            temperature=self.temperature, seed=self.seed,
            draw=draw, cache_if=cache_if,
        )
        return _Resp(text, usage=usage)


class ChatAnthropic:
    """LangChain-compatible shim backed by llm_call."""

    def __init__(self, model: str, api_key: Optional[str] = None,
                 temperature: Optional[float] = None, seed: Optional[int] = None,
                 **kwargs):
        self.model = model
        self.api_key = api_key
        self.temperature = temperature
        self.seed = seed

    def invoke(self, prompt: str, draw: int = 0,
               cache_if: Optional[Callable[[str], bool]] = None):
        """Invoke the model with a prompt; response carries a UsageRecord."""
        text, usage = llm_call_with_usage(
            prompt, self.model, provider="anthropic", api_key=self.api_key,
            temperature=self.temperature, seed=self.seed,
            draw=draw, cache_if=cache_if,
        )
        return _Resp(text, usage=usage)


def _is_retryable(exc: Exception) -> bool:
    """Return True if the exception is worth retrying.

    Exception type decides when it is recognised; prose is the fallback. A
    permanent failure (bad key, unsupported param) must stay non-retryable even
    if its message happens to contain a transient-sounding word, so the
    non-retryable set is consulted first.
    """
    name = type(exc).__name__.lower()
    if name in _NON_RETRYABLE_EXC_NAMES:
        return False
    if name in _RETRYABLE_EXC_NAMES:
        return True
    msg = str(exc).lower()
    if any(s in msg for s in ("authenticationerror", "unsupportedparamserror",
                              "permissiondenied", "notfounderror",
                              "badrequesterror")):
        return False
    return (any(s in msg for s in _RETRYABLE_SUBSTRINGS)
            or _RETRYABLE_STATUS.search(msg) is not None)


def _resolve_api_key(provider: str, api_key: Optional[str]) -> str:
    """Resolve API key from env vars or file: references."""
    if not api_key:
        env_map = {
            "anthropic":     "ANTHROPIC_API_KEY",
            "openai":        "OPENAI_API_KEY",
            "openrouter":    "OPENROUTER_API_KEY",
            "amazon-bedrock": "AWS_BEARER_TOKEN_BEDROCK",
            "google":        "GEMINI_API_KEY",
            "gemini":        "GEMINI_API_KEY",
        }
        api_key = os.environ.get(env_map.get(provider, "OPENAI_API_KEY"), "")
    if api_key and api_key.startswith("file:"):
        key_path = os.path.expanduser(api_key[len("file:"):])
        try:
            with open(key_path, encoding="utf-8") as _f:
                api_key = _f.read().strip()
        except OSError as exc:
            raise ValueError(f"Cannot read API key from file '{key_path}': {exc}") from exc
    return api_key


def unsupported_sampling_params(
    model: str, provider: str, temperature=None, seed=None
) -> list:
    """Names of requested sampling params this provider will reject.

    Bedrock, for one, accepts ``temperature`` but rejects ``seed`` outright with
    ``UnsupportedParamsError``. Callers use this to fail before a run rather
    than after: the per-answer exception handler turns such a rejection into a
    recorded ``generation_failed``, which yields a complete-looking report whose
    every score is null.

    Returns an empty list when the check cannot be performed, so an unknown
    model never blocks a run on this account.
    """
    requested = [n for n, v in (("temperature", temperature), ("seed", seed))
                 if v is not None]
    if not requested:
        return []
    try:
        from litellm import get_supported_openai_params

        supported = get_supported_openai_params(
            model=_build_litellm_model(model, provider)
        ) or []
    except Exception:
        return []
    if not supported:
        return []
    return [n for n in requested if n not in supported]


def _sampling_kwargs(
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    seed: Optional[int] = None,
) -> dict:
    """Request-shaping kwargs, omitting any that were not set.

    Omission matters: passing ``temperature=None`` explicitly is not the same as
    not passing it, and some providers reject a null. Callers that set nothing
    get exactly the request they got before these params existed.
    """
    out: dict = {}
    if max_tokens is not None:
        out["max_tokens"] = max_tokens
    if temperature is not None:
        out["temperature"] = temperature
    if seed is not None:
        out["seed"] = seed
    return out


def _build_litellm_model(model: str, provider: str) -> str:
    """Build litellm model string from model + provider."""
    if provider == "openrouter":
        return model if model.startswith("openrouter/") else f"openrouter/{model}"
    elif provider == "openai-codex":
        return f"openai/{model}"
    elif "/" in model:
        return model
    elif provider == "openai":
        return model
    elif provider in ("google", "gemini", "google-vertex"):
        return f"vertex_ai/{model}"
    elif provider == "amazon-bedrock":
        # Bedrock's openai.* model namespace only accepts invocations via the
        # Converse API; the plain bedrock/<model> route 404s on it (Anthropic
        # models on Bedrock work fine either way, so this is scoped narrowly).
        if "openai." in model:
            return f"bedrock/converse/{model}"
        return f"bedrock/{model}"
    else:
        return f"{provider}/{model}"


def _safe_completion_cost(resp, model: str, litellm_model: Optional[str] = None) -> Optional[float]:
    """Dollar cost of a completion via litellm, or ``None`` if no pricing.

    A missing price is logged once per model and returns ``None`` — distinct
    from a genuinely free ``0.0`` call.

    ``litellm_model`` is the provider-prefixed model string (e.g.
    ``vertex_ai/claude-3-5-haiku@20241022``). Passing it is required for
    providers whose response ``model`` field is bare (Vertex AI returns
    ``claude-3-5-haiku@...`` with no ``vertex_ai/`` prefix), which otherwise
    makes ``completion_cost`` fail with "LLM Provider NOT provided" → null cost.
    """
    try:
        from litellm import completion_cost

        kwargs = {"completion_response": resp}
        if litellm_model:
            kwargs["model"] = litellm_model
        cost = completion_cost(**kwargs)
        if cost is None:
            raise ValueError("completion_cost returned None")
        return float(cost)
    except Exception as exc:
        if model not in _COST_UNKNOWN_LOGGED:
            _COST_UNKNOWN_LOGGED.add(model)
            logger.info("No litellm pricing for model %r; cost_usd will be null (%s)", model, exc)
        return None


def _extract_usage(
    resp,
    model: str,
    provider: str,
    latency_sec: float = 0.0,
    ttft_sec: Optional[float] = None,
    litellm_model: Optional[str] = None,
) -> UsageRecord:
    """Build a :class:`UsageRecord` from a litellm response (defensive).

    Pulls tokens, cache tokens (Anthropic ``cache_creation_input_tokens`` /
    ``cache_read_input_tokens``; OpenAI ``prompt_tokens_details.cached_tokens``),
    and reasoning tokens (``completion_tokens_details.reasoning_tokens``) where
    the provider exposes them; every field defaults to 0 otherwise.
    """
    prompt_tokens = completion_tokens = total_tokens = 0
    reasoning_tokens = cache_read = cache_write = 0

    u = getattr(resp, "usage", None)
    if u:
        prompt_tokens = getattr(u, "prompt_tokens", 0) or 0
        completion_tokens = getattr(u, "completion_tokens", 0) or 0
        total_tokens = getattr(u, "total_tokens", 0) or 0
        # Anthropic-style cache fields live on usage directly.
        cache_write = getattr(u, "cache_creation_input_tokens", 0) or 0
        cache_read = getattr(u, "cache_read_input_tokens", 0) or 0
        # OpenAI-style cached tokens live under prompt_tokens_details.
        ptd = getattr(u, "prompt_tokens_details", None)
        if ptd is not None and not cache_read:
            cache_read = (getattr(ptd, "cached_tokens", None)
                          or (ptd.get("cached_tokens") if isinstance(ptd, dict) else 0)
                          or 0)
        ctd = getattr(u, "completion_tokens_details", None)
        if ctd is not None:
            reasoning_tokens = (getattr(ctd, "reasoning_tokens", None)
                                or (ctd.get("reasoning_tokens") if isinstance(ctd, dict) else 0)
                                or 0)

    # Resolve the provider-prefixed model so cost works even when a caller omits
    # litellm_model (e.g. agent_runner) — Vertex/Bedrock return a bare
    # response.model that otherwise makes completion_cost emit null.
    resolved_litellm_model = litellm_model or _build_litellm_model(model, provider)
    cost_usd = _safe_completion_cost(resp, model, litellm_model=resolved_litellm_model)
    return UsageRecord(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        reasoning_tokens=reasoning_tokens,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
        cost_usd=cost_usd,
        latency_sec=latency_sec,
        ttft_sec=ttft_sec,
        model=model,
        provider=provider,
        n_calls=1,
        cost_known_calls=1 if cost_usd is not None else 0,
    )


def llm_call(
    prompt: str,
    model: str,
    provider: str = "openai",
    api_key: Optional[str] = None,
    max_retries: int = 3,
    retry_delay: float = 2.0,
    system: Optional[str] = None,
    cache_prefix: Optional[str] = None,
    temperature: Optional[float] = None,
    seed: Optional[int] = None,
    draw: int = 0,
    cache_if: Optional[Callable[[str], bool]] = None,
) -> str:
    """Call any LLM via litellm with automatic retry on transient errors.

    Args:
        prompt: User prompt string.
        model: Model name (e.g. "gpt-4o-mini", "claude-3-5-sonnet-20241022").
        provider: Provider name — "openai", "anthropic", "openrouter", "bedrock".
        api_key: Optional API key (falls back to env var).
        max_retries: Max retry attempts on transient errors (default 3).
        retry_delay: Initial delay in seconds; doubles each attempt (exponential backoff).
        system: Optional system prompt. When provided it is sent as a leading
            ``system`` message — used to vary the answering agent's persona.
        cache_prefix: Stable text preceding ``prompt``; see
            :func:`llm_call_with_usage`.
        temperature: Optional sampling temperature. Omitted when None, so the
            provider default applies and request shape is unchanged.
        seed: Optional sampling seed, for providers that honour one.
        draw: Independent-sample index; see :func:`llm_call_with_usage`.
        cache_if: Response-cache acceptance test; see :func:`llm_call_with_usage`.

    Returns:
        Model response as a plain string.
    """
    text, _ = llm_call_with_usage(
        prompt=prompt, model=model, provider=provider,
        api_key=api_key, max_retries=max_retries, retry_delay=retry_delay,
        system=system, cache_prefix=cache_prefix,
        temperature=temperature, seed=seed, draw=draw, cache_if=cache_if,
    )
    return text


def llm_call_with_usage(
    prompt: str,
    model: str,
    provider: str = "openai",
    api_key: Optional[str] = None,
    max_retries: int = 3,
    retry_delay: float = 2.0,
    system: Optional[str] = None,
    stream: bool = False,
    max_tokens: Optional[int] = None,
    cache_prefix: Optional[str] = None,
    temperature: Optional[float] = None,
    seed: Optional[int] = None,
    draw: int = 0,
    cache_if: Optional[Callable[[str], bool]] = None,
) -> "tuple[str, UsageRecord]":
    """Like llm_call, but returns ``(text, UsageRecord)``.

    The :class:`~dokimi.metrics.usage.UsageRecord` carries tokens,
    cache read/write tokens, dollar cost (``None`` when litellm has no pricing),
    per-call latency, and — when ``stream=True`` — time-to-first-token. All
    fields default to 0 / None when the provider does not expose them.

    Args:
        system: Optional system prompt sent as a leading ``system`` message.
        stream: When True, stream the response and measure ``ttft_sec`` (time to
            the first content chunk). Default off — the non-streaming path keeps
            the well-tested retry/usage behavior; TTFT is opt-in for
            latency-sensitive sweeps.
        max_tokens: Optional output-token ceiling. Reasoning models can spend the
            provider's default budget entirely on internal reasoning and return
            empty content, so callers benchmarking them should raise this.
        cache_prefix: Stable leading text (instructions, injected docs, a skill
            document) that repeats across calls. It is concatenated with
            ``prompt`` verbatim, and on Anthropic-family models it gets an
            explicit cache breakpoint when long enough to be worth one — see
            :mod:`dokimi.caching`. Passing the whole prompt as
            ``prompt`` still works and simply never caches.
        temperature: Optional sampling temperature. Sampling is non-deterministic
            by default, which shows up as run-to-run score variance; pin this to
            reduce it. Omitted when None so the request shape does not change.
        seed: Optional sampling seed, for providers that honour one. Best-effort
            reproducibility — not every provider guarantees it.
        draw: Index of an independent sample of the same request. Draw 0 is
            the ordinary call; a caller that wants a *second opinion* on a
            byte-identical prompt (a judge retry after an unparseable reply, a
            repeated measurement) passes a new index, which gets its own
            response-cache entry instead of replaying the first one.
        cache_if: Optional acceptance test for the response cache: a reply it
            rejects is neither stored nor replayed (see :func:`caching.keeps`).

    Returns:
        (response_text: str, usage: UsageRecord)
    """
    from litellm import completion

    api_key = _resolve_api_key(provider, api_key)
    litellm_model = _build_litellm_model(model, provider)

    messages, _cached_prefix = caching.build_messages(
        prompt, system=system, cache_prefix=cache_prefix,
        model=model, provider=provider,
    )

    # max_tokens belongs in the key: it changes the response (a truncated or
    # reasoning-starved completion is a different answer), so replaying a cache
    # entry recorded under a different ceiling would silently mix the two.
    # temperature/seed belong in the key: a pinned run must not be served a
    # response that was cached under free sampling. Added only when set, so
    # entries cached before they existed still hit.
    cache_key = caching.response_cache_key(
        litellm_model=litellm_model,
        messages=messages,
        extra=caching.draw_extra(
            {"stream": stream, "max_tokens": max_tokens,
             **_sampling_kwargs(temperature=temperature, seed=seed)},
            draw,
        ),
    )
    hit = caching.load_response(cache_key)
    if hit is not None and caching.keeps(cache_if, hit[0]):
        return hit

    last_exc: Optional[Exception] = None
    delay = retry_delay

    for attempt in range(max_retries + 1):
        try:
            if stream:
                text, usage = _stream_call(completion, litellm_model, messages, api_key,
                                           model, provider, max_tokens=max_tokens,
                                           temperature=temperature, seed=seed)
                if caching.keeps(cache_if, text):
                    caching.store_response(cache_key, text, usage)
                return text, usage

            t0 = time.time()
            extra = _sampling_kwargs(max_tokens=max_tokens,
                                     temperature=temperature, seed=seed)
            resp = completion(
                model=litellm_model,
                messages=messages,
                api_key=api_key or None,
                **extra,
            )
            latency = time.time() - t0
            text = resp.choices[0].message.content
            usage = _extract_usage(resp, model, provider, latency_sec=latency,
                                   litellm_model=litellm_model)
            if caching.keeps(cache_if, text):
                caching.store_response(cache_key, text, usage)
            return text, usage

        except Exception as exc:
            last_exc = exc
            if attempt < max_retries and _is_retryable(exc):
                logger.warning(
                    f"LLM call failed (attempt {attempt+1}/{max_retries+1}), "
                    f"retrying in {delay:.0f}s: {exc}"
                )
                time.sleep(delay)
                delay *= 2
            else:
                raise

    raise last_exc  # should never reach here


def _stream_call(completion, litellm_model, messages, api_key, model, provider,
                 max_tokens: Optional[int] = None, temperature: Optional[float] = None,
                 seed: Optional[int] = None):
    """Streaming variant of a single completion that also measures TTFT.

    Requests usage in the terminal chunk (``stream_options``) and stamps
    ``ttft_sec`` at the first content delta. Falls back to ``None`` usage fields
    if the provider omits the usage chunk.
    """
    t0 = time.time()
    ttft: Optional[float] = None
    parts: list = []
    final_chunk = None

    stream_resp = completion(
        model=litellm_model,
        messages=messages,
        api_key=api_key or None,
        stream=True,
        stream_options={"include_usage": True},
        **_sampling_kwargs(max_tokens=max_tokens or None, temperature=temperature, seed=seed),
    )
    for chunk in stream_resp:
        final_chunk = chunk
        try:
            delta = chunk.choices[0].delta
            piece = getattr(delta, "content", None)
        except (AttributeError, IndexError):
            piece = None
        if piece:
            if ttft is None:
                ttft = time.time() - t0
            parts.append(piece)

    latency = time.time() - t0
    text = "".join(parts)
    # The terminal chunk carries usage when include_usage is honored. Some
    # providers/proxies ignore it — warn so all-zero tokens are not mistaken
    # for a genuinely empty call.
    if final_chunk is None or not getattr(final_chunk, "usage", None):
        logger.warning(
            "Streaming response for %r returned no usage chunk; token/cost "
            "metrics will be zero/None for this call.", model
        )
    usage = _extract_usage(final_chunk, model, provider, latency_sec=latency, ttft_sec=ttft,
                           litellm_model=litellm_model)
    return text, usage


def chat_completion(
    messages: list,
    model: str,
    provider: str = "openai",
    tools: Optional[list] = None,
    tool_choice: Optional[str] = None,
    api_key: Optional[str] = None,
    max_retries: int = 3,
    retry_delay: float = 2.0,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    seed: Optional[int] = None,
):
    """Multi-turn completion with optional tool-calling.

    Unlike :func:`llm_call_with_usage` (single user prompt), this accepts a full
    message list and optional ``tools`` schemas, returning the raw litellm
    response so callers can inspect ``choices[0].message.tool_calls``. Used by
    the agentic treatment runner.

    A long system prompt gets a provider cache breakpoint
    (:func:`caching.annotate_system_breakpoint`), because an agent loop re-sends
    the whole conversation every turn and would otherwise pay full input price
    for the same skill body N times over. The **response** cache is deliberately
    not used here: replaying a stored completion would skip the tool calls the
    loop is measuring, so a hit would be a different run, not a cheaper one.

    Args:
        messages: OpenAI-style message dicts.
        tools: Optional list of tool schemas (OpenAI/litellm format).
        tool_choice: Optional tool-choice directive (e.g. ``"auto"``).
        max_tokens: Optional output-token ceiling; see
            :func:`llm_call_with_usage`.
        temperature: Optional sampling temperature; see
            :func:`llm_call_with_usage`.
        seed: Optional sampling seed; see :func:`llm_call_with_usage`.

    Returns:
        The litellm completion response object.
    """
    from litellm import completion

    api_key = _resolve_api_key(provider, api_key)
    litellm_model = _build_litellm_model(model, provider)
    messages, _cached = caching.annotate_system_breakpoint(
        messages, model=model, provider=provider
    )

    kwargs = _sampling_kwargs(max_tokens=max_tokens,
                              temperature=temperature, seed=seed)
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = tool_choice or "auto"

    last_exc: Optional[Exception] = None
    delay = retry_delay
    for attempt in range(max_retries + 1):
        try:
            return completion(
                model=litellm_model,
                messages=messages,
                api_key=api_key or None,
                **kwargs,
            )
        except Exception as exc:
            last_exc = exc
            if attempt < max_retries and _is_retryable(exc):
                logger.warning(
                    f"chat_completion failed (attempt {attempt+1}/{max_retries+1}), "
                    f"retrying in {delay:.0f}s: {exc}"
                )
                time.sleep(delay)
                delay *= 2
            else:
                raise
    raise last_exc  # pragma: no cover


# ── Robust JSON extraction ─────────────────────────────────────────────────────

import re as _re

def extract_json_object(text: str) -> dict:
    """Extract a JSON object from LLM output, handling markdown fences and leading text."""
    text = (text or "").strip()
    if not text:
        raise ValueError("LLM returned empty response")
    # Direct parse
    try:
        return __import__("json").loads(text)
    except Exception:
        pass
    # Markdown fence
    fence = _re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        try:
            return __import__("json").loads(fence.group(1).strip())
        except Exception:
            pass
    # First { ... } block
    m = _re.search(r"\{[\s\S]*\}", text)
    if m:
        return __import__("json").loads(m.group(0))
    raise ValueError(f"No JSON object found in LLM response: {text[:200]!r}")


def extract_json_array(text: str) -> list:
    """Extract a JSON array from LLM output, handling markdown fences and leading text."""
    text = (text or "").strip()
    if not text:
        raise ValueError("LLM returned empty response")
    # Direct parse
    try:
        result = __import__("json").loads(text)
        if isinstance(result, list):
            return result
    except Exception:
        pass
    # Markdown fence
    fence = _re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        try:
            result = __import__("json").loads(fence.group(1).strip())
            if isinstance(result, list):
                return result
        except Exception:
            pass
    # First [ ... ] block
    m = _re.search(r"\[[\s\S]*\]", text)
    if m:
        result = __import__("json").loads(m.group(0))
        if isinstance(result, list):
            return result
    raise ValueError(f"No JSON array found in LLM response: {text[:200]!r}")
