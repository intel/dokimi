# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""What a run's LLM calls cost: one accumulator of calls, tokens and dollars, and the cap on it.

Every cost total follows one rule (#229 P2-7). A call replayed from the response cache
(``DOKIMI_RESPONSE_CACHE``) was not paid for by this process: it is counted apart and adds no
tokens and no dollars. The dollar total is known only when every paid call has a price; one
call with no price, or one that returned no usage, makes it unknown, never the priced part.

The answers' ``metrics`` rows do not record a replay yet (``UsageRecord.as_metrics_dict``), so
the arms ``cost_summary`` still counts a replayed answer call as paid (#229).
"""

from __future__ import annotations

import threading
from collections.abc import Iterable
from dataclasses import dataclass, field, fields, replace
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from dokimi.metrics.usage import UsageRecord


@dataclass(kw_only=True)
class Spend:
    """Calls, tokens and dollars summed under the module's rule.

    ``calls`` counts every call, replayed ones included; the tokens and ``spent_usd`` are the
    paid calls' only. ``spent_usd`` is the priced calls' sum: :attr:`usd` is the total.
    """

    calls: int = 0
    replayed_calls: int = 0
    unpriced_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    spent_usd: float = 0.0

    @classmethod
    def of(cls, rows: Iterable[UsageRecord | None]) -> Spend:
        spend = cls()
        for row in rows:
            spend.add(row)
        return spend

    def add(self, usage: UsageRecord | None) -> None:
        """Count one usage row, of ``usage.n_calls`` calls; ``None`` is one call that returned no usage."""
        if usage is None:
            self.calls += 1
            self.unpriced_calls += 1
            return
        n = _count(usage.n_calls, missing=1)
        self.calls += n
        if usage.replayed:
            self.replayed_calls += n
            return
        cost = _price(usage.cost_usd)
        # A row claiming more priced calls than it has must not offset another row's unpriced one.
        priced = 0 if cost is None else min(max(_count(usage.priced_calls), 0), n)
        self.unpriced_calls += n - priced
        self.spent_usd += cost or 0.0
        self.prompt_tokens += _count(usage.prompt_tokens)
        self.completion_tokens += _count(usage.completion_tokens)
        self.total_tokens += _count(usage.total_tokens)
        self.cache_read_tokens += _count(usage.cache_read_tokens)
        self.cache_write_tokens += _count(usage.cache_write_tokens)

    def add_call(self, usage: UsageRecord | None) -> None:
        """Count one call, whatever its row's ``n_calls`` says; ``None`` is a call that returned no usage."""
        if usage is not None:
            usage = replace(usage, n_calls=1, cost_known_calls=int(usage.cost_usd is not None))
        self.add(usage)

    def __add__(self, other: Spend) -> Spend:
        return Spend(**{f.name: getattr(self, f.name) + getattr(other, f.name) for f in fields(Spend)})

    @property
    def priced_calls(self) -> int:
        """The calls whose price is known; a replayed call is, at $0."""
        return self.calls - self.unpriced_calls

    @property
    def known_usd(self) -> float | None:
        """The priced calls' dollars, for a report that shows them with ``priced_calls``; ``None`` with none."""
        return round(self.spent_usd, 6) if self.priced_calls else None

    @property
    def usd(self) -> float | None:
        """The dollar total; ``None`` with no call, or with any paid call unpriced."""
        return round(self.spent_usd, 6) if self.calls and not self.unpriced_calls else None


def _count(value: Any, missing: int = 0) -> int:
    """A usage count as an int: a provider may send ``"12"`` (``_extract_usage`` copies it); what does not convert is ``missing``."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return missing


def _price(value: object) -> float | None:
    """A usage row's ``cost_usd`` as a float; ``None`` (unpriced) for one that is not a number."""
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


class SpendCapReached(BaseException):
    """The run hit its ``--max-usd`` cap, or made a call whose price is unknown.

    A ``BaseException``, like ``KeyboardInterrupt``: the stages treat a failed call as a
    missing datum and catch ``Exception``, so an ``Exception`` here would be absorbed one
    call at a time and the run would carry on with every later call refused.

    ``reply`` is set when the refused call came back with a reply (a call with no price): that
    call was paid for, so the caller can still score the reply before it stops. ``truncated`` says
    the reply was also cut at the output ceiling: this stop is raised in place of :class:`OutputTruncated`.
    """

    reply: str | None = None
    truncated: bool = False
    #: With a judge panel: the verdicts of the judges graded before this stop, by model.
    panel_votes: dict[str, str] | None = None

    def __init__(self, message: str, *, reason: str = "spend_cap") -> None:
        super().__init__(message)
        # "spend_cap" for the limit; "no_price" for a call the cap cannot meter.
        self.reason = reason


@dataclass(kw_only=True, init=False)
class SpendCap(Spend):
    """One USD limit shared by every client of one run.

    Checked before each call, so the run can overshoot by the calls already in flight
    (``--concurrency``). A call that comes back without a price stops the run: the cap
    cannot be enforced on spend it cannot see. A reply replayed from the response cache
    (``DOKIMI_RESPONSE_CACHE``) costs nothing now and counts as $0, so a stopped run
    resumed with the same cap spends only on the calls it had not made.
    """

    limit_usd: float
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def __init__(self, limit_usd: float, spent_usd: float = 0.0, calls: int = 0, **counts: int) -> None:
        # Positional as before the move to Spend: SpendCap(limit_usd, spent_usd, calls).
        super().__init__(spent_usd=spent_usd, calls=calls, **counts)
        self.limit_usd = limit_usd
        self._lock = threading.Lock()

    def before_call(self, model: str) -> None:
        with self._lock:
            # An unpriced call stops every worker, not just the one whose call raised.
            if self.unpriced_calls:
                raise SpendCapReached(
                    f"the run stopped on a call with no known price; not calling {model}. "
                    "Drop --max-usd to run unmetered",
                    reason="no_price",
                )
            if self.spent_usd >= self.limit_usd:
                raise SpendCapReached(
                    f"spend cap reached: ${self.spent_usd:.2f} of ${self.limit_usd:.2f} "
                    f"after {self.calls} calls (next call: {model}). "
                    "Raise --max-usd, or measure less (a cheaper --tier, a lower --max-atoms)"
                )

    def record(self, usage: UsageRecord | None, model: str) -> None:
        with self._lock:
            unpriced = self.unpriced_calls
            self.add_call(usage)
            if self.unpriced_calls > unpriced:
                raise SpendCapReached(
                    f"no price is known for {model}, so --max-usd cannot be enforced; "
                    f"stopped after {self.calls} calls. Drop --max-usd to run unmetered",
                    reason="no_price",
                )
