# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`runs show` (#278): one artifact's fields, schema check, contents and neighbours; a failed
schema check exits 0, an address that is not an artifact under the root exits 2."""

from __future__ import annotations

import dataclasses
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from dokimi import cli
from dokimi.artifacts import SCHEMA_DIR, SCHEMA_VERSIONS, save_artifact
from dokimi.runs import kinds
from dokimi.runs.show import MAX_SIBLINGS, Shown, show

#: What ``contents`` counts in each kind's fixture (the length of its top-level lists).
CONTENTS = {
    "answers.v1": {"answers": 1},
    "arms.v1": {"arms": 4, "answers": 0},
    "eval.v1": {"evaluations": 1},
    "explore_runs.v1": {"tasks": 1, "arms": 2, "results": 2},
    "guidance_reach.v1": {"files": 5, "scenarios": 5, "records": 25},
    "matrix_rollup.v1": {"cells": 2},
    "novelty.v1": {"conflicts": 0, "warnings": 2},
    "plan.v1": {"cells": 6},
    "plugin_delta.v1": {"arms": 2},
    "questions.v2": {"questions": 3},
    "repo_budget.v1": {"entries": 9},
    "repo_environment.v1": {"instructions": 4, "findings": 13},
    "repo_readiness.v1": {"files": 2, "claims": 52},
    "result.v1": {"cells": 6},
    "subject_scorecard.v2": {"cells": 0},
    "cli_routes.v1": {"roots": 2, "leaves": 2, "tombstones": 1},
    "redteam.v1": {"violations": 2, "questions": 2},
    "task_inspect.v1": {"items": 4},
    "task_runs.v1": {"tasks": 6, "harnesses": 2, "results": 24},
    "tool_preference.v1": {"items": 16, "results": 16},
}

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "artifacts"


def _fixture(version: str) -> dict:
    return json.loads((FIXTURES / f"{version}.json").read_text(encoding="utf-8"))


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DOKIMI_RUNS_DIR", raising=False)
    root = tmp_path / "results"
    save_artifact("arms", {**_fixture("arms.v1"), "generated_at": "2026-10-29T14:02:00Z"},
                  root / "arms" / "onetbb.json")
    save_artifact("plugin_delta", _fixture("plugin_delta.v1"), root / "arms" / "plugin_delta.json")
    (root / "arms" / "onetbb.md").write_text("# arms", encoding="utf-8")
    (root / "arms" / "onetbb-matrix").mkdir()
    return root


def _show(monkeypatch, capsys, *argv: str):
    monkeypatch.setattr("sys.argv", ["dokimi", "runs", "show", *argv])
    cli.main()
    return capsys.readouterr()


def _fails(monkeypatch, capsys, *argv: str) -> str:
    with pytest.raises(SystemExit) as exc:
        _show(monkeypatch, capsys, *argv)
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert err.startswith("Error: ") and "Traceback" not in err
    return err


@pytest.mark.parametrize("kind", sorted(kinds.FIELDS))
def test_every_count_is_a_top_level_list_of_the_kinds_schema(kind):
    schema = json.loads((SCHEMA_DIR / f"{SCHEMA_VERSIONS[kind]}.json").read_text("utf-8"))
    for key in kinds.FIELDS[kind].counts:
        declared = schema["properties"][key].get("type")
        assert declared == "array" or "array" in (declared or []), (kind, key)


def test_a_valid_artifact_shows_its_fields_contents_and_neighbours(monkeypatch, capsys, root):
    out = _show(monkeypatch, capsys, "arms/onetbb.json").out

    size = (root / "arms" / "onetbb.json").stat().st_size
    assert out.splitlines() == [
        "path:       arms/onetbb.json",
        "kind:       arms (arms.v1)",
        "schema:     valid",
        "subject:    onetbb",
        "model:      m",
        "time:       2026-10-29T14:02:00Z",
        "run_id:     - (no artifact records one yet)",
        f"size:       {size:,} bytes",
        "contents:   arms: 4, answers: 0",
        "next to it: onetbb-matrix/, onetbb.md, plugin_delta.json (plugin_delta.v1)",
    ]


def test_the_path_may_be_absolute_or_from_the_working_directory(monkeypatch, capsys, root):
    first = _show(monkeypatch, capsys, "arms/onetbb.json").out
    assert _show(monkeypatch, capsys, str(root / "arms" / "onetbb.json")).out == first
    assert _show(monkeypatch, capsys, "results/arms/onetbb.json").out == first


def test_an_artifact_that_fails_its_schema_lists_the_errors_and_exits_0(monkeypatch, capsys, root):
    data = json.loads((root / "arms" / "onetbb.json").read_text(encoding="utf-8"))
    data["arms"], data["total_questions"] = "baseline", "six"
    (root / "arms" / "bad.json").write_text(json.dumps(data, indent=2), encoding="utf-8")

    lines = _show(monkeypatch, capsys, "arms/bad.json").out.splitlines()  # no SystemExit: 0

    assert lines[2:5] == [
        "schema:     invalid, 2 errors",
        "  - arms: 'baseline' is not of type 'array'",
        "  - total_questions: 'six' is not of type 'integer'",
    ]
    assert lines[5] == "subject:    onetbb"


def test_a_time_the_artifact_does_not_record_is_shown_as_the_files_mtime(monkeypatch, capsys, root):
    stamp = 1_780_000_000
    os.utime(root / "arms" / "plugin_delta.json", (stamp, stamp))
    out = _show(monkeypatch, capsys, "arms/plugin_delta.json").out

    (time_line,) = [line for line in out.splitlines() if line.startswith("time:")]
    when = datetime.fromtimestamp(stamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
    assert time_line == f"time:       - ({when} UTC, the file's mtime)"


@pytest.mark.parametrize("address, message", [
    ("arms/nope.json", "no file arms/nope.json under the runs root results/"),
    ("arms", "arms is a directory; runs show takes one artifact file"),
    ("arms/onetbb.md", "arms/onetbb.md is not an artifact: it does not open with a schema_version"),
    ("../outside.json", "../outside.json is not under the runs root results/"),
    ("pairwise.json", "pairwise.json is not an artifact of a registered kind: "
                      "schema_version pairwise.v0"),
    ("broken.json", "broken.json: not valid JSON: Expecting value at line 1"),
])
def test_an_address_that_is_not_an_artifact_under_the_root_exits_2(monkeypatch, capsys, root,
                                                                   address, message):
    (root.parent / "outside.json").write_text(json.dumps({"schema_version": "eval.v1"}),
                                              encoding="utf-8")
    (root / "pairwise.json").write_text(json.dumps({"schema_version": "pairwise.v0"}),
                                        encoding="utf-8")
    (root / "broken.json").write_text('{"schema_version": "eval.v1", "x": [', encoding="utf-8")

    assert _fails(monkeypatch, capsys, address) == f"Error: {message}\n"


def test_an_absolute_path_outside_the_root_exits_2(monkeypatch, capsys, root, tmp_path):
    other = tmp_path / "eval.json"
    save_artifact("eval", _fixture("eval.v1"), other)

    assert "is not under the runs root" in _fails(monkeypatch, capsys, str(other))


def test_show_without_a_root_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DOKIMI_RUNS_DIR", raising=False)

    err = _fails(monkeypatch, capsys, "x.json")

    assert err.startswith("Error: no runs root: results/ does not exist")


def test_json_has_the_list_fields_the_schema_check_and_the_neighbours(monkeypatch, capsys, root):
    data = json.loads(_show(monkeypatch, capsys, "arms/onetbb.json", "--json").out)

    assert data == {
        "schema_version": "arms.v1", "kind": "arms", "run_id": None, "subject": "onetbb",
        "model": "m", "generated_at": "2026-10-29T14:02:00Z", "path": "arms/onetbb.json",
        "size_bytes": (root / "arms" / "onetbb.json").stat().st_size, "root": "results",
        "schema": "arms.v1", "schema_valid": True, "schema_errors": [],
        "contents": {"arms": 4, "answers": 0},
        "siblings": [{"name": "onetbb-matrix/", "schema_version": None},
                     {"name": "onetbb.md", "schema_version": None},
                     {"name": "plugin_delta.json", "schema_version": "plugin_delta.v1"}],
        "siblings_total": 3, "siblings_error": None,
    }


def test_json_of_an_invalid_artifact_carries_its_errors(monkeypatch, capsys, root):
    (root / "arms" / "bad.json").write_text(json.dumps({"schema_version": "arms.v1"}),
                                            encoding="utf-8")

    data = json.loads(_show(monkeypatch, capsys, "arms/bad.json", "--json").out)

    assert data["schema_valid"] is False
    assert sorted(data["schema_errors"]) == ["<root>: 'answers' is a required property",
                                             "<root>: 'arms' is a required property"]


def test_a_crowded_directory_names_the_first_neighbours_and_counts_the_rest(monkeypatch, capsys,
                                                                            root):
    for i in range(MAX_SIBLINGS + 5):
        (root / "arms" / f"z{i:02}.log").write_text("", encoding="utf-8")

    data = json.loads(_show(monkeypatch, capsys, "arms/onetbb.json", "--json").out)
    text = _show(monkeypatch, capsys, "arms/onetbb.json").out

    assert len(data["siblings"]) == MAX_SIBLINGS and data["siblings_total"] == MAX_SIBLINGS + 8
    assert text.rstrip().endswith(", and 8 more")


def test_a_superseded_version_is_checked_against_its_own_schema(monkeypatch, capsys, root):
    old = {"schema_version": "questions.v1",
           **{k: v for k, v in _fixture("questions.v1").items() if k != "schema_version"}}
    (root / "q.json").write_text(json.dumps(old, indent=2), encoding="utf-8")

    data = json.loads(_show(monkeypatch, capsys, "q.json", "--json").out)

    assert (data["schema"], data["schema_valid"], data["kind"]) == ("questions.v1", True,
                                                                    "questions")


def test_the_count_is_kept_for_every_list_of_every_kind(tmp_path):
    assert set(CONTENTS) == set(SCHEMA_VERSIONS.values())
    for version, expected in CONTENTS.items():
        save_artifact(kinds.VERSION_KINDS[version], _fixture(version), tmp_path / f"{version}.json")
        assert show(tmp_path, f"{version}.json").contents == expected, version


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFOs are POSIX")
def test_a_fifo_is_named_as_a_neighbour_and_refused_as_an_address(monkeypatch, capsys, root):
    os.mkfifo(root / "arms" / "pipe.json")  # opening it would block

    data = json.loads(_show(monkeypatch, capsys, "arms/onetbb.json", "--json").out)
    assert {"name": "pipe.json", "schema_version": None} in data["siblings"]
    assert _fails(monkeypatch, capsys, "arms/pipe.json") == "Error: arms/pipe.json is not a regular file\n"


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_a_neighbour_that_cannot_be_stated_is_named_not_a_crash(monkeypatch, capsys, root):
    (root / "arms" / "loop").symlink_to(root / "arms" / "loop")

    data = json.loads(_show(monkeypatch, capsys, "arms/onetbb.json", "--json").out)

    assert {"name": "loop", "schema_version": None} in data["siblings"]


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0,
                    reason="POSIX permissions; root lists every directory")
def test_a_directory_that_cannot_be_listed_still_shows_its_artifact(monkeypatch, capsys, root):
    (root / "arms").chmod(0o311)  # files can be opened by name, the directory cannot be listed
    try:
        data = json.loads(_show(monkeypatch, capsys, "arms/onetbb.json", "--json").out)
        text = _show(monkeypatch, capsys, "arms/onetbb.json").out
    finally:
        (root / "arms").chmod(0o755)

    assert (data["siblings"], data["siblings_error"]) == ([], "cannot list: Permission denied")
    assert text.splitlines()[-1] == "next to it: cannot list: Permission denied"


def test_control_characters_in_fields_are_escaped(monkeypatch, capsys, root):
    save_artifact("arms", {**_fixture("arms.v1"), "library_name": "evil\x1b[2J\nnext"},
                  root / "evil.json")

    lines = _show(monkeypatch, capsys, "evil.json").out.splitlines()

    assert "subject:    evil\\x1b[2J\\nnext" in lines and len(lines) == 10


def test_an_unknown_version_is_named_escaped_in_the_error(monkeypatch, capsys, root):
    (root / "x.json").write_text(json.dumps({"schema_version": "v\u2028forged"}, ensure_ascii=False),
                                 encoding="utf-8")

    err = _fails(monkeypatch, capsys, "x.json")

    assert err == ("Error: x.json is not an artifact of a registered kind: "
                   "schema_version v\\u2028forged\n")


def test_an_mtime_past_the_year_9999_is_shown_as_seconds(monkeypatch, capsys, root):
    from dokimi.commands import runs as runs_cmd

    real = show(root, "arms/plugin_delta.json")
    far = Shown(artifact=dataclasses.replace(real.artifact, mtime=253402300800.0),
                schema=real.schema, errors=[], contents=real.contents)
    monkeypatch.setattr(runs_cmd, "show", lambda root, address: far)

    out = _show(monkeypatch, capsys, "arms/plugin_delta.json").out

    assert "time:       - (@253402300800 UTC, the file's mtime)" in out.splitlines()


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0,
                    reason="POSIX permissions; root searches every directory")
def test_an_address_in_a_directory_that_cannot_be_searched_exits_2(monkeypatch, capsys, root):
    (root / "arms").chmod(0o600)
    try:
        err = _fails(monkeypatch, capsys, "arms/onetbb.json")
    finally:
        (root / "arms").chmod(0o755)

    # Python 3.11-3.13 raise PermissionError on the lookup; 3.14 reports the file as missing.
    assert err in ("Error: arms/onetbb.json: cannot read: Permission denied\n",
                   "Error: no file arms/onetbb.json under the runs root results/\n")


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
@pytest.mark.parametrize("address", ["linked/eval.json", "external.json"])
def test_a_symlink_that_leads_outside_the_root_is_not_read(monkeypatch, capsys, root, address):
    save_artifact("eval", _fixture("eval.v1"), root.parent / "ext" / "eval.json")
    (root / "linked").symlink_to(root.parent / "ext", target_is_directory=True)
    (root / "external.json").symlink_to(root.parent / "ext" / "eval.json")

    err = _fails(monkeypatch, capsys, address)

    assert err == f"Error: {address} leads outside the runs root results/\n"


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_a_symlink_inside_the_root_is_shown_by_its_own_name(monkeypatch, capsys, root):
    (root / "latest.json").symlink_to(root / "arms" / "onetbb.json")

    data = json.loads(_show(monkeypatch, capsys, "latest.json", "--json").out)

    assert (data["path"], data["kind"]) == ("latest.json", "arms")


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_the_real_path_of_a_symlinked_root_is_inside_it(monkeypatch, capsys, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DOKIMI_RUNS_DIR", raising=False)
    save_artifact("eval", _fixture("eval.v1"), tmp_path / "store" / "e.json")
    (tmp_path / "results").symlink_to(tmp_path / "store", target_is_directory=True)

    data = json.loads(_show(monkeypatch, capsys, str(tmp_path / "store" / "e.json"), "--json").out)

    assert data["path"] == "e.json"


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_a_neighbour_that_links_outside_the_root_is_named_not_read(monkeypatch, capsys, root):
    save_artifact("eval", _fixture("eval.v1"), root.parent / "ext" / "eval.json")
    (root / "arms" / "ext.json").symlink_to(root.parent / "ext" / "eval.json")
    (root / "arms" / "in.json").symlink_to(root / "arms" / "plugin_delta.json")

    data = json.loads(_show(monkeypatch, capsys, "arms/onetbb.json", "--json").out)

    assert {"name": "ext.json", "schema_version": None} in data["siblings"]
    assert {"name": "in.json", "schema_version": "plugin_delta.v1"} in data["siblings"]


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0,
                    reason="POSIX permissions; root reads every file")
def test_an_artifact_that_cannot_be_opened_exits_2(monkeypatch, capsys, root):
    (root / "arms" / "onetbb.json").chmod(0o000)
    try:
        err = _fails(monkeypatch, capsys, "arms/onetbb.json")
    finally:
        (root / "arms" / "onetbb.json").chmod(0o644)

    assert err == "Error: arms/onetbb.json: cannot read: Permission denied\n"


@pytest.mark.skipif(os.name == "nt", reason="Windows paths cannot hold control characters")
def test_a_rejected_address_is_named_escaped(monkeypatch, capsys, root):
    err = _fails(monkeypatch, capsys, "missing\nforged\x1b[2J.json")

    assert err == "Error: no file missing\\nforged\\x1b[2J.json under the runs root results/\n"


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_dot_dot_after_a_symlink_is_the_file_the_filesystem_opens(monkeypatch, capsys, root):
    save_artifact("eval", _fixture("eval.v1"), root / "nested" / "eval.json")
    save_artifact("plugin_delta", _fixture("plugin_delta.v1"), root / "eval.json")
    (root / "nested" / "deep").mkdir()
    (root / "link").symlink_to(root / "nested" / "deep", target_is_directory=True)

    data = json.loads(_show(monkeypatch, capsys, "link/../eval.json", "--json").out)

    assert (data["kind"], data["path"]) == ("eval", "nested/eval.json")


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_neighbours_are_never_listed_from_outside_the_root(monkeypatch, capsys, root):
    save_artifact("eval", _fixture("eval.v1"), root.parent / "ext" / "secret.json")
    (root / "linked").symlink_to(root.parent / "ext", target_is_directory=True)
    (root.parent / "ext" / "return.json").symlink_to(root / "arms" / "onetbb.json")

    data = json.loads(_show(monkeypatch, capsys, "linked/return.json", "--json").out)

    assert data["kind"] == "arms"
    assert [s["name"] for s in data["siblings"]] == ["onetbb-matrix/", "onetbb.md",
                                                     "plugin_delta.json"]


def test_an_absolute_address_under_a_root_named_with_dot_dot(tmp_path, monkeypatch, capsys):
    save_artifact("eval", _fixture("eval.v1"), tmp_path / "results" / "e.json")
    (tmp_path / "work").mkdir()
    monkeypatch.chdir(tmp_path / "work")
    monkeypatch.setenv("DOKIMI_RUNS_DIR", os.path.join("..", "results"))

    by_path = json.loads(_show(monkeypatch, capsys, str(tmp_path / "results" / "e.json"),
                               "--json").out)
    by_name = json.loads(_show(monkeypatch, capsys, "e.json", "--json").out)

    assert (by_path["path"], by_path["kind"]) == (by_name["path"], by_name["kind"]) == ("e.json",
                                                                                       "eval")
