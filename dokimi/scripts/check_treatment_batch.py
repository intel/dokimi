#!/usr/bin/env python
# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Acceptance gate + paired deltas for an arms.v1 batch directory.

A "batch" is one treatment measured over models x question sets: 2 arms per file,
one file per cell. Two jobs:

1. **Gate the batch mechanically**, not by eye: schema, arm names, baseline arm,
   pinned judge, no self-judging, question counts, per-arm answer coverage, and
   `used_system_prompt` (the whole point of a `profile:` arm is that a system
   prompt reaches the model -- 7720/7720 pre-existing records have it false).
2. **Report paired deltas.** A failed judge call leaves a score record with
   `aggregate: None`, so the artifact's own `delta_vs_baseline` can subtract
   means taken over different question counts (39 vs 40 was observed). Only
   questions where *both* arms scored are comparable.

Usage:
    python scripts/check_treatment_batch.py results/unified_judge/persona-batch \\
        --profile data/agent_profiles/concise_expert.md
    python scripts/check_treatment_batch.py results/unified_judge/skills \\
        --treatment-arm 'skill:*' --expect-n regular=40,golden=20

Nothing about the judge or the question-set sizes is inherent to the format, so both are
flags: `--judge-model` / `--judge-provider` pin the judge a batch claims (the check that
matters is that every cell names the *same* one, and that it is not the answering model),
and `--expect-n` maps a filename suffix to the question count that suffix must carry.
Reported deltas are pooled over questions; the mean of per-cell means differs slightly
(0.3 points was observed) because the sets have different sizes.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import statistics
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dokimi.defaults import DEFAULT_JUDGE_MODEL, DEFAULT_JUDGE_PROVIDER  # noqa: E402

# The judge a batch run with default flags records. A batch judged otherwise (older
# batches used amazon-bedrock/global.anthropic.claude-sonnet-4-6) passes --judge-model /
# --judge-provider.
JUDGE_MODEL = DEFAULT_JUDGE_MODEL
JUDGE_PROVIDER = DEFAULT_JUDGE_PROVIDER
# Question-set sizes keyed by the filename suffix the run_*.sh scripts use. These are one
# product's sizes and nothing more — observed golden sets have run 20, 24 and 111 questions
# depending on the product — so a batch over another product must pass --expect-n. The suffix
# is a filename convention, not something the artifact knows, which is why `check_file` also
# flags a want_n that disagrees with the artifact's own total_questions instead of trusting
# the map.
EXPECTED_N = {"regular": 40, "golden": 20}
# The observed judge-call failure rate is ~1.25% (Intel proxy APIConnectionError),
# so "zero errors" is not a reachable gate across ~960 calls. Gate on coverage.
MIN_PAIRED_FRACTION = 0.95


def parse_expect_n(spec: str) -> dict[str, int]:
    """Parse ``--expect-n`` into a suffix -> count map.

    Accepts ``regular=40,golden=20`` and a bare ``40`` (every file, whatever its name).
    An empty string disables the count gate, falling back to each artifact's own
    ``total_questions`` -- which is not a weaker gate but no gate at all: the runner writes
    that field as ``len(records)``, so a truncated run reports the truncated total and
    every count check holds by construction.
    """
    spec = spec.strip()
    if not spec:
        return {}
    if spec.isdigit():
        return {"": int(spec)}
    mapping: dict[str, int] = {}
    for part in spec.split(","):
        suffix, _, count = part.partition("=")
        if not _ or not count.strip().isdigit():
            raise argparse.ArgumentTypeError(
                f"--expect-n: expected 'suffix=count' pairs or a bare integer, got {part!r}"
            )
        mapping[suffix.strip()] = int(count)
    return mapping


def numeric(value: object) -> float | None:
    """A judge score, or None for a failed call / non-numeric aggregate."""
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def expected_n(path: Path, artifact: dict, sizes: dict[str, int]) -> tuple[int | None, str]:
    """The count this file must carry, and where the number came from.

    The source matters because the fallback is not a weaker gate, it is *no* gate:
    ``total_questions`` is written as ``len(records)`` by the arm runner, so comparing the
    record count against it can only ever hold. A caller that lands on ``"artifact"``
    while a suffix map was supplied has a filename the map does not describe, and should
    hear about it rather than get a row of green counts.
    """
    for suffix, n in sizes.items():
        if not suffix or path.stem.endswith(f"_{suffix}"):
            return n, "expect-n"
    total = artifact.get("total_questions")
    if isinstance(total, int):
        return int(total), "artifact"
    return None, "none"


def profile_digest(profile_path: Path) -> str:
    """The digest the artifacts must carry for *profile_path*'s current text."""
    from dokimi.agent_profiles import load_agent_profile
    from dokimi.utils import digest_text

    return digest_text(load_agent_profile(profile_path).system_prompt)


def check_file(
    path: Path,
    treatment_pattern: str,
    judge_model: str,
    want_digest: str | None = None,
    judge_provider: str = JUDGE_PROVIDER,
    sizes: dict[str, int] | None = None,
) -> dict:
    """Return one row of findings; `problems` empty means the file passed."""
    problems: list[str] = []
    artifact = json.loads(path.read_text(encoding="utf-8"))

    schema = artifact.get("schema_version")
    if schema != "arms.v1":
        problems.append(f"schema_version={schema!r}, want 'arms.v1'")

    arms = artifact.get("arms") or []
    baseline_arm = artifact.get("baseline_arm")
    if baseline_arm != "baseline":
        problems.append(f"baseline_arm={baseline_arm!r}, want 'baseline'")
    if len(arms) != 2:
        problems.append(f"{len(arms)} arms {arms}, want exactly 2 (parity with docs/skills batches)")

    treatment = next((a for a in arms if a != baseline_arm), None)
    if treatment is None:
        problems.append("no treatment arm found")
    elif not fnmatch.fnmatch(treatment, treatment_pattern):
        problems.append(f"treatment arm {treatment!r} does not match {treatment_pattern!r}")

    # Judge provenance. A missing judge_model means the artifact cannot be
    # attributed to a judge at all -- that is what made the old batch unusable.
    recorded_judge = artifact.get("judge_model")
    if recorded_judge != judge_model:
        problems.append(f"judge_model={recorded_judge!r}, want {judge_model!r}")
    if judge_provider and artifact.get("judge_provider") != judge_provider:
        problems.append(f"judge_provider={artifact.get('judge_provider')!r}, want {judge_provider!r}")
    answer_model = artifact.get("model")
    if recorded_judge and answer_model and recorded_judge == answer_model:
        problems.append(f"SELF-JUDGING: judge_model == model == {answer_model!r}")

    sizes = EXPECTED_N if sizes is None else sizes
    want_n, n_source = expected_n(path, artifact, sizes)
    if sizes and n_source != "expect-n":
        problems.append(
            f"filename matches none of the --expect-n suffixes ({', '.join(sorted(sizes)) or '<bare>'}), "
            f"so the count gate fell back to the artifact's own total_questions"
            f"{f'={want_n}' if want_n is not None else ' (absent)'} -- which the runner writes as "
            "len(records), so it cannot detect a short run; rename the file with the suffix or "
            "pass --expect-n N"
        )
    evaluations = artifact.get("evaluations") or []
    if not evaluations:
        problems.append("0 evaluations -- nothing in this cell was judged")
    if want_n is not None and len(evaluations) != want_n:
        problems.append(f"{len(evaluations)} evaluations, want {want_n}")
    total = artifact.get("total_questions")
    if n_source == "expect-n" and isinstance(total, int) and total != want_n:
        problems.append(
            f"total_questions={total} but --expect-n wants {want_n} for this filename -- the "
            "suffix map does not describe the set this cell ran (golden sets are 20, 24 or 111 "
            "questions depending on the product), so fix --expect-n before reading the counts"
        )

    # Paired deltas: keep only questions both arms scored.
    pairs: list[tuple[float, float]] = []
    errors = {baseline_arm: 0, treatment: 0}
    for ev in evaluations:
        scores = ev.get("scores") or {}
        base = numeric((scores.get(baseline_arm) or {}).get("aggregate"))
        treat = numeric((scores.get(treatment) or {}).get("aggregate"))
        if base is None:
            errors[baseline_arm] = errors.get(baseline_arm, 0) + 1
        if treat is None:
            errors[treatment] = errors.get(treatment, 0) + 1
        if base is not None and treat is not None:
            pairs.append((base, treat))

    if evaluations:  # the empty case is already a problem, reported above
        fraction = len(pairs) / len(evaluations)
        if fraction < MIN_PAIRED_FRACTION:
            problems.append(
                f"only {len(pairs)}/{len(evaluations)} questions scored in both arms "
                f"({fraction:.0%} < {MIN_PAIRED_FRACTION:.0%})"
            )

    # The system prompt must actually have been sent -- and only in the treatment arm.
    # Distinguish a *missing answer* from a *misrouted system prompt*. Both show up as
    # used_system_prompt != true, but they mean completely different things: the first is
    # a transient proxy failure (re-run the cell), the second is a code bug (stop and fix).
    # Conflating them sends you hunting a routing bug that isn't there.
    sent = {arm: 0 for arm in arms}
    present = {arm: 0 for arm in arms}
    records = {arm: 0 for arm in arms}
    digests: set[str] = set()
    for answer in artifact.get("answers") or []:
        for arm, record in (answer.get("arms") or {}).items():
            if arm in records:
                records[arm] += 1
                if record.get("answer"):
                    present[arm] += 1
                    if record.get("used_system_prompt"):
                        sent[arm] += 1
                if arm == treatment:
                    stamped = (record.get("metadata") or {}).get("profile_prompt_sha256")
                    if stamped:
                        digests.add(stamped)

    # A stale artifact is the dangerous case: the arm is named after the profile's
    # frontmatter id, so editing a profile in place leaves old cells with the same arm name
    # and a passing gate. The runner would skip them as "already complete" and the batch
    # would silently mix two different system prompts. Compare the stamped digest instead.
    if want_digest:
        if not digests:
            problems.append(
                "no profile_prompt_sha256 stamped on any treatment answer -- cannot prove which "
                "prompt text produced these scores; re-run with the current code"
            )
        elif digests != {want_digest}:
            problems.append(
                f"STALE ARTIFACT: scored with prompt digest(s) {sorted(d[:19] + '...' for d in digests)} "
                f"but the profile file now digests to {want_digest[:19]}... -- delete or re-run this cell"
            )
    for arm in arms:
        if want_n is not None and records.get(arm, 0) != want_n:
            problems.append(f"arm {arm!r} has {records.get(arm, 0)} answer records, want {want_n}")
        if want_n is not None and present.get(arm, 0) != want_n:
            problems.append(
                f"arm {arm!r} is missing {want_n - present.get(arm, 0)} answers "
                f"({present.get(arm, 0)}/{want_n} generated) -- transient provider failure, re-run the cell"
            )
    if treatment and treatment.startswith("profile:"):
        # Guarding on `present > 0` keeps a proxy outage from being read as a routing bug,
        # but on its own it also means a cell that generated *nothing* passes the routing
        # gate silently. Say so instead: with no generated answer there is no evidence
        # either way, and the cell has to be re-run before its delta means anything.
        if not present.get(treatment, 0):
            problems.append(
                f"arm {treatment!r} generated 0 answers -- the persona was never exercised, so "
                "nothing here shows whether the system prompt reaches the model"
            )
        elif sent.get(treatment, 0) != present.get(treatment, 0):
            problems.append(
                f"MISROUTED PERSONA: only {sent.get(treatment, 0)}/{present.get(treatment, 0)} *generated* "
                f"{treatment!r} answers carried the system prompt -- this is a code bug, not a proxy failure"
            )
        if sent.get(baseline_arm, 0):
            problems.append(f"baseline sent a system prompt in {sent[baseline_arm]} answers (should be 0)")

    reported = (artifact.get("summary", {}).get("per_arm", {}).get(treatment) or {}).get("delta_vs_baseline")
    paired_delta = (
        statistics.fmean(t - b for b, t in pairs) if pairs else None
    )
    return {
        "file": path.name,
        "model": answer_model,
        "treatment": treatment,
        "n": len(evaluations),
        "pairs": len(pairs),
        "errors": sum(errors.values()),
        "paired_delta": paired_delta,
        "reported_delta": numeric(reported),
        "judge_cost": (artifact.get("judge_metrics") or {}).get("total_cost_usd"),
        "problems": problems,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("target", type=Path, help="batch directory, or a single arms.v1 JSON file")
    parser.add_argument("--treatment-arm", default="profile:*", help="glob the treatment arm must match")
    parser.add_argument("--judge-model", default=JUDGE_MODEL, help="judge model every file must record")
    parser.add_argument(
        "--judge-provider",
        default=JUDGE_PROVIDER,
        help="judge provider every file must record ('' to skip the check)",
    )
    parser.add_argument(
        "--expect-n",
        type=parse_expect_n,
        default=",".join(f"{k}={v}" for k, v in EXPECTED_N.items()),
        metavar="SUFFIX=N[,...]",
        help="question count per filename suffix, or a bare integer ('' to fall back to "
             "each artifact's total_questions)",
    )
    parser.add_argument("--expect-files", type=int, default=12, help="0 to skip the completeness check")
    parser.add_argument(
        "--profile",
        type=Path,
        help="profile .md the batch was run with; every cell must carry its current prompt digest",
    )
    parser.add_argument(
        "--quiet", action="store_true", help="print nothing; exit status only (for per-cell gating in run scripts)"
    )
    args = parser.parse_args()

    want_digest = profile_digest(args.profile) if args.profile else None

    # A single file is accepted so a run script can validate one cell before deciding
    # to skip it. Without that, a partially-judged artifact (a proxy outage can cost
    # 28 of 40 questions) is written once and then skipped forever as "already done".
    if args.target.is_file():
        files, args.expect_files = [args.target], 0
    elif args.target.is_dir():
        files = sorted(p for p in args.target.glob("*.json") if not p.name.startswith("."))
        if not files:
            print(f"FAIL: no JSON artifacts in {args.target}", file=sys.stderr)
            return 1
    else:
        print(f"FAIL: no such file or directory: {args.target}", file=sys.stderr)
        return 1

    if args.quiet:
        # The completeness check must run here too, not only in the verbose path. It did
        # not, and a chained runner read "every file present passes" as "the batch is
        # done" and started the next batch against a 2-of-12 directory — two batches then
        # shared the proxy that is already the bottleneck.
        if args.expect_files and len(files) != args.expect_files:
            return 1
        for path in files:
            try:
                if check_file(
                    path,
                    args.treatment_arm,
                    args.judge_model,
                    want_digest,
                    args.judge_provider,
                    args.expect_n,
                )["problems"]:
                    return 1
            except (json.JSONDecodeError, OSError):
                return 1
        return 0

    rows, broken = [], []
    for path in files:
        try:
            rows.append(
                check_file(
                    path,
                    args.treatment_arm,
                    args.judge_model,
                    want_digest,
                    args.judge_provider,
                    args.expect_n,
                )
            )
        except (json.JSONDecodeError, OSError) as exc:
            broken.append(f"{path.name}: unreadable ({exc})")

    print(f"{'file':<28} {'pairs':>7} {'err':>4} {'paired':>8} {'reported':>9}  arm")
    print("-" * 82)
    for row in rows:
        paired = f"{row['paired_delta']:+.1f}" if row["paired_delta"] is not None else "n/a"
        rep = f"{row['reported_delta']:+.1f}" if row["reported_delta"] is not None else "n/a"
        flag = "" if not row["problems"] else "  <-- FAIL"
        print(
            f"{row['file']:<28} {row['pairs']:>3}/{row['n']:<3} {row['errors']:>4} "
            f"{paired:>8} {rep:>9}  {row['treatment']}{flag}"
        )

    deltas = [r["paired_delta"] for r in rows if r["paired_delta"] is not None]
    if deltas:
        print(
            f"\npaired delta: mean {statistics.fmean(deltas):+.2f}  "
            f"median {statistics.median(deltas):+.2f}  "
            f"range {min(deltas):+.1f}..{max(deltas):+.1f}  (n={len(deltas)} cells)"
        )
    costs = [r["judge_cost"] for r in rows if isinstance(r["judge_cost"], (int, float))]
    if costs:
        print(f"judge spend: ${sum(costs):.2f} across {len(costs)} files")

    failures = broken + [f"{r['file']}: {p}" for r in rows for p in r["problems"]]
    if args.expect_files and len(files) != args.expect_files:
        failures.append(f"batch has {len(files)} files, expected {args.expect_files}")

    if failures:
        print(f"\nFAIL ({len(failures)} problem(s)):")
        for problem in failures:
            print(f"  - {problem}")
        return 1

    print(f"\nPASS: {len(rows)} files, all gates green.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
