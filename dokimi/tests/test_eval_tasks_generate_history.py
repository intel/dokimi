# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``eval tasks generate --from history``: authoring tasks from bug-fix commits (contract §4.8).

A toy history built per test; ``eval check`` runs on :class:`FakeRunner` as in ``test_repo_authoring``.
No network, no docker.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from dokimi.cli import build_parser
from dokimi.repos.replay import authoring_prompt
from dokimi.utils import InputError
from repo_eval_fixture import FakeRunner, git, write

MANIFEST = {
    "schema": "repo_manifest.v1", "id": "toy", "source": {"url": "https://example.invalid/toy"},
    "eval_dir": ".agent-eval", "guidance": {"globs": ["**/AGENTS.md"]},
    "image": {"dockerfile": ".agent-eval/Dockerfile", "network": "none"},
    "entry_points": {"test": "python3 -m pytest -q -p no:cacheprovider --junitxml={out} {tests}"},
    "tasks": ".agent-eval/tasks",
}
CALC = "def add(a, b):\n    return a + b\n"
MUL = CALC + "\n\ndef mul(a, b):\n    return a * b\n"
TEST_MUL = "from src.calc import mul\n\n\ndef test_mul_multiplies():\n    assert mul(2, 3) == 6\n"


def _commit(root: Path, msg: str, files: dict[str, str]) -> str:
    for rel, text in files.items():
        write(root, rel, text)
    git(root, "add", "-A")
    git(root, "commit", "-qm", msg)
    return git(root, "rev-parse", "HEAD")


@pytest.fixture
def toy(tmp_path):
    """A root commit, then: a squash-merged fix with a test (``(#12)``), a fix with no test, a non-fix with a
    test, and a direct fix commit with a test and a body that links its own commit."""
    root = tmp_path / "toy"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "core.autocrlf", "false")
    h = {"root": root}
    h["c0"] = _commit(root, "init", {
        "src/calc.py": CALC, "AGENTS.md": "# guidance\n", ".agent-eval/repo-eval.yaml": yaml.safe_dump(MANIFEST),
        ".agent-eval/Dockerfile": "FROM scratch\n", ".agent-eval/tasks/.keep": "",
        "tests/test_calc.py": "from src.calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"})
    h["c1"] = _commit(root, "fix(calc): mul was missing (#12)\n\nSee https://example.invalid/pull/12 and #3.", {
        "src/calc.py": MUL, "tests/test_mul.py": TEST_MUL, "AGENTS.md": "# guidance\nmore\n"})
    h["c2"] = _commit(root, "fix: tidy calc", {"src/calc.py": MUL + "# tidy\n"})
    h["c3"] = _commit(root, "feat: add sub", {"src/sub.py": "def sub(a, b):\n    return a - b\n",
                                              "tests/test_sub.py": "def test_sub():\n    pass\n"})
    h["c4"] = _commit(root, "Crash on neg\n\nThe change: https://example.invalid/toy/commit/abc. "
                      "Also https://example.invalid/pull/12 stays.",
                      {"src/neg.py": "def neg(a):\n    return -a\n",
                       "tests/test_neg.py": "from src.neg import neg\n\n\ndef test_neg():\n    assert neg(1) == -1\n"})
    return h


def _gen(root: Path, out: Path, *extra: str, rev: str = "HEAD", check: bool = True) -> None:
    args = build_parser().parse_args(["eval", "tasks", "generate", f"{root}@{rev}", "--from", "history",
                                      "--out-dir", str(out), *extra])
    if check:
        args.func(args)
        return
    with pytest.raises(SystemExit):
        args.func(args)


def _task(out: Path, sha: str) -> dict:
    return json.loads((out / f"authoring_{sha[:12]}" / "task.json").read_text())


def test_each_fix_commit_with_a_test_becomes_a_task_from_its_parent(toy, tmp_path, capsys):
    out = tmp_path / "tasks"
    _gen(toy["root"], out)
    printed = capsys.readouterr().out
    assert "3 of 5 first-parent commit(s)" in printed  # c1, c2, c4: fix, fix, crash
    assert f"skipped authoring_{toy['c2'][:12]}: it adds or changes no test file" in printed
    assert "2 task(s) written, 0 kept, 1 commit(s) skipped" in printed
    assert sorted(p.name for p in out.iterdir()) == sorted(f"authoring_{toy[c][:12]}" for c in ("c1", "c4"))
    t1 = _task(out, toy["c1"])
    assert t1["base"] == toy["c0"] and t1["grader"] == "dokimi:hidden_tests" and t1["family"] == "fix"
    assert t1["source_commit"] == toy["c1"] and t1["source_pr"] == 12
    assert t1["provenance"]["source"].startswith("generated:dokimi-tasks-generate@")
    assert t1["hidden_tests"] == ["tests/test_mul.py"] and t1["left_out_files"] == ["AGENTS.md"]
    assert "public_at" not in t1
    assert t1["prompt"].startswith("fix(calc): mul was missing\n\n")  # the (#12) suffix goes
    assert "/pull/12" not in t1["prompt"] and "#3" in t1["prompt"] and t1["prompt_dropped"]["links"] == 1
    assert (out / f"authoring_{toy['c1'][:12]}" / "reference/src/calc.py").read_text() == MUL
    t4 = _task(out, toy["c4"])
    assert "source_pr" not in t4 and t4["base"] == toy["c3"] and t4["family"] == "fix"
    # no PR number: a link to a pull request stays, a link to a commit goes
    assert "/commit/abc" not in t4["prompt"] and "https://example.invalid/pull/12 stays" in t4["prompt"]


def test_subject_and_commits_choose_the_commits(toy, tmp_path, capsys):
    out = tmp_path / "tasks"
    _gen(toy["root"], out, "--subject", r"^feat", "--commits", "2")  # c4, c3; only c3 matches
    printed = capsys.readouterr().out
    assert "1 of 2 first-parent commit(s)" in printed and "1 task(s) written" in printed
    assert [p.name for p in out.iterdir()] == [f"authoring_{toy['c3'][:12]}"]


def test_the_root_commit_is_skipped(toy, tmp_path, capsys):
    with pytest.raises(SystemExit):
        _gen(toy["root"], tmp_path / "tasks", "--subject", "^init$")
    captured = capsys.readouterr()
    assert "a root commit has no parent" in captured.out and "✗ no commit gave a task" in captured.err


def test_no_matching_commit_exits_1(toy, tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        _gen(toy["root"], tmp_path / "tasks", "--subject", "nothing-like-this")
    assert exc.value.code == 1 and "0 of 5 first-parent commit(s)" in capsys.readouterr().out


def test_a_shallow_clone_exits_1(toy, tmp_path, capsys):
    shallow = tmp_path / "shallow"
    git(tmp_path, "clone", "-q", "--depth", "1", toy["root"].as_uri(), str(shallow))
    with pytest.raises(SystemExit) as exc:
        _gen(shallow, tmp_path / "tasks")
    assert exc.value.code == 1
    assert "✗ history generation needs a full clone; run git fetch --unshallow" in capsys.readouterr().err


def test_dry_run_writes_nothing_and_kept_tasks_stay(toy, tmp_path, capsys):
    out = tmp_path / "tasks"
    _gen(toy["root"], out, "--dry-run")
    assert "2 task(s) to write" in capsys.readouterr().out and not out.exists()
    _gen(toy["root"], out)
    path = out / f"authoring_{toy['c1'][:12]}" / "task.json"
    path.write_text(path.read_text().replace("mul was missing", "edited"))
    _gen(toy["root"], out, "--max-lines", "1")  # kept before the size filter
    assert f"kept authoring_{toy['c1'][:12]}" in capsys.readouterr().out and "edited" in path.read_text()
    _gen(toy["root"], out, "--force")
    assert "edited" not in path.read_text()


@pytest.mark.parametrize("extra, error", [
    (["--index", "x.scip"], "--index is for --from graph"),
    (["--prs", "p.json"], "--prs is for --from prs"),
    (["--references", "9"], "--references is for --from graph"),
    (["--subject", "("], "is not a regular expression"),
    (["--subject", "(" * 10000], "is not a regular expression"),
    (["--subject", "a{4294967296}"], "is not a regular expression"),
    (["--subject", ""], "--subject is empty"),
])
def test_usage_errors(toy, tmp_path, extra, error):
    with pytest.raises(InputError, match=error):
        _gen(toy["root"], tmp_path / "tasks", *extra)


def test_history_flags_are_refused_with_from_graph(toy, tmp_path):
    args = build_parser().parse_args(["eval", "tasks", "generate", f"{toy['root']}@HEAD", "--from", "graph",
                                      "--index", "x.scip", "--out-dir", str(tmp_path / "t"), "--commits", "5"])
    with pytest.raises(InputError, match="--commits is for --from history"):
        args.func(args)


@pytest.mark.parametrize("extra", [
    ["--from", "prs", "--prs", "p.json", "--commits", "300"],
    ["--from", "replay", "--threads", "t.json", "--subject", "fix"],
    ["--from", "prs", "--prs", "p.json", "--dry-run"],
])
def test_history_flags_are_refused_with_another_source(toy, tmp_path, extra):
    args = build_parser().parse_args(["eval", "tasks", "generate", f"{toy['root']}@HEAD", "--out-dir",
                                      str(tmp_path / "t"), *extra])
    with pytest.raises(InputError, match=f"{extra[-2] if extra[-1] != '--dry-run' else '--dry-run'} is for --from"):
        args.func(args)


def test_an_unpinned_source_is_a_usage_error_and_an_empty_prompt_file_exits_1(toy, tmp_path, capsys):
    args = build_parser().parse_args(["eval", "tasks", "generate", str(toy["root"]), "--from", "history",
                                      "--out-dir", str(tmp_path / "t")])
    with pytest.raises(InputError, match="pin the revision"):
        args.func(args)
    (tmp_path / "empty.txt").write_text("  \n")
    with pytest.raises(SystemExit) as exc:
        _gen(toy["root"], tmp_path / "t", "--prompt-file", str(tmp_path / "empty.txt"))
    assert exc.value.code == 1
    assert "is empty" in capsys.readouterr().err


def test_a_prompt_with_no_number_keeps_pull_links_and_drops_commit_links():
    p = authoring_prompt.build("", "x https://h/pull/7 y https://h/commit/1", names=[], changed_lines=set(),
                               number=None, commits=[])
    assert p.text.startswith("Change\n\nx https://h/pull/7 y") and p.dropped["links"] == 1


def test_a_commit_task_passes_eval_check(toy, tmp_path, monkeypatch):
    root = toy["root"]
    _gen(root, root / ".agent-eval/tasks", "--commits", "4", rev=toy["c1"])  # c1 only (c0 is a root)
    git(root, "add", "-A")
    git(root, "commit", "-qm", "tasks")
    monkeypatch.setattr("dokimi.repos.container.DockerRunner", lambda *a, **k: FakeRunner())
    out = tmp_path / "chk"
    task_id = f"authoring_{toy['c1'][:12]}"
    args = build_parser().parse_args(["eval", "check", f"{root}@HEAD", "--out-dir", str(out),
                                      "--cache-root", str(out / "caches"), "--task", task_id])
    try:
        args.func(args)
    except SystemExit:
        pass
    res = json.loads((out / "check/check.json").read_text())["tasks"][0]
    assert res["verdict"] == "ok", res
    assert res["null"]["pass"] is False and res["oracle"]["pass"] is True


def test_merges_and_their_branches_give_no_task_and_are_counted(toy, tmp_path, capsys):
    root = toy["root"]
    git(root, "checkout", "-qb", "topic")
    branch_fix = _commit(root, "fix: branch fix", {"src/b.py": "def b():\n    return 1\n",
                                                   "tests/test_b.py": "def test_b():\n    pass\n"})
    git(root, "checkout", "-q", "-")
    git(root, "merge", "-q", "--no-ff", "-m", "Merge pull request #5 from topic: fix branch", "topic")
    out = tmp_path / "tasks"
    _gen(root, out, "--commits", "4")  # the merge, c4, c3, c2: without --first-parent the branch fix is one
    printed = capsys.readouterr().out
    assert "2 of 3 first-parent commit(s)" in printed and "; 1 merge commit(s) left out" in printed
    assert [p.name for p in out.iterdir()] == [f"authoring_{toy['c4'][:12]}"]  # c2 has no test
    assert branch_fix[:12] not in printed


def test_a_revert_or_fixup_is_never_a_fix(toy, tmp_path, capsys):
    root = toy["root"]
    _commit(root, 'Revert "fix(calc): mul was missing (#12)"', {"src/calc.py": CALC, "tests/test_mul.py": "\n"})
    for subject in ("fixup! fix: neg", "amend! fix: neg", "revert: fix(calc): mul", "Revert fix for crash",
                    "Back out fix: neg", '[backport] Revert "fix x"'):
        _commit(root, subject, {"src/neg.py": f"def neg(a):\n    return 0 - a  # {subject}\n",
                                "tests/test_neg.py": f"def test_neg():\n    pass  # {subject}\n"})
    _gen(root, tmp_path / "tasks", "--commits", "7", "--dry-run", check=False)
    assert "0 of 7 first-parent commit(s)" in capsys.readouterr().out


def test_trailers_stay_out_of_the_prompt(toy, tmp_path):
    root = toy["root"]
    sha = _commit(root, "fix: neg twice\n\nThe body.\nWhy: it crashed.\n\nSigned-off-by: Jane <jane@example.invalid>\n"
                  "Co-authored-by: Bob\n <bob@example.invalid>\n(cherry picked from commit 0123abc)",
                  {"src/neg.py": "def neg(a):\n    return -a\n\n\ndef neg2(a):\n    return a\n",
                   "tests/test_neg2.py": "def test_neg2():\n    pass\n"})
    out = tmp_path / "tasks"
    _gen(root, out, "--commits", "1")
    prompt = _task(out, sha)["prompt"]
    assert "The body.\nWhy: it crashed." in prompt and "example.invalid" not in prompt and "cherry" not in prompt


@pytest.mark.parametrize("newline", ["\r\n", "\r"])
@pytest.mark.parametrize("key", ["Signed-off-by", "Co-developed-by", "Requested-by", "Bisected-by", "Reported-and-tested-by"])
def test_messages_lose_person_trailers(newline, key):
    from dokimi.repos.replay.authoring_commits import without_trailers

    message = newline.join(("Body.", "", f"{key}: J <j@x>", "(cherry picked from commit 0123abc)"))
    assert without_trailers(message) == "Body."


def test_two_commits_of_one_pull_request_give_one_task_and_the_dry_run_says_so(toy, tmp_path, capsys):
    root = toy["root"]
    first = _commit(root, "fix: neg one (#20)", {"src/neg.py": "def neg(a):\n    return 0 - a\n",
                                                 "tests/test_neg.py": "def test_neg():\n    pass  # 1\n"})
    second = _commit(root, "fix: neg two (#20)", {"src/neg.py": "def neg(a):\n    return -1 * a\n",
                                                  "tests/test_neg.py": "def test_neg():\n    pass  # 2\n"})
    out = tmp_path / "tasks"
    for extra, summary in ((("--dry-run",), "1 task(s) to write, 1 kept"), ((), "1 task(s) written, 1 kept")):
        _gen(root, out, "--commits", "2", *extra)  # newest first: the second commit is the pull request's task
        printed = capsys.readouterr().out
        assert summary in printed and f"kept authoring_{second[:12]}: authoring_{second[:12]} is this pull" in printed
        assert f"skipped authoring_{first[:12]}" in printed
    assert [p.name for p in out.iterdir()] == [f"authoring_{second[:12]}"]


def test_a_pull_request_with_a_task_from_prs_gets_no_second_one(toy, tmp_path, capsys):
    out = tmp_path / "tasks"
    (out / "authoring_pr12").mkdir(parents=True)
    (out / "authoring_pr12" / "task.json").write_text("{}")
    _gen(toy["root"], out)
    printed = capsys.readouterr().out
    assert "kept authoring_pr12: authoring_pr12 is this pull request's task" in printed
    assert not (out / f"authoring_{toy['c1'][:12]}").exists()
    _gen(toy["root"], out, "--subject", r"\(#12\)", "--force")  # nothing else to write: kept, so exit 0
    assert "0 task(s) written, 1 kept" in capsys.readouterr().out


def test_prs_after_history_gives_no_second_task(toy, tmp_path):
    from dokimi.repos.replay.authoring import Options
    from dokimi.repos.replay.authoring_prs import build_one
    from dokimi.repos.replay.threads import PullRequest

    out = tmp_path / "tasks"
    _gen(toy["root"], out)
    pr = PullRequest(number=12, title="fix(calc): mul was missing", url=None, author=None, head=None, merge=None,
                     threads=(), reviews=(), body="")
    o = build_one(None, None, pr, Options(out=out))  # never reaches the history: the task is found first
    assert (o.status, o.task) == ("kept", f"authoring_{toy['c1'][:12]}") and not (out / "authoring_pr12").exists()


def test_an_oversized_pr_suffix_is_ordinary_subject_text():
    from dokimi.repos.replay.authoring_commits import _split_subject

    subject = f"fix: bad suffix (#{'1' * 5000})"
    assert _split_subject(subject) == (subject, None)
