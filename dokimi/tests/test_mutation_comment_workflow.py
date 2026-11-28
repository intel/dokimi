# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The surviving-mutants PR comment: ci.yml's mutation-diff job uploads it, cli-interface-comment.yml posts it.

The poster's script runs under node with the fake GitHub client of test_cli_interface_comment_workflow.py,
fed good and hostile mutation results; the job that runs PR code is checked to hold no write token.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tests.test_cli_interface_comment_workflow import SHA, _bodies, _names, _run

CI = Path(__file__).resolve().parents[2] / ".github/workflows/ci.yml"
MARKER = "<!-- mutation-survivors -->"
COUNTS = {"killed": 4, "timeout": 0, "error": 0, "survived": 1, "waived": 1, "inconclusive": 0, "not run": 0}
SURVIVOR = {"path": "dokimi/dokimi/x.py", "line": 12, "op": "flip-compare", "original": "a == b",
            "replacement": "a != b", "tests_run": 3}
WAIVED = {"path": "dokimi/dokimi/x.py", "line": 20, "op": "bump-int", "original": "1", "replacement": "2",
          "reason": "only the sign is read"}
OLD = [{"id": 99, "user": {"login": "github-actions[bot]"}, "body": MARKER + "\nold survivors"}]


UNTESTED = {"failing_tests": 0, "tests_not_run": 0, "uncovered_lines": 0}


def _result(survivors=(SURVIVOR,), waived=(WAIVED,), bare=(), untested=UNTESTED, capped=0, **kw):
    result = {"counts": COUNTS, "survivors": list(survivors), "waived": list(waived), "waivers_without_reason": list(bare),
              "untested": untested, "capped": capped}
    return {"pr_number": 12, "head_sha": SHA, "status": "ok", "result": result, **kw}


def _post(tmp_path: Path, result, gate: str = "", **kw):
    return _run(tmp_path, result, workflow="ci", artifact="mutation-diff-result", env={"MUTATION_GATE": gate}, **kw)


def test_survivors_are_posted_as_one_sticky_comment_with_a_row_each(tmp_path):
    calls = _post(tmp_path, _result())
    body = next(a for n, a in calls if n == "createComment")
    assert body["issue_number"] == 12 and body["body"].startswith(MARKER + "\n## Mutation of changed lines")
    text = body["body"]
    assert "| ` dokimi/dokimi/x.py `:12 | ` flip-compare ` | ` a == b ` | ` a != b ` | 3 |" in text
    assert "| ` dokimi/dokimi/x.py `:20 | ` bump-int ` | ` 1 ` | ` 2 ` | ` only the sign is read ` |" in text
    assert "**1 survived**, 1 waived" in text and "`# mutation-ok: <reason>`" in text
    assert "Warn-only" in text and "https://x/run/7" in text
    assert not {"addLabels", "removeLabel"} & set(_names(calls))


def test_with_the_gate_on_the_comment_says_the_job_fails(tmp_path):
    body = _bodies(_post(tmp_path, _result(), gate="true"))[0]
    assert "The job fails while a survivor is unwaived." in body and "Warn-only" not in body


def test_a_push_that_kills_every_survivor_marks_the_comment_clean(tmp_path):
    calls = _post(tmp_path, _result(survivors=()), comments=OLD)
    update = next(a for n, a in calls if n == "updateComment")
    assert update["comment_id"] == 99 and "No surviving mutants" in update["body"] and "**0 survived**" in update["body"]
    assert "old survivors" not in update["body"] and "only the sign is read" in update["body"]
    assert "createComment" not in _names(calls)


@pytest.mark.parametrize(("untested", "said"), [
    ({**UNTESTED, "failing_tests": 3}, "3 candidate tests fail without a mutant"),
    ({**UNTESTED, "tests_not_run": 2}, "2 candidate test files were not run"),
])
def test_a_run_that_could_not_test_is_not_shown_as_clean(tmp_path, untested, said):
    for comments in (OLD, []):
        calls = _post(tmp_path, _result(survivors=(), untested=untested), comments=comments)
        body = _bodies(calls)[0]
        assert "No surviving mutants" not in body and said in body
        assert "not a clean result" in body


def test_a_run_that_ran_out_of_time_is_not_shown_as_clean(tmp_path):
    result = _result(survivors=(), waived=())
    result["result"] = {**result["result"], "counts": {**COUNTS, "not run": 1}}
    for comments in (OLD, []):
        body = _bodies(_post(tmp_path, result, comments=comments))[0]
        assert "No surviving mutants" not in body and "1 mutants were not run" in body
        assert "not a clean result" in body


def test_a_run_the_mutant_cap_cut_is_not_shown_as_clean_on_every_changed_line(tmp_path):
    for survivors in ((), (SURVIVOR,)):
        body = _bodies(_post(tmp_path, _result(survivors=survivors, capped=14), comments=OLD))[0]
        assert "No surviving mutants" not in body and "The mutant cap left out 14 more mutants" in body


def test_a_run_that_mutated_nothing_because_no_line_was_covered_is_not_shown_as_clean(tmp_path):
    zero = {k: 0 for k in COUNTS}
    untested = {"failing_tests": 0, "tests_not_run": 0, "uncovered_lines": 5}
    result = _result(survivors=(), waived=())
    result["result"] = {**result["result"], "counts": zero, "untested": untested}
    body = _bodies(_post(tmp_path, result, comments=OLD))[0]
    assert "No surviving mutants" not in body and "5 changed lines were not mutated" in body
    assert "not a clean result" in body


def test_a_malformed_untested_count_posts_nothing(tmp_path):
    for bad in (None, {"failing_tests": -1, "tests_not_run": 0, "uncovered_lines": 0}, {"failing_tests": 0}):
        assert not {"createComment", "updateComment"} & set(_names(_post(tmp_path, _result(untested=bad))))


def test_no_survivor_and_no_earlier_comment_posts_nothing(tmp_path):
    calls = _post(tmp_path, _result(survivors=()))
    assert not {"createComment", "updateComment"} & set(_names(calls))


def test_a_waiver_with_no_reason_is_listed(tmp_path):
    body = _bodies(_post(tmp_path, _result(bare=["dokimi/dokimi/x.py:30"])))[0]
    assert "with no reason waives nothing: ` dokimi/dokimi/x.py:30 `" in body


def test_source_text_cannot_mention_forge_the_marker_break_the_table_or_render(tmp_path):
    hostile = {**SURVIVOR, "original": f"{MARKER} @octocat | ``x`` [l](http://evil)\nnext",
               "path": "dokimi/dokimi/<img src=x>.py"}
    body = _bodies(_post(tmp_path, _result(survivors=[hostile])))[0]
    assert body.count(MARKER) == 1 and "@octocat" not in body
    row = next(ln for ln in body.split("\n") if "octocat" in ln)
    assert row.count(" | ") == 4  # five cells: the source's pipe is escaped, its newline joined
    assert "``` <!\u200b-- mutation-survivors --> @\u200boctocat \\| ``x`` [l](http://evil) next ```" in row


def test_many_long_rows_stay_under_githubs_body_limit(tmp_path):
    row = {**SURVIVOR, "original": "@|" * 40, "replacement": "@|" * 40, "path": "@" * 200}
    # 150 of each keeps result.json under the poster's 200 KiB cap; their rows are twice the body's room
    body = _bodies(_post(tmp_path, _result(survivors=[row] * 150, waived=[{**WAIVED, "reason": "@|" * 40}] * 150,
                                           bare=["@" * 200] * 150)))[0]
    assert len(body) < 65536 and "more in the [ci run](https://x/run/7) summary." in body


def test_both_tables_and_their_notes_stay_within_the_table_cap(tmp_path):
    row = {**SURVIVOR, "original": "a" * 80, "replacement": "b" * 80, "path": "p" * 200}
    body = _bodies(_post(tmp_path, _result(survivors=[row] * 150, waived=[{**WAIVED, "reason": "r" * 80}] * 150)))[0]
    shown = sum(len(ln) + 1 for ln in body.split("\n") if ln.startswith("|") or "more in the" in ln)
    assert shown <= 55000 + 2 and body.count("more in the") == 2
    assert "| Line | Operator | Was | Became | Reason |\n|---|---|---|---|---|\n|" in body  # the waived table keeps rows


def test_an_error_result_says_the_check_could_not_run_in_the_jobs_own_words(tmp_path):
    body = _bodies(_post(tmp_path, {"pr_number": 12, "head_sha": SHA, "status": "error", "result": None}))[0]
    assert body.startswith(MARKER) and "could not run" in body and "https://x/run/7" in body


@pytest.mark.parametrize(
    "bad",
    [
        _result(status="done"),
        _result(result=None),
        {**_result(), "result": {**_result()["result"], "counts": {**COUNTS, "killed": -1}}},
        {**_result(), "result": {**_result()["result"], "counts": {**COUNTS, "waived": "1"}}},
        _result(survivors=[{**SURVIVOR, "line": 0}]),
        _result(survivors=[{**SURVIVOR, "tests_run": None}]),
        _result(survivors=[{**SURVIVOR, "original": 5}]),
        {**_result(), "result": {**_result()["result"], "survivors": "a, b"}},
        _result(waived=[{**WAIVED, "reason": None}]),
        _result(bare=[3]),
        _result(capped=-1),
        {**_result(), "result": {k: v for k, v in _result()["result"].items() if k != "capped"}},
        _result(pr_number="12"),
        _result(head_sha="b" * 40),
    ],
)
def test_a_malformed_mutation_result_posts_nothing(tmp_path, bad):
    calls = _post(tmp_path, bad)
    assert not {"createComment", "updateComment", "pulls.get"} & set(_names(calls)) and "warning" in _names(calls)


def test_a_result_for_an_older_head_is_skipped(tmp_path):
    calls = _post(tmp_path, _result(), pr_sha="c" * 40)
    assert not {"createComment", "updateComment"} & set(_names(calls))


def test_a_ci_run_reads_only_its_own_artifact_and_an_unknown_workflow_posts_nothing(tmp_path):
    calls = _run(tmp_path, _result(), workflow="ci", artifact="cli-interface-result")
    assert "download" not in _names(calls) and not _bodies(calls)
    calls = _run(tmp_path, _result(), workflow="docs", artifact="mutation-diff-result")
    assert "download" not in _names(calls) and not _bodies(calls)


def test_a_too_large_mutation_result_replaces_the_stale_comment(tmp_path):
    calls = _post(tmp_path, _result(), size=10**9, comments=OLD)
    update = next(a for n, a in calls if n == "updateComment")
    assert update["body"].startswith(MARKER) and "The mutation result for this commit is too large" in update["body"]


# ---- the producer: ci.yml's mutation-diff job ------------------------------------------------------------------


def _job() -> dict:
    return yaml.safe_load(CI.read_text(encoding="utf-8"))["jobs"]["mutation-diff"]


def test_the_job_that_runs_pr_code_has_no_write_token():
    wf = yaml.safe_load(CI.read_text(encoding="utf-8"))
    assert wf["permissions"] == {"contents": "read"} and "permissions" not in _job()


def test_the_gate_is_off_unless_the_repository_variable_is_true():
    step = next(s for s in _job()["steps"] if s.get("name") == "Mutate changed lines")
    assert step["env"]["MUTATION_GATE"] == "${{ vars.MUTATION_GATE }}"
    assert 'if [ "$MUTATION_GATE" = true ]; then gate=(--fail-on-survivor); fi' in step["run"]
    assert '"${gate[@]}"' in step["run"] and "--comment-json mutation-diff-comment.json" in step["run"]
    assert "--fail-on-survivor" not in step["run"].replace("gate=(--fail-on-survivor)", "")


def test_the_result_is_uploaded_even_when_the_gate_fails_the_mutation_step():
    steps = _job()["steps"]
    pack = next(i for i, s in enumerate(steps) if s.get("name") == "Package the result for the PR comment")
    upload = steps[pack + 1]
    assert steps[pack]["if"] == upload["if"] == "${{ !cancelled() }}"
    assert upload["uses"].startswith("actions/upload-artifact@") and upload["with"]["name"] == "mutation-diff-result"
    assert '"status": "ok" if p.is_file() else "error"' in steps[pack]["run"]
