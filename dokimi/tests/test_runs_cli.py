# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`runs list` (#278): exit codes of the root, filters, order, the --json fields and the summary."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from dokimi import cli
from dokimi.artifacts import save_artifact

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "artifacts"


def _fixture(version: str) -> dict:
    return json.loads((FIXTURES / f"{version}.json").read_text(encoding="utf-8"))


@pytest.fixture
def root(tmp_path, monkeypatch):
    """An empty default root, ``results/`` in the working directory."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DOKIMI_RUNS_DIR", raising=False)
    (tmp_path / "results").mkdir()
    return tmp_path / "results"


@pytest.fixture
def filled(root, monkeypatch):
    """Each artifact a run of its own (``r-<kind>``), so the rows are newest first."""
    def save(kind, *args):
        monkeypatch.setenv("DOKIMI_RUN_ID", f"r-{kind}")
        save_artifact(kind, *args)
        monkeypatch.delenv("DOKIMI_RUN_ID")

    save("arms", {**_fixture("arms.v1"), "generated_at": "2026-10-29T14:02:00Z"},
                  root / "arms" / "onetbb.json")
    save("plugin_delta", _fixture("plugin_delta.v1"), root / "arms" / "plugin_delta.json")
    save("eval", _fixture("eval.v1"), root / "onetbb_r1" / "eval" / "onetbb.json")
    save("questions", {**_fixture("questions.v2"), "product": "oneTBB",
                                "generated_at": "2026-10-20T09:00:00+02:00"},
                  root / "onetbb_r1" / "questions" / "oneTBB.json")
    os.utime(root / "arms" / "plugin_delta.json", (1_780_000_000, 1_780_000_000))  # 2026-05-28
    (root / "arms" / "onetbb.md").write_text("# arms", encoding="utf-8")
    (root / "pairwise.json").write_text(json.dumps({"schema_version": "pairwise.v0"}),
                                        encoding="utf-8")
    (root / "broken.json").write_text('{"schema_version": "eval.v1", "evaluations": [',
                                      encoding="utf-8")
    return root


def _list(monkeypatch, capsys, *argv: str):
    monkeypatch.setattr("sys.argv", ["dokimi", "runs", "list", *argv])
    cli.main()
    return capsys.readouterr()


def _paths(out: str) -> list[str]:
    rows = out.split("\n\n")[0].splitlines()[1:]
    return [row.split()[-1] for row in rows]


def test_a_missing_default_root_is_no_runs_and_exits_0(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DOKIMI_RUNS_DIR", raising=False)

    out = _list(monkeypatch, capsys).out
    assert out == "no runs under results/ (it does not exist; DOKIMI_RUNS_DIR names another root)\n"
    data = json.loads(_list(monkeypatch, capsys, "--json").out)
    assert (data["root"], data["artifacts"], data["total"]) == ("results", [], 0)


@pytest.mark.parametrize("make, message", [
    (lambda p: None, "does not exist"),
    (lambda p: p.write_text("", encoding="utf-8"), "is not a directory"),
], ids=["missing", "a-file"])
def test_a_bad_dokimi_runs_dir_exits_2_with_a_message(tmp_path, monkeypatch, capsys, make, message):
    named = tmp_path / "named"
    make(named)
    monkeypatch.setenv("DOKIMI_RUNS_DIR", str(named))

    with pytest.raises(SystemExit) as exc:
        _list(monkeypatch, capsys)

    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert err == f"Error: runs root DOKIMI_RUNS_DIR={named} {message}\n"


def test_dokimi_runs_dir_is_the_root_listed(tmp_path, monkeypatch, capsys, root):
    other = tmp_path / "elsewhere"
    save_artifact("eval", _fixture("eval.v1"), other / "e.json")
    monkeypatch.setenv("DOKIMI_RUNS_DIR", str(other))

    out = _list(monkeypatch, capsys).out

    assert _paths(out) == ["e.json"]
    assert f"1 artifact under {other.as_posix()}/" in out


def test_an_empty_root_lists_nothing_and_says_so(monkeypatch, capsys, root):
    assert _list(monkeypatch, capsys).out == "0 artifacts under results/\n"


def test_rows_are_newest_first_by_recorded_time_then_mtime(monkeypatch, capsys, filled):
    out = _list(monkeypatch, capsys).out

    assert _paths(out) == ["arms/onetbb.json", "onetbb_r1/questions/oneTBB.json",
                           "arms/plugin_delta.json", "onetbb_r1/eval/onetbb.json"]
    lines = out.splitlines()
    assert lines[0].split() == ["RUN", "SCHEMA", "SUBJECT", "MODEL", "TIME", "(UTC)", "PATH"]
    assert lines[2].split()[:6] == ["-", "questions.v2", "oneTBB", "-", "2026-10-20", "07:00"]
    assert lines[3].split()[:5] == ["r-plugin_delta", "plugin_delta.v1", "oneTBB", "m", "2026-05-28"]
    assert lines[3].split()[5].endswith("*")


def test_kind_is_repeatable_and_subject_matches_exactly(monkeypatch, capsys, filled):
    out = _list(monkeypatch, capsys, "--kind", "arms", "--kind", "eval").out
    assert _paths(out) == ["arms/onetbb.json", "onetbb_r1/eval/onetbb.json"]
    assert "2 of 4 artifacts under results/ match" in out

    out = _list(monkeypatch, capsys, "--subject", "oneTBB").out
    assert _paths(out) == ["onetbb_r1/questions/oneTBB.json", "arms/plugin_delta.json"]

    out = _list(monkeypatch, capsys, "--subject", "oneTBB", "--kind", "plugin_delta").out
    assert _paths(out) == ["arms/plugin_delta.json"]

    assert _list(monkeypatch, capsys, "--subject", "onet").out.startswith("0 of 4 artifacts")


def test_json_has_the_vocabulary_fields_and_every_count(monkeypatch, capsys, filled):
    data = json.loads(_list(monkeypatch, capsys, "--json", "--kind", "arms").out)

    assert data["artifacts"] == [{
        "schema_version": "arms.v1", "kind": "arms", "run_id": "r-arms", "subject": "onetbb",
        "model": "m", "generated_at": "2026-10-29T14:02:00Z", "path": "arms/onetbb.json",
        "size_bytes": (filled / "arms" / "onetbb.json").stat().st_size,
    }]
    assert data["runs"] == [{"run_id": "r-arms", "kinds": ["arms"], "paths": ["arms/onetbb.json"]}]
    assert data["root"] == "results" and data["total"] == 4
    assert data["skipped"] == {"not_json": 1, "unknown_schema_version": 1}
    assert data["unknown_versions"] == {"pairwise.v0": 1}
    (bad,) = data["unreadable"]
    assert (bad["path"], bad["schema_version"]) == ("broken.json", "eval.v1")
    assert bad["reason"].startswith("not valid JSON: ")


def test_the_text_summary_counts_what_was_skipped_and_names_the_first_unreadable(
        monkeypatch, capsys, filled):
    (filled / "late.json").write_text(json.dumps({"x": 1, "schema_version": "eval.v1"}),
                                      encoding="utf-8")
    (filled / "arms" / "onetbb.json.rejected").write_text("{}", encoding="utf-8")

    summary = _list(monkeypatch, capsys).out.split("\n\n")[1].splitlines()

    assert summary == [
        "4 artifacts under results/ (* the file's mtime: the artifact records no time)",
        "runs: 3 (3 artifacts); no run_id: 1 (runs show PATH says why)",
        "skipped: 1 JSON with no leading schema_version, 1 JSON of an unknown schema_version "
        "(pairwise.v0: 1), 1 *.rejected (payloads save_artifact refused), 1 not .json",
        "unreadable: 1 (first: broken.json: not valid JSON: Expecting value at line 1)",
    ]


def test_the_mtime_footnote_is_there_only_when_a_shown_row_has_a_star(monkeypatch, capsys, filled):
    out = _list(monkeypatch, capsys, "--kind", "arms").out

    assert out.split("\n\n")[1].splitlines()[0] == "1 of 4 artifacts under results/ match"


@pytest.mark.skipif(os.name == "nt", reason="Windows file names cannot hold control characters")
def test_control_characters_in_names_and_fields_are_escaped(monkeypatch, capsys, root):
    save_artifact("arms", {**_fixture("arms.v1"), "library_name": "evil\x1b[2J",
                           "generated_at": "2026-10-29T14:02:00Z"}, root / "two\nlines.json")

    rows = _list(monkeypatch, capsys).out.split("\n\n")[0].splitlines()

    assert len(rows) == 2
    assert rows[1].split()[2] == "evil\\x1b[2J" and rows[1].endswith("two\\nlines.json")


def test_an_unknown_version_with_a_line_separator_is_printed_escaped(monkeypatch, capsys, root):
    (root / "x.json").write_text(json.dumps({"schema_version": "v\u2028forged"}, ensure_ascii=False),
                                 encoding="utf-8")

    out = _list(monkeypatch, capsys).out

    assert "\u2028" not in out
    assert "JSON of an unknown schema_version (v\\u2028forged: 1)" in out


def test_an_mtime_past_the_year_9999_is_shown_as_seconds(monkeypatch, capsys, root):
    from dokimi.commands import runs as runs_cmd
    from dokimi.runs.scan import Artifact, ScanResult

    far = Artifact(path=Path("p.json"), schema_version="plugin_delta.v1", run_id=None, subjects=(), models=(),
                   generated_at=None, size_bytes=1, mtime=253402300800.0)  # tmpfs keeps such mtimes
    monkeypatch.setattr(runs_cmd, "scan", lambda path: ScanResult(root=path, artifacts=[far]))

    rows = _list(monkeypatch, capsys).out.splitlines()

    assert rows[1].split()[:5] == ["-", "plugin_delta.v1", "-", "-", "@253402300800*"]


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0,
                    reason="POSIX permissions; root searches every directory")
def test_a_root_under_a_directory_that_cannot_be_searched_exits_2(tmp_path, monkeypatch, capsys):
    named = tmp_path / "locked" / "root"
    named.mkdir(parents=True)
    (tmp_path / "locked").chmod(0o600)
    monkeypatch.setenv("DOKIMI_RUNS_DIR", str(named))
    try:
        with pytest.raises(SystemExit) as exc:
            _list(monkeypatch, capsys)
    finally:
        (tmp_path / "locked").chmod(0o755)

    assert exc.value.code == 2
    # Python 3.11-3.13 raise PermissionError on the lookup; 3.14 reports the path as missing.
    assert capsys.readouterr().err in (
        f"Error: runs root DOKIMI_RUNS_DIR={named}: cannot read: Permission denied\n",
        f"Error: runs root DOKIMI_RUNS_DIR={named} does not exist\n")


def test_a_time_that_is_not_iso_8601_is_shown_as_written(monkeypatch, capsys, root):
    save_artifact("eval", {**_fixture("eval.v1"), "evaluated_at": "yesterday"}, root / "e.json")

    rows = _list(monkeypatch, capsys).out.splitlines()

    assert rows[1].split()[1:5] == ["eval.v1", "-", "demo-judge", "yesterday"]


@pytest.mark.skipif(os.name == "nt", reason="Windows paths cannot hold control characters")
def test_a_bad_root_is_named_escaped(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DOKIMI_RUNS_DIR", str(tmp_path / "no\nwhere"))

    with pytest.raises(SystemExit):
        _list(monkeypatch, capsys)

    err = capsys.readouterr().err
    assert err == f"Error: runs root DOKIMI_RUNS_DIR={tmp_path}/no\\nwhere does not exist\n"


@pytest.fixture
def two_runs(root, monkeypatch):
    """Run ``a`` (an eval at 10:00, arms at 08:00), run ``b`` (09:00) and a question set (09:30,
    no envelope)."""
    def save(run, kind, data, path):
        monkeypatch.setenv("DOKIMI_RUN_ID", run)
        save_artifact(kind, data, root / path)

    save("a", "arms", {**_fixture("arms.v1"), "generated_at": "2026-11-01T08:00:00Z"}, "a/arms.json")
    save("a", "eval", {**_fixture("eval.v1"), "evaluated_at": "2026-11-01T10:00:00Z"}, "a/eval.json")
    save("b", "eval", {**_fixture("eval.v1"), "evaluated_at": "2026-11-01T09:00:00Z"}, "b/eval.json")
    save("b", "questions", {**_fixture("questions.v2"), "generated_at": "2026-11-01T09:30:00Z"},
         "q.json")
    monkeypatch.delenv("DOKIMI_RUN_ID")
    return root


def test_the_artifacts_of_one_run_are_listed_together_and_the_runs_newest_first(
        monkeypatch, capsys, two_runs):
    out = _list(monkeypatch, capsys).out

    assert _paths(out) == ["a/eval.json", "a/arms.json", "q.json", "b/eval.json"]
    assert [row.split()[0] for row in out.split("\n\n")[0].splitlines()[1:]] == ["a", "a", "-", "b"]
    assert out.split("\n\n")[1].splitlines()[1] == (
        "runs: 2 (3 artifacts); no run_id: 1 (runs show PATH says why)")


def test_json_lists_the_runs_in_the_order_of_the_rows(monkeypatch, capsys, two_runs):
    data = json.loads(_list(monkeypatch, capsys, "--json").out)

    assert [a["path"] for a in data["artifacts"]] == ["a/eval.json", "a/arms.json", "q.json",
                                                      "b/eval.json"]
    assert data["runs"] == [
        {"run_id": "a", "kinds": ["arms", "eval"], "paths": ["a/eval.json", "a/arms.json"]},
        {"run_id": "b", "kinds": ["eval"], "paths": ["b/eval.json"]},
    ]


def test_a_filter_groups_only_the_rows_it_keeps(monkeypatch, capsys, two_runs):
    out = _list(monkeypatch, capsys, "--kind", "arms", "--kind", "questions").out
    assert _paths(out) == ["q.json", "a/arms.json"]  # run a now starts at 08:00
    assert out.split("\n\n")[1].splitlines()[1] == (
        "runs: 1 (1 artifact); no run_id: 1 (runs show PATH says why)")
    out = _list(monkeypatch, capsys, "--kind", "eval").out
    assert out.split("\n\n")[1].splitlines()[1] == "runs: 2 (2 artifacts)"

    data = json.loads(_list(monkeypatch, capsys, "--json", "--kind", "questions").out)
    assert data["runs"] == []
    out = _list(monkeypatch, capsys, "--kind", "questions").out
    assert out.split("\n\n")[1].splitlines()[1] == "runs: 0; no run_id: 1 (runs show PATH says why)"
