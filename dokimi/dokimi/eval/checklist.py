# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Required-point coverage: did the answer make the points a correct answer must make?

The 0-100 judge saturates on good answers: on the perf golden set (Sonnet 4.6
answers) the three arms averaged 94.8-96.5 under the stored judge, where a skill
adding real content showed up as +1.7 points. The golden set already says what a correct answer contains — each
question's ``expected_topics`` — so this asks a narrower question per point,
one the judge can answer without a scale to saturate: *is this point in the
answer?*

Each point is asked twice, in opposite polarities ("is it covered?" and "is it
absent?"). A judge with a yes- or no-bias answers both the same way, and that
item is counted as unsure (0.5) rather than as whichever way the bias leans.
Haiku is measurably no-biased here; a small local model was yes-biased; the
average of the two polarities cancels either. On model tiers it resolved
opus > sonnet with Haiku asking (32-13, p=0.007) but not with gpt-oss (22-21),
while both judges' aggregates did (48-23, 50-26) — so it is not the more
sensitive axis. Its sign was stable across the two judges on skill-vs-baseline,
and its use is the list of points an arm added or lost.

What it does not do: judge correctness of anything outside the listed points,
or notice a point stated wrongly in confident prose (``wrong_numbers`` is 0.57
here, worse than the aggregate judge). It is a second axis next to the
aggregate, not a replacement, and it is only as good as the points it is given.
"""

from __future__ import annotations

import ast
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from dokimi.eval.binary_stats import exact_binomial_two_sided
from dokimi.eval.pairwise import ask_parsed, standalone_verdict
from dokimi.eval.question_text import require_question_text
from dokimi.records import RecordLike, arm_answers, records as as_records
from dokimi.eval.stats import mean_ci
from dokimi.utils import question_id

logger = logging.getLogger(__name__)

_TEMPLATE = (
    "Here is a technical question and an answer to it.\n\nQUESTION:\n{question}\n\n"
    "ANSWER:\n{answer}\n\nRequired point:\n{point}\n\n{ask} Reply with exactly one word: YES or NO."
)
ASK_COVERED = "Does the ANSWER cover this required point?"
ASK_ABSENT = "Is this required point ABSENT from the ANSWER (not stated, or only gestured at)?"


#: A coverage difference smaller than this on one question is a tie. One point
#: of three flipped is 0.33; a lone polarity flip is 0.17.
TIE_EPSILON = 1e-9

#: Asks the model; returns its raw reply. Same contract as
#: :data:`dokimi.eval.pairwise.Ask` (``draw`` and ``cache_if`` keywords).
Ask = Callable[..., str]


def parse_yes_no(text: Optional[str]) -> Optional[bool]:
    """The YES/NO verdict in the reply, or None when there is neither.

    A bare reply ("yes", "No.") is read case-insensitively; inside prose only
    a standalone upper-case YES or NO counts, so "there is no mention ... YES"
    is not read as NO, and a reply naming both ("NO ... YES") is not read at all
    (see :func:`~dokimi.eval.pairwise.standalone_verdict`).
    """
    verdict = standalone_verdict(text, ("YES", "NO"))
    return None if verdict is None else verdict == "YES"


def points_of(question: Dict[str, Any]) -> List[str]:
    """A question's required points. Accepts a list or a stringified list."""
    raw = question.get("expected_topics") or []
    if isinstance(raw, str):
        try:
            raw = ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            raw = [raw]
    return [str(p).strip() for p in raw if str(p).strip()]


@dataclass
class PointVerdict:
    point: str
    covered: Optional[bool]  # answer to "is it covered?"
    absent: Optional[bool]   # answer to "is it absent?"

    @property
    def score(self) -> Optional[float]:
        """1 covered, 0 missing, 0.5 when the two polarities disagree."""
        if self.covered is None or self.absent is None:
            return None
        if self.covered != self.absent:
            return 1.0 if self.covered else 0.0
        return 0.5

    @property
    def consistent(self) -> Optional[bool]:
        if self.covered is None or self.absent is None:
            return None
        return self.covered != self.absent


class JudgeFailed(RuntimeError):
    """A judge call raised, or its reply had no YES or NO after every draw (``strict`` checks only)."""


def check_answer(ask: Ask, question: str, answer: str, points: Sequence[str], *,
                 strict: bool = False) -> List[PointVerdict]:
    """Both polarities for every point.

    By default a failed call or an unparseable reply leaves its point unscored. With ``strict``
    it raises :class:`JudgeFailed` instead, for a caller that must not grade an answer on the
    points that did parse.
    """
    def one(point: str, q: str) -> Optional[bool]:
        try:
            # Retried under new draws, and an unparseable reply is never cached.
            verdict, _ = ask_parsed(ask, _TEMPLATE.format(question=question, answer=answer, point=point, ask=q),
                                    parse_yes_no)
        except Exception as exc:  # one failed call is an unparsed point, not a lost run
            if strict:
                raise JudgeFailed(str(exc) or type(exc).__name__) from exc
            logger.warning("Checklist call failed, point left unscored: %s", exc)
            return None
        if verdict is None and strict:
            raise JudgeFailed("no YES or NO in the reply")
        return verdict

    return [
        PointVerdict(point=p, covered=one(p, ASK_COVERED), absent=one(p, ASK_ABSENT)) for p in points
    ]


def coverage(verdicts: Sequence[PointVerdict]) -> Optional[float]:
    scores = [v.score for v in verdicts if v.score is not None]
    return sum(scores) / len(scores) if scores else None


@dataclass
class ChecklistReport:
    judge: str
    baseline_arm: str
    #: question_id -> arm -> verdicts
    verdicts: Dict[str, Dict[str, List[PointVerdict]]] = field(default_factory=dict)
    questions: Dict[str, str] = field(default_factory=dict)
    n_without_points: int = 0

    @property
    def arms(self) -> List[str]:
        seen: List[str] = []
        for by_arm in self.verdicts.values():
            seen += [a for a in by_arm if a not in seen]
        return seen

    def _cov(self, qid: str, arm: str) -> Optional[float]:
        v = self.verdicts.get(qid, {}).get(arm)
        return coverage(v) if v else None

    def summary(self) -> Dict[str, Any]:
        arms: Dict[str, Any] = {}
        for arm in self.arms:
            covs = [c for q in self.verdicts if (c := self._cov(q, arm)) is not None]
            items = [v for q in self.verdicts for v in self.verdicts[q].get(arm, [])]
            consistent = [v.consistent for v in items if v.consistent is not None]
            row: Dict[str, Any] = {
                "n": len(covs),
                "coverage": mean_ci(covs),
                "full_coverage_rate": sum(1 for c in covs if c == 1.0) / len(covs) if covs else None,
                "consistency": sum(consistent) / len(consistent) if consistent else None,
                "unparsed": sum(1 for v in items if v.score is None),
            }
            if arm != self.baseline_arm:
                pairs = [
                    (a, b) for q in self.verdicts
                    if (a := self._cov(q, arm)) is not None and (b := self._cov(q, self.baseline_arm)) is not None
                ]
                wins = sum(1 for a, b in pairs if a - b > TIE_EPSILON)
                losses = sum(1 for a, b in pairs if b - a > TIE_EPSILON)
                row["vs_baseline"] = {
                    "n": len(pairs),
                    "delta": mean_ci([a - b for a, b in pairs]),
                    "wins": wins,
                    "losses": losses,
                    "ties": len(pairs) - wins - losses,
                    "p_sign": exact_binomial_two_sided(wins, wins + losses),
                }
            arms[arm] = row
        return {
            "judge": self.judge,
            "baseline_arm": self.baseline_arm,
            "n_questions": len(self.verdicts),
            "n_without_points": self.n_without_points,
            "arms": arms,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": "checklist.v0",
            "summary": self.summary(),
            "questions": [
                {
                    "question_id": q,
                    "question_text": self.questions.get(q, ""),
                    "arms": {
                        arm: {
                            "coverage": coverage(vs),
                            "points": [
                                {"point": v.point, "covered": v.covered, "absent": v.absent, "score": v.score}
                                for v in vs
                            ],
                        }
                        for arm, vs in by_arm.items()
                    },
                }
                for q, by_arm in self.verdicts.items()
            ],
        }

    def format_markdown(self, max_rows: int = 15) -> str:
        s = self.summary()
        lines = [
            "## Required-point coverage",
            "",
            f"Each question's `expected_topics` checked one by one by `{s['judge']}`, asked as "
            "*covered?* and as *absent?*; a point where the two disagree counts ½. "
            f"{s['n_questions']} questions"
            + (f" ({s['n_without_points']} skipped: no points)" if s["n_without_points"] else "")
            + ".",
            "",
            "| Arm | Coverage | 95% CI | All points | Δ vs baseline | W/L/T | p (sign) | Consistent |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
        for arm, r in s["arms"].items():
            c = r["coverage"]
            vb = r.get("vs_baseline")
            delta = "(baseline)" if not vb else _fmt(vb["delta"]["mean"], "+.3f")
            wlt = "" if not vb else f"{vb['wins']}/{vb['losses']}/{vb['ties']}"
            p = "" if not vb or vb["p_sign"] is None else f"{vb['p_sign']:.3g}"
            lines.append(
                f"| `{arm}` | {_fmt(c['mean'], '.3f')} | [{_fmt(c['lo'], '.3f')}, {_fmt(c['hi'], '.3f')}] | "
                f"{_fmt(r['full_coverage_rate'], '.0%')} | {delta} | {wlt} | {p} | {_fmt(r['consistency'], '.0%')} |"
            )
        unparsed = sum(r["unparsed"] for r in s["arms"].values())
        if unparsed:
            lines += ["", f"{unparsed} point(s) got no usable reply and are left out (`-` where an arm has none)."]
        lines += ["", "### Points the baseline missed and a treatment made", ""]
        shown = 0
        for q, by_arm in self.verdicts.items():
            base = {v.point: v.score for v in by_arm.get(self.baseline_arm, [])}
            for arm, vs in by_arm.items():
                if arm == self.baseline_arm:
                    continue
                gained = [v.point for v in vs if v.score == 1.0 and base.get(v.point) == 0.0]
                lost = [v.point for v in vs if v.score == 0.0 and base.get(v.point) == 1.0]
                for point in gained:
                    if shown < max_rows:
                        lines.append(f"- **+** `{arm}` · {q}: {point[:140]}")
                    shown += 1
                for point in lost:
                    if shown < max_rows:
                        lines.append(f"- **−** `{arm}` · {q}: {point[:140]}")
                    shown += 1
        if shown > max_rows:
            lines.append(f"- … {shown - max_rows} more in the JSON report")
        if not shown:
            lines.append("- none")
        return "\n".join(lines) + "\n"


def _fmt(x: Optional[float], spec: str) -> str:
    """``x`` formatted, or ``-`` when nothing of the arm parsed."""
    return "-" if x is None else format(x, spec)


def checklist_jobs(
    records: Sequence[RecordLike],
    points_by_qid: Dict[Any, List[str]],
    *,
    arms: Optional[Sequence[str]] = None,
    report: Optional["ChecklistReport"] = None,
) -> List[Tuple[Any, str, str, str, List[str]]]:
    """The (question_id, arm, question, answer, points) checks to judge.

    Pure apart from filling *report*'s questions and ``n_without_points``: no
    judge call. Raises :class:`EmptyQuestionError` for a record that would be
    checked and has no question text; a record without expected points is
    skipped, not refused.
    """
    jobs: List[Tuple[Any, str, str, str, List[str]]] = []
    for i, rec in enumerate(as_records(records, source="answers", kind="answers")):
        qid: Any = rec.question_id
        points = points_by_qid.get(qid) or []
        if not points:
            if report is not None:
                report.n_without_points += 1
            continue
        pending = [(arm, a.text) for arm, a in arm_answers(rec).items()
                   if a.text and (arms is None or arm in arms)]
        # Only a record that would be judged must carry a question; the others keep what they have.
        question = require_question_text(rec.raw, i) if pending else rec.question_text
        if report is not None:
            report.questions[qid] = question
        for arm, answer in pending:
            jobs.append((qid, arm, question, answer, points))
    return jobs


def run_checklist(
    records: Sequence[RecordLike],
    points_by_qid: Dict[Any, List[str]],
    ask: Ask,
    *,
    judge: str = "unknown",
    baseline_arm: str = "baseline",
    arms: Optional[Sequence[str]] = None,
    concurrency: int = 1,
    progress: Optional[Callable[[int, int], None]] = None,
) -> ChecklistReport:
    """Check every (question, arm) answer in an arms run against its points.

    One call per point and polarity. Asking every point of a question in one
    call per polarity is a third of the price and was measured useless: the
    judge turned lenient (baseline coverage 0.76 -> 0.91, 62 of 81 questions
    tied) and the skill effect vanished (p 0.06 -> 0.36).
    """
    report = ChecklistReport(judge=judge, baseline_arm=baseline_arm)
    jobs = checklist_jobs(records, points_by_qid, arms=arms, report=report)

    def one(job):
        qid, arm, question, answer, points = job
        return qid, arm, check_answer(ask, question, answer, points)

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        for i, (qid, arm, verdicts) in enumerate(pool.map(one, jobs), 1):
            report.verdicts.setdefault(qid, {})[arm] = verdicts
            if progress:
                progress(i, len(jobs))
    return report


def load_points(path: str) -> Dict[str, List[str]]:
    """question_id -> required points, from a questions file."""
    from dokimi.questions.artifact import load_questions

    # Only ids and points are read here, so a record without question text is no fault.
    questions = load_questions(path, validate=False).questions
    return {question_id(q): points_of(q) for q in questions if question_id(q) is not None}
