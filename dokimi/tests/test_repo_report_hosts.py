# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`repo report` rows split by host fingerprint: what a row shows and which missing cells it counts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dokimi.repos.host import fingerprint_id
from dokimi.repos.report import CellFileError, _check_host_fields, format_report, summarize

HOST_A = {"cpus": 4, "cpu_vendor": "GenuineIntel", "collectors": {"os": {"system": "Linux"}}}
HOST_B = {"cpus": 8, "cpu_vendor": "AuthenticAMD", "collectors": {"os": {"system": "Linux"}}}
FP_A, FP_B = fingerprint_id(HOST_A), fingerprint_id(HOST_B)


def _cell(batch, rep, *, host=None, fingerprint=None, result=True, **extra):
    rd = batch / f"t__guidance-raw__m-1__r{rep}"
    rd.mkdir(parents=True)
    (rd / "meta.json").write_text(json.dumps({"host": host}))
    if result:
        rec = {"task": "t", "arm": "guidance:raw", "model": "m-1", "score_task": False, "score_max": None,
               "grade": {"status": "graded", "success": True, "score_only": False, "score": None},
               "agent": {"timed_out": False, "is_error": False, "over_budget": False, "cost_usd": 0.2,
                         "num_turns": 3}, "host_fingerprint": fingerprint, **extra}
        (rd / "result.json").write_text(json.dumps(rec))


def _plan(batch, reps):
    (batch / "_eval").mkdir(parents=True, exist_ok=True)
    cells = [f"t__guidance-raw__m-1__r{r}" for r in reps]
    (batch / "_eval" / "plan.json").write_text(json.dumps({"cells": cells}))


def test_a_started_missing_cell_counts_on_the_row_of_the_host_that_started_it(tmp_path):
    batch = tmp_path / "b1"
    _cell(batch, 1, host=HOST_A, fingerprint=FP_A)
    _cell(batch, 2, host=HOST_B, fingerprint=FP_B)
    _cell(batch, 3, host=HOST_B, result=False)  # started on B, never finished
    rows = {r["host_fingerprint"]: r for r in summarize(batch)["rows"]}
    assert {fp: r["missing"] for fp, r in rows.items()} == {FP_A: 0, FP_B: 1}


def test_an_unstarted_missing_cell_is_on_no_row_when_two_hosts_could_own_it(tmp_path):
    batch = tmp_path / "b1"
    _cell(batch, 1, host=HOST_A, fingerprint=FP_A)
    _cell(batch, 2, host=HOST_B, fingerprint=FP_B)
    _plan(batch, [1, 2, 3])
    summary = summarize(batch)
    assert [r["missing"] for r in summary["rows"]] == [0, 0]
    assert summary["missing"] == ["t__guidance-raw__m-1__r3"]


def test_an_unstarted_missing_cell_stays_on_the_only_row(tmp_path):
    batch = tmp_path / "b1"
    _cell(batch, 1, host=HOST_A, fingerprint=FP_A)
    _plan(batch, [1, 2])
    assert [r["missing"] for r in summarize(batch)["rows"]] == [1]


def test_a_started_missing_cell_whose_host_has_no_row_counts_on_the_only_row(tmp_path):
    batch = tmp_path / "b1"
    _cell(batch, 1, host=HOST_A, fingerprint=FP_A)
    _cell(batch, 2, host=HOST_B, result=False)  # started on B, crashed before result.json; B has no finished cell
    summary = summarize(batch)
    assert [(r["host_fingerprint"], r["missing"]) for r in summary["rows"]] == [(FP_A, 1)]
    assert summary["missing"] == ["t__guidance-raw__m-1__r2"]


def test_text_report_names_the_host_of_every_fingerprinted_row_without_label_mode(tmp_path):
    batch = tmp_path / "b1"
    _cell(batch, 1, host=HOST_A, fingerprint=FP_A)
    _cell(batch, 2, host=HOST_B, fingerprint=FP_B)
    _cell(batch, 3)  # written before fingerprints: no host to name
    lines = format_report(summarize(batch)).splitlines()
    assert [ln.strip() for ln in lines if ln.strip().startswith("host ")] == sorted(
        [f"host {FP_A}", f"host {FP_B}"])


def test_a_labelled_row_keeps_its_verdict_and_an_undecided_one_is_not_unmet(tmp_path):
    batch = tmp_path / "b1"
    miss = {"met": False, "fields": [{"field": "cpu_flags.avx512f", "met": False}]}
    unknown = {"met": None, "fields": [{"field": "min_cpus", "met": None}]}
    _cell(batch, 1, host=HOST_A, fingerprint=FP_A, host_match=miss)
    _cell(batch, 2, host=HOST_B, fingerprint=FP_B, host_match=unknown)
    text = format_report(summarize(batch))
    assert f"host {FP_A} (labelled, unmet: cpu_flags.avx512f)" in text
    assert f"host {FP_B} (labelled, undecided)" in text


def test_a_missing_cell_whose_meta_host_is_malformed_is_placed_on_no_host_row(tmp_path):
    batch = tmp_path / "b1"
    _cell(batch, 1, host=HOST_A, fingerprint=FP_A)
    _cell(batch, 2, host={"collectors": [1]}, result=False)
    _cell(batch, 3, host=[1], result=False)
    summary = summarize(batch)
    assert [r["missing"] for r in summary["rows"]] == [2]  # unplaced, and the only row takes them
    assert len(summary["missing"]) == 2


def test_a_missing_cell_with_container_only_host_facts_counts_on_the_unfingerprinted_row(tmp_path):
    batch = tmp_path / "b1"
    legacy = {"cpus": 4, "cpu_vendor": "GenuineIntel"}  # facts from before the collectors
    _cell(batch, 1, host=legacy)  # its result.json has no host_fingerprint
    _cell(batch, 2, host=legacy, result=False)
    summary = summarize(batch)
    assert [(r["host_fingerprint"], r["missing"]) for r in summary["rows"]] == [(None, 1)]


def test_a_legacy_missing_cell_counts_on_the_unfingerprinted_row_beside_a_fingerprinted_one(tmp_path):
    batch = tmp_path / "b1"
    legacy = {"cpus": 4, "cpu_vendor": "GenuineIntel"}  # a pre-collector batch resumed by this version
    _cell(batch, 1, host=legacy)
    _cell(batch, 2, host=legacy, result=False)
    _cell(batch, 3, host=HOST_A, fingerprint=FP_A)
    rows = {r["host_fingerprint"]: r["missing"] for r in summarize(batch)["rows"]}
    assert rows == {None: 1, FP_A: 0}


def test_a_host_match_without_met_is_refused():
    with pytest.raises(CellFileError, match="host_match"):
        _check_host_fields(Path("r.json"), {"host_fingerprint": None, "host_match": {"fields": []}})
