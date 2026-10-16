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
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from dokimi.eval.binary_stats import exact_binomial_two_sided
from dokimi.eval.judge_context import SOURCE_STORED, JudgeContextStore
from dokimi.eval.stats import adjust_pvalues, mean_ci
from dokimi.utils import digest_text

ALPHA = 0.05

#: Scores (library, question, answer, ground_truth, context) -> aggregate.
Scorer = Callable[[str, str, str, Optional[str], str], float]


@dataclass
class Measure:
    """One judge or axis: per (question_id, arm) score."""

    name: str
    kind: str  # "aggregate" | "grounded" | "pairwise" | "coverage"
    scores: Dict[Tuple[str, str], Optional[float]] = field(default_factory=dict)
    #: "PASS" / "FAIL" from this judge's own judge-validity run; None = not validated.
    gate: Optional[str] = None
    #: "stored" when scores have judge_context_source==SOURCE_STORED or store has material; "none" otherwise.
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


def stored_label(data: Dict[str, Any]) -> str:
    """``provider/model (stored)`` from the file's judge fields, as ``--judges`` and
    ``judge-validity`` label the same judge; ``stored judge`` when the file does not
    say. Named that way, a validity report for the judge reaches its stored scores
    and a stored-plus-re-scored verdict from one judge reads "(one judge)"."""
    provider, model = data.get("judge_provider"), data.get("judge_model")
    return f"{provider}/{model}{STORED}" if provider and model else "stored judge"


def unreproducible_arms(data: Dict[str, Any], *, with_material: bool = False) -> List[str]:
    """Arms whose stored scores saw treatment material the other measures will not.

    ``with_material=True`` is for re-scored judges only: they are given what the
    artifact's ``judge_materials`` holds (:meth:`JudgeContextStore.from_artifact`),
    so an arm is reproducible when every stored score's material is in the file.
    Pairwise and checklist judges are shown the answers alone, so for them every
    arm the stored judge saw material for stays unreproducible.

    With ``judge_context``, the arms run stores its judge's context material
    in the artifact (:mod:`dokimi.eval.judge_context`), so a re-scored judge
    reading the file sees the same material. In review that was 28 characters
    of context against 11,079, and the same judge on the same answers read
    +24.2 (p 0.0006) one way and +1.4 (p 0.65) the other. A baseline arm has
    no material, so both sides see the same thing.
    """
    kinds = {
        arm: (body.get("metadata") or {}).get("arm_kind")
        for r in data.get("answers") or [] for arm, body in (r.get("arms") or {}).items()
        if isinstance(body, dict)
    }
    materials = data.get("judge_materials") if with_material else None
    # Only material that hashes to its digest: JudgeContextStore.from_artifact skips the rest.
    held = (
        {d for d, t in materials.items() if isinstance(t, str) and digest_text(t) == d}
        if isinstance(materials, dict) else set()
    )
    return sorted({
        arm for ev in data.get("evaluations") or [] for arm, s in (ev.get("scores") or {}).items()
        if isinstance(s, dict) and s.get("judge_context_source") == SOURCE_STORED
        and kinds.get(arm) != "baseline"
        and s.get("judge_context_sha256") not in held
    })


def stored_measure(data: Dict[str, Any], name: Optional[str] = None) -> Optional[Measure]:
    """The aggregate the arms run itself recorded, if it was judged."""
    m = Measure(name=name or stored_label(data), kind="aggregate", context="stored")
    for ev in data.get("evaluations") or []:
        for arm, s in (ev.get("scores") or {}).items():
            if isinstance(s, dict) and s.get("aggregate") is not None:
                m.scores[(ev["question_id"], arm)] = float(s["aggregate"])
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


def score_measure(
    records: Sequence[Dict[str, Any]], scorer: Scorer, *, name: str, library: str,
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
    jobs = [
        (r["question_id"], arm, r.get("question_text", ""), body["answer"],
         store.resolve_with_source(r["question_id"], arm, body)[0])
        for r in records for arm, body in (r.get("arms") or {}).items()
        if isinstance(body, dict) and body.get("answer") and (arms is None or arm in arms)
    ]

    def one(job):
        qid, arm, question, answer, context = job
        try:
            return (qid, arm), float(
                scorer(library, question, answer, (ground_truth or {}).get(qid), context)
            )
        except Exception:  # an unscored answer drops out of the pairing, it does not kill the run
            return (qid, arm), None

    has_material = any(store.materials().values()) if store else False
    m = Measure(name=name, kind="grounded" if ground_truth else "aggregate", context="stored" if has_material else "none")
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

        ``validity`` holds ``eval judge-validity`` summaries. A summary marks
        the absolute or pairwise measure of the same judge label PASS or FAIL.
        A FAIL also reaches that judge's grounded and checklist-coverage
        measures — the gate tested neither, but a judge that cannot order
        damaged answers is not trusted to vote just because it was handed
        reference points or a list of them. A PASS does not carry over: those
        measures stay unvalidated.
        """
        unmatched = []
        for v in validity:
            passed = bool(v.get("passed"))
            v_context = v.get("context", "none")
            v_judge = v.get("judge")
            hits = 0
            for m in self.measures:
                m_name = m.name.removesuffix(STORED)
                if m_name != v_judge:
                    continue
                # Match on (label, context): stored-context validity gates measures with context="stored",
                # none-context validity gates measures with context="none".
                if m.context != v_context:
                    # Mismatch: skip this measure
                    continue
                if m.kind in ("aggregate", "pairwise"):
                    m.gate = "PASS" if passed else "FAIL"
                    hits += 1
                elif not passed:
                    # Independent of whether the same judge also has an aggregate measure
                    # in this report: --grounded without --both, or --checklist alone, leaves
                    # only these, and a FAIL has to reach them all the same.
                    m.gate = "FAIL"
                    hits += 1
            if not hits:
                unmatched.append(str(v_judge))
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
