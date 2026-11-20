# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Spend controls of the paid CLI leaves (issue #940, VISION.md: spend "should be configurable").

``scripts/cli_spend_controls.py`` joins the parser tree to ``cli_routes.cost_of``. A paid
leaf without a dollar cap flag must be written out in ``UNCAPPED`` below, so a new paid
command cannot ship without one unnoticed. Remove a path from the list when its leaf gets
a ``--max-usd``; the test fails on a stale row too.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "cli_spend_controls", PROJECT / "scripts" / "cli_spend_controls.py"
)
assert _spec and _spec.loader
scs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scs)

# Paid leaves with no cap flag yet. Reason for all: the cost is not metered per invocation.
UNCAPPED = frozenset(
    """
answers generate
arms matrix-run
arms run
benchmark batch
benchmark run
catalog trigger-probe
eval judge agreement
eval judge validate
eval judge wording
eval personas discover
eval questions analyze
eval questions generate
eval questions refine
eval questions review
eval redteam
eval score checklist
eval score pairwise
eval score panel
eval score pointwise
eval score ragas
eval score reference
eval triggers generate
evaluate
explore run
repo reach
repo skill-reach
subjects run
tasks run
""".split("\n")
) - {""}


def test_the_uncapped_paid_commands_are_exactly_the_ones_written_out_here():
    """Issue #940: a paid leaf without a cap flag must be listed (VISION.md: spend should be configurable)."""
    found = {r["path"] for r in scs.paid_leaves() if not r["capped"]}
    assert sorted(found - UNCAPPED) == [], "paid leaf without a cap flag, add one or list it"
    assert sorted(UNCAPPED - found) == [], "listed leaf now has a cap flag or is not paid"


def test_summary_counts_capped_uncapped_and_no_control():
    """Issue #940: `summary` counts capped, uncapped and no-control leaves."""
    rows = [
        {"path": "a", "capped": True, "preview": False},
        {"path": "b", "capped": False, "preview": True},
        {"path": "c", "capped": False, "preview": False},
    ]
    assert scs.summary(rows) == {"paid": 3, "capped": 1, "uncapped": 2, "no_control": 1}


def test_summary_of_no_paid_leaf_is_all_zero():
    """Issue #940: no paid leaf gives all-zero counts."""
    assert scs.summary([]) == {"paid": 0, "capped": 0, "uncapped": 0, "no_control": 0}


def test_json_output_ends_with_the_summary_line(capsys):
    """Issue #940: `--json` prints one row per leaf, then the summary line."""
    assert scs.main(["--json"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert json.loads(lines[-1]) == scs.summary(scs.paid_leaves())
    assert all(json.loads(x)["path"] for x in lines[:-1])


def test_the_inventory_counts_every_paid_leaf_of_the_parser_tree():
    """Issue #940: 39 paid leaves, 11 capped, as `cli_routes.cost_of` and the parser tree give."""
    assert scs.summary(scs.paid_leaves()) == {
        "paid": 39,
        "capped": 11,
        "uncapped": 28,
        "no_control": 23,
    }


def test_cap_and_preview_are_read_from_the_flags_of_a_real_command():
    """Issue #940: cap and preview come from the flags a real command takes."""
    rows = {r["path"]: r for r in scs.paid_leaves()}
    assert rows["evaluate"] == {"path": "evaluate", "capped": False, "preview": False}
    assert rows["tasks run"]["preview"] is True and rows["tasks run"]["capped"] is False
    assert rows["tools preference"] == {"path": "tools preference", "capped": True, "preview": True}
    assert rows["repo run"] == {"path": "repo run", "capped": True, "preview": False}


def test_every_cap_flag_is_taken_by_some_leaf_of_the_parser_tree():
    """Issue #940: a cap flag no leaf takes any more (a renamed `--budget`) is dropped from `CAP_FLAGS`."""
    taken = set().union(*(flags for _, flags in scs._leaves(scs.cli.build_parser())))
    assert sorted(scs.CAP_FLAGS - taken) == []
