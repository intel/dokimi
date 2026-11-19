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
from dokimi.repos.compare import compare
from dokimi.repos.report import CellFileError, refuse_mixed_executions, summarize
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
        _run(setup, *extra, "--concurrency", "2", models=models)
    assert exc.value.code == 1
    assert "is planned 2 times" in capsys.readouterr().err
    assert not _agents(setup)
    assert not list((setup["out"] / "b1").glob("*/result.json"))


def test_every_repeated_cell_is_named_in_the_refusal(setup, capsys):
    # two repeated --task values plan two cells twice: the refusal names both, not only the first
    with pytest.raises(SystemExit) as exc:
        _run(setup, "--task", "fix_add", "--task", "fix_add", "--reps", "2", "--concurrency", "2")
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "cell fix_add__guidance-raw__m-1__r1 is planned 2 times" in err
    assert "cell fix_add__guidance-raw__m-1__r2 is planned 2 times" in err
    assert not _agents(setup)


def test_names_that_differ_only_in_case_are_refused_before_any_agent(setup, capsys):
    # on a case-insensitive volume (default macOS APFS) ...__m-1__r1 and ...__M-1__r1 are one directory: the
    # run of one would rmtree the other's while it works in it
    with pytest.raises(SystemExit) as exc:
        _run(setup, "--task", "fix_add", "--concurrency", "2", models=("m-1", "M-1"))
    assert exc.value.code == 1
    assert "case-insensitive volume" in capsys.readouterr().err
    assert not _agents(setup)


@pytest.mark.skipif(os.name == "nt", reason="signal.pthread_kill is POSIX-only")
def test_ctrl_c_starts_no_queued_cell(setup, monkeypatch):
    # CancelContract of cfg/fixed.cfg (cfg/mut_no_cancel_guard.cfg breaks it): SIGINT wakes the main thread and
    # pool.map cancels the queued cells; _thread.interrupt_main() sends no signal and would let them start.
    started = []
    both = threading.Barrier(2)
    ended = threading.Semaphore(0)
    real = repo_runner.run_cell
    cancelled = threading.Event()
    real_shutdown = repo_runner.ThreadPoolExecutor.shutdown

    def shutdown(self, wait=True, *, cancel_futures=False):
        real_shutdown(self, wait=wait, cancel_futures=cancel_futures)
        if cancel_futures:
            cancelled.set()

    def run_cell(ctx, task, arm, rep, *args, **kwargs):
        started.append(rep)
        if rep <= 2:
            both.wait(5)  # both threads busy, every cell already submitted to the pool
            if rep == 1:
                signal.pthread_kill(threading.main_thread().ident, signal.SIGINT)  # emulates Ctrl-C reaching the main thread
            # the cells wait for the cancel, not for time (#874)
            assert cancelled.wait(30)
        try:
            return real(ctx, task, arm, rep, *args, **kwargs)
        finally:
            ended.release()

    monkeypatch.setattr(repo_runner, "run_cell", run_cell)
    monkeypatch.setattr(repo_runner.ThreadPoolExecutor, "shutdown", shutdown)
    old = signal.signal(signal.SIGINT, signal.default_int_handler)  # a shell may have left SIGINT ignored
    try:
        with pytest.raises(KeyboardInterrupt):
            _run(setup, "--task", "fix_add", "--reps", "5", "--concurrency", "2")
    finally:
        signal.signal(signal.SIGINT, old)
    batch = setup["out"] / "b1"
    assert all(ended.acquire(timeout=30) for _ in range(2))  # running cells finish, maybe after run_batch raised
    assert sorted(started) == [1, 2]
    summary = summarize(batch)  # the running cells finished; the queued ones are missing, not failed
    assert summary["cells"] == 2
    assert summary["missing"] == [f"fix_add__guidance-raw__m-1__r{r}" for r in (3, 4, 5)]


def test_an_interrupt_during_submission_starts_no_queued_cell(setup, monkeypatch):
    # Ctrl-C lands in the main thread while pool.map is still submitting: the queued cells must not start.
    started = []
    calls = []
    real = repo_runner.run_cell
    real_submit = repo_runner.ThreadPoolExecutor.submit

    def submit(self, fn, *a, **k):
        calls.append(1)
        if len(calls) == 4:
            time.sleep(0.2)
            raise KeyboardInterrupt
        return real_submit(self, fn, *a, **k)

    def run_cell(ctx, task, arm, rep, *args, **kwargs):
        started.append(rep)
        time.sleep(0.5)  # both workers busy, cell 3 queued
        return real(ctx, task, arm, rep, *args, **kwargs)

    monkeypatch.setattr(repo_runner, "run_cell", run_cell)
    monkeypatch.setattr(repo_runner.ThreadPoolExecutor, "submit", submit)
    with pytest.raises(KeyboardInterrupt):
        _run(setup, "--task", "fix_add", "--reps", "5", "--concurrency", "2")
    time.sleep(1)
    assert sorted(started) == [1, 2]


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


def _cell(arm, model, ran, batch=""):
    return {"batch": batch, "arm": arm, "model": model, "execution": {"id": ran}}


def test_the_mixed_execution_refusal_is_per_batch_arm_and_model():
    # one execution per row is the rule; two arms, two models or two batches may each have their own
    ok = [_cell("a", "m", "e1"), _cell("a", "m", "e1"), _cell("b", "m", "e2"), _cell("a", "n", "e3"),
          _cell("a", "m", "e4", batch="other")]
    refuse_mixed_executions(ok, "b1")
    with pytest.raises(CellFileError, match="arm a, model m ran with executions e1, e4"):
        refuse_mixed_executions([*ok, _cell("a", "m", "e4")], "b1")
    with pytest.raises(CellFileError, match="e1, unrecorded"):
        refuse_mixed_executions([_cell("a", "m", "e1"), {"arm": "a", "model": "m"}], "b1")
    # the remedy is a --force run over the whole batch: an arm-only rerun is refused while another arm holds the older id
    with pytest.raises(CellFileError, match="rerun every arm and model of the batch with --force and "
                                            "the batch's --task and --reps"):
        refuse_mixed_executions([_cell("a", "m", "e1"), _cell("a", "m", "e2")], "b1")


def test_summarize_refuses_two_batch_names_pooled_in_one_row(tmp_path, monkeypatch):
    # one batch directory is one batch: cells that record two batch names must not share a summary row
    cells = [{"batch": "b1", "task": "t", "arm": "a", "model": "m", "execution": {"id": "e1"}, "score_task": True},
             {"batch": "b0", "task": "t", "arm": "a", "model": "m", "execution": {"id": "e2"}, "score_task": True}]
    monkeypatch.setattr("dokimi.repos.report.load_cells", lambda d: (cells, []))
    with pytest.raises(CellFileError, match="ran with executions e1, e2"):
        summarize(tmp_path)


def test_compare_refuses_only_a_compared_row_of_two_executions(setup):
    _run(setup, "--task", "fix_add", "--arm", "guidance:none,guidance:bridge", "--reps", "2")
    b1 = setup["out"] / "b1"
    target = b1 / "fix_add__guidance-bridge__m-1__r1/result.json"
    rec = json.loads(target.read_text())
    rec["execution"]["id"] = "old"  # what a --force run stopped part way leaves beside the new cells
    target.write_text(json.dumps(rec))
    with pytest.raises(CellFileError, match="arm guidance:bridge, model m-1 ran with executions"):
        compare([b1], "guidance:none", ["guidance:bridge"])
    assert compare([b1], "guidance:none", ["guidance:raw"])["comparisons"]  # the mixed arm is not compared


def test_compare_with_a_model_refuses_only_a_mixed_row_of_that_model(setup):
    # m-2 alone has the mixed arm: --model m-1 compares rows that are not mixed, so it must not be refused
    _run(setup, "--task", "fix_add", "--arm", "guidance:none,guidance:bridge", "--reps", "2", models=("m-1", "m-2"))
    b1 = setup["out"] / "b1"
    target = b1 / "fix_add__guidance-bridge__m-2__r1/result.json"
    rec = json.loads(target.read_text())
    rec["execution"]["id"] = "old"
    target.write_text(json.dumps(rec))
    assert compare([b1], "guidance:none", ["guidance:bridge"], models=["m-1"])["comparisons"]
    with pytest.raises(CellFileError, match="arm guidance:bridge, model m-2 ran with executions"):
        compare([b1], "guidance:none", ["guidance:bridge"], models=["m-2"])
    with pytest.raises(CellFileError, match="arm guidance:bridge, model m-2 ran with executions"):
        compare([b1], "guidance:none", ["guidance:bridge"])


_CTRL_C_DRIVER = """
import os, signal, sys, threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from repo_eval_fixture import FakeRunner, fake_harness, make_repo
from dokimi import cli
from dokimi.repos import runner as repo_runner
tmp = Path(sys.argv[2])
root, _ = make_repo(tmp)
exe, profile = fake_harness(tmp)
fake = FakeRunner()
import dokimi.repos.container as container
container.DockerRunner = lambda *a, **k: fake
os.environ["FAKE_ACTION"] = "fix"
both, real = threading.Barrier(2), repo_runner.run_cell
cancelled, real_shutdown = threading.Event(), ThreadPoolExecutor.shutdown
def shutdown(self, wait=True, *, cancel_futures=False):
    real_shutdown(self, wait=wait, cancel_futures=cancel_futures)
    if cancel_futures:
        cancelled.set()
ThreadPoolExecutor.shutdown = shutdown
submitted, real_submit, count = threading.Event(), ThreadPoolExecutor.submit, []
def pool_submit(self, *args, **kwargs):
    future = real_submit(self, *args, **kwargs)
    count.append(1)
    if len(count) == 5:  # the fifth of --reps 5: every cell is submitted and its thread has started
        submitted.set()
    return future
ThreadPoolExecutor.submit = pool_submit
def run_cell(ctx, task, arm, rep, *args, **kwargs):
    if rep <= 2:
        both.wait(30)
        if rep == 1:
            if not submitted.wait(30):  # an interrupt inside submit() is a slow thread start, not the Ctrl-C under test
                os._exit(3)
            signal.pthread_kill(threading.main_thread().ident, signal.SIGINT)
        # both workers stay busy until the queue is cancelled, so this checks the cancel, not an early start (#874)
        if not cancelled.wait(30):
            os._exit(4)
    return real(ctx, task, arm, rep, *args, **kwargs)
repo_runner.run_cell = run_cell
signal.signal(signal.SIGINT, signal.default_int_handler)
out = tmp / "runs"
sys.argv = ["dokimi", "repo", "run", f"{root}@HEAD", "--manifest", ".agent-eval/repo-eval.yaml", "--harness",
            str(profile), "--bin", str(exe), "--model", "m-1", "--batch", "b1", "--out-dir", str(out),
            "--cache-root", str(out / "caches"), "--agent-network", "host", "--arm", "guidance:raw",
            "--task", "fix_add", "--reps", "5", "--concurrency", "2"]
cli.main()
"""


@pytest.mark.skipif(os.name == "nt", reason="SIGINT to the own process is POSIX")
def test_ctrl_c_makes_the_repo_run_process_exit_nonzero(tmp_path):
    # P6 of cfg/fixed.cfg: an interrupted run never exits 0, so a script cannot read it as a full batch.
    import subprocess
    import sys
    from pathlib import Path

    tests = Path(__file__).parent
    env = {k: v for k, v in os.environ.items() if not k.startswith("DOKIMI_")}
    env |= {"HOME": str(tmp_path), "PYTHONPATH": os.pathsep.join([str(tests.parent), env.get("PYTHONPATH", "")])}
    proc = subprocess.run([sys.executable, "-c", _CTRL_C_DRIVER, str(tests), str(tmp_path)],
                          capture_output=True, text=True, timeout=50, env=env, cwd=tmp_path)
    assert proc.returncode in (130, -signal.SIGINT), (proc.returncode, proc.stderr[-2000:])
    assert "KeyboardInterrupt" in proc.stderr
    done = sorted(p.parent.name for p in (tmp_path / "runs" / "b1").glob("*/result.json"))
    assert done == [f"fix_add__guidance-raw__m-1__r{r}" for r in (1, 2)]  # the running cells end, no queued one starts
