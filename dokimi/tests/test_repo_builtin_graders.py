# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Built-in review graders ``dokimi:review_seeds`` and ``dokimi:review_replay`` (contract §4.5). No docker, no LLM."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

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


def test_a_fence_nested_too_deep_is_unreadable_not_a_grader_crash(tmp_path):
    seeds, replay = _task_dir(tmp_path, "seeds_mul"), _task_dir(tmp_path, "replay_mul")
    deep = "```json\n" + "[" * 100000 + "]" * 100000 + "\n```"
    assert review.grade_seeds({}, seeds, deep) == {"pass": False, "parse_error": True, "recall": 0.0}
    assert review.grade_replay({}, replay, deep)["parse_error"] is True
    # an earlier readable fence is still the answer
    assert review.grade_seeds({}, seeds, review.oracle_seeds({}, seeds) + deep)["recall"] == 1.0
    assert review.grade_replay({}, replay, review.oracle_replay({}, replay) + deep)["recall"] == 1.0


def test_fence_nesting_cap_is_exactly_max_depth():
    def nested(n):
        return "```json\n" + "[" * n + "]" * n + "\n```"

    cap = review.MAX_DEPTH
    assert review._too_deep("[" * cap + "]" * cap) is False
    assert review._too_deep("[" * (cap + 1) + "]" * (cap + 1)) is True
    assert review.last_json(nested(cap)) is not None  # just under the cap parses
    assert review.last_json(nested(cap + 1)) is None  # just over is unreadable
    # brackets inside strings do not count toward the depth
    assert review._too_deep('["' + "[" * (cap + 5) + '"]') is False
    # an over-deep last fence falls back to the earlier readable one
    assert review.last_json("```json\n[7]\n```" + nested(cap + 1)) == [7]


def test_the_nesting_cap_counts_open_brackets_not_brackets_seen(tmp_path):
    d = _task_dir(tmp_path, "seeds_mul")
    cap = review.MAX_DEPTH
    many = ORACLE + [_f(90 + i, message=f"note {i}") for i in range(2 * cap)]  # more objects than the cap
    assert review.grade_seeds({}, d, fence(many))["recall"] == 1.0
    quoted = [{**f, "message": 'quotes "' + "[" * (2 * cap) + '"'} for f in ORACLE]  # \" stays in the string
    assert review.grade_seeds({}, d, fence(quoted))["recall"] == 1.0


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


@pytest.mark.parametrize(("line", "read"), [("6", 6), (" 7 ", 7), (5.9, 5), (0, -99), (None, -99), ("6.0", -99)])
def test_a_finding_line_is_read_as_the_contract_says(line, read):
    assert review.finding_fields({"file": "a", "line": line})[2] == read


@pytest.mark.parametrize("line", ["Infinity", "-Infinity", "1e999"])
def test_an_infinite_line_is_unreadable_not_a_grader_crash(tmp_path, line):
    seeds, replay = _task_dir(tmp_path, "seeds_mul"), _task_dir(tmp_path, "replay_mul")
    text = '```json\n[{"file": "src/calc.py", "line": ' + line + ', "message": "x"}]\n```'
    assert review.grade_seeds({}, seeds, text)["unmatched"] == 1
    assert review.grade_replay({}, replay, text)["threads_hit"] == []


@pytest.mark.outcome("cjm-builtin-review-graders")
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


def test_replay_matches_record_each_credited_pair_nearest_first(tmp_path):
    d = _task_dir(tmp_path, "replay_mul")
    # object 1 (line 4) is near both threads: t_doc at 0 takes it, so t_op goes to object 2 at 1, not object 0 at 4
    findings = ["not an object", _f(9, message="far"), _f(4, severity="major", message="near both"),
                _f("6", file="/eval/run/workspace/src/calc.py", message="m" * 300)]
    g = review.grade_replay({}, d, fence(findings))
    assert g["threads_hit"] == ["t_doc", "t_op"] and g["recall"] == 1.0 and g["precision_proxy"] == 1.0
    assert g["matches"] == [
        {"thread": "t_doc", "thread_file": "src/calc.py", "thread_line": 4, "comment": "docstring?",
         "finding": 1, "file": "src/calc.py", "line": 4, "severity": "major", "message": "near both", "distance": 0},
        {"thread": "t_op", "thread_file": "src/calc.py", "thread_line": 5,
         "comment": "mul adds instead of multiplying\nsecond line", "finding": 2, "file": "src/calc.py", "line": 6,
         "severity": "minor", "message": "m" * 200, "distance": 1}]
    # the entry script keeps them under details, so grade.json alone is enough to check a hit
    doc = json.loads((_script(tmp_path, "grade", "review_replay", "replay_mul", fence(findings)) / "grade.json")
                     .read_text())
    assert doc["details"]["matches"] == g["matches"] and "matches" not in doc["metrics"] and check_grade(doc) == []
    assert review.grade_replay({}, d, fence([_f(30)]))["matches"] == []
    # t_op is credited first (distance 0), but matches follow threads_hit
    later = review.grade_replay({}, d, fence([_f(5), _f(3)]))["matches"]
    assert [(m["thread"], m["finding"], m["distance"]) for m in later] == [("t_doc", 1, 1), ("t_op", 0, 0)]


@pytest.mark.outcome("cjm-builtin-review-graders")
def test_matching_rules_as_the_contract_states_them(tmp_path):
    """§4.5: same file only, kw case-insensitive, replay within ``window`` (default 5) inclusive."""
    seeds, replay = _task_dir(tmp_path, "seeds_mul"), _task_dir(tmp_path, "replay_mul")
    other = "src/other.py"
    assert review.grade_seeds({}, seeds, fence([_f(40, file=other, message="mul returns a + b")]))["seeds_hit"] == []
    assert review.grade_seeds({}, seeds, fence([_f(40, message="MUL RETURNS A + B")]))["seeds_hit"] == ["s_op"]
    assert review.grade_seeds({}, seeds, fence([_f(1, file=other, message="add() is unused")]))["decoys_flagged"] == []
    assert review.grade_seeds({}, seeds, fence([_f(30, message="ADD() IS UNUSED")]))["decoys_flagged"] == ["d_add"]
    assert review.grade_replay({}, replay, fence([_f(5, file=other)]))["threads_hit"] == []
    assert review.grade_replay({"window": 2}, replay, fence([_f(7)]))["threads_hit"] == ["t_op"]
    assert review.grade_replay({}, replay, fence([_f(10)]))["threads_hit"] == ["t_op"]
    assert review.grade_replay({}, replay, fence([_f(11)]))["threads_hit"] == []
    empty = {"id": "e", "class": "c", "file": "src/calc.py", "lines": [], "kw": "zzz"}
    write(tmp_path / "e", "manifest.json", json.dumps({"seeds": [empty]}))
    assert review.grade_seeds({}, tmp_path / "e", fence([_f(5)]))["seeds_hit"] == []
    write(tmp_path / "c", "gold.json", json.dumps([{"id": "c", "file": "a.py", "line": 1, "comment": "x" * 300}]))
    assert review.last_json(review.oracle_replay({}, tmp_path / "c"))[0]["message"] == "x" * 200


@pytest.mark.outcome("cjm-builtin-review-graders")
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


@pytest.mark.outcome("cjm-builtin-review-graders")
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


@pytest.mark.outcome("cjm-builtin-review-graders")
def test_repo_validate_finds_a_builtin_task_without_its_data_file_or_a_wrong_name(tmp_path, capsys):
    root = _repo(tmp_path, {"nodata": {**REVIEW, "grader": "dokimi:review_seeds"},  # no manifest.json
                            "typo": {**REVIEW, "grader": "dokimi:review_seed"}})
    args = build_parser().parse_args(["repo", "validate", f"{root}@HEAD", "--manifest", ".agent-eval/repo-eval.yaml",
                                      "--cache-dir", str(tmp_path / "caches"), "--json"])
    with pytest.raises(SystemExit):
        args.func(args)
    errors = json.loads(capsys.readouterr().out)["errors"]
    assert any("manifest file does not exist: manifest.json" in e and "nodata" in e for e in errors), errors
    assert any("unknown built-in grader 'dokimi:review_seed'" in e for e in errors), errors


@pytest.mark.outcome("cjm-builtin-review-graders")
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

    # the container runs with --init, so tini's message is the only one a missing python3 produces
    (tmp_path / "grade.err").write_text("python3: not found")
    assert not _python3_not_found_in_stderr(tmp_path, "grade")

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


# ------------------------------------------------------------------ review of 243c6a4


def test_mixed_int_and_str_ids_do_not_crash_the_graders(tmp_path):
    seeds = {"seeds": [{"id": 1, "class": "c", "file": "src/calc.py", "lines": [5], "kw": "mul"},
                       {"id": "b", "class": "c", "file": "src/calc.py", "lines": [5], "kw": "mul"}]}
    gold = [{"id": 1, "file": "src/calc.py", "line": 5, "kind": "k", "comment": "c"},
            {"id": "b", "file": "src/calc.py", "line": 5, "kind": "k", "comment": "c"}]
    write(tmp_path / "t", "manifest.json", json.dumps(seeds))
    write(tmp_path / "t", "gold.json", json.dumps(gold))
    assert review.grade_seeds({}, tmp_path / "t", fence([_f(5, message="mul")]))["seeds_hit"] == [1, "b"]
    # no keyword: the line fallback credits the nearest seed, and both are one line away
    assert review.grade_seeds({}, tmp_path / "t", fence([_f(6)]))["seeds_hit"] == [1]
    assert review.grade_replay({}, tmp_path / "t", fence([_f(5)]))["threads_hit"] == [1]


def test_the_replay_oracle_survives_an_empty_comment_and_a_missing_kind(tmp_path):
    write(tmp_path / "t", "gold.json", json.dumps([{"id": "e", "file": "src/calc.py", "line": 5, "comment": ""},
                                                   {"id": "k", "file": "src/calc.py", "line": 7, "kind": "docs"}]))
    ans = review.last_json(review.oracle_replay({}, tmp_path / "t"))
    assert ans[0] == {"file": "src/calc.py", "line": 5, "severity": "minor", "rule": "", "message": ""}
    assert ans[1]["rule"] == "docs"


def test_the_replay_oracle_reaches_full_recall_on_a_suggestion_comment(tmp_path):
    write(tmp_path / "t", "gold.json", json.dumps([{"id": "s", "file": "src/calc.py", "line": 5, "kind": "k",
                                                    "comment": "```suggestion\n    return a * b\n```"}]))
    oracle = review.oracle_replay({}, tmp_path / "t")
    assert review.grade_replay({}, tmp_path / "t", oracle)["recall"] == 1.0
    assert review.last_json(oracle)[0]["message"] == "```suggestion"


def test_the_seeds_oracle_reaches_full_recall_when_oracle_json_quotes_a_fence(tmp_path):
    d = _task_dir(tmp_path, "seeds_mul")
    quoted = [{**f, "message": f["message"] + " ```suggestion x``` naming"} for f in ORACLE]
    write(d, "oracle.json", json.dumps(quoted))
    oracle = review.oracle_seeds({}, d)
    assert review.grade_seeds({}, d, oracle)["recall"] == 1.0
    assert review.last_json(oracle) == quoted
    write(d, "oracle.json", json.dumps(ORACLE))  # no fence in it: the text is the file's, unchanged
    assert review.oracle_seeds({}, d) == "```json\n" + json.dumps(ORACLE) + "\n```"


def test_wildcard_seeds_and_absolute_paths_match_as_pinned(tmp_path):
    seeds = {"seeds": [{"id": "w", "class": "c", "file": "*", "lines": [5], "kw": "zzz"},
                       {"id": "s", "class": "c", "file": "src/calc.py", "lines": [9], "kw": "mul"}]}
    write(tmp_path / "t", "manifest.json", json.dumps(seeds))
    d = tmp_path / "t"
    # a "*" seed takes a keyword hit only: a line alone does not credit it
    assert review.grade_seeds({}, d, fence([_f(5, message="nothing")]))["seeds_hit"] == []
    assert review.grade_seeds({}, d, fence([_f(5, file="other.py", message="zzz")]))["seeds_hit"] == ["w"]
    # a leading slash is not part of the repository-relative path
    assert review.grade_seeds({}, d, fence([_f(9, file="/src/calc.py", message="x")]))["seeds_hit"] == ["s"]


def test_a_repository_grader_exiting_127_is_not_the_python3_failure(tmp_path):
    """Only a built-in grader's 127 is reported as a missing python3; a repository's grade stays a plain exit."""
    ctx = EvalContext(manifest=parse_manifest(MANIFEST), git_dir=tmp_path, content_dir=tmp_path / "content",
                      image="img", runner=NoPython(), caches={"build-cache": tmp_path})
    (tmp_path / "content").mkdir()
    rd = tmp_path / "rd"
    rd.mkdir()
    task = Task(id="t", raw={"grader": "repo_review"}, dir=tmp_path / "content" / "t")
    result = run_entry(ctx, "grade", task, rd, "none")
    assert result.returncode == 127 and ctx.runner.specs[0].argv[0] == "python3"


def test_int_ids_break_ties_in_numeric_order_as_onedal_does(tmp_path):
    seeds = {"seeds": [{"id": 10, "class": "c", "file": "src/calc.py", "lines": [4], "kw": "zzz"},
                       {"id": 2, "class": "c", "file": "src/calc.py", "lines": [6], "kw": "zzz"}]}
    gold = [{"id": 10, "file": "src/calc.py", "line": 4, "kind": "k", "comment": "c"},
            {"id": 2, "file": "src/calc.py", "line": 6, "kind": "k", "comment": "c"}]
    write(tmp_path / "t", "manifest.json", json.dumps(seeds))
    write(tmp_path / "t", "gold.json", json.dumps(gold))
    # one finding at line 5 is one line from both seeds and both threads: the lower int id wins
    assert review.grade_seeds({}, tmp_path / "t", fence([_f(5)]))["seeds_hit"] == [2]
    assert review.grade_replay({}, tmp_path / "t", fence([_f(5)]))["threads_hit"] == [2]
    # ids that all match by keyword are listed in numeric order, not as strings
    assert review.grade_seeds({}, tmp_path / "t", fence([_f(4, message="zzz"), _f(6, message="zzz")]))[
        "seeds_hit"] == [2, 10]


def test_a_builtin_grader_exiting_127_for_another_reason_is_a_plain_exit(tmp_path):
    """Only a missing python3 (tini's message on stderr) is the image failure; any other 127 is the grader's."""
    class OtherExit127(FakeRunner):
        def run(self, spec, *, stdout, stderr, timeout_s):
            if spec.argv[0] == "python3":
                self.specs.append(spec)
                stdout.write_text("")
                stderr.write_text("Grader exit 127 for other reason\n")
                return ContainerResult(returncode=127, timed_out=False, wall_s=0.0)
            return super().run(spec, stdout=stdout, stderr=stderr, timeout_s=timeout_s)

    ctx = EvalContext(manifest=parse_manifest(MANIFEST), git_dir=tmp_path, content_dir=tmp_path / "content",
                      image="img", runner=OtherExit127(), caches={"build-cache": tmp_path})
    (tmp_path / "content").mkdir()
    rd = tmp_path / "rd"
    rd.mkdir()
    task = Task(id="t", raw={"grader": "dokimi:review_seeds"}, dir=tmp_path / "content" / "t")
    result = run_entry(ctx, "grade", task, rd, "none")  # no ContainerError: not the python3 failure
    assert result.returncode == 127 and ctx.runner.specs[0].argv[0] == "python3"


def test_a_replay_window_that_is_not_a_positive_number_is_an_authoring_error(tmp_path, capsys):
    root = _repo(tmp_path, {"replay_mul": {**TASKS["replay_mul"], "window": "5"}})  # its gold.json is in DATA
    args = build_parser().parse_args(["repo", "validate", f"{root}@HEAD", "--manifest", ".agent-eval/repo-eval.yaml",
                                      "--cache-dir", str(tmp_path / "caches"), "--json"])
    with pytest.raises(SystemExit):
        args.func(args)
    errors = json.loads(capsys.readouterr().out)["errors"]
    assert any("replay_mul" in e and "window must be a positive number" in e for e in errors), errors


def test_a_finding_without_a_file_never_matches_an_empty_seed_path(tmp_path):
    # same_file must not treat "no file" and an empty/root seed path as the same file
    seeds = {"seeds": [{"id": "e", "class": "c", "file": "", "lines": [5], "kw": "zzz"},
                       {"id": "r", "class": "c", "file": "/", "lines": [7], "kw": "zzz"}]}
    write(tmp_path / "t", "manifest.json", json.dumps(seeds))
    d = tmp_path / "t"
    no_file = {"line": 5, "severity": "minor", "rule": "", "message": "nothing"}
    assert review.grade_seeds({}, d, fence([no_file]))["seeds_hit"] == []
    assert review.grade_seeds({}, d, fence([_f(5, file="", message="nothing")]))["seeds_hit"] == []
    assert review.grade_seeds({}, d, fence([_f(7, file="/", message="nothing")]))["seeds_hit"] == []


def test_a_replay_window_is_checked_only_for_replay_and_defaults_to_five(tmp_path, capsys):
    root = _repo(tmp_path, {"replay_nowin": {**REVIEW, "grader": "dokimi:review_replay"},
                            "replay_win3": {**REVIEW, "grader": "dokimi:review_replay", "window": 3},
                            "seeds_strwin": {**REVIEW, "grader": "dokimi:review_seeds", "window": "x"}})
    for task_id in ("replay_nowin", "replay_win3", "seeds_strwin"):
        write(root, f".agent-eval/tasks/{task_id}/gold.json" if task_id.startswith("replay")
              else f".agent-eval/tasks/{task_id}/manifest.json",
              json.dumps(GOLD if task_id.startswith("replay") else {"seeds": [], "decoys": []}))
    git(root, "add", "-A")
    git(root, "commit", "-qm", "window cases")
    args = build_parser().parse_args(["repo", "validate", f"{root}@HEAD", "--manifest", ".agent-eval/repo-eval.yaml",
                                      "--cache-dir", str(tmp_path / "caches"), "--json"])
    try:
        args.func(args)
    except SystemExit:
        pass
    out = capsys.readouterr().out
    errors = json.loads(out).get("errors", []) if out.strip() else []
    assert not any(t in e for e in errors for t in ("replay_nowin", "replay_win3", "seeds_strwin")), errors


def test_a_cell_graded_by_a_builtin_grader_needs_no_grade_entry_point(tmp_path, monkeypatch):
    # `repo validate` accepts a manifest without entry_points.grade when every grader is `dokimi:`;
    # `repo run` must then grade with the built-in one instead of failing every cell.
    root = _repo(tmp_path)
    manifest = {**MANIFEST, "entry_points": {}}
    write(root, ".agent-eval/repo-eval.yaml", yaml.safe_dump(manifest))
    git(root, "add", "-A")
    git(root, "commit", "-qm", "no grade entry point")
    exe, profile = fake_harness(tmp_path)
    runner = FakeRunner()
    monkeypatch.setattr("dokimi.repos.container.DockerRunner", lambda *a, **k: runner)
    monkeypatch.setenv("FAKE_ACTION", "fix")
    out = tmp_path / "runs"
    args = build_parser().parse_args(
        ["repo", "run", f"{root}@HEAD", "--manifest", ".agent-eval/repo-eval.yaml", "--harness", str(profile),
         "--bin", str(exe), "--model", "m-1", "--batch", "b1", "--out", str(out), "--cache-root", str(out / "caches"),
         "--agent-network", "host", "--arm", "guidance:raw", "--task", "seeds_mul"])
    args.func(args)
    rec = json.loads((out / "b1/seeds_mul__guidance-raw__m-1__r1/result.json").read_text())
    assert rec["grade"]["status"] == "graded", rec["grade"]["reasons"]
    assert any(f"{GRADERS_DIR}/main.py" in s.argv for s in runner.specs)


def test_a_repository_grader_without_a_grade_entry_point_is_refused_before_any_agent_runs(tmp_path, monkeypatch, capsys):
    # Without the check every cell would run, be paid for, and grade as grader_failure.
    root = _repo(tmp_path)
    write(root, ".agent-eval/repo-eval.yaml", yaml.safe_dump({**MANIFEST, "entry_points": {}}))
    git(root, "add", "-A")
    git(root, "commit", "-qm", "no grade entry point")
    exe, profile = fake_harness(tmp_path)
    runner = FakeRunner()
    monkeypatch.setattr("dokimi.repos.container.DockerRunner", lambda *a, **k: runner)
    out = tmp_path / "runs"
    args = build_parser().parse_args(
        ["repo", "run", f"{root}@HEAD", "--manifest", ".agent-eval/repo-eval.yaml", "--harness", str(profile),
         "--bin", str(exe), "--model", "m-1", "--batch", "b1", "--out", str(out), "--cache-root", str(out / "caches"),
         "--agent-network", "host", "--arm", "guidance:raw", "--task", "fix_add"])
    with pytest.raises(SystemExit):
        args.func(args)
    assert runner.specs == [] and not (out / "b1/_eval/batch.json").exists()
    assert "entry_points/grade: required" in capsys.readouterr().err
