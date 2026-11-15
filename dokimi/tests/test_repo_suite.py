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


@pytest.mark.parametrize("rev", ["main", "v1.0", "HEAD~1", "abcdef0", "a" * 39, "a" * 41, "a" * 63])
def test_a_branch_tag_or_ref_is_refused_as_a_pin(rev):
    with pytest.raises(SuiteError, match="not a commit; pin its SHA"):
        parse_suite(_raw(repos={"sc": f"https://example.invalid/scaffold@{rev}"}))


@pytest.mark.parametrize("rev", ["a" * 40, "b" * 64])
def test_a_full_commit_sha_is_a_pin(rev):
    suite = parse_suite(_raw(repos={"sc": f"https://example.invalid/scaffold@{rev}"}))
    assert suite.repos["sc"].rev == rev


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
    assert "no such task(s) under" in capsys.readouterr().err
    assert not (run["out"] / "b1" / "nope__guidance-none__m-1__r1").exists()


def test_a_suite_path_that_leaves_the_tree_is_a_usage_error(run):
    with pytest.raises(InputError, match="path escapes the repository"):
        run["invoke"]("--suite", "../suite.yaml", "--arm", "guidance:none")


def test_a_suite_file_that_is_not_utf8_is_a_suite_error(tmp_path):
    root, _ = make_repo(tmp_path)
    write(root, ".agent-eval/suite.yaml", "")
    (root / ".agent-eval/suite.yaml").write_bytes(b"schema: \xff\xfe\n")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "suite")
    tree = open_source(RepoSource.parse(str(root)))
    with pytest.raises(SuiteError, match="not valid YAML"):
        load_suite(tree, ".agent-eval/suite.yaml")


@pytest.mark.parametrize("scheme", ["", "file://"])
def test_a_missing_repository_leaves_its_tasks_unsupported_in_either_spelling(tmp_path, scheme):
    good, sha = _scaffold(tmp_path)
    suite = parse_suite(_raw(repos={"good": f"{good}@{sha}", "gone": f"{scheme}{tmp_path}/missing@{'b' * 40}"},
                             tasks=[{"id": "ok", "repo": "good"}, {"id": "lost", "repo": "gone"}]))
    found = resolve_suite(suite, {"ok": sha, "lost": sha}, tmp_path / "cache")
    assert set(found.git_dirs) == {"ok"}
    assert found.gaps["lost"].startswith("base repository gone:") and "unavailable" in found.gaps["lost"]


def test_a_local_path_that_is_not_pinned_is_still_refused(tmp_path):
    with pytest.raises(SuiteError, match="local source is not a directory"):
        parse_suite(_raw(repos={"sc": f"{tmp_path}/missing"}), allow_floating=True)


def test_a_base_the_warm_cache_of_a_remote_predates_is_fetched_not_a_gap(tmp_path):
    root, sha = _scaffold(tmp_path)
    suite = parse_suite(_raw(repos={"sc": f"file://{root}@{sha}"}))
    cache = tmp_path / "cache"
    assert resolve_suite(suite, {"t1": sha}, cache).gaps == {}  # warms the cache at the pin
    git(root, "checkout", "-q", "-b", "other")
    write(root, "src/more.py", "X = 1\n")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "base on another branch")
    later = git(root, "rev-parse", "HEAD")
    found = resolve_suite(suite, {"t1": later}, cache)
    assert found.gaps == {} and "t1" in found.git_dirs
    assert resolve_suite(suite, {"t1": "1" * 40}, cache).gaps["t1"].startswith(f"base {'1' * 40!r} is not a commit of sc")


def _suite_of(root: Path, tmp: Path, name: str, scaffold: Path, sha: str, *tasks: str) -> str:
    """Commit ``.agent-eval/<name>.yaml``: both suites declare ``a`` and ``b`` (the same scaffold), ``tasks`` map to ``b``."""
    repos = {"a": f"{scaffold}@{sha}", "b": f"{scaffold}@{sha}"}
    path = f".agent-eval/{name}.yaml"
    write(root, path, yaml.safe_dump(_raw(repos=repos, tasks=[{"id": t, "repo": "b"} for t in tasks])))
    git(root, "add", "-A")
    git(root, "commit", "-qm", name)
    return path


def test_a_resume_with_another_suite_file_or_none_is_refused(run, capsys):
    scaffold, sha = _scaffold(run["tmp"])
    _commit_suite(run["root"], sha, _raw())  # fix_add's base is the scaffold's commit
    first = _suite_of(run["root"], run["tmp"], "first", scaffold, sha, "fix_add")
    second = _suite_of(run["root"], run["tmp"], "second", scaffold, sha, "review_add")
    same = _suite_of(run["root"], run["tmp"], "same", scaffold, sha, "fix_add")  # first's content, another file
    argv = ["--task", "fix_add", "--arm", "guidance:none"]
    run["invoke"]("--suite", first, *argv)
    run["invoke"]("--suite", first, *argv)  # the same suite resumes
    for extra in (["--suite", second], ["--suite", same], []):
        with pytest.raises(SystemExit) as exc:
            run["invoke"](*extra, *argv)
        assert exc.value.code == 1
        assert "use another --batch" in capsys.readouterr().err


def test_a_malformed_task_the_suite_and_the_selection_leave_out_does_not_stop_the_run(run):
    scaffold, sha = _scaffold(run["tmp"])
    write(run["root"], ".agent-eval/tasks/broken/task.json", "{bad")
    _commit_suite(run["root"], sha, _raw(repos={"sc": f"{scaffold}@{sha}"}, tasks=[{"id": "fix_add", "repo": "sc"}]))
    run["invoke"]("--suite", ".agent-eval/suite.yaml", "--task", "fix_add", "--arm", "guidance:none")
    assert (run["out"] / "b1/fix_add__guidance-none__m-1__r1/result.json").is_file()


def test_a_guidance_revision_arm_reads_the_eval_tree_while_the_base_comes_from_the_scaffold(run):
    scaffold, sha = _scaffold(run["tmp"])
    _commit_suite(run["root"], sha, _raw(repos={"sc": f"{scaffold}@{sha}"}, tasks=[{"id": "fix_add", "repo": "sc"}]))
    rev = git(run["root"], "rev-parse", "HEAD")
    run["invoke"]("--suite", ".agent-eval/suite.yaml", "--task", "fix_add", "--arm", f"guidance:rev:{rev}")
    cell = next((run["out"] / "b1").glob("fix_add__*/meta.json"))
    meta = json.loads(cell.read_text(encoding="utf-8"))
    assert meta["base_sha"] == sha
    assert "AGENTS.md" in meta["arm_files"]  # the scaffold has none: it is the eval tree's, at the revision
    assert (cell.parent / "workspace/AGENTS.md").read_text(encoding="utf-8") == "# guidance\n"


def test_a_suite_gap_does_not_abort_the_batch_on_a_skill_its_eval_base_ships(run):
    root = run["root"]
    skill = "---\nname: lint\ndescription: x\n---\nLint.\n"
    write(root, ".claude/skills/lint/SKILL.md", skill)
    git(root, "add", "-A")
    git(root, "commit", "-qm", "base ships lint")
    base = git(root, "rev-parse", "HEAD")
    write(root, ".agent-eval/skills/lint/SKILL.md", skill)
    gone = f"file://{run['tmp']}/missing@{'c' * 40}"
    _commit_suite(root, base, _raw(repos={"sc": gone}, tasks=[{"id": "fix_add", "repo": "sc"}]))
    run["invoke"]("--suite", ".agent-eval/suite.yaml", "--task", "fix_add", "--task", "review_add",
                  "--arm", "guidance:raw+skill:skills/lint")
    batch = run["out"] / "b1"
    assert list(batch.glob("fix_add__*/unsupported.json"))
    assert list(batch.glob("review_add__*/result.json"))


def _check(tmp: Path, monkeypatch, suite: dict, *extra: str, tasks: dict | None = None) -> tuple[int, dict]:
    """``eval check --suite`` over a repository whose suite is ``suite``; returns (exit code, check.json)."""
    root, base = make_repo(tmp, tasks)
    _commit_suite(root, None, suite)
    monkeypatch.setattr("dokimi.repos.container.DockerRunner", lambda *a, **k: FakeRunner())
    out = tmp / "check-out"
    args = build_parser().parse_args(["eval", "check", f"{root}@HEAD", "--out", str(out), "--cache-root",
                                      str(out / "caches"), "--suite", ".agent-eval/suite.yaml", *extra])
    code = 0
    try:
        args.func(args)
    except SystemExit as exc:
        code = exc.code
    return code, json.loads((out / "check/check.json").read_text(encoding="utf-8"))


def test_a_self_check_where_every_selected_task_is_a_suite_gap_exits_0(tmp_path, monkeypatch, capsys):
    gone = f"file://{tmp_path}/missing@{'c' * 40}"
    code, report = _check(tmp_path, monkeypatch, _raw(repos={"sc": gone}, tasks=[{"id": "fix_add", "repo": "sc"}]),
                          "--task", "fix_add")
    captured = capsys.readouterr()
    assert code == 0 and "incomplete" not in captured.err
    assert [(r["task"], r["unsupported_by"]) for r in report["tasks"]] == [("fix_add", "suite")]


def test_a_suite_gap_is_not_checked_even_when_the_task_also_needs_egress(tmp_path, monkeypatch):
    gone = f"file://{tmp_path}/missing@{'c' * 40}"
    net = {"net_add": {"family": "review", "grader": "review", "review_patch": "change.patch", "egress": ["example.com"],
                       "score": {"metric": "recall", "max": 1.0}, "prompt": "Review HEAD."}}
    code, report = _check(tmp_path, monkeypatch, _raw(repos={"sc": gone}, tasks=[{"id": "net_add", "repo": "sc"}]),
                          "--task", "net_add", tasks=net)
    assert code == 0
    assert report["tasks"][0]["unsupported_by"] == "suite"
