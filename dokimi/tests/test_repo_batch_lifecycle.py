# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`repo run` cell lifecycle: the counterexamples TLC found in formal/repo_batch/RepoBatch.tla, replayed.

Each test names the TLC config whose trace it follows. No docker, no LLM.
"""

from __future__ import annotations

import json
import os
import signal
import threading
import time

import pytest
import yaml

from dokimi.cli import build_parser
from dokimi.repos import runner as repo_runner
from dokimi.repos.report import CellFileError, summarize
from repo_eval_fixture import FakeRunner, fake_harness, make_repo


@pytest.fixture
def setup(tmp_path, monkeypatch):
    root, _ = make_repo(tmp_path)
    exe, profile = fake_harness(tmp_path)
    runner = FakeRunner()
    monkeypatch.setattr("dokimi.repos.container.DockerRunner", lambda *a, **k: runner)
    monkeypatch.setenv("FAKE_ACTION", "fix")
    return {"root": root, "exe": exe, "profile": profile, "runner": runner, "out": tmp_path / "runs"}


def _run(s, *extra, models=("m-1",)):
    argv = ["repo", "run", f"{s['root']}@HEAD", "--manifest", ".agent-eval/repo-eval.yaml", "--harness",
            str(s["profile"]), "--bin", str(s["exe"]), *[a for m in models for a in ("--model", m)],
            "--batch", "b1", "--out", str(s["out"]), "--cache-root", str(s["out"] / "caches"),
            "--agent-network", "host", "--arm", "guidance:raw", *extra]
    args = build_parser().parse_args(argv)
    args.func(args)


def _agents(s):
    return [spec for spec in s["runner"].specs if "-p" in spec.argv]


@pytest.mark.parametrize("extra, models", [
    (("--task", "fix_add", "--task", "fix_add"), ("m-1",)),
    (("--task", "fix_add"), ("m-1", "m-1")),
    (("--task", "fix_add"), ("org/m-1", "org:m-1")),  # one slug, one directory
])
def test_a_plan_naming_one_cell_twice_is_refused_before_any_agent(setup, capsys, extra, models):
    # cfg/code_main_dup_plan.cfg: two threads took one cell; the second rmtree'd the first one's directory while
    # its agent ran, and the first then wrote result.json next to the second one's meta.json and answer.txt.
    with pytest.raises(SystemExit) as exc:
        _run(setup, *extra, "--jobs", "2", models=models)
    assert exc.value.code == 1
    assert "is planned 2 times" in capsys.readouterr().err
    assert not _agents(setup)
    assert not list((setup["out"] / "b1").glob("*/result.json"))


def test_ctrl_c_starts_no_queued_cell(setup, monkeypatch):
    # CancelContract of cfg/fixed.cfg (cfg/mut_no_cancel_guard.cfg breaks it): SIGINT wakes the main thread and
    # pool.map cancels the queued cells; _thread.interrupt_main() sends no signal and would let them start.
    started = []
    both = threading.Barrier(2)
    real = repo_runner.run_cell

    def run_cell(ctx, task, arm, rep, *args, **kwargs):
        started.append(rep)
        if rep <= 2:
            both.wait(5)  # both threads busy, every cell already submitted to the pool
            if rep == 1:
                os.kill(os.getpid(), signal.SIGINT)  # what Ctrl-C sends
            time.sleep(0.3)  # the main thread takes the interrupt while these cells still run
        return real(ctx, task, arm, rep, *args, **kwargs)

    monkeypatch.setattr(repo_runner, "run_cell", run_cell)
    with pytest.raises(KeyboardInterrupt):
        _run(setup, "--task", "fix_add", "--reps", "5", "--jobs", "2")
    batch = setup["out"] / "b1"
    for _ in range(100):  # the running cells are not joined; they end on their own
        if len(list(batch.glob("*/result.json"))) == 2:
            break
        time.sleep(0.05)
    time.sleep(0.3)  # a thread that would start another cell has had the time to
    assert sorted(started) == [1, 2]
    summary = summarize(batch)  # the running cells finished; the queued ones are missing, not failed
    assert summary["cells"] == 2
    assert summary["missing"] == [f"fix_add__guidance-raw__m-1__r{r}" for r in (3, 4, 5)]


def test_a_force_run_stopped_part_way_does_not_pool_two_executions(setup, monkeypatch, capsys):
    # cfg/code_main_force_crash.cfg: --force with another execution replaced r1, then stopped; r2 kept the old
    # execution and `repo report` pooled both in one row.
    _run(setup, "--task", "fix_add", "--reps", "2")
    raw = yaml.safe_load(setup["profile"].read_text())
    raw["delegation"] = {"none": {"all": [*raw["tools"]["all"], "--no-agents"]}}
    setup["profile"].write_text(yaml.safe_dump(raw))
    real = repo_runner.run_cell

    def run_cell(ctx, task, arm, rep, *args, **kwargs):
        if rep == 2:
            raise KeyboardInterrupt
        return real(ctx, task, arm, rep, *args, **kwargs)

    monkeypatch.setattr(repo_runner, "run_cell", run_cell)
    with pytest.raises(KeyboardInterrupt):
        _run(setup, "--task", "fix_add", "--reps", "2", "--delegation", "none", "--force")
    batch = setup["out"] / "b1"
    ids = {json.loads((batch / f"fix_add__guidance-raw__m-1__r{r}/result.json").read_text())["execution"]["id"]
           for r in (1, 2)}
    assert len(ids) == 2
    with pytest.raises(CellFileError, match="ran with executions") as exc:
        summarize(batch)
    assert all(i in str(exc.value) for i in ids)

    monkeypatch.setattr(repo_runner, "run_cell", real)  # finishing the forced run makes the batch one execution
    _run(setup, "--task", "fix_add", "--reps", "2", "--delegation", "none", "--force")
    assert summarize(batch)["rows"][0]["n"] == 2


def test_a_resume_after_a_crash_between_the_artifacts_and_result_json_reruns_the_cell_once(setup, monkeypatch):
    # cfg/witness_NoCrashAfterArtifacts.cfg, then P1/P2 of cfg/fixed.cfg: the crashed cell is missing, the resume
    # replaces its directory, and the report counts it once, with the files of the attempt that finished.
    def crash(path, data):
        if path.name == "result.json":
            raise SystemExit("killed")  # the process dies after meta.json, answer.txt and grade.json
        return real_write(path, data)

    real_write = repo_runner._write_json
    monkeypatch.setattr(repo_runner, "_write_json", crash)
    with pytest.raises(SystemExit):
        _run(setup, "--task", "fix_add")
    rd = setup["out"] / "b1/fix_add__guidance-raw__m-1__r1"
    assert (rd / "answer.txt").is_file() and not (rd / "result.json").exists()
    assert summarize(setup["out"] / "b1")["missing"] == [rd.name]
    first = json.loads((rd / "meta.json").read_text())["start_sha"]

    monkeypatch.setattr(repo_runner, "_write_json", real_write)
    _run(setup, "--task", "fix_add")
    summary = summarize(setup["out"] / "b1")
    assert summary["missing"] == [] and summary["rows"][0]["n"] == 1
    assert json.loads((rd / "meta.json").read_text())["start_sha"] == first
    assert len(_agents(setup)) == 2  # the retry is a second attempt at r1, not an r2
