# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for ``dokimi.eval.leakgate``: the leak gate over answers and checks, and the split lock.

Everything is in memory or in ``tmp_path``; no model is called.
"""

from __future__ import annotations

import json

import pytest

from dokimi.eval.leakgate import (
    _allow_runs,
    assess,
    assignment_digest,
    exposed_test_items,
    expected_texts,
    format_table,
    item_digests,
    lock_split,
    match_split,
    read_ids,
    read_skill,
    read_split,
    read_terms,
    run,
    skill_index,
)

ANSWER = "the flag writes each kernel timing into the chrome trace file under the output directory"  # 15 words
QUESTION = "Which switch should a user pass to get per kernel timings from a profiling run on the device?"
NOW = "2026-11-05T12:00:00+00:00"


def _match(*values, negate=False):
    check = {"type": "match", "values": list(values), "location": "any"}
    if negate:
        check["negate"] = True
    return check


def _q(qid, checks, text=QUESTION):
    return {"id": qid, "question": text, "grading": {"checks": checks}}


def _skill(body, **extra):
    return {"SKILL.md": f"---\nname: demo\n---\n{body}\n", **extra}


def _split_file(tmp_path, dev=(), val=(), test=(), name="split.json"):
    path = tmp_path / name
    path.write_text(json.dumps({"dev": list(dev), "val": list(val), "test": list(test)}), encoding="utf-8")
    return path


# --- leak gate: what is compared ---------------------------------------------------------------

def test_a_skill_that_copies_an_expected_answer_is_flagged_with_its_file():
    rows = assess([_q("1", [_match(ANSWER)])], skill_index(_skill(f"Use this: {ANSWER}.")))
    assert rows[0]["status"] == "flagged"
    assert rows[0]["files"] == ["SKILL.md"]
    assert rows[0]["fraction"] == 1.0


def test_a_skill_that_only_restates_the_question_is_not_flagged():
    rows = assess([_q("1", [_match(ANSWER)])], skill_index(_skill(f"Questions like this: {QUESTION}")))
    assert rows[0]["status"] == "clear" and rows[0]["shared"] == 0


CRITERION = "the answer names the trace flag and says where the chrome trace file is written"  # 16 words


def test_a_judge_reference_is_the_expected_answer_and_its_criterion_is_not_counted_beside_it():
    judge = {"type": "judge", "criterion": CRITERION, "reference": ANSWER}
    assert expected_texts(_q("1", [judge])) == [ANSWER]
    assert assess([_q("1", [judge])], skill_index(_skill(f"Notes: {ANSWER}")))[0]["status"] == "flagged"
    assert assess([_q("1", [judge])], skill_index(_skill(f"Notes: {CRITERION}")))[0]["status"] == "clear"


def test_a_judge_check_with_no_reference_counts_its_criterion():
    judge = {"type": "judge", "criterion": CRITERION}
    assert expected_texts(_q("1", [judge])) == [CRITERION]
    assert assess([_q("1", [judge])], skill_index(_skill(f"Notes: {CRITERION}")))[0]["status"] == "flagged"


def test_a_negated_check_is_not_an_expected_answer():
    forbidden = "the old flag wrote each kernel timing to the legacy text file in the cwd"
    q = _q("1", [_match(ANSWER), _match(forbidden, negate=True)])
    assert forbidden not in expected_texts(q)
    rows = assess([q], skill_index(_skill(f"Do not use: {forbidden}")))
    assert rows[0]["status"] == "clear"


def test_a_regex_pattern_is_not_an_expected_answer():
    q = _q("1", [{"type": "regex", "values": [ANSWER]}])
    assert expected_texts(q) == []
    assert assess([q], skill_index(_skill(ANSWER)))[0]["reason"] == "no literal or judge check to compare"


def test_a_json_check_value_is_compared_as_its_json_text():
    body = '{"count": 2, "device": "gpu", "kernel": "matmul", "run": "first", "unit": "ms"}'  # keys sorted, as the grader dumps them
    q = _q("1", [{"type": "json", "values": [{"device": "gpu", "count": 2, "kernel": "matmul", "unit": "ms", "run": "first"}]}])
    assert assess([q], skill_index(_skill(f"Expected output: {body}")))[0]["status"] == "flagged"


def test_an_answer_shorter_than_the_run_length_is_unchecked_with_its_reason_not_passed():
    q = _q("1", [_match("--ze-profile 1")])
    row = assess([q], skill_index(_skill("--ze-profile 1 appears everywhere")))[0]
    assert row["status"] == "unchecked"
    assert row["reason"] == "no 8-word run left after the allowlist"
    assert row["fraction"] is None


def test_an_item_with_no_check_is_unchecked():
    row = assess([{"id": "1", "question": QUESTION}], skill_index(_skill(QUESTION)))[0]
    assert row["status"] == "unchecked" and row["grams"] == 0


def test_a_run_of_words_does_not_span_two_skill_files():
    left = "alpha beta gamma delta"
    right = "epsilon zeta eta theta"
    answer = f"{left} {right}"  # 8 words: one run, which only the two files together would hold
    files = {"a.md": f"x {left}", "b.md": f"{right} y"}
    assert assess([_q("1", [_match(answer)])], skill_index(files))[0]["status"] == "clear"
    assert assess([_q("1", [_match(answer)])], skill_index({"a.md": f"{left} {right}"}))[0]["status"] == "flagged"


def test_the_flag_is_a_share_of_the_answer_runs_against_min_fraction():
    half = "the flag writes each kernel timing into the chrome trace file"  # first 11 words
    answer = f"{half} and then the summary goes to a second file for review"
    q = [_q("1", [_match(answer)])]
    index = skill_index(_skill(f"Notes: {half}"))
    row = assess(q, index, min_fraction=0.15)[0]
    assert row["shared"] > 0 and 0 < row["fraction"] < 1
    assert row["status"] == "flagged"
    assert assess(q, index, min_fraction=row["fraction"] + 0.01)[0]["status"] == "clear"


# --- allowlist ---------------------------------------------------------------------------------

API_ANSWER = (
    "Run /opt/intel/oneapi/vtune/2024.1/bin64/vtune -collect hotspots -result-dir r1 "
    "and then read the summary table from r1 for the top functions"
)
API_SKILL = "Example command: /opt/intel/oneapi/vtune/2024.1/bin64/vtune -collect hotspots -result-dir r1"


def test_a_shared_path_flags_an_item_without_an_allowlist():
    assert assess([_q("1", [_match(API_ANSWER)])], skill_index(_skill(API_SKILL)))[0]["status"] == "flagged"


def test_the_same_path_on_the_allowlist_is_not_a_leak():
    allow = ["/opt/intel/oneapi/vtune/2024.1/bin64/vtune"]
    row = assess([_q("1", [_match(API_ANSWER)])], skill_index(_skill(API_SKILL), allow), allow)[0]
    assert row["status"] == "clear" and row["shared"] == 0


def test_a_copied_sentence_that_holds_an_allowlisted_term_is_still_flagged():
    # The term is cut from both sides, and the words around it run on: the copy is caught, the term is not counted.
    allow = ["xpu_profiler_chrome_kernel_logging"]
    answer = "one two three four xpu_profiler_chrome_kernel_logging five six seven eight nine"
    assert assess([_q("1", [_match(answer)])], skill_index({"s.md": answer}))[0]["status"] == "flagged"
    row = assess([_q("1", [_match(answer)])], skill_index({"s.md": answer}, allow), allow)[0]
    assert row["status"] == "flagged"


def test_an_allowlisted_term_matches_whole_words_only():
    text = "one two three four vtunes five six seven eight nine"
    assert skill_index({"s": text}, ["vtune"]) == skill_index({"s": text})


def test_read_terms_takes_the_terms_then_the_file_lines_and_skips_comments_and_blanks(tmp_path):
    allow_file = tmp_path / "allow.txt"
    allow_file.write_text("# API names\n\n  torch.profiler  \n/opt/intel/oneapi\n", encoding="utf-8")
    assert read_terms(["vtune", "  "], allow_file) == ["vtune", "torch.profiler", "/opt/intel/oneapi"]


# --- reading the skill and the split -----------------------------------------------------------

def test_read_skill_reads_text_files_beside_skill_md_and_skips_binary_and_git(tmp_path):
    root = tmp_path / "demo"
    (root / "references").mkdir(parents=True)
    (root / ".git").mkdir()
    (root / "SKILL.md").write_text("body", encoding="utf-8")
    (root / "references" / "api.md").write_text("api", encoding="utf-8")
    (root / "blob.bin").write_bytes(b"\xff\xfe\x00")
    (root / ".git" / "config").write_text("skipped", encoding="utf-8")
    assert read_skill(root) == {"SKILL.md": "body", "references/api.md": "api"}
    assert read_skill(root / "SKILL.md") == read_skill(root)  # a SKILL.md means its directory


def test_read_skill_refuses_a_missing_path_and_a_directory_with_no_text(tmp_path):
    with pytest.raises(OSError):
        read_skill(tmp_path / "missing")
    (tmp_path / "empty").mkdir()
    with pytest.raises(ValueError, match="no UTF-8 text file"):
        read_skill(tmp_path / "empty")


def test_read_split_reads_each_id_once_as_a_string(tmp_path):
    path = _split_file(tmp_path, dev=["1", 2], val=["3"], test=["4"])
    assert read_split(path) == {"1": "dev", "2": "dev", "3": "val", "4": "test"}


@pytest.mark.parametrize("body, message", [
    ({"dev": ["1"], "test": ["1"]}, "both dev and test"),
    ({"train": ["1"]}, "keys"),
    ({"dev": "1"}, "list of question ids"),
    ({"dev": [True]}, "list of question ids"),
    ([], "keys"),
])
def test_read_split_refuses_a_malformed_split(tmp_path, body, message):
    path = tmp_path / "split.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        read_split(path)


def test_read_ids_reads_a_json_list_and_refuses_anything_else(tmp_path):
    path = tmp_path / "ids.json"
    path.write_text(json.dumps(["1", 2]), encoding="utf-8")
    assert read_ids(path) == ["1", "2"]
    path.write_text(json.dumps({"ids": ["1"]}), encoding="utf-8")
    with pytest.raises(ValueError, match="JSON list of question ids"):
        read_ids(path)


def test_match_split_refuses_a_missing_extra_or_repeated_id():
    questions = [{"id": "1"}, {"id": "2"}]
    match_split(questions, {"1": "dev", "2": "test"})
    with pytest.raises(ValueError, match="places no question id 2"):
        match_split(questions, {"1": "dev"})
    with pytest.raises(ValueError, match="not in the set: 9"):
        match_split(questions, {"1": "dev", "2": "test", "9": "val"})
    with pytest.raises(ValueError, match="repeat"):
        match_split([{"id": "1"}, {"id": "1"}], {"1": "dev"})


def _contents(*ids, text="a"):
    return {i: text * 8 + i for i in ids}


def test_the_digest_is_the_same_for_the_same_assignment_however_it_is_listed(tmp_path):
    a = read_split(_split_file(tmp_path, dev=["1", "3"], test=["2"], name="a.json"))
    b = read_split(_split_file(tmp_path, test=["2"], dev=["3", "1"], name="b.json"))
    c = _contents("1", "2", "3")
    assert assignment_digest(a, c) == assignment_digest(b, c)
    assert assignment_digest({**a, "1": "val"}, c) != assignment_digest(a, c)


def test_the_digest_covers_the_item_content_not_only_its_id_and_split():
    a = {"1": "dev", "2": "test"}
    assert assignment_digest(a, _contents("1", "2")) != assignment_digest(a, _contents("1", "2", text="b"))


def test_item_digests_change_with_the_grading_block_and_not_with_the_question_text():
    base = _q("1", [_match(ANSWER)])
    assert item_digests([base]) == item_digests([{**base, "question": "another wording of the same item"}])
    assert item_digests([base]) != item_digests([_q("1", [_match("a different answer entirely here now")])])


# --- split lock ----------------------------------------------------------------------------------

def test_the_first_use_creates_the_lock_and_a_later_identical_split_matches_it(tmp_path):
    lock = tmp_path / "split.lock.json"
    assignment, contents = {"1": "dev", "2": "test"}, _contents("1", "2")
    assert lock_split(assignment, contents, lock, NOW) == {"state": "created", "moved": [], "previous_digest": None}
    record = json.loads(lock.read_text(encoding="utf-8"))
    assert record["digest"] == assignment_digest(assignment, contents)
    assert record["counts"] == {"dev": 1, "val": 0, "test": 1}
    assert lock_split(dict(assignment), contents, lock, NOW)["state"] == "matched"


def test_a_split_that_moved_an_item_is_changed_names_the_items_and_leaves_the_lock(tmp_path):
    lock = tmp_path / "split.lock.json"
    lock_split({"1": "dev", "2": "test", "3": "val"}, _contents("1", "2", "3"), lock, NOW)
    before = lock.read_text(encoding="utf-8")
    result = lock_split({"1": "dev", "2": "dev", "3": "val", "4": "test"}, _contents("1", "2", "3", "4"), lock, NOW)
    assert result["state"] == "changed" and result["moved"] == ["2", "4"]
    assert lock.read_text(encoding="utf-8") == before  # never rewritten without relock


def test_an_item_whose_grading_block_changed_is_moved_even_when_its_split_did_not(tmp_path):
    lock = tmp_path / "split.lock.json"
    lock_split({"1": "dev", "2": "test"}, _contents("1", "2"), lock, NOW)
    changed = {**_contents("1", "2"), "2": "edited-content"}
    result = lock_split({"1": "dev", "2": "test"}, changed, lock, NOW)
    assert result["state"] == "changed" and result["moved"] == ["2"]


def test_relock_overwrites_a_changed_lock_and_reports_the_digest_it_replaced(tmp_path):
    lock = tmp_path / "split.lock.json"
    old = {"1": "dev", "2": "test"}
    lock_split(old, _contents("1", "2"), lock, NOW)
    old_digest = json.loads(lock.read_text(encoding="utf-8"))["digest"]
    new = {"1": "dev", "2": "val"}
    result = lock_split(new, _contents("1", "2"), lock, NOW, relock=True)
    assert result == {"state": "relocked", "moved": ["2"], "previous_digest": old_digest}
    record = json.loads(lock.read_text(encoding="utf-8"))
    assert record["assignment"] == new and record["digest"] == assignment_digest(new, _contents("1", "2"))
    assert lock_split(new, _contents("1", "2"), lock, NOW)["state"] == "matched"


def test_relock_on_an_identical_lock_is_matched_and_rewrites_nothing(tmp_path):
    lock = tmp_path / "split.lock.json"
    lock_split({"1": "dev"}, _contents("1"), lock, NOW)
    before = lock.read_text(encoding="utf-8")
    assert lock_split({"1": "dev"}, _contents("1"), lock, "later", relock=True) == {
        "state": "matched", "moved": [], "previous_digest": None}
    assert lock.read_text(encoding="utf-8") == before


def test_relock_replaces_an_unreadable_lock_with_no_previous_digest(tmp_path):
    lock = tmp_path / "split.lock.json"
    lock.write_text("not json", encoding="utf-8")
    assert lock_split({"1": "dev"}, _contents("1"), lock, NOW, relock=True) == {
        "state": "relocked", "moved": [], "previous_digest": None}
    assert lock_split({"1": "dev"}, _contents("1"), lock, NOW)["state"] == "matched"


def test_relock_over_a_hand_edited_lock_records_the_stored_digest(tmp_path):
    lock = tmp_path / "split.lock.json"
    lock_split({"1": "dev"}, _contents("1"), lock, NOW)
    record = json.loads(lock.read_text(encoding="utf-8"))
    record["assignment"]["1"] = "test"
    lock.write_text(json.dumps(record), encoding="utf-8")
    result = lock_split({"1": "dev"}, _contents("1"), lock, NOW, relock=True)
    assert result["state"] == "relocked" and result["previous_digest"] == record["digest"]


def test_a_lock_edited_by_hand_is_refused(tmp_path):
    lock = tmp_path / "split.lock.json"
    lock_split({"1": "dev"}, _contents("1"), lock, NOW)
    record = json.loads(lock.read_text(encoding="utf-8"))
    record["assignment"]["1"] = "test"  # the digest still says dev
    lock.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ValueError, match="was edited"):
        lock_split({"1": "dev"}, _contents("1"), lock, NOW)


@pytest.mark.parametrize("content", ["not json", "[]", json.dumps({"schema_version": "other", "assignment": {}}),
                                     json.dumps({"schema_version": "split_lock.v1", "digest": "x", "assignment": {}})])
def test_a_file_that_is_not_a_split_lock_is_refused(tmp_path, content):
    lock = tmp_path / "split.lock.json"
    lock.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match="not a split lock"):
        lock_split({"1": "dev"}, _contents("1"), lock, NOW)


def test_exposed_test_items_names_the_test_ids_the_proposer_sees_and_refuses_unknown_ones():
    assignment = {"1": "dev", "2": "val", "3": "test"}
    assert exposed_test_items(assignment, ["1", "3"]) == ["3"]
    assert exposed_test_items(assignment, ["1", "2"]) == []
    with pytest.raises(ValueError, match="not in the set: 9"):
        exposed_test_items(assignment, ["9"])


# --- run ------------------------------------------------------------------------------------------

SPLIT = {"1": "dev", "2": "val", "3": "test"}
QUESTIONS = [_q("1", [_match(ANSWER)]), _q("2", [_match(f"{ANSWER} sorted")]), _q("3", [_match("the other answer is written there and nothing else in this one")])]


def test_a_clean_skill_with_a_locked_split_passes(tmp_path):
    result = run(_skill("Use the chrome trace flag."), QUESTIONS, assignment=SPLIT, lock_path=tmp_path / "l.json", now=NOW)
    assert result["status"] == "pass"
    assert result["split"]["state"] == "created" and result["violations"] == []
    assert result["summary"] == {"items": 3, "checked": 3, "flagged": 0, "unchecked": 0, "exposed": 0, "violations": 0}


def test_a_leak_blocks_the_run_and_names_the_item_with_its_split():
    result = run(_skill(f"Answer: {ANSWER}"), QUESTIONS, assignment=SPLIT)
    assert result["status"] == "blocked"
    assert {"kind": "leak", "question_id": "1"} in result["violations"]
    flagged = [r for r in result["items"] if r["status"] == "flagged"]
    assert [(r["question_id"], r["split"]) for r in flagged] == [("1", "dev"), ("2", "val")]


def test_a_changed_split_blocks_the_run_even_with_no_leak(tmp_path):
    lock = tmp_path / "l.json"
    run(_skill("clean"), QUESTIONS, assignment=SPLIT, lock_path=lock, now=NOW)
    result = run(_skill("clean"), QUESTIONS, assignment={**SPLIT, "2": "dev"}, lock_path=lock, now=NOW)
    assert result["status"] == "blocked"
    assert result["split"]["state"] == "changed" and result["split"]["moved"] == ["2"]
    assert {"kind": "split_changed", "moved": ["2"]} in result["violations"]


def test_a_test_item_on_the_proposer_list_blocks_the_run():
    result = run(_skill("clean"), QUESTIONS, assignment=SPLIT, proposer_ids=["1", "3"])
    assert result["status"] == "blocked"
    assert result["exposed_test_items"] == ["3"]
    assert {"kind": "test_exposed", "question_id": "3"} in result["violations"]


def test_a_proposer_that_sees_only_dev_and_val_items_is_not_blocked():
    assert run(_skill("clean"), QUESTIONS, assignment=SPLIT, proposer_ids=["1", "2"])["status"] == "pass"


def test_a_run_with_an_unchecked_item_is_inconclusive_not_pass():
    questions = [_q("1", [_match("--ze-profile 1")])]
    result = run(_skill("clean text"), questions)
    assert result["status"] == "inconclusive"
    assert result["summary"]["unchecked"] == 1 and result["summary"]["checked"] == 0


def test_an_empty_question_set_is_inconclusive():
    assert run(_skill("clean"), [])["status"] == "inconclusive"


def test_a_leak_is_still_reported_when_an_item_is_unchecked():
    questions = [_q("1", [_match(ANSWER)]), _q("2", [_match("--ze-profile 1")])]
    result = run(_skill(ANSWER), questions)
    assert result["status"] == "blocked"
    assert result["summary"] == {"items": 2, "checked": 1, "flagged": 1, "unchecked": 1, "exposed": 0, "violations": 1}


def test_a_proposer_list_without_a_split_is_refused():
    with pytest.raises(ValueError, match="needs a split"):
        run(_skill("clean"), QUESTIONS, proposer_ids=["1"])


def test_a_split_that_does_not_match_the_questions_is_refused():
    with pytest.raises(ValueError, match="places no question id"):
        run(_skill("clean"), QUESTIONS, assignment={"1": "dev"})


@pytest.mark.parametrize("bad", [0, -0.1, 1.5])
def test_a_min_fraction_outside_zero_to_one_is_refused(bad):
    with pytest.raises(ValueError, match="min_fraction"):
        run(_skill("clean"), QUESTIONS, min_fraction=bad)


def test_the_result_names_its_settings_and_the_skill_files():
    result = run({"SKILL.md": "a", "x/ref.md": "b"}, [_q("1", [_match(ANSWER)])], allow=["vtune", "vtune"])
    assert result["allow"] == ["vtune"] and result["ngram"] == 8
    assert result["skill_files"] == ["SKILL.md", "x/ref.md"]
    assert result["split"] is None


def test_the_table_names_the_split_the_changed_items_and_the_status(tmp_path):
    lock = tmp_path / "l.json"
    run(_skill("clean"), QUESTIONS, assignment=SPLIT, lock_path=lock, now=NOW)
    result = run(_skill(f"Answer: {ANSWER}"), QUESTIONS, assignment={**SPLIT, "3": "dev"}, lock_path=lock, now=NOW)
    table = format_table(result)
    assert "split changed since its lock: 1 items moved: 3" in table
    assert table.splitlines()[-1].startswith("status blocked: 3 items, 3 checked, 2 flagged")


def test_the_table_cuts_the_flagged_items_at_twenty_and_points_to_the_artifact():
    questions = [_q(str(i), [_match(ANSWER)]) for i in range(25)]
    result = run(_skill(ANSWER), questions)
    assert "5 more flagged items; rerun with --out FILE" in format_table(result)
    assert "5 more flagged items in the artifact" in format_table(result, output="leak.json")


def test_an_edited_grading_block_blocks_the_run_as_a_changed_split(tmp_path):
    lock = tmp_path / "l.json"
    run(_skill("clean"), QUESTIONS, assignment=SPLIT, lock_path=lock, now=NOW)
    edited = [QUESTIONS[0], _q("2", [_match("a corrected answer that replaces the old one in full here")]), QUESTIONS[2]]
    result = run(_skill("clean"), edited, assignment=SPLIT, lock_path=lock, now=NOW)
    assert result["status"] == "blocked"
    assert result["split"]["state"] == "changed" and result["split"]["moved"] == ["2"]


def test_relock_accepts_the_new_split_and_reports_the_old_digest_without_a_split_violation(tmp_path):
    lock = tmp_path / "l.json"
    first = run(_skill("clean"), QUESTIONS, assignment=SPLIT, lock_path=lock, now=NOW)
    moved = {**SPLIT, "2": "dev"}
    result = run(_skill("clean"), QUESTIONS, assignment=moved, lock_path=lock, now=NOW, relock=True)
    assert result["status"] == "pass"
    assert result["violations"] == []
    assert result["split"]["state"] == "relocked"
    assert result["split"]["previous_digest"] == first["split"]["digest"]
    assert result["split"]["moved"] == ["2"]


def test_relock_needs_a_split_and_a_lock():
    with pytest.raises(ValueError, match="relock needs a split and its lock"):
        run(_skill("clean"), QUESTIONS, relock=True)
    with pytest.raises(ValueError, match="relock needs a split and its lock"):
        run(_skill("clean"), QUESTIONS, assignment=SPLIT, relock=True)


def test_the_table_reports_a_relock_with_the_digest_it_replaced(tmp_path):
    lock = tmp_path / "l.json"
    first = run(_skill("clean"), QUESTIONS, assignment=SPLIT, lock_path=lock, now=NOW)
    result = run(_skill("clean"), QUESTIONS, assignment={**SPLIT, "2": "dev"}, lock_path=lock, now=NOW, relock=True)
    table = format_table(result)
    assert table.splitlines()[0].startswith("split relocked: digest ")
    assert f"lock overwritten (--relock): previous digest {first['split']['digest'][:12]}" in table
    assert "1 items moved since the old lock: 2" in table


def test_the_default_min_fraction_is_a_tenth():
    from dokimi.eval.leakgate import DEFAULT_MIN_FRACTION
    assert DEFAULT_MIN_FRACTION == 0.10
    assert run(_skill("clean"), QUESTIONS)["min_fraction"] == 0.10


def test_a_skill_stored_in_nfd_is_checked_like_nfc():
    # A combining accent is not a word character for \w+, so an NFD copy of an NFC answer used to split its words.
    import unicodedata
    answer = "the profiler writes café timelines of kernel launches"  # 8 words, one of them accented
    nfd = unicodedata.normalize("NFD", answer)
    assert nfd != answer
    assert assess([_q("1", [_match(answer)])], skill_index({"s.md": nfd}))[0]["status"] == "flagged"
    assert assess([_q("1", [_match(nfd)])], skill_index({"s.md": answer}))[0]["status"] == "flagged"


def test_a_json_check_value_with_accented_text_is_compared_as_its_text():
    # json.dumps with ensure_ascii escaped the accent into tokens the skill's own text never has
    words_ = " ".join(f"café{i}" for i in range(20))
    q = _q("1", [{"type": "json", "values": [{"note": words_}]}])
    assert assess([q], skill_index(_skill(f"Expected output: {words_}")))[0]["status"] == "flagged"


def test_a_bom_on_the_first_allow_line_does_not_make_a_comment_a_term(tmp_path):
    allow = tmp_path / "allow.txt"
    allow.write_bytes("﻿# why this term is allowed\nxpu_profiler\n".encode("utf-8"))
    from dokimi.eval.leakgate import read_terms
    assert read_terms([], allow) == ["xpu_profiler"]


# --- relock is written only when the run passes ---------------------------------------------------

def test_a_relock_of_a_run_blocked_by_a_leak_leaves_the_old_lock(tmp_path):
    lock = tmp_path / "l.json"
    first = run(_skill("clean"), QUESTIONS, assignment=SPLIT, lock_path=lock, now=NOW)
    before = lock.read_bytes()
    moved = {**SPLIT, "2": "dev"}
    result = run(_skill(f"Answer: {ANSWER}"), QUESTIONS, assignment=moved, lock_path=lock, now=NOW, relock=True)
    assert result["status"] == "blocked"
    assert lock.read_bytes() == before
    assert result["split"]["state"] == "not_relocked"
    assert result["split"]["previous_digest"] == first["split"]["digest"]
    assert result["split"]["moved"] == ["2"]
    assert all(v["kind"] != "split_changed" for v in result["violations"])


def test_a_relock_of_a_run_that_gives_the_proposer_a_test_item_leaves_the_old_lock(tmp_path):
    lock = tmp_path / "l.json"
    run(_skill("clean"), QUESTIONS, assignment=SPLIT, lock_path=lock, now=NOW)
    before = lock.read_bytes()
    result = run(_skill("clean"), QUESTIONS, assignment={**SPLIT, "2": "dev"}, lock_path=lock, now=NOW,
                 relock=True, proposer_ids=["3"])
    assert result["status"] == "blocked" and result["split"]["state"] == "not_relocked"
    assert lock.read_bytes() == before


def test_a_relock_of_an_inconclusive_run_leaves_the_old_lock(tmp_path):
    lock = tmp_path / "l.json"
    run(_skill("clean"), QUESTIONS, assignment=SPLIT, lock_path=lock, now=NOW)
    before = lock.read_bytes()
    unchecked = QUESTIONS + [_q("4", [])]
    result = run(_skill("clean"), unchecked, assignment={**SPLIT, "4": "val"}, lock_path=lock, now=NOW, relock=True)
    assert result["status"] == "inconclusive" and result["split"]["state"] == "not_relocked"
    assert lock.read_bytes() == before


def test_a_relock_of_a_passing_run_writes_the_new_lock(tmp_path):
    lock = tmp_path / "l.json"
    run(_skill("clean"), QUESTIONS, assignment=SPLIT, lock_path=lock, now=NOW)
    result = run(_skill("clean"), QUESTIONS, assignment={**SPLIT, "2": "dev"}, lock_path=lock, now=NOW, relock=True)
    assert result["status"] == "pass" and result["split"]["state"] == "relocked"
    assert json.loads(lock.read_text(encoding="utf-8"))["assignment"]["2"] == "dev"


def test_lock_split_with_write_relock_off_keeps_the_lock_and_names_the_moved_items(tmp_path):
    from dokimi.eval.leakgate import lock_split
    lock = tmp_path / "split.lock.json"
    lock_split({"1": "dev", "2": "test"}, _contents("1", "2"), lock, NOW)
    before = lock.read_bytes()
    result = lock_split({"1": "dev", "2": "val"}, _contents("1", "2"), lock, NOW, relock=True, write_relock=False)
    assert result == {"state": "not_relocked", "moved": ["2"],
                      "previous_digest": json.loads(before)["digest"]}
    assert lock.read_bytes() == before


def test_the_table_says_a_relock_that_did_not_run_left_the_old_lock(tmp_path):
    lock = tmp_path / "l.json"
    run(_skill("clean"), QUESTIONS, assignment=SPLIT, lock_path=lock, now=NOW)
    result = run(_skill(f"Answer: {ANSWER}"), QUESTIONS, assignment={**SPLIT, "2": "dev"}, lock_path=lock, now=NOW,
                 relock=True)
    table = format_table(result)
    assert table.splitlines()[0].startswith("split not relocked: digest ")
    assert "the run did not pass, so the old lock stands" in table


# --- verbatim run rule: N words copied from an answer, whatever the share ------------------------

LONG = " ".join(f"term{i}" for i in range(200))  # 200 distinct words: a share of 12 words is a few percent


def _run_of(start, count):
    return " ".join(f"term{i}" for i in range(start, start + count))


def test_a_verbatim_run_of_twelve_words_is_flagged_though_its_share_is_below_the_threshold():
    row = assess([_q("1", [_match(LONG)])], skill_index(_skill(f"Notes: {_run_of(50, 12)} end")),
                 verbatim_index=skill_index(_skill(f"Notes: {_run_of(50, 12)} end"), n=12), verbatim=12)[0]
    assert row["fraction"] < 0.10 and row["verbatim"] == 1
    assert row["status"] == "flagged" and row["flagged_by"] == ["verbatim"]


def test_an_eleven_word_run_is_not_a_verbatim_leak_at_the_default_length():
    text = _skill(f"Notes: {_run_of(50, 11)} end")
    row = assess([_q("1", [_match(LONG)])], skill_index(text), verbatim_index=skill_index(text, n=12), verbatim=12)[0]
    assert row["verbatim"] == 0 and row["status"] == "clear"


def test_the_verbatim_length_is_a_parameter():
    text = _skill(f"Notes: {_run_of(50, 11)} end")
    result = run(text, [_q("1", [_match(LONG)])], verbatim=11)
    assert result["verbatim_words"] == 11 and result["status"] == "blocked"
    assert result["items"][0]["flagged_by"] == ["verbatim"]


def test_a_verbatim_run_is_flagged_through_run_and_blocks_it():
    result = run(_skill(f"Notes: {_run_of(50, 12)} end"), [_q("1", [_match(LONG)])])
    assert result["status"] == "blocked"
    assert {"kind": "leak", "question_id": "1"} in result["violations"]


def test_an_allowlisted_run_is_not_a_verbatim_leak():
    allow = [f"term{i}" for i in range(50, 62)]
    result = run(_skill(f"Notes: {_run_of(50, 12)} end"), [_q("1", [_match(LONG)])], allow=allow)
    assert result["items"][0]["verbatim"] == 0 and result["status"] == "pass"


def test_a_verbatim_run_does_not_span_two_skill_files():
    files = {"SKILL.md": f"Notes: {_run_of(50, 6)}", "other.md": f"{_run_of(56, 6)} end"}
    result = run(files, [_q("1", [_match(LONG)])])
    assert result["items"][0]["verbatim"] == 0


def test_the_verbatim_length_must_be_at_least_one():
    with pytest.raises(ValueError, match="verbatim"):
        run(_skill("clean"), QUESTIONS, verbatim=0)


def test_a_verbatim_run_in_nfd_is_caught_like_nfc():
    import unicodedata
    answer = " ".join(["café"] + [f"term{i}" for i in range(12)] + ["x"])
    nfd = unicodedata.normalize("NFD", f"Notes: {answer}")
    result = run(_skill(nfd), [_q("1", [_match(answer)])], verbatim=12)
    assert result["items"][0]["verbatim"] > 0 and result["status"] == "blocked"


# --- skipped skill files are warnings -----------------------------------------------------------

def test_read_skill_reports_a_non_utf8_file_and_a_symlink_as_skipped(tmp_path):
    import os
    from dokimi.eval.leakgate import read_skill as rs
    root = tmp_path / "skill"
    root.mkdir()
    (root / "SKILL.md").write_text("benign\n", encoding="utf-8")
    (root / "notes.txt").write_bytes("held-out answer".encode("utf-16"))
    os.symlink(root / "SKILL.md", root / "link.md")
    skipped: list[str] = []
    files = rs(root, skipped=skipped)
    assert sorted(files) == ["SKILL.md"]
    assert skipped == ["link.md: symbolic link, not read", "notes.txt: not UTF-8 text, not read"]


def test_read_skill_without_a_skipped_list_still_skips_quietly(tmp_path):
    from dokimi.eval.leakgate import read_skill as rs
    (tmp_path / "SKILL.md").write_text("benign\n", encoding="utf-8")
    (tmp_path / "bin.dat").write_bytes(b"\xff\xfe\x00\x81")
    assert sorted(rs(tmp_path)) == ["SKILL.md"]


def test_format_table_prints_each_warning_under_the_split_line():
    result = run({"SKILL.md": "clean"}, QUESTIONS)
    result["warnings"] = ["notes.txt: not UTF-8 text, not read"]
    lines = format_table(result).splitlines()
    assert "warning: notes.txt: not UTF-8 text, not read" in lines


# --- review follow-ups: a blank reference, a non-string type, digests, ties, the threshold and case -----------

def test_a_blank_judge_reference_falls_back_to_the_criterion_as_the_grader_does():
    # judge_point strips the reference and uses the criterion when the reference is blank: so must the gate
    judge = {"type": "judge", "criterion": CRITERION, "reference": "   "}
    assert expected_texts(_q("1", [judge])) == [CRITERION]
    assert assess([_q("1", [judge])], skill_index(_skill(f"Notes: {CRITERION}")))[0]["status"] == "flagged"


def test_a_check_type_that_is_not_a_string_is_refused_with_a_value_error():
    with pytest.raises(ValueError, match="type must be a string"):
        expected_texts(_q("1", [{"type": ["match"], "values": [ANSWER]}]))


def test_a_fuzzy_check_value_is_an_expected_answer():
    q = _q("1", [{"type": "fuzzy", "values": [ANSWER]}])
    assert expected_texts(q) == [ANSWER]
    assert assess([q], skill_index(_skill(f"Use this: {ANSWER}.")))[0]["status"] == "flagged"


def test_a_skill_in_capitals_is_checked_like_its_lower_case_answer():
    assert assess([_q("1", [_match(ANSWER)])], skill_index(_skill(ANSWER.upper())))[0]["status"] == "flagged"


def test_an_item_exactly_at_min_fraction_is_flagged():
    # 17 words hold 10 runs of 8; one run in the skill is 1 of 10, which is 0.10 exactly
    answer = ("alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike november oscar "
              "papa quebec")
    rows = assess([_q("1", [_match(answer)])], skill_index(_skill("Notes: alpha bravo charlie delta echo foxtrot golf hotel.")),
                  min_fraction=0.10)
    assert rows[0]["grams"] == 10 and rows[0]["fraction"] == 0.1
    assert rows[0]["status"] == "flagged"


def test_equal_length_allowlist_terms_are_cut_longest_first_then_in_a_fixed_order():
    # the order must not depend on the order the terms were listed in, or on the hash seed
    assert _allow_runs(["beta gamma", "alpha beta"]) == _allow_runs(["alpha beta", "beta gamma"])
    assert _allow_runs(["beta gamma", "alpha beta"]) == [("alpha", "beta"), ("beta", "gamma")]
    answer = "alpha beta gamma delta epsilon zeta eta theta iota kappa"
    skill = _skill("Notes: gamma delta epsilon zeta eta theta iota kappa.")
    for allow in (["alpha beta", "beta gamma"], ["beta gamma", "alpha beta"]):
        assert assess([_q("1", [_match(answer)])], skill_index(skill, allow))[0]["status"] == "flagged"


def test_a_stored_digest_that_is_not_sha256_hex_is_not_reported_as_the_previous_digest(tmp_path):
    lock = tmp_path / "split.lock.json"
    lock_split({"1": "dev"}, _contents("1"), lock, NOW)
    record = json.loads(lock.read_text(encoding="utf-8"))
    record["digest"] = "x"
    lock.write_text(json.dumps(record), encoding="utf-8")
    result = lock_split({"1": "dev"}, _contents("1"), lock, NOW, relock=True)
    assert result["state"] == "relocked" and result["previous_digest"] is None


def test_a_blocked_relock_leaves_an_unreadable_lock_as_it_was(tmp_path):
    lock = tmp_path / "split.lock.json"
    lock.write_text("not json", encoding="utf-8")
    result = lock_split({"1": "dev"}, _contents("1"), lock, NOW, relock=True, write_relock=False)
    assert result == {"state": "not_relocked", "moved": [], "previous_digest": None}
    assert lock.read_text(encoding="utf-8") == "not json"
