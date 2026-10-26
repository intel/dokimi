# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Is an arm's verdict a property of the arm, or of the judge that scored it?

An arms report states one delta from one judge. On the perf golden set that
delta changed sign with the judge: the stored run said the skill arm beat
baseline on 54 questions and lost 15 (p<0.001); Haiku 4.5, scoring the same
answers with the same prompt, said it lost 50-29 (p=0.024); gpt-oss-120b said
44-32 (p=0.21). Answer length does not explain the gap (|r| <= 0.18 for every
judge). Per question, two different judges agree on the direction 49-73% of
the time — about as often as one judge agrees with its own re-draw (68-76%) —
so per-question verdicts are noise and only the per-arm count means anything.
Nothing in the report shows that; a judge panel averages the disagreement away.

This scores the same arms with several judges (and, optionally, takes
required-point coverage from :mod:`dokimi.eval.checklist` as further
measures) and labels each arm by whether the *significant* measures agree.
"Significant" is after a Holm correction over the measures that vote on that
arm: with six to eight measures at α=0.05, one raw p < 0.05 is far more likely
than 5% on an arm with no effect.

* ``CONSISTENT +`` / ``CONSISTENT −`` — at least one measure significant, none
  significant the other way, every point estimate on the same side.
* ``LEANING +`` / ``LEANING −`` — significant one way, none significant the
  other way, but some point estimates disagree.
* ``JUDGE-DEPENDENT`` — significant in both directions. Do not ship on it.
* ``NO DETECTABLE EFFECT`` — no measure significant.
* ``NOT MEASURED`` — no measure that votes scored a single question of the arm
  against baseline (every call errored, or every judge is gated out). It is not
  "no effect": nothing was compared.

A label whose significant measures all rest on one judge says so —
``CONSISTENT + (one judge)`` — because then it is one judge's verdict, not
agreement across judges. ``pairwise X → Y`` reuses X's decided verdicts, so it
counts as X's for this check, and X's absolute and grounded measures are X's
too.

A judge's verdict should only count if the judge can order answers whose order
is known. Pass ``eval judge-validity`` reports with :meth:`CrossJudgeReport.
apply_validity` and every measure from a judge that FAILED its gate — its
grounded scoring included — is shown but left out of the label. With
``require_validated`` only judges that PASSED vote. On the Opus-answer perf run
that is the difference between JUDGE-DEPENDENT (an unvalidated stored judge
+3.2, 60/15, against a gate-passing pairwise judge, 27/49) and CONSISTENT −
(one judge). Neither is evidence the gate-passing judge is right on arm
comparisons; it is evidence the other one was never checked.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

from dokimi.eval.binary_stats import exact_binomial_two_sided
from dokimi.eval.judge_context import SOURCE_STORED, JudgeContextStore
from dokimi.eval.question_text import require_question_text
from dokimi.records import Answer, Record, RecordError, RecordLike, Run, arm_answers, arm_scores
from dokimi.records import record as as_record
from dokimi.records import records as as_records
from dokimi.eval.stats import adjust_pvalues, mean_ci

ALPHA = 0.05

#: Scores (library, question, answer, ground_truth, context) -> aggregate.
Scorer = Callable[[str, str, str, Optional[str], str], float]


@dataclass
class Measure:
    """One judge or axis: per (question_id, arm) score."""

    name: str
    kind: str  # "aggregate" | "grounded" | "pairwise" | "coverage"
    scores: Dict[Tuple[Union[str, int], str], Optional[float]] = field(default_factory=dict)
    #: "PASS" / "FAIL" from this judge's own judge-validity run; None = not validated.
    gate: Optional[str] = None
    #: "stored" when every score saw the stored material, "none" when none did,
    #: "mixed" when a stored measure's scores differ (no PASS validates it).
    context: str = "none"

    def versus(self, arm: str, baseline: str) -> Dict[str, Any]:
        pairs = [
            (v, b) for (q, a), v in self.scores.items()
            if a == arm and v is not None and (b := self.scores.get((q, baseline))) is not None
        ]
        wins = sum(1 for v, b in pairs if v > b)
        losses = sum(1 for v, b in pairs if v < b)
        delta = mean_ci([v - b for v, b in pairs])
        p = exact_binomial_two_sided(wins, wins + losses)
        return {
            "measure": self.name, "kind": self.kind, "gate": self.gate, "n": len(pairs), "delta": delta,
            "wins": wins, "losses": losses, "ties": len(pairs) - wins - losses, "p_sign": p,
            # Raw; label() replaces it with the Holm-adjusted call over the voting measures.
            "significant": p is not None and p < ALPHA,
            "sign": 0 if not pairs or delta["mean"] in (None, 0) else (1 if delta["mean"] > 0 else -1),
        }


def votes(row: Dict[str, Any], *, require_validated: bool = False) -> bool:
    """Never a FAILed judge; with ``require_validated``, only judges that PASSED."""
    return row.get("gate") == "PASS" if require_validated else row.get("gate") != "FAIL"


def apply_holm(rows: Sequence[Dict[str, Any]], *, require_validated: bool = False) -> None:
    """Set ``votes``, ``p_holm`` and ``significant`` (Holm-adjusted, voters only) on each row."""
    voters = [r for r in rows if votes(r, require_validated=require_validated)]
    raw = [r["p_sign"] for r in voters]
    # Decided on the unrounded adjusted p; the 4-decimal one is for display.
    # Three tests at p=0.0166662 have Holm p 0.0499986, which displays as 0.05.
    exact = dict(zip(map(id, voters), adjust_pvalues(raw, method="holm", rounded=False), strict=True))
    for r, p in zip(voters, adjust_pvalues(raw, method="holm"), strict=True):
        r["p_holm"] = p
    for r in rows:
        r["votes"] = id(r) in exact
        r.setdefault("p_holm", None)
        p_exact = exact.get(id(r))
        r["significant"] = r["votes"] and p_exact is not None and p_exact < ALPHA


#: Marks the measure read from the answers file's own evaluations.
STORED = " (stored)"


def judge_of(measure: str) -> str:
    """The judge a measure ultimately rests on. ``X pairwise → Y`` reuses X's decided
    verdicts, so it counts as X's for the one-judge check, not as a second judge;
    ``X (stored)`` is X's scores as the arms run recorded them."""
    return measure.removesuffix(STORED).split(" → ")[0].removesuffix(" pairwise")


def label(rows: Sequence[Dict[str, Any]], *, require_validated: bool = False) -> str:
    """Verdict over the rows allowed to vote (see :func:`votes`), on Holm-adjusted p."""
    apply_holm(rows, require_validated=require_validated)
    rows = [r for r in rows if r["votes"]]
    if not any(r.get("n", 1) for r in rows):  # no voter compared a single question
        return "NOT MEASURED"
    sig = {r["sign"] for r in rows if r["significant"] and r["sign"]}
    if not sig:
        return "NO DETECTABLE EFFECT"
    if len(sig) == 2:
        return "JUDGE-DEPENDENT"
    direction = sig.pop()
    arrow = "+" if direction > 0 else "−"
    same = all(r["sign"] in (0, direction) for r in rows)
    single = len({judge_of(r.get("measure") or str(id(r))) for r in rows if r["significant"]}) == 1
    return f"{'CONSISTENT' if same else 'LEANING'} {arrow}" + (" (one judge)" if single else "")


def stored_label(data: Union[Run, Dict[str, Any]]) -> str:
    """``provider/model (stored)`` from the file's judge fields, as ``--judges`` and
    ``judge-validity`` label the same judge; ``stored judge`` when the file does not
    say. Named that way, a validity report for the judge reaches its stored scores
    and a stored-plus-re-scored verdict from one judge reads "(one judge)"."""
    meta = data.meta if isinstance(data, Run) else data
    provider, model = meta.get("judge_provider"), meta.get("judge_model")
    return f"{provider}/{model}{STORED}" if provider and model else "stored judge"


def _unjoined(data: Dict[str, Any], key: str, *, skip_malformed: bool = False) -> Sequence[Record]:
    """The *key* (``answers`` or ``evaluations``) records of an arms run, not joined.

    Neither caller needs a join, and a join refuses repeated question ids, which
    older arms files hold (the runner's ``unknown_q`` fallback id). With
    *skip_malformed*, a record that does not parse is skipped instead of raised
    on (the reproducibility gate only looks for stored-material stamps).

    Takes the file's dict only: a :class:`Run` does not say which list it was
    loaded from, so reading ``key`` from it would silently return the wrong records.
    """
    if not isinstance(data, dict):
        raise TypeError(f"expected an arms file's dict, got {type(data).__name__}")
    items = data.get(key)
    if not isinstance(items, list):
        return []
    if not skip_malformed:
        return as_records(items, source=f"arms run {key}", kind=key)
    out: List[Record] = []
    for i, item in enumerate(items):
        try:
            out.append(as_record(item, source=f"arms run {key}", index=i, kind=key))
        except RecordError:
            continue
    return out


def unreproducible_arms(data: Dict[str, Any]) -> List[str]:
    """Arms whose stored scores saw treatment material the other measures will not.

    This is the gate for pairwise and checklist judges, which are shown the answers
    alone, so every arm the stored judge saw material for is unreproducible. A
    re-scored judge (``cross-judge --judges``) is gated per question instead, on
    what :meth:`JudgeContextStore.from_artifact` serves it.

    The arms run stores its judge's view of treatment material in ``judge_materials``
    (:mod:`dokimi.eval.judge_context`). ``cross-judge --judges`` gives its judge
    that same material by using the artifact's ``judge_materials``. Pairwise and
    checklist judges see the answers alone. In review, the same judge on the same
    answers read +24.2 (p 0.0006) with the full material (11,079 chars) and +1.4
    (p 0.65) with the 28-char snippet. A baseline arm has no material, so all judges
    see the same thing.
    """
    kinds = {
        arm: (a.raw.get("metadata") or {}).get("arm_kind")
        for r in _unjoined(data, "answers", skip_malformed=True) for arm, a in arm_answers(r).items()
    }
    return sorted({
        arm for r in _unjoined(data, "evaluations", skip_malformed=True) for arm, s in arm_scores(r).items()
        if s.raw.get("judge_context_source") == SOURCE_STORED
        and kinds.get(arm) != "baseline"
    })


def stored_measure(data: Dict[str, Any], name: Optional[str] = None) -> Optional[Measure]:
    """The aggregate the arms run itself recorded in its ``evaluations``, if it was judged.

    Only ``evaluations[].scores``: a legacy inline ``arms[name].evaluation`` and a
    binary file's condition scores are not this measure.
    """
    m = Measure(name=name or stored_label(data), kind="aggregate")
    sources = set()
    for r in _unjoined(data, "evaluations"):
        for arm, s in arm_scores(r).items():
            if s.aggregate is not None:
                m.scores[(r.question_id, arm)] = float(s.aggregate)
                sources.add(s.raw.get("judge_context_source") == SOURCE_STORED)
    # Per score: "stored" only when every score saw the stored material, "none"
    # when none did (snippet_fallback, pre-judge_context files). A file mixing
    # both is "mixed": no single-context PASS describes it, so none validates it
    # (a FAIL of the judge still reaches it).
    m.context = {frozenset({True}): "stored", frozenset({False}): "none"}.get(frozenset(sources), "mixed")
    return m if m.scores else None


def coverage_measure(checklist: Dict[str, Any], name: str) -> Measure:
    m = Measure(name=name, kind="coverage")
    for q in checklist.get("questions") or []:
        for arm, v in (q.get("arms") or {}).items():
            m.scores[(q["question_id"], arm)] = v.get("coverage")
    return m


def pairwise_measure(pairwise: Dict[str, Any], name: str, baseline_arm: str) -> Measure:
    """An `eval pairwise` report as a measure: the arm scores its verdict, the baseline 0."""
    m = Measure(name=name, kind="pairwise")
    for arm, by_q in (pairwise.get("verdicts") or {}).items():
        for qid, v in by_q.items():
            m.scores[(qid, arm)] = float(v["score"])
            m.scores[(qid, baseline_arm)] = 0.0
    return m


def scored_answers(
    records: Sequence[RecordLike], arms: Optional[Sequence[str]] = None,
) -> List[Tuple[Record, str, Answer, str]]:
    """The (record, arm, answer, question) answers :func:`score_measure` sends to a judge.

    Pure: no judge call. Raises :class:`EmptyQuestionError` for a record with an
    answer to score and no question text; a record with only empty answers is
    skipped, not refused.
    """
    return [
        (r, arm, a, require_question_text(r.raw, i))
        for i, r in enumerate(as_records(records, source="answers", kind="answers"))
        for arm, a in arm_answers(r).items()
        if a.text and (arms is None or arm in arms)
    ]


def score_measure(
    records: Sequence[RecordLike], scorer: Scorer, *, name: str, library: str,
    arms: Optional[Sequence[str]] = None, concurrency: int = 1,
    progress: Optional[Callable[[int, int], None]] = None,
    ground_truth: Optional[Dict[str, str]] = None,
    store: Optional[JudgeContextStore] = None,
) -> Measure:
    """Score every answer; with ``ground_truth`` (question_id -> text), grounded.

    Grounding in the golden set's expected points narrowed the spread across
    judges: on the perf skill arm, Haiku, gpt-oss and Sonnet 5 spanned
    -3.4..+2.3 ungrounded (only Haiku significant, raw p=0.024, against the arm)
    and +0.3..+2.3 grounded (none against it).

    Each answer's context comes from the arms run's own resolver
    (:meth:`~dokimi.eval.judge_context.JudgeContextStore.resolve_with_source`)
    over *store* — pass ``JudgeContextStore.from_artifact(data)`` and the judge
    sees what the arms run's judge saw. Answers whose material the file does not
    hold (or with no store) fall back to the arm's report snippets, which is
    what a run before ``judge_context`` showed its judge.
    """
    store = store if store is not None else JudgeContextStore()
    jobs = []
    stored_flags: set = set()  # per answer: True if the judge saw stored material
    for r, arm, a, question in scored_answers(records, arms):
        context, source = store.resolve_with_source(r.question_id, arm, dict(a.raw))
        stored_flags.add(source == SOURCE_STORED)
        jobs.append((r.question_id, arm, question, a.text, context))

    def one(job):
        qid, arm, question, answer, context = job
        try:
            return (qid, arm), float(
                scorer(library, question, answer, (ground_truth or {}).get(qid), context)
            )
        except Exception:  # an unscored answer drops out of the pairing, it does not kill the run
            return (qid, arm), None

    if not stored_flags:
        context_label = "none"
    elif stored_flags == {True}:
        context_label = "stored"
    elif stored_flags == {False}:
        context_label = "none"
    else:
        context_label = "mixed"  # some answers fell back to snippets: no single-context PASS describes it
    m = Measure(name=name, kind="grounded" if ground_truth else "aggregate", context=context_label)
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        for i, (key, value) in enumerate(pool.map(one, jobs), 1):
            m.scores[key] = value
            if progress:
                progress(i, len(jobs))
    return m


def _short(name: str) -> str:
    """Display form of a judge label: drop provider prefixes and vendor namespaces."""
    name = re.sub(r"\b[a-z][a-z-]*/", "", name)
    return re.sub(r"\b(us\.anthropic\.claude-|openai\.)", "", name)


@dataclass
class CrossJudgeReport:
    baseline_arm: str
    measures: List[Measure] = field(default_factory=list)
    #: Only gate-passing measures vote. On the Opus-answer perf run the stored
    #: (never validated) judge said +3.2 and the only gate-passing judge −0.27,
    #: which reads JUDGE-DEPENDENT unless the unvalidated vote is dropped.
    require_validated: bool = False

    def apply_validity(self, validity: Sequence[Dict[str, Any]]) -> List[str]:
        """Mark measures with their judge's gate result; returns validity labels that matched nothing.

        ``validity`` holds ``eval judge-validity`` summaries. A PASS marks the
        absolute or pairwise measure of the same judge label and the same
        context (``none`` or ``stored``; a report without ``context`` counts as
        ``none``): the gate only validated the setting it ran in. A FAIL reaches
        every measure of that judge label, in any context, including its
        grounded and checklist-coverage measures — a judge that cannot order
        damaged answers in one setting is not trusted to vote in another. A
        PASS does not carry over to grounded or coverage measures: those stay
        unvalidated. A label that matched a measure only in another context is
        returned with a note naming both contexts. A FAIL is never overwritten
        by a PASS, so the result does not depend on the order of ``validity``.
        """
        unmatched = []
        for v in validity:
            passed = bool(v.get("passed"))
            v_context = v.get("context", "none")
            v_judge = v.get("judge")
            hits = 0
            other_contexts = set()
            for m in self.measures:
                m_name = m.name.removesuffix(STORED)
                if m_name != v_judge:
                    continue
                if not passed:
                    # Every kind and context, independent of whether the same judge also has
                    # an aggregate measure here: --grounded without --both, or --checklist
                    # alone, leaves only those, and a FAIL has to reach them all the same.
                    m.gate = "FAIL"
                    hits += 1
                elif m.kind not in ("aggregate", "pairwise"):
                    # A PASS never validates grounded or coverage measures, so their context
                    # says nothing about this PASS.
                    continue
                elif m.context != v_context:
                    # A PASS validates only the (label, context) it was measured in.
                    other_contexts.add(m.context)
                elif m.kind in ("aggregate", "pairwise"):
                    # A FAIL is final: a PASS never lifts it, whichever report came first.
                    if m.gate != "FAIL":
                        m.gate = "PASS"
                    hits += 1
            if not hits:
                unmatched.append(
                    f"{v_judge} (report context {v_context}; its measures are "
                    f"{', '.join(sorted(other_contexts))})" if other_contexts else str(v_judge))
        return unmatched

    def summary(self) -> Dict[str, Any]:
        arms = sorted({a for m in self.measures for (_, a) in m.scores} - {self.baseline_arm})
        out = {}
        for arm in arms:
            rows = [m.versus(arm, self.baseline_arm) for m in self.measures]
            out[arm] = {"label": label(rows, require_validated=self.require_validated), "rows": rows}
        return {"baseline_arm": self.baseline_arm, "measures": [m.name for m in self.measures], "arms": out}

    def format_markdown(self) -> str:
        s = self.summary()
        lines = [
            "## Verdict robustness",
            "",
            f"Every arm against `{s['baseline_arm']}`, under each judge and axis. A verdict that holds under "
            "one judge only is a property of that judge. Gate: the judge's own `judge-validity` result; "
            "a measure whose judge failed it (✗) is shown but does not vote"
            + (", and neither does an unvalidated one (—)." if self.require_validated else "."),
            "",
            "| Arm | Verdict | Measure | Gate | Δ | 95% CI | W/L/T | p (sign) | p (Holm) |",
            "| --- | --- | --- | :---: | ---: | ---: | ---: | ---: | ---: |",
        ]
        for arm, r in s["arms"].items():
            for i, row in enumerate(r["rows"]):
                d = row["delta"]
                fmt = "{:+.3f}" if row["kind"] in ("coverage", "pairwise") else "{:+.1f}"
                ci = "" if d["lo"] is None else f"[{fmt.format(d['lo'])}, {fmt.format(d['hi'])}]"
                p = "" if row["p_sign"] is None else f"{row['p_sign']:.3g}"
                if not row["votes"]:
                    ph = "does not vote"
                else:
                    ph = "" if row["p_holm"] is None else f"{row['p_holm']:.3g}" + (" *" if row["significant"] else "")
                lines.append(
                    f"| {'`' + arm + '`' if i == 0 else ''} | {'**' + r['label'] + '**' if i == 0 else ''} | "
                    f"{row['kind']} · {_short(row['measure'])} | "
                    f"{ {'PASS': '✓', 'FAIL': '✗'}.get(row['gate'] or '', '—') } | "
                    f"{fmt.format(d['mean']) if d['mean'] is not None else '—'} | {ci} | "
                    f"{row['wins']}/{row['losses']}/{row['ties']} | {p} | {ph} |"
                )
        lines += ["", "\\* p < 0.05 after Holm over the measures that vote on the arm; two-sided sign test "
                  "over questions."]
        return "\n".join(lines) + "\n"

    def to_dict(self) -> Dict[str, Any]:
        return {"schema_version": "cross_judge.v0", "summary": self.summary()}
