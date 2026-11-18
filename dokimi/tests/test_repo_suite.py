# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Suite manifests (suite_manifest.v1, contract §2.1): the schema, the pins, the base of each task
and the run. Every repository here is a local git repository; nothing reaches the network."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from dokimi.cli import build_parser
from dokimi.repos.source import RepoSource, open_source
from dokimi.repos.suite import SuiteError, load_suite, parse_suite, resolve_suite
from dokimi.utils import InputError
from repo_eval_fixture import PROFILE, FakeRunner, fake_harness, git, make_repo, write
from test_repo_host import AMD_CPUINFO, HostRunner, _probe_out
from test_repo_host import _check as _host_check
from test_repo_host import _repo as _race_repo

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
    {"repos": {1: PINNED, "sc": PINNED}},
])
def test_the_schema_refuses_a_malformed_suite(change):
    with pytest.raises(SuiteError):
        parse_suite(_raw(**change))


def test_a_suite_file_missing_from_the_tree_is_refused(tmp_path):
    root, _ = make_repo(tmp_path)
    tree = open_source(RepoSource.parse(str(root)))
    with pytest.raises(SuiteError, match="suite manifest not found"):
        load_suite(tree, ".agent-eval/suite.yaml")


def test_a_repo_name_that_is_not_a_string_is_a_suite_error_and_names_it():
    with pytest.raises(SuiteError, match="the name 1 is not a string"):
        parse_suite(_raw(repos={1: None, "sc": PINNED}))


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
    assert found.pins == {"good": sha, "gone": "b" * 40}  # every declared pin, the down repository's too
    assert "is not a commit of good" in found.gaps["absent"]
    assert found.gaps["lost"].startswith("base repository gone:") and "unavailable" in found.gaps["lost"]


def test_a_floating_branch_resolves_once_to_the_commit_it_names(tmp_path):
    root, sha = _scaffold(tmp_path)
    suite = parse_suite(_raw(repos={"sc": f"{root}@main"}, tasks=[{"id": "t1", "repo": "sc"}]),
                        allow_floating=True)
    found = resolve_suite(suite, {"t1": sha}, tmp_path / "cache")
    assert found.pins == {"sc": sha} and found.gaps == {}


def test_a_branch_named_like_a_hash_resolves_when_the_run_allows_floating_revs(tmp_path):
    root, sha = _scaffold(tmp_path)
    git(root, "branch", "b" * 64, sha)
    suite = parse_suite(_raw(repos={"sc": f"{root}@{'b' * 64}"}, tasks=[{"id": "t1", "repo": "sc"}]),
                        allow_floating=True)
    assert resolve_suite(suite, {"t1": sha}, tmp_path / "cache").pins == {"sc": sha}


def test_a_branch_named_like_a_hash_is_not_the_pinned_commit(tmp_path):
    root, sha = _scaffold(tmp_path)
    write(root, "src/more.py", "x = 1\n")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "more")
    fake = "b" * 64  # a SHA-1 repository: this is a branch, whatever its spelling
    git(root, "branch", fake, sha)
    suite = parse_suite(_raw(repos={"sc": f"{root}@{fake}"}))
    with pytest.raises(SuiteError, match="resolves to .*not to that commit"):
        resolve_suite(suite, {"t1": sha}, tmp_path / "cache")


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


@pytest.mark.parametrize("flags", [[], ["--allow-floating-suite-revs"]])
def test_a_batch_whose_full_sha_repository_was_down_resumes_once_it_is_back(run, flags):
    scaffold, sha = _scaffold(run["tmp"])
    _commit_suite(run["root"], sha, _raw(repos={"sc": f"{scaffold}@{sha}"}, tasks=[{"id": "fix_add", "repo": "sc"}]))
    argv = ["--suite", ".agent-eval/suite.yaml", "--task", "fix_add", "--task", "review_add", "--arm", "guidance:none"]
    away = scaffold.rename(run["tmp"] / "away")
    run["invoke"](*argv, *flags)
    assert list((run["out"] / "b1").glob("fix_add__*/unsupported.json"))
    away.rename(scaffold)
    run["invoke"](*argv, *flags)
    assert (run["out"] / "b1/fix_add__guidance-none__m-1__r1/result.json").is_file()


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


def _check(tmp: Path, monkeypatch, suite: dict, *extra: str, tasks: dict | None = None,
           base_of_fix: str | None = None) -> tuple[int, dict]:
    """``eval check --suite`` over a repository whose suite is ``suite``; returns (exit code, check.json)."""
    root, base = make_repo(tmp, tasks)
    _commit_suite(root, base_of_fix, suite)
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


def test_a_self_check_where_every_selected_task_is_a_suite_gap_exits_1(tmp_path, monkeypatch, capsys):
    gone = f"file://{tmp_path}/missing@{'c' * 40}"
    code, report = _check(tmp_path, monkeypatch, _raw(repos={"sc": gone}, tasks=[{"id": "fix_add", "repo": "sc"}]),
                          "--task", "fix_add")
    captured = capsys.readouterr()
    assert code == 1 and "incomplete: no task was checked" in captured.err
    assert [(r["task"], r["unsupported_by"]) for r in report["tasks"]] == [("fix_add", "suite")]


def test_a_self_check_names_the_suite_gaps_it_did_not_check(tmp_path, monkeypatch, capsys):
    gone = f"file://{tmp_path}/missing@{'c' * 40}"
    code, report = _check(tmp_path, monkeypatch, _raw(repos={"sc": gone}, tasks=[{"id": "fix_add", "repo": "sc"}]),
                          "--task", "fix_add", "--task", "review_add")
    out = capsys.readouterr().out
    assert code == 0 and "1 not checked, the repository of their base is missing: fix_add" in out
    assert {r["task"]: r["verdict"] for r in report["tasks"]}["review_add"] == "ok"


def test_a_suite_gap_is_not_checked_even_when_the_task_also_needs_egress(tmp_path, monkeypatch, capsys):
    gone = f"file://{tmp_path}/missing@{'c' * 40}"
    net = {"net_add": {"family": "review", "grader": "review", "review_patch": "change.patch", "egress": ["example.com"],
                       "score": {"metric": "recall", "max": 1.0}, "prompt": "Review HEAD."}}
    code, report = _check(tmp_path, monkeypatch, _raw(repos={"sc": gone}, tasks=[{"id": "net_add", "repo": "sc"}]),
                          "--task", "net_add", tasks=net)
    assert code == 1
    assert "incomplete: no task was checked" in capsys.readouterr().err
    assert report["tasks"][0]["unsupported_by"] == "suite"


def test_a_self_check_fails_only_on_the_other_bad_tasks_not_on_a_suite_gap(tmp_path, monkeypatch):
    gone = f"file://{tmp_path}/missing@{'c' * 40}"
    code, report = _check(tmp_path, monkeypatch, _raw(repos={"sc": gone}, tasks=[{"id": "fix_add", "repo": "sc"}]),
                          "--task", "fix_add", "--task", "review_add")
    assert code == 0
    assert {r["task"]: r["verdict"] for r in report["tasks"]}["review_add"] == "ok"


def test_a_self_check_takes_a_suite_task_base_from_its_scaffold_not_the_eval_tree(tmp_path, monkeypatch):
    scaffold, sha = _scaffold(tmp_path)  # this commit exists only in the scaffold
    code, report = _check(tmp_path, monkeypatch, _raw(repos={"sc": f"{scaffold}@{sha}"},
                                                      tasks=[{"id": "fix_add", "repo": "sc"}]),
                          "--task", "fix_add", base_of_fix=sha)
    assert code == 0 and report["tasks"][0]["verdict"] == "ok"


def test_a_self_check_with_a_suite_gap_and_only_an_allowed_host_gap_is_incomplete(tmp_path, monkeypatch, capsys):
    root = _race_repo(tmp_path)
    gone = f"file://{tmp_path}/missing@{'c' * 40}"
    _commit_suite(root, None, _raw(repos={"sc": gone}, tasks=[{"id": "fix_add", "repo": "sc"}]))
    with pytest.raises(SystemExit) as exc:
        _host_check(root, tmp_path / "a", HostRunner(_probe_out(affinity=2, cpuinfo=AMD_CPUINFO)), monkeypatch,
                    "--suite", ".agent-eval/suite.yaml", "--allow-unsupported", "--task", "fix_add", "--task", "fix_race")
    assert exc.value.code == 1 and "incomplete" in capsys.readouterr().err


def test_a_skill_the_scaffold_base_ships_is_refused_before_any_cell(run, capsys):
    scaffold, _ = _scaffold(run["tmp"])
    write(scaffold, ".claude/skills/lint/SKILL.md", "---\nname: lint\ndescription: x\n---\nLint.\n")
    git(scaffold, "add", "-A")
    git(scaffold, "commit", "-qm", "ships lint")
    sha = git(scaffold, "rev-parse", "HEAD")
    write(run["root"], ".agent-eval/skills/lint/SKILL.md", "---\nname: lint\ndescription: x\n---\nLint.\n")
    _commit_suite(run["root"], sha, _raw(repos={"sc": f"{scaffold}@{sha}"}, tasks=[{"id": "fix_add", "repo": "sc"}]))
    with pytest.raises(SystemExit) as exc:
        run["invoke"]("--suite", ".agent-eval/suite.yaml", "--task", "fix_add", "--task", "review_add",
                      "--arm", "guidance:raw+skill:skills/lint")
    assert exc.value.code == 1
    assert "the base of task fix_add already has .claude/skills/lint" in capsys.readouterr().err
    assert not list((run["out"] / "b1").glob("*__*/result.json"))


def test_a_malformed_mcp_config_in_a_scaffold_base_is_refused_before_any_cell(run):
    profile = run["tmp"] / "fake.yaml"  # a harness that passes the repository's .mcp.json to the agent
    profile.write_text(yaml.safe_dump({**PROFILE, "args_if_present": {".mcp.json": ["--mcp-config", ".mcp.json"]}}),
                       encoding="utf-8")
    scaffold, _ = _scaffold(run["tmp"])
    write(scaffold, ".mcp.json", "{")
    git(scaffold, "add", "-A")
    git(scaffold, "commit", "-qm", "bad mcp")
    sha = git(scaffold, "rev-parse", "HEAD")
    _commit_suite(run["root"], sha, _raw(repos={"sc": f"{scaffold}@{sha}"}, tasks=[{"id": "fix_add", "repo": "sc"}]))
    with pytest.raises(InputError) as exc:  # cli.main exits 2 on it, a usage error
        run["invoke"]("--suite", ".agent-eval/suite.yaml", "--task", "fix_add", "--arm", "guidance:none")
    assert "not valid JSON" in str(exc.value)
    assert "--tools read/none" in str(exc.value)
    assert not list((run["out"] / "b1").glob("*__*/result.json"))


def test_an_empty_suite_path_is_refused_by_repo_run_and_not_read_as_no_suite(run, capsys):
    _commit_suite(run["root"], None, _raw(repos={"sc": f"file://{run['tmp']}/missing@{'c' * 40}"},
                                          tasks=[{"id": "fix_add", "repo": "sc"}]))
    with pytest.raises(SystemExit) as exc:
        run["invoke"]("--suite", "", "--task", "review_add", "--arm", "guidance:none")
    assert exc.value.code == 1
    assert not (run["out"] / "b1" / "review_add__guidance-none__m-1__r1/result.json").exists()


def test_an_empty_suite_path_is_refused_by_self_check_too(tmp_path, monkeypatch, capsys):
    root, _ = make_repo(tmp_path)
    monkeypatch.setattr("dokimi.repos.container.DockerRunner", lambda *a, **k: FakeRunner())
    out = tmp_path / "check-out"
    args = build_parser().parse_args(["eval", "check", f"{root}@HEAD", "--out", str(out), "--cache-root",
                                      str(out / "caches"), "--suite", "", "--task", "review_add"])
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 1
    assert not (out / "check/check.json").exists()


def test_a_suite_gap_with_egress_does_not_abort_the_batch_under_no_network(run, capsys):
    task = run["root"] / ".agent-eval/tasks/fix_add/task.json"
    write(run["root"], ".agent-eval/tasks/fix_add/task.json",
          json.dumps({**json.loads(task.read_text(encoding="utf-8")), "egress": ["example.com"]}))
    gone = f"file://{run['tmp']}/missing@{'c' * 40}"
    _commit_suite(run["root"], None, _raw(repos={"sc": gone}, tasks=[{"id": "fix_add", "repo": "sc"}]))
    run["invoke"]("--suite", ".agent-eval/suite.yaml", "--task", "fix_add", "--task", "review_add",
                  "--arm", "guidance:none")
    batch = run["out"] / "b1"
    unsupported = json.loads((batch / "fix_add__guidance-none__m-1__r1/unsupported.json").read_text(encoding="utf-8"))
    assert unsupported["task"] == "fix_add"
    assert (batch / "review_add__guidance-none__m-1__r1/result.json").is_file()


def test_a_moved_hash_shaped_floating_branch_is_resolved_again_not_from_the_warm_cache(tmp_path):
    root, first = _scaffold(tmp_path)
    git(root, "branch", "abcdef0", first)
    suite = parse_suite(_raw(repos={"sc": f"file://{root}@abcdef0"}), allow_floating=True)
    cache = tmp_path / "cache"
    assert resolve_suite(suite, {"t1": first}, cache).pins == {"sc": first}  # warms the cache at the branch
    write(root, "src/more.py", "X = 1\n")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "moved")
    moved = git(root, "rev-parse", "HEAD")
    git(root, "branch", "-f", "abcdef0", moved)
    found = resolve_suite(suite, {"t1": moved}, cache)
    assert found.pins == {"sc": moved} and found.gaps == {}


def test_a_parent_base_the_warm_cache_of_a_remote_lacks_is_fetched_not_a_gap(tmp_path):
    root, sha = _scaffold(tmp_path)
    suite = parse_suite(_raw(repos={"sc": f"file://{root}@{sha}"}))
    cache = tmp_path / "cache"
    assert resolve_suite(suite, {"t1": sha}, cache).gaps == {}  # warms the cache at the pin
    git(root, "checkout", "-q", "-b", "other")
    for name in ("parent", "child"):
        write(root, "src/more.py", f"X = '{name}'\n")
        git(root, "add", "-A")
        git(root, "commit", "-qm", name)
    child = git(root, "rev-parse", "HEAD")
    found = resolve_suite(suite, {"t1": f"{child}^"}, cache)
    assert found.gaps == {} and "t1" in found.git_dirs


@pytest.mark.parametrize("suffix", ["", "^"])
def test_an_abbreviated_base_the_warm_cache_lacks_is_fetched_not_a_gap(tmp_path, suffix):
    root, sha = _scaffold(tmp_path)
    suite = parse_suite(_raw(repos={"sc": f"file://{root}@{sha}"}))
    cache = tmp_path / "cache"
    assert resolve_suite(suite, {"t1": sha}, cache).gaps == {}  # warms the cache at the pin
    git(root, "checkout", "-q", "-b", "other")
    write(root, "src/more.py", "X = 1\n")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "base on another branch")
    short = git(root, "rev-parse", "--short=7", "HEAD")
    found = resolve_suite(suite, {"t1": f"{short}{suffix}"}, cache)
    assert found.gaps == {} and "t1" in found.git_dirs


def test_a_short_base_fetch_leaves_the_full_clone_without_a_partial_filter(tmp_path):
    root, sha = _scaffold(tmp_path)
    git(root, "config", "uploadpack.allowFilter", "true")
    suite = parse_suite(_raw(repos={"sc": f"file://{root}@{sha}"}))
    cache = tmp_path / "cache"
    assert resolve_suite(suite, {"t1": sha}, cache).gaps == {}  # warms the full clone at the pin
    git(root, "checkout", "-q", "-b", "other")
    write(root, "src/more.py", "X = 1\n")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "base on another branch")
    short = git(root, "rev-parse", "--short=7", "HEAD")
    found = resolve_suite(suite, {"t1": short}, cache)
    assert found.gaps == {} and "t1" in found.git_dirs
    promisor = subprocess.run(["git", "-C", str(found.git_dirs["t1"]), "config", "--get", "remote.origin.promisor"],
                              capture_output=True, text=True, check=False)
    assert promisor.returncode != 0  # the full clone is not a partial clone


@pytest.mark.parametrize("floating", [False, True])
def test_a_full_sha_pin_is_recorded_even_when_its_repository_is_down(tmp_path, floating):
    good, sha = _scaffold(tmp_path)
    gone = "b" * 40
    suite = parse_suite(_raw(repos={"good": f"{good}@{sha}", "gone": f"{tmp_path}/missing@{gone}"},
                             tasks=[{"id": "ok", "repo": "good"}, {"id": "lost", "repo": "gone"}]),
                        allow_floating=floating)
    found = resolve_suite(suite, {"ok": sha, "lost": gone}, tmp_path / "cache")
    assert found.pins == {"good": sha, "gone": gone}


def test_a_floating_full_sha_pin_on_no_branch_is_fetched_by_name(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_NO_LAZY_FETCH", "1")  # else the partial clone fetches the commit on its own
    root, sha = _scaffold(tmp_path)
    git(root, "checkout", "-q", "-b", "gone")
    write(root, "src/more.py", "X = 1\n")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "on a branch that is deleted")
    pin = git(root, "rev-parse", "HEAD")
    git(root, "checkout", "-q", "main")
    git(root, "branch", "-D", "gone")  # the pinned commit is on no branch or tag any more
    suite = parse_suite(_raw(repos={"sc": f"file://{root}@{pin}"}), allow_floating=True)
    found = resolve_suite(suite, {"t1": pin}, tmp_path / "cache")
    assert found.pins == {"sc": pin} and found.gaps == {}


def test_a_moved_full_length_floating_branch_is_resolved_again_in_a_sha256_scaffold(tmp_path):
    root = tmp_path / "sc256"
    write(root, "src/calc.py", CALC)
    git(root, "init", "-q", "-b", "main", "--object-format=sha256")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "scaffold")
    first = git(root, "rev-parse", "HEAD")
    name = "a" * 40  # a branch in a SHA-256 repository: its spelling is not a commit
    git(root, "branch", name, first)
    suite = parse_suite(_raw(repos={"sc": f"file://{root}@{name}"}), allow_floating=True)
    cache = tmp_path / "cache"
    assert resolve_suite(suite, {"t1": first}, cache).pins == {"sc": first}  # warms the cache at the branch
    write(root, "src/more.py", "X = 1\n")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "moved")
    moved = git(root, "rev-parse", "HEAD")
    git(root, "branch", "-f", name, moved)
    found = resolve_suite(suite, {"t1": moved}, cache)
    assert found.pins == {"sc": moved} and found.gaps == {}
