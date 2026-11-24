# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""What a run's LLM calls cost: one accumulator of calls, tokens and dollars, and the cap on it.

Every cost total follows one rule (#229 P2-7). A call replayed from the response cache
(``DOKIMI_RESPONSE_CACHE``) was not paid for by this process: it is counted apart and adds no
tokens and no dollars. The dollar total is known only when every paid call has a price; one
call with no price, or one that returned no usage, makes it unknown, never the priced part.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable
from dataclasses import dataclass, field, fields
from typing import TYPE_CHECKING

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
        self.calls += usage.n_calls
        if usage.replayed:
            self.replayed_calls += usage.n_calls
            return
        self.unpriced_calls += usage.n_calls - usage.priced_calls
        self.spent_usd += usage.cost_usd or 0.0
        self.prompt_tokens += usage.prompt_tokens
        self.completion_tokens += usage.completion_tokens
        self.total_tokens += usage.total_tokens
        self.cache_read_tokens += usage.cache_read_tokens
        self.cache_write_tokens += usage.cache_write_tokens

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


@dataclass(kw_only=True)
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
            self.add(usage)
            if self.unpriced_calls > unpriced:
                raise SpendCapReached(
                    f"no price is known for {model}, so --max-usd cannot be enforced; "
                    f"stopped after {self.calls} calls. Drop --max-usd to run unmetered",
                    reason="no_price",
                )
