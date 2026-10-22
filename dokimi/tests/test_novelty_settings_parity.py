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
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
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


def _without_coverage_headline(text: str) -> str:
    """Drop the stdout lines the coverage headline added after main.

    They say how much of the artifact was read, which is neither a setting, a bill nor a
    tier record, so this module does not compare them; ``test_novelty_journey`` pins them.
    """
    return "".join(line for line in text.splitlines(keepends=True)
                   if not line.startswith(("coverage: ", "WARNING: only ")))


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
                "stderr": err.getvalue().replace(str(work), "<work>"),
                "calls": [[c.role, c.calls] for c in clients],
                **_kept(argv, work),
            })
    finally:
        os.chdir(cwd)
        novelty_cmd._client = real_client
    return steps


@pytest.mark.parametrize("name", list(CASES))
def test_no_setting_bill_or_tier_record_differs_from_main(name, tmp_path):
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert _capture(name, tmp_path) == golden[name]


def test_the_golden_cases_are_the_ones_this_module_runs():
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert sorted(golden) == sorted(CASES)


if __name__ == "__main__" and sys.argv[1:] == ["--write"]:
    import tempfile

    tmp = Path(tempfile.mkdtemp())
    captured = {name: _capture(name, tmp) for name in CASES}
    GOLDEN.write_text(json.dumps(captured, indent=1, sort_keys=True, ensure_ascii=False)
                      + "\n", encoding="utf-8")
    print(f"{len(captured)} cases -> {GOLDEN}")
