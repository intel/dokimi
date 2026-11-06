# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`refusal()` (tests/cli_refusal.py) answers an input refusal as `cli.main` does.

Tests that call a command through `refusal()` rely on it standing in for `cli.main`. These run
the same argv both ways and require the same exit code, stdout and stderr, so the helper cannot drift.
"""

from __future__ import annotations

import sys

import pytest

from dokimi import cli
from tests.cli_refusal import refusal


def _assert_helper_matches_main(argv, monkeypatch, capsys, expect):
    with refusal() as outcome:
        args = cli.parse_args(argv)
        args.func(args)
    capsys.readouterr()  # drop the output the helper re-emitted
    monkeypatch.setattr(sys, "argv", ["dokimi", *argv])
    code = 0
    try:
        cli.main()
    except SystemExit as exc:
        code = exc.code
    captured = capsys.readouterr()
    assert outcome.code == 2
    assert outcome.err.startswith("Error: ") and expect in outcome.err, outcome.err
    assert (outcome.code, outcome.out, outcome.err) == (code, captured.out, captured.err)


@pytest.mark.parametrize("argv_for, expect", [
    (lambda d: ["data", "export", "--kind", "answers", "--input", str(d / "missing.json"),
                "--out-dir", str(d / "out")], "cannot export"),
    (lambda d: ["eval", "judge", "validate", "--answers", str(d / "a.json"), "--mode", "absolute",
                "--escalate-model", "m"], "need --mode pairwise"),
], ids=["data-export-missing-input", "judge-validate-escalation-needs-pairwise"])
def test_helper_matches_cli_main_for_converted_commands(argv_for, expect, tmp_path, monkeypatch, capsys):
    _assert_helper_matches_main(argv_for(tmp_path), monkeypatch, capsys, expect)
