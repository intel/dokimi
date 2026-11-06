# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``eval skill-coverage``: which checks of an eval set exercise each requirement of a SKILL.md (#220, A5).

Makes two model calls through the novelty client: one lists the skill's requirements, one names the
eval set's checks that would detect a violation of each. It prints a Markdown table; ``--out`` writes a
``skill_coverage.v1`` artifact. The result is a list of candidate checks, not a score: the command exits 0
whenever the map was made, and no flag turns it into a gate. Exits 2 on an unreadable input or an ``--out``
that would overwrite one, 1 when the model gives no usable reply or the ``--max-usd`` cap is reached.
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from pathlib import Path

from dokimi.exit_codes import EXIT_FAILURE
from dokimi.utils import InputError

# Reasoning models spend part of the output budget on hidden reasoning; a smaller ceiling returns no text.
MAX_TOKENS = 12000


def _usd(text: str) -> float:
    """A positive, finite USD amount (``0``, negatives, ``nan`` and ``inf`` would disable the cap)."""
    value = float(text)
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError(f"must be a positive number of USD, got {text!r}")
    return value


def _model_ref(value: str) -> tuple[str, str]:
    from dokimi.eval.judge import JUDGE_PROVIDERS

    provider, _, model = value.partition("/")
    if provider not in JUDGE_PROVIDERS or not model.strip():
        raise argparse.ArgumentTypeError(
            f"expected PROVIDER/MODEL with PROVIDER one of {', '.join(JUDGE_PROVIDERS)}, got {value!r}")
    return provider, model.strip()


def _client(args: argparse.Namespace):
    from dokimi.novelty.client import LLMClient, SpendCap

    provider, model = args.model
    cap = SpendCap(limit_usd=args.max_usd) if args.max_usd is not None else None
    return LLMClient(model=model, provider=provider, role="coverage", max_tokens=MAX_TOKENS, cap=cap)


def _refuse_out_over_input(out: str, inputs: list[Path]) -> None:
    """Exit 2 when ``--out`` cannot be resolved or would overwrite an input (or the ``.rejected`` sibling)."""
    try:
        target = Path(out)
        outputs = (target, target.with_name(target.name + ".rejected"))  # save_artifact removes that sibling
        resolved = {p.resolve() for p in inputs}
        clobbers = any(p.resolve() in resolved for p in outputs)
    except (OSError, RuntimeError, ValueError) as exc:  # a symlink loop: Python 3.11/3.12 raise RuntimeError
        raise InputError(f"cannot resolve {out}: {exc}") from exc
    if clobbers:
        raise InputError(f"--out would overwrite an input file: {out}")
    # Refused here, before the paid calls: save_artifact would fail on these after both have run.
    if Path(out).is_dir() or not os.access(Path(out).parent, os.W_OK):
        raise InputError(f"cannot write {out}: it is a directory, or its folder is missing or not writable")


def _guard_inputs(skill: Path, evals: Path) -> list[Path]:
    """Every file the command reads: the SKILL.md (inside a skill directory), the eval inputs and the check files."""
    from dokimi.eval_coverage import input_files
    from dokimi.skill_coverage import check_files

    target = skill / "SKILL.md" if skill.is_dir() else skill
    return [target, *input_files(evals), *check_files(evals)]


def cmd_eval_skill_coverage(args: argparse.Namespace) -> None:
    from dokimi.artifacts import save_artifact
    from dokimi.novelty.client import SpendCapReached
    from dokimi.skill_coverage import (
        SkillCoverageError, check_budget, coverage_report, extract_requirements, load_checks, map_requirements,
        read_skill, render_markdown,
    )

    evals = Path(args.evals)
    try:
        if not evals.exists():
            raise ValueError(f"{evals} does not exist")
        skill = read_skill(Path(args.skill))
        checks = load_checks(evals)
        if not checks:
            raise ValueError(f"no evals.json rows or task tests under {evals}")
        check_budget(checks)  # refuse before the first paid call
    except (OSError, ValueError) as exc:
        raise InputError(str(exc)) from exc
    if args.out:
        try:
            inputs = _guard_inputs(Path(args.skill), evals)
        except (OSError, RuntimeError, ValueError) as exc:
            raise InputError(f"cannot resolve the eval inputs under {evals}: {exc}") from exc
        _refuse_out_over_input(args.out, inputs)

    client = _client(args)
    try:
        requirements, requirements_dropped = extract_requirements(skill, client)
        links = map_requirements(requirements, checks, client)
    except SpendCapReached as exc:
        print(f"Error: the --max-usd cap was reached before the map was made: {exc}", file=sys.stderr)
        sys.exit(EXIT_FAILURE)
    except SkillCoverageError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(EXIT_FAILURE)
    provider, model = args.model
    report = coverage_report(skill, requirements, checks, links, evals_root=str(evals), model=model,
                             provider=provider, cost=client.cost_summary(),
                             requirements_dropped=requirements_dropped)
    if args.out:
        try:
            save_artifact("skill_coverage", report, Path(args.out))
        except (OSError, ValueError) as exc:
            raise InputError(f"cannot write {args.out}: {exc}") from exc
    print(render_markdown(report))


def register_eval(eval_sub) -> None:
    from dokimi.defaults import DEFAULT_JUDGE_MODEL, DEFAULT_JUDGE_PROVIDER

    p = eval_sub.add_parser(
        "skill-coverage",
        help="Which checks of an eval set exercise each SKILL.md requirement; uncovered ones are candidate checks")
    p.add_argument("skill", help="SKILL.md, or a skill directory holding one")
    p.add_argument("evals", help="evals.json, a skill directory (evals/evals.json) or a tasks root, as for eval coverage")
    p.add_argument("--model", type=_model_ref, default=(DEFAULT_JUDGE_PROVIDER, DEFAULT_JUDGE_MODEL),
                   metavar="PROVIDER/MODEL",
                   help=f"Model that lists and maps the requirements (default {DEFAULT_JUDGE_PROVIDER}/{DEFAULT_JUDGE_MODEL})")
    p.add_argument("--max-usd", type=_usd, default=None, dest="max_usd", metavar="X",
                   help="Stop before a model call once this much has been spent (exit 1)")
    p.add_argument("--out", metavar="FILE", default=None, help="skill_coverage.v1 JSON to write")
    p.set_defaults(func=cmd_eval_skill_coverage)
