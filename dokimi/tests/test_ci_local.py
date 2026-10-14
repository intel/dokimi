"""Tests for scripts/ci_local.py: synthetic workflows, stubbed subprocess, no network."""

from __future__ import annotations

import argparse
import importlib.util
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
    return {"calls": calls, "root": tmp_path}


def ns(**kw: Any) -> argparse.Namespace:
    d: dict[str, Any] = dict(jobs=None, base="main", offline=False, with_docker=False)
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
