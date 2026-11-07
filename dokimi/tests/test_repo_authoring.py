# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``repo tasks authoring`` and ``dokimi:hidden_tests``: authoring tasks from merged pull requests (contract §4.8).

The pull request files have the shape ``gh api graphql --paginate --slurp`` writes, with the SHAs of
a toy history built per test. ``eval check`` runs on :class:`FakeRunner`, which runs each container's
command on this host: the manifest's ``test`` entry point is this interpreter's pytest. No network, no docker.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import yaml

from dokimi.cli import build_parser
from dokimi.repos import selfcheck
from dokimi.repos.container import ContainerError
from dokimi.repos.graders import hidden_tests, main
from dokimi.repos.grading import EvalContext, _run_hidden_tests, _stage_hidden
from dokimi.repos.manifest import ManifestError, grade_problems, parse_manifest, TaskSummary
from dokimi.repos.replay import History, authoring_prompt
from dokimi.repos.replay.authoring import family, is_test_file, is_test_path
from dokimi.repos.workspace import Task
from repo_eval_fixture import FakeRunner, git, write

TEST_CMD = "python3 -m pytest -q -p no:cacheprovider --junitxml={out} {tests}"
MANIFEST = {
    "schema": "repo_manifest.v1", "id": "toy", "source": {"url": "https://example.invalid/toy"},
    "eval_dir": ".agent-eval", "guidance": {"globs": ["**/AGENTS.md"]},
    "image": {"dockerfile": ".agent-eval/Dockerfile", "network": "none",
              "caches": [{"name": "flip", "path": "/cache/flip"}], "env": {"FLIP_DIR": "/cache/flip"}},
    "entry_points": {"test": TEST_CMD}, "tasks": ".agent-eval/tasks",
}
CALC = "def add(a, b):\n    return a + b\n"
MUL = CALC + "\n\ndef mul(a, b):\n    return a * b\n"
TEST_MUL = "from src.calc import mul\n\n\ndef test_mul_multiplies():\n    assert mul(2, 3) == 6\n"
BODY = """Adds `mul` to the calculator.

The new test_mul_multiplies checks it.

```python
def mul(a, b):
    return a * b
```

```
an example run, not from the change
```

Diff: [the files](https://example.invalid/pull/1/files), commit {sha7}; see also #12."""


def _commit(root: Path, msg: str) -> str:
    git(root, "add", "-A")
    git(root, "commit", "-qm", msg)
    return git(root, "rev-parse", "HEAD")


def _pr_branch(root: Path, main: str, name: str, files: dict[str, str | None]) -> str:
    """A branch off ``main`` with one commit writing (or, for ``None``, deleting) ``files``; its head."""
    git(root, "checkout", "-q", main)
    git(root, "checkout", "-qb", name)
    for rel, text in files.items():
        if text is None:
            (root / rel).unlink()
        else:
            write(root, rel, text)
    return _commit(root, name)


def _squash(root: Path, main: str, branch: str) -> str:
    git(root, "checkout", "-q", main)
    git(root, "merge", "-q", "--squash", branch)
    return _commit(root, f"squash {branch}")


@pytest.fixture
def toy(tmp_path):
    """main and its merged PRs, each squashed with its head kept as refs/pull/<n>/head.

    1 adds mul with a test (and edits AGENTS.md and eval_dir); 2 changes code only; 3 changes tests only;
    4 is merged by fast-forward; 5 names its test on six lines; 6 adds a test whose result flips each run."""
    root = tmp_path / "toy"
    write(root, "src/calc.py", CALC)
    write(root, "tests/test_calc.py", "from src.calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n")
    write(root, "AGENTS.md", "# guidance\n")
    write(root, ".agent-eval/repo-eval.yaml", yaml.safe_dump(MANIFEST))
    write(root, ".agent-eval/Dockerfile", "FROM scratch\n")
    write(root, ".agent-eval/tasks/.keep", "")
    git(root, "init", "-q")
    git(root, "config", "core.autocrlf", "false")
    c0 = _commit(root, "code")
    main = git(root, "rev-parse", "--abbrev-ref", "HEAD")
    h = {"root": root, "c0": c0, "main": main}
    h["h1"] = _pr_branch(root, main, "pr1", {"src/calc.py": MUL, "tests/test_mul.py": TEST_MUL,
                                            "tests/data/case.txt": "6\n", "AGENTS.md": "# guidance\nmore\n",
                                            ".agent-eval/notes.md": "notes\n", "docs/mul.md": "mul\n"})
    h["m1"] = _squash(root, main, "pr1")
    h["h2"] = _pr_branch(root, main, "pr2", {"src/calc.py": MUL + "# tidy\n"})
    h["m2"] = _squash(root, main, "pr2")
    h["h3"] = _pr_branch(root, main, "pr3", {"tests/test_calc.py": "def test_more():\n    assert True\n"})
    h["m3"] = _squash(root, main, "pr3")
    h["h4"] = _pr_branch(root, main, "pr4", {"src/sub.py": "def sub(a, b):\n    return a - b\n",
                                            "tests/test_sub.py": "def test_sub():\n    pass\n"})
    git(root, "checkout", "-q", main)
    git(root, "merge", "-q", "--ff-only", "pr4")
    h["m4"] = h["h4"]
    h["h5"] = _pr_branch(root, main, "pr5", {"src/div.py": "def div(a, b):\n    return a / b\n",
                                            "tests/test_div.py": "def test_div_halves():\n    pass\n"})
    h["m5"] = _squash(root, main, "pr5")
    flip = ("import os\nfrom pathlib import Path\nfrom src.neg import neg\n\n\ndef test_neg_flips():\n"
            "    p = Path(os.environ['FLIP_DIR']) / 'n'\n    n = int(p.read_text()) if p.exists() else 0\n"
            "    p.write_text(str(n + 1))\n    assert neg(1) == -1 and n % 2 == 0\n")
    h["h6"] = _pr_branch(root, main, "pr6", {"src/neg.py": "def neg(a):\n    return -a\n", "tests/test_neg.py": flip})
    h["m6"] = _squash(root, main, "pr6")
    for n in range(1, 7):
        if n != 4:
            git(root, "update-ref", f"refs/pull/{n}/head", h[f"h{n}"])
    git(root, "branch", "-q", "-D", "pr1", "pr2", "pr3", "pr4", "pr5", "pr6")
    return h


def _pr(number, head, merge, *, title="Add a thing", body="", created="2026-10-01T09:00:00Z"):
    return {"number": number, "title": title, "body": body, "createdAt": created,
            "url": f"https://example.invalid/pull/{number}", "author": {"login": "alice", "__typename": "User"},
            "headRefOid": head, "mergeCommit": {"oid": merge} if merge else None}


def _pr1(h, **kw):
    return _pr(1, h["h1"], h["m1"], title="feat(calc): add mul", body=BODY.format(sha7=h["h1"][:7]), **kw)


def _pages(*prs):
    return [{"data": {"search": {"pageInfo": {"hasNextPage": True, "endCursor": "x"}, "nodes": list(prs)}}},
            {"data": {"search": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": list(prs[:1])}}}]


def _authoring(root: Path, doc, out: Path, *extra: str, raw: str | None = None, source: str | None = None) -> None:
    path = root.parent / "prs.json"
    path.write_text(raw if raw is not None else json.dumps(doc), encoding="utf-8")
    args = build_parser().parse_args(["repo", "tasks", "authoring", source or str(root), "--prs", str(path),
                                      "--out", str(out), *extra])
    args.func(args)


def _task(out: Path, number: int) -> dict:
    return json.loads((out / f"authoring_pr{number}" / "task.json").read_text())


# ------------------------------------------------------------------ the task of one PR


def test_a_merged_pr_with_a_test_becomes_a_task_with_hidden_tests_and_a_reference(toy, tmp_path, capsys):
    out = tmp_path / "tasks"
    _authoring(toy["root"], _pages(_pr1(toy)), out)
    task, d = _task(out, 1), out / "authoring_pr1"
    assert task["id"] == "authoring_pr1" and task["family"] == "feature" and task["grader"] == "dokimi:hidden_tests"
    assert task["base"] == toy["c0"] and task["merge_commit"] == toy["m1"] and task["head_commit"] == toy["h1"]
    assert task["source_pr"] == 1 and task["source_url"] == "https://example.invalid/pull/1"
    assert task["hidden_tests"] == ["tests/test_mul.py"]
    assert task["hidden_files"] == ["tests/data/case.txt", "tests/test_mul.py"]  # a fixture the tests read, too
    assert task["left_out_files"] == [".agent-eval/notes.md", "AGENTS.md"]
    assert task["patch_files"] == ["docs/mul.md", "src/calc.py", "tests/data/case.txt", "tests/test_mul.py"]
    assert task["public_at"] == "2026-10-01T09:00:00Z" and "gate" not in task  # the default gate: pass
    assert (d / "hidden/tests/test_mul.py").read_text() == TEST_MUL
    assert (d / "reference/src/calc.py").read_text() == MUL and (d / "reference/docs/mul.md").exists()
    assert sorted(p.relative_to(d).as_posix() for p in d.rglob("*") if p.is_file()) == [
        "hidden/tests/data/case.txt", "hidden/tests/test_mul.py", "reference/docs/mul.md", "reference/src/calc.py",
        "task.json"]  # no guidance, no eval_dir, no test in the reference
    assert task["oracle_lines"] == 5  # calc.py: 4 lines added; docs/mul.md: 1
    printed = capsys.readouterr().out
    assert "PR 1: written authoring_pr1: 1 hidden test file(s), 5 line(s) outside the tests" in printed
    assert "1 task(s) written, 0 kept, 0 pull request(s) skipped" in printed


def test_the_prompt_drops_quoted_code_change_links_and_lines_naming_a_hidden_test(toy, tmp_path):
    out = tmp_path / "tasks"
    _authoring(toy["root"], [_pr1(toy)], out)
    task = _task(out, 1)
    assert task["prompt_dropped"] == {"test_lines": 1, "code_blocks": 1, "links": 2}
    prompt = task["prompt"]
    assert prompt.startswith("feat(calc): add mul\n\nAdds `mul` to the calculator.")
    assert "test_mul" not in prompt and "return a * b" not in prompt  # the test's line, the quoted code
    assert "an example run, not from the change" in prompt  # a block that quotes nothing of the change stays
    assert "/pull/1/files" not in prompt and toy["h1"][:7] not in prompt and "see also #12" in prompt
    assert prompt.endswith(authoring_prompt.DEFAULT_INSTRUCTION)


def test_the_prompt_drops_a_diff_and_change_links_and_keeps_other_links_and_hex_words():
    body = ("```diff\nsmall\n```\n\n```\n@@ -1 +1 @@\n```\n\n"
            "See [the docs](https://example.invalid/docs) and https://example.invalid/wiki.\n"
            "Compare https://example.invalid/compare/a...b, build deadbeef1, commit abcdef12.")
    prompt = authoring_prompt.build("T", body, names=[], changed_lines=set(), number=9, commits=["abcdef1234"])
    assert prompt.dropped == {"test_lines": 0, "code_blocks": 2, "links": 2}
    assert prompt.text.split("\n\n")[1] == ("See [the docs](https://example.invalid/docs) and "
                                            "https://example.invalid/wiki.\nCompare  build deadbeef1, commit .")


def test_a_line_naming_a_hidden_test_by_a_shorter_path_is_dropped():
    body = "see `tests/test_a.py`\nrun ./tests/test_a.py\nFAILED tests/test_a.py::test_x\nmytest_a.py stays"
    names = authoring_prompt.test_names(["pkg/tests/test_a.py"], [])
    prompt = authoring_prompt.build("T", body, names=names, changed_lines=set(), number=9, commits=[])
    assert prompt.dropped["test_lines"] == 3 and prompt.text.split("\n\n")[1] == "mytest_a.py stays"


def test_a_prompt_file_replaces_the_instruction_and_an_empty_one_exits_1(toy, tmp_path, capsys):
    out, instr = tmp_path / "tasks", tmp_path / "instr.txt"
    instr.write_text("Do it with no new dependency.\n", encoding="utf-8")
    _authoring(toy["root"], [_pr1(toy)], out, "--prompt-file", str(instr))
    assert _task(out, 1)["prompt"].endswith("\n\nDo it with no new dependency.")
    instr.write_text("  \n", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        _authoring(toy["root"], [_pr1(toy)], tmp_path / "other", "--prompt-file", str(instr))
    assert exc.value.code == 1 and f"✗ the prompt file {instr} is empty" in capsys.readouterr().err


@pytest.mark.parametrize(("created", "public"), [("2026-10-01T09:00:00+02:00", True), ("2026-10-01T09:00:00", False),
                                                 (None, False), ("yesterday", False)])
def test_public_at_is_the_prs_created_at_when_it_has_an_offset(toy, tmp_path, capsys, created, public):
    out = tmp_path / "tasks"
    _authoring(toy["root"], [_pr1(toy, created=created)], out)
    assert _task(out, 1).get("public_at") == (created if public else None)
    assert ("no public_at: createdAt" in capsys.readouterr().out) is not public


def test_prs_without_a_task_say_why(toy, tmp_path, capsys):
    t = toy
    prs = [_pr(2, t["h2"], t["m2"]), _pr(3, t["h3"], t["m3"]), _pr(4, t["h4"], t["m4"]), _pr(7, t["h2"], None),
           _pr(8, "ab" * 20, t["m2"]), {k: v for k, v in _pr(9, t["h6"], t["m6"]).items() if k != "body"}, _pr1(t)]
    _authoring(t["root"], prs, tmp_path / "tasks")
    printed = capsys.readouterr().out
    for line in ("PR 2: skipped: it adds or changes no test file",
                 "PR 3: skipped: it changes only test files, so there is nothing to implement",
                 "PR 4: skipped: merged by fast-forward: its mergeCommit is its head",
                 "PR 7: skipped: not merged: no mergeCommit in the input",
                 f"PR 8: skipped: its headRefOid {'ab' * 6} is not in the source (fetch refs/pull/8/head)",
                 "PR 9: skipped: no body in the input: the query in the contract (§4.8) asks for body and createdAt",
                 "1 task(s) written, 0 kept, 6 pull request(s) skipped"):
        assert line in printed, line
    assert sorted(p.name for p in (tmp_path / "tasks").iterdir()) == ["authoring_pr1"]


def _off_base(toy, name: str, files: dict[str, str | None], link: str | None = None) -> str:
    """A PR head on ``c0``, the parent of ``m1``, so that its base is ``c0``; ``link`` is made a link to calc.py."""
    root = toy["root"]
    git(root, "checkout", "-q", toy["c0"])
    git(root, "checkout", "-qb", name)
    for rel, text in files.items():
        if text is None:
            (root / rel).unlink()
        else:
            write(root, rel, text)
    if link:
        (root / link).symlink_to("calc.py")
    head = _commit(root, name)
    git(root, "checkout", "-q", toy["main"])
    return head


def test_prs_whose_diff_gives_no_task_say_why(toy, tmp_path, capsys):
    t, root, test = toy, toy["root"], {"tests/test_x.py": "def test_x():\n    pass\n"}
    empty = git(root, "commit-tree", f"{t['c0']}^{{tree}}", "-p", t["c0"], "-m", "nothing")
    unrelated = git(root, "commit-tree", f"{t['h1']}^{{tree}}", "-m", "no parent")
    heads = {20: empty, 21: _off_base(t, "guidance", {"AGENTS.md": "# more\n", ".agent-eval/n.md": "n\n"}),
             22: _off_base(t, "link", test, link="src/link.py"),
             23: _off_base(t, "bytes", {**test, os.fsdecode(b"src/\xff.py"): "x = 1\n"}), 24: unrelated}
    _authoring(root, [_pr(n, head, t["m1"]) for n, head in heads.items()] + [_pr1(t)], tmp_path / "tasks")
    printed = capsys.readouterr().out
    for line in (f"PR 20: skipped: {empty[:12]} changes nothing against its base {t['c0'][:12]}",
                 "PR 21: skipped: it changes only guidance files or files under eval_dir",
                 "PR 22: skipped: it makes src/link.py a symbolic link or a submodule, which a task copies no file for",
                 f"PR 23: skipped: src/�.py is not in {heads[23][:12]}",  # a path git gives that is not UTF-8
                 f"PR 24: skipped: its headRefOid {unrelated[:12]} shares no history with the base branch"):
        assert line in printed, line
    assert sorted(p.name for p in (tmp_path / "tasks").iterdir()) == ["authoring_pr1"]


def test_a_deleted_file_is_in_reference_deleted_and_a_quote_of_a_removed_line_is_dropped(toy, tmp_path):
    head = _off_base(toy, "move", {"src/calc.py": None, "src/plus.py": "def add(a, b):\n    return sum((a, b))\n",
                                   "tests/test_plus.py": "from src.plus import add\n\n\ndef test_add():\n"
                                                         "    assert add(1, 2) == 3\n"})
    body = "Moves add.\n\n```\nreturn a + b\n```\n\nThe old line goes."
    _authoring(toy["root"], [_pr(30, head, toy["m1"], body=body)], tmp_path / "tasks")
    task, d = _task(tmp_path / "tasks", 30), tmp_path / "tasks/authoring_pr30"
    assert task["reference_deleted"] == ["src/calc.py"] and not (d / "reference/src/calc.py").exists()
    assert task["prompt_dropped"]["code_blocks"] == 1 and "return a + b" not in task["prompt"]
    assert "The old line goes." in task["prompt"]


def test_a_test_file_the_pr_deletes_is_not_hidden_and_an_executable_one_keeps_its_mode(toy, tmp_path):
    root = toy["root"]
    git(root, "checkout", "-q", toy["c0"])
    git(root, "checkout", "-qb", "swap")
    (root / "tests/test_calc.py").unlink()
    write(root, "src/neg.py", "def neg(a):\n    return -a\n")
    write(root, "tests/test_neg.py", "def test_neg():\n    pass\n")
    write(root, "tests/run.sh", "#!/bin/sh\nexit 0\n")
    (root / "tests/run.sh").chmod(0o755)
    head = _commit(root, "swap")
    git(root, "checkout", "-q", toy["main"])
    _authoring(root, [_pr(31, head, toy["m1"])], tmp_path / "tasks")
    task, d = _task(tmp_path / "tasks", 31), tmp_path / "tasks/authoring_pr31"
    assert task["hidden_tests"] == ["tests/test_neg.py"] and "tests/test_calc.py" not in task["hidden_files"]
    assert [oct((d / "hidden" / p).stat().st_mode & 0o777) for p in ("tests/run.sh", "tests/test_neg.py")] == [
        "0o755", "0o644"]


def test_a_first_oracle_run_that_cannot_run_makes_the_task_broken_not_a_crash(toy, tmp_path, monkeypatch):
    root = toy["root"]
    _authoring(root, [_pr1(toy)], root / ".agent-eval/tasks")
    _commit(root, "authoring tasks")
    run_entry = selfcheck.run_entry

    def fails_the_first_oracle_grade(ctx, kind, task, rd, *rest):
        if kind == "grade" and rd.name.endswith("__oracle"):
            raise ContainerError("the daemon went away")
        return run_entry(ctx, kind, task, rd, *rest)

    monkeypatch.setattr(selfcheck, "run_entry", fails_the_first_oracle_grade)
    res = _check(root, tmp_path / "chk", monkeypatch, "authoring_pr1")
    assert res["verdict"] == "broken" and res.get("oracle_again") is None
    assert any("the daemon went away" in p for p in res["problems"]), res["problems"]


def test_a_remote_source_fetches_the_prs_head_by_sha(toy, tmp_path):
    """A clone holds the branches, not refs/pull/*: the PR's head is fetched from origin by SHA."""
    _authoring(toy["root"], [_pr1(toy)], tmp_path / "local")
    _authoring(toy["root"], [_pr1(toy)], tmp_path / "remote", "--cache-dir", str(tmp_path / "cache"),
               source=f"file://{toy['root']}@HEAD")
    assert _task(tmp_path / "remote", 1) == _task(tmp_path / "local", 1)


def test_n_lines_changed_of_no_path_is_zero_not_the_whole_diff(toy):
    h = History(toy["root"] / ".git")
    assert h.n_lines_changed(toy["c0"], toy["m1"], []) == 0
    assert h.n_lines_changed(toy["c0"], toy["m1"], ["src/calc.py"]) == 4


def test_max_lines_skips_a_pr_whose_diff_outside_the_tests_is_larger(toy, tmp_path, capsys):
    with pytest.raises(SystemExit):  # its one PR skipped: no task
        _authoring(toy["root"], [_pr1(toy)], tmp_path / "small", "--max-lines", "4")
    assert "PR 1: skipped: its diff outside the tests changes 5 lines, more than --max-lines 4" in \
        capsys.readouterr().out
    _authoring(toy["root"], [_pr1(toy)], tmp_path / "exact", "--max-lines", "5")
    assert _task(tmp_path / "exact", 1)["oracle_lines"] == 5


def test_a_description_that_names_its_tests_on_more_than_five_lines_is_skipped(toy, tmp_path, capsys):
    body = "\n".join(["Adds div."] + [f"- test_div_halves case {i}" for i in range(6)])
    _authoring(toy["root"], [_pr(5, toy["h5"], toy["m5"], body=body), _pr1(toy)], tmp_path / "tasks")
    assert ("PR 5: skipped: its description names the hidden tests on 6 lines, more than 5: the description is "
            "its tests") in capsys.readouterr().out
    _authoring(toy["root"], [_pr(5, toy["h5"], toy["m5"], body="\n".join(body.split("\n")[:6]))], tmp_path / "five")
    task = _task(tmp_path / "five", 5)
    assert task["prompt_dropped"]["test_lines"] == 5 and "test_div_halves" not in task["prompt"]
    assert task["prompt"].startswith("Add a thing\n\nAdds div.\n\n")


def test_a_fix_title_gives_family_fix():
    assert [family(t) for t in ("fix(cli): x", "Fix the parser", "bugfix: y", "feat: z", "fixture loader", "docs")] \
        == ["fix", "fix", "fix", "feature", "feature", "feature"]


def test_which_files_are_tests():
    assert [is_test_file(p) for p in ("tests/test_a.py", "a_test.go", "x.spec.ts", "b.test.js", "tests/util.py",
                                      "contest.py")] == [True, True, True, True, False, False]
    assert [is_test_path(p) for p in ("tests/util.py", "src/conftest.py", "pkg/__tests__/x.js", "src/testing.py",
                                      "latest/a.py")] == [True, True, True, False, False]


def test_an_existing_task_is_kept_unless_forced(toy, tmp_path, capsys):
    out = tmp_path / "tasks"
    _authoring(toy["root"], [_pr1(toy)], out)
    path = out / "authoring_pr1/task.json"
    path.write_text(json.dumps({**_task(out, 1), "family": "fix"}))
    (out / "authoring_pr1/reference/stale.txt").write_text("from an earlier build")
    _authoring(toy["root"], [_pr1(toy)], out)
    assert _task(out, 1)["family"] == "fix"
    assert "PR 1: kept authoring_pr1: its task.json exists (--force rewrites it)" in capsys.readouterr().out
    _authoring(toy["root"], [_pr1(toy)], out, "--force")
    assert _task(out, 1)["family"] == "feature" and not (out / "authoring_pr1/reference/stale.txt").exists()


@pytest.mark.parametrize(("raw", "message"), [
    (None, "cannot read the pull request file"),
    ("", "cannot read the pull request file"),
    ("[]", "holds no pull request: no object with `number` and `mergeCommit`"),
    ('{"data": {"x": 1}}', "holds no pull request"),
    ('[{"number": null, "mergeCommit": null}]', "a pull request's number must be an integer, not None"),
])
def test_an_unusable_pr_file_exits_1_with_the_reason(toy, tmp_path, capsys, raw, message):
    path = tmp_path / "prs.json"
    if raw is not None:
        path.write_text(raw, encoding="utf-8")
    args = build_parser().parse_args(["repo", "tasks", "authoring", str(toy["root"]), "--prs", str(path),
                                      "--out", str(tmp_path / "tasks")])
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    err = capsys.readouterr().err
    assert exc.value.code == 1 and err.startswith("✗ ") and message in err


def test_no_task_from_any_pr_exits_1(toy, tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        _authoring(toy["root"], [_pr(2, toy["h2"], toy["m2"])], tmp_path / "tasks")
    assert exc.value.code == 1 and "✗ no pull request gave a task" in capsys.readouterr().err


# ------------------------------------------------------------------ eval check: null fails, oracle passes, twice


def _check(root: Path, out: Path, monkeypatch, task_id: str) -> dict:
    monkeypatch.setattr("dokimi.repos.container.DockerRunner", lambda *a, **k: FakeRunner())
    args = build_parser().parse_args(["eval", "check", f"{root}@HEAD", "--out", str(out),
                                      "--cache-root", str(out / "caches"), "--task", task_id])
    try:
        args.func(args)
    except SystemExit:
        pass
    return json.loads((out / "check/check.json").read_text())["tasks"][0]


def _metrics(out: Path, task_id: str, step: str) -> dict:
    return json.loads((out / "check" / f"{task_id}__{step}" / "grade.json").read_text())["metrics"]


def test_the_task_passes_eval_check_base_fails_and_the_oracle_passes_twice(toy, tmp_path, monkeypatch):
    root = toy["root"]
    _authoring(root, [_pr1(toy)], root / ".agent-eval/tasks")
    _commit(root, "authoring tasks")
    out = tmp_path / "chk"
    res = _check(root, out, monkeypatch, "authoring_pr1")
    assert res["verdict"] == "ok", res
    assert res["null"]["pass"] is False and res["oracle"]["pass"] is True and res["oracle"]["success"] is True
    assert res["null_again"]["pass"] is False and res["oracle_again"]["pass"] is True
    assert _metrics(out, "authoring_pr1", "oracle")["pass_share"] == 1.0
    null = _metrics(out, "authoring_pr1", "null")
    assert null["n_errors"] == 1 and null["pass_share"] == 0.0 and null["tests_rc"] != 0  # mul cannot be imported
    ws = out / "check/authoring_pr1__oracle/workspace"
    assert (ws / "src/calc.py").read_text() == MUL and (ws / "tests/test_mul.py").read_text() == TEST_MUL
    assert not (out / "check/authoring_pr1__null/workspace/docs/mul.md").exists()


def test_a_task_whose_two_oracle_runs_differ_is_flaky_not_ok(toy, tmp_path, monkeypatch, capsys):
    root = toy["root"]
    _authoring(root, [_pr(6, toy["h6"], toy["m6"], title="Add neg")], root / ".agent-eval/tasks")
    _commit(root, "authoring tasks")
    res = _check(root, tmp_path / "chk", monkeypatch, "authoring_pr6")
    assert res["verdict"] == "flaky" and res["oracle"]["pass"] is True and res["oracle_again"]["pass"] is False
    assert res["problems"] == ["flaky: the two oracle runs differ: graded, pass True (n_passed 1, n_failed 0, "
                               "n_errors 0, n_skipped 0) then graded, pass False (n_passed 0, n_failed 1, n_errors 0, "
                               "n_skipped 0)"]
    assert "0/1 ok" in capsys.readouterr().out


def test_a_second_run_that_cannot_run_makes_the_task_flaky_and_reads_not_graded(toy, tmp_path, monkeypatch):
    root = toy["root"]
    _authoring(root, [_pr1(toy)], root / ".agent-eval/tasks")
    _commit(root, "authoring tasks")
    run_entry = selfcheck.run_entry

    def fails_the_second_oracle(ctx, kind, task, rd, *rest):
        if rd.name.endswith("__oracle2"):
            raise ContainerError("the daemon went away")
        return run_entry(ctx, kind, task, rd, *rest)

    monkeypatch.setattr(selfcheck, "run_entry", fails_the_second_oracle)
    res = _check(root, tmp_path / "chk", monkeypatch, "authoring_pr1")
    assert res["verdict"] == "flaky" and res["oracle"]["pass"] is True and res["oracle_again"] is None
    assert res["problems"] == ["flaky: the two oracle runs differ: graded, pass True (n_passed 1, n_failed 0, "
                               "n_errors 0, n_skipped 0) then not graded"]


SQ_TEST = ("import sys\n\nimport pytest\nfrom src.calc import add\n\n\ndef test_add_still_adds():\n"
           "    assert add(1, 1) == 2\n\n\n")
SKIPS = {"importorskip": "def test_sq_squares():\n    assert pytest.importorskip('src.sq').sq(3) == 9\n",
         "platform": "def test_sq():\n    from src.sq import sq\n    assert sq(3) == 9\n\n\n"
                     "@pytest.mark.skipif(sys.platform != 'none', reason='elsewhere')\ndef test_sq_elsewhere():\n"
                     "    pass\n"}


@pytest.mark.parametrize(("skip", "verdict", "share"), [("importorskip", "ok", 1 / 2), ("platform", "broken", 2 / 3)])
def test_a_skipped_hidden_case_fails_the_grade_so_a_task_whose_oracle_skips_one_is_broken(toy, tmp_path, monkeypatch,
                                                                                        skip, verdict, share):
    """A test that skips when the change is absent shows nothing; nor does one that the oracle run skips too."""
    root = toy["root"]
    sq = {"src/sq.py": "def sq(a):\n    return a * a\n", "tests/test_sq.py": SQ_TEST + SKIPS[skip]}
    _authoring(root, [_pr(40, _off_base(toy, skip, sq), toy["m1"], title="Add sq")], root / ".agent-eval/tasks")
    _commit(root, "authoring tasks")
    res = _check(root, tmp_path / "chk", monkeypatch, "authoring_pr40")
    step = "null" if skip == "importorskip" else "oracle"  # the run in which the case skips
    got = _metrics(tmp_path / "chk", "authoring_pr40", step)
    assert res["verdict"] == verdict and res[step]["pass"] is False, res
    assert (got["n_skipped"], got["pass_share"]) == (1, share)
    if skip == "platform":  # the skipped case is the one grade.json names
        assert res["problems"] == ["oracle: the reference solution does not pass the gate"]
        doc = json.loads((tmp_path / "chk/check/authoring_pr40__oracle/grade.json").read_text())
        assert doc["details"]["not_passed"] == ["tests.test_sq::test_sq_elsewhere"]


def test_repo_validate_finds_a_hidden_file_the_task_lacks(toy, tmp_path, capsys):
    root = toy["root"]
    _authoring(root, [_pr1(toy)], root / ".agent-eval/tasks")
    (root / ".agent-eval/tasks/authoring_pr1/hidden/tests/data/case.txt").unlink()
    _commit(root, "authoring tasks")
    capsys.readouterr()
    args = build_parser().parse_args(["repo", "validate", f"{root}@HEAD", "--cache-dir", str(tmp_path / "caches"),
                                      "--json"])
    with pytest.raises(SystemExit):
        args.func(args)
    errors = json.loads(capsys.readouterr().out)["errors"]
    assert errors == [".agent-eval/tasks/authoring_pr1/task.json: hidden file does not exist: hidden/tests/data/case.txt"]


def test_a_hidden_file_that_is_a_link_fails_validate_and_eval_check_grades_the_task_broken(toy, tmp_path, monkeypatch,
                                                                                            capsys):
    root = toy["root"]
    _authoring(root, [_pr1(toy)], root / ".agent-eval/tasks")
    hidden = root / ".agent-eval/tasks/authoring_pr1/hidden/tests/test_mul.py"
    hidden.unlink()
    hidden.symlink_to("does-not-exist.py")
    _commit(root, "authoring tasks")
    capsys.readouterr()
    args = build_parser().parse_args(["repo", "validate", f"{root}@HEAD", "--cache-dir", str(tmp_path / "caches"),
                                      "--json"])
    with pytest.raises(SystemExit):
        args.func(args)
    errors = json.loads(capsys.readouterr().out)["errors"]
    assert errors == [".agent-eval/tasks/authoring_pr1/task.json: hidden file is a symbolic link: hidden/tests/test_mul.py"]
    res = _check(root, tmp_path / "chk", monkeypatch, "authoring_pr1")
    assert res["verdict"] == "broken" and "hidden/tests/test_mul.py in the task is a symbolic link" in str(res["problems"])


# ------------------------------------------------------------------ the grader on its own


def _run_dir(tmp_path: Path, report: str | None, rc: int = 0, **task) -> Path:
    rd = tmp_path / "run"
    (rd / "workspace").mkdir(parents=True)
    (rd / "task.json").write_text(json.dumps({"id": "t", "grader": "dokimi:hidden_tests",
                                              "hidden_tests": ["tests/test_a.py"], **task}))
    (rd / hidden_tests.RUN_RECORD).write_text(json.dumps({"rc": rc, "timed_out": False}))
    if report is not None:
        (rd / hidden_tests.REPORT).write_text(report)
    return rd


def _junit(*outcomes: str) -> str:
    tag = {"failed": "<failure message='x'/>", "error": "<error message='x'/>", "skipped": "<skipped/>", "passed": ""}
    cases = "".join(f"<testcase classname='t' name='c{i}'>{tag[o]}</testcase>" for i, o in enumerate(outcomes))
    return f"<testsuites><testsuite name='s'>{cases}</testsuite></testsuites>"


@pytest.mark.parametrize(("outcomes", "rc", "passed", "share"), [
    (("passed", "passed", "skipped"), 0, False, 2 / 3),  # a skipped case did not show the change
    (("passed", "failed", "error", "passed"), 1, False, 0.5),
    (("passed",), 1, False, 1.0),  # the command failed though every case passed: not a pass
    (("skipped",), 0, False, 0.0),  # nothing ran
])
def test_the_grade_passes_only_when_every_hidden_test_ran_and_passed(tmp_path, outcomes, rc, passed, share):
    doc = main.grade("hidden_tests", tmp_path, _run_dir(tmp_path, _junit(*outcomes), rc))
    assert doc["pass"] is passed and doc["metrics"]["pass_share"] == share and doc["errors"] == []
    assert doc["metrics"]["n_tests"] == len(outcomes) and doc["metrics"]["tests_rc"] == rc


@pytest.mark.parametrize(("report", "why"), [(None, "wrote no JUnit report"), ("<testsuite", "is not XML"),
                                             ("<!DOCTYPE x [<!ENTITY a 'b'>]><testsuite/>", "declares a DOCTYPE")])
def test_a_missing_or_unreadable_report_fails_the_run(tmp_path, report, why):
    doc = main.grade("hidden_tests", tmp_path, _run_dir(tmp_path, report))
    assert doc["pass"] is False and doc["metrics"]["n_tests"] == 0 and why in doc["details"]["report"]
    assert doc["metrics"]["pass_share"] == 0.0  # counted in repo compare's mean, as the untouched base is


def test_a_report_the_grader_cannot_read_fails_the_run_not_the_grader(tmp_path, monkeypatch):
    rd = _run_dir(tmp_path, _junit("passed"))

    def denied(self):
        raise PermissionError(13, "Permission denied", str(self))

    monkeypatch.setattr(Path, "read_bytes", denied)
    doc = main.grade("hidden_tests", tmp_path, rd)
    assert doc["pass"] is False and doc["errors"] == [] and doc["metrics"]["n_tests"] == 0
    assert doc["details"]["report"].startswith(f"{hidden_tests.REPORT} cannot be read: [Errno 13] Permission denied")


def test_a_staging_problem_or_a_bad_task_is_a_grader_error(tmp_path):
    rd = _run_dir(tmp_path, _junit("passed"))
    (rd / hidden_tests.RUN_RECORD).write_text(json.dumps({"problem": "hidden file x: a link"}))
    assert main.grade("hidden_tests", tmp_path, rd)["errors"][0] == "ValueError: hidden file x: a link"
    rd = _run_dir(tmp_path / "b", _junit("passed"), hidden_tests=["../x.py"])
    assert "hidden_tests must be a non-empty list" in main.grade("hidden_tests", tmp_path, rd)["errors"][0]


@pytest.mark.parametrize(("record", "error"), [
    (None, f"ValueError: no {hidden_tests.RUN_RECORD}: the test command did not run"),
    ("[]", f"ValueError: {hidden_tests.RUN_RECORD} is not an object"),
])
def test_a_missing_or_malformed_run_record_is_a_grader_error(tmp_path, record, error):
    rd = _run_dir(tmp_path, _junit("passed"))
    if record is None:
        (rd / hidden_tests.RUN_RECORD).unlink()
    else:
        (rd / hidden_tests.RUN_RECORD).write_text(record)
    doc = main.grade("hidden_tests", tmp_path, rd)
    assert doc["pass"] is None and doc["errors"][0] == error


def test_a_report_over_the_size_limit_fails_the_run(tmp_path, monkeypatch):
    report = _junit("passed")
    monkeypatch.setattr(hidden_tests, "MAX_REPORT_BYTES", len(report) - 1)
    doc = main.grade("hidden_tests", tmp_path, _run_dir(tmp_path, report))
    assert doc["pass"] is False and doc["details"]["report"] == f"{hidden_tests.REPORT} is over {len(report) - 1} bytes"
    monkeypatch.setattr(hidden_tests, "MAX_REPORT_BYTES", len(report))
    assert main.grade("hidden_tests", tmp_path, tmp_path / "run")["pass"] is True


def test_hidden_paths_must_be_distinct_and_the_tests_among_the_hidden_files():
    assert hidden_tests.task_problems({"hidden_tests": ["tests/test_a.py", "tests/./test_a.py"]}) == [
        "hidden_tests must be distinct after normalising"]
    assert hidden_tests.task_problems({"hidden_tests": ["tests/test_a.py", "tests/test_b.py"],
                                       "hidden_files": ["tests/test_a.py"]}) == [
        "hidden_tests not in hidden_files: tests/test_b.py"]


def test_the_oracle_refuses_a_bad_task_and_writes_nothing(tmp_path):
    rd = _run_dir(tmp_path, None, hidden_tests=[])
    write(tmp_path / "task", "reference/src/new.py", "new\n")
    with pytest.raises(ValueError, match="hidden_tests must be a non-empty list"):
        main.oracle("hidden_tests", tmp_path / "task", rd)
    assert not (rd / "workspace/src/new.py").exists()


def test_the_oracle_copies_the_reference_and_deletes_what_the_change_deletes(tmp_path):
    rd = _run_dir(tmp_path, None, reference_deleted=["src/old.py", "src/gone.py"])
    write(rd / "workspace", "src/old.py", "old\n")
    td = tmp_path / "task"
    write(td, "reference/src/new.py", "new\n")
    assert main.oracle("hidden_tests", td, rd) == ""
    assert (rd / "workspace/src/new.py").read_text() == "new\n" and not (rd / "workspace/src/old.py").exists()


def test_the_oracle_deletes_nothing_through_a_link(tmp_path):
    rd = _run_dir(tmp_path, None, reference_deleted=["src/keep.py"])
    write(tmp_path / "outside", "keep.py", "outside the workspace\n")
    (rd / "workspace/src").symlink_to(tmp_path / "outside", target_is_directory=True)
    with pytest.raises(ValueError, match="through a symbolic link"):
        hidden_tests.oracle(json.loads((rd / "task.json").read_text()), tmp_path / "task", rd)
    assert (tmp_path / "outside/keep.py").exists()


def _hidden_task(tmp_path: Path) -> Task:
    td = tmp_path / "content/authoring_x"
    write(td, "hidden/tests/test_a.py", "def test_a():\n    pass\n")
    raw = {"id": "authoring_x", "family": "feature", "base": "a" * 40, "prompt": "p", "grader": "dokimi:hidden_tests",
           "hidden_tests": ["tests/test_a.py"]}
    return Task(id="authoring_x", raw=raw, dir=td)


def test_staging_replaces_a_link_and_refuses_a_linked_directory(tmp_path):
    task, ws = _hidden_task(tmp_path), tmp_path / "ws"
    write(ws, "tests/keep.txt", "")
    (tmp_path / "target.py").write_text("outside\n")
    (ws / "tests/test_a.py").symlink_to(tmp_path / "target.py")
    assert _stage_hidden(task, ws) is None
    assert not (ws / "tests/test_a.py").is_symlink() and (tmp_path / "target.py").read_text() == "outside\n"
    ws2 = tmp_path / "ws2"
    ws2.mkdir()
    (ws2 / "tests").symlink_to(tmp_path / "elsewhere", target_is_directory=True)
    (tmp_path / "elsewhere").mkdir()
    assert "tests in the workspace is a symbolic link" in (_stage_hidden(task, ws2) or "")
    assert not (tmp_path / "elsewhere/test_a.py").exists()


@pytest.mark.parametrize("link", ["absolute", "dangling", "directory"])
def test_staging_refuses_a_hidden_file_reached_through_a_link_and_copies_nothing(tmp_path, link):
    task, ws = _hidden_task(tmp_path), tmp_path / "ws"
    ws.mkdir()
    (tmp_path / "host").mkdir()
    (tmp_path / "host/test_a.py").write_text("TOKEN = 'host-only'\n")
    if link == "directory":
        (task.dir / "hidden/tests/test_a.py").unlink()
        (task.dir / "hidden/tests").rmdir()
        (task.dir / "hidden/tests").symlink_to(tmp_path / "host", target_is_directory=True)
    else:
        (task.dir / "hidden/tests/test_a.py").unlink()
        (task.dir / "hidden/tests/test_a.py").symlink_to(tmp_path / ("host/test_a.py" if link == "absolute" else "no"))
    assert _stage_hidden(task, ws) == ("hidden file tests/test_a.py: hidden/tests/test_a.py in the task is a "
                                       "symbolic link or not a file")
    assert not (ws / "tests/test_a.py").exists()


def test_a_hidden_file_that_cannot_be_copied_is_a_staging_problem(tmp_path, monkeypatch):
    def denied(src, dest):
        raise PermissionError(13, "Permission denied", str(dest))

    monkeypatch.setattr("dokimi.repos.grading.shutil.copy", denied)
    assert (_stage_hidden(_hidden_task(tmp_path), tmp_path) or "").startswith("hidden file tests/test_a.py: [Errno 13]")


def test_staging_keeps_each_hidden_files_mode(tmp_path):
    task, ws = _hidden_task(tmp_path), tmp_path / "ws"
    write(task.dir, "hidden/tests/run.sh", "#!/bin/sh\nexit 0\n")
    (task.dir / "hidden/tests/run.sh").chmod(0o755)
    (task.dir / "hidden/tests/test_a.py").chmod(0o640)
    write(ws, "tests/test_a.py", "")
    (ws / "tests/test_a.py").chmod(0o777)
    staged = Task(id=task.id, raw={**task.raw, "hidden_files": ["tests/test_a.py", "tests/run.sh"]}, dir=task.dir)
    assert _stage_hidden(staged, ws) is None
    assert [oct((ws / p).stat().st_mode & 0o777) for p in ("tests/run.sh", "tests/test_a.py")] == ["0o755", "0o640"]


def test_staging_refuses_a_bad_task_and_a_workspace_that_is_no_directory(tmp_path):
    task = _hidden_task(tmp_path)
    bad = Task(id=task.id, raw={**task.raw, "hidden_tests": ["../x.py"]}, dir=task.dir)
    (tmp_path / "ws").mkdir()
    assert _stage_hidden(bad, tmp_path / "ws") == ("hidden_tests must be a non-empty list of workspace-relative "
                                                   "file paths")
    (tmp_path / "file").write_text("")
    (tmp_path / "linked").symlink_to(tmp_path / "ws", target_is_directory=True)
    assert [_stage_hidden(task, tmp_path / w) for w in ("file", "linked")] == ["the workspace is not a directory"] * 2
    assert not (tmp_path / "ws/tests").exists()


def _ctx(tmp_path: Path, runner: FakeRunner, **entry_points) -> EvalContext:
    return EvalContext(manifest=parse_manifest(_manifest(**entry_points)), git_dir=tmp_path,
                       content_dir=tmp_path / "content", image="img", runner=runner)


def test_a_hidden_tests_grade_without_a_test_entry_point_cannot_start(tmp_path):
    (tmp_path / "rd").mkdir()
    with pytest.raises(ContainerError, match="the manifest declares no `test` entry point"):
        _run_hidden_tests(_ctx(tmp_path, FakeRunner(), grade="x"), _hidden_task(tmp_path), tmp_path / "rd", "none",
                          [], None)


def test_a_staging_problem_is_recorded_without_running_the_tests_and_stale_outputs_go(tmp_path):
    rd, runner = tmp_path / "rd", FakeRunner()
    (rd / "workspace/tests/test_a.py").mkdir(parents=True)  # a directory where the hidden test goes
    (rd / hidden_tests.REPORT).mkdir()
    (rd / "test.log").write_text("an earlier run\n")
    _run_hidden_tests(_ctx(tmp_path, runner, test=TEST_CMD), _hidden_task(tmp_path), rd, "none", [], None)
    assert json.loads((rd / hidden_tests.RUN_RECORD).read_text()) == {
        "problem": "hidden file tests/test_a.py: the workspace has a directory there"}
    assert runner.specs == [] and not (rd / hidden_tests.REPORT).exists() and not (rd / "test.log").exists()


# ------------------------------------------------------------------ the manifest's test entry point


def _manifest(**entry_points) -> dict:
    return {**MANIFEST, "entry_points": entry_points}


@pytest.mark.parametrize(("entry_points", "error"), [
    ({"test": "pytest --junitxml={out}"}, "entry_points/test: must name {tests}"),
    ({"test": "pytest {tests}"}, "entry_points/test: must name {out}"),
    ({"test": TEST_CMD, "grade": "run {tests}"}, "entry_points/grade: {tests} is only for entry_points.test"),
])
def test_the_test_entry_point_names_its_tests_and_report(entry_points, error):
    with pytest.raises(ManifestError) as exc:
        parse_manifest(_manifest(**entry_points))
    assert error in str(exc.value)


def test_a_hidden_tests_task_needs_the_test_entry_point():
    tasks = [TaskSummary("authoring_pr1", "feature", "a" * 40, "dokimi:hidden_tests", ("pass",))]
    assert grade_problems(parse_manifest(_manifest(test=TEST_CMD)), tasks) == []
    assert grade_problems(parse_manifest(_manifest(grade="x")), tasks) == [
        "entry_points/test: required, since these tasks run their hidden tests with it (dokimi:hidden_tests): "
        "authoring_pr1"]
