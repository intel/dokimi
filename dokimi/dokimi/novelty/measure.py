# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Run the paired probes and produce the added / redundant / degraded / still-missing 2×2.

The measurement is one design decision repeated everywhere: **compare, never level**.
Two independent judges in the study agreed on the 6-way verdict scale only 0.664 of the
time but 0.852 on the correct-ish binary collapse, so no conclusion is drawn from the
6-way scale, every rate is reported under both a lenient and a strict collapse, and the
headline is a paired difference in which judge bias cancels within the pair.

Three isolation rules are enforced in code rather than documented:

* A baseline probe receives the question and nothing else — no artifact text, no
  expected answer, no earlier turns. Leakage inflates ``redundant`` and understates
  ``added``, i.e. it makes an artifact look worthless.
* The judge may not be the answering model unless the caller passes
  ``allow_self_judge``, because grading a response with the model that produced it
  inflates the score.
* The answerer's self-reported confidence is recorded and never scored. Measured
  correlation with the graded score was ≤0.454, and as low as 0.018.
"""

from __future__ import annotations

import random
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .atoms import Atom
from .client import LLMClient
from .probes import Probe

# Judge verdict scale. Deliberately finer than the collapses below so a rater
# disagreement is visible as a confusion rather than hidden inside a boolean.
VERDICTS = (
    "correct_complete",
    "correct_missing_qualifier",
    "partially_correct",
    "wrong_other",
    "wrong_reversed",
    "abstained",
)
STRICT_CORRECT = frozenset({"correct_complete"})
LENIENT_CORRECT = frozenset({
    "correct_complete", "correct_missing_qualifier", "partially_correct",
})
SCORINGS = ("lenient", "strict")
# Not a verdict: the answer or the grade call failed, so the probe has no datum and its
# pair is dropped (counted in ``n_unpaired_dropped``) rather than scored as wrong.
ERRORED = "error"

# Arm names. `no_artifact` is the baseline; `full` is the artifact as written; the two
# controls exist because a with-artifact gain can otherwise be a prompt-length artifact
# (placebo) or a narrative that is not actually load-bearing (shuffled).
BASELINE_ARM = "no_artifact"
ARMS = (BASELINE_ARM, "full", "placebo", "shuffled")

_ANSWER_SYSTEM = (
    "Answer the question directly and concisely. If you do not know, say you do not know "
    "rather than guessing. End with a final line 'confidence: <0-1>'."
)

_JUDGE_PROMPT = """\
Grade the RESPONSE against the EXPECTED answer.

QUESTION: {question}
EXPECTED: {expected}
RESPONSE: {response}

Choose exactly one verdict:
- correct_complete: same facts, same qualifiers, same numbers/versions.
- correct_missing_qualifier: right claim, but a qualifier, version bound or exact value
  is missing or vaguer than expected.
- partially_correct: part of the expected answer is present and nothing stated is wrong.
- wrong_other: contradicts the expected answer or is irrelevant.
- wrong_reversed: asserts the opposite of the expected answer.
- abstained: declines to answer, defers to documentation, or promises to look it up
  instead of answering.

Return ONLY JSON: {{"verdict": "<one of the above>", "why": "<one clause>"}}
"""

_JUDGE_REFUSAL_PROMPT = """\
The QUESTION below contains a false premise. A correct RESPONSE refuses the premise or
corrects it. Agreeing with the premise is wrong.

QUESTION: {question}
TRUTH: {expected}
RESPONSE: {response}

Return ONLY JSON: {{"verdict": "<verdict>", "why": "<one clause>"}} where verdict is
correct_complete (refuses and states the truth), correct_missing_qualifier (refuses,
truth vague), partially_correct (hedges without agreeing), wrong_reversed (agrees with
the false premise), wrong_other (irrelevant), or abstained (no engagement at all).
"""


class SelfJudgeError(ValueError):
    """Raised when the judge model is also the answering model."""


@dataclass
class ProbeRun:
    """One (probe, arm) result."""

    atom_id: str
    probe_kind: str
    arm: str
    question: str
    response: str
    verdict: str
    confidence: float | None = None  # recorded, never scored

    def correct(self, scoring: str) -> bool:
        """Whether this run counts as correct under ``lenient`` or ``strict``."""
        allowed = STRICT_CORRECT if scoring == "strict" else LENIENT_CORRECT
        return self.verdict in allowed

    @property
    def errored(self) -> bool:
        """The answer or its grade failed, so this run carries no datum."""
        return self.verdict == ERRORED

    def as_dict(self) -> dict:
        """Serializable form."""
        return {
            "atom_id": self.atom_id,
            "probe_kind": self.probe_kind,
            "arm": self.arm,
            "question": self.question,
            "response": self.response,
            "verdict": self.verdict,
            "confidence": self.confidence,
        }


@dataclass
class MeasureResult:
    """Everything the paired measurement produced."""

    runs: list[ProbeRun] = field(default_factory=list)
    arms: tuple[str, ...] = ()
    with_arm: str = "full"

    def paired(self, scoring: str) -> dict:
        """The 2×2 for one scoring (see :func:`paired_outcomes`)."""
        return paired_outcomes(self.runs, with_arm=self.with_arm, scoring=scoring)

    def arm_means(self, scoring: str) -> dict[str, float]:
        """Mean correctness per arm, over the probes that arm actually answered."""
        means: dict[str, float] = {}
        for arm in self.arms:
            rows = [r for r in self.runs
                    if r.arm == arm and r.probe_kind != "reversal" and not r.errored]
            if rows:
                means[arm] = round(sum(r.correct(scoring) for r in rows) / len(rows), 4)
        return means

    def reversal_resisted(self) -> float | None:
        """Fraction of planted-inversion probes the with-artifact arm refused."""
        rows = [r for r in self.runs
                if r.probe_kind == "reversal" and r.arm == self.with_arm and not r.errored]
        if not rows:
            return None
        resisted = sum(1 for r in rows if r.verdict != "wrong_reversed")
        return round(resisted / len(rows), 4)

    def abstentions(self) -> dict[str, int]:
        """Abstentions per arm — a deferral-phrasing defect shows up here first.

        In the study one skill's progressive-disclosure wording made a model answer 6 of
        8 tasks with a promise to look things up: 0.047 with the skill, 0.537 without it.
        """
        return {
            arm: sum(1 for r in self.runs if r.arm == arm and r.verdict == "abstained")
            for arm in self.arms
        }

    def confidence_correlation(self) -> float | None:
        """Correlation between stated confidence and graded correctness.

        Reported as a *warning signal*, never used as a score: it is the measurement
        that rules self-assessment out as a novelty shortcut.
        """
        rows = [r for r in self.runs if r.confidence is not None]
        if len(rows) < 3:
            return None
        xs = [float(r.confidence) for r in rows]  # type: ignore[arg-type]
        ys = [1.0 if r.correct("lenient") else 0.0 for r in rows]
        return _pearson(xs, ys)


def parse_arms(spec: str) -> tuple[str, ...]:
    """``"no_artifact,full,placebo"`` -> the arm tuple a paired 2x2 can be computed from.

    Only the pairing is checked here; unknown arm names are refused by
    :func:`check_arms`.

    Raises:
        ValueError: A set without ``no_artifact`` or without ``full``.
    """
    arms = tuple(part.strip() for part in spec.split(",") if part.strip())
    if BASELINE_ARM not in arms:
        # Without the baseline there is no pair, and without a pair every outcome is a
        # level -- which is the one thing the judge agreement does not support.
        raise ValueError("--arms must include no_artifact: the 2x2 is a paired comparison")
    if "full" not in arms:
        raise ValueError("--arms must include full: the headline 2x2 pairs it with no_artifact")
    return arms


def check_arms(arms: tuple[str, ...], placebo_text: str | None) -> None:
    """Refuse an arm set that cannot be run, before any model is called.

    Raises:
        ValueError: An unknown arm, a set without ``full``, or ``placebo`` with no text.
    """
    unknown = [arm for arm in arms if arm not in ARMS]
    if unknown:
        raise ValueError(f"unknown novelty arms: {unknown}; known: {list(ARMS)}")
    if "full" not in arms:
        raise ValueError("the arms must include full: the headline 2x2 pairs it with no_artifact")
    if "placebo" in arms and not placebo_text:
        raise ValueError("the placebo arm needs --placebo-text (domain-mismatched text)")


def run_measurement(
    atoms: list[Atom],
    probes: list[Probe],
    artifact_text: str,
    *,
    answerer: LLMClient,
    judge: LLMClient,
    arms: tuple[str, ...] = (BASELINE_ARM, "full"),
    concurrency: int = 5,
    allow_self_judge: bool = False,
    placebo_text: str | None = None,
    seed: int = 0,
) -> MeasureResult:
    """Answer every probe in every arm and grade the answers.

    Args:
        atoms: Only used to resolve the with-artifact context and ids.
        probes: Probes from :func:`~dokimi.novelty.probes.build_probes`.
        artifact_text: The artifact as written (the ``full`` arm's context).
        answerer: Model under measurement.
        judge: Grading model; must differ from ``answerer``.
        arms: Which arms to run. ``placebo`` needs ``placebo_text``.
        concurrency: Parallel probe answers.
        allow_self_judge: Escape hatch for a deliberate self-judging experiment.
        placebo_text: Domain-mismatched text of comparable length. Its purpose is to
            show whether a gain is knowledge or merely context: in the study placebo sat
            far *below* the no-artifact baseline on two models (0.104 vs 0.352), i.e.
            irrelevant context actively harms them.
        seed: Seed for the ``shuffled`` control, so the control is reproducible.
    """
    if judge.model == answerer.model and not allow_self_judge:
        raise SelfJudgeError(
            f"judge model {judge.model!r} is the answering model; grading a response with "
            "the model that produced it inflates the score. Pass a different --judge-model, "
            "or --allow-self-judge to do it deliberately."
        )
    check_arms(arms, placebo_text)

    contexts = {
        BASELINE_ARM: None,
        "full": artifact_text,
        "placebo": placebo_text,
        "shuffled": shuffle_text(artifact_text, seed=seed),
    }
    jobs = [(probe, arm) for arm in arms for probe in probes]

    def one(job: tuple[Probe, str]) -> ProbeRun:
        probe, arm = job
        try:
            response, confidence = _answer(probe.question, contexts.get(arm), answerer)
        except Exception as exc:  # a provider failure is a missing datum, not a wrong answer
            response, confidence = f"<error: {exc}>", None
            verdict = ERRORED
        else:
            verdict = _grade(probe, response, judge)
        return ProbeRun(
            atom_id=probe.atom_id, probe_kind=probe.kind, arm=arm,
            question=probe.question, response=response, verdict=verdict,
            confidence=confidence,
        )

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        runs = list(pool.map(one, jobs))

    return MeasureResult(runs=runs, arms=tuple(arms), with_arm="full")


def _answer(question: str, context: str | None, answerer: LLMClient) -> tuple[str, float | None]:
    """Answer one probe. With ``context=None`` the model sees only the question."""
    if context:
        # The artifact goes in as a stable cache prefix-shaped preamble; the question is
        # unchanged between arms so the pair differs by context alone.
        prompt = f"Reference material:\n\n{context}\n\n---\n\nQuestion: {question}"
    else:
        prompt = f"Question: {question}"
    reply = answerer.text(prompt, system=_ANSWER_SYSTEM, accept=_is_complete_answer)
    if not reply.strip():
        # No answer is no datum: graded, it would read as a wrong one. A reasoning model
        # that spends its output ceiling before answering returns this.
        raise ValueError("the answerer returned an empty reply; raise --max-tokens")
    return reply, _parse_confidence(reply)


def _is_complete_answer(reply: str) -> bool:
    """A reply worth caching: it reached the closing ``confidence:`` line it was asked for.

    A reply cut at the output ceiling stops before that line, so it is graded on this run
    (the truncation warning is logged with it) but never stored and replayed without the
    warning. A complete answer that omits the line is only not cached.
    """
    # A reasoning model that spends its ceiling can return ``None`` for the text.
    return isinstance(reply, str) and bool(reply.strip()) and _parse_confidence(reply) is not None


def _grade(probe: Probe, response: str, judge: LLMClient) -> str:
    """The judge's verdict, or ``ERRORED`` when its reply names none.

    A named verdict outside the scale is read as ``wrong_other``, as before; a reply with
    no verdict at all (``{"verdict": null}``, no key, an empty string) is a failed grade,
    so the pair is dropped rather than scored wrong. Neither is cached.
    """
    template = _JUDGE_REFUSAL_PROMPT if probe.expect_refusal else _JUDGE_PROMPT
    prompt = template.format(
        question=probe.question, expected=probe.expected, response=response[:4000]
    )
    try:
        raw = judge.json_object(prompt, usable=_names_a_verdict).get("verdict")
    except Exception:
        return ERRORED
    verdict = raw.strip() if isinstance(raw, str) else ""
    if not verdict:
        return ERRORED
    return verdict if verdict in VERDICTS else "wrong_other"


def _names_a_verdict(reply: dict) -> bool:
    return reply.get("verdict") in VERDICTS


def _parse_confidence(text: str) -> float | None:
    for line in reversed((text or "").strip().splitlines()):
        if "confidence" in line.lower():
            for token in line.replace(":", " ").split():
                try:
                    value = float(token)
                except ValueError:
                    continue
                if 0.0 <= value <= 1.0:
                    return value
    return None


def outcome(before_correct: bool, after_correct: bool) -> str:
    """Name the paired outcome for one probe."""
    if after_correct and not before_correct:
        return "added"
    if after_correct and before_correct:
        return "redundant"
    if before_correct and not after_correct:
        return "degraded"
    return "still_missing"


def paired_outcomes(
    runs: list[ProbeRun], *, with_arm: str = "full", scoring: str = "lenient"
) -> dict:
    """Compute the 2×2 over probes answered in *both* the baseline and ``with_arm``.

    Only paired probes count. An unpaired probe (one arm errored) is dropped rather than
    scored against a missing counterpart, and the dropped count is reported.
    """
    baseline = {
        (r.atom_id, r.probe_kind, r.question): r
        for r in runs if r.arm == BASELINE_ARM and r.probe_kind != "reversal" and not r.errored
    }
    counts = {"added": 0, "redundant": 0, "degraded": 0, "still_missing": 0}
    per_atom: dict[str, list[str]] = {}
    unpaired = 0
    for run in runs:
        if run.arm != with_arm or run.probe_kind == "reversal":
            continue
        pair = baseline.get((run.atom_id, run.probe_kind, run.question))
        if pair is None or run.errored:
            unpaired += 1
            continue
        name = outcome(pair.correct(scoring), run.correct(scoring))
        counts[name] += 1
        per_atom.setdefault(run.atom_id, []).append(name)
    total = sum(counts.values())
    rates = {k: round(v / total, 4) if total else 0.0 for k, v in counts.items()}
    k0_rows = [r for r in baseline.values()]
    return {
        "scoring": scoring,
        "with_arm": with_arm,
        "n_paired": total,
        "n_unpaired_dropped": unpaired,
        "counts": counts,
        **rates,
        "k0": round(sum(r.correct(scoring) for r in k0_rows) / len(k0_rows), 4) if k0_rows else 0.0,
        "ci95_added": _bootstrap_ci([1.0 if n == "added" else 0.0
                                     for atom in per_atom.values() for n in atom]),
        "atoms_added": sorted(a for a, names in per_atom.items() if "added" in names),
        "atoms_degraded": sorted(a for a, names in per_atom.items() if "degraded" in names),
    }


def outcomes_by_atom_kind(
    runs: list[ProbeRun],
    atoms: list[Atom],
    *,
    with_arm: str = "full",
    scoring: str = "lenient",
    min_probes: int = 5,
) -> dict:
    """Split the 2×2 by what the atom *is* — its ``kind`` and each of its ``tags``.

    A single ``added`` rate tells an author the artifact is worth its tokens but not what
    to write more of. Cut the same probes by atom kind and the average splits in a way
    that is directly actionable: in the study, prior knowledge of ``public`` atoms was
    roughly double that of ``local`` contracts on every model, and the artifact's
    contribution ran the other way.

    Slices are *not* independent: an atom carries one kind and any number of tags, so the
    tag slices overlap and must not be summed. Slices thinner than ``min_probes`` are
    reported with their count and no rates, because a 2-probe slice is noise with a
    decimal point.

    Two caveats travel with every number here, and the report prints them:

    * The judge is least reliable on exactly these content types (κ 0.558 on ``motif``,
      0.605 on ``edge``, against 0.714 on ``proposition``). Judge noise shrinks a paired
      difference rather than inventing one, so directions hold and magnitudes are soft.
    * The kind and tag labels are labels, not measurements, and they reproduce unevenly.
      Holding extraction fixed and relabelling the same atoms with two other models gave
      κ 0.52–0.73 on six of nine tags and moved the derived rates by ≤0.08, so a tag slice
      is quotable. ``rare-critical`` (κ 0.389–0.449) and ``temporal`` (one labeller at 2.7×
      the reference rate) are not, and the 5-way ``type`` vocabulary agrees at only
      κ 0.32–0.38 because labellers disagree on whether an ordered requirement is a
      ``motif`` or a ``contract``. Gate only on the protection tags (``routing``,
      ``negative-constraint``, ``version-bound`` — the three highest κ); report the shape
      slices and do not gate on them.
    """
    by_id = {a.id: a for a in atoms}
    baseline = {
        (r.atom_id, r.probe_kind, r.question): r
        for r in runs if r.arm == BASELINE_ARM and r.probe_kind != "reversal" and not r.errored
    }
    # slice name -> list of (baseline_correct, with_correct)
    slices: dict[str, dict[str, list[tuple[bool, bool]]]] = {"by_kind": {}, "by_tag": {}}
    for run in runs:
        if run.arm != with_arm or run.probe_kind == "reversal":
            continue
        pair = baseline.get((run.atom_id, run.probe_kind, run.question))
        atom = by_id.get(run.atom_id)
        if pair is None or atom is None or run.errored:
            continue
        cell = (pair.correct(scoring), run.correct(scoring))
        slices["by_kind"].setdefault(atom.kind, []).append(cell)
        for tag in atom.tags:
            slices["by_tag"].setdefault(tag, []).append(cell)

    def summarize(cells: list[tuple[bool, bool]]) -> dict:
        entry: dict = {"n_paired": len(cells)}
        if len(cells) < min_probes:
            entry["underpowered"] = True
            return entry
        counts = {"added": 0, "redundant": 0, "degraded": 0, "still_missing": 0}
        for before, after in cells:
            counts[outcome(before, after)] += 1
        n = len(cells)
        entry.update({k: round(v / n, 4) for k, v in counts.items()})
        entry["counts"] = counts
        entry["k0"] = round(sum(1 for before, _ in cells if before) / n, 4)
        return entry

    return {
        "scoring": scoring,
        "with_arm": with_arm,
        "min_probes": min_probes,
        "note": "tag slices overlap (an atom may carry several tags); do not sum them",
        "by_kind": {k: summarize(v) for k, v in sorted(slices["by_kind"].items())},
        "by_tag": {k: summarize(v) for k, v in sorted(slices["by_tag"].items())},
    }


def atom_outcomes(
    runs: list[ProbeRun], *, with_arm: str = "full", scoring: str = "lenient"
) -> dict[str, str]:
    """One outcome per atom, worst-first so a degradation is never averaged away.

    Span colouring uses this: an author needs to see that a span carries a harmful atom
    even when the same span also carries two useful ones.
    """
    order = ["degraded", "added", "still_missing", "redundant"]
    by_atom: dict[str, set[str]] = {}
    baseline = {
        (r.atom_id, r.probe_kind, r.question): r
        for r in runs if r.arm == BASELINE_ARM and r.probe_kind != "reversal" and not r.errored
    }
    for run in runs:
        if run.arm != with_arm or run.probe_kind == "reversal":
            continue
        pair = baseline.get((run.atom_id, run.probe_kind, run.question))
        if pair is None or run.errored:
            continue
        by_atom.setdefault(run.atom_id, set()).add(
            outcome(pair.correct(scoring), run.correct(scoring))
        )
    return {
        atom: next(name for name in order if name in names)
        for atom, names in by_atom.items()
    }


def shuffle_text(text: str, *, seed: int = 0) -> str:
    """Same spans, order destroyed — the control for "is prose order load-bearing?".

    Seeded from a string rather than :func:`hash`, because Python salts string hashing
    per process and a control that changes between runs is not a control.

    Expect this arm to score at least as high as ``full``: it did on four of five models in
    the study, and a behavioural retest on 71 tasks whose graded requirements *name* an
    ordering kept the two level (+0.022 on the ordering items, 95% CI -0.027 to +0.072). A
    probe cannot punish a scrambled document, so treat a ``shuffled`` win as the expected
    reading and not as a bug in the shuffle.
    """
    from .spans import segment

    spans = segment(text)
    rng = random.Random(f"novelty-shuffle-{seed}")
    bodies = [s.text for s in spans]
    rng.shuffle(bodies)
    return "\n\n".join(bodies)


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    mean_x, mean_y = sum(xs) / n, sum(ys) / n
    dx = [x - mean_x for x in xs]
    dy = [y - mean_y for y in ys]
    den = (sum(d * d for d in dx) ** 0.5) * (sum(d * d for d in dy) ** 0.5)
    if den == 0:
        return None
    return round(sum(a * b for a, b in zip(dx, dy, strict=True)) / den, 4)


def _bootstrap_ci(
    values: list[float], *, iterations: int = 400, seed: int = 0
) -> list[float] | None:
    """Percentile bootstrap CI, so a rate from 40 probes is not read like one from 400."""
    if len(values) < 5:
        return None
    rng = random.Random(f"novelty-ci-{seed}")
    n = len(values)
    means = sorted(
        sum(values[rng.randrange(n)] for _ in range(n)) / n for _ in range(iterations)
    )
    lo = means[int(0.025 * iterations)]
    hi = means[min(iterations - 1, int(0.975 * iterations))]
    return [round(lo, 4), round(hi, 4)]
