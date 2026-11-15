# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Suite manifests (suite_manifest.v1, contract §2.1): the schema, the pins, the base of each task
and the run. Every repository here is a local git repository; nothing reaches the network."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from dokimi.cli import build_parser
from dokimi.repos.source import RepoSource, open_source
from dokimi.repos.suite import SuiteError, load_suite, parse_suite, resolve_suite
from dokimi.utils import InputError
from repo_eval_fixture import FakeRunner, fake_harness, git, make_repo, write

EXAMPLE = Path(__file__).resolve().parent.parent / "data" / "suites" / "example-suite.yaml"
PINNED = "https://example.invalid/scaffold@" + "a" * 40
CALC = "def add(a, b):\n    return a + b\n"


def _raw(**over):
    raw = {"schema": "suite_manifest.v1", "id": "s", "repos": {"sc": PINNED}, "tasks": [{"id": "t1", "repo": "sc"}]}
    raw.update(over)
    return raw


def test_the_example_suite_parses_and_every_repo_is_pinned():
    suite = parse_suite(yaml.safe_load(EXAMPLE.read_text(encoding="utf-8")), str(EXAMPLE))
    assert suite.tasks == {"solver-pick-cholesky": "sklearn-scaffold", "solver-pick-lstsq": "numpy-scaffold"}
    assert all(len(src.rev or "") == 40 for src in suite.repos.values())


@pytest.mark.parametrize("rev", ["main", "v1.0", "HEAD~1"])
def test_a_branch_tag_or_ref_is_refused_as_a_pin(rev):
    with pytest.raises(SuiteError, match="not a commit; pin its SHA"):
        parse_suite(_raw(repos={"sc": f"https://example.invalid/scaffold@{rev}"}))


def test_a_floating_rev_is_kept_only_when_the_run_allows_it():
    suite = parse_suite(_raw(repos={"sc": "https://example.invalid/scaffold@main"}), allow_floating=True)
    assert suite.repos["sc"].rev == "main"


def test_a_repo_without_a_revision_is_refused_even_when_floating_is_allowed(tmp_path):
    with pytest.raises(SuiteError, match="not pinned"):
        parse_suite(_raw(repos={"sc": str(tmp_path)}), allow_floating=True)
    with pytest.raises(SuiteError, match="must be pinned"):
        parse_suite(_raw(repos={"sc": "https://example.invalid/scaffold"}))


def test_a_task_must_name_a_declared_repo_and_appear_once():
    with pytest.raises(SuiteError, match="repo 'nope' is not declared"):
        parse_suite(_raw(tasks=[{"id": "t1", "repo": "nope"}]))
    with pytest.raises(SuiteError, match="listed more than once"):
        parse_suite(_raw(tasks=[{"id": "t1", "repo": "sc"}, {"id": "t1", "repo": "sc"}]))


@pytest.mark.parametrize("change", [
    {"schema": "suite_manifest.v2"},
    {"unexpected": True},
    {"repos": {}},
    {"tasks": [{"id": "a,b", "repo": "sc"}]},
    {"tasks": [{"id": "t1"}]},
])
def test_the_schema_refuses_a_malformed_suite(change):
    with pytest.raises(SuiteError):
        parse_suite(_raw(**change))


def test_a_suite_file_missing_from_the_tree_is_refused(tmp_path):
    root, _ = make_repo(tmp_path)
    tree = open_source(RepoSource.parse(str(root)))
    with pytest.raises(SuiteError, match="suite manifest not found"):
        load_suite(tree, ".agent-eval/suite.yaml")


def _scaffold(tmp_path: Path, name: str = "scaffold") -> tuple[Path, str]:
    root = tmp_path / name
    write(root, "src/calc.py", CALC)
    git(root, "init", "-q", "-b", "main")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "scaffold")
    return root, git(root, "rev-parse", "HEAD")


def test_each_task_takes_its_base_from_the_repo_it_names_and_a_gap_is_recorded(tmp_path):
    good, sha = _scaffold(tmp_path)
    suite = parse_suite(_raw(repos={"good": f"{good}@{sha}", "gone": f"file://{tmp_path}/missing@{'b' * 40}"},
                             tasks=[{"id": "ok", "repo": "good"}, {"id": "absent", "repo": "good"},
                                    {"id": "lost", "repo": "gone"}]))
    bases = {"ok": sha, "absent": "1" * 40, "lost": sha}
    found = resolve_suite(suite, bases, tmp_path / "cache")
    assert set(found.git_dirs) == {"ok"} and found.git_dirs["ok"].is_dir()
    assert found.meta == {"ok": {"repo": "good", "source": f"{good}@{sha}"}}
    assert found.pins == {"good": sha}
    assert "is not a commit of good" in found.gaps["absent"]
    assert found.gaps["lost"].startswith("base repository gone:") and "unavailable" in found.gaps["lost"]


def test_a_floating_branch_resolves_once_to_the_commit_it_names(tmp_path):
    root, sha = _scaffold(tmp_path)
    suite = parse_suite(_raw(repos={"sc": f"{root}@main"}, tasks=[{"id": "t1", "repo": "sc"}]),
                        allow_floating=True)
    found = resolve_suite(suite, {"t1": sha}, tmp_path / "cache")
    assert found.pins == {"sc": sha} and found.gaps == {}


def test_the_suite_file_is_read_from_the_eval_tree(tmp_path):
    root, _ = make_repo(tmp_path)
    write(root, ".agent-eval/suite.yaml", yaml.safe_dump(_raw()))
    git(root, "add", "-A")
    git(root, "commit", "-qm", "suite")
    tree = open_source(RepoSource.parse(str(root)))
    assert load_suite(tree, ".agent-eval/suite.yaml").tasks == {"t1": "sc"}


@pytest.fixture
def run(tmp_path, monkeypatch):
    root, _ = make_repo(tmp_path)
    exe, profile = fake_harness(tmp_path)
    monkeypatch.setattr("dokimi.repos.container.DockerRunner", lambda *a, **k: FakeRunner())
    monkeypatch.setenv("FAKE_ACTION", "fix")
    state = {"root": root, "out": tmp_path / "runs", "tmp": tmp_path}

    def invoke(*extra: str) -> None:
        argv = ["repo", "run", f"{root}@HEAD", "--manifest", ".agent-eval/repo-eval.yaml", "--harness",
                str(profile), "--bin", str(exe), "--model", "m-1", "--batch", "b1", "--out", str(state["out"]),
                "--cache-root", str(state["out"] / "caches"), "--agent-network", "host", *extra]
        args = build_parser().parse_args(argv)
        args.func(args)

    state["invoke"] = invoke
    return state


def _commit_suite(root: Path, base_of_fix: str | None, suite: dict) -> None:
    """Point fix_add at ``base_of_fix`` (when given) and commit the suite file into the eval tree."""
    if base_of_fix is not None:
        task = root / ".agent-eval/tasks/fix_add/task.json"
        write(root, ".agent-eval/tasks/fix_add/task.json",
              json.dumps({**json.loads(task.read_text(encoding="utf-8")), "base": base_of_fix}))
    write(root, ".agent-eval/suite.yaml", yaml.safe_dump(suite))
    git(root, "add", "-A")
    git(root, "commit", "-qm", "suite")


def test_a_run_takes_the_base_of_a_suite_task_from_its_repo(run):
    scaffold, sha = _scaffold(run["tmp"])
    _commit_suite(run["root"], sha, _raw(repos={"sc": f"{scaffold}@{sha}"}, tasks=[{"id": "fix_add", "repo": "sc"}]))
    run["invoke"]("--suite", ".agent-eval/suite.yaml", "--task", "fix_add", "--task", "review_add",
                  "--arm", "guidance:none")
    batch = run["out"] / "b1"
    fix = json.loads((batch / "fix_add__guidance-none__m-1__r1/meta.json").read_text(encoding="utf-8"))
    assert fix["base_sha"] == sha and fix["base_repo"] == {"repo": "sc", "source": f"{scaffold}@{sha}"}
    assert (batch / "fix_add__guidance-none__m-1__r1/result.json").is_file()
    review = json.loads((batch / "review_add__guidance-none__m-1__r1/meta.json").read_text(encoding="utf-8"))
    assert "base_repo" not in review  # a task the suite does not name keeps its eval-tree base
    pin = json.loads((batch / "_eval/batch.json").read_text(encoding="utf-8"))
    assert pin["suite"] == {"sc": sha}


def test_a_missing_suite_repo_leaves_its_cells_unsupported_and_not_failed(run, capsys):
    gone = f"file://{run['tmp']}/missing@{'c' * 40}"
    _commit_suite(run["root"], None, _raw(repos={"sc": gone}, tasks=[{"id": "fix_add", "repo": "sc"}]))
    run["invoke"]("--suite", ".agent-eval/suite.yaml", "--task", "fix_add", "--task", "review_add",
                  "--arm", "guidance:none")
    batch = run["out"] / "b1"
    assert not (batch / "fix_add__guidance-none__m-1__r1/result.json").exists()
    unsupported = json.loads((batch / "fix_add__guidance-none__m-1__r1/unsupported.json").read_text(encoding="utf-8"))
    assert unsupported["reason"].startswith("base repository sc:") and unsupported["task"] == "fix_add"
    assert (batch / "review_add__guidance-none__m-1__r1/result.json").is_file()
    assert "fix_add: UNSUPPORTED, its cells are not run: base repository sc:" in capsys.readouterr().out


def test_a_suite_task_the_tree_lacks_is_refused_before_any_cell(run, capsys):
    _commit_suite(run["root"], None, _raw(tasks=[{"id": "nope", "repo": "sc"}],
                                          repos={"sc": f"{run['root']}@{'d' * 40}"}))
    with pytest.raises(SystemExit) as exc:
        run["invoke"]("--suite", ".agent-eval/suite.yaml", "--arm", "guidance:none")
    assert exc.value.code == 1
    assert "task nope is not under" in capsys.readouterr().err
    assert not (run["out"] / "b1" / "nope__guidance-none__m-1__r1").exists()


def test_a_suite_path_that_leaves_the_tree_is_a_usage_error(run):
    with pytest.raises(InputError, match="path escapes the repository"):
        run["invoke"]("--suite", "../suite.yaml", "--arm", "guidance:none")
