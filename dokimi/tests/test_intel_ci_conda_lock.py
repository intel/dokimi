# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""ci/conda-lock/: Intel CI's conda test environments come from the locks on a PR or a main push, and are current."""

from __future__ import annotations

import argparse
import ast
import datetime
import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest
import yaml

_REPO = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(not (_REPO / "ci" / "lock.py").is_file(),
                                reason="not a checkout: ci/ is next to the project")
_LINE = re.compile(r"https://conda\.anaconda\.org/conda-forge/(noarch|linux-64)/[^/#\s]+\.(conda|tar\.bz2)#[0-9a-f]{64}")


def _script(name: str, monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    monkeypatch.syspath_prepend(str(_REPO / "ci"))
    spec = importlib.util.spec_from_file_location(f"ci_{name}", _REPO / "ci" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _lock_on(host: str, monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """ci/lock.py loaded on a host whose subdir is host: hooks is patched before lock.py reads it."""
    hooks = _script("hooks", monkeypatch)
    monkeypatch.setattr(hooks, "HOST_SUBDIR", host)
    monkeypatch.setitem(sys.modules, "hooks", hooks)
    return _script("lock", monkeypatch)


def _versions(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    lock = _script("lock", monkeypatch)
    return [p.version for p in lock.matrix.declared("conda-build", lock.recipe())]


def test_each_declared_python_has_a_lock_solved_from_todays_specs(monkeypatch: pytest.MonkeyPatch) -> None:
    """A PR that changes the recipe's requirements or ci/config.yml's test list must re-run ci/lock.py."""
    lock = _script("lock", monkeypatch)
    versions = _versions(monkeypatch)
    assert versions
    paths = {v: lock.hooks.lock_path(v, lock.PLATFORM) for v in versions}
    stale = {v: lock.specs(v) for v, path in paths.items()
             if not path.is_file() or lock.locked_specs(path) != lock.specs(v)}
    assert stale == {}
    assert sorted(p.name for p in lock.hooks.CONDA_LOCKS.glob("*.txt")) == sorted(path.name for path in paths.values())


def _generator(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, declared: list[str]) -> types.ModuleType:
    """ci/lock.py on a Windows machine, with the declared versions and the solver stubbed, writing under tmp_path."""
    lock = _lock_on("win-64", monkeypatch)
    monkeypatch.setattr(lock.hooks, "CONDA_LOCKS", tmp_path / "ci" / "conda-lock")
    monkeypatch.setattr(lock.hooks, "ROOT", tmp_path)
    monkeypatch.setattr(lock, "recipe", lambda: tmp_path / "meta.yaml")
    monkeypatch.setattr(lock.matrix, "declared", lambda system, selector: [types.SimpleNamespace(version=v) for v in declared])
    monkeypatch.setattr(lock, "specs", lambda version: [f"python {version}.*"])
    monkeypatch.setattr(lock, "solve", lambda wanted, min_age_days: [])
    return lock


def test_the_locks_are_for_linux_64_whatever_machine_writes_them(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run from Windows or macOS, ci/lock.py still solves, labels and names the files for the Linux runners."""
    lock = _generator(tmp_path, monkeypatch, ["3.12"])
    assert lock.main([]) == 0
    written = sorted(p.name for p in (tmp_path / "ci" / "conda-lock").iterdir())
    assert written == ["linux-64-py3.12.txt"]
    assert "# platform: linux-64\n" in (tmp_path / "ci" / "conda-lock" / written[0]).read_text(encoding="utf-8")


def test_a_lock_written_on_windows_has_lf_line_ends(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Text mode writes os.linesep; a CRLF lock would reach the Linux runners' conda as is."""
    lock = _generator(tmp_path, monkeypatch, ["3.12"])
    real = Path.write_text

    def windows_write_text(self: Path, data: str, encoding: str | None = None, errors: str | None = None,
                           newline: str | None = None) -> int:
        """Text mode on Windows: newline=None turns each \\n into \\r\\n."""
        return real(self, data.replace("\n", "\r\n") if newline is None else data, encoding, errors, newline)

    monkeypatch.setattr(Path, "write_text", windows_write_text)
    assert lock.main([]) == 0
    data = (tmp_path / "ci" / "conda-lock" / "linux-64-py3.12.txt").read_bytes()
    assert data.endswith(b"@EXPLICIT\n") and b"\r" not in data


def _stub_rattler(solve, monkeypatch: pytest.MonkeyPatch) -> None:
    rattler = types.ModuleType("rattler")
    for name in ("GenericVirtualPackage", "PackageName", "Version"):
        setattr(rattler, name, lambda *args: args)
    rattler.solve = solve  # type: ignore[attr-defined]
    build_string = types.ModuleType("rattler.package.build_string")
    build_string.BuildString = lambda build: build  # type: ignore[attr-defined]
    for name, module in (("rattler", rattler), ("rattler.package", types.ModuleType("rattler.package")),
                         ("rattler.package.build_string", build_string)):
        monkeypatch.setitem(sys.modules, name, module)


def test_the_solve_targets_linux_64_whatever_machine_runs_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """py-rattler is asked for linux-64 and noarch records, not the machine's subdir, from builds 7 days old."""
    calls: list[dict] = []

    async def solve(channels: list[str], specs: list[str], **options: object) -> list:
        calls.append(options)
        return [types.SimpleNamespace(name=types.SimpleNamespace(normalized="python"))]

    _stub_rattler(solve, monkeypatch)
    lock = _lock_on("osx-64", monkeypatch)
    assert len(lock.solve(["python 3.12.*"], 7)) == 1
    assert [call["platforms"] for call in calls] == [["linux-64", "noarch"]]
    assert calls[0]["exclude_newer"] == datetime.timedelta(days=7)


def test_an_empty_solve_is_a_lock_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A lock with no packages would pass the specs check yet install nothing."""
    async def solve(channels: list[str], specs: list[str], **options: object) -> list:
        return []

    _stub_rattler(solve, monkeypatch)
    lock = _script("lock", monkeypatch)
    with pytest.raises(lock.LockError, match=r"^the solve of \['python 3\.12\.\*'\] returned no packages$"):
        lock.solve(["python 3.12.*"], 7)


def test_a_missing_input_prints_a_lock_line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without ci/config.yml, ci/lock.py exits 1 with a lock: line, not a traceback."""
    shutil.copytree(_REPO / "ci", tmp_path / "ci", ignore=shutil.ignore_patterns("config.yml", "conda-lock"))
    shutil.copytree(_REPO / "conda-recipe", tmp_path / "conda-recipe")
    # ci/test.py sets PYTHONSAFEPATH=1, which drops ci/ from sys.path and so the sibling hooks import
    monkeypatch.delenv("PYTHONSAFEPATH", raising=False)
    result = subprocess.run([sys.executable, str(tmp_path / "ci" / "lock.py"), "--python", "3.12"],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 1
    assert result.stderr.startswith("lock: ") and "config.yml" in result.stderr and "Traceback" not in result.stderr


@pytest.mark.skipif(sys.platform == "win32", reason="runs the step's bash")
def test_the_refresh_stops_without_conda_lock_token(tmp_path: Path) -> None:
    """A refresh PR opened with GITHUB_TOKEN gets no Intel CI run, so the step fails rather than open one."""
    workflow = yaml.safe_load((_REPO / ".github" / "workflows" / "conda-lock.yml").read_text(encoding="utf-8"))
    step = next(s for s in workflow["jobs"]["refresh"]["steps"] if s.get("name") == "Open or update the refresh PR")
    assert step["env"]["GH_TOKEN"] == "${{ secrets.CONDA_LOCK_TOKEN }}"
    done = subprocess.run(["/bin/bash", "-e", "-c", step["run"]], env={"PATH": "/usr/bin:/bin", "GH_TOKEN": ""},
                          capture_output=True, text=True, check=False)
    assert done.returncode == 1 and "CONDA_LOCK_TOKEN is not set" in done.stdout


def test_a_failed_solve_is_a_lock_error_naming_the_specs(monkeypatch: pytest.MonkeyPatch) -> None:
    """The __main__ block prints a LockError as one line; a solver exception would be a traceback."""
    async def solve(channels: list[str], specs: list[str], **options: list[str]) -> list:
        raise RuntimeError("cannot solve")

    _stub_rattler(solve, monkeypatch)
    lock = _script("lock", monkeypatch)
    with pytest.raises(lock.LockError, match=r"^the solve of \['python 3\.12\.\*'\] failed: cannot solve$"):
        lock.solve(["python 3.12.*"], 7)


def test_a_full_run_removes_the_locks_of_versions_no_longer_declared(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The directory must hold exactly the declared versions' locks; a run for some versions leaves the others."""
    lock = _generator(tmp_path, monkeypatch, ["3.12"])
    directory = tmp_path / "ci" / "conda-lock"
    directory.mkdir(parents=True)
    for name in ("linux-64-py3.11.txt", "linux-64-py3.12.txt", "osx-64-py3.11.txt"):
        (directory / name).write_text("old\n", encoding="utf-8")
    assert lock.main(["--python", "3.12"]) == 0
    assert (directory / "linux-64-py3.11.txt").is_file()
    assert lock.main([]) == 0
    assert sorted(p.name for p in directory.iterdir()) == ["linux-64-py3.12.txt", "osx-64-py3.11.txt"]


def test_a_lock_lists_hashed_conda_forge_builds_of_its_python_and_not_the_package(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """The artifact under test is installed on top of the lock; a dokimi build in it would shadow that."""
    hooks = _script("hooks", monkeypatch)
    monkeypatch.setattr(hooks, "HOST_SUBDIR", "win-64")  # as on Windows: the committed locks are linux-64 anyway
    for version in _versions(monkeypatch):
        lines = hooks.lock_path(version, "linux-64").read_text(encoding="utf-8").splitlines()
        packages = lines[lines.index("@EXPLICIT") + 1:]
        assert [line for line in packages if not _LINE.fullmatch(line)] == []
        names = [line.rsplit("/", 1)[1].rsplit("-", 2)[0] for line in packages]
        assert "dokimi" not in names and len(names) == len(set(names))
        python = next(line for line in packages if line.rsplit("/", 1)[1].startswith("python-3."))
        assert f"/python-{version}." in python


def _args(tmp_path: Path, event: str) -> argparse.Namespace:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "dokimi-0.2.0-pyh01cfe5d_0.conda").write_bytes(b"package")
    return argparse.Namespace(kind="conda", python="3.12", artifacts=artifacts, work=tmp_path / "work", event=event,
                              commit="")


def _record_conda(hooks: types.ModuleType, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    """hooks as on the linux-64 runner, whatever machine runs the test, with conda and the tests recorded."""
    monkeypatch.setattr(hooks, "HOST_SUBDIR", "linux-64")
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(hooks, "conda", lambda *args: calls.append(args))
    monkeypatch.setattr(hooks, "stage_channel", lambda packages, channel: None)
    monkeypatch.setattr(hooks, "run_recipe_tests", lambda env_dir: calls.append(("recipe tests", str(env_dir))))
    return calls


@pytest.mark.parametrize("event", ["pr", "push"])
def test_a_pr_or_push_creates_the_env_from_the_lock_then_adds_the_artifact(
        event: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No solve against today's conda-forge: create --file the lock, install the artifact --no-deps, run its tests."""
    hooks = _script("hooks", monkeypatch)
    calls = _record_conda(hooks, monkeypatch)
    monkeypatch.setattr(hooks, "unmet_depends", lambda env_dir, packages: [])
    args = _args(tmp_path, event)
    env = hooks.install(args)
    package = str(args.artifacts / "dokimi-0.2.0-pyh01cfe5d_0.conda")
    assert calls == [
        ("create", "-y", "-q", "-p", str(env), "--file", str(hooks.lock_path("3.12"))),
        ("install", "-y", "-q", "-p", str(env), "--no-deps", package),
        ("recipe tests", str(env)),
    ]


@pytest.mark.parametrize("event", ["tag", "schedule"])
def test_a_tag_or_the_schedule_resolves_the_latest_conda_forge_builds(
        event: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """These test what `conda install dokimi` gets today, so they solve the specs and leave the lock alone."""
    hooks = _script("hooks", monkeypatch)
    calls = _record_conda(hooks, monkeypatch)
    hooks.install(_args(tmp_path, event))
    assert len(calls) == 1 and calls[0][0] == "create"
    create = calls[0]
    assert "--file" not in create
    assert create[create.index("conda-forge") + 1:] == (
        "python 3.12.*", *hooks.test_requirements()[0], "dokimi=0.2.0=pyh01cfe5d_0")


def test_a_lock_that_misses_a_dependency_of_the_artifact_stops_before_the_tests(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A recipe change the lock does not follow fails with what is unmet and how to refresh it."""
    hooks = _script("hooks", monkeypatch)
    calls = _record_conda(hooks, monkeypatch)
    monkeypatch.setattr(hooks, "unmet_depends",
                        lambda env_dir, packages: ["dokimi-0.2.0-pyh01cfe5d_0.conda needs regex >=2024, has none"])
    with pytest.raises(hooks.HookError) as error:
        hooks.install(_args(tmp_path, "pr"))
    assert str(error.value) == ("ci/conda-lock/linux-64-py3.12.txt does not satisfy "
                                "dokimi-0.2.0-pyh01cfe5d_0.conda needs regex >=2024, has none: run python ci/lock.py")
    assert [call[0] for call in calls] == ["create", "install"]


def test_unmet_depends_reports_each_dependency_the_env_does_not_match(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The artifact's depends go to the conda check; what it finds unmet comes back as needs/has lines."""
    hooks = _script("hooks", monkeypatch)
    package = tmp_path / "dokimi-0.2.0-pyh01cfe5d_0.conda"
    monkeypatch.setattr(hooks, "package_record", lambda path: (
        "noarch", {"depends": ["python >=3.11", "__glibc >=2.17", "regex >=2024", "__unix"]}))
    checked: list[list[str]] = []

    def run(command: list[str], **options: object) -> subprocess.CompletedProcess:
        checked.append(json.loads(command[-1]))
        return subprocess.CompletedProcess(command, 0, json.dumps([["regex >=2024", "none"]]), "")

    monkeypatch.setattr(hooks.subprocess, "run", run)
    assert hooks.unmet_depends(tmp_path, [package]) == [
        "dokimi-0.2.0-pyh01cfe5d_0.conda needs regex >=2024, has none"]
    assert checked == [["python >=3.11", "regex >=2024"]]


def test_the_recipe_tests_refuse_a_rattler_build_recipe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A recipe.yaml has no `test:` key to read, so running nothing would pass a pull request untested."""
    hooks = _script("hooks", monkeypatch)
    (tmp_path / "recipe.yaml").write_text("tests: []\n", encoding="utf-8")
    monkeypatch.setattr(hooks, "ROOT", tmp_path)
    monkeypatch.setattr(hooks, "run", lambda command, **options: pytest.fail("ran a test command"))
    with pytest.raises(hooks.HookError, match=r"^the recipe tests of a rattler-build recipe are not run: only conda-build is supported$"):
        hooks.run_recipe_tests(tmp_path / "env")


def test_a_host_other_than_linux_64_cannot_install_the_locks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The locks are linux-64 explicit lists, so another host stops before any conda command."""
    hooks = _script("hooks", monkeypatch)
    calls = _record_conda(hooks, monkeypatch)
    monkeypatch.setattr(hooks, "HOST_SUBDIR", "win-64")
    with pytest.raises(hooks.HookError, match=r"^the conda locks are linux-64 only; win-64 cannot install them$"):
        hooks.install_locked(tmp_path / "env", [], "3.12")
    assert calls == []


def test_a_missing_lock_names_the_command_that_writes_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hooks = _script("hooks", monkeypatch)
    calls = _record_conda(hooks, monkeypatch)
    monkeypatch.setattr(hooks, "CONDA_LOCKS", tmp_path / "none")
    monkeypatch.setattr(hooks, "ROOT", tmp_path)
    with pytest.raises(hooks.HookError, match=r"^none/linux-64-py3\.12\.txt is missing: run python ci/lock\.py$"):
        hooks.install_locked(tmp_path / "env", [], "3.12")
    assert calls == []


@pytest.mark.parametrize(("system", "skip"), [("conda-build", ["--no-test"]), ("rattler-build", ["--test", "skip"])])
def test_build_skips_the_recipe_tests_only_when_asked(system: str, skip: list[str], tmp_path: Path,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    build = _script("build", monkeypatch)
    python = build.matrix.Python("3.12", "3.12")
    tested, untested = (build.commands(system, tmp_path / "meta.yaml", python, ["conda-forge"], tmp_path, test=t)[0]
                        for t in (True, False))
    assert untested == [*tested, *skip]


def test_the_refresh_workflow_solves_main_whatever_ref_a_manual_run_selects() -> None:
    """The branch is cut from the checkout and its PR is based on main, so the checkout must be main."""
    workflow = yaml.safe_load((_REPO / ".github" / "workflows" / "conda-lock.yml").read_text(encoding="utf-8"))
    checkout = next(s for s in workflow["jobs"]["refresh"]["steps"] if s.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"]["ref"] == "main"


@pytest.mark.skipif(sys.platform == "win32", reason="runs the step's bash with stub git and gh")
@pytest.mark.parametrize("open_pr", ["42", ""])
def test_current_locks_close_an_open_refresh_pr_and_push_nothing(open_pr: str, tmp_path: Path) -> None:
    """When a fresh solve matches main, the refresh PR left open proposes stale locks and is closed."""
    workflow = yaml.safe_load((_REPO / ".github" / "workflows" / "conda-lock.yml").read_text(encoding="utf-8"))
    step = next(s for s in workflow["jobs"]["refresh"]["steps"] if s.get("name") == "Open or update the refresh PR")
    log = tmp_path / "calls.txt"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "git").write_text(f'#!/bin/sh\necho "git $*" >> {log}\nexit 0\n', encoding="utf-8")
    (bin_dir / "gh").write_text(f'#!/bin/sh\necho "gh $*" >> {log}\n[ "$2" = list ] && echo "{open_pr}"\nexit 0\n',
                                encoding="utf-8")
    for tool in bin_dir.iterdir():
        tool.chmod(0o755)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "RUN_URL": "https://example.invalid/run/1", "GH_TOKEN": "token"}
    done = subprocess.run(["/bin/bash", "-e", "-c", step["run"]], env=env, capture_output=True, text=True, check=False)
    calls = log.read_text(encoding="utf-8").splitlines()
    assert done.returncode == 0 and "The locks are current." in done.stdout
    closes = [call for call in calls if call.startswith("gh pr close")]
    if open_pr:
        assert len(closes) == 1 and closes[0].startswith("gh pr close 42 --delete-branch --comment ")
    else:
        assert closes == []
    assert not [call for call in calls if call.split()[1] in ("push", "commit", "switch")]
    assert not [call for call in calls if call.startswith("gh pr create")]


@pytest.mark.skipif(sys.platform == "win32" or shutil.which("git") is None, reason="runs the step's check with git")
@pytest.mark.parametrize(("change", "current"), [("none", True), ("modified", False), ("new", False)])
def test_the_refresh_step_takes_a_new_or_changed_lock_for_a_change(change: str, current: bool, tmp_path: Path) -> None:
    """A lock written for a newly declared Python is untracked; the step's check must still see it."""
    workflow = yaml.safe_load((_REPO / ".github" / "workflows" / "conda-lock.yml").read_text(encoding="utf-8"))
    step = next(s for s in workflow["jobs"]["refresh"]["steps"] if s.get("name") == "Open or update the refresh PR")
    check = re.search(r"^\s*if (.*git status.+); then$", step["run"], re.M)
    assert check, step["run"]
    locks = tmp_path / "ci" / "conda-lock"
    locks.mkdir(parents=True)
    (locks / "linux-64-py3.12.txt").write_text("a\n", encoding="utf-8")
    git = ["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "locks"]):
        subprocess.run([*git, *args], check=True, capture_output=True)
    if change == "modified":
        (locks / "linux-64-py3.12.txt").write_text("b\n", encoding="utf-8")
    elif change == "new":
        (locks / "linux-64-py3.15.txt").write_text("c\n", encoding="utf-8")
    done = subprocess.run(["/bin/bash", "-c", f"if {check.group(1)}; then exit 0; else exit 1; fi"],
                          cwd=tmp_path, capture_output=True, text=True, check=False)
    assert (done.returncode == 0) is current, done.stderr


@pytest.mark.skipif(sys.platform == "win32" or shutil.which("jq") is None, reason="the stub gh applies --jq with jq")
@pytest.mark.parametrize(("diff_rc", "fork", "closed", "created"),
                         [(0, True, False, False), (1, True, False, True), (0, False, True, False), (1, False, False, False)])
def test_only_a_same_repo_refresh_pr_counts_as_open(diff_rc: int, fork: bool, closed: bool, created: bool,
                                                    tmp_path: Path) -> None:
    """A fork PR from a branch of the same name is neither closed nor taken for the bot's PR."""
    workflow = yaml.safe_load((_REPO / ".github" / "workflows" / "conda-lock.yml").read_text(encoding="utf-8"))
    step = next(s for s in workflow["jobs"]["refresh"]["steps"] if s.get("name") == "Open or update the refresh PR")
    log, prs, bin_dir = tmp_path / "calls.txt", tmp_path / "prs.json", tmp_path / "bin"
    spaced = tmp_path / "jq dir"  # a jq installed under a path with a space must still run
    spaced.mkdir()
    jq = spaced / "jq"
    jq.symlink_to(str(shutil.which("jq")))
    prs.write_text(f'[{{"number": 701, "isCrossRepository": {str(fork).lower()}}}]', encoding="utf-8")
    bin_dir.mkdir()
    status = "echo ' M ci/conda-lock/linux-64-py3.12.txt'" if diff_rc else ":"  # a changed lock, or none
    (bin_dir / "git").write_text(
        f'#!/bin/sh\necho "git $*" >> {shlex.quote(str(log))}\n[ "$1" = status ] && {status}\nexit 0\n',
        encoding="utf-8")
    (bin_dir / "gh").write_text(
        f'#!/bin/sh\necho "gh $*" >> {shlex.quote(str(log))}\n[ "$2" = list ] || exit 0\n'
        f'while [ $# -gt 0 ]; do [ "$1" = --jq ] && filter=$2; shift; done\n'
        f'{shlex.quote(str(jq))} -r "$filter" {shlex.quote(str(prs))}\n', encoding="utf-8")
    for tool in bin_dir.iterdir():
        tool.chmod(0o755)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "RUN_URL": "https://example.invalid/run/1", "GH_TOKEN": "token"}
    done = subprocess.run(["/bin/bash", "-e", "-c", step["run"]], env=env, capture_output=True, text=True, check=False)
    calls = log.read_text(encoding="utf-8").splitlines()
    assert done.returncode == 0, done.stderr
    assert [c for c in calls if c.startswith("gh pr close")] == (["gh pr close 701 --delete-branch --comment "
                                                                  f"Closed by {env['RUN_URL']}: main's locks already match a "
                                                                  "fresh solve, so this refresh is obsolete."] if closed else [])
    assert any(c.startswith("gh pr create") for c in calls) is created


@pytest.mark.skipif(sys.platform == "win32" or shutil.which("jq") is None, reason="the stub gh applies --jq with jq")
@pytest.mark.parametrize(("workflow", "job"), [("intel-ci.yml", "report"), ("pip-audit.yml", "report")])
@pytest.mark.parametrize(("issues", "expected"), [("[]", "gh issue create"), ('[{"number": 9}]', "gh issue comment 9")])
def test_a_failed_scheduled_run_opens_an_issue_when_none_is_open(workflow: str, job: str, issues: str, expected: str,
                                                                 tmp_path: Path) -> None:
    """With no open issue gh's .[0].number is null, which must open an issue rather than comment on null."""
    steps = yaml.safe_load((_REPO / ".github" / "workflows" / workflow).read_text(encoding="utf-8"))["jobs"][job]["steps"]
    step = next(s for s in steps if s.get("name") == "Open or update the issue")
    log, found, bin_dir = tmp_path / "calls.txt", tmp_path / "issues.json", tmp_path / "bin"
    found.write_text(issues, encoding="utf-8")
    bin_dir.mkdir()
    (bin_dir / "gh").write_text(
        f'#!/bin/sh\necho "gh $*" >> {shlex.quote(str(log))}\n[ "$2" = list ] || exit 0\n'
        f'while [ $# -gt 0 ]; do [ "$1" = --jq ] && filter=$2; shift; done\n'
        f'{shlex.quote(str(shutil.which("jq")))} -r "$filter" {shlex.quote(str(found))}\n', encoding="utf-8")
    (bin_dir / "gh").chmod(0o755)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "RUN_URL": "https://example.invalid/run/1"}
    done = subprocess.run(["/bin/bash", "-e", "-c", step["run"]], env=env, capture_output=True, text=True, check=False)
    calls = log.read_text(encoding="utf-8").splitlines()
    assert done.returncode == 0, done.stderr
    assert [c.split(" --")[0] for c in calls if not c.startswith("gh issue list")] == [expected]


def test_the_workflow_takes_the_latest_releases_on_a_tag_or_the_schedule_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """The build's recipe tests, both test jobs' --event and hooks.py's FLOATING agree; a failed schedule reports."""
    hooks = _script("hooks", monkeypatch)
    workflow = yaml.safe_load((_REPO / ".github" / "workflows" / "intel-ci.yml").read_text(encoding="utf-8"))
    jobs = workflow["jobs"]
    events = {step["env"]["EVENT"] for name in ("test", "test-wheel") for step in jobs[name]["steps"]
              if "EVENT" in step.get("env", {})}
    assert events == {"${{ startsWith(github.ref, 'refs/tags/') && 'tag' || github.event_name == 'schedule' && "
                      "'schedule' || github.event_name == 'pull_request' && 'pr' || 'push' }}"}
    assert hooks.FLOATING == ("tag", "schedule")
    build = next(step for step in jobs["build"]["steps"] if "LATEST" in step.get("env", {}))
    assert build["env"]["LATEST"] == "${{ startsWith(github.ref, 'refs/tags/') || github.event_name == 'schedule' }}"
    assert build["run"].count("--no-test") == 1 and build["run"].index("--no-test") > build["run"].index("else")
    assert workflow[True]["schedule"] and jobs["report"]["if"] == "${{ failure() && github.event_name == 'schedule' }}"
    assert set(jobs["report"]["needs"]) == {"build", "test", "build-wheel", "test-wheel"}


@pytest.mark.parametrize("event", ["pr", "push", "tag", "schedule"])
def test_a_wheel_installs_on_the_hashed_lock_unless_a_tag_or_the_schedule(
        event: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A PR or push puts the wheel --no-deps on requirements.lock.txt; only a tag or the schedule floats."""
    hooks = _script("hooks", monkeypatch)
    calls: list[list[str]] = []
    monkeypatch.setattr(hooks, "run", lambda command, **options: calls.append(command))
    monkeypatch.setattr(hooks, "conda_create", lambda env_dir, *specs, channels=(): None)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    wheel = artifacts / "dokimi-0.2.0-py3-none-any.whl"
    wheel.write_bytes(b"wheel")
    env = hooks.install(argparse.Namespace(kind="wheel", python="3.12", artifacts=artifacts, work=tmp_path / "work",
                                           event=event, commit=""))
    pip = [str(hooks.env_python(env)), "-m", "pip", "install", "--no-input"]
    locked = event in ("pr", "push")
    assert [c for c in calls if "--require-hashes" in c] == ([[*pip, "--no-deps", "--require-hashes", "-r",
                                                               str(hooks.LOCK)]] if locked else [])
    installs = [c for c in calls if str(wheel) in c]
    assert len(installs) == 1 and ("--no-deps" in installs[0]) is locked


def test_the_recipe_tests_run_its_imports_and_commands_in_the_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With the build's --no-test, these are the only place a PR's conda job runs the recipe's own tests."""
    hooks = _script("hooks", monkeypatch)
    calls: list[list[str]] = []
    paths: list[str] = []
    leaked: list[bool] = []
    cwds: list[str] = []

    def record(command: list[str], **options: dict) -> None:
        calls.append(command)
        paths.append(options["env"]["PATH"])
        leaked.append("CI_PROOF_RUNNER_TOKEN" in options["env"])
        cwds.append(str(options.get("cwd")))
        assert os.listdir(options["cwd"]) == []

    monkeypatch.setattr(hooks, "run", record)
    monkeypatch.setenv("PATH", "/runner/bin")
    monkeypatch.setenv("CI_PROOF_RUNNER_TOKEN", "secret")
    hooks.run_recipe_tests(tmp_path)
    assert calls == [[str(hooks.env_python(tmp_path)), "-c", "import dokimi"], ["pip", "check"], ["dokimi", "--help"]]
    expected = os.pathsep.join([*map(str, hooks.env_path(tmp_path)), "/runner/bin"])
    assert paths == [expected] * 3
    assert leaked == [False] * 3
    assert len(set(cwds)) == 1 and Path(cwds[0]) != hooks.ROOT


_RENOVATE_WORKFLOW = _REPO / ".github" / "workflows" / "conda-lock-renovate.yml"
_PUSH_STEP = "Push the re-solved locks to the branch"


def _renovate_workflow() -> dict:
    return yaml.safe_load(_RENOVATE_WORKFLOW.read_text(encoding="utf-8"))


def _push_step() -> dict:
    return next(s for s in _renovate_workflow()["jobs"]["push"]["steps"] if s.get("name") == _PUSH_STEP)


def test_the_renovate_workflow_runs_on_every_push_to_renovate_branches() -> None:
    """#682: a Renovate branch is re-solved on every push, with no path filter; a fork cannot push there, so no pull_request_target.

    A filter on the inputs of lock.py misses a rebuild onto a newer main that changes only an unrelated file, while the run in flight loses its push.
    """
    workflow = _renovate_workflow()
    triggers = workflow["on"] if "on" in workflow else workflow[True]  # YAML 1.1 reads a bare `on` as True
    assert list(triggers) == ["push"]
    assert triggers["push"] == {"branches": ["renovate/**"]}


def _stage_locks(change: str, work: Path, artifact: Path, git, branch: str) -> None:
    """The branch holds linux-64-py3.12.txt; the artifact holds what the solve job made of it, as `change` says."""
    artifact.mkdir()
    (artifact / "linux-64-py3.12.txt").write_text("a\n", encoding="utf-8")
    if change in ("modified", "no token", "rejected"):
        (artifact / "linux-64-py3.12.txt").write_text("b\n", encoding="utf-8")
    elif change == "new":
        (artifact / "linux-64-py3.13.txt").write_text("b\n", encoding="utf-8")
    elif change == "removed":  # the branch also has py3.11, which the solve dropped
        (artifact / "linux-64-py3.12.txt").unlink()
        (artifact / "linux-64-py3.11.txt").write_text("a\n", encoding="utf-8")
        (work / "ci" / "conda-lock" / "linux-64-py3.11.txt").write_text("a\n", encoding="utf-8")
        git("add", "-A")
        git("commit", "-q", "-m", "an older Python")
        git("push", "-q", "origin", branch)
    elif change == "stray":  # a symlink, a file of another name, a file in a subdirectory: none is a lock to take
        (artifact / "linux-64-py3.13.txt").symlink_to(work / "ci" / "config.yml")
        (artifact / "notes.txt").write_text("x\n", encoding="utf-8")
        (artifact / "sub").mkdir()
        (artifact / "sub" / "linux-64-py3.14.txt").write_text("x\n", encoding="utf-8")
    if change not in ("none", "removed"):
        (work / "ci" / "config.yml").write_text("y\n", encoding="utf-8")  # not a lock: must stay out of the commit


@pytest.mark.skipif(sys.platform == "win32" or shutil.which("git") is None, reason="runs the step's bash with git")
@pytest.mark.parametrize("change", ["none", "modified", "new", "removed", "stray", "no token", "rejected"])
def test_the_renovate_push_commits_only_the_locks_and_never_forces(change: str, tmp_path: Path) -> None:
    """#682: the locks the solve job handed over are pushed; current locks end the loop; no token fails (GITHUB_TOKEN starts no CI).

    The artifact is the branch's code's output: only regular files named like a lock are taken from it.
    """
    step = _push_step()
    assert step["env"]["GH_TOKEN"] == "${{ secrets.CONDA_LOCK_TOKEN }}"
    assert step["env"]["BRANCH"] == "${{ github.ref_name }}" and "${{" not in step["run"]
    git_bin, branch = str(shutil.which("git")), "renovate/mypy-2.x"
    base = {"HOME": str(tmp_path), "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
    remote, work, bin_dir, log = tmp_path / "remote.git", tmp_path / "work", tmp_path / "bin", tmp_path / "calls.txt"
    bin_dir.mkdir()
    (bin_dir / "git").write_text(f'#!/bin/sh\necho "git $*" >> {log}\nexec {git_bin} "$@"\n', encoding="utf-8")
    (bin_dir / "gh").write_text(f'#!/bin/sh\necho "gh $*" >> {log}\nexit 0\n', encoding="utf-8")
    for tool in bin_dir.iterdir():
        tool.chmod(0o755)

    def git(*args: str, cwd: Path = work) -> str:
        done = subprocess.run([git_bin, "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", *args],
                              cwd=cwd, env={**base, "PATH": "/usr/bin:/bin"}, capture_output=True, text=True, check=True)
        return done.stdout

    git("init", "-q", "--bare", str(remote), cwd=tmp_path)
    git("init", "-q", "-b", branch, str(work), cwd=tmp_path)
    git("remote", "add", "origin", str(remote))
    (work / "ci" / "conda-lock").mkdir(parents=True)
    (work / "ci" / "conda-lock" / "linux-64-py3.12.txt").write_text("a\n", encoding="utf-8")
    (work / "ci" / "config.yml").write_text("x\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "pin bump")
    git("push", "-q", "origin", branch)
    artifact = tmp_path / "locks"  # what the download step leaves: the solve job's output
    _stage_locks(change, work, artifact, git, branch)
    before = git("rev-parse", "HEAD").strip()
    if change == "rejected":
        other = tmp_path / "other"
        git("clone", "-q", "-b", branch, str(remote), str(other), cwd=tmp_path)
        (other / "moved").write_text("m\n", encoding="utf-8")
        git("add", "-A", cwd=other)
        git("commit", "-q", "-m", "Renovate rebased", cwd=other)
        git("push", "-q", "origin", f"HEAD:{branch}", cwd=other)
    env = {**base, "PATH": f"{bin_dir}:/usr/bin:/bin", "GH_TOKEN": "" if change == "no token" else "token", "BRANCH": branch,
           "LOCKS": str(artifact)}
    done = subprocess.run(["/bin/bash", "-e", "-c", step["run"]], cwd=work, env=env, capture_output=True, text=True,
                          check=False)
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    pushed = git("rev-parse", branch, cwd=remote).strip()
    if change in ("none", "stray", "no token"):
        assert done.returncode == (1 if change == "no token" else 0)
        assert [c for c in calls if c.split()[1] in ("push", "commit", "add")] == [] and (calls == [] or change != "no token")
        assert ("The locks are current." in done.stdout) is (change != "no token")
        assert sorted(p.name for p in (work / "ci" / "conda-lock").iterdir()) == ["linux-64-py3.12.txt"]
        assert (work / "ci" / "conda-lock" / "linux-64-py3.12.txt").read_text(encoding="utf-8") == "a\n"
        assert ("CONDA_LOCK_TOKEN is not set" in done.stdout) is (change == "no token")
        assert pushed == before
    elif change == "rejected":
        assert done.returncode == 1 and "was rejected" in done.stdout and branch in done.stdout
        assert len([c for c in calls if c.startswith("git push")]) == 1 and pushed != before
        assert git("log", "-1", "--format=%s", branch, cwd=remote).strip() == "Renovate rebased"
    else:
        assert done.returncode == 0, done.stderr
        pushes = [c for c in calls if c.startswith("git push")]
        assert pushes == [f"git push origin HEAD:refs/heads/{branch}"] and "gh auth setup-git" in calls
        assert git("rev-list", "--count", f"{before}..{pushed}", cwd=remote).strip() == "1"
        assert git("diff", "--name-only", before, pushed, cwd=remote).splitlines() == [
            "ci/conda-lock/" + ("linux-64-py3.13.txt" if change == "new" else "linux-64-py3.12.txt")]
        assert git("diff", "--name-status", before, pushed, cwd=remote)[0] == {"modified": "M", "new": "A", "removed": "D"}[change]
        message = git("log", "-1", "--format=%an <%ae>%n%B", branch, cwd=remote)
        assert message.startswith("github-actions[bot] <41898282+github-actions[bot]@users.noreply.github.com>\n")
        assert "ci(conda-lock): re-solve the Intel CI conda locks" in message and "\nSigned-off-by: " in message


def test_renovate_ignores_the_author_the_lock_commit_is_made_as() -> None:
    """#682: otherwise Renovate reads the pushed locks as a foreign edit and stops rebasing the branch."""
    committer = re.search(r'git config user\.email "([^"]+)"', _push_step()["run"])
    assert committer
    email = committer.group(1)
    config = (_REPO / ".github" / "renovate.json5").read_text(encoding="utf-8")
    ignored = re.search(r"^\s*gitIgnoredAuthors:\s*\[(.*)\],$", config, re.M)
    assert ignored and email in re.findall(r'"([^"]+)"', ignored.group(1))


def _job_runs(condition: str, repository: str, email: str, message: str) -> bool:
    """Evaluates the job's `if:` for the few operators it uses (&&, !, ==, startsWith, dotted names)."""
    github = {"repository": repository, "event": {"head_commit": {"author": {"email": email}, "message": message}}}
    source = re.sub(r"startsWith\(([^,]+),\s*", r"(\1).startswith(", condition)
    source = source.replace("&&", " and ").replace("!(", " not (")
    source = re.sub(r"(?<![\w.@'])github\.([\w.]+)", lambda m: "github" + "".join(f"[{k!r}]" for k in m.group(1).split(".")), source)

    def value(node: ast.AST) -> object:
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            return github
        if isinstance(node, ast.Subscript):
            return value(node.value)[value(node.slice)]  # type: ignore[index]
        if isinstance(node, ast.BoolOp):
            results = [bool(value(v)) for v in node.values]
            return all(results) if isinstance(node.op, ast.And) else any(results)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return not value(node.operand)
        if isinstance(node, ast.Compare) and isinstance(node.ops[0], ast.Eq):
            return value(node.left) == value(node.comparators[0])
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "startswith":
            return str(value(node.func.value)).startswith(str(value(node.args[0])))
        raise AssertionError(f"unsupported expression {ast.dump(node)}")

    return bool(value(ast.parse(source.strip(), mode="eval").body))

def test_the_resolve_job_skips_only_its_own_lock_commit() -> None:
    """The lock commit starts the workflow again: it must not solve twice, a rebuild must."""
    job = _renovate_workflow()["jobs"]["resolve"]
    commit = re.search(r'git commit -q -s -m "([^"]+)"', _push_step()["run"])
    committer = re.search(r'git config user\.email "([^"]+)"', _push_step()["run"])
    assert commit and committer
    bot, message = committer.group(1), commit.group(1)
    condition = " ".join(job["if"].split())
    assert _job_runs(condition, "intel/dokimi", bot, message) is False
    assert _job_runs(condition, "intel/dokimi", "renovate@example.com", "bump mypy") is True
    assert _job_runs(condition, "intel/dokimi", bot, "something else") is True
    assert _job_runs(condition, "intel/dokimi", "renovate@example.com", message) is True
    assert _job_runs(condition, "someone/fork", "renovate@example.com", "bump mypy") is False


def test_the_renovate_workflow_keeps_the_token_away_from_the_branch_code() -> None:
    """#682: the job that runs the branch's code (pip install, lock.py) has no secret and a read-only token; the job with CONDA_LOCK_TOKEN runs none of it."""
    workflow = _renovate_workflow()
    jobs = workflow["jobs"]
    assert list(jobs) == ["resolve", "push"]
    assert workflow["permissions"] == {"contents": "read"}
    assert [job["permissions"] for job in jobs.values()] == [{"contents": "read"}] * 2
    resolve, push = jobs["resolve"], jobs["push"]
    assert "secrets." not in json.dumps(resolve) and "CONDA_LOCK_TOKEN" not in json.dumps(resolve)
    assert "CONDA_LOCK_TOKEN" in json.dumps(push)
    assert "python ci/lock.py --if-stale" in [s.get("run") for s in resolve["steps"]]
    assert any(str(s.get("uses", "")).startswith("actions/upload-artifact@") for s in resolve["steps"])
    assert any(str(s.get("uses", "")).startswith("actions/download-artifact@") for s in push["steps"])
    for job in (resolve, push):  # the locks are pushed onto the commit they were solved for
        checkout = next(s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/checkout@"))
        assert checkout["with"]["ref"] == "${{ github.sha }}" and checkout["with"]["persist-credentials"] is False
    runs = " ".join(s.get("run", "") for s in push["steps"])
    assert not re.search(r"\bpython\b|\bpip\b|lock\.py", runs)
    assert push["needs"] == "resolve" and push["if"] == "needs.resolve.outputs.changed == 'true'"


@pytest.mark.parametrize(("flag", "header", "solved"), [
    (True, "current", False), (True, "stale", True), (True, "missing", True),
    (False, "current", True),
])
def test_if_stale_keeps_a_lock_solved_from_todays_specs(flag: bool, header: str, solved: bool, tmp_path: Path,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    """#682: builds that aged in since the lock was solved must not change a lock whose inputs did not."""
    lock = _generator(tmp_path, monkeypatch, ["3.12"])
    calls: list[list[str]] = []

    def solve(wanted: list[str], min_age_days: int) -> list:
        calls.append(wanted)
        return []

    monkeypatch.setattr(lock, "solve", solve)
    path = tmp_path / "ci" / "conda-lock" / "linux-64-py3.12.txt"
    path.parent.mkdir(parents=True)
    if header != "missing":
        wanted = ["python 3.12.*"] if header == "current" else ["python 3.12.*", "mypy ==2.4.0"]
        path.write_text(f"{lock.SPECS_PREFIX}{json.dumps(wanted)}\nold\n", encoding="utf-8")
    assert lock.main(["--if-stale"] if flag else []) == 0
    assert bool(calls) is solved
    assert path.read_text(encoding="utf-8").endswith("old\n") is (not solved)
    assert path.is_file()
