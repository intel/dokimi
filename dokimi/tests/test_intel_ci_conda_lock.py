# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""ci/conda-lock/: Intel CI's conda test environments come from the locks on a PR or a main push, and are current."""

from __future__ import annotations

import argparse
import importlib.util
import re
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
    lock = _script("lock", monkeypatch)
    monkeypatch.setattr(lock.hooks, "HOST_SUBDIR", "win-64")
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


def test_the_solve_targets_linux_64_whatever_machine_runs_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """py-rattler is asked for linux-64 and noarch records, not the machine's subdir."""
    platforms: list[list[str]] = []

    async def solve(channels: list[str], specs: list[str], **options: list[str]) -> list:
        platforms.append(options["platforms"])
        return []

    rattler = types.ModuleType("rattler")
    for name in ("GenericVirtualPackage", "PackageName", "Version"):
        setattr(rattler, name, lambda *args: args)
    rattler.solve = solve  # type: ignore[attr-defined]
    build_string = types.ModuleType("rattler.package.build_string")
    build_string.BuildString = lambda build: build  # type: ignore[attr-defined]
    for name, module in (("rattler", rattler), ("rattler.package", types.ModuleType("rattler.package")),
                         ("rattler.package.build_string", build_string)):
        monkeypatch.setitem(sys.modules, name, module)
    lock = _script("lock", monkeypatch)
    monkeypatch.setattr(lock.hooks, "HOST_SUBDIR", "osx-64")
    assert lock.solve(["python 3.12.*"], 7) == []
    assert platforms == [["linux-64", "noarch"]]


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
    for version in _versions(monkeypatch):
        lines = hooks.lock_path(version).read_text(encoding="utf-8").splitlines()
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
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "RUN_URL": "https://example.invalid/run/1"}
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
