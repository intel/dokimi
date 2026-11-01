# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The runs root scan (#278): an artifact's kind from its first bytes, its fields, and the count
of every file skipped."""

from __future__ import annotations

import importlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from dokimi.artifacts import SCHEMA_VERSIONS, SUPERSEDED_VERSIONS, save_artifact
from dokimi.runs import kinds, runs_root, scan
from dokimi.runs.scan import HEAD_BYTES, RunsRoot
from dokimi.utils import InputError

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "artifacts"
# The module, not the scan() that dokimi.runs re-exports under the same name.
scan_module = importlib.import_module("dokimi.runs.scan")

#: version -> (keys added to its fixture, expected subject, model, time).
EXPECTED: dict[str, tuple[dict, str | None, str | None, str | None]] = {
    "questions.v2": ({"model": "gen-m", "generated_at": "2026-10-01T00:00:00Z"},
                     "demo", "gen-m", "2026-10-01T00:00:00Z"),
    "answers.v1": ({}, "demo", "demo-model", "2026-01-01T00:00:00Z"),
    "eval.v1": ({}, None, "demo-judge", "2026-01-01T00:00:00Z"),
    "arms.v1": ({"generated_at": "2026-10-02T00:00:00Z"}, "onetbb", "m", "2026-10-02T00:00:00Z"),
    "matrix_rollup.v1": ({}, "oneTBB", None, "2026-10-04T17:47:16.294238Z"),
    "plugin_delta.v1": ({}, "oneTBB", "m", None),
    "subject_scorecard.v2": ({}, "docs-subject", None, "2026-10-04T17:47:19.175356Z"),
    "novelty.v1": ({}, "skill:/tmp/run/accel-tuning/SKILL.md", "answerer-m",
                   "2026-10-04T17:47:16.841483Z"),
    "task_runs.v1": ({}, None, "haiku", "2026-01-01T00:00:00Z"),
    "explore_runs.v1": ({"model": "explorer-m"}, None, "explorer-m", "2026-10-04T17:47:15.816155Z"),
    "guidance_reach.v1": ({}, "toy", "m", "2026-10-06T04:30:15.383519+00:00"),
    "repo_readiness.v1": ({"generated_at": "2026-10-03T00:00:00Z"}, "toy", None,
                          "2026-10-03T00:00:00Z"),
    "tool_preference.v1": ({}, None, "claude-haiku-5-5", "2026-10-14T00:00:00+00:00"),
    "repo_environment.v1": ({}, "toy@4ff761e2d943", None, None),
    "plan.v1": ({}, "skill:onetbb", "claude-sonnet-4-6", "2026-10-21T00:00:00Z"),
    "result.v1": ({}, None, None, "2026-10-21T01:00:00Z"),
    "repo_budget.v1": ({}, "toy", None, None),
    "task_inspect.v1": ({}, "tests/fixtures/task_formats", None, None),
    "cli_routes.v1": ({}, None, None, None),
    "need_graph.v1": ({}, "dpnp-io", None, None),
    "eval_coverage.v1": ({}, "dpnp-io", None, None),
    "repo_hits_judged.v1": ({}, "review_replay_pr3098", "gpt-5.1", None),
}


def _fixture(version: str) -> dict:
    return json.loads((FIXTURES / f"{version}.json").read_text(encoding="utf-8"))


def _save(root: Path, version: str, rel: str, **extra) -> Path:
    path = root / rel
    save_artifact(kinds.VERSION_KINDS[version], {**_fixture(version), **extra}, path)
    return path


def test_every_kind_has_a_row_in_the_fields_table_and_in_this_test():
    assert set(kinds.FIELDS) == set(SCHEMA_VERSIONS)
    assert set(EXPECTED) == set(SCHEMA_VERSIONS.values())


@pytest.mark.parametrize("version", sorted(EXPECTED))
def test_each_kind_is_listed_with_the_subject_model_and_time_it_records(tmp_path, version):
    extra, subject, model, time = EXPECTED[version]
    path = _save(tmp_path, version, f"some/dir/{version}.json", **extra)

    result = scan(tmp_path)

    assert result.unreadable == [] and not result.skipped
    (found,) = result.artifacts
    assert found.to_json() == {
        "schema_version": version, "kind": kinds.VERSION_KINDS[version], "run_id": None,
        "subject": subject, "model": model, "generated_at": time,
        "path": f"some/dir/{version}.json", "size_bytes": path.stat().st_size,
    }


@pytest.mark.parametrize("version", [v for vs in SUPERSEDED_VERSIONS.values() for v in vs])
def test_a_superseded_version_is_still_an_artifact(tmp_path, version):
    data = _fixture(version)
    payload = {"schema_version": version, **{k: v for k, v in data.items() if k != "schema_version"}}
    (tmp_path / "old.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    (found,) = scan(tmp_path).artifacts

    assert (found.schema_version, found.kind) == (version, kinds.VERSION_KINDS[version])
    assert found.subject == {"questions.v1": "demo", "subject_scorecard.v1": "s"}[version]


def test_distinct_values_of_a_list_are_all_kept_and_joined(tmp_path):
    data = _fixture("answers.v1")
    first = data["answers"][0]
    data["answers"] = [first, {**first, "question_id": "q2", "library_name": "other"},
                       {**first, "question_id": "q3"}]
    save_artifact("answers", data, tmp_path / "a.json")

    (found,) = scan(tmp_path).artifacts

    assert found.subjects == ("demo", "other")
    assert found.subject == "demo, other"


def test_every_file_that_is_not_an_artifact_is_counted_by_reason(tmp_path):
    _save(tmp_path, "eval.v1", "run/eval/p.json")
    (tmp_path / "run" / "eval" / "p.json.123.456.tmp").write_text("{", encoding="utf-8")
    (tmp_path / "run" / "eval" / "p.json.rejected").write_text(
        json.dumps({"schema_version": "eval.v1"}), encoding="utf-8")
    (tmp_path / "run" / "report.md").write_text("# report", encoding="utf-8")
    (tmp_path / "dashboard.json").write_text(json.dumps({"libraries": []}), encoding="utf-8")
    (tmp_path / "pairwise.json").write_text(json.dumps({"schema_version": "pairwise.v0"}),
                                            encoding="utf-8")
    (tmp_path / "checklist.json").write_text(json.dumps({"schema_version": "checklist.v0"}),
                                             encoding="utf-8")
    (tmp_path / "broken.json").write_text('{"schema_version": "answers.v1", "answers": [',
                                          encoding="utf-8")
    (tmp_path / "late.json").write_text(json.dumps({"evaluations": [], "schema_version": "eval.v1"}),
                                        encoding="utf-8")

    result = scan(tmp_path)

    assert [a.path.as_posix() for a in result.artifacts] == ["run/eval/p.json"]
    assert result.skipped == {"temporary": 1, "rejected": 1, "not_json": 1,
                              "no_leading_schema_version": 2, "unknown_schema_version": 2}
    assert result.unknown_versions == {"pairwise.v0": 1, "checklist.v0": 1}
    (bad,) = result.unreadable
    assert (bad.path.as_posix(), bad.schema_version) == ("broken.json", "answers.v1")
    assert bad.reason.startswith("not valid JSON: ")


def test_a_file_that_is_not_utf8_is_unreadable_with_its_reason(tmp_path):
    (tmp_path / "x.json").write_bytes(b'{"schema_version": "eval.v1", "judge_model": "\xff"}')

    (bad,) = scan(tmp_path).unreadable

    assert (bad.reason, bad.schema_version) == ("not UTF-8", "eval.v1")


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_a_symlinked_directory_is_counted_and_not_walked(tmp_path):
    _save(tmp_path, "eval.v1", "run/eval.json")
    (tmp_path / "run" / "loop").symlink_to(tmp_path, target_is_directory=True)
    (tmp_path / "self.json").symlink_to(tmp_path / "self.json")

    result = scan(tmp_path)

    assert [a.path.as_posix() for a in result.artifacts] == ["run/eval.json"]
    assert result.skipped == {"linked_directory": 1}
    (bad,) = result.unreadable
    assert bad.path.as_posix() == "self.json" and bad.reason.startswith("cannot read: ")


def test_the_kind_comes_from_the_first_bytes_and_the_rest_is_not_parsed_to_skip(tmp_path):
    # The tail is broken: a skipped file was never parsed, a file of a known kind was.
    tail = "x" * HEAD_BYTES + "{not json"
    (tmp_path / "big.json").write_text('{"libraries": "' + tail, encoding="utf-8")
    (tmp_path / "eval.json").write_text('{"schema_version": "eval.v1", "x": "' + tail,
                                        encoding="utf-8")

    result = scan(tmp_path)

    assert result.skipped == {"no_leading_schema_version": 1}
    (bad,) = result.unreadable
    assert (bad.path.as_posix(), bad.schema_version) == ("eval.json", "eval.v1")


def test_a_head_that_names_one_version_and_a_body_that_holds_another_is_unreadable(tmp_path):
    (tmp_path / "x.json").write_text('{"schema_version": "eval.v1", "schema_version": "arms.v1"}',
                                     encoding="utf-8")

    (bad,) = scan(tmp_path).unreadable

    assert "does not hold the schema_version eval.v1" in bad.reason


def test_artifacts_are_newest_first_by_their_time_then_by_mtime(tmp_path):
    _save(tmp_path, "eval.v1", "old.json", evaluated_at="2026-01-01T00:00:00Z")
    _save(tmp_path, "eval.v1", "new.json", evaluated_at="2026-10-01T00:00:00+00:00")
    _save(tmp_path, "eval.v1", "naive.json", evaluated_at="2026-06-01T00:00:00")
    no_time = _save(tmp_path, "plugin_delta.v1", "no_time.json")
    stamp = 1_780_000_000  # 2026-05-28, between old and naive
    os.utime(no_time, (stamp, stamp))

    paths = [a.path.as_posix() for a in scan(tmp_path).artifacts]

    assert paths == ["new.json", "naive.json", "no_time.json", "old.json"]


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="TZ is read by time.tzset, POSIX only")
def test_a_time_that_names_no_zone_is_utc_whatever_the_local_zone(tmp_path, monkeypatch):
    _save(tmp_path, "eval.v1", "e.json", evaluated_at="2026-06-01T09:30:00")
    monkeypatch.setenv("TZ", "Asia/Kolkata")
    time.tzset()
    try:
        (found,) = scan(tmp_path).artifacts
    finally:
        monkeypatch.undo()
        time.tzset()

    assert found.recorded_at == datetime(2026, 6, 1, 9, 30, tzinfo=timezone.utc)


def test_dokimi_runs_dir_overrides_the_default_root(tmp_path):
    assert runs_root({}) == RunsRoot(Path("results"), from_env=False)
    assert runs_root({"DOKIMI_RUNS_DIR": ""}) == RunsRoot(Path("results"), from_env=False)
    assert runs_root({"DOKIMI_RUNS_DIR": str(tmp_path)}) == RunsRoot(tmp_path, from_env=True)


def test_a_missing_default_root_is_no_runs_and_a_bad_named_root_is_an_input_error(tmp_path):
    assert RunsRoot(tmp_path / "results", from_env=False).exists() is False
    assert RunsRoot(tmp_path, from_env=True).exists() is True
    with pytest.raises(InputError, match="DOKIMI_RUNS_DIR=.* does not exist"):
        RunsRoot(tmp_path / "nope", from_env=True).exists()
    (tmp_path / "file").write_text("", encoding="utf-8")
    with pytest.raises(InputError, match="is not a directory"):
        RunsRoot(tmp_path / "file", from_env=True).exists()
    with pytest.raises(InputError, match="is not a directory"):
        RunsRoot(tmp_path / "file", from_env=False).exists()


def test_upper_case_suffixes_are_matched_too(tmp_path):
    _save(tmp_path, "eval.v1", "E.JSON")
    (tmp_path / "x.json.1.2.TMP").write_text("{", encoding="utf-8")

    result = scan(tmp_path)

    assert [a.path.as_posix() for a in result.artifacts] == ["E.JSON"]
    assert result.skipped == {"temporary": 1}


def test_a_schema_version_past_the_first_bytes_is_not_a_leading_one(tmp_path):
    assert HEAD_BYTES < 1000
    padded = "{" + " " * 1000 + '"schema_version": "eval.v1", "evaluations": []}'
    (tmp_path / "padded.json").write_text(padded, encoding="utf-8")

    assert scan(tmp_path).skipped == {"no_leading_schema_version": 1}


def test_a_document_nested_too_deep_to_parse_is_unreadable(tmp_path, monkeypatch):
    # How deep json can go depends on the interpreter (3.14 bounds it by C stack), so the
    # RecursionError is raised for the parse instead of found by nesting.
    def too_deep(path, version):
        raise RecursionError("maximum recursion depth exceeded while decoding a JSON array")

    _save(tmp_path, "eval.v1", "deep.json")
    monkeypatch.setattr(scan_module, "load_document", too_deep)

    (bad,) = scan(tmp_path).unreadable

    assert (bad.path.as_posix(), bad.schema_version) == ("deep.json", "eval.v1")
    assert bad.reason == "maximum recursion depth exceeded while decoding a JSON array"


def test_a_time_out_of_range_falls_back_to_the_mtime(tmp_path):
    early = _save(tmp_path, "eval.v1", "early.json", evaluated_at="0001-01-01T00:00:00+05:00")
    late = _save(tmp_path, "eval.v1", "late.json", evaluated_at="9999-12-31T23:00:00-05:00")
    os.utime(early, (1_780_000_000, 1_780_000_000))
    os.utime(late, (1_790_000_000, 1_790_000_000))

    found = scan(tmp_path).artifacts

    assert [(a.path.as_posix(), a.recorded_at) for a in found] == [("late.json", None),
                                                                    ("early.json", None)]


def test_unreadable_files_come_in_path_order(tmp_path):
    names = [f"{d}/{i:02}.json" for d in ("b", "a") for i in range(20, 0, -1)]
    for rel in names:
        (tmp_path / rel).parent.mkdir(exist_ok=True)
        (tmp_path / rel).write_text('{"schema_version": "eval.v1", ', encoding="utf-8")

    paths = [u.path.as_posix() for u in scan(tmp_path).unreadable]

    assert paths == sorted(names)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFOs are POSIX")
def test_a_fifo_is_skipped_without_being_opened(tmp_path):
    os.mkfifo(tmp_path / "pipe.json")  # opening it for reading would block the scan

    assert scan(tmp_path).skipped == {"not_a_file": 1}


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0,
                    reason="POSIX permissions; root reads every directory")
@pytest.mark.parametrize("mode", [0o000, 0o444], ids=["unreadable", "not-searchable"])
def test_a_directory_that_cannot_be_walked_is_unreadable_not_a_crash(tmp_path, mode):
    _save(tmp_path, "eval.v1", "ok.json")
    (tmp_path / "locked" / "sub").mkdir(parents=True)
    (tmp_path / "locked").chmod(mode)
    try:
        result = scan(tmp_path)
    finally:
        (tmp_path / "locked").chmod(0o755)

    assert [a.path.as_posix() for a in result.artifacts] == ["ok.json"]
    (bad,) = result.unreadable
    expected = "locked" if mode == 0o000 else "locked/sub"
    assert (bad.path.as_posix(), bad.reason) == (expected, "cannot list: Permission denied")


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_a_file_symlink_is_read_only_when_it_resolves_inside_the_root(tmp_path):
    root = tmp_path / "results"
    _save(root, "eval.v1", "arms/real.json")
    _save(tmp_path, "eval.v1", "outside/eval.json")
    (root / "latest.json").symlink_to(root / "arms" / "real.json")
    (root / "external.json").symlink_to(tmp_path / "outside" / "eval.json")

    result = scan(root)

    assert sorted(a.path.as_posix() for a in result.artifacts) == ["arms/real.json", "latest.json"]
    assert result.skipped == {"linked_outside": 1}


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_a_root_that_is_itself_a_symlink_is_read(tmp_path):
    _save(tmp_path, "eval.v1", "store/e.json")
    (tmp_path / "store" / "latest.json").symlink_to(tmp_path / "store" / "e.json")
    (tmp_path / "results").symlink_to(tmp_path / "store", target_is_directory=True)

    result = scan(tmp_path / "results")

    assert sorted(a.path.as_posix() for a in result.artifacts) == ["e.json", "latest.json"]
    assert not result.skipped


@pytest.mark.skipif(os.name != "nt", reason="junctions are a Windows directory link")
def test_a_junction_is_counted_and_not_walked(tmp_path):
    import _winapi

    root = tmp_path / "results"
    _save(root, "eval.v1", "in.json")
    _save(tmp_path, "eval.v1", "outside/eval.json")
    _winapi.CreateJunction(str(tmp_path / "outside"), str(root / "external"))

    result = scan(root)

    assert [a.path.as_posix() for a in result.artifacts] == ["in.json"]
    assert result.skipped == {"linked_directory": 1}
