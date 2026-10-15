# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Normalized per-run telemetry record.

One shape for tokens, cache, cost, and latency returned by every LLM call. The ``llm.py``
chokepoints build a :class:`UsageRecord`; runners accumulate it across agentic
turns with ``+`` and serialize it into the per-row ``metrics{}`` block (plus the
legacy 3-key ``token_usage`` alias).

Extraction is deliberately defensive: any field a provider does not expose
defaults to ``0`` / ``None`` rather than raising. ``cost_usd`` is ``None`` (not
``0.0``) when no pricing is available — a missing price is not a free call.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Iterable, Optional


@dataclass(frozen=True)
class UsageRecord:
    """Resource usage for one or more LLM calls."""

    # tokens
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    reasoning_tokens: int = 0          # o1/o-style; 0 when absent
    # cache (provider-dependent; 0 when not exposed)
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    # cost — None means "no pricing available", distinct from 0.0
    cost_usd: Optional[float] = None
    # latency
    latency_sec: float = 0.0
    ttft_sec: Optional[float] = None   # only when streaming; else None
    # provenance / accumulation
    model: str = ""
    provider: str = ""
    role: str = "main"                 # "main" | "subagent" — see roll_up_by_role
    n_calls: int = 1
    cost_known_calls: int = 0
    # served from the response cache: cost_usd is the original call's, not this process's bill
    replayed: bool = False

    def __add__(self, other: "UsageRecord | int") -> "UsageRecord":
        """Sum two records: tokens/cost/latency/n_calls add; first ttft wins.

        ``cost_usd`` stays ``None`` only if *both* operands are ``None`` (one
        priced + one unpriced call yields the priced subtotal, not ``None``).
        ``model``/``provider`` are taken from whichever operand has them.
        """
        if other == 0:  # identity element, so sum([...]) works
            return self
        if not isinstance(other, UsageRecord):
            return NotImplemented
        if self.cost_usd is None and other.cost_usd is None:
            cost = None
        else:
            cost = (self.cost_usd or 0.0) + (other.cost_usd or 0.0)
        # First non-null TTFT (first token of the first turn) wins.
        ttft = self.ttft_sec if self.ttft_sec is not None else other.ttft_sec
        return UsageRecord(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
            reasoning_tokens=self.reasoning_tokens + other.reasoning_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
            cost_usd=cost,
            latency_sec=round(self.latency_sec + other.latency_sec, 6),
            ttft_sec=ttft,
            model=self.model or other.model,
            provider=self.provider or other.provider,
            role=self.role or other.role,
            n_calls=self.n_calls + other.n_calls,
            cost_known_calls=_known_cost_calls(self) + _known_cost_calls(other),
            replayed=self.replayed and other.replayed,
        )

    __radd__ = __add__  # enables sum([...]) — 0 + record is handled in __add__

    def __getitem__(self, key: str) -> Any:
        """Legacy dict-style access for callers of ``llm_call_with_usage``."""
        return self.as_metrics_dict()[key]

    def get(self, key: str, default: Any = None) -> Any:
        """Legacy dict-style ``get`` for callers of ``llm_call_with_usage``."""
        return self.as_metrics_dict().get(key, default)

    def as_token_usage_dict(self) -> dict:
        """The legacy 3-key ``token_usage`` shape kept for backward compat."""
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }

    def as_metrics_dict(
        self, answer_chars: Optional[int] = None, final_chars: Optional[int] = None
    ) -> dict:
        """The per-row ``metrics{}`` block.

        ``answer_chars`` is the *raw* model output size; ``final_chars`` is the
        size delivered after any output-shaper plugin ran. When ``final_chars``
        is omitted the two are equal (no shaper). Storing both is what lets a
        Caveman-style shaper report a length reduction it did *not* save in
        billed model tokens.
        """
        m: dict[str, Any] = {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "cost_usd": self.cost_usd,
            "latency_sec": round(self.latency_sec, 4),
            "ttft_sec": round(self.ttft_sec, 4) if self.ttft_sec is not None else None,
            "n_calls": self.n_calls,
            "cost_known_calls": _known_cost_calls(self),
            "role": self.role,
        }
        if answer_chars is not None:
            m["raw_answer_chars"] = answer_chars
            m["final_answer_chars"] = final_chars if final_chars is not None else answer_chars
        return m

    def with_latency(self, latency_sec: float, ttft_sec: Optional[float] = None) -> "UsageRecord":
        """Return a copy with latency/TTFT filled in (extraction-time helper)."""
        return replace(self, latency_sec=latency_sec, ttft_sec=ttft_sec)


def to_record(
    obj: Any,
    model: str = "",
    provider: str = "",
) -> UsageRecord:
    """Coerce a usage value into a :class:`UsageRecord`.

    Accepts a :class:`UsageRecord` (returned as-is, back-filling
    model/provider), or a legacy ``{prompt_tokens, completion_tokens,
    total_tokens, ...}`` dict (e.g. from a test stub). ``None`` yields an empty
    record. This keeps callers tolerant of both the new return type and older
    dict-shaped stubs.
    """
    if isinstance(obj, UsageRecord):
        if (not obj.model and model) or (not obj.provider and provider):
            return replace(obj, model=obj.model or model, provider=obj.provider or provider)
        return obj
    if obj is None:
        return UsageRecord(model=model, provider=provider, n_calls=0, total_tokens=0)
    if isinstance(obj, dict):
        return UsageRecord(
            prompt_tokens=int(obj.get("prompt_tokens", 0) or 0),
            completion_tokens=int(obj.get("completion_tokens", 0) or 0),
            total_tokens=int(obj.get("total_tokens", 0) or 0),
            reasoning_tokens=int(obj.get("reasoning_tokens", 0) or 0),
            cache_read_tokens=int(obj.get("cache_read_tokens", 0) or 0),
            cache_write_tokens=int(obj.get("cache_write_tokens", 0) or 0),
            cost_usd=obj.get("cost_usd"),
            latency_sec=float(obj.get("latency_sec", 0.0) or 0.0),
            ttft_sec=obj.get("ttft_sec"),
            model=obj.get("model", model) or model,
            provider=obj.get("provider", provider) or provider,
            role=str(obj.get("role") or "main"),
            # Preserve an explicit n_calls=0; only default to 1 when absent/None.
            n_calls=(int(obj["n_calls"])
                     if obj.get("n_calls") is not None else 1),
            cost_known_calls=int(obj.get("cost_known_calls", 0) or 0),
        )
    raise TypeError(f"Cannot coerce {type(obj).__name__} into UsageRecord")


def roll_up_by_role(records: "Iterable[UsageRecord]") -> dict:
    """Split usage into main-agent vs subagent buckets.

    Groups records by ``role`` (anything other than ``"subagent"`` counts as
    main) and reports tokens and cost for each bucket plus the full-system
    total. Cost stays ``None`` when a bucket has no priced calls; the
    full-system cost is the sum of whichever buckets are priced (``None`` only
    when *both* are unpriced) — so an unpriced subagent never silently zeroes
    the system cost.
    """
    records = list(records)
    # Seed with role="" so the empty identity element never overrides the
    # bucket's real role under ``__add__`` (role=self.role or other.role).
    main = sum((r for r in records if r.role != "subagent"), UsageRecord(n_calls=0, role=""))
    sub = sum((r for r in records if r.role == "subagent"), UsageRecord(n_calls=0, role=""))
    # full-system cost is a trustworthy total only when *every* call (main and
    # subagent) is priced; otherwise an unpriced portion would read as free.
    cost_complete = _cost_complete(main) and _cost_complete(sub)
    return {
        "main_agent_tokens": main.total_tokens,
        "subagent_tokens": sub.total_tokens,
        "full_system_tokens": main.total_tokens + sub.total_tokens,
        "main_agent_cost": main.cost_usd,
        "subagent_cost": sub.cost_usd,
        "full_system_cost": (
            _add_optional_costs(main.cost_usd, sub.cost_usd) if cost_complete else None
        ),
        "full_system_cost_complete": cost_complete,
    }


def _cost_complete(rec: UsageRecord) -> bool:
    """True when a bucket's cost is fully known: empty, or all calls priced."""
    return rec.n_calls == 0 or _known_cost_calls(rec) == rec.n_calls


def _add_optional_costs(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None and b is None:
        return None
    return round((a or 0.0) + (b or 0.0), 6)


def _known_cost_calls(rec: UsageRecord) -> int:
    """Number of LLM calls whose dollar cost is represented in ``cost_usd``."""
    if rec.cost_known_calls:
        return rec.cost_known_calls
    if rec.cost_usd is not None and rec.n_calls > 0:
        return rec.n_calls
    return 0
