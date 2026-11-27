# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The harness of the CLI fault contract (tests/cli_faults.py and its helpers) checked on its own."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from tests import cli_faults
from tests import test_cli_fault_contract as contract

CASES = {case.id: case for case in contract.CASES}


def _case(case_id: str) -> cli_faults.Case:
    return CASES[case_id]


def test_a_malformed_allowlist_row_is_named_with_its_line(tmp_path):
    path = tmp_path / "rows.txt"
    path.write_text("# a comment\nrepo scan | source | missing | fails\nrepo scan | source | empty\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"rows\.txt:3: expected `command \| parameter \| fault \| reason`"):
        contract.load_allowlist(path)


def test_an_indented_comment_and_a_blank_line_are_not_rows(tmp_path):
    path = tmp_path / "rows.txt"
    path.write_text("   # indented comment\n\n\t\nrepo scan | source | missing | fails\n", encoding="utf-8")
    assert contract.load_allowlist(path) == {"repo scan | source | missing": "fails"}


def test_the_allowlist_file_is_parsed_once(tmp_path):
    path = tmp_path / "rows.txt"
    path.write_text("repo scan | source | missing | fails\n", encoding="utf-8")
    first = contract.load_allowlist(path)
    path.write_text("", encoding="utf-8")  # a second read would see no rows
    assert contract.load_allowlist(path) == first


def test_git_is_on_the_path_on_windows_too(tmp_path, monkeypatch):
    monkeypatch.setattr(contract.shutil, "which", lambda name: "C:/Git/cmd/git.exe")
    assert contract._git_only_path(tmp_path / "bin", windows=True) == str(Path("C:/Git/cmd"))
    assert not (tmp_path / "bin").exists()


def test_git_is_the_only_thing_on_the_path_elsewhere(tmp_path, monkeypatch):
    monkeypatch.setattr(contract.shutil, "which", lambda name: "/opt/tools/git")
    path = contract._git_only_path(tmp_path / "bin", windows=False)
    assert path == str(tmp_path / "bin")
    assert os.readlink(tmp_path / "bin" / "git") == "/opt/tools/git"


@pytest.mark.skipif(os.name == "nt", reason="modes are not enforced on Windows")
def test_unlock_gives_files_and_directories_their_usual_modes(tmp_path):
    (tmp_path / "locked.json").write_text("{}", encoding="utf-8")
    (tmp_path / "readonly").mkdir()
    (tmp_path / "locked.json").chmod(0)
    (tmp_path / "readonly").chmod(0o555)
    cli_faults.unlock(tmp_path)
    assert stat.S_IMODE((tmp_path / "locked.json").stat().st_mode) == 0o644
    assert stat.S_IMODE((tmp_path / "readonly").stat().st_mode) == 0o755


def test_a_converter_that_makes_a_path_of_its_text_is_still_a_path_parameter():
    keys = {(c.param.command, c.param.key) for c in contract.CASES}
    assert ("skill routing", "artifact") in keys
    assert ("skill routing", "--competitors") in keys
    assert ("skill routing", "--reps") not in keys  # a scalar converter is not


def test_an_overwrite_a_removal_and_a_new_file_all_count_as_changes(tmp_path):
    (tmp_path / "kept.json").write_text("one", encoding="utf-8")
    (tmp_path / "gone.json").write_text("two", encoding="utf-8")
    (tmp_path / "link").symlink_to("kept.json")
    before = cli_faults.tree(tmp_path)
    (tmp_path / "kept.json").write_text("one", encoding="utf-8")  # the same bytes, written again
    os.utime(tmp_path / "kept.json", ns=(1, 1))
    (tmp_path / "gone.json").unlink()
    (tmp_path / "link").unlink()
    (tmp_path / "link").symlink_to("elsewhere")
    (tmp_path / "new.json").write_text("{}", encoding="utf-8")
    assert cli_faults.changes(before, cli_faults.tree(tmp_path)) == [
        "new.json", "removed gone.json", "changed kept.json", "changed link"]
    assert cli_faults.changes(before, before) == []


def test_a_string_exit_is_printed_as_the_interpreter_would(tmp_path, monkeypatch, capsys, fake_llm):
    case = _case("catalog check | --tasks-subdir | missing")
    messages = iter(["first refusal", "second refusal"])

    def main():
        raise SystemExit(next(messages))

    monkeypatch.setattr(contract.cli, "main", main)
    first = contract.run_case(case, tmp_path / "a", monkeypatch, capsys, fake_llm)
    second = contract.run_case(case, tmp_path / "b", monkeypatch, capsys, fake_llm)
    assert (first.code, first.err.strip()) == (1, "first refusal")
    assert contract._answer(first, tmp_path / "a") != contract._answer(second, tmp_path / "b")


def test_cut_short_json_that_reaches_a_model_call_is_a_problem():
    case = _case("repo hits judge | --labels | truncated-json")
    found = contract.problems(case, contract.Result(2, "", [], 1))
    assert found and "cut short" in found[0]
    assert contract.problems(case, contract.Result(2, "", [], 0)) == []
    shape = _case("repo hits judge | --labels | json-list")
    assert contract.problems(shape, contract.Result(0, "", [], 1)) == []


def test_a_root_relative_path_is_built_under_the_root_the_command_gets(tmp_path):
    param = _case("catalog check | --tasks-subdir | missing").param
    assert param.under == cli_faults.ROOT_DIR
    root, outside = tmp_path / "root", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    operand = {}
    for fault in ("not-a-directory", "dangling-link", "link-out-of-root", "dotdot", "unreadable"):
        case = _case(f"catalog check | --tasks-subdir | {fault}")
        try:
            operand[fault] = root / cli_faults.ROOT_DIR / cli_faults.build(case, root, outside)
        except cli_faults.Unbuildable:
            continue
    assert operand["not-a-directory"].is_file()
    assert operand["dangling-link"].is_symlink() and not operand["dangling-link"].exists()
    assert operand["link-out-of-root"].resolve() == (outside / "dir").resolve()
    assert operand["dotdot"].resolve() == (outside / "dir").resolve()
    if "unreadable" in operand:
        assert stat.S_IMODE(operand["unreadable"].stat().st_mode) == 0
        cli_faults.unlock(root)


def test_an_escape_is_valid_input_where_a_root_confines_it_and_malformed_elsewhere(tmp_path):
    root, outside = tmp_path / "root", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    confined = cli_faults.build(_case("repo scan | --manifest | absolute"), root, outside)
    assert json.loads(Path(confined).read_text(encoding="utf-8")) == {}
    other = cli_faults.build(_case("baseline save | --from-eval | absolute"), root, outside)
    with pytest.raises(ValueError):
        json.loads(Path(other).read_text(encoding="utf-8"))
    directory = cli_faults.build(_case("catalog check | --tasks-subdir | absolute"), root, outside)
    assert (Path(directory) / "demo-skill" / "SKILL.md").is_file()


def test_a_skill_file_fault_is_a_file_called_skill_md(tmp_path):
    root, outside = tmp_path / "root", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    for fault in ("invalid-utf8", "empty", "json-list", "truncated-json", "dangling-link"):
        value = cli_faults.build(_case(f"skill novelty analyze | --artifact | {fault}"), root, outside)
        assert Path(value).name == "SKILL.md", fault
    bad = root / cli_faults.build(_case("skill novelty analyze | --artifact | invalid-utf8"), root, outside)
    with pytest.raises(UnicodeDecodeError):
        bad.read_text(encoding="utf-8")
    good = cli_faults.build(cli_faults.Case(_case("skill novelty analyze | --artifact | empty").param, cli_faults.GOOD),
                            root, outside)
    assert (root / good / "SKILL.md").is_file()


def test_cut_short_json_that_gets_a_model_call_is_reached_even_if_answered_like_the_control(tmp_path):
    case = _case("answers generate | --questions | truncated-json")
    same = contract.Result(0, "", [], 1)
    assert contract.reached(case, same, tmp_path, (0, ""))
    assert not contract.reached(_case("answers generate | --questions | json-list"), same, tmp_path, (0, ""))
