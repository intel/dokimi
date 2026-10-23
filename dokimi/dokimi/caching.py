# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Prompt and response caching for the LLM chokepoints.

Two unrelated mechanisms live here because both are cache-shaped and both hang
off :mod:`dokimi.llm`:

**Provider prompt caching.** The provider stores a prefix of the request and
charges a fraction of the input price when a later request repeats it. Two
conditions have to hold, and the second is easy to miss:

1. Anthropic-family models need an *explicit* breakpoint
   (``cache_control: {"type": "ephemeral"}``) on the last content block that
   should be cached. OpenAI and Gemini cache long prefixes automatically.
2. The repeated text must be a genuine **prefix** — everything before it must be
   byte-identical too. A prompt that opens with the question and only then
   supplies a 12k-character skill document has no reusable prefix at all, so
   nothing is cacheable regardless of breakpoints. Callers therefore pass the
   stable part as ``cache_prefix`` and keep the per-question part in ``prompt``.

:func:`build_messages` decides this for the single-prompt shape and
:func:`annotate_system_breakpoint` for the multi-turn agentic one. Below :data:`MIN_CACHEABLE_PREFIX_CHARS` it does not bother:
providers enforce a minimum cacheable length (1024 tokens for most Anthropic
models, 2048 for the small ones) and silently skip anything shorter, while every
write still carries a surcharge.

**Response caching.** Reuse of a previous *result* for a byte-identical request,
which no provider does for us. Applies to the single-shot path only — the agentic
loop is excluded on purpose, since replaying a completion would skip the tool
calls that track exists to measure. Off by default; set
``DOKIMI_RESPONSE_CACHE`` to a directory to enable. It exists mainly
for the baseline arm, which is identical across treatment runs, and for
re-rendering reports without paying for inference twice.

Note what a replayed entry reports: the *stored* usage record — tokens, cost and
latency as they were when the call was really made. Cost figures therefore keep
describing the modelled call rather than this process's bill. A run served from
cache is not free to reproduce, and reporting ``$0``/``0s`` would make cost and
latency incomparable across runs. The flip side is that a cached run's latency
numbers are a replay, not a measurement, which is why the cache stays off unless
asked for.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple, TypeVar

from dokimi.metrics.usage import UsageRecord

logger = logging.getLogger(__name__)

T = TypeVar("T")

#: Environment switch for provider prompt caching (on unless explicitly off).
PROMPT_CACHE_ENV = "DOKIMI_PROMPT_CACHE"

#: Directory for the response cache. Unset means the response cache is off.
RESPONSE_CACHE_ENV = "DOKIMI_RESPONSE_CACHE"

#: Don't set a breakpoint below this prefix length. Anthropic's minimum
#: cacheable prefix is 1024 tokens for most models and **2048 for the small
#: ones** (Haiku), and a request under the floor is silently not cached while a
#: cache-write surcharge still applies. At roughly 4 characters per token, 8000
#: chars is only ~2000 tokens — under the small-model floor, i.e. exactly the
#: case this constant exists to avoid. 12000 chars is ~3000 tokens, which clears
#: 2048 with enough margin for text that tokenizes densely.
#:
#: This is a heuristic on characters because the tokenizer is provider-specific
#: and not available here; erring high costs a missed cache hit, erring low
#: costs money on every call.
MIN_CACHEABLE_PREFIX_CHARS = 12_000

#: Values that turn a cache switch off. Shared by both mechanisms so the two
#: are configured the same way; an empty string counts as off, not as unset.
_FALSEY = {"0", "false", "no", "off", ""}

_FALSEY_HINT = "one of " + ", ".join(repr(v) for v in sorted(_FALSEY) if v)


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in _FALSEY


def prompt_cache_enabled() -> bool:
    """Whether to set explicit cache breakpoints. Default on."""
    return _env_flag(PROMPT_CACHE_ENV, True)


def supports_explicit_cache(model: str, provider: str) -> bool:
    """Whether ``model`` takes Anthropic-style ``cache_control`` breakpoints.

    Bedrock and Vertex serve several model families; only the Anthropic ones
    accept the field, so the model name decides there. Everything else (OpenAI,
    Gemini) caches long prefixes on its own and needs no breakpoint — returning
    ``False`` is not "no caching", it is "no annotation required".
    """
    provider = (provider or "").lower()
    if provider == "anthropic":
        return True
    if provider in {"amazon-bedrock", "bedrock", "google-vertex", "vertex_ai", "openrouter"}:
        return "claude" in (model or "").lower()
    return False


def build_messages(
    prompt: str,
    *,
    system: Optional[str] = None,
    cache_prefix: Optional[str] = None,
    model: str = "",
    provider: str = "",
) -> Tuple[List[Dict[str, Any]], bool]:
    """Assemble the litellm ``messages`` list, cache breakpoint included.

    ``cache_prefix`` is the stable leading text (instructions, injected docs, a
    skill document); ``prompt`` is the part that varies per question. They are
    concatenated **verbatim**, with no separator inserted, so a caller that
    wants a blank line between them puts it at the end of the prefix. This
    keeps the single-string form byte-identical to the two-block form, meaning
    turning caching off changes billing but never the text the model sees.

    Returns ``(messages, breakpoint_set)``.
    """
    if not isinstance(prompt, str):
        raise TypeError(f"prompt must be a str, got {type(prompt).__name__}")
    if cache_prefix is not None and not isinstance(cache_prefix, str):
        raise TypeError(
            f"cache_prefix must be a str or None, got {type(cache_prefix).__name__}"
        )

    messages: List[Dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})

    if not cache_prefix:
        messages.append({"role": "user", "content": prompt})
        return messages, False

    if (
        prompt_cache_enabled()
        and supports_explicit_cache(model, provider)
        and len(cache_prefix) >= MIN_CACHEABLE_PREFIX_CHARS
    ):
        messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": cache_prefix,
                        "cache_control": {"type": "ephemeral"},
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        )
        return messages, True

    messages.append({"role": "user", "content": cache_prefix + prompt})
    return messages, False


def annotate_system_breakpoint(
    messages: List[Dict[str, Any]], *, model: str = "", provider: str = ""
) -> Tuple[List[Dict[str, Any]], bool]:
    """Put a breakpoint on the system message of a multi-turn conversation.

    :func:`build_messages` only handles the single-prompt shape. An agentic loop
    re-sends the whole conversation every turn, so its system prompt — a skill
    body, often 10k+ characters — is billed at full input price once per turn
    with nothing cached. That is where the cost of this track actually is, and
    the system message is the one part guaranteed to be a byte-identical prefix
    of every later request.

    Returns ``(messages, breakpoint_set)``. The input list is not mutated, and
    the *text* is untouched either way: this changes billing, never output.
    Later turns are deliberately left unannotated — Anthropic allows only a few
    breakpoints per request, and the system prompt is the only block whose
    prefix status holds for the whole loop.
    """
    if not messages:
        return messages, False
    head = messages[0]
    if head.get("role") != "system":
        return messages, False
    content = head.get("content")
    if not isinstance(content, str) or len(content) < MIN_CACHEABLE_PREFIX_CHARS:
        return messages, False
    if not (prompt_cache_enabled() and supports_explicit_cache(model, provider)):
        return messages, False
    annotated = {
        **head,
        "content": [
            {"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}
        ],
    }
    return [annotated, *messages[1:]], True


# ---------------------------------------------------------------------------
# Response cache
# ---------------------------------------------------------------------------


def response_cache_dir() -> Optional[Path]:
    """Configured response-cache directory, or ``None`` when disabled.

    The falsey tokens :data:`_FALSEY` accepts for the prompt-cache switch mean
    "off" here too. Without that, someone disabling this cache the way they
    disable the other one — ``DOKIMI_RESPONSE_CACHE=0`` — got a cache
    enabled in a directory literally named ``0``.

    Raises:
        ValueError: when the path exists but is not a directory. A cache is
            allowed to fail quietly at *runtime*, but a misconfiguration that
            can never hit should not masquerade as a permanent cache miss.
    """
    raw = os.getenv(RESPONSE_CACHE_ENV)
    if raw is None or raw.strip().lower() in _FALSEY:
        return None
    path = Path(raw.strip())
    if path.exists() and not path.is_dir():
        raise ValueError(
            f"{RESPONSE_CACHE_ENV}={raw!r} points at a file, not a directory. "
            f"Unset it or set it to {_FALSEY_HINT} to disable the response cache."
        )
    return path


def _canonical_content(content: Any) -> Any:
    """Reduce one message's content to what the model actually reads.

    The two caches must stay independent, and the naive key does not manage it:
    :func:`build_messages` emits the *same text* either as one string or as two
    ``text`` blocks depending on whether a prompt-cache breakpoint was set, so
    hashing the raw structure means flipping ``DOKIMI_PROMPT_CACHE``
    invalidates every response-cache entry — a billing knob silently throwing
    away paid-for completions.

    All-text block lists therefore collapse to their concatenation, which
    :func:`build_messages` guarantees is byte-identical to the single-string
    form. Anything else (image blocks, provider-specific parts) is kept
    structurally, with ``cache_control`` stripped: it changes billing, never the
    completion.
    """
    if not isinstance(content, list):
        return content
    if all(isinstance(b, dict) and b.get("type") == "text" for b in content):
        return "".join(b.get("text", "") for b in content)
    return [
        {k: v for k, v in b.items() if k != "cache_control"} if isinstance(b, dict) else b
        for b in content
    ]


def draw_extra(extra: Dict[str, Any], draw: int) -> Dict[str, Any]:
    """Add a sample index to a cache key's ``extra``, leaving draw 0 unchanged.

    Without this a repeated request can never be a new sample once the cache is
    on: the judge's retry after an unparseable reply replays the same
    unparseable reply, and two "independent" judgements of one answer are the
    same completion read twice. Draw 0 adds nothing, so every entry written
    before draws existed still hits.
    """
    return {**extra, "draw": draw} if draw else extra


def keeps(cache_if: Optional[Callable[[str], bool]], text: str) -> bool:
    """Whether a completion may be stored, or a stored one replayed.

    *cache_if* is the caller's own acceptance test. The judge passes "this reply
    parses": without it an unparseable reply is stored like any other, and once
    all three of the judge's attempts are stored that way, every later run
    replays the failure without calling the model. Checking on replay too means
    entries stored before the check existed stop poisoning the run.
    """
    return cache_if is None or cache_if(text)


class CacheMiss(LookupError):
    """Raised by :meth:`dokimi.llm.LLM.complete` on a miss inside :func:`replay_only`."""


_REPLAY_ONLY: ContextVar[bool] = ContextVar("dokimi_response_replay_only", default=False)


@contextmanager
def replay_only() -> Iterator[None]:
    """Serve completions from the response cache only: a miss raises :class:`CacheMiss`."""
    token = _REPLAY_ONLY.set(True)
    try:
        yield
    finally:
        _REPLAY_ONLY.reset(token)


def replaying_only() -> bool:
    """Whether the calling context is inside :func:`replay_only`."""
    return _REPLAY_ONLY.get()


def first_replayed(attempts: int, ask: Callable[[int], T]) -> Optional[Tuple[int, T]]:
    """``(i, ask(i))`` for the first of *attempts* answered from the cache, else ``None``.

    For a redraw loop that caches only accepted replies (``cache_if``). Its rejected
    attempts are never stored, so a rerun that walked the attempts in order asked the
    first one live again on every run before the stored accepted one replayed. Probing
    every attempt's entry first makes a rerun of an already-accepted judgement free,
    with the keys unchanged. Returns ``None`` at once when the cache is off. A
    stored entry *ask*'s ``cache_if`` rejects is a miss. An *ask* that does not go
    through :meth:`dokimi.llm.LLM.complete` cannot miss: its first answer is
    returned as attempt 0, as the plain loop would have asked it.
    """
    if response_cache_dir() is None:
        return None
    with replay_only():
        for attempt in range(attempts):
            try:
                return attempt, ask(attempt)
            except CacheMiss:
                continue
    return None


def response_cache_key(
    *, litellm_model: str, messages: List[Dict[str, Any]], extra: Optional[Dict[str, Any]] = None
) -> str:
    """Stable hash of everything that can change a completion.

    ``messages`` is serialized with sorted keys so an equivalent request built
    in a different key order still hits, and canonicalized by
    :func:`_canonical_content` so a prompt-cache breakpoint — which changes the
    request's shape but not its text — does not change the key. Sampling
    parameters belong in ``extra``; anything omitted there is, by construction,
    assumed not to affect the result.
    """
    canonical = [
        {**{k: v for k, v in m.items() if k != "content"},
         "content": _canonical_content(m.get("content"))}
        for m in messages
    ]
    payload = {
        "model": litellm_model,
        "messages": canonical,
        "extra": extra or {},
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _usage_value_fits(name: str, value: object) -> bool:
    """Whether a stored usage value has its field's type, so summing the replay cannot crash."""
    default = UsageRecord.__dataclass_fields__[name].default
    if isinstance(default, str):
        return isinstance(value, str)
    if value is None:
        return default is None
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def load_response(key: str) -> Optional[Tuple[Optional[str], UsageRecord]]:
    """Return ``(text, usage)`` for a cached call, or ``None`` on any miss.

    ``text`` is ``None`` for a reply stored without content.

    A corrupt or unreadable entry is a miss, not an error: the call is simply
    made again. The stored usage is replayed as-is (see the module docstring), marked
    ``replayed`` so a spend cap can tell a hit from a call it paid for.
    """
    directory = response_cache_dir()
    if directory is None:
        return None
    path = directory / f"{key}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        text = data["text"]
        usage_fields = data.get("usage")
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
    # Valid JSON of the wrong shape (a list, a number as the text) is as corrupt
    # as unparseable JSON. A null text is kept: a reply without content is stored
    # and replayed as such.
    if not isinstance(text, (str, type(None))) or not isinstance(usage_fields, (dict, type(None))):
        return None
    usage_fields = dict(usage_fields or {})

    known = set(UsageRecord.__dataclass_fields__)
    unknown = set(usage_fields) - known
    if unknown:
        # Written by a newer version with extra telemetry fields; the text is
        # still valid, so keep the hit and drop what we cannot represent.
        logger.debug("Dropping unknown usage fields from cache entry: %s", sorted(unknown))
        usage_fields = {k: v for k, v in usage_fields.items() if k in known}
    usage_fields.pop("replayed", None)
    if not all(_usage_value_fits(k, v) for k, v in usage_fields.items()):
        return None
    try:
        usage = UsageRecord(**usage_fields, replayed=True)
    except TypeError:
        return None
    return text, usage


def store_response(key: str, text: str, usage: UsageRecord) -> None:
    """Persist one completion. Failures are logged, never raised.

    A cache is an optimisation; an unwritable directory must not take down a
    run that has already paid for the inference.
    """
    directory = response_cache_dir()
    if directory is None:
        return
    try:
        directory.mkdir(parents=True, exist_ok=True)
        tmp = directory / f".{key}.tmp"
        tmp.write_text(
            json.dumps({"text": text, "usage": asdict(usage)}, ensure_ascii=False),
            encoding="utf-8",
        )
        # Atomic within a directory, so concurrent arms cannot observe a
        # half-written entry.
        tmp.replace(directory / f"{key}.json")
    except OSError as exc:
        logger.warning("Could not write response cache entry: %s", exc)
