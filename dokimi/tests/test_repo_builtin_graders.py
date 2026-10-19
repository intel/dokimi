# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Built-in review graders ``dokimi:review_seeds`` and ``dokimi:review_replay`` (contract §4.5). No docker, no LLM."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from dokimi.cli import build_parser
from dokimi.repos.container import CONTENT_DIR, GRADERS_DIR, SOURCE_DIR, ContainerError, ContainerResult
from dokimi.repos.graders import HOST_DIR, builtin_name, main, review
from dokimi.repos.grading import EvalContext, check_grade, run_entry
from dokimi.repos.manifest import parse_manifest
from dokimi.repos.workspace import Task
from repo_eval_fixture import MANIFEST, FakeRunner, fake_harness, git, make_repo, write

SEEDS = {"seeds": [{"id": "s_op", "class": "correctness", "file": "src/calc.py", "lines": [5], "kw": "mul.*(add|plus|\\+)"},
                   {"id": "s_name", "class": "convention", "file": "src/calc.py", "lines": [4], "kw": "naming"}],
         "decoys": [{"id": "d_add", "file": "src/calc.py", "lines": [1], "kw": "add\\(\\) is unused"}]}
GOLD = [{"id": "t_op", "file": "src/calc.py", "line": 5, "kind": "correctness",
         "comment": "mul adds instead of multiplying\nsecond line", "addressed": True},
        {"id": "t_doc", "file": "src/calc.py", "line": 4, "kind": "docs", "comment": "docstring?", "addressed": False}]
ORACLE = [{"file": "src/calc.py", "line": 5, "severity": "major", "rule": "wrong-op", "message": "mul adds"},
          {"file": "src/calc.py", "line": 4, "severity": "minor", "rule": "naming", "message": "name it multiply"}]
REVIEW = {"family": "review", "review_patch": "change.patch", "prompt": "Review HEAD."}
TASKS = {
    "seeds_mul": {**REVIEW, "grader": "dokimi:review_seeds", "score": {"metric": "recall", "max": 1.0}},
    "replay_mul": {**REVIEW, "grader": "dokimi:review_replay", "score": {"metric": "recall", "max": 1.0}},
    "clean_mul": {**REVIEW, "grader": "dokimi:review_seeds"},  # clean-diff control: gate ["pass"]
    "addressed_mul": {**REVIEW, "grader": "dokimi:review_replay", "score": {"metric": "recall_addressed", "max": 1.0}},
}
DATA = {"seeds_mul": {"manifest.json": SEEDS, "oracle.json": ORACLE}, "replay_mul": {"gold.json": GOLD},
        "clean_mul": {"manifest.json": {"seeds": [], "decoys": []}, "oracle.json": []},
        "addressed_mul": {"gold.json": GOLD}}


def fence(findings) -> str:
    return "Review done.\n```json\n" + json.dumps(findings) + "\n```\n"


def _task_dir(tmp: Path, task_id: str) -> Path:
    for name, doc in DATA[task_id].items():
        write(tmp / task_id, name, json.dumps(doc))
    return tmp / task_id


def _f(line, file="src/calc.py", severity="minor", rule="", message=""):
    return {"file": file, "line": line, "severity": severity, "rule": rule, "message": message}


# ------------------------------------------------------------------ semantics


def test_last_json_takes_the_last_parseable_fence():
    assert review.last_json("```json\n[1]\n``` and ```json\n[2]\n``` then ```json\n{broken\n```") == [2]
    assert review.last_json("no fence") is None and review.last_json(None) is None


def test_norm_path_cuts_at_workspace_and_repo_and_drops_leading_dots_and_slashes():
    # workspace path (agent's working directory in container)
    assert review.norm_path("/eval/run/workspace/src/calc.py") == "src/calc.py"
    # /repo/ path (legacy, for backwards compatibility)
    assert review.norm_path("/runs/r1/repo/src/calc.py") == "src/calc.py"
    assert review.norm_path(" ./././src\\calc.py ") == "src/calc.py"
    assert review.norm_path(None) == ""


def test_seeds_keyword_line_fallback_decoys_and_recall_by_class(tmp_path):
    d = _task_dir(tmp_path, "seeds_mul")
    findings = [
        _f(40, message="mul returns a + b"),  # keyword hit far from the line
        _f(1, message="add() is unused", severity="Major"),  # a decoy, by keyword
        _f(1, message="something"),  # a decoy, by line
        _f(30, file="other.py", severity="blocker"),  # unmatched
        "not an object",
    ]
    g = review.grade_seeds({}, d, fence(findings))
    assert g == {"pass": None, "n_findings": 4, "n_severe": 2, "seeds_hit": ["s_op"], "decoys_flagged": ["d_add"],
                 "n_decoys": 1, "unmatched": 1, "recall": 0.5,
                 "recall_by_class": {"correctness": "1/1", "convention": "0/1"}}


def test_seeds_with_workspace_paths(tmp_path):
    """Regression: findings with workspace paths (/eval/run/workspace) must match seeds correctly."""
    d = _task_dir(tmp_path, "seeds_mul")
    findings = [
        _f(5, file="/eval/run/workspace/src/calc.py", message="mul returns a + b"),  # keyword hit with workspace path
        _f(4, file="/eval/run/workspace/src/calc.py", message="naming issue"),  # line fallback with workspace path
    ]
    g = review.grade_seeds({}, d, fence(findings))
    assert g["seeds_hit"] == ["s_name", "s_op"], f"workspace paths should match seeds; got {g}"
    assert g["recall"] == 1.0


def test_replay_with_workspace_paths(tmp_path):
    """Regression: findings with workspace paths (/eval/run/workspace) must match gold threads correctly."""
    d = _task_dir(tmp_path, "replay_mul")
    findings = [
        _f(5, file="/eval/run/workspace/src/calc.py"),
        _f(4, file="/eval/run/workspace/src/calc.py"),
    ]
    g = review.grade_replay({}, d, fence(findings))
    assert g["threads_hit"] == ["t_doc", "t_op"], f"workspace paths should match threads; got {g}"
    assert g["recall"] == 1.0


def test_a_nested_repo_directory_matches_from_any_path_form(tmp_path):
    seeds = {"seeds": [{"id": "s", "class": "c", "file": "src/repo/calc.py", "lines": [5], "kw": "nomatch"}]}
    write(tmp_path / "t", "manifest.json", json.dumps(seeds))
    gold = [{"id": "g", "file": "src/repo/calc.py", "line": 5, "kind": "k", "comment": "c"}]
    write(tmp_path / "t", "gold.json", json.dumps(gold))
    for path in ("src/repo/calc.py", "/eval/run/workspace/src/repo/calc.py", "/runs/r1/repo/src/repo/calc.py"):
        assert review.grade_seeds({}, tmp_path / "t", fence([_f(5, file=path)]))["recall"] == 1.0, path
        assert review.grade_replay({}, tmp_path / "t", fence([_f(5, file=path)]))["recall"] == 1.0, path


def test_line_fallback_credits_only_the_nearest_seed_within_one_line(tmp_path):
    d = _task_dir(tmp_path, "seeds_mul")
    assert review.grade_seeds({}, d, fence([_f(4)]))["seeds_hit"] == ["s_name"]  # 0 from s_name, 1 from s_op
    assert review.grade_seeds({}, d, fence([_f("6")]))["seeds_hit"] == ["s_op"]  # a string line still counts
    assert review.grade_seeds({}, d, fence([_f(7)]))["seeds_hit"] == []
    assert review.grade_seeds({}, d, fence([_f("x")]))["unmatched"] == 1  # unreadable line: near nothing


@pytest.mark.parametrize("line", ["Infinity", "-Infinity", "1e999"])
def test_an_infinite_line_is_unreadable_not_a_grader_crash(tmp_path, line):
    seeds, replay = _task_dir(tmp_path, "seeds_mul"), _task_dir(tmp_path, "replay_mul")
    text = '```json\n[{"file": "src/calc.py", "line": ' + line + ', "message": "x"}]\n```'
    assert review.grade_seeds({}, seeds, text)["unmatched"] == 1
    assert review.grade_replay({}, replay, text)["threads_hit"] == []


def test_seeds_parse_error_and_clean_control(tmp_path):
    seeds, clean = _task_dir(tmp_path, "seeds_mul"), _task_dir(tmp_path, "clean_mul")
    unparsed = {"pass": False, "parse_error": True, "recall": 0.0}
    assert review.grade_seeds({}, seeds, "") == unparsed
    assert review.grade_seeds({}, seeds, "```json\n{\"file\": \"a\"}\n```") == unparsed
    assert review.grade_seeds({}, clean, "") == {**unparsed, "recall": None}
    assert review.grade_seeds({}, clean, fence([_f(5, severity="minor")]))["pass"] is True
    g = review.grade_seeds({}, clean, fence([_f(5, severity="blocker")]))
    assert g["pass"] is False and g["recall"] is None and g["unmatched"] == 1


def test_replay_is_one_to_one_nearest_first_within_the_window(tmp_path):
    d = _task_dir(tmp_path, "replay_mul")
    # finding 0 is nearest to both threads' lines: it takes t_op (distance 0), finding 1 takes t_doc
    g = review.grade_replay({}, d, fence([_f(5), _f(5), _f(20)]))
    assert g["threads_hit"] == ["t_doc", "t_op"] and g["recall"] == g["recall_all"] == 1.0
    assert g["precision_proxy"] == 0.667 and g["recall_addressed"] == 1.0 and g["pass"] is None
    assert g["recall_by_kind"] == {"correctness": "1/1", "docs": "1/1"} and g["decoys_flagged"] == []
    one = review.grade_replay({}, d, fence([_f(5)]))
    assert one["threads_hit"] == ["t_op"] and one["recall"] == 0.5 and one["recall_addressed"] == 1.0
    assert review.grade_replay({"window": 2}, d, fence([_f(8)]))["threads_hit"] == []
    assert review.grade_replay({}, d, fence([_f(3)]))["threads_hit"] == ["t_doc"]  # 1 from t_doc, 2 from t_op
    assert review.grade_replay({}, d, fence([]))["precision_proxy"] is None
    assert review.grade_replay({}, d, "none") == {"pass": False, "parse_error": True, "recall": 0.0, "recall_all": 0.0,
                                                  "recall_addressed": 0.0}


def test_oracles_reach_full_recall(tmp_path):
    for task_id, grade, oracle in (("seeds_mul", review.grade_seeds, review.oracle_seeds),
                                   ("replay_mul", review.grade_replay, review.oracle_replay)):
        d = _task_dir(tmp_path, task_id)
        assert grade({}, d, oracle({}, d))["recall"] == 1.0
    replay = review.last_json(review.oracle_replay({}, _task_dir(tmp_path, "replay_mul")))
    assert replay[0] == {"file": "src/calc.py", "line": 5, "severity": "minor", "rule": "correctness",
                         "message": "mul adds instead of multiplying"}


# ------------------------------------------------------------------ the entry script


def _script(tmp_path, kind, name, task_id, answer="", task=None):
    rd = tmp_path / "run"
    rd.mkdir(exist_ok=True)
    (rd / "task.json").write_text(json.dumps(task or {"id": task_id, "grader": f"dokimi:{name}"}))
    (rd / "answer.txt").write_text(answer)
    # as the grading container runs it: -I, so the script's own directory is not on sys.path
    proc = subprocess.run([sys.executable, "-I", "-B", str(HOST_DIR / "main.py"), kind, name,
                           str(_task_dir(tmp_path, task_id)), str(rd)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return rd


def test_entry_script_writes_repo_grade_v1_with_scalars_under_metrics(tmp_path):
    rd = _script(tmp_path, "grade", "review_seeds", "seeds_mul", fence(ORACLE))
    doc = json.loads((rd / "grade.json").read_text())
    assert check_grade(doc) == []
    assert doc["task"] == "seeds_mul" and doc["grader"] == "dokimi:review_seeds" and doc["pass"] is None
    assert doc["metrics"] == {"n_findings": 2, "n_severe": 1, "n_decoys": 1, "unmatched": 0, "recall": 1.0}
    assert doc["details"] == {"seeds_hit": ["s_name", "s_op"], "decoys_flagged": [], "grader_version": review.VERSION,
                              "recall_by_class": {"correctness": "1/1", "convention": "1/1"}}


def test_entry_script_oracle_writes_the_answer(tmp_path):
    rd = _script(tmp_path, "oracle", "review_replay", "replay_mul")
    assert review.last_json((rd / "answer.txt").read_text())[1]["line"] == 4


def test_a_grader_that_raises_is_not_graded_and_an_unreached_gate_metric_is_null(tmp_path):
    task = {"id": "seeds_mul", "grader": "dokimi:review_seeds", "manifest": "missing.json",
            "gate": ["recall_ok", {"metric": "recall", "min": 0.5}, "pass"]}
    doc = json.loads((_script(tmp_path, "grade", "review_seeds", "seeds_mul", task=task) / "grade.json").read_text())
    assert doc["pass"] is None and doc["errors"][0].startswith("FileNotFoundError") and doc["metrics"] == {}
    task["manifest"] = "manifest.json"
    doc = json.loads((_script(tmp_path, "grade", "review_seeds", "seeds_mul", "", task) / "grade.json").read_text())
    assert doc["metrics"] == {"parse_error": True, "recall": 0.0, "recall_ok": None} and doc["pass"] is False


def test_oracle_then_grade_in_process_and_a_missing_answer_is_empty(tmp_path):
    for task_id, name in (("seeds_mul", "review_seeds"), ("replay_mul", "review_replay")):
        rd = tmp_path / task_id / "run"
        rd.mkdir(parents=True)
        (rd / "task.json").write_text(json.dumps({"id": task_id, "grader": f"dokimi:{name}"}))
        d = _task_dir(tmp_path / "data", task_id)
        assert main.grade(name, d, rd)["metrics"]["parse_error"] is True  # no answer.txt
        assert main.oracle(name, d, rd) == (rd / "answer.txt").read_text()
        doc = main.grade(name, d, rd)
        assert doc["metrics"]["recall"] == 1.0 and json.loads((rd / "grade.json").read_text()) == doc


def test_entry_script_usage_and_split_result():
    assert main.main(["grade", "nope", "x"]) == 2
    assert main.split_result({"pass": True, "a": 1, "b": None, "c": [1], "d": "s", "e": {"x": 1}, "f": False}) == (
        {"a": 1, "b": None, "f": False}, {"c": [1], "d": "s", "e": {"x": 1}})


def test_builtin_name():
    assert builtin_name("dokimi:review_seeds") == "review_seeds" and builtin_name("dokimi:nope") == "nope"
    assert builtin_name("review") is None and builtin_name(None) is None


# ------------------------------------------------------------------ dispatch in run_entry


def _repo(tmp_path, extra=None):
    tasks = {**TASKS, **(extra or {})}
    root, _ = make_repo(tmp_path, tasks)
    for task_id, files in DATA.items():
        for name, doc in files.items():
            write(root, f".agent-eval/tasks/{task_id}/{name}", json.dumps(doc))
    git(root, "add", "-A")
    git(root, "commit", "-qm", "built-in review tasks")
    return root


def _check(root, out, runner, monkeypatch, *tasks):
    monkeypatch.setattr("dokimi.repos.container.DockerRunner", lambda *a, **k: runner)
    argv = ["repo", "tasks", "check", f"{root}@HEAD", "--manifest", ".agent-eval/repo-eval.yaml",
            "--out", str(out), "--cache-root", str(out / "caches")]
    for t in tasks:
        argv += ["--task", t]
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except SystemExit:
        pass
    return {t["task"]: t for t in json.loads((out / "check/check.json").read_text())["tasks"]}


def test_tasks_check_runs_builtin_graders_not_the_repository_grade(tmp_path, monkeypatch):
    runner = FakeRunner()
    res = _check(_repo(tmp_path), tmp_path / "chk", runner, monkeypatch, *TASKS, "fix_add")
    assert {k: v["verdict"] for k, v in res.items()} == dict.fromkeys([*TASKS, "fix_add"], "ok"), res
    for task_id in ("seeds_mul", "replay_mul", "addressed_mul"):
        null, oracle = res[task_id]["null"], res[task_id]["oracle"]
        assert oracle["score"] == 1.0 and oracle["score_only"]
        # an empty answer has no findings: a failed run with recall 0 (parse_error), not a grader failure
        assert null["status"] == "graded" and null["pass"] is False and null["success"] is False
        assert null["score"] == 0.0
    assert res["clean_mul"]["null"]["success"] is False and res["clean_mul"]["oracle"]["success"] is True
    builtin = [s for s in runner.specs if f"{GRADERS_DIR}/main.py" in s.argv]
    repo = [s for s in runner.specs if any(a.endswith("/grade.py") for a in s.argv)]
    assert len(builtin) == 3 * len(TASKS) and len(repo) == 3  # each: null grade, oracle, oracle grade
    for s in builtin:
        assert s.argv[:3] == ["python3", "-I", "-B"]
        kind, name, task_dir = s.argv[4:7]
        assert kind in ("grade", "oracle") and name in ("review_seeds", "review_replay")
        assert task_dir.startswith(f"{CONTENT_DIR}/tasks/")
        mounts = {m.container: m for m in s.mounts}
        assert mounts[GRADERS_DIR].read_only and mounts[GRADERS_DIR].host == HOST_DIR
        assert mounts[CONTENT_DIR].read_only and SOURCE_DIR not in mounts  # the repository's oracle names it
    assert all(GRADERS_DIR not in {m.container for m in s.mounts} for s in repo)


def test_an_unknown_builtin_grader_is_refused_not_given_to_the_repository(tmp_path, monkeypatch):
    root = _repo(tmp_path, {"typo": {**REVIEW, "grader": "dokimi:review_seed"}})
    res = _check(root, tmp_path / "chk", FakeRunner(), monkeypatch, "typo")
    assert res["typo"]["verdict"] == "broken"
    assert "unknown built-in grader 'dokimi:review_seed'" in res["typo"]["problems"][0]


class NoPython(FakeRunner):
    """An image without python3: docker run exits 127."""

    def run(self, spec, *, stdout, stderr, timeout_s):
        if spec.argv[0] == "python3":
            self.specs.append(spec)
            stdout.write_text("")
            stderr.write_text("[FATAL tini (7)] exec python3 failed: No such file or directory\n")
            return ContainerResult(returncode=127, timed_out=False, wall_s=0.0)
        return super().run(spec, stdout=stdout, stderr=stderr, timeout_s=timeout_s)


def test_an_image_without_python3_is_a_grader_failure_with_the_reason(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    res = _check(root, tmp_path / "chk", NoPython(), monkeypatch, "clean_mul")
    assert res["clean_mul"]["verdict"] == "broken"
    assert "dokimi:review_seeds runs on the image's python3, and the image has none on PATH" in \
        res["clean_mul"]["problems"][0]

    exe, profile = fake_harness(tmp_path)
    monkeypatch.setenv("FAKE_ACTION", "fix")
    no_python_runner = NoPython()  # capture for verification after loop
    for runner, check in ((no_python_runner, "none on PATH"), (FakeRunner(), None)):
        out = tmp_path / ("runs-np" if check else "runs")
        monkeypatch.setattr("dokimi.repos.container.DockerRunner", lambda *a, r=runner, **k: r)
        args = build_parser().parse_args([
            "repo", "run", f"{root}@HEAD", "--manifest", ".agent-eval/repo-eval.yaml", "--harness", str(profile),
            "--bin", str(exe), "--model", "m-1", "--batch", "b1", "--out", str(out), "--cache-root",
            str(out / "caches"), "--agent-network", "host", "--arm", "guidance:raw", "--task", "clean_mul"])
        args.func(args)
        cell = next((out / "b1").glob("clean_mul__*/result.json")).parent
        grade = json.loads((cell / "result.json").read_text())["grade"]
        if check:
            assert grade["status"] == "grader_failure" and check in grade["reasons"][0]
        else:  # the fake agent's answer has no findings JSON: graded, and failed
            assert grade["status"] == "graded" and grade["success"] is False and grade["pass"] is False
            assert json.loads((cell / "grade.json").read_text())["metrics"]["parse_error"] is True

    # Verify that NoPython runner was called with python3 command (the 127-exit-code path)
    assert any(s.argv[0] == "python3" for s in no_python_runner.specs), \
        "Built-in grader must invoke python3 to trigger 127-exit detection"


def test_builtin_grader_stderr_validation_for_127_exit(tmp_path):
    """Only treat 127 exit as python3-not-found if stderr has expected signature."""
    from dokimi.repos.grading import _python3_not_found_in_stderr

    # Write stderr with python3 exec failure signature
    (tmp_path / "grade.err").write_text("exec python3 failed: No such file or directory")
    assert _python3_not_found_in_stderr(tmp_path, "grade")

    # Write stderr with alternate signature
    (tmp_path / "grade.err").write_text("python3: not found")
    assert _python3_not_found_in_stderr(tmp_path, "grade")

    # Write stderr without signature (127 exit from grader itself)
    (tmp_path / "grade.err").write_text("Grader exit 127 for other reason\n")
    assert not _python3_not_found_in_stderr(tmp_path, "grade")

    # No stderr file (missing error logs)
    (tmp_path / "grade.err").unlink()
    assert not _python3_not_found_in_stderr(tmp_path, "grade")


def test_run_entry_needs_the_task_inside_the_exported_eval_dir(tmp_path):
    ctx = EvalContext(manifest=parse_manifest(MANIFEST), git_dir=tmp_path, content_dir=tmp_path / "content",
                      image="img", runner=FakeRunner())
    (tmp_path / "content").mkdir()
    task = Task(id="t", raw={"grader": "dokimi:review_seeds"}, dir=tmp_path / "elsewhere" / "t")
    with pytest.raises(ContainerError, match="not in the exported eval dir"):
        run_entry(ctx, "grade", task, tmp_path / "rd", "none")
