# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The texts that point a user at the tested mypy pin. No LLM."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from packaging.requirements import Requirement

from dokimi.cli import build_parser

_PROJECT = Path(__file__).resolve().parents[1]


def test_the_help_and_the_contract_name_no_mypy_pin_but_the_tested_one(
        capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    """A dependency bump moves requirements-test.txt only, so a text naming another mypy pin is stale."""
    monkeypatch.setenv("COLUMNS", "100000")  # argparse would wrap "requirements-test.txt" at its hyphen
    lines = (_PROJECT / "requirements-test.txt").read_text(encoding="utf-8").splitlines()
    specs = (Requirement(s) for s in lines if s.strip() and not s.lstrip().startswith("#"))
    pin = next(str(r.specifier) for r in specs if r.name == "mypy")
    with pytest.raises(SystemExit):
        build_parser().parse_args(["repo", "scan", "--help"])
    texts = {"repo scan --help": capsys.readouterr().out,
             "contract": (_PROJECT / "docs" / "repo-evaluation-contract.md").read_text(encoding="utf-8")}
    assert all("requirements-test.txt" in text for text in texts.values())
    stated = {name: re.findall(r"mypy\s*(==\s*\d[\w.]*\w)", " ".join(text.split())) for name, text in texts.items()}
    assert {name: found for name, found in stated.items() if any(f.replace(" ", "") != pin for f in found)} == {}
