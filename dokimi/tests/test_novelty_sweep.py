# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The corpus sweep's bookkeeping: rows, resume, strata and the summary. No model call."""

from __future__ import annotations

import json

import pytest

from dokimi.novelty.client import NestedSpendCap, SpendCap, SpendCapReached
from dokimi.metrics.usage import UsageRecord
from dokimi.novelty.sweep import (
    ALL, UNASSIGNED, group_stats, load_strata, make_row, measured_fields, new_share,
    read_rows, resumable, stratum_of, summarize, summary_lines,
)
from dokimi.utils import InputError

SETTINGS = {"max_atoms": 120}


def _ok(key, new, usd=0.1, tokens=800, name=None, digest="d1"):
    measured = {"tokens": tokens, "known_lenient": round(1 - new, 6), "new_lenient": new,
                "new_strict": round(min(1.0, new + 0.1), 6), "n_paired": 4}
    return make_row(key=key, name=name or key, path=f"/c/{key}/SKILL.md", skill_sha256=digest,
                    status="ok", settings=SETTINGS, version="1.0", usd=usd, calls=3,
                    measured=measured, novelty_json=f"/out/{key}/novelty.json")


def _row(key, status, **kw):
    return make_row(key=key, name=key, path=f"/c/{key}/SKILL.md", skill_sha256="d1",
                    status=status, settings=SETTINGS, version="1.0", usd=kw.pop("usd", 0.05), **kw)


def test_new_share_is_one_minus_the_already_known_share():
    assert new_share(0.66) == pytest.approx(0.34)
    assert new_share(None) is None


def test_measured_fields_read_the_lenient_and_strict_k0_and_the_pairs():
    artifact = {"k0": {"lenient": 0.25, "strict": 0.1}, "paired": {"lenient": {"n_paired": 4}},
                "artifact": {"tokens": 900}}
    fields = measured_fields(artifact)
    assert fields["known_lenient"] == 0.25
    assert fields["new_lenient"] == pytest.approx(0.75)
    assert fields["new_strict"] == pytest.approx(0.9)
    assert (fields["n_paired"], fields["tokens"]) == (4, 900)


def test_measured_fields_of_an_artifact_without_k0_has_no_share():
    assert measured_fields({})["new_lenient"] is None


def test_an_unpriced_call_charged_through_a_child_stops_the_parent():
    parent = SpendCap(limit_usd=10.0)
    child = NestedSpendCap(limit_usd=1.0, parent=parent)
    child.record(UsageRecord(cost_usd=0.4), "m")
    assert (child.spent_usd, parent.spent_usd) == (0.4, 0.4)
    with pytest.raises(SpendCapReached, match="no price"):
        child.record(None, "m")
    with pytest.raises(SpendCapReached, match="no known price"):
        parent.before_call("m")


def test_the_child_cap_checks_its_own_limit_as_well_as_the_parent():
    parent = SpendCap(limit_usd=10.0)
    child = NestedSpendCap(limit_usd=0.5, parent=parent)
    child.record(UsageRecord(cost_usd=0.5), "m")
    with pytest.raises(SpendCapReached, match="spend cap reached"):
        child.before_call("m")
    assert parent.spent_usd == 0.5


def test_the_parent_refusal_stops_a_child_that_still_has_room():
    parent = SpendCap(limit_usd=0.5)
    child = NestedSpendCap(limit_usd=5.0, parent=parent)
    parent.record(UsageRecord(cost_usd=0.5), "m")
    with pytest.raises(SpendCapReached):
        child.before_call("m")


def test_load_strata_maps_each_skill_to_its_stratum(tmp_path):
    path = tmp_path / "strata.csv"
    path.write_text("skill,stratum\nops/deploy,ops\nreview,dev\n", encoding="utf-8")
    assert load_strata(path, "stratum") == {"ops/deploy": "ops", "review": "dev"}


def test_load_strata_refuses_a_missing_column(tmp_path):
    path = tmp_path / "strata.csv"
    path.write_text("skill,tier\nreview,dev\n", encoding="utf-8")
    with pytest.raises(InputError, match="needs a `skill` column and a `stratum` column"):
        load_strata(path, "stratum")


def test_load_strata_refuses_skill_as_the_stratum_column(tmp_path):
    path = tmp_path / "strata.csv"
    path.write_text("skill,skill\nreview,dev\n", encoding="utf-8")
    with pytest.raises(InputError, match="must not be `skill`"):
        load_strata(path, "skill")


def test_load_strata_refuses_a_skill_in_two_strata(tmp_path):
    path = tmp_path / "strata.csv"
    path.write_text("skill,stratum\nreview,dev\nreview,ops\n", encoding="utf-8")
    with pytest.raises(InputError, match="is in two strata"):
        load_strata(path, "stratum")


def test_load_strata_refuses_an_empty_cell(tmp_path):
    path = tmp_path / "strata.csv"
    path.write_text("skill,stratum\nreview,\n", encoding="utf-8")
    with pytest.raises(InputError, match="must both be set"):
        load_strata(path, "stratum")


def test_stratum_of_matches_by_key_then_name_and_falls_back_to_unassigned():
    strata = {"ops/deploy": "ops", "review": "dev"}
    assert stratum_of({"key": "ops/deploy", "name": "deploy"}, strata) == "ops"
    assert stratum_of({"key": "x/review", "name": "review"}, strata) == "dev"
    assert stratum_of({"key": "other", "name": "other"}, strata) == UNASSIGNED
    assert stratum_of({"key": "other", "name": "other"}, None) == ALL


def test_read_rows_drops_a_last_line_a_killed_run_left_half_written(tmp_path):
    path = tmp_path / "sweep.jsonl"
    path.write_text(json.dumps(_ok("a", 0.3)) + "\n" + '{"key": "b", "sta', encoding="utf-8")
    rows = read_rows(path)
    assert [r["key"] for r in rows] == ["a"]


def test_read_rows_refuses_a_bad_line_that_is_not_the_last(tmp_path):
    path = tmp_path / "sweep.jsonl"
    path.write_text('{"key": "b", "sta\n' + json.dumps(_ok("a", 0.3)) + "\n", encoding="utf-8")
    with pytest.raises(InputError, match="line 1 is not a sweep row"):
        read_rows(path)


def test_read_rows_refuses_a_line_that_is_not_a_row(tmp_path):
    path = tmp_path / "sweep.jsonl"
    path.write_text(json.dumps({"hello": 1}) + "\n", encoding="utf-8")
    with pytest.raises(InputError, match="is not a sweep row"):
        read_rows(path)


def test_read_rows_of_a_missing_file_is_empty(tmp_path):
    assert read_rows(tmp_path / "absent.jsonl") == []


def test_resumable_keeps_only_measured_rows_for_the_same_skill_digest_settings_and_version():
    good = _ok("keep", 0.3)
    changed_skill = _ok("changed", 0.3, digest="old")
    errored = _row("errored", "error")
    partial = _row("partial", "partial")
    gone = _ok("gone", 0.3)
    old_version = dict(_ok("version", 0.3), dokimi_version="0.9")
    prior = [good, changed_skill, errored, partial, gone, old_version]
    selected = {"keep": "d1", "changed": "d2", "errored": "d1", "partial": "d1", "version": "d1"}
    kept = resumable(prior, selected, SETTINGS, "1.0")
    assert [r["key"] for r in kept] == ["keep"]
    assert resumable(prior, selected, {"max_atoms": 90}, "1.0") == []


def test_group_stats_bootstraps_the_new_share_and_flips_the_interval_for_the_known_share():
    rows = [_ok("a", 0.2), _ok("b", 0.4), _ok("c", 0.6)]
    stats = group_stats(rows, resamples=300)
    assert stats["n"] == 3
    assert stats["new_lenient"]["mean"] == pytest.approx(0.4, abs=1e-3)
    known, new = stats["known_lenient"], stats["new_lenient"]
    assert known["mean"] == pytest.approx(1 - new["mean"], abs=1e-3)
    assert known["lo"] == pytest.approx(1 - new["hi"], abs=1e-3)
    assert known["hi"] == pytest.approx(1 - new["lo"], abs=1e-3)
    assert stats["usd"] == pytest.approx(0.3)
    assert stats["median_tokens"] == 800


def test_summarize_counts_every_status_but_measures_only_ok_rows():
    rows = [_ok("a", 0.2, usd=0.3), _row("b", "partial", usd=0.1), _row("c", "error", usd=0.0)]
    summary = summarize(rows, resamples=200)
    assert summary["counts"] == {"ok": 1, "partial": 1, "error": 1}
    assert summary["usd"] == pytest.approx(0.4)
    assert summary["overall"]["n"] == 1
    assert summary["strata"] is None


def test_summarize_splits_the_measure_by_stratum_and_names_the_unassigned():
    rows = [_ok("ops/deploy", 0.2), _ok("review", 0.6), _ok("misc", 0.5)]
    summary = summarize(rows, {"ops/deploy": "ops", "review": "dev"}, resamples=200)
    assert set(summary["strata"]) == {"dev", "ops", UNASSIGNED}
    assert summary["strata"]["dev"]["n"] == 1
    assert summary["strata"]["dev"]["new_lenient"]["mean"] == pytest.approx(0.6)


def test_summary_lines_say_when_nothing_was_measured():
    lines = summary_lines(summarize([_row("a", "error")], resamples=50))
    assert lines[0].startswith("# novelty sweep: 0 measured, 0 partial, 1 error")
    assert lines[1] == "no skill was measured: no share to report"


def test_summary_lines_lead_with_the_already_known_share():
    lines = summary_lines(summarize([_ok("a", 0.34)], resamples=50))
    assert lines[1].startswith("already known by the model: 0.66 [")
    assert "new to it: 0.34" in lines[1]


def test_make_row_refuses_an_unknown_status():
    with pytest.raises(ValueError, match="unknown sweep status"):
        _row("a", "done")


def test_resumable_never_keeps_a_row_whose_directory_could_not_be_digested():
    prior = [dict(_ok("gone-quiet", 0.3), skill_sha256=None)]
    assert resumable(prior, {"gone-quiet": None}, SETTINGS, "1.0") == []


def test_ranking_orders_by_new_share_per_1k_tokens():
    rows = [_ok("a", 0.2, tokens=800), _ok("b", 0.6, tokens=3000), _ok("c", 0.5, tokens=500)]
    ranked = summarize(rows, resamples=50)["ranking"]
    assert [r["key"] for r in ranked] == ["c", "a", "b"]  # 1.0, 0.25, 0.2 per 1k tokens
    assert ranked[0]["new_per_1k_tokens"] == pytest.approx(1.0)


def test_ranking_breaks_rate_ties_by_key():
    rows = [_ok("b", 0.5, tokens=500), _ok("a", 0.5, tokens=500)]
    assert [row["key"] for row in summarize(rows, resamples=50)["ranking"]] == ["a", "b"]


def test_ranking_leaves_out_rows_that_are_not_measured_or_have_no_size():
    rows = [_ok("a", 0.2, tokens=800), _ok("sizeless", 0.9, tokens=None),
            _ok("zero-size", 0.9, tokens=0), _row("b", "partial"),
            _row("c", "error")]
    assert [r["key"] for r in summarize(rows, resamples=50)["ranking"]] == ["a"]


def test_summary_lines_list_the_ranking_after_the_stratum_table():
    lines = summary_lines(summarize([_ok("a", 0.2, tokens=800), _ok("b", 0.6, tokens=3000)], resamples=50))
    head = next(i for i, line in enumerate(lines) if line.startswith("ranking by new share per 1k tokens"))
    assert lines[head] == "ranking by new share per 1k tokens (top 2 of 2):"
    assert lines[head + 1].lstrip().startswith("1. a:")


def test_ranking_lists_a_skill_once_from_its_latest_ok_row():
    """Two sweeps into one JSONL measure `a` twice: the ranking has one entry for it, the later one,
    while the overall figures still sum every ok row."""
    rows = [_ok("a", 0.2, tokens=800), _ok("b", 0.6, tokens=3000), _ok("a", 0.5, tokens=800)]
    summary = summarize(rows, resamples=50)
    assert [(r["key"], r["new_lenient"]) for r in summary["ranking"]] == [("a", 0.5), ("b", 0.6)]
    assert summary["overall"]["n"] == 3


def test_ranking_keeps_an_earlier_ok_row_past_a_later_error():
    rows = [_ok("a", 0.5, tokens=800), _row("a", "error")]
    assert [(r["key"], r["new_lenient"]) for r in summarize(rows, resamples=50)["ranking"]] == [("a", 0.5)]


@pytest.mark.parametrize("bad", [
    '{"key": "a", "name": "a", "status": "ok", "usd": "x"}\n',
    '{"key": [], "name": "a", "status": "partial", "usd": 0.0}\n',
    '{"key": "a", "status": "ok"}\n',
    '{"key": "a", "name": "a", "status": "ok", "usd": 0.1, "new_lenient": "high"}\n',
    '{"key": "a", "name": "a", "status": "ok", "usd": 0}\n',  # the summary reads new_lenient of an ok row
    '[1]\n',
    '{"key": "a", "name": "a", "status": "ok", "usd": null, "new_lenient": 0.5, "tokens": 800}\n',
    '{"key": "a", "name": "a", "status": "ok", "usd": 0.1, "new_lenient": 0.5, "tokens": true}\n',
    '{"key": "a", "name": "a", "status": "ok", "usd": 0.1, "new_lenient": 0.5, "tokens": -800}\n',
    '{"key": "a", "name": "a", "status": "ok", "usd": 0.1, "new_lenient": 0.5, "tokens": ' + '1' + '0' * 400 + '}\n',
    '{"key": "a", "name": "a", "status": "ok", "usd": true, "new_lenient": 0.5, "tokens": 800}\n',
    '{"key": "a", "name": "a", "status": "ok", "usd": NaN, "new_lenient": 0.5, "tokens": 800}\n',
    '{"key": "a", "name": "a", "status": "ok", "usd": 0.1, "new_lenient": NaN, "tokens": 800}\n',
    '{"key": "a", "name": "a", "status": "ok", "usd": 0.1, "new_lenient": Infinity, "tokens": 800}\n',
    '{"key": "a", "name": "a", "status": "ok", "usd": 0.1, "new_lenient": 0.5, "tokens": Infinity}\n',
    '{"key": "a", "name": "a", "status": "ok", "usd": 0.1, "new_lenient": 0.5, "tokens": "800"}\n',
    '{"key": "a", "status": "ok", "usd": 0.1, "new_lenient": 0.5, "tokens": 800}\n',
])
def test_a_row_of_the_wrong_shape_is_a_line_numbered_input_error(tmp_path, bad):
    path = tmp_path / "s.jsonl"
    path.write_text(json.dumps(_ok("a", 0.3)) + "\n" + bad, encoding="utf-8")
    with pytest.raises(InputError, match="line 2 is not a sweep row"):
        read_rows(path)
