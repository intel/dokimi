# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`repo report` text table: how a score task's mean is written in the result column."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from dokimi.repos.report import _format_score, format_report, summarize
from tests.test_repo_compare import _cell


def _result_texts(batch: Path) -> list[str]:
    return re.findall(r"score \S+", format_report(summarize(batch)))


def test_a_mean_near_1e_4_keeps_two_significant_digits(tmp_path):
    batch = tmp_path / "b1"
    for rep in range(8):
        _cell(batch, "t", "guidance:raw", rep, score=1e-4 + rep * 6e-4 / 7)
    texts = _result_texts(batch)
    assert texts == ["score 0.0004"]  # mean of 1e-4 .. 7e-4; 2 decimals would read `score 0.00`


@pytest.mark.parametrize(("score", "text"), [(1e-4, "score 0.0001"), (1e-12, "score 1e-12")])
def test_a_tiny_score_row_fits_the_result_column(tmp_path, score, text):
    batch = tmp_path / "b1"
    _cell(batch, "t", "guidance:raw", 1, score=0.5)
    summary = summarize(batch)
    summary["rows"][0]["score"] = score  # summarize rounds a mean to 4 decimals, so 1e-12 is set on the row
    line = next(ln for ln in format_report(summary).splitlines() if "score" in ln and "t " in ln)
    assert f"  {text:<22}  " in line


@pytest.mark.parametrize(("score", "text"), [(0.5, "score 0.50"), (0.0, "score 0.00"), (1.0, "score 1.00"),
                                            (0.83, "score 0.83")])
def test_an_ordinary_score_prints_at_two_decimals(tmp_path, score, text):
    batch = tmp_path / "b1"
    _cell(batch, "t", "guidance:raw", 1, score=score)
    assert _result_texts(batch) == [text]


def test_a_negative_tiny_value_keeps_its_sign_and_digits():
    assert [_format_score(v) for v in (-1e-4, -0.5, 0.0)] == ["-0.0001", "-0.50", "0.00"]
