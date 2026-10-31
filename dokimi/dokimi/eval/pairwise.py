# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Pairwise judging: show the judge both answers and ask which is better.

The absolute judge scores each answer alone on 0-100 and the comparison is made
afterwards, by subtracting. That throws away the one thing a comparison has
that a score does not — the two answers side by side — and it is where the
absolute judge's failures sit. On the judge-validity pairs (40 perf bases, 315
graded pairs, truth known by construction), gpt-oss-120b:

=========================  ======  ===========  ==========  =============
mode                       pooled  strip_code   wrong_nums  contradiction
=========================  ======  ===========  ==========  =============
absolute, shipped prompt    0.883        0.80        0.81           0.74
absolute 0-100, this text   0.895        0.71        0.72           0.95
pairwise, this text         0.962        0.93        0.92           0.96
=========================  ======  ===========  ==========  =============

The middle row is a control: this module's wording, one answer at a time, one
integer out. It changes the output format as well as the wording, so "the mode,
not the wording" is the best reading rather than a clean isolation, but the
variants pairwise fixes (strip_code, wrong_numbers) are the ones the control
does not. Measured cost of the pairwise gate: $0.47 for 710 calls ($0.0013 a
pair). Of the absolute judges measured, only Opus 4.6 also passed (0.964, at
$0.034 a pair, ~26x); Opus 5.5 failed, rating the copy with its code stripped
*higher* (0.45).

Order matters to the judge: swapping A and B flipped or broke the verdict on
5.4% of gpt-oss's graded gate pairs and 21.9% of Haiku's — and on **56%** of
gpt-oss's real skill-vs-baseline comparisons, which are far closer calls than a
damaged copy. A gate pass therefore says the judge can separate clearly
different answers; it does not say its arm verdicts are as reliable. So every
pair is asked in **both orders** and only an order-consistent verdict counts;
any other outcome is a tie. An **order-inconsistent** pair can be escalated to a second, stronger
judge, asked the same way: cheap first, expensive only where the cheap judge
contradicted itself. A pair the cheap judge called TIE in both orders is a
decision, not a failure to decide, and is kept: escalating those too (measured,
40 perf bases, gpt-oss → Opus 5.5) bought 0.986 → 0.992 pooled accuracy for 61
escalated pairs instead of 38 ($3.08 of Opus calls instead of $1.66), and the
strong judge broke the consistent padding ties toward the shorter copy, taking
padding pairs judged a tie from 0.42 to 0.03. Whether preferring the unpadded
copy is wrong is arguable — the prompt says length is not quality — but it is
not what the neutral variant measures. ``escalate_ties=True`` restores it.

Not every model is good at this. Pairwise Haiku 4.5 preferred the
self-contradicting copy outright on 27 of 40 pairs and tied 13 (0.16 on
``contradiction``), so a pairwise judge must pass ``eval judge-validity --mode
pairwise`` like any other.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

from dokimi.eval.binary_stats import exact_binomial_two_sided
from dokimi.llm import ATTEMPTS, Unparseable
from dokimi.judged import judged_reply
from dokimi.eval.question_text import require_question_text
from dokimi.records import RecordLike, arm_answers, records as as_records

PAIRWISE_PROMPT = """You are comparing two answers to the same technical question about __LIBRARY_NAME__.

**Question:** __QUESTION__

**Answer A:**
__ANSWER_A__

**Answer B:**
__ANSWER_B__

Which answer would serve a practitioner better? Judge factual correctness first (including every number, unit, tool and flag it names), then whether it answers the question that was asked, then completeness and how directly it can be applied. Length and polish are not quality.

Reply with exactly one token: A, B, or TIE."""

#: Invokes a model on a prompt, returns its reply text. Called as
#: ``ask(prompt, draw=n, cache_if=check)``: *draw* selects an independent sample
#: of the same request and *cache_if* is the response cache's acceptance test
#: (see :func:`dokimi.caching.keeps`), as ``Judge.llm.invoke`` takes them.
Ask = Callable[..., str]

_CHOICES = ("A", "B", "TIE")


_THINK = re.compile(r"<think(?:ing)?>.*?(?:</think(?:ing)?>|\Z)", re.DOTALL | re.IGNORECASE)
_ARTICLE_TIE = re.compile(r"(?<![A-Za-z])A\s+tie(?![A-Za-z])", re.IGNORECASE)


def standalone_verdict(text: Optional[str], choices: Sequence[str]) -> Optional[str]:
    """The one verdict *text* names, or None.

    A bare reply ("b", "Tie.") is read case-insensitively. Inside prose only
    standalone upper-case tokens count (the article in "...so it is a TIE." is
    not verdict A: 1 of 256 real replies), and only when they are all the same
    one. A reply that names two ("Neither A nor B is wrong, so TIE", "B is
    better than A", the prompt echoed back) has no verdict to read, and neither
    the first nor the last token is reliably it; the caller redraws, unless the
    first line is itself a bare verdict, which an explanation may follow. A
    ``<think>`` block, closed or cut off, is not part of the reply.
    """
    clean = _THINK.sub("", text or "").replace("**", "").strip()
    bare = clean.rstrip(".!").upper()
    if bare in choices:
        return bare
    first = clean.split("\n", 1)[0].strip().rstrip(".!").upper()
    if first in choices:  # a bare verdict line, then an explanation that may name the other answer
        return first
    pattern = "|".join(choices)
    found = set(re.findall(rf"(?<![A-Za-z])({pattern})(?![A-Za-z])", clean))
    return found.pop() if len(found) == 1 else None


def parse_choice(text: Optional[str]) -> Optional[str]:
    """``A``, ``B``, ``TIE`` or None; see :func:`standalone_verdict`."""
    return standalone_verdict(_ARTICLE_TIE.sub("TIE", text or ""), _CHOICES)


@dataclass
class PairVerdict:
    """+1 when the first answer is better, -1 when the second is, 0 for a tie."""

    score: int
    first_order: Optional[str]
    second_order: Optional[str]
    escalated: bool = False


def _prompt(library: str, question: str, a: str, b: str) -> str:
    return (
        PAIRWISE_PROMPT.replace("__LIBRARY_NAME__", library).replace("__QUESTION__", question)
        .replace("__ANSWER_A__", a).replace("__ANSWER_B__", b)
    )


class UnparseableVerdict(ValueError):
    """The judge's reply named none of A, B or TIE."""


def _both_orders(ask: Ask, library: str, question: str, x: str, y: str, *, pinned: bool) -> PairVerdict:
    """Both orders; raises :class:`UnparseableVerdict` rather than reading a non-answer as TIE.

    An empty or truncated reply (a reasoning model with the verdict outside
    ``content``, a max_tokens cutoff) scored as a tie would look like a
    well-behaved, length-neutral judge — 0.5 on graded pairs, "unmoved" on
    padding — and would buy an escalation it did not earn.
    """
    def verdict(a: str, b: str, order: int) -> str:
        try:
            return judged_reply(ask, _prompt(library, question, a, b), parse_choice,
                                pinned=pinned, label=f"Pairwise order {order}")[0]
        except Unparseable as exc:
            raise UnparseableVerdict(
                f"order {order}: no A/B/TIE in {ATTEMPTS} replies, last {str(exc.reply)[:80]!r}"
            ) from exc

    v1 = verdict(x, y, 1)
    v2 = verdict(y, x, 2)
    s1 = {"A": 1, "B": -1}[v1] if v1 in ("A", "B") else 0
    s2 = {"A": -1, "B": 1}[v2] if v2 in ("A", "B") else 0
    return PairVerdict(score=s1 if s1 == s2 else 0, first_order=v1, second_order=v2)


@dataclass
class PairwiseJudge:
    """Both-order A/B judge with optional escalation of undecided pairs."""

    ask: Ask
    escalate: Optional[Ask] = None
    #: Also escalate pairs the cheap judge called TIE in both orders.
    escalate_ties: bool = False
    #: Pinned sampling repeats a redraw's reply, so it is reprompted; unpinned it is a new sample.
    #: See :func:`dokimi.judged.judged_reply`.
    pinned: bool = False

    def compare(self, library: str, question: str, x: str, y: str) -> PairVerdict:
        verdict = _both_orders(self.ask, library, question, x, y, pinned=self.pinned)
        consistent_tie = verdict.first_order == verdict.second_order == "TIE"
        if verdict.score == 0 and self.escalate is not None and (self.escalate_ties or not consistent_tie):
            verdict = _both_orders(self.escalate, library, question, x, y, pinned=self.pinned)
            verdict.escalated = True
        return verdict

    def pair_scorer(self) -> Callable[[Dict[str, Any]], Tuple[float, float]]:
        """Adapt to judge-validity: intact gets 100 / 50 / 0, the damaged copy 50.

        The 50-point spread keeps a tie inside the neutral tolerance (so a
        padding pair judged TIE counts as "did not move") and a decided pair
        far outside it.
        """

        def score(pair: Dict[str, Any]) -> Tuple[float, float]:
            v = self.compare(pair["library"], pair["question"], pair["intact"], pair["degraded"])
            return 50.0 + 50.0 * v.score, 50.0

        return score


@dataclass
class ArmPairwiseReport:
    """Every arm against the baseline, one both-order verdict per question."""

    judge: str
    baseline_arm: str
    #: arm -> question_id -> verdict (arm's answer first)
    verdicts: Dict[str, Dict[str, PairVerdict]] = field(default_factory=dict)
    #: Questions that could not be judged; left out of every count.
    errors: List[Dict[str, str]] = field(default_factory=list)

    def summary(self) -> Dict[str, Any]:
        arms = {}
        for arm, by_q in self.verdicts.items():
            scores = [v.score for v in by_q.values()]
            wins, losses = scores.count(1), scores.count(-1)
            arms[arm] = {
                "n": len(scores), "wins": wins, "losses": losses, "ties": len(scores) - wins - losses,
                "win_rate": (wins + 0.5 * (len(scores) - wins - losses)) / len(scores) if scores else None,
                "p_sign": exact_binomial_two_sided(wins, wins + losses),
                "escalated": sum(v.escalated for v in by_q.values()),
            }
        return {"judge": self.judge, "baseline_arm": self.baseline_arm, "arms": arms, "n_errors": len(self.errors)}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": "pairwise.v0",
            "summary": self.summary(),
            "errors": self.errors,
            "verdicts": {
                arm: {q: {"score": v.score, "first_order": v.first_order, "second_order": v.second_order,
                          "escalated": v.escalated} for q, v in by_q.items()}
                for arm, by_q in self.verdicts.items()
            },
        }

    def format_markdown(self) -> str:
        s = self.summary()
        lines = [
            "## Pairwise comparison",
            "",
            f"Each arm's answer shown next to `{s['baseline_arm']}`'s, in both orders, by `{s['judge']}`; "
            "a verdict counts only when both orders agree.",
            "",
            "| Arm | Better | Worse | Tie | Win rate | p (sign) | Escalated |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
        for arm, r in s["arms"].items():
            p = "" if r["p_sign"] is None else f"{r['p_sign']:.3g}"
            lines.append(f"| `{arm}` | {r['wins']} | {r['losses']} | {r['ties']} | {r['win_rate']:.2f} | {p} | "
                         f"{r['escalated']} |")
        if s["n_errors"]:
            lines += ["", f"{s['n_errors']} comparison(s) failed and are left out; see `errors` in the JSON."]
        return "\n".join(lines) + "\n"


def pairwise_jobs(
    records: Sequence[RecordLike],
    *,
    baseline_arm: str = "baseline",
    arms: Optional[Sequence[str]] = None,
) -> List[Tuple[str, Union[str, int], str, str, str]]:
    """The (arm, question_id, question, answer, baseline answer) comparisons to judge.

    Pure: no judge call. Raises :class:`EmptyQuestionError` for a record that
    would be compared and has no question text; a record that would be skipped
    anyway (no baseline answer, no other arm) is not refused.
    """
    jobs: List[Tuple[str, Union[str, int], str, str, str]] = []
    for i, rec in enumerate(as_records(records, source="answers", kind="answers")):
        by_arm = arm_answers(rec)
        base_answer = by_arm.get(baseline_arm)
        base = base_answer.text if base_answer is not None else None
        if not base:
            continue
        for arm, a in by_arm.items():
            if arm != baseline_arm and a.text and (arms is None or arm in arms):
                jobs.append((arm, rec.question_id, require_question_text(rec.raw, i), a.text, base))
    return jobs


def run_arm_pairwise(
    records: Sequence[RecordLike],
    judge: PairwiseJudge,
    *,
    label: str = "unknown",
    library: str = "the library",
    baseline_arm: str = "baseline",
    arms: Optional[Sequence[str]] = None,
    concurrency: int = 1,
    progress: Optional[Callable[[int, int], None]] = None,
) -> ArmPairwiseReport:
    jobs = pairwise_jobs(records, baseline_arm=baseline_arm, arms=arms)

    def one(job):
        arm, qid, question, answer, base = job
        try:
            return arm, qid, judge.compare(library, question, answer, base)
        except Exception as exc:  # one failed question must not lose the run
            return arm, qid, exc

    report = ArmPairwiseReport(judge=label, baseline_arm=baseline_arm)
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        for i, (arm, qid, verdict) in enumerate(pool.map(one, jobs), 1):
            if isinstance(verdict, Exception):
                report.errors.append({"arm": arm, "question_id": qid, "error": f"{type(verdict).__name__}: {verdict}"[:200]})
            else:
                report.verdicts.setdefault(arm, {})[qid] = verdict
            if progress:
                progress(i, len(jobs))
    return report
