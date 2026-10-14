"""Tests for scripts/ci_local.py: synthetic workflows, stubbed subprocess, no network."""

from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

_spec = importlib.util.spec_from_file_location(
    "ci_local", Path(__file__).resolve().parents[1] / "scripts" / "ci_local.py"
)
assert _spec and _spec.loader
cl = importlib.util.module_from_spec(_spec)
sys.modules["ci_local"] = cl
_spec.loader.exec_module(cl)

PYV = f"{sys.version_info.major}.{sys.version_info.minor}"
WF = """
name: t
on:
  pull_request:
defaults:
  run:
    working-directory: sub
jobs:
  a:
    runs-on: x
    env:
      JOBVAR: jv
    steps:
      - uses: actions/checkout@v1
      - run: pip install ruff 'mypy>=1' -r req.txt
      - name: one
        run: echo ${{ github.event.pull_request.base.ref }} ${{ matrix.python-version }}
      - name: two
        working-directory: other
        env:
          B: ${{ github.event.pull_request.base.ref }}
        run: exit 1
      - name: three
        if: always() && matrix.python-version == '3.11'
        run: echo hi
      - name: unk
        run: echo ${{ secrets.TOKEN }}
  b:
    runs-on: x
    if: github.event_name == 'schedule'
    steps:
      - run: echo never
  package-smoke:
    runs-on: x
    steps:
      - run: echo net
"""


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    wfdir = tmp_path / ".github" / "workflows"
    wfdir.mkdir(parents=True)
    (wfdir / "x.yml").write_text(WF.replace("'3.11'", f"'{PYV}'"), encoding="utf-8")
    (wfdir / "push.yml").write_text("on: push\njobs:\n  z:\n    steps:\n      - run: x\n")
    monkeypatch.setattr(cl, "WORKFLOWS", wfdir)
    monkeypatch.setattr(cl, "REPO", tmp_path)
    calls: list[dict[str, Any]] = []

    def fake_run(cmd: list[str], **kw: Any) -> Any:
        calls.append({"cmd": cmd, **kw})
        rc = 1 if cmd[-1].strip() == "exit 1" else 0
        return subprocess.CompletedProcess(cmd, rc)

    monkeypatch.setattr(cl.subprocess, "run", fake_run)
    monkeypatch.setattr(cl, "network_ok", lambda: True)
    monkeypatch.setattr(cl, "make_isolated_env", lambda t, py: (tmp_path, {"PATH": ""}))
    monkeypatch.setattr(cl.shutil, "which", lambda _: None)
    monkeypatch.setattr(cl, "upstream_remote", lambda override: override or "origin")
    monkeypatch.setattr(cl, "resolve_base", lambda base, remote: (f"{remote}/{base}", None))
    return {"calls": calls, "root": tmp_path}


def ns(**kw: Any) -> argparse.Namespace:
    d: dict[str, Any] = dict(jobs=None, base="main", remote=None, offline=False, with_docker=False)
    d.update(kw)
    return argparse.Namespace(**d)


def test_job_and_workflow_env_reach_the_step(env: dict[str, Any]) -> None:
    """Job-level env: is passed to bash; step env overrides it."""
    cl.run(ns(jobs="a"))
    assert env["calls"][0]["env"]["JOBVAR"] == "jv"
    wf = "on: pull_request\nenv:\n  W: w\n  J: wf\njobs:\n  a:\n    env:\n      J: job\n    steps:\n      - run: echo\n"
    (cl.WORKFLOWS / "x.yml").write_text(wf)
    env["calls"].clear()
    cl.run(ns())
    assert env["calls"][0]["env"]["W"] == "w" and env["calls"][0]["env"]["J"] == "job"


def test_unevaluable_condition_runs_step_with_notice(env: dict[str, Any], capsys: Any) -> None:
    """An `if:` outside the supported grammar does not crash the run."""
    for expr in ("failure()", "github.ref == 'refs/heads/main'", "contains(github.event.labels.*.name, 'x')"):
        assert cl.Context("main", PYV, Path(".")).condition(expr) is True
    assert "NOTICE: cannot evaluate" in capsys.readouterr().out


def test_tool_specs_keep_each_requirement_file_flagged(env: dict[str, Any]) -> None:
    """Every requirements file keeps its own -r; a repeated file is listed once."""
    wf = ("on: pull_request\njobs:\n  a:\n    steps:\n      - run: pip install -r one.txt\n"
          "      - run: pip install -r two.txt ruff\n      - run: pip install -r one.txt ruff\n")
    (cl.WORKFLOWS / "x.yml").write_text(wf)
    root = env["root"]
    assert cl.tool_specs() == ["-r", str(root / "one.txt"), "-r", str(root / "two.txt"), "ruff"]


def test_tool_specs_keep_constraint_and_editable_pairs(env: dict[str, Any]) -> None:
    """-c and -e keep their own flag in every spelling; URLs after -e stay as written."""
    wf = ("on: pull_request\njobs:\n  a:\n    steps:\n"
          "      - run: pip install -c a.txt --constraint=b.txt -cb.txt -e . --editable ../x\n"
          "      - run: pip install --requirement=r.txt -rr.txt -e git+https://h/p.git ruff\n")
    (cl.WORKFLOWS / "x.yml").write_text(wf)
    root = env["root"]
    assert cl.tool_specs() == [
        "-c", str(root / "a.txt"), "-c", str(root / "b.txt"),
        "-e", str(root / "."), "-e", str(root / "../x"),
        "-r", str(root / "r.txt"), "-e", "git+https://h/p.git", "ruff",
    ]


def test_pip_install_line_dropped_rest_of_step_runs(env: dict[str, Any]) -> None:
    """A step that starts with pip install and continues still runs its other lines."""
    wf = "on: pull_request\njobs:\n  a:\n    steps:\n      - run: |\n          pip install x\n          echo after\n      - run: pip install y\n"
    (cl.WORKFLOWS / "x.yml").write_text(wf)
    cl.run(ns())
    assert [c["cmd"][-1].strip() for c in env["calls"]] == ["echo after"]


BASE_WF = """on: pull_request
jobs:
  a:
    steps:
      - run: diff-cover c.xml --compare-branch="origin/$BASE_REF"
      - run: git diff "origin/${BASE_REF}"...HEAD
      - run: echo origin/main
"""


def test_base_steps_use_the_resolved_remote(env: dict[str, Any]) -> None:
    """`origin/$BASE_REF` in a step reads the resolved remote; other text is untouched."""
    (cl.WORKFLOWS / "x.yml").write_text(BASE_WF)
    assert cl.run(ns(remote="intel")) == 0
    assert [c["cmd"][-1] for c in env["calls"]] == [
        'diff-cover c.xml --compare-branch="intel/$BASE_REF"',
        'git diff "intel/${BASE_REF}"...HEAD',
        "echo origin/main",
    ]


def test_unusable_base_fails_its_steps_without_running_them(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    """No base ref: the diffing steps FAIL with the reason; the rest still run."""
    (cl.WORKFLOWS / "x.yml").write_text(BASE_WF)
    monkeypatch.setattr(cl, "resolve_base", lambda base, remote: (f"{remote}/{base}", "no such ref"))
    assert cl.run(ns()) == 1
    assert [c["cmd"][-1] for c in env["calls"]] == ["echo origin/main"]
    out = capsys.readouterr().out
    assert "Base: origin/main" in out and out.count("ERROR: no such ref") == 3


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """A real git repo with one commit; returns a git runner for it."""
    for k, v in {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                 "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                 "GIT_COMMITTER_EMAIL": "t@t"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(cl, "REPO", tmp_path)

    def git(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=tmp_path, check=True,
                              capture_output=True, text=True).stdout.strip()

    git("init", "-q")
    git("commit", "-q", "--allow-empty", "-m", "c")
    return git


def test_upstream_remote_is_the_intel_dokimi_url(repo: Any) -> None:
    """origin pointing elsewhere does not win; an override always does."""
    assert cl.upstream_remote(None) == "origin"  # no remotes at all
    repo("remote", "add", "origin", "https://github.com/someone/other")
    repo("remote", "add", "fork", "https://github.com/me/dokimi")
    assert cl.upstream_remote(None) == "origin"  # nothing matches: fall back
    repo("remote", "add", "intel", "git@github.com:Intel/dokimi.git")
    assert cl.upstream_remote(None) == "intel"
    assert cl.upstream_remote("fork") == "fork"
    repo("remote", "set-url", "origin", "https://github.com/intel/dokimi")
    assert cl.upstream_remote(None) == "origin"  # several match: origin first


def test_resolve_base_reports_missing_and_unrelated_refs(repo: Any) -> None:
    """A usable base passes; a missing ref or one with no shared history says why."""
    head = repo("rev-parse", "HEAD")
    repo("update-ref", "refs/remotes/intel/main", head)
    assert cl.resolve_base("main", "intel") == ("intel/main", None)
    ref, err = cl.resolve_base("main", "origin")
    assert ref == "origin/main" and err and "does not exist" in err and "git fetch origin" in err
    tree = repo("hash-object", "-t", "tree", "-w", os.devnull)  # the empty tree
    repo("update-ref", "refs/remotes/origin/main", repo("commit-tree", tree, "-m", "other"))
    ref, err = cl.resolve_base("main", "origin")
    assert err and "no merge base" in err and "--remote" in err
