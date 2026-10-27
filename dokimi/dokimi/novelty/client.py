# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The novelty layer's single LLM entry point.

Every novelty stage that needs a model goes through :class:`LLMClient`, for three
reasons: the usage records accumulate in one place (so a `novelty.v1` artifact can
state what the measurement cost), the roles stay separable (extractor, answerer,
judge are distinct clients, never silently the same model), and tests can swap the
transport for a stub without patching :mod:`dokimi.llm`.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

from dokimi.defaults import (
    BEDROCK_MODEL_IDS,
    DEFAULT_GENERATOR_MODEL,
    DEFAULT_GENERATOR_PROVIDER,
    DEFAULT_JUDGE_MODEL,
    DEFAULT_JUDGE_PROVIDER,
)
from dokimi.metrics.usage import UsageRecord

# A transport takes (prompt, system) and returns (text, usage).
Transport = Callable[[str, "str | None"], "tuple[str, UsageRecord | None]"]
# An acceptance test for a reply: False means "unusable", so the response cache neither
# stores it nor replays it (see :func:`dokimi.caching.keeps`).
Accept = Callable[[str], bool]


class SpendCapReached(BaseException):
    """The run hit its ``--max-usd`` cap, or made a call whose price is unknown.

    A ``BaseException``, like ``KeyboardInterrupt``: the stages treat a failed call as a
    missing datum and catch ``Exception``, so an ``Exception`` here would be absorbed one
    call at a time and the run would carry on with every later call refused.
    """


@dataclass
class SpendCap:
    """One USD limit shared by every client of one run.

    Checked before each call, so the run can overshoot by the calls already in flight
    (``--concurrency``). A call that comes back without a price stops the run: the cap
    cannot be enforced on spend it cannot see. A reply replayed from the response cache
    (``DOKIMI_RESPONSE_CACHE``) costs nothing now and counts as $0, so a stopped run
    resumed with the same cap spends only on the calls it had not made.
    """

    limit_usd: float
    spent_usd: float = 0.0
    calls: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def before_call(self, model: str) -> None:
        with self._lock:
            if self.spent_usd >= self.limit_usd:
                raise SpendCapReached(
                    f"spend cap reached: ${self.spent_usd:.2f} of ${self.limit_usd:.2f} "
                    f"after {self.calls} calls (next call: {model}); nothing was written. "
                    "Raise --max-usd, or measure less (a cheaper --tier, a lower --max-atoms)"
                )

    def record(self, usage: UsageRecord | None, model: str) -> None:
        with self._lock:
            self.calls += 1
            if usage is not None and usage.replayed:
                return
            if usage is None or usage.cost_usd is None:
                raise SpendCapReached(
                    f"no price is known for {model}, so --max-usd cannot be enforced; "
                    f"stopped after {self.calls} calls. Drop --max-usd to run unmetered"
                )
            self.spent_usd += usage.cost_usd


@dataclass
class LLMClient:
    """One model in one role, with its usage recorded.

    Args:
        model: Model id passed to the provider.
        provider: Provider name understood by :mod:`dokimi.llm`.
        role: What this client is for (``extractor``, ``answerer``, ``judge``, …).
            Recorded on every usage row so cost can be attributed per role.
        max_tokens: Output ceiling. Reasoning models can spend the provider default
            entirely on internal reasoning and return empty content, so novelty runs
            against them need this raised.
        transport: Injected call function; defaults to the real LLM on first use.
        cap: Spend limit shared with the run's other clients; ``None`` is unmetered.
    """

    model: str
    provider: str = "openai"
    role: str = "novelty"
    max_tokens: int | None = None
    transport: Transport | None = None
    usage: list[UsageRecord] = field(default_factory=list)
    calls: int = 0
    cap: SpendCap | None = None

    def text(
        self, prompt: str, *, system: str | None = None, accept: Accept | None = None,
        draw: int = 0,
    ) -> str:
        """Return the model's reply as plain text.

        ``accept`` is the caller's test for a usable reply. The default transport passes
        it to the response cache as ``cache_if``: without it, with
        ``DOKIMI_RESPONSE_CACHE`` set, a cut-off or unparseable reply is stored and every
        later run replays the failure without calling the model. An injected transport
        is the caller's own and is not given it.

        ``draw`` is the sample index of this prompt. With the response cache on, a repeat
        of a byte-identical prompt replays draw 0 unless it has its own index, so a
        repeated ask is only a new sample when it passes a new one. Draw 0 is the default
        and keeps every existing cache entry. An injected transport is not given it.
        """
        if self.cap is not None:
            self.cap.before_call(self.model)
        if self.transport is not None:
            reply, usage = self.transport(prompt, system)
        else:
            reply, usage = self._default_transport(prompt, system, accept=accept, draw=draw)
        self.calls += 1
        if usage is not None:
            # UsageRecord is frozen; re-stamp the role so cost rolls up per role.
            self.usage.append(replace(usage, role=self.role))
        if self.cap is not None:
            self.cap.record(usage, self.model)
        return reply or ""

    def json_object(
        self, prompt: str, *, system: str | None = None,
        usable: Callable[[dict[str, Any]], bool] | None = None,
    ) -> dict[str, Any]:
        """Return the model's reply parsed as a JSON object; raise ``ValueError`` otherwise.

        ``usable`` is the caller's test for the object's shape (``{"verdict": null}``
        parses, and is no verdict). It decides only what the response cache keeps: an
        object it rejects is still returned, for the caller to treat as it treats a bad
        reply, but is never stored or replayed, so a later run asks again.
        """

        def shaped(reply: str) -> dict[str, Any]:
            value = parse_json_object(reply)
            if usable is not None and not usable(value):
                raise ValueError("unusable reply")
            return value

        return parse_json_object(self.text(prompt, system=system, accept=accepting(shaped)))

    def json_array(self, prompt: str, *, system: str | None = None) -> list[Any]:
        """Return the model's reply parsed as a JSON array; raise ``ValueError`` otherwise."""
        from dokimi.llm import extract_json_array

        return extract_json_array(
            self.text(prompt, system=system, accept=accepting(extract_json_array))
        )

    def cost_summary(self) -> dict[str, Any]:
        """Roll up this client's usage.

        ``usd`` stays ``None`` when any call this run paid for had unknown pricing or
        returned no usage (a replayed call is not paid for, so it cannot make it unknown),
        rather than silently reporting the sum of the priced subset as the total.
        Calls served from the response cache are counted in ``replayed_calls`` and
        left out of the token and dollar totals.
        """
        # A replayed row is the response cache's copy of an earlier call: its tokens and
        # price were not spent by this process, so it is counted apart, as the spend cap does.
        billed = [u for u in self.usage if not u.replayed]
        input_tokens = sum(u.prompt_tokens for u in billed)
        output_tokens = sum(u.completion_tokens for u in billed)
        cache_read = sum(u.cache_read_tokens for u in billed)
        cache_write = sum(u.cache_write_tokens for u in billed)
        costs = [u.cost_usd for u in billed]
        # A call that returned no usage row has no price either, so it makes the total unknown.
        priced = self.calls > 0 and len(self.usage) == self.calls and None not in costs
        usd = round(sum(c for c in costs if c is not None), 6) if priced else None
        return {
            "model": self.model,
            "provider": self.provider,
            "role": self.role,
            "llm_calls": self.calls,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_tokens": cache_read,
            "cache_write_tokens": cache_write,
            "replayed_calls": len(self.usage) - len(billed),
            "usd": usd,
        }

    def _default_transport(
        self, prompt: str, system: str | None, *, accept: Accept | None = None,
        draw: int = 0,
    ) -> tuple[str, UsageRecord | None]:
        from dokimi.llm import llm_call_with_usage

        return llm_call_with_usage(
            prompt,
            model=self.model,
            provider=self.provider,
            system=system,
            max_tokens=self.max_tokens,
            cache_if=accept,
            draw=draw,
        )


def parse_json_object(reply: str) -> dict[str, Any]:
    """The reply as a JSON object. A list, ``null`` or scalar raises ``ValueError``.

    :func:`dokimi.llm.extract_json_object` returns whatever ``json.loads`` gives, so a
    ``[]`` reply used to come back as a list and fail later, at a ``.get``, outside any
    handler that knew it was a bad reply.
    """
    from dokimi.llm import extract_json_object

    value = extract_json_object(reply)
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object, got {type(value).__name__}: {reply[:120]!r}")
    return value


def accepting(parse: Callable[[str], Any]) -> Accept:
    """``parse`` as a response-cache acceptance test: usable means it does not raise."""

    def accept(reply: str) -> bool:
        try:
            parse(reply)
        except Exception:
            return False
        return True

    return accept


def roll_up_cost(clients: list[LLMClient]) -> dict[str, Any]:
    """Total cost across roles, with the per-role breakdown kept."""
    per_role = [c.cost_summary() for c in clients]
    usd_parts = [row["usd"] for row in per_role]
    return {
        "llm_calls": sum(row["llm_calls"] for row in per_role),
        "input_tokens": sum(row["input_tokens"] for row in per_role),
        "output_tokens": sum(row["output_tokens"] for row in per_role),
        "cache_read_tokens": sum(row["cache_read_tokens"] for row in per_role),
        "cache_write_tokens": sum(row["cache_write_tokens"] for row in per_role),
        "replayed_calls": sum(row["replayed_calls"] for row in per_role),
        "usd": round(sum(p for p in usd_parts if p is not None), 6)
        if usd_parts and None not in usd_parts
        else None,
        "by_role": per_role,
    }


def default_judge(model: str, provider: str | None = None) -> tuple[str, str]:
    """A (model, provider) judge that is not the answerer, when the caller did not name one.

    The default judge, unless it is the model under measurement (by bare name, so a
    Bedrock ``us.anthropic.claude-...`` id counts too); then the default generator,
    which is of the other family. With ``provider`` (an explicit ``--judge-provider``)
    only a default that runs there is taken: its own provider, or Bedrock for a name in
    ``BEDROCK_MODEL_IDS``. Pairing the provider with a model of another provider would
    send e.g. ``bedrock/gpt-6.1-sol``, so with no such default this raises instead.

    Raises:
        ValueError: ``provider`` runs no default judge other than ``model``.
    """
    bare = model.rsplit("/", 1)[-1].rsplit("anthropic.", 1)[-1]
    candidates = [(m, p) for m, p in ((DEFAULT_JUDGE_MODEL, DEFAULT_JUDGE_PROVIDER),
                                      (DEFAULT_GENERATOR_MODEL, DEFAULT_GENERATOR_PROVIDER))
                  if m != bare]
    if provider is None:
        return candidates[0]
    for m, p in candidates:
        if p == provider or (provider == "amazon-bedrock" and m in BEDROCK_MODEL_IDS):
            return m, provider
    raise ValueError(
        f"--judge-provider {provider}: no default judge runs there other than the model "
        f"under measurement ({model}); pass --judge-model as well")
