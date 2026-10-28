# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The entry point for LLM calls in the framework.

``llm_call``, ``chat_completion``, the shims and ``utils.get_llm`` already delegate
here. Not yet moved: ``utils.get_llm``'s route and key table,
``commands/benchmark.py``'s ``_credential_env_var``, ``eval/ragas_eval.py``'s
``_resolve_key``, and the persona and validator redraw loops.

Interface
---------
:class:`LLM` is one model at one provider::

    llm = LLM("claude-sonnet-5-5", provider="amazon-bedrock", temperature=0.0)
    reply = llm.complete(prompt, system=..., cache_prefix=..., draw=0, cache_if=...)
    reply.text, reply.usage, reply.finish_reason, reply.truncated
    llm.ask(prompt, draw=1)          # the reply text only (the ``Ask`` shape)
    llm.chat(messages, tools=...)    # multi-turn tool calling; never response-cached
    llm.metrics()                    # thread-safe UsageRecord total over this object's replies

and :func:`ask_parsed` is the one "redraw until it parses, cache only parsed
replies" loop, for any ``ask(prompt, draw=, cache_if=) -> str`` callable.

What a caller can rely on:

- **Providers.** :data:`PROVIDERS` (canonical name -> key variable) plus
  :data:`PROVIDER_ALIASES`. :func:`resolve_model` is this module's model-name map and
  :func:`api_key_env` its key-variable map. A provider outside the table is
  passed to litellm as its own route prefix (``azure/<model>``), reading
  ``OPENAI_API_KEY``. A provider in :data:`dokimi.llm_cli.BACKENDS` (``claude-cli``)
  is a logged-in coding-agent CLI, not an API: no key, no tools, no sampling
  control (:mod:`dokimi.llm_cli`). An explicit ``api_key`` wins over the environment; either
  may be ``file:<path>``, and an unreadable file raises ``ValueError``.
- **Bedrock bare names.** A name in :data:`dokimi.defaults.BEDROCK_MODEL_IDS`
  runs under its inference profile (:func:`dokimi.defaults.bedrock_profile`);
  ``openai.*`` models take the Converse route.
- **Sampling.** ``max_tokens``, ``temperature`` and ``seed`` are sent only when
  set. :func:`unsupported_sampling_params` names the ones a provider would
  reject, so a run can refuse before it pays.
- **Retry.** Transient errors (:func:`is_retryable`) are retried
  ``max_retries`` times with doubling backoff; anything else is raised on the
  first attempt.
- **Response cache** (:mod:`dokimi.caching`, off unless
  ``DOKIMI_RESPONSE_CACHE`` is set). ``complete`` is cached, ``chat`` never is:
  replaying a completion would skip the tool calls an agent loop measures. The
  key holds the litellm model, the messages, ``stream`` and ``max_tokens``, and
  ``temperature``/``seed`` when set; ``draw=n`` is an independent sample with its
  own entry (draw 0 adds nothing). ``cache_if(text)`` decides whether a reply may
  be stored *or replayed*. A replay returns the stored usage record.
- **Usage.** Every reply carries a :class:`UsageRecord`; ``cost_usd`` is ``None``
  when litellm has no price for the model, never a fake ``0.0``.
- **Truncation.** A live ``complete``/``ask`` reply cut at the output ceiling
  (``reply.truncated``) is logged as a warning; a replay is not logged again.
  ``chat`` does not log it.

Seam
----
Behind the interface sits one :data:`Adapter`, ``(Request) -> Reply``: a single
provider request with no retry, cache or metering. :class:`LiteLLMAdapter` is
production; :class:`FakeAdapter` answers from a script and records every
request, for tests (the ``fake_llm`` fixture). :func:`use_adapter` swaps the
process default; ``LLM(..., adapter=...)`` overrides it for one object.

``llm_call``, ``llm_call_with_usage``, ``chat_completion``, the
``ChatOpenAI``/``ChatAnthropic`` shims and ``utils.get_llm`` are delegates to
:class:`LLM` until their callers move.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, Iterator, List, Optional, Protocol, Tuple, TypeVar, cast

from dokimi import caching, llm_cli
from dokimi.defaults import bedrock_profile
from dokimi.llm_json import extract_json_array as extract_json_array
from dokimi.llm_json import extract_json_object as extract_json_object
from dokimi.metrics.usage import UsageRecord

logger = logging.getLogger(__name__)

T = TypeVar("T")

# ── Providers ─────────────────────────────────────────────────────────────────

#: Canonical provider names, as the CLI offers them, and the variable each key is read from.
PROVIDERS: Dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "openai-codex": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "amazon-bedrock": "AWS_BEARER_TOKEN_BEDROCK",
    "google-vertex": "GEMINI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}

#: ``--provider`` choices: the API providers, then the CLI backends.
ANSWER_PROVIDERS: Tuple[str, ...] = (*PROVIDERS, *llm_cli.BACKENDS)

#: Other spellings of a canonical provider.
PROVIDER_ALIASES: Dict[str, str] = {
    "google": "google-vertex",
    "gemini": "google-vertex",
}


def _canonical(provider: str) -> str:
    return PROVIDER_ALIASES.get(provider, provider)


def api_key_env(provider: str) -> str:
    """The environment variable holding *provider*'s key (``OPENAI_API_KEY`` off the table)."""
    return PROVIDERS.get(_canonical(provider), "OPENAI_API_KEY")


def resolve_api_key(provider: str, api_key: Optional[str] = None) -> Optional[str]:
    """*api_key*, else the provider's environment variable; a ``file:<path>`` value is read.

    Returns ``None`` when there is no key; a CLI provider takes its login, never a key.

    Raises:
        ValueError: a ``file:`` key is unreadable.
    """
    key = "" if llm_cli.backend(provider) else (api_key or os.environ.get(api_key_env(provider), ""))
    if key.startswith("file:"):
        path = os.path.expanduser(key[len("file:"):])
        try:
            with open(path, encoding="utf-8") as fh:
                key = fh.read().strip()
        except OSError as exc:
            raise ValueError(f"Cannot read API key from file '{path}': {exc}") from exc
    return key or None


def resolve_model(model: str, provider: str) -> str:
    """The litellm model string for *model* at *provider*.

    A name that already carries a litellm route (contains ``/``) passes through,
    except on OpenRouter and openai-codex, which add their own prefix.
    """
    name = _canonical(provider)
    if name == "openrouter":
        return model if model.startswith("openrouter/") else f"openrouter/{model}"
    if name == "openai-codex":
        return f"openai/{model}"
    if "/" in model:
        return model
    if name == "openai":
        return model
    if name == "google-vertex":
        return f"vertex_ai/{model}"
    if name == "amazon-bedrock":
        # Bedrock's openai.* model namespace only accepts invocations via the
        # Converse API; the plain bedrock/<model> route 404s on it (Anthropic
        # models on Bedrock work fine either way, so this is scoped narrowly).
        if "openai." in model:
            return f"bedrock/converse/{model}"
        return f"bedrock/{bedrock_profile(model) or model}"
    # anthropic, and a provider off the table, are their own litellm route.
    return f"{provider}/{model}"


def _sampling_kwargs(
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    seed: Optional[int] = None,
) -> Dict[str, Any]:
    """Request-shaping kwargs, omitting any that were not set.

    Omission matters: passing ``temperature=None`` explicitly is not the same as
    not passing it, and some providers reject a null. Callers that set nothing
    get exactly the request they got before these params existed.
    """
    out: Dict[str, Any] = {}
    if max_tokens is not None:
        out["max_tokens"] = max_tokens
    if temperature is not None:
        out["temperature"] = temperature
    if seed is not None:
        out["seed"] = seed
    return out


def unsupported_sampling_params(
    model: str, provider: str, temperature: Optional[float] = None, seed: Optional[int] = None,
) -> List[str]:
    """Names of requested sampling params this provider will reject.

    Bedrock, for one, accepts ``temperature`` but rejects ``seed`` outright with
    ``UnsupportedParamsError``. Callers use this to fail before a run rather
    than after: the per-answer exception handler turns such a rejection into a
    recorded ``generation_failed``, which yields a complete-looking report whose
    every score is null.

    Returns an empty list when the check cannot be performed, so an unknown
    model never blocks a run on this account.
    """
    requested = list(_sampling_kwargs(temperature=temperature, seed=seed))
    if not requested:
        return []
    cli = llm_cli.backend(_canonical(provider))
    if cli is not None:
        return [n for n in requested if n not in cli.supports]
    try:
        from litellm import get_supported_openai_params

        supported = get_supported_openai_params(model=resolve_model(model, provider)) or []
    except Exception:
        return []
    if not supported:
        return []
    return [n for n in requested if n not in supported]


# ── Retry ─────────────────────────────────────────────────────────────────────

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


def is_retryable(exc: BaseException) -> bool:
    """Whether *exc* is a transient provider error worth another attempt.

    Exception type decides when it is recognised; prose is the fallback. A
    permanent failure (bad key, unsupported param) must stay non-retryable even
    if its message happens to contain a transient-sounding word, so the
    non-retryable set is consulted first.
    """
    flag = getattr(exc, "retryable", None)  # set by an error that knows (llm_cli.CliCallError)
    if isinstance(flag, bool):
        return flag
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


# ── Usage and cost ────────────────────────────────────────────────────────────

# Models for which litellm has no pricing — logged once each, then suppressed.
_COST_UNKNOWN_LOGGED: set = set()


def _has_price_entry(litellm_model: str, model: str) -> bool:
    """Whether litellm's price map has an entry for the routed name, the name without its
    route prefixes (``bedrock/converse/openai.x`` -> ``openai.x``) or the bare model name."""
    from litellm import model_cost

    candidates = {litellm_model, model, *litellm_model.split("/", 1)[1:],
                  litellm_model.rsplit("/", 1)[-1]}
    return any(c in model_cost for c in candidates)


def cost_per_token(**kwargs: Any) -> tuple[float, float]:  # litellm's, for llm_pricing
    from litellm import cost_per_token as price
    return price(**kwargs)


def _safe_completion_cost(resp: Any, model: str, litellm_model: Optional[str] = None) -> Optional[float]:
    """Dollar cost of a completion via litellm, or ``None`` if no pricing.

    A missing price is logged once per model and returns ``None`` — distinct
    from a genuinely free ``0.0`` call. A ``0.0`` for a model that is not in
    litellm's price map is a missing price too, not a free call.

    ``litellm_model`` is the provider-prefixed model string (e.g.
    ``vertex_ai/claude-3-5-haiku@20241022``). Passing it is required for
    providers whose response ``model`` field is bare (Vertex AI returns
    ``claude-3-5-haiku@...`` with no ``vertex_ai/`` prefix), which otherwise
    makes ``completion_cost`` fail with "LLM Provider NOT provided" → null cost.
    """
    try:
        from litellm import completion_cost

        kwargs: Dict[str, Any] = {"completion_response": resp}
        if litellm_model:
            kwargs["model"] = litellm_model
        cost = completion_cost(**kwargs)
        if cost is None:
            raise ValueError("completion_cost returned None")
        cost = float(cost)
        # litellm prices a model it has no entry for at 0.0 when the provider
        # route is known (claude-sonnet-5-5 on litellm 1.104), which would read
        # as a valid $0 total. Only a model in the price map can cost $0.
        if cost == 0.0 and not _has_price_entry(litellm_model or model, model):
            raise ValueError("model is not in litellm's price map")
        return cost
    except Exception as exc:
        if model not in _COST_UNKNOWN_LOGGED:
            _COST_UNKNOWN_LOGGED.add(model)
            logger.info("No litellm pricing for model %r; cost_usd will be null (%s)", model, exc)
        return None


def _extract_usage(
    resp: Any,
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
    resolved_litellm_model = litellm_model or resolve_model(model, provider)
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


# ── The seam: one provider request ────────────────────────────────────────────

# finish_reason values that mean "cut at the output ceiling": litellm normalizes to
# "length"; some provider passthroughs keep Anthropic's "max_tokens".
_TRUNCATED = frozenset({"length", "max_tokens"})


@dataclass(frozen=True)
class Request:
    """One provider request, fully resolved: what an :data:`Adapter` receives."""

    model: str  # recorded on the reply's usage
    provider: str  # recorded on the reply's usage
    litellm_model: str
    messages: List[Dict[str, Any]]
    api_key: Optional[str] = None
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
    seed: Optional[int] = None
    stream: bool = False
    tools: Optional[List[Dict[str, Any]]] = None
    tool_choice: Optional[str] = None


@dataclass(frozen=True)
class ToolCall:
    """One tool call the model asked for; ``arguments`` is the raw JSON string."""

    id: Optional[str]
    name: Optional[str]
    arguments: str = "{}"


@dataclass(frozen=True)
class Reply:
    """A model reply. ``text`` is ``None`` when the provider returned no content.

    ``response`` is the provider's own response object when there is one (not on a
    cache replay); ``chat_completion`` hands it to the callers that read it.
    """

    text: Optional[str]
    usage: UsageRecord
    finish_reason: Optional[str] = None
    tool_calls: Tuple[ToolCall, ...] = ()
    response: Any = field(default=None, compare=False, repr=False)
    #: The model the provider resolved to, when it says (a CLI names the version behind an alias).
    model_id: Optional[str] = None

    @property
    def truncated(self) -> bool:
        """Whether the reply was cut at the output ceiling."""
        return self.finish_reason in _TRUNCATED


#: One provider request, no retry/cache/metering: ``(Request) -> Reply``.
Adapter = Callable[[Request], Reply]


def _tool_calls(message: Any) -> Tuple[ToolCall, ...]:
    """Normalize a litellm message's tool calls (objects or dicts)."""
    calls = getattr(message, "tool_calls", None)
    out = []
    for tc in calls or ():
        if isinstance(tc, dict):
            fn = tc.get("function") or {}
            out.append(ToolCall(tc.get("id"), fn.get("name"), fn.get("arguments") or "{}"))
        else:
            fn = getattr(tc, "function", None)
            out.append(ToolCall(getattr(tc, "id", None), getattr(fn, "name", None),
                                getattr(fn, "arguments", None) or "{}"))
    return tuple(out)


def _stream(completion: Any, request: Request) -> Reply:
    """Streaming variant of a single completion that also measures TTFT.

    Requests usage in the terminal chunk (``stream_options``) and stamps
    ``ttft_sec`` at the first content delta. Falls back to empty usage fields
    if the provider omits the usage chunk.
    """
    t0 = time.time()
    ttft: Optional[float] = None
    parts: List[str] = []
    final_chunk = None

    stream_resp = completion(
        model=request.litellm_model,
        messages=request.messages,
        api_key=request.api_key,
        stream=True,
        stream_options={"include_usage": True},
        **_sampling_kwargs(max_tokens=request.max_tokens or None,
                           temperature=request.temperature, seed=request.seed),
    )
    finish_reason = None
    for chunk in stream_resp:
        final_chunk = chunk
        try:
            delta = chunk.choices[0].delta
            piece = getattr(delta, "content", None)
            finish_reason = getattr(chunk.choices[0], "finish_reason", None) or finish_reason
        except (AttributeError, IndexError):
            piece = None
        if piece:
            if ttft is None:
                ttft = time.time() - t0
            parts.append(piece)

    latency = time.time() - t0
    # The terminal chunk carries usage when include_usage is honored. Some
    # providers/proxies ignore it — warn so all-zero tokens are not mistaken
    # for a genuinely empty call.
    if final_chunk is None or not getattr(final_chunk, "usage", None):
        logger.warning(
            "Streaming response for %r returned no usage chunk; token/cost "
            "metrics will be zero/None for this call.", request.model
        )
    usage = _extract_usage(final_chunk, request.model, request.provider, latency_sec=latency,
                           ttft_sec=ttft, litellm_model=request.litellm_model)
    return Reply(text="".join(parts), usage=usage, finish_reason=finish_reason)


def _cli_reply(request: Request) -> Reply:
    """A request for a CLI provider (:mod:`dokimi.llm_cli`): one prompt, an optional system."""
    turns = [m for m in request.messages if m.get("role") != "system"]
    if request.tools or len(turns) != 1 or request.temperature is not None or request.seed is not None:
        raise llm_cli.CliCallError(f"{request.provider} takes one prompt: no tools, no conversation, "
                                   "no temperature or seed", retryable=False)
    system = [prompt_text(replace(request, messages=[m]))
              for m in request.messages if m.get("role") == "system"]
    out = llm_cli.call(request.provider, model=request.model, prompt=prompt_text(request),
                       system="\n\n".join(system) or None)
    return Reply(text=out.text, usage=out.usage, finish_reason=out.finish_reason, model_id=out.model_id)


class LiteLLMAdapter:
    """The production adapter: one ``litellm.completion`` call, priced by litellm.

    A CLI provider (:data:`dokimi.llm_cli.BACKENDS`) goes to its CLI instead.
    """

    def __call__(self, request: Request) -> Reply:
        if llm_cli.backend(request.provider) is not None:
            return _cli_reply(request)
        from litellm import completion

        if request.stream:
            return _stream(completion, request)
        kwargs = _sampling_kwargs(max_tokens=request.max_tokens,
                                  temperature=request.temperature, seed=request.seed)
        if request.tools:
            kwargs["tools"] = request.tools
            kwargs["tool_choice"] = request.tool_choice or "auto"
        t0 = time.time()
        resp = completion(
            model=request.litellm_model,
            messages=request.messages,
            api_key=request.api_key,
            **kwargs,
        )
        latency = time.time() - t0
        # No choices (a billed 200 some routes return) is not an error here:
        # chat_completion hands the raw response on, and its callers price it.
        choices = getattr(resp, "choices", None) or ()
        choice = choices[0] if choices else None
        message = getattr(choice, "message", None)
        return Reply(
            text=getattr(message, "content", None),
            usage=_extract_usage(resp, request.model, request.provider, latency_sec=latency,
                                 litellm_model=request.litellm_model),
            finish_reason=getattr(choice, "finish_reason", None),
            tool_calls=_tool_calls(message),
            response=resp,
        )


class FakeAdapter:
    """A scripted provider for tests; records every :class:`Request` it gets.

    Scripted items are consumed in order: a ``str`` (or ``None``) is the reply
    text, a :class:`Reply` is returned as-is, an exception is raised (to exercise
    retry). Once the script is empty, ``respond(request)`` supplies the item;
    with no ``respond`` an unscripted call fails the test. ``usage`` is the record
    every text reply carries, stamped with the request's model and provider.
    """

    def __init__(self, *script: Any, respond: Optional[Callable[[Request], Any]] = None,
                 usage: Optional[UsageRecord] = None):
        self.script: List[Any] = list(script)
        self.respond = respond
        self.usage = usage or UsageRecord(prompt_tokens=10, completion_tokens=5, total_tokens=15)
        self.requests: List[Request] = []
        self._lock = threading.Lock()

    def push(self, *items: Any) -> "FakeAdapter":
        """Append replies to the script."""
        with self._lock:
            self.script.extend(items)
        return self

    def __call__(self, request: Request) -> Reply:
        with self._lock:
            self.requests.append(request)
            scripted = bool(self.script)
            item = self.script.pop(0) if scripted else None
        if not scripted:
            if self.respond is None:
                raise AssertionError(f"FakeAdapter: unscripted call to {request.litellm_model}")
            item = self.respond(request)
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, Reply):
            return item
        usage = replace(self.usage, model=request.model, provider=request.provider)
        return Reply(text=item, usage=usage, finish_reason="stop")

    @property
    def prompts(self) -> List[str]:
        """The text of each request's last message, in call order."""
        return [prompt_text(r) for r in self.requests]


def prompt_text(request: Request) -> str:
    """The text of *request*'s last message, cache-breakpoint blocks joined."""
    if not request.messages:
        return ""
    content = request.messages[-1].get("content")
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content if isinstance(b, dict))
    return content or ""


_default_adapter: Adapter = LiteLLMAdapter()


@contextlib.contextmanager
def use_adapter(adapter: Adapter) -> Iterator[Adapter]:
    """Route every :class:`LLM` without an explicit adapter through *adapter*."""
    global _default_adapter
    previous, _default_adapter = _default_adapter, adapter
    try:
        yield adapter
    finally:
        _default_adapter = previous


# ── The module's interface ────────────────────────────────────────────────────


def _warn_if_truncated(reply: Reply, model: str, max_tokens: Optional[int]) -> None:
    """Log a reply cut at the output ceiling, on every live call (streamed or not).

    A cut reply looks like an ordinary one to its caller: a JSON one usually fails to
    parse, a prose one is just short. A response-cache replay does not log it again; the
    warning went out when the reply was stored, and callers that pass ``cache_if`` keep
    an unparseable cut reply out of the cache altogether.
    """
    if reply.truncated:
        logger.warning(
            f"LLM reply hit the output ceiling ({model}, max_tokens="
            f"{max_tokens or 'provider default'}, "
            f"{reply.usage.completion_tokens} tokens); "
            "raise max_tokens if this repeats"
        )


@dataclass(eq=False)
class LLM:
    """One model at one provider: completions, retry, response cache, usage totals.

    Construction does no I/O. ``max_tokens`` is the output ceiling for every call
    (``None``: the provider's own); :meth:`complete` may override it per call.
    ``temperature`` and ``seed`` apply to every call. The key is resolved per call,
    so an unreadable ``file:`` key raises there.
    """

    model: str
    provider: str = "openai"
    api_key: Optional[str] = field(default=None, repr=False)
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
    seed: Optional[int] = None
    adapter: Optional[Adapter] = field(default=None, repr=False)
    max_retries: int = 3
    retry_delay: float = 2.0
    litellm_model: str = field(init=False)
    _lock: Any = field(init=False, repr=False, default_factory=threading.Lock)
    _total: UsageRecord = field(init=False, repr=False,
                                default_factory=lambda: UsageRecord(n_calls=0, role=""))

    def __post_init__(self) -> None:
        self.litellm_model = self._route()

    def complete(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        cache_prefix: Optional[str] = None,
        max_tokens: Optional[int] = None,
        stream: bool = False,
        draw: int = 0,
        cache_if: Optional[Callable[[str], bool]] = None,
    ) -> Reply:
        """One prompt, one reply, served from the response cache when it is on.

        Args:
            system: Sent as a leading ``system`` message.
            cache_prefix: Stable leading text (instructions, injected docs, a skill
                document) concatenated verbatim before *prompt*. On Anthropic-family
                models it gets a provider cache breakpoint when long enough
                (:func:`caching.build_messages`).
            max_tokens: Overrides the object's ceiling for this call.
            stream: Stream the reply and measure ``usage.ttft_sec``.
            draw: Index of an independent sample of this request: a new index gets
                its own response-cache entry instead of replaying draw 0's.
            cache_if: Acceptance test for storing *and* replaying a cached reply
                (:func:`caching.keeps`).

        Raises:
            caching.CacheMiss: inside :func:`caching.replay_only`, when nothing
                acceptable is stored; no request is sent.
        """
        if max_tokens is None:
            max_tokens = self.max_tokens
        api_key = self._key()
        messages, _ = caching.build_messages(
            prompt, system=system, cache_prefix=cache_prefix,
            model=self.model, provider=self.provider,
        )
        key = caching.response_cache_key(
            litellm_model=self.litellm_model, messages=messages,
            extra=caching.draw_extra(self._cache_extra(stream, max_tokens), draw),
        )
        hit = caching.load_response(key)
        if hit is not None and caching.keeps(cache_if, cast(str, hit[0])):
            return self._metered(Reply(text=hit[0], usage=hit[1]))
        if caching.replaying_only():
            raise caching.CacheMiss(key)
        reply = self._send(self._request(messages, api_key, max_tokens=max_tokens, stream=stream))
        if reply.response is not None and not getattr(reply.response, "choices", None):
            raise IndexError("reply has no choices")  # as llm_call always did; not cached
        _warn_if_truncated(reply, self.model, max_tokens)
        # A content-less reply is stored as null, as it always was.
        text = cast(str, reply.text)
        if caching.keeps(cache_if, text):
            caching.store_response(key, text, reply.usage)
        return reply

    def ask(self, prompt: str, *, draw: int = 0,
            cache_if: Optional[Callable[[str], bool]] = None) -> str:
        """The reply text alone (``""`` for no content): the :data:`Ask` shape."""
        return self.complete(prompt, draw=draw, cache_if=cache_if).text or ""

    def chat(
        self,
        messages: List[Dict[str, Any]],
        *,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        max_tokens: Optional[int] = None,
    ) -> Reply:
        """A multi-turn request, optionally offering *tools*. Never response-cached.

        A long system prompt gets a provider cache breakpoint
        (:func:`caching.annotate_system_breakpoint`), because an agent loop re-sends
        the whole conversation every turn and would otherwise pay full input price
        for the same skill body N times over. The **response** cache is not used:
        replaying a stored completion would skip the tool calls the loop is
        measuring, so a hit would be a different run, not a cheaper one.
        """
        api_key = self._key()
        messages, _ = caching.annotate_system_breakpoint(
            messages, model=self.model, provider=self.provider,
        )
        return self._send(self._request(
            messages, api_key, max_tokens=self.max_tokens if max_tokens is None else max_tokens,
            tools=tools, tool_choice=tool_choice,
        ))

    def metrics(self) -> UsageRecord:
        """The sum of every reply this object returned, cache replays included.

        ``n_calls`` counts replies; ``cost_usd`` is ``None`` when none was priced. A
        replay is summed at its stored usage and cost, and the total is not marked
        ``replayed``: it is the modelled cost, not this process's spend (a spend cap
        reads the per-reply records and skips ``usage.replayed`` ones).
        """
        with self._lock:
            return self._total

    # Overridden only by the ``utils.get_llm`` delegate (``_GetLLMCompat``).
    def _route(self) -> str:
        return resolve_model(self.model, self.provider)

    def _key(self) -> Optional[str]:
        return resolve_api_key(self.provider, self.api_key)

    def _cache_extra(self, stream: bool, max_tokens: Optional[int]) -> Dict[str, Any]:
        # max_tokens belongs in the key: it changes the response (a truncated or
        # reasoning-starved completion is a different answer). temperature/seed
        # belong in it too, only when set: a pinned run must not be served a
        # free-sampling reply, and entries cached before they existed still hit.
        return {"stream": stream, "max_tokens": max_tokens,
                **_sampling_kwargs(temperature=self.temperature, seed=self.seed)}

    def _labels(self) -> Tuple[str, str]:
        """The ``(model, provider)`` the reply's usage records."""
        return self.model, self.provider

    def _request(self, messages: List[Dict[str, Any]], api_key: Optional[str],
                 **kwargs: Any) -> Request:
        model, provider = self._labels()
        return Request(
            model=model, provider=provider, litellm_model=self.litellm_model,
            messages=messages, api_key=api_key,
            temperature=self.temperature, seed=self.seed, **kwargs,
        )

    def _send(self, request: Request) -> Reply:
        adapter = self.adapter or _default_adapter
        delay = self.retry_delay
        attempt = 0
        while True:
            try:
                return self._metered(adapter(request))
            except Exception as exc:
                if attempt >= self.max_retries or not is_retryable(exc):
                    raise
                attempt += 1
                logger.warning(
                    f"LLM call failed (attempt {attempt}/{self.max_retries + 1}), "
                    f"retrying in {delay:.0f}s: {exc}"
                )
                time.sleep(delay)
                delay *= 2

    def _metered(self, reply: Reply) -> Reply:
        with self._lock:
            self._total = self._total + reply.usage
        return reply


#: Asks a model one prompt: ``ask(prompt, draw=n, cache_if=check) -> str``, as
#: :meth:`LLM.ask` takes it.
Ask = Callable[..., str]

#: Draws per prompt before :func:`ask_parsed` gives up.
ATTEMPTS = 3


class Unparseable(ValueError):
    """No attempt produced a reply the caller could parse.

    The message is the last parse error's; ``reply`` is the last raw reply.
    """

    def __init__(self, message: str, reply: str):
        super().__init__(message)
        self.reply = reply


def ask_parsed(ask: Ask, prompt: str, parse: Callable[[str], Optional[T]], *,
               attempts: int = ATTEMPTS, draw: int = 0, label: str = "LLM", start: int = 0,
               reprompt: Optional[Callable[[str, int], str]] = None) -> Tuple[T, str]:
    """``(parse(reply), reply)`` for the first of *attempts* draws that parses.

    *parse* signals an unusable reply by raising or by returning ``None``. Each
    attempt is its own draw (``draw * attempts + i``, so draw 0 asks draws 0, 1,
    2) and only a reply that parses is cached: otherwise, with the response cache
    on, an unparseable reply is stored and every later run replays it without a
    call. A redraw is a new sample only while sampling is free; at pinned
    sampling the same prompt returns the same reply, so a caller that pins it
    passes *reprompt*: ``reprompt(prompt, i)`` is the prompt of attempt ``i >= 1``.
    Transport faults are not retried here (:meth:`LLM.complete` does that).
    *start* skips the attempts before it, which the caller has already answered.

    Raises:
        Unparseable: no attempt parsed. Errors from *ask* itself propagate as-is.
    """
    def parses(raw: str) -> bool:
        try:
            return parse(raw) is not None
        except Exception:
            return False

    raw, error = "", ""
    for attempt in range(start, attempts):
        asked = reprompt(prompt, attempt) if reprompt and attempt else prompt
        raw = ask(asked, draw=draw * attempts + attempt, cache_if=parses)
        try:
            value = parse(raw)
        except Exception as exc:
            error = str(exc)
        else:
            if value is not None:
                return value, raw
            error = f"no parseable reply, last {str(raw)[:80]!r}"
        logger.warning("%s unparseable (attempt %d/%d): %s", label, attempt + 1, attempts, error)
    raise Unparseable(error, raw)


# ── Legacy entry points ───────────────────────────────────────────────────────
# Delegates for the callers that have not moved to :class:`LLM` yet; each is the
# module's behaviour under the old call shape. They go once nothing imports them.

# Backward-compatibility shims for existing tests/modules.
LANGCHAIN_AVAILABLE = True

_build_litellm_model = resolve_model
_is_retryable = is_retryable


class ChatResponse(Protocol):
    content: str
    usage: Optional[UsageRecord]


class ChatModel(Protocol):
    """A chat model the eval and question stages call: ``invoke(prompt).content``.

    ``ChatOpenAI``/``ChatAnthropic`` below and the wrapper ``utils.get_llm``
    returns all satisfy it.
    """

    def invoke(self, prompt: str, draw: int = 0,
               cache_if: Optional[Callable[[str], bool]] = None) -> ChatResponse: ...


class _Resp:
    def __init__(self, content: Any, usage: Optional[UsageRecord] = None):
        self.content = content
        self.usage = usage   # UsageRecord | None — lets callers (judge) track cost


class _Invoker:
    """``invoke(prompt, draw=, cache_if=)`` over an :class:`LLM`, the shape ``get_llm`` returns."""

    def __init__(self, llm: LLM):
        self.llm = llm

    @property
    def litellm_model(self) -> str:
        return self.llm.litellm_model

    def invoke(self, prompt: str, draw: int = 0,
               cache_if: Optional[Callable[[str], bool]] = None) -> _Resp:
        reply = self.llm.complete(prompt, draw=draw, cache_if=cache_if)
        return _Resp(reply.text, usage=reply.usage)


@dataclass(eq=False)
class _GetLLMCompat(LLM):
    """The transport ``utils.get_llm`` had, on :class:`LLM`; goes when the judge moves.

    ``get_llm`` keeps its own provider table, so it hands in the litellm *route* and
    the *key* it resolved. Its cache entries were keyed without ``stream``, and its
    usage records the route as the model and ``"litellm"`` as the provider.
    """

    route: str = ""
    key: Optional[str] = field(default=None, repr=False)

    def _route(self) -> str:
        return self.route

    def _key(self) -> Optional[str]:
        return self.key

    def _cache_extra(self, stream: bool, max_tokens: Optional[int]) -> Dict[str, Any]:
        return {"max_tokens": max_tokens,
                **_sampling_kwargs(temperature=self.temperature, seed=self.seed)}

    def _labels(self) -> Tuple[str, str]:
        return self.litellm_model, "litellm"


class _Shim:
    """LangChain-compatible shim over :class:`LLM` (``invoke(prompt).content``)."""

    provider = "openai"

    def __init__(self, model: str, api_key: Optional[str] = None,
                 temperature: Optional[float] = None, seed: Optional[int] = None,
                 **kwargs: Any):
        self.model = model
        self.api_key = api_key
        self.temperature = temperature
        self.seed = seed

    def invoke(self, prompt: str, draw: int = 0,
               cache_if: Optional[Callable[[str], bool]] = None) -> _Resp:
        """Invoke the model with a prompt; response carries a UsageRecord."""
        text, usage = llm_call_with_usage(
            prompt, self.model, provider=self.provider, api_key=self.api_key,
            temperature=self.temperature, seed=self.seed,
            draw=draw, cache_if=cache_if,
        )
        return _Resp(text, usage=usage)


class ChatOpenAI(_Shim):
    """LangChain-compatible shim over :class:`LLM` at ``openai``."""

    provider = "openai"


class ChatAnthropic(_Shim):
    """LangChain-compatible shim over :class:`LLM` at ``anthropic``."""

    provider = "anthropic"


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
    """``LLM(model, provider, ...).complete(prompt, ...).text``; see :meth:`LLM.complete`."""
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
) -> Tuple[str, UsageRecord]:
    """:meth:`LLM.complete` as ``(text, usage)``; ``text`` is ``None`` for no content."""
    llm = LLM(model, provider, api_key, max_tokens=max_tokens, temperature=temperature,
              seed=seed, max_retries=max_retries, retry_delay=retry_delay)
    reply = llm.complete(prompt, system=system, cache_prefix=cache_prefix, stream=stream,
                         draw=draw, cache_if=cache_if)
    return cast(str, reply.text), reply.usage


def chat_completion(
    messages: List[Dict[str, Any]],
    model: str,
    provider: str = "openai",
    tools: Optional[List[Dict[str, Any]]] = None,
    tool_choice: Optional[str] = None,
    api_key: Optional[str] = None,
    max_retries: int = 3,
    retry_delay: float = 2.0,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    seed: Optional[int] = None,
) -> Any:
    """:meth:`LLM.chat`, returning the raw litellm response the agent loop reads.

    Args:
        messages: OpenAI-style message dicts.
        tools: Optional list of tool schemas (OpenAI/litellm format).
        tool_choice: Optional tool-choice directive (e.g. ``"auto"``).
    """
    llm = LLM(model, provider, api_key, max_tokens=max_tokens, temperature=temperature,
              seed=seed, max_retries=max_retries, retry_delay=retry_delay)
    return llm.chat(messages, tools=tools, tool_choice=tool_choice).response
