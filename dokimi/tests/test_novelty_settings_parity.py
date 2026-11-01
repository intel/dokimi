# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""No run setting, bill, forfeit or tier record the novelty CLI produces differs from ``main``.

Resolving the tier-controlled flags in one place (``tiers.resolve_settings``) is a
refactor, and a refactor changes no number (#4). Each case runs the real CLI -- with
scripted clients, so nothing leaves the machine -- and keeps what the flags decide: the
``--estimate-only`` bill and its stderr, and for a measured run the ``run_config`` (tier
record included), the warnings, the model calls per role, the atom cap and the probe kinds.
``tests/fixtures/novelty_settings_main.json`` is this module's ``_capture`` output on
``main`` at f76a760. The module uses only interfaces that exist there, so it runs there
unchanged; to recapture, from a ``main`` checkout's ``dokimi/`` with this file and the
JSON copied in::

    python -m tests.test_novelty_settings_parity --write

The one intended change to an estimate is its bill: ``tokens``, ``expected_usd``, ``by_role``,
``expected_calls``, and the atom count and totals that the projected atoms move. ``_kept_estimate``
drops exactly those and compares the rest of the payload, the settings echo on stderr and the
tier record with its overrides included.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import sys
from decimal import ROUND_FLOOR, Decimal
from pathlib import Path
from typing import Any

import pytest

from dokimi.cli import build_parser
from dokimi.commands import novelty as novelty_cmd
from tests.test_novelty_analyze import SKILL, _scripted

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "novelty_settings_main.json"
NO_STAGES = ["--no-paraphrase", "--no-reversal", "--no-verify", "--no-classify",
             "--no-shorten", "--no-confirm-carriers"]


def _big_skill() -> str:
    """Enough spans that every tier's atom cap binds in the projection."""
    body = "\n\n".join(f"## Topic {i}\n\nSet option-{i} to {i}. Never pair it with "
                       f"option-{i + 1}." for i in range(160))
    return f"---\nname: big\ndescription: A big skill\n---\n\n# Big\n\n{body}\n"


EST = ["novelty", "analyze", "--artifact", "skill:big", "--estimate-only"]
RUN = ["novelty", "analyze", "--artifact", "skill:accel-tuning", "--concurrency", "1",
       "--out-dir", "out"]
ATOMS = ["novelty", "atoms", "--artifact", "skill:accel-tuning", "--out", "atoms.json"]
PROBE = ["novelty", "probe", "--artifact", "skill:accel-tuning", "--atoms", "atoms.json",
         "--concurrency", "1", "--out", "p.json"]

CASES: dict[str, list[list[str]]] = {
    "estimate/default": [EST],
    **{f"estimate/{tier}": [[*EST, "--tier", tier]] for tier in ("triage", "standard", "study")},
    "estimate/triage-max-atoms-90": [[*EST, "--tier", "triage", "--max-atoms", "90"]],
    "estimate/triage-max-atoms-25-agrees": [[*EST, "--tier", "triage", "--max-atoms", "25"]],
    "estimate/study-max-atoms-120-explicit": [[*EST, "--tier", "study", "--max-atoms", "120"]],
    "estimate/study-all-atoms": [[*EST, "--tier", "study", "--all-atoms"]],
    "estimate/study-all-atoms-beats-max": [[*EST, "--tier", "study", "--all-atoms",
                                            "--max-atoms", "10"]],
    "estimate/triage-every-stage-off": [[*EST, "--tier", "triage", *NO_STAGES]],
    "estimate/study-every-stage-off": [[*EST, "--tier", "study", *NO_STAGES]],
    "estimate/max-atoms-1": [[*EST, "--max-atoms", "1"]],
    "estimate/all-atoms": [[*EST, "--all-atoms"]],
    "estimate/all-atoms-beats-max": [[*EST, "--all-atoms", "--max-atoms", "5"]],
    "estimate/every-stage-off": [[*EST, *NO_STAGES]],
    "estimate/standard-budgets": [[*EST, "--tier", "standard", "--budgets", "0.6", "0.4",
                                   "0.2"]],
    "estimate/four-arms": [[*EST, "--arms", "no_artifact,full,placebo,shuffled"]],
    "estimate/arms-without-full": [[*EST, "--tier", "triage", "--arms", "no_artifact"]],
    "estimate/arms-without-baseline": [[*EST, "--arms", "full,placebo"]],
    "estimate/max-usd": [[*EST, "--tier", "standard", "--max-usd", "1"]],
    "run/default": [RUN],
    **{f"run/{tier}": [[*RUN, "--tier", tier]] for tier in ("triage", "standard", "study")},
    "run/study-all-atoms": [[*RUN, "--tier", "study", "--all-atoms"]],
    "run/triage-max-atoms-1": [[*RUN, "--tier", "triage", "--max-atoms", "1"]],
    "run/standard-no-verify": [[*RUN, "--tier", "standard", "--no-verify"]],
    "run/max-atoms-1": [[*RUN, "--max-atoms", "1"]],
    "run/no-paraphrase-no-reversal": [[*RUN, "--no-paraphrase", "--no-reversal"]],
    "run/every-stage-off": [[*RUN, *NO_STAGES]],
    "run/arms-without-full": [[*RUN, "--arms", "no_artifact"]],
    "atoms/default": [ATOMS],
    "atoms/max-atoms-1": [[*ATOMS, "--max-atoms", "1"]],
    "atoms/all-atoms-beats-max": [[*ATOMS, "--all-atoms", "--max-atoms", "1"]],
    "atoms/no-verify": [[*ATOMS, "--no-verify"]],
    "probe/default": [ATOMS, PROBE],
    "probe/no-paraphrase": [ATOMS, [*PROBE, "--no-paraphrase"]],
    "probe/no-reversal": [ATOMS, [*PROBE, "--no-reversal"]],
    "probe/no-probe-wordings": [ATOMS, [*PROBE, "--no-paraphrase", "--no-reversal"]],
    "probe/arms-without-baseline": [ATOMS, [*PROBE, "--arms", "full"]],
}


def _kept(argv: list[str], work: Path) -> dict[str, Any]:
    """What the flags decided in the files one invocation wrote."""
    if argv[1] == "analyze" and (work / "out" / "novelty.json").exists():
        artifact = json.loads((work / "out" / "novelty.json").read_text(encoding="utf-8"))
        run_config = dict(artifact["run_config"])
        run_config.pop("prompts_sha256")   # a prompt edit elsewhere is not this module's
        return {"run_config": run_config, "warnings": artifact["warnings"]}
    if argv[1] == "atoms" and (work / "atoms.json").exists():
        saved = json.loads((work / "atoms.json").read_text(encoding="utf-8"))
        return {"cap": saved.get("cap"), "n_atoms": len(saved["atoms"]),
                "faithfulness": sorted({str(a.get("faithfulness")) for a in saved["atoms"]})}
    if argv[1] == "probe" and (work / "p.json").exists():
        saved = json.loads((work / "p.json").read_text(encoding="utf-8"))
        return {"probe_kinds": sorted({run["probe_kind"] for run in saved["runs"]}),
                "arms": sorted({run["arm"] for run in saved["runs"]})}
    return {}


COVERAGE_LINES = ("coverage: ", "WARNING: only ")


def _without_coverage_headline(text: str) -> str:
    """Drop the stdout lines the coverage headline added after main.

    They say how much of the artifact was read, which is neither a setting, a bill nor a
    tier record, so the comparison with main leaves them out. They cannot be in main's
    golden, so ``_headline_coverage`` checks them against the record they were printed from.
    """
    return "".join(line for line in text.splitlines(keepends=True)
                   if not line.startswith(COVERAGE_LINES))


def _headline_coverage(text: str, work: Path) -> dict[str, Any]:
    """The coverage lines stdout printed, beside the ``bundle`` figures in the record."""
    printed = [line for line in text.splitlines() if line.startswith(COVERAGE_LINES)]
    record = work / "out" / "novelty.json"
    saved = json.loads(record.read_text(encoding="utf-8")) if record.exists() else {}
    extraction = saved.get("extraction") or {}
    return {"printed": printed, "measured_share": (saved.get("bundle") or {}).get("measured_share"),
            "capped": bool(extraction.get("reached") or extraction.get("sampled"))}


def _check_headline(coverage: dict[str, Any]) -> None:
    """Each dropped line agrees with the measured share the same run stamped in its record."""
    printed, share = coverage["printed"], coverage["measured_share"]
    if share is None:
        # No record to check against (an estimate, or a run that wrote none): nothing printed.
        assert printed == []
        return
    # An integer floor of the decimal share, not a float product (0.29 * 100 < 29).
    pct = f"{int(Decimal(str(share)).scaleb(2).to_integral_value(ROUND_FLOOR))}%"
    assert printed, "a run with a record must print its coverage line"
    # A capped run's words went into the input; its atoms do not cover all of them.
    went = "went into the input" if coverage["capped"] else "were measured"
    assert printed[0].startswith(
        f"coverage: {pct} of the artifact's readable words {went}"
    ), printed
    warned = [line.startswith(f"WARNING: only {pct} ") for line in printed[1:]]
    assert warned == ([True] if share <= 0.5 else []), printed


def _capture(name: str, tmp_path: Path) -> list[dict[str, Any]]:
    work = tmp_path / name.replace("/", "-")
    (work / "accel-tuning").mkdir(parents=True)
    (work / "accel-tuning" / "SKILL.md").write_text(SKILL, encoding="utf-8")
    (work / "big").mkdir()
    (work / "big" / "SKILL.md").write_text(_big_skill(), encoding="utf-8")
    clients: list = []
    real_client = novelty_cmd._client

    def scripted(model, provider, role, args):
        client = real_client(model, provider, role, args)
        client.transport = _scripted("informed")
        clients.append(client)
        return client

    steps = []
    cwd = os.getcwd()
    novelty_cmd._client = scripted
    os.chdir(work)
    try:
        for argv in CASES[name]:
            clients.clear()
            out, err = io.StringIO(), io.StringIO()
            status: object = 0
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                try:
                    args = build_parser().parse_args(argv)
                    args.func(args)
                except SystemExit as exc:
                    status = exc.code if isinstance(exc.code, int) else str(exc.code)
            steps.append({
                "status": status,
                "stdout": (_without_coverage_headline(out.getvalue())
                           if argv[1] == "analyze" else None),
                "coverage": (_headline_coverage(out.getvalue(), work)
                             if argv[1] == "analyze" else None),
                "stderr": err.getvalue().replace(str(work), "<work>"),
                "calls": [[c.role, c.calls] for c in clients],
                **_kept(argv, work),
            })
    finally:
        os.chdir(cwd)
        novelty_cmd._client = real_client
    return steps


# The stderr note the estimate prints: the one whose wording changed is cut from both sides.
_ESTIMATE_NOTES = ("The estimate counts calls", "expected_usd prices")
# The bill the estimate now projects, and the atom count and totals that move with it.
_BILL_KEYS = ("tokens", "expected_usd")
_ESTIMATE_BILL_KEYS = (
    "by_role",
    "expected_calls",
    "n_atoms",
    "total_calls",
    "total_calls_if_every_pair_escalates",
    # Extraction asks every batch under a cap too (#293); tests/test_novelty_tiers.py pins it.
    "stage_calls",
)


def _kept_estimate(step: dict) -> dict:
    """An estimate step with only the bill removed; the rest of the payload is compared whole.

    The settings echo on stderr, the tier record with its overrides, the forfeits and the
    caveat all stay. A refusal has no payload and is kept whole.
    """
    if not step["stdout"]:
        return step
    payload = json.loads(step["stdout"])
    for key in _BILL_KEYS:
        payload.pop(key, None)
    payload["estimate"] = {
        key: value for key, value in payload["estimate"].items() if key not in _ESTIMATE_BILL_KEYS
    }
    stderr = step["stderr"]
    for note in _ESTIMATE_NOTES:
        stderr = stderr.split(note)[0]
    return {**step, "stdout": payload, "stderr": stderr}


# Runs main refuses only after the model calls it had already made (the extraction of `analyze`,
# the probe wordings of `probe`).
REFUSED_BEFORE_ANY_CALL = {"run/arms-without-full", "probe/arms-without-baseline"}
# Input refusals from the arm-set checks: main raised the message as the exit status, visible only at interpreter exit.
# The exit-code table (docs/quickstart.md) makes these exit 2, and usage_exit prints the message before raising.
ARMS_REFUSALS = {"estimate/arms-without-full", "estimate/arms-without-baseline",
                 "probe/arms-without-baseline", "run/arms-without-full"}


def _refusal_as_exit_2(step: dict[str, Any]) -> dict[str, Any]:
    if isinstance(step["status"], str):
        return {**step, "status": 2, "stderr": step["stderr"] + step["status"] + "\n"}
    return step


# `novelty atoms` runs whose cap binds.
CAPPED_ATOMS = {"atoms/max-atoms-1"}
# Intended change: the cap warning says how far the cap reached instead of calling the
# atoms a sample. In the warnings and on stderr alike.
CAP_WARNING_ON_MAIN = ("atom extraction hit the cap of 1; rates describe the sampled atoms, "
                       "not the whole artifact")
# Intended change (#293): the cap probes atoms spread over the input, and the warning says so.
CAP_WARNING = ("atom extraction read the whole input, and the atom cap probes 1 of the input's 2 "
               "atoms, spread over all of it (a stride of 2 atoms in document order): rates are "
               "estimated from that sample; pass --all-atoms to probe every atom")
# Intended change: the cap's forfeit no longer calls `added` a sampled rate, in the tier
# record, the warnings and the estimate's stderr.
CAP_FORFEIT_ON_MAIN = "`added` is a sampled rate over the atom cap: usable"
CAP_FORFEIT = "`added` is a rate over the atoms the cap kept, not the whole input: usable"
# Intended change (#293): shortening is scored against a spread sample, not the opening. The
# forfeit names the cap, which tests/test_novelty_tiers.py pins; here both sides read <cap>.
SHORTEN_FORFEIT_ON_MAIN = ("shortening candidates are scored only against the capped atoms, i.e. "
                           "the opening of the artifact: a cut of the uncovered tail looks free")
SHORTEN_FORFEIT = ("shortening candidates are scored against a spread sample of <cap> of the "
                   "extracted atoms: a cut of a span with no sampled atom looks free")


# Intended change (#293): a cap of 1 on two atoms probes the second (s0003, mid-stride), not the first,
# so what follows the atom (dead tokens, oracle calls, the warnings on it) is not main's.
SPREAD_RUNS = {"run/max-atoms-1", "run/triage-max-atoms-1"}


def _settings_only(step: dict) -> dict:
    """What the flags decide in a run step: its status, run_config and tier and cap warnings."""
    return {"status": step["status"], "run_config": step.get("run_config"),
            "warnings": [w for w in step.get("warnings", [])
                         if w.startswith("tier `") or w == CAP_WARNING]}


def _cap_named(steps: list) -> list:
    return json.loads(re.sub(r"spread sample of \d+ of", "spread sample of <cap> of",
                             json.dumps(steps)))


@pytest.mark.parametrize("name", list(CASES))
def test_no_setting_bill_or_tier_record_differs_from_main(name, tmp_path):
    golden = json.loads(json.dumps(json.loads(GOLDEN.read_text(encoding="utf-8")))
                        .replace(CAP_FORFEIT_ON_MAIN, CAP_FORFEIT)
                        .replace(SHORTEN_FORFEIT_ON_MAIN, SHORTEN_FORFEIT))
    steps = _cap_named(_capture(name, tmp_path))
    coverage = [step.pop("coverage") for step in steps]
    for step_coverage in coverage:
        if step_coverage is not None:
            _check_headline(step_coverage)
    expected = [_refusal_as_exit_2(step) if name in ARMS_REFUSALS else step
                for step in golden[name]]
    if name.startswith("estimate/"):
        assert [_kept_estimate(step) for step in steps] == [
            _kept_estimate(step) for step in expected]
    else:
        expected = json.loads(json.dumps(expected).replace(CAP_WARNING_ON_MAIN, CAP_WARNING))
        if name in REFUSED_BEFORE_ANY_CALL:
            # Intended change: the arm and placebo checks now run before the clients are
            # built, so the refusal makes no model call. Same status and output as on main.
            expected = [*expected[:-1], dict(expected[-1], calls=[])]  # the refused step is the last
        if name in CAPPED_ATOMS:
            # Intended change: `novelty atoms` prints the cap warning `analyze` records.
            expected = [dict(step, stderr=f"{step['stderr']}WARNING {CAP_WARNING}\n")
                        for step in expected]
        if name in SPREAD_RUNS:
            steps, expected = ([_settings_only(step) for step in side] for side in (steps, expected))
            assert any(CAP_WARNING in step["warnings"] for step in steps)
        assert steps == expected


def test_the_golden_cases_are_the_ones_this_module_runs():
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert sorted(golden) == sorted(CASES)


if __name__ == "__main__" and sys.argv[1:] == ["--write"]:
    import tempfile

    tmp = Path(tempfile.mkdtemp())
    captured = {
        name: [{k: v for k, v in step.items() if k != "coverage"} for step in _capture(name, tmp)]
        for name in CASES
    }
    GOLDEN.write_text(json.dumps(captured, indent=1, sort_keys=True, ensure_ascii=False)
                      + "\n", encoding="utf-8")
    print(f"{len(captured)} cases -> {GOLDEN}")
