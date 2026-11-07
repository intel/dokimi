# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for ``eval leak``: exit codes, the artifact, the split lock and the proposer list.

The question sets are real files read by the question loader; no model is called.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import pytest

from dokimi.artifacts import schema_errors
from dokimi.commands.leak import cmd_eval_leak
from dokimi.utils import InputError

ANSWER = "the flag writes each kernel timing into the chrome trace file under the output directory"
SPLIT = {"dev": ["1"], "val": ["2"], "test": ["3"]}


def _questions(path: Path, answers: dict[str, str]) -> Path:
    questions = [{"id": qid, "question": f"Question {qid} about profiling?",
                  "grading": {"checks": [{"type": "match", "values": [answer], "location": "any"}]}}
                 for qid, answer in answers.items()]
    path.write_text(json.dumps({"questions": questions}), encoding="utf-8")
    return path


def _skill(root: Path, body: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "SKILL.md").write_text(f"---\nname: demo\n---\n{body}\n", encoding="utf-8")
    return root


def _split(path: Path, split: dict[str, list[str]] = SPLIT) -> Path:
    path.write_text(json.dumps(split), encoding="utf-8")
    return path


def _args(skill, questions, **overrides):
    base = {"skill": str(skill), "questions": str(questions), "allow": None, "allow_file": None,
            "min_fraction": 0.10, "split": None, "lock": None, "proposer": None, "relock": False, "out": None, "verbatim_words": 12}
    return argparse.Namespace(**{**base, **overrides})


def _exit_code(args) -> int:
    """The exit code of a run. A refused input is an InputError, which cli.main prints as ``Error: ...``
    and exits 2 for; this does the same here so the stderr check sees the line the user sees."""
    try:
        cmd_eval_leak(args)
    except SystemExit as exc:
        return exc.code
    except InputError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    raise AssertionError("the run did not exit")


def _clean_run(tmp_path):
    questions = _questions(tmp_path / "q.json", {"1": ANSWER, "2": ANSWER + " sorted",
                                                 "3": "a third answer that names the other profiling switch and its default"})
    return _skill(tmp_path / "skill", "Use the trace flag; see the docs for details."), questions


def test_a_clean_skill_passes_and_writes_a_leak_artifact(tmp_path, capsys):
    skill, questions = _clean_run(tmp_path)
    out = tmp_path / "leak.json"
    cmd_eval_leak(_args(skill, questions, out=out))  # exit 0 returns normally
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["schema_version"] == "leak.v1" and saved["status"] == "pass"
    assert saved["skill_files"] == ["SKILL.md"] and saved["split"] is None
    assert schema_errors("leak.v1", saved) == []
    assert f"Wrote leak artifact to {out}" in capsys.readouterr().out


def test_a_leak_exits_1_and_the_table_names_the_item(tmp_path, capsys):
    questions = _questions(tmp_path / "q.json", {"1": ANSWER})
    skill = _skill(tmp_path / "skill", f"Copied: {ANSWER}.")
    assert _exit_code(_args(skill, questions)) == 1
    out = capsys.readouterr().out
    assert any(line.startswith("1 ") for line in out.splitlines())  # the flagged row names the item
    assert "status blocked: 1 items, 1 checked, 1 flagged" in out


def test_an_item_that_cannot_be_checked_exits_1_as_inconclusive(tmp_path, capsys):
    questions = _questions(tmp_path / "q.json", {"1": "42"})
    assert _exit_code(_args(_skill(tmp_path / "skill", "clean"), questions)) == 1
    assert "status inconclusive" in capsys.readouterr().out


def test_an_allowlisted_path_clears_a_shared_path_that_flags_without_it(tmp_path):
    binary = "/opt/intel/oneapi/vtune/2024.1/bin64/vtune"
    command = f"{binary} -collect hotspots -result-dir r1"
    questions = _questions(tmp_path / "q.json", {"1": f"Run {command} and then read the summary table from r1 for the top functions"})
    skill = _skill(tmp_path / "skill", f"Example: {command}")
    assert _exit_code(_args(skill, questions)) == 1  # the shared path makes a run of 8 words in common
    cmd_eval_leak(_args(skill, questions, allow=[binary]))  # the path is cut from both: exit 0
    allow_file = tmp_path / "allow.txt"
    allow_file.write_text(f"# API names\n{binary}\n", encoding="utf-8")
    cmd_eval_leak(_args(skill, questions, allow_file=str(allow_file)))


def test_the_first_split_use_writes_the_lock_next_to_the_split(tmp_path, capsys):
    skill, questions = _clean_run(tmp_path)
    split = _split(tmp_path / "split.json")
    out = tmp_path / "leak.json"
    cmd_eval_leak(_args(skill, questions, split=str(split), out=out))
    lock = tmp_path / "split.lock.json"
    assert lock.exists() and json.loads(lock.read_text(encoding="utf-8"))["schema_version"] == "split_lock.v1"
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["split"]["state"] == "created" and saved["lock_path"] == str(lock)
    assert saved["split_path"] == str(split)
    assert schema_errors("leak.v1", saved) == []
    assert "split created" in capsys.readouterr().out


def test_the_same_split_on_the_next_run_matches_the_lock(tmp_path):
    skill, questions = _clean_run(tmp_path)
    split = _split(tmp_path / "split.json")
    cmd_eval_leak(_args(skill, questions, split=str(split)))
    out = tmp_path / "leak.json"
    cmd_eval_leak(_args(skill, questions, split=str(split), out=out))
    assert json.loads(out.read_text(encoding="utf-8"))["split"]["state"] == "matched"


def test_a_changed_split_exits_1_and_leaves_the_lock_as_it_was(tmp_path, capsys):
    skill, questions = _clean_run(tmp_path)
    split = _split(tmp_path / "split.json")
    cmd_eval_leak(_args(skill, questions, split=str(split)))
    lock = tmp_path / "split.lock.json"
    before = lock.read_text(encoding="utf-8")
    moved = _split(tmp_path / "moved.json", {"dev": ["1", "3"], "val": ["2"], "test": []})
    assert _exit_code(_args(skill, questions, split=str(moved), lock=str(lock))) == 1
    assert lock.read_text(encoding="utf-8") == before
    out = capsys.readouterr().out
    assert "split changed since its lock: 1 items moved: 3" in out


def test_a_test_item_on_the_proposer_list_exits_1(tmp_path, capsys):
    skill, questions = _clean_run(tmp_path)
    split = _split(tmp_path / "split.json")
    proposer = tmp_path / "proposer.json"
    proposer.write_text(json.dumps(["1", "3"]), encoding="utf-8")
    assert _exit_code(_args(skill, questions, split=str(split), proposer=str(proposer))) == 1
    assert "test items given to the proposer: 3" in capsys.readouterr().out


def test_a_proposer_that_sees_no_test_item_passes(tmp_path, capsys):
    skill, questions = _clean_run(tmp_path)
    split = _split(tmp_path / "split.json")
    proposer = tmp_path / "proposer.json"
    proposer.write_text(json.dumps(["1", "2"]), encoding="utf-8")
    cmd_eval_leak(_args(skill, questions, split=str(split), proposer=str(proposer)))  # exit 0 returns normally
    out = capsys.readouterr().out
    assert "test items given to the proposer:" not in out and out.splitlines()[-1].startswith("status pass")


def test_a_lock_without_a_split_exits_2(tmp_path, capsys):
    skill, questions = _clean_run(tmp_path)
    assert _exit_code(_args(skill, questions, lock=str(tmp_path / "x.lock.json"))) == 2
    assert capsys.readouterr().err.startswith("Error: --lock locks a split")


def test_a_proposer_list_without_a_split_exits_2(tmp_path, capsys):
    skill, questions = _clean_run(tmp_path)
    proposer = tmp_path / "proposer.json"
    proposer.write_text(json.dumps(["1"]), encoding="utf-8")
    assert _exit_code(_args(skill, questions, proposer=str(proposer))) == 2
    assert capsys.readouterr().err.startswith("Error: a proposer list needs a split")


def test_an_unknown_id_on_the_proposer_list_exits_2(tmp_path, capsys):
    skill, questions = _clean_run(tmp_path)
    split = _split(tmp_path / "split.json")
    proposer = tmp_path / "proposer.json"
    proposer.write_text(json.dumps(["9"]), encoding="utf-8")
    assert _exit_code(_args(skill, questions, split=str(split), proposer=str(proposer))) == 2
    assert "not in the set: 9" in capsys.readouterr().err


def test_a_corrupt_lock_exits_2_and_is_not_rewritten(tmp_path, capsys):
    skill, questions = _clean_run(tmp_path)
    split = _split(tmp_path / "split.json")
    lock = tmp_path / "split.lock.json"
    lock.write_text("{not json", encoding="utf-8")
    assert _exit_code(_args(skill, questions, split=str(split))) == 2
    assert lock.read_text(encoding="utf-8") == "{not json"
    assert capsys.readouterr().err.startswith("Error:")


def test_a_malformed_split_exits_2(tmp_path, capsys):
    skill, questions = _clean_run(tmp_path)
    split = _split(tmp_path / "split.json", {"train": ["1"]})
    assert _exit_code(_args(skill, questions, split=str(split))) == 2
    assert "keys" in capsys.readouterr().err


def test_an_unreadable_skill_exits_2(tmp_path, capsys):
    questions = _questions(tmp_path / "q.json", {"1": ANSWER})
    assert _exit_code(_args(tmp_path / "missing", questions)) == 2
    assert capsys.readouterr().err.startswith("Error:")


def test_an_unreadable_question_set_exits_2(tmp_path, capsys):
    skill, _ = _clean_run(tmp_path)
    assert _exit_code(_args(skill, tmp_path / "missing.json")) == 2
    assert capsys.readouterr().err.startswith("Error:")


def test_an_unwritable_out_exits_2_after_the_table(tmp_path, capsys):
    skill, questions = _clean_run(tmp_path)
    blocker = tmp_path / "a_file"
    blocker.write_text("not a directory", encoding="utf-8")
    assert _exit_code(_args(skill, questions, out=blocker / "out.json")) == 2  # not 1: a bad --out is not a blocked run
    assert capsys.readouterr().err.startswith("Error:")


def test_a_min_fraction_is_read_from_the_command_line():
    import dokimi.cli as cli

    parser = cli.build_parser()
    ns = parser.parse_args(["eval", "leak", "skill", "q.json", "--min-fraction", "0.5"])
    assert ns.min_fraction == 0.5
    with pytest.raises(SystemExit):
        parser.parse_args(["eval", "leak", "skill", "q.json", "--min-fraction", "1.5"])


def test_a_moved_split_is_blocked_until_relock_then_passes_and_keeps_the_old_digest(tmp_path, capsys):
    skill, questions = _clean_run(tmp_path)
    split = _split(tmp_path / "split.json")
    cmd_eval_leak(_args(skill, questions, split=str(split)))
    old_digest = json.loads((tmp_path / "split.lock.json").read_text(encoding="utf-8"))["digest"]
    moved = _split(tmp_path / "moved.json", {"dev": ["1", "3"], "val": ["2"], "test": []})
    lock = str(tmp_path / "split.lock.json")
    assert _exit_code(_args(skill, questions, split=str(moved), lock=lock)) == 1
    out = tmp_path / "leak.json"
    cmd_eval_leak(_args(skill, questions, split=str(moved), lock=lock, relock=True, out=out))  # exit 0: relock is not a violation
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["split"]["state"] == "relocked" and saved["split"]["previous_digest"] == old_digest
    assert saved["status"] == "pass" and schema_errors("leak.v1", saved) == []
    assert "lock overwritten (--relock): previous digest " + old_digest[:12] in capsys.readouterr().out
    assert json.loads((tmp_path / "split.lock.json").read_text(encoding="utf-8"))["assignment"]["3"] == "dev"


def test_an_edited_grading_block_blocks_the_run_until_relock(tmp_path):
    skill, questions = _clean_run(tmp_path)
    split = _split(tmp_path / "split.json")
    cmd_eval_leak(_args(skill, questions, split=str(split)))
    edited = _questions(tmp_path / "edited.json", {"1": ANSWER, "2": ANSWER + " sorted",
                                                   "3": "a corrected third answer naming the switch and its default value"})
    assert _exit_code(_args(skill, edited, split=str(split))) == 1  # item 3's grading changed: the split is not the locked one
    cmd_eval_leak(_args(skill, edited, split=str(split), relock=True))


def test_relock_over_a_corrupt_lock_writes_a_new_lock(tmp_path, capsys):
    skill, questions = _clean_run(tmp_path)
    split = _split(tmp_path / "split.json")
    (tmp_path / "split.lock.json").write_text("{not json", encoding="utf-8")
    out = tmp_path / "leak.json"
    cmd_eval_leak(_args(skill, questions, split=str(split), relock=True, out=out))
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["split"]["state"] == "relocked" and saved["split"]["previous_digest"] is None
    assert schema_errors("leak.v1", saved) == []


def test_relock_without_a_split_exits_2(tmp_path, capsys):
    skill, questions = _clean_run(tmp_path)
    assert _exit_code(_args(skill, questions, relock=True)) == 2
    assert capsys.readouterr().err.startswith("Error: --relock overwrites the split's lock")


def test_relock_is_read_from_the_command_line():
    import dokimi.cli as cli

    ns = cli.build_parser().parse_args(["eval", "leak", "skill", "q.json", "--split", "s.json", "--relock"])
    assert ns.relock is True


@pytest.mark.parametrize("target", ["split", "lock", "questions"])
def test_an_out_that_names_an_input_is_refused_and_the_input_kept(tmp_path, target):
    # --out over the split, its lock or the questions would replace the input with the leak.v1 artifact
    skill, questions = _clean_run(tmp_path)
    split = _split(tmp_path / "split.json")
    cmd_eval_leak(_args(skill, questions, split=str(split)))  # a clean run returns; first use creates the lock
    lock = tmp_path / "split.lock.json"
    named = {"split": split, "lock": lock, "questions": questions}[target]
    before = named.read_bytes()
    assert _exit_code(_args(skill, questions, split=str(split), out=str(named))) == 2
    assert named.read_bytes() == before


def test_an_out_whose_rejected_sibling_is_an_input_is_refused_and_the_input_kept(tmp_path):
    # save_artifact writes <out>.rejected when the payload is refused and removes it after a save: an input there goes too
    skill, questions = _clean_run(tmp_path)
    allow_file = tmp_path / "o.json.rejected"
    allow_file.write_text("a term\n", encoding="utf-8")
    before = allow_file.read_bytes()
    assert _exit_code(_args(skill, questions, allow_file=str(allow_file), out=str(tmp_path / "o.json"))) == 2
    assert allow_file.read_bytes() == before


def test_an_out_inside_the_skill_directory_is_refused_and_the_skill_file_kept(tmp_path):
    skill, questions = _clean_run(tmp_path)
    skill_md = skill / "SKILL.md"
    before = skill_md.read_bytes()
    assert _exit_code(_args(skill, questions, out=str(skill_md))) == 2
    assert skill_md.read_bytes() == before


def test_a_relock_lock_that_is_a_hard_link_to_the_split_is_refused_and_the_split_kept(tmp_path):
    # a second name for the split's inode is the split: the name check alone let the lock write go through it
    skill, questions = _clean_run(tmp_path)
    split = _split(tmp_path / "split.json")
    alias = tmp_path / "alias.json"
    os.link(split, alias)
    before = split.read_bytes()
    assert _exit_code(_args(skill, questions, split=str(split), lock=str(alias), relock=True)) == 2
    assert split.read_bytes() == before


def test_an_out_that_is_a_hard_link_to_an_input_is_refused_and_the_input_kept(tmp_path):
    skill, questions = _clean_run(tmp_path)
    allow_file = tmp_path / "allow.txt"
    allow_file.write_text("a term\n", encoding="utf-8")
    alias = tmp_path / "alias.txt"
    os.link(allow_file, alias)
    before = allow_file.read_bytes()
    assert _exit_code(_args(skill, questions, allow_file=str(allow_file), out=str(alias))) == 2
    assert allow_file.read_bytes() == before


def test_a_relock_lock_that_names_the_split_is_refused_and_the_split_kept(tmp_path):
    skill, questions = _clean_run(tmp_path)
    split = _split(tmp_path / "split.json")
    before = split.read_bytes()
    assert _exit_code(_args(skill, questions, split=str(split), lock=str(split), relock=True)) == 2
    assert split.read_bytes() == before


def test_an_out_beside_a_skill_md_given_as_the_skill_is_refused_and_the_sibling_kept(tmp_path):
    # read_skill reads the directory of a SKILL.md argument, so its sibling files are inputs too
    skill, questions = _clean_run(tmp_path)
    helper = skill / "helper.md"
    helper.write_text("helper notes\n", encoding="utf-8")
    before = helper.read_bytes()
    assert _exit_code(_args(skill / "SKILL.md", questions, out=str(helper))) == 2
    assert helper.read_bytes() == before


def test_a_relock_of_a_blocked_run_exits_1_and_leaves_the_old_lock(tmp_path, capsys):
    skill, questions = _clean_run(tmp_path)
    split = _split(tmp_path / "split.json")
    cmd_eval_leak(_args(skill, questions, split=str(split)))
    lock = tmp_path / "split.lock.json"
    before = lock.read_bytes()
    moved = _split(tmp_path / "moved.json", {"dev": ["1", "3"], "val": ["2"], "test": []})
    leaky = _skill(tmp_path / "leaky", f"Copied: {ANSWER}.")
    assert _exit_code(_args(leaky, questions, split=str(moved), lock=str(lock), relock=True)) == 1
    assert lock.read_bytes() == before
    assert "lock not overwritten: the run is blocked, so the old lock stands" in capsys.readouterr().out


def test_the_verbatim_words_flag_defaults_to_twelve_and_refuses_zero(capsys):
    import dokimi.cli as cli

    parser = cli.build_parser()
    assert parser.parse_args(["eval", "leak", "skill", "q.json"]).verbatim_words == 12
    assert parser.parse_args(["eval", "leak", "skill", "q.json", "--verbatim-words", "9"]).verbatim_words == 9
    with pytest.raises(SystemExit):
        parser.parse_args(["eval", "leak", "skill", "q.json", "--verbatim-words", "0"])


def test_a_verbatim_run_of_the_flag_length_exits_1(tmp_path, capsys):
    run_words = " ".join(f"term{i}" for i in range(200))
    questions = _questions(tmp_path / "q.json", {"1": run_words})
    skill = _skill(tmp_path / "skill", "Notes: " + " ".join(f"term{i}" for i in range(50, 62)) + " end.")
    out = tmp_path / "leak.json"
    assert _exit_code(_args(skill, questions, out=out, verbatim_words=12)) == 1
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["verbatim_words"] == 12 and saved["items"][0]["flagged_by"] == ["verbatim"]
    assert schema_errors("leak.v1", saved) == []


def test_a_skipped_skill_file_is_a_warning_in_the_table_and_the_artifact(tmp_path, capsys):
    skill, questions = _clean_run(tmp_path)
    (skill / "notes.txt").write_bytes(f"{ANSWER}".encode("utf-16"))  # UTF-16: not read, so not checked
    out = tmp_path / "leak.json"
    cmd_eval_leak(_args(skill, questions, out=out))
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["warnings"] == ["notes.txt: not UTF-8 text, not read"]
    assert saved["skill_files"] == ["SKILL.md"] and schema_errors("leak.v1", saved) == []
    assert "warning: notes.txt: not UTF-8 text, not read" in capsys.readouterr().out
