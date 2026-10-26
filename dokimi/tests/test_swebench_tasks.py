# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""SWE-bench rows as executable tasks (dokimi.harnesses.swebench) and their grader.

The fixture rows keep the SWE-bench Lite field set (FAIL_TO_PASS as the Hugging Face
JSON string in one row, a list in the other) with tiny synthetic patches. The docker
test at the end runs one real instance image and is skipped unless
``DOKIMI_SWEBENCH_DOCKER=1``: the image is ~4 GB.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
from pathlib import Path

import pytest

from dokimi.cli import build_parser
from dokimi.harnesses import create_harness
from dokimi.harnesses import swebench_grade as grader
from dokimi.harnesses.swebench import (
    SweBenchError,
    eval_script,
    instance_image,
    load_swebench,
    patch_files,
    read_instances,
    repo_spec,
    specs,
    stage,
    test_directives as directives,
)
from dokimi.harnesses.tasks import FORMAT_SWE_BENCH

FIXTURE = Path(__file__).parent / "fixtures" / "swebench" / "lite-2.json"
PYTEST_ID = "pytest-dev__pytest-5227"
DJANGO_ID = "django__django-11099"


def _row(iid: str) -> dict:
    return next(r for r in read_instances(FIXTURE) if r["instance_id"] == iid)


def _log(body: str) -> str:
    return f"+ git apply\n>>>>> Start Test Output\n{body}\n>>>>> End Test Output\n"


# ── loading and staging ──────────────────────────────────────────────────────


def test_a_row_stages_as_a_swe_bench_task_on_its_prebuilt_image(tmp_path):
    task = stage(_row(PYTEST_ID), tmp_path)
    assert task.format == FORMAT_SWE_BENCH
    assert task.environment["docker_image"] == "swebench/sweb.eval.x86_64.pytest-dev_1776_pytest-5227:latest"
    dockerfile = (task.path / "environment" / "Dockerfile").read_text()
    assert dockerfile.startswith("FROM swebench/sweb.eval.x86_64.pytest-dev_1776_pytest-5227:latest\n")
    assert "mv /testbed /app && ln -s /app /testbed" in dockerfile
    assert task.solution_dir == task.path / "solution" and task.tests_dir == task.path / "tests"
    assert (task.solution_dir / "patch.diff").read_text() == _row(PYTEST_ID)["patch"]
    assert task.instruction.startswith("Improve default logging format")


def test_the_hugging_face_json_string_test_lists_are_decoded(tmp_path):
    task = stage(_row(PYTEST_ID), tmp_path)
    config = json.loads((task.tests_dir / "config.json").read_text())
    assert config == {"instance_id": PYTEST_ID, "parser": "pytest",
                      "FAIL_TO_PASS": ["testing/test_x.py::test_b"],
                      "PASS_TO_PASS": ["testing/test_x.py::test_a"]}


def test_an_empty_patch_override_is_the_negative_control(tmp_path):
    task = stage(_row(PYTEST_ID), tmp_path, patch="")
    assert (task.solution_dir / "patch.diff").read_text() == ""
    assert task.metadata["oracle_patch"] == "override"


def test_a_row_naming_its_own_image_keeps_it():
    row = {"instance_id": "a__b-1", "image_name": "jyangballin/swesmith.x86_64.a_1776_b.abc"}
    assert instance_image(row) == "jyangballin/swesmith.x86_64.a_1776_b.abc"
    assert instance_image({"instance_id": "Org__Repo-2"}, namespace="me") == "me/sweb.eval.x86_64.org_1776_repo-2:latest"


def test_a_row_image_name_with_a_newline_is_refused_not_written_into_the_dockerfile(tmp_path):
    # The image goes into the Dockerfile's FROM line; a newline would add instructions.
    row = dict(_row(PYTEST_ID), image_name="ubuntu:22.04\nRUN echo injected > /tmp/x")
    with pytest.raises(SweBenchError, match="image reference"):
        stage(row, tmp_path / "root")
    assert list(tmp_path.iterdir()) == []


def test_load_selects_ids_in_order_and_names_an_unknown_one(tmp_path):
    tasks = load_swebench(FIXTURE, tmp_path, [DJANGO_ID, PYTEST_ID])
    assert [t.name for t in tasks] == [DJANGO_ID, PYTEST_ID]
    with pytest.raises(SweBenchError, match="no instance nope__nope-1"):
        load_swebench(FIXTURE, tmp_path, ["nope__nope-1"])


def test_all_skips_the_known_bad_rows_and_an_explicit_id_still_runs(tmp_path):
    from dokimi.harnesses.network_policy import resolve
    from dokimi.harnesses.swebench import KNOWN_BAD

    rows = json.loads(FIXTURE.read_text(encoding="utf-8"))
    bad = dict(rows[0], instance_id="psf__requests-863")
    data = tmp_path / "rows.json"
    data.write_text(json.dumps([*rows, bad]), encoding="utf-8")
    assert "psf__requests-863" in KNOWN_BAD
    assert "psf__requests-863" not in {t.name for t in load_swebench(data, tmp_path / "a")}
    (task,) = load_swebench(data, tmp_path / "b", ["psf__requests-863"])
    assert resolve(task).verify.mode == "none"  # offline unless the operator opens it


def test_the_all_banner_names_only_the_known_bad_rows_the_file_holds(tmp_path, capsys):
    import argparse

    from dokimi.commands.tasks import _select_swebench

    rows = json.loads(FIXTURE.read_text(encoding="utf-8"))
    data = tmp_path / "rows.json"
    args = argparse.Namespace(all=True, tasks=None, swe_bench=str(data), output_dir=str(tmp_path / "out"))
    data.write_text(json.dumps([*rows, dict(rows[0], instance_id="psf__requests-863")]), encoding="utf-8")
    _select_swebench(args)
    err = capsys.readouterr().err
    assert "skips 1 known-bad rows" in err and "psf__requests-863" in err
    assert "astropy__astropy-7606" not in err  # a KNOWN_BAD id the file does not hold is not named
    data.write_text(json.dumps(rows), encoding="utf-8")
    _select_swebench(args)
    assert "skips" not in capsys.readouterr().err


def test_a_row_without_a_test_spec_or_a_field_is_refused(tmp_path):
    row = dict(_row(PYTEST_ID), repo="someone/else")
    with pytest.raises(SweBenchError, match="no SWE-bench test spec for repo 'someone/else'"):
        stage(row, tmp_path)
    row = {k: v for k, v in _row(PYTEST_ID).items() if k != "test_patch"}
    with pytest.raises(SweBenchError, match="missing test_patch"):
        stage(row, tmp_path)
    with pytest.raises(SweBenchError, match="version '0.0'"):
        repo_spec("pytest-dev/pytest", "0.0")



@pytest.mark.parametrize("field", ["repo", "base_commit", "problem_statement", "patch", "test_patch"])
def test_a_null_or_non_string_text_field_is_a_row_error_not_a_traceback(tmp_path, field):
    with pytest.raises(SweBenchError, match=f"{field} must be a string"):
        stage(dict(_row(PYTEST_ID), **{field: None}), tmp_path)


def test_an_unsafe_or_duplicate_instance_id_is_refused(tmp_path):
    row = _row(PYTEST_ID)
    for bad in ("../../escaped__x-1", str(tmp_path / "abs__x-1"), "a/b", ".hidden"):
        with pytest.raises(SweBenchError, match="instance_id must match"):
            stage(dict(row, instance_id=bad), tmp_path / "root")
    assert list(tmp_path.iterdir()) == []
    data = tmp_path / "dup.json"
    data.write_text(json.dumps([row, dict(row, patch="SECOND")]), encoding="utf-8")
    for ids in (None, [PYTEST_ID]):
        with pytest.raises(SweBenchError, match=f"duplicate instance_id {PYTEST_ID}"):
            load_swebench(data, tmp_path / "d", ids)
    assert not (tmp_path / "d").exists()


# ── the eval script ──────────────────────────────────────────────────────────


def test_the_eval_script_resets_applies_and_runs_the_touched_test_files():
    script = eval_script(_row(PYTEST_ID), repo_spec("pytest-dev/pytest", "4.4")[1])
    base = _row(PYTEST_ID)["base_commit"]
    assert script.count(f"git checkout {base} testing/test_x.py") == 2  # before and after
    assert "rm -f -- testing/data.json" in script  # added by the test patch
    assert "python -m pip install -e ." in script  # upstream reinstalls before the run
    start, run, end = script.index("Start Test Output"), script.index("pytest -rA testing/test_x.py"), \
        script.index("End Test Output")
    assert start < run < end
    assert "testing/data.json" not in script[start:end]  # a .json file is not a directive


def test_the_eval_script_passes_row_values_as_words_not_shell():
    test_patch = ("diff --git a/t$(touch pwned2).py b/t$(touch pwned2).py\n"
                  "diff --git a/a b.py b/a b.py\n"
                  "diff --git a/t.py b/t.py\n--- a/t.py\n+++ b/t.py\n")
    row = dict(_row(PYTEST_ID), base_commit="abc; touch pwned #", test_patch=test_patch)
    lines = eval_script(row, repo_spec("pytest-dev/pytest", "4.4")[1]).splitlines()
    words = [shlex.split(line) for line in lines if line.startswith(("git checkout", "git -c", "pytest"))]
    assert ["git", "-c", "core.fileMode=false", "diff", "abc; touch pwned #"] in words
    assert words.count(["git", "checkout", "abc; touch pwned #", "t.py"]) == 2
    assert ["pytest", "-rA", "t$(touch pwned2).py", "a b.py", "t.py"] in words


def test_a_tracked_test_file_with_a_space_is_reset_by_its_whole_name():
    # git puts a tab after a name with a space in its ---/+++ header lines.
    test_patch = "diff --git a/a b.py b/a b.py\n--- a/a b.py\t\n+++ b/a b.py\t\n"
    row = dict(_row(PYTEST_ID), test_patch=test_patch)
    lines = eval_script(row, repo_spec("pytest-dev/pytest", "4.4")[1]).splitlines()
    words = [shlex.split(line) for line in lines if line.startswith("git checkout")]
    assert words == [["git", "checkout", row["base_commit"], "a b.py"]] * 2


def test_an_added_test_file_with_a_space_is_cleaned_by_its_whole_name():
    # An added file has no "--- a/" name, so only the tab-stripped "+++ b/" side supplies it.
    test_patch = "diff --git a/new file.py b/new file.py\n--- /dev/null\n+++ b/new file.py\t\n"
    assert patch_files(test_patch) == ([], ["new file.py"])
    row = dict(_row(PYTEST_ID), test_patch=test_patch)
    lines = eval_script(row, repo_spec("pytest-dev/pytest", "4.4")[1]).splitlines()
    words = [shlex.split(line) for line in lines if line.startswith("rm -f")]
    assert words and all(w == ["rm", "-f", "--", "new file.py"] for w in words)


def test_a_test_patch_that_ends_the_heredoc_is_refused_not_run_as_shell():
    # The patch is fed to git apply by a heredoc; a line equal to its delimiter ends the heredoc.
    test_patch = _row(PYTEST_ID)["test_patch"] + "EOF_114329324912\ntouch pwned\n"
    row = dict(_row(PYTEST_ID), test_patch=test_patch)
    with pytest.raises(SweBenchError, match="heredoc delimiter"):
        eval_script(row, repo_spec("pytest-dev/pytest", "4.4")[1])


@pytest.mark.parametrize("line", ["EOF_1143293\x0024912", "EOF_114329324912\x00"])
def test_a_nul_byte_that_bash_would_drop_cannot_end_the_heredoc(line):
    # bash drops NUL bytes while reading a script, so either line ends the heredoc there.
    row = dict(_row(PYTEST_ID), test_patch=_row(PYTEST_ID)["test_patch"] + line + "\ntouch pwned\n")
    with pytest.raises(SweBenchError, match="NUL byte"):
        eval_script(row, repo_spec("pytest-dev/pytest", "4.4")[1])


def test_django_directives_are_dotted_test_modules():
    assert directives("django/django", _row(DJANGO_ID)["test_patch"]) == ["auth_tests.test_validators"]
    script = eval_script(_row(DJANGO_ID), repo_spec("django/django", "3.0")[1])
    assert "./tests/runtests.py --verbosity 2 --settings=test_sqlite --parallel 1 auth_tests.test_validators" in script


def test_every_repo_in_the_spec_table_has_a_grader_parser():
    parsers = {entry["parser"] for entry in specs()["repos"].values()}
    assert parsers <= set(grader.PARSERS)
    assert specs()["source"].startswith("swebench==")


# ── the grader (SWE-bench resolution semantics) ──────────────────────────────

_CONFIG = {"instance_id": "x", "parser": "pytest", "FAIL_TO_PASS": ["t.py::f"], "PASS_TO_PASS": ["t.py::p"]}


def test_all_tests_passing_resolves():
    report = grader.grade(_log("PASSED t.py::f\nPASSED t.py::p"), _CONFIG)
    assert report["resolved"] is True


def test_a_missing_or_failed_fail_to_pass_test_does_not_resolve():
    assert grader.grade(_log("PASSED t.py::p"), _CONFIG)["FAIL_TO_PASS"]["failure"] == ["t.py::f"]
    report = grader.grade(_log("FAILED t.py::f - AssertionError\nPASSED t.py::p"), _CONFIG)
    assert report["resolved"] is False


def test_a_skipped_test_counts_neither_way_as_upstream():
    report = grader.grade(_log("PASSED t.py::f\nSKIPPED t.py::p"), _CONFIG)
    assert report["PASS_TO_PASS"] == {"success": [], "failure": []}
    assert report["resolved"] is True


def test_an_xfail_fail_to_pass_test_counts_as_a_success():
    report = grader.grade(_log("XFAIL t.py::f\nPASSED t.py::p"), _CONFIG)
    assert report["FAIL_TO_PASS"] == {"success": ["t.py::f"], "failure": []}
    assert report["resolved"] is True


def test_an_empty_marked_section_falls_back_to_the_whole_log():
    log = "PASSED t.py::f\nPASSED t.py::p\n" + _log("")
    assert grader.grade(log, _CONFIG)["resolved"] is True


def test_a_bare_seaborn_status_word_is_skipped_not_an_index_error():
    # A line that is only FAILED or PASSED has no test name; it must not raise.
    assert grader.parse_seaborn("FAILED\nPASSED\nPASSED t.py::f\n") == {"t.py::f": "PASSED"}


def test_pytest_v2_reads_coloured_and_old_style_lines():
    config = dict(_CONFIG, parser="pytest_v2")
    coloured = _log("\x1b[32mPASSED\x1b[0m t.py::f\nPASSED t.py::p")
    assert grader.grade(coloured, config)["resolved"] is True
    old_style = _log("t.py::f PASSED\nPASSED t.py::p")
    assert grader.grade(old_style, config)["resolved"] is True


def test_a_log_without_a_test_run_does_not_resolve():
    assert grader.grade("error: patch failed\n", _CONFIG) == {"instance_id": "x", "tests_ran": False,
                                                             "resolved": False}


@pytest.mark.parametrize("code", ["Patch Apply Failed", "Reset Failed", "Tests Errored", "Tests Timed Out"])
def test_a_bad_code_in_the_log_does_not_resolve_even_with_passing_lines(code):
    log = _log("PASSED t.py::f\nPASSED t.py::p") + f">>>>> {code}\n"
    assert grader.grade(log, _CONFIG) == {"instance_id": "x", "tests_ran": False, "resolved": False}


@pytest.mark.parametrize("body", ["ERROR t.py::f\nPASSED t.py::p", "PASSED t.py::f\nERROR t.py::p"])
def test_an_error_status_counts_as_a_failure(body):
    assert grader.grade(_log(body), _CONFIG)["resolved"] is False


def test_django_fail_and_error_headers_are_read():
    # Upstream keys a "FAIL:"/"ERROR:" header by the word after it, as here.
    found = grader.parse_django("FAIL: test_y (m.T)\nERROR: test_z (m.T)\n")
    assert found == {"test_y": "FAILED", "test_z": "ERROR"}


def test_the_scripts_trust_the_image_checkout_and_fall_back_through_the_apply_commands(tmp_path):
    task = stage(_row(PYTEST_ID), tmp_path)
    trust = "git config --global --add safe.directory '*'"
    eval_sh = (task.tests_dir / "eval.sh").read_text()
    assert eval_sh.index(trust) < eval_sh.index("git status")
    solve = (task.solution_dir / "solve.sh").read_text()
    assert solve.index(trust) < solve.index("git apply")
    assert ("git apply --verbose /solution/patch.diff || git apply --verbose --reject /solution/patch.diff"
            " || patch --batch --fuzz=5 -p1 -i /solution/patch.diff || ") in solve


def test_django_and_sympy_logs_parse_to_statuses():
    django = grader.parse_django(
        "test_a (m.T) ... ok\ntest_b (m.T) ... FAIL\ntest_c (m.T) ... skipped 'x'\n"
        "test_d (m.T) ... Internal noise\nok\n")
    assert django == {"test_a (m.T)": "PASSED", "test_b (m.T)": "FAILED",
                      "test_c (m.T)": "SKIPPED", "test_d (m.T)": "PASSED"}
    assert grader.parse_sympy("test_one ok\ntest_two F\ntest_three E\n") == {
        "test_one": "PASSED", "test_two": "FAILED", "test_three": "ERROR"}


def test_pytest_options_shortens_a_path_parameter():
    found = grader.parse_pytest_options("PASSED t.py::f[/tmp/a/b.txt]\nPASSED t.py::g[x-1]\n")
    assert found == {"t.py::f[/b.txt]": "PASSED", "t.py::g[x-1]": "PASSED"}


def test_the_grader_script_writes_reward_and_report(tmp_path, capsys):
    (tmp_path / "config.json").write_text(json.dumps(_CONFIG))
    (tmp_path / "test.log").write_text(_log("PASSED t.py::f\nFAILED t.py::p"))
    out = tmp_path / "verifier"
    assert grader.main(["grade.py", str(tmp_path / "config.json"), str(tmp_path / "test.log"), str(out)]) == 0
    assert (out / "reward.txt").read_text() == "0\n"
    assert json.loads((out / "report.json").read_text())["PASS_TO_PASS"]["failure"] == ["t.py::p"]
    assert "PASS_TO_PASS failed: t.py::p" in capsys.readouterr().out


# ── the command line ─────────────────────────────────────────────────────────


def _run(argv: list[str]) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


def test_tasks_run_stages_swe_bench_rows_for_a_dry_run(tmp_path):
    out = tmp_path / "out"
    _run(["tasks", "run", "--swe-bench", str(FIXTURE), "--tasks", PYTEST_ID,
          "--harnesses", "docker-oracle", "--output-dir", str(out), "--dry-run"])
    assert (out / "swe-bench-tasks" / PYTEST_ID / "tests" / "eval.sh").is_file()
    runs = json.loads((out / "task_runs.json").read_text())
    assert [r["task"] for r in runs["results"]] == [PYTEST_ID]


def test_tasks_run_names_a_missing_swe_bench_instance(tmp_path):
    with pytest.raises(SystemExit, match="--swe-bench: .*no instance x__y-1"):
        _run(["tasks", "run", "--swe-bench", str(FIXTURE), "--tasks", "x__y-1",
              "--harnesses", "docker-oracle", "--output-dir", str(tmp_path)])



def test_tasks_run_refuses_an_all_selection_with_no_runnable_row(tmp_path):
    rows = json.loads(FIXTURE.read_text(encoding="utf-8"))
    data = tmp_path / "bad.json"
    data.write_text(json.dumps([dict(rows[0], instance_id="psf__requests-863")]), encoding="utf-8")
    out = tmp_path / "out"
    with pytest.raises(SystemExit, match="no runnable rows"):
        _run(["tasks", "run", "--swe-bench", str(data), "--all",
              "--harnesses", "docker-oracle", "--output-dir", str(out), "--dry-run"])
    assert not (out / "task_runs.json").exists()


# ── one real instance image (opt-in) ─────────────────────────────────────────

_REAL = Path(os.environ.get("DOKIMI_SWEBENCH_ROWS", ""))


@pytest.mark.skipif(os.environ.get("DOKIMI_SWEBENCH_DOCKER") != "1" or shutil.which("docker") is None
                    or not _REAL.is_file(),
                    reason="pulls a ~4 GB SWE-bench image: set DOKIMI_SWEBENCH_DOCKER=1 and "
                           "DOKIMI_SWEBENCH_ROWS=<SWE-bench Lite .jsonl holding pytest-dev__pytest-5227>")
@pytest.mark.allow_hosts(["127.0.0.1"])
def test_gold_patch_resolves_and_empty_patch_does_not_in_docker(tmp_path):
    row = next(r for r in read_instances(_REAL) if r["instance_id"] == PYTEST_ID)
    harness = create_harness("docker-oracle")
    gold = harness.run(stage(row, tmp_path / "gold"), output_dir=tmp_path / "run-gold")
    empty = harness.run(stage(row, tmp_path / "empty", patch=""), output_dir=tmp_path / "run-empty")
    assert (gold.reward, empty.reward) == (1.0, 0.0)


def test_tasks_inspect_names_docker_as_the_consumer_of_a_supported_dataset(tmp_path):
    from dokimi.task_formats import inspect_path

    [report] = inspect_path(FIXTURE)
    assert (report.format, report.consumers) == ("swe-bench", ["docker-*"])
    other = tmp_path / "other.jsonl"
    other.write_text(json.dumps(dict(_row(PYTEST_ID), repo="someone/else")) + "\n")
    [report] = inspect_path(other)
    assert report.consumers == [] and report.details["repos_without_test_spec"] == ["someone/else"]
