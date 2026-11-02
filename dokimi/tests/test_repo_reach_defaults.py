# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The documented defaults of `repo reach`: the parser leaves the probe flags unset, and a probe fills them in."""

from __future__ import annotations

import json

import pytest
from tests.test_repo_reach import _repo

from dokimi.cli import build_parser


@pytest.mark.outcome("cjm-repo-reach")
def test_repo_reach_bare_dry_run_plans_the_documented_defaults(tmp_path, capsys):
    args = build_parser().parse_args(["repo", "reach", str(_repo(tmp_path)), "--manifest", "evals/repo-eval.yaml",
                                      "--model", "m", "--dry-run"])
    args.func(args)
    runs = json.loads(capsys.readouterr().out)["runs"]
    assert runs
    ids = [run["scenario"] for run in runs]
    assert "cwd=." in ids                                           # the root kind
    assert any(i.startswith("cwd=") and i != "cwd=." for i in ids)  # the cwd kind
    assert any(i.startswith("read=") for i in ids)                  # the read kind
    for run in runs:
        argv = run["argv"]
        assert argv[argv.index("--max-budget-usd") + 1] == "0.1"
        assert argv[argv.index("--max-turns") + 1] == "4"
    assert (args.timeout, args.scenarios, args.budget, args.max_turns) == (300, "root,cwd,read", 0.1, 4)
