# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Run directories (#94 R2, docs/run-directories.md): one directory per run, its ``run.json``,
no clobbering, artifacts by relative path, and the result a stopped run still leaves."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

import pytest

from dokimi import cli, process, process_hooks
from dokimi.artifacts import RUN_ID_ENV, load_artifact, save_artifact
from dokimi.eval.arm_stop import RunStopped
from dokimi.novelty.client import SpendCapReached
from dokimi.runner.plan import completeness
from dokimi.runs import rundir
from dokimi.runs.rundir import open_run
from dokimi.utils import InputError

PROJECT = Path(__file__).resolve().parents[1]
FIXTURES = PROJECT / "tests" / "fixtures" / "artifacts"


def _fixture(version: str) -> dict:
    return json.loads((FIXTURES / f"{version}.json").read_text(encoding="utf-8"))


def _record(run_dir: Path) -> dict:
    """``run.json``, checked against run.v1."""
    return load_artifact("run", run_dir / "run.json")


def _ids() -> list[str]:
    return [cell["cell_id"] for cell in _fixture("plan.v1")["cells"]]


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv(RUN_ID_ENV, "r-1")
    monkeypatch.setattr(sys, "argv", ["dokimi", "eval", "arms", "run", "--max-usd", "5"])
    return tmp_path / "results"


def test_a_run_gets_a_directory_named_by_its_run_id_and_a_run_json_that_says_running(root):
    """docs/run-directories.md: "in a directory named by the run's name when it has one, else by its run_id"."""
    run = open_run(root)

    assert run.path == root / "r-1"
    assert sorted(os.listdir(run.path)) == ["run.json"]
    record = _record(run.path)
    started = record.pop("started_at")
    assert record == {"schema_version": "run.v1", "run_id": "r-1", "dokimi_version": record["dokimi_version"],
                      "name": None, "argv": ["eval", "arms", "run", "--max-usd", "5"], "finished_at": None,
                      "status": "running", "stop": None, "artifacts": []}
    assert datetime.fromisoformat(started).utcoffset().total_seconds() == 0
    assert list(record)[:3] == ["schema_version", "run_id", "dokimi_version"]


def test_a_name_names_the_directory_and_a_taken_name_is_refused(root, monkeypatch):
    """docs/run-directories.md: "A name already taken under the root is refused"."""
    first = open_run(root, name="nightly-7")
    kept = (first.path / "run.json").read_bytes()
    monkeypatch.setenv(RUN_ID_ENV, "r-2")

    with pytest.raises(InputError, match=r"nightly-7 already exists: a run never writes into another run's"):
        open_run(root, name="nightly-7")

    assert first.path == root / "nightly-7"
    assert _record(first.path)["name"] == "nightly-7"
    assert (first.path / "run.json").read_bytes() == kept


def test_a_run_id_directory_that_exists_is_refused(root):
    """docs/run-directories.md: "and so is a run_id directory that exists (two commands sharing one DOKIMI_RUN_ID)"."""
    open_run(root)
    with pytest.raises(InputError, match="r-1 already exists"):
        open_run(root)


@pytest.mark.parametrize("name", ["", "../up", "a/b", "a\\b", ".hidden", "-x", "x" * 129, "a b"])
def test_a_name_that_is_not_one_path_segment_of_run_id_characters_is_refused(root, name):
    """docs/run-directories.md: "Names have the characters of a run id ... so a name is one path segment"."""
    with pytest.raises(InputError, match="run name"):
        open_run(root, name=name)
    assert not root.exists()


@pytest.mark.parametrize("made", [False, True])
def test_an_output_directory_is_the_run_directory(root, tmp_path, made):
    """docs/run-directories.md: "An output directory given to the run (--out-dir, #591) is the run directory
    itself"; it "may also be an empty directory"."""
    out = tmp_path / "a" / "b" / "out"
    if made:
        out.mkdir(parents=True)

    run = open_run(root, out_dir=out)

    assert run.path == out
    assert _record(out)["name"] is None
    assert not root.exists()


def test_an_output_directory_that_holds_anything_is_refused(root, tmp_path):
    """docs/run-directories.md: "The directory must not exist yet; an output directory may also be an empty directory"."""
    out = tmp_path / "out"
    out.mkdir()
    (out / "notes.txt").write_text("mine", encoding="utf-8")
    afile = tmp_path / "a-file"
    afile.write_text("mine", encoding="utf-8")

    for given in (out, afile):
        with pytest.raises(InputError, match="already exists"):
            open_run(root, out_dir=given)
    assert sorted(os.listdir(out)) == ["notes.txt"]
    assert afile.read_text(encoding="utf-8") == "mine"


def test_a_run_directory_whose_parent_is_a_file_is_refused(tmp_path, monkeypatch):
    """docs/run-directories.md: "So is a directory that cannot be made: a runs root or a parent of the
    output directory that is a file"."""
    monkeypatch.setenv(RUN_ID_ENV, "r-1")
    afile = tmp_path / "a-file"
    afile.write_text("mine", encoding="utf-8")

    for root, out_dir in ((afile, None), (tmp_path / "results", afile / "out"), (afile / "deeper", None)):
        with pytest.raises(InputError, match="cannot make run directory"):
            open_run(root, out_dir=out_dir)
    assert afile.read_text(encoding="utf-8") == "mine"


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="POSIX modes, not as root")
def test_a_run_directory_the_root_does_not_let_us_make_is_refused(root):
    """docs/run-directories.md: "So is a directory that cannot be made"."""
    root.mkdir()
    root.chmod(0o500)
    try:
        with pytest.raises(InputError, match="cannot make run directory .*Permission denied"):
            open_run(root)
    finally:
        root.chmod(0o700)


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="POSIX modes, not as root")
@pytest.mark.parametrize("mode", [0o500, 0o300], ids=["not-writable", "not-listable"])
def test_an_empty_output_directory_we_may_not_write_or_list_is_refused(root, tmp_path, mode):
    """docs/run-directories.md: "So is a directory that cannot be made": an empty output directory where
    run.json cannot be created, or whose entries cannot be listed."""
    out = tmp_path / "out"
    out.mkdir()
    out.chmod(mode)
    try:
        with pytest.raises(InputError, match=f"cannot make run directory {out}: Permission denied"):
            open_run(root, out_dir=out)
    finally:
        out.chmod(0o700)
    assert os.listdir(out) == []


def test_a_name_with_an_output_directory_is_refused(root, tmp_path):
    """docs/run-directories.md: "A name together with an output directory is refused"."""
    with pytest.raises(InputError, match="give one of them"):
        open_run(root, name="n", out_dir=tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_of_two_runs_racing_for_one_empty_directory_only_one_gets_it(root, tmp_path, monkeypatch):
    """docs/run-directories.md: "run.json is created exclusively, so of two runs that race for one directory
    only one gets it": the second still saw the directory empty."""
    out = tmp_path / "out"
    out.mkdir()
    first = open_run(root, out_dir=out)
    kept = (out / "run.json").read_bytes()
    monkeypatch.setattr(Path, "iterdir", lambda self: iter(()))

    with pytest.raises(InputError, match="already exists"):
        open_run(root, out_dir=out)
    assert (first.path / "run.json").read_bytes() == kept


def test_artifacts_land_in_the_directory_and_run_json_lists_them_by_relative_path(root):
    """docs/run-directories.md: "Artifacts are <schema_version>.json in the directory" and "artifacts lists the
    files the run asked a path for and wrote, as the run ends"."""
    with open_run(root, name="n") as run:
        arms = run.artifact_path("arms")
        save_artifact("arms", _fixture("arms.v1"), arms)
        run.artifact_path("eval")  # asked for, never written
        assert _record(run.path)["artifacts"] == []

    assert arms == root / "n" / "arms.v1.json"
    record = _record(run.path)
    assert record["status"] == "complete"
    assert record["artifacts"] == [{"kind": "arms", "path": "arms.v1.json"}]
    assert load_artifact("arms", run.path / record["artifacts"][0]["path"])["run_id"] == record["run_id"]


def test_a_stop_leaves_run_json_incomplete_and_a_result_with_the_cells_left_not_run(root):
    """docs/run-directories.md: Ctrl-C gives "incomplete", "interrupted", and "the cells left are not_run"."""
    ids = _ids()
    with pytest.raises(KeyboardInterrupt):
        with open_run(root, name="n") as run:
            plan = run.write_plan(_fixture("plan.v1"))
            run.record_cell(ids[0], "pass", model="m-served")
            run.record_cell(ids[1], "fail")
            raise KeyboardInterrupt

    record = _record(run.path)
    assert (record["status"], record["stop"]) == ("incomplete", {"reason": "interrupted", "detail": None})
    assert record["finished_at"] >= record["started_at"]
    assert record["artifacts"] == [{"kind": "plan", "path": "plan.v1.json"},
                                   {"kind": "result", "path": "result.v1.json"}]
    result = load_artifact("result", run.path / "result.v1.json")  # checked against result.v1
    assert [(c["cell_id"], c["status"], c["model"]) for c in result["cells"]] == [
        (ids[0], "pass", "m-served"), (ids[1], "fail", None), *((cid, "not_run", None) for cid in ids[2:])]
    assert {c["detail"] for c in result["cells"][2:]} == {"the run stopped before this cell (interrupted)"}
    found = completeness(plan, result)
    assert (found.state, found.missing) == ("incomplete", tuple(ids[2:]))


@pytest.mark.parametrize(("stop", "status", "reason", "detail"), [
    (SpendCapReached("spent $5.02 of $5.00"), "incomplete", "spend_cap", "spent $5.02 of $5.00"),
    (SpendCapReached("no price for m", reason="no_price"), "incomplete", "no_price", "no price for m"),
    (RunStopped(RuntimeError("usage limit reached"), {}), "incomplete", "run_stopped", "usage limit reached"),
    (process.ShuttingDown("SIGTERM"), "incomplete", "terminated", "SIGTERM"),
    (OSError("disk full"), "failed", "error", "OSError: disk full"),
])
def test_each_stop_is_recorded_with_its_reason(root, stop, status, reason, detail):
    """docs/run-directories.md, "How a run ends": the spend cap, a call with no price, a run-wide CLI failure
    and SIGTERM leave the run incomplete; any other error leaves it failed."""
    with pytest.raises(type(stop)):
        with open_run(root) as run:
            run.write_plan(_fixture("plan.v1"))
            raise stop

    record = _record(run.path)
    assert (record["status"], record["stop"]) == (status, {"reason": reason, "detail": detail})
    cells = load_artifact("result", run.path / "result.v1.json")["cells"]
    assert {c["status"] for c in cells} == {"not_run"}
    assert {c["detail"] for c in cells} == {f"the run stopped before this cell ({reason})"}


def test_a_stopped_run_without_a_plan_writes_no_result(root):
    """docs/run-directories.md: "A run without a plan writes no result.v1.json"."""
    with pytest.raises(KeyboardInterrupt):
        with open_run(root) as run:
            raise KeyboardInterrupt
    assert sorted(os.listdir(run.path)) == ["run.json"]
    assert _record(run.path)["status"] == "incomplete"


@pytest.mark.parametrize(("last", "status"), [("inconclusive", "complete"), ("infra_error", "incomplete")])
def test_a_block_that_ends_normally_is_complete_only_when_every_planned_cell_is_measured(root, last, status):
    """docs/run-directories.md: "complete when every planned cell has a measured status, else incomplete"."""
    ids = _ids()
    with open_run(root) as run:
        plan = run.write_plan(_fixture("plan.v1"))
        for cid in ids[:-1]:
            run.record_cell(cid, "pass")
        run.record_cell(ids[-1], last)

    record = _record(run.path)
    assert (record["status"], record["stop"]) == (status, None)
    result = load_artifact("result", run.path / "result.v1.json")
    assert completeness(plan, result).state == status
    assert result["cells"][-1]["status"] == last


def test_a_cell_recorded_while_a_normal_end_is_decided_cannot_split_status_and_result(root, monkeypatch):
    """docs/run-directories.md: "complete when every planned cell has a measured status, else incomplete": the
    status is chosen from the cells the written result holds, even when a cell changes meanwhile."""
    real, fired = rundir.completeness, []

    def cell_changes_meanwhile(plan, result):
        if not fired:
            fired.append(True)
            run.record_cell(_ids()[0], "infra_error")  # a worker's late update, after the cells were read
        return real(plan, result)

    with open_run(root) as run:
        run.write_plan(_fixture("plan.v1"))
        for cid in _ids():
            run.record_cell(cid, "pass")
        monkeypatch.setattr(rundir, "completeness", cell_changes_meanwhile)

    saved = completeness(load_artifact("plan", run.path / "plan.v1.json"),
                         load_artifact("result", run.path / "result.v1.json"))
    assert (_record(run.path)["status"], _record(run.path)["stop"]) == (saved.state, None)


def test_complete_is_refused_while_a_planned_cell_has_no_measured_status(root):
    """docs/run-directories.md: "a run is refused complete while a planned cell has no measured status"."""
    run = open_run(root)
    run.write_plan(_fixture("plan.v1"))
    run.record_cell(_ids()[0], "pass")

    with pytest.raises(ValueError, match="not complete"):
        run.finish("complete")
    assert _record(run.path)["status"] == "running"
    assert not (run.path / "result.v1.json").exists()

    run.finish("incomplete")
    assert (_record(run.path)["status"], run.status) == ("incomplete", "incomplete")


def test_a_finish_whose_record_is_not_written_leaves_the_run_running_for_the_stop(root, monkeypatch):
    """docs/run-directories.md: "a block that raises records the stop": a finish interrupted while it
    writes run.json is not taken as the end of the run."""
    real = rundir.save_artifact

    def fails_on_the_end(kind, data, path, **kw):
        if kind == "run" and data["status"] == "complete":
            raise KeyboardInterrupt
        return real(kind, data, path, **kw)

    monkeypatch.setattr(rundir, "save_artifact", fails_on_the_end)
    with pytest.raises(KeyboardInterrupt):
        with open_run(root) as run:
            run.finish("complete")
    record = _record(run.path)
    assert (record["status"], record["stop"]) == ("incomplete", {"reason": "interrupted", "detail": None})


def test_a_stop_while_a_normal_end_is_written_is_recorded_as_that_stop(root, monkeypatch):
    """docs/run-directories.md: Ctrl-C gives "incomplete", "interrupted", also when it lands while the run's
    end is written after the block ended normally."""
    real, fired = rundir.save_artifact, []

    def interrupted_once(kind, data, path, **kw):
        if kind == "result" and not fired:
            fired.append(kind)
            raise KeyboardInterrupt
        return real(kind, data, path, **kw)

    monkeypatch.setattr(rundir, "save_artifact", interrupted_once)
    with pytest.raises(KeyboardInterrupt):
        with open_run(root) as run:
            run.write_plan(_fixture("plan.v1"))
            run.record_cell(_ids()[0], "pass")

    record = _record(run.path)
    assert (record["status"], record["stop"]) == ("incomplete", {"reason": "interrupted", "detail": None})
    assert [c["status"] for c in load_artifact("result", run.path / "result.v1.json")["cells"]] == \
        ["pass", *["not_run"] * 5]


def test_a_signal_while_a_normal_end_is_written_is_recorded_then_delivered_again(root, handlers, monkeypatch):
    """docs/run-directories.md: "the record is written, and the signal is delivered again", also when the
    signal lands while the end of a block that ended normally is written."""
    seen: list[str] = []
    signal.signal(signal.SIGTERM, lambda signum, frame: seen.append(_record(root / "n")["status"]))
    process.install_signal_handlers([signal.SIGTERM])
    real, fired = rundir.save_artifact, []

    def signalled_once(kind, data, path, **kw):
        if kind == "result" and not fired:
            fired.append(kind)
            signal.raise_signal(signal.SIGTERM)
        return real(kind, data, path, **kw)

    monkeypatch.setattr(rundir, "save_artifact", signalled_once)
    with pytest.raises(process.ShuttingDown):
        with open_run(root, name="n") as run:
            run.write_plan(_fixture("plan.v1"))

    assert seen == ["incomplete"]
    assert _record(run.path)["stop"]["reason"] == "terminated"
    assert (run.path / "result.v1.json").exists()
    assert process_hooks._hooks == []


def test_a_run_ends_once(root):
    """docs/run-directories.md: "A run ends once: a second finish is refused"."""
    run = open_run(root)
    run.finish("failed")
    with pytest.raises(ValueError, match="already ended: failed"):
        run.finish("complete")
    assert _record(run.path)["status"] == "failed"


def test_a_plan_is_saved_under_the_runs_lock_and_refused_once_the_run_ended(root, monkeypatch):
    """docs/run-directories.md: "result.v1.json ... when it has a plan": a finish in another thread cannot
    end the run between the plan's save and its taking effect, and an ended run takes no plan."""
    run = open_run(root)
    real = rundir.save_artifact
    held: list[bool] = []

    def saving(kind, data, path, **kw):
        if kind == "plan":
            worker = threading.Thread(target=lambda: held.append(run._lock.acquire(blocking=False)))
            worker.start()
            worker.join()
        return real(kind, data, path, **kw)

    monkeypatch.setattr(rundir, "save_artifact", saving)
    run.write_plan(_fixture("plan.v1"))
    assert held == [False]  # another thread cannot take the lock while the plan is saved
    run.finish("incomplete")
    assert (run.path / "result.v1.json").exists()

    ended = open_run(root / "other")
    ended.finish("failed")
    with pytest.raises(ValueError, match="already ended: failed"):
        ended.write_plan(_fixture("plan.v1"))
    assert not (ended.path / "plan.v1.json").exists()


def test_a_plan_changed_by_the_caller_after_its_save_does_not_change_the_result(root):
    """result.v1: plan_digest is the digest of the plan the run was given, plan.v1.json; a caller that
    changes the dict it passed in, or the one write_plan returned, does not change it."""
    given = _fixture("plan.v1")
    with open_run(root) as run:
        written = run.write_plan(given)
        given["budget"]["cells"] = 99
        written["budget"]["cells"] = 98
        for cid in _ids():
            run.record_cell(cid, "pass")

    saved = load_artifact("plan", run.path / "plan.v1.json")
    assert completeness(saved, load_artifact("result", run.path / "result.v1.json")).state == "complete"
    assert _record(run.path)["status"] == "complete"


def test_a_stop_while_the_plan_is_copied_leaves_no_plan_without_a_result(root, monkeypatch):
    """docs/run-directories.md: a run with a plan has "result.v1.json"; a stop that lands while the plan is
    taken in leaves no plan.v1.json behind for a result to miss."""
    def interrupted(value):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        with open_run(root) as run:
            monkeypatch.setattr(rundir.copy, "deepcopy", interrupted)
            run.write_plan(_fixture("plan.v1"))
    monkeypatch.undo()

    assert sorted(os.listdir(run.path)) == ["run.json"]
    assert _record(run.path)["artifacts"] == []


@pytest.mark.parametrize("published", [True, False], ids=["after-publishing", "before-publishing"])
def test_a_stop_inside_the_plans_save_leaves_a_result_exactly_when_the_plan_is_on_disk(root, monkeypatch,
                                                                                       published):
    """docs/run-directories.md: a run with a plan has "result.v1.json": a stop that lands in the save once
    plan.v1.json is published (os.replace done, the save not returned) still leaves the result."""
    real = rundir.save_artifact

    def stopped(kind, data, path, **kw):
        if kind == "plan":
            if published:
                real(kind, data, path, **kw)
            raise KeyboardInterrupt
        return real(kind, data, path, **kw)

    monkeypatch.setattr(rundir, "save_artifact", stopped)
    with pytest.raises(KeyboardInterrupt):
        with open_run(root) as run:
            run.write_plan(_fixture("plan.v1"))

    files = sorted(os.listdir(run.path))
    if published:
        assert files == ["plan.v1.json", "result.v1.json", "run.json"]
        result = load_artifact("result", run.path / "result.v1.json")
        assert completeness(load_artifact("plan", run.path / "plan.v1.json"), result).missing == tuple(_ids())
    else:
        assert files == ["run.json"]
    assert _record(run.path)["stop"]["reason"] == "interrupted"


@pytest.mark.parametrize("published", [True, False], ids=["after-publishing", "before-publishing"])
def test_a_stop_inside_a_second_plans_save_leaves_the_result_of_the_plan_on_disk(root, monkeypatch, published):
    """docs/run-directories.md: a result is "tied to its plan by plan_digest": when a second write_plan is
    stopped, the result is for whichever plan plan.v1.json holds, the earlier one if the new one never got there."""
    first = _fixture("plan.v1")
    second = {**_fixture("plan.v1"), "cells": first["cells"][:2], "budget": {**first["budget"], "cells": 2}}
    real = rundir.save_artifact

    def second_stopped(kind, data, path, **kw):
        if kind == "plan" and len(data["cells"]) == 2:
            if published:
                real(kind, data, path, **kw)
            raise KeyboardInterrupt
        return real(kind, data, path, **kw)

    monkeypatch.setattr(rundir, "save_artifact", second_stopped)
    with pytest.raises(KeyboardInterrupt):
        with open_run(root) as run:
            run.write_plan(first)
            run.record_cell(_ids()[0], "pass")
            run.write_plan(second)

    found = completeness(load_artifact("plan", run.path / "plan.v1.json"),
                         load_artifact("result", run.path / "result.v1.json"))
    assert found.state == "incomplete"  # not invalid: the result matches the plan on disk
    assert found.planned == (2 if published else 6)


def test_a_refused_plan_save_keeps_its_own_error(root, monkeypatch):
    """dokimi.artifacts.save_artifact: a malformed DOKIMI_RUN_ID is an InputError that says where the payload is
    kept; write_plan passes that error on, not one of its own."""
    run = open_run(root)
    monkeypatch.setenv(RUN_ID_ENV, "bad id!")
    with pytest.raises(InputError, match=r"payload kept at .*plan\.v1\.json\.rejected"):
        run.write_plan(_fixture("plan.v1"))
    assert not (run.path / "plan.v1.json").exists()


def test_a_cell_the_plan_lacks_or_a_status_result_v1_lacks_is_refused(root):
    """result.v1: a result "names cells the plan does not have" is invalid, and a status is one of its enum."""
    run = open_run(root)
    with pytest.raises(ValueError, match="not in the plan"):
        run.record_cell(_ids()[0], "pass")  # no plan yet
    run.write_plan(_fixture("plan.v1"))
    with pytest.raises(ValueError, match="not in the plan"):
        run.record_cell("c-0000000000000000", "pass")
    with pytest.raises(ValueError, match="'passed' is not a result.v1 status"):
        run.record_cell(_ids()[0], "passed")
    run.finish("incomplete")
    with pytest.raises(ValueError, match="already ended: incomplete"):
        run.record_cell(_ids()[0], "pass")  # the result is written: the cell would never reach it
    assert {c["status"] for c in load_artifact("result", run.path / "result.v1.json")["cells"]} == {"not_run"}


SIGNALS = [s for s in (getattr(signal, "SIGTERM", None), getattr(signal, "SIGHUP", None)) if s]


@pytest.fixture
def handlers():
    """The SIGTERM and SIGHUP handlers put back after the test."""
    before = {signum: signal.getsignal(signum) for signum in SIGNALS}
    yield
    for signum, handler in before.items():
        signal.signal(signum, handler)


@pytest.mark.parametrize("signum", SIGNALS, ids=lambda signum: signal.Signals(signum).name)
def test_a_signal_is_recorded_through_the_process_handler_then_delivered_again(root, handlers, signum):
    """docs/run-directories.md: the handler "kills the child process groups, then stops the run's block
    (dokimi.process_hooks); the record is written, and the signal is delivered again"."""
    seen: list[str] = []

    def previous(signum, frame):
        seen.append(_record(root / "n")["status"])

    signal.signal(signum, previous)
    process.install_signal_handlers([signum])
    with pytest.raises(process.ShuttingDown, match=f"{signal.Signals(signum).name}: the run stops"):
        with open_run(root, name="n") as run:
            run.write_plan(_fixture("plan.v1"))
            run.record_cell(_ids()[0], "pass")
            signal.raise_signal(signum)

    assert seen == ["incomplete"]
    assert process._closing is False  # the earlier handler returned: run() starts processes again
    assert process_hooks._hooks == []
    assert _record(run.path)["stop"] == {"reason": "terminated",
                                         "detail": f"{signal.Signals(signum).name}: the run stops"}
    cells = load_artifact("result", run.path / "result.v1.json")["cells"]
    assert [c["status"] for c in cells] == ["pass", *["not_run"] * 5]


@pytest.mark.parametrize("measured", [True, False])
def test_a_signal_the_block_caught_still_ends_the_run_terminated(root, handlers, measured):
    """docs/run-directories.md: a run that gets SIGTERM is "incomplete", "terminated", even when code in the
    block catches the ShuttingDown and the block ends normally."""
    signal.signal(signal.SIGTERM, lambda signum, frame: None)
    process.install_signal_handlers([signal.SIGTERM])
    with open_run(root, name="n") as run:
        run.write_plan(_fixture("plan.v1"))
        for cid in _ids() if measured else _ids()[:1]:
            run.record_cell(cid, "pass")
        try:
            signal.raise_signal(signal.SIGTERM)
        except process.ShuttingDown:
            pass
    record = _record(run.path)
    assert (record["status"], record["stop"]) == ("incomplete", {"reason": "terminated",
                                                                 "detail": "SIGTERM: the run stops"})


def test_a_signal_while_a_child_is_being_registered_stops_the_run_once_it_is(root, handlers, monkeypatch):
    """dokimi.process defers a signal that lands inside _spawn until the child is in _live, so the
    child is killed; the run's stop waits with it."""
    signal.signal(signal.SIGTERM, lambda signum, frame: None)
    process.install_signal_handlers([signal.SIGTERM])
    monkeypatch.setattr(process._spawning, "active", True, raising=False)
    monkeypatch.setattr(process._spawning, "deferred", [], raising=False)
    with pytest.raises(process.ShuttingDown):
        with open_run(root, name="n") as run:
            signal.raise_signal(signal.SIGTERM)
            assert (run.status, len(process._spawning.deferred)) == ("running", 1)
            process._spawning.active = False
            process._replay_deferred_signals()
    assert _record(run.path)["stop"]["reason"] == "terminated"


def test_an_ignored_sigterm_stays_ignored(root, handlers):
    """docs/run-directories.md: "a signal the process ignores stays ignored"."""
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    process.install_signal_handlers([signal.SIGTERM])
    with open_run(root) as run:
        signal.raise_signal(signal.SIGTERM)
    assert run.status == "complete"


def test_a_run_opened_off_the_main_thread_records_its_stop_too(root):
    """docs/run-directories.md: "Only a run opened on the main thread is stopped this way": a worker's run
    adds no hook, and records its own stops."""
    caught: list[BaseException] = []
    hooks: list[list] = []

    def work():
        try:
            with open_run(root, name="t") as run:
                hooks.append(list(process_hooks._hooks))
                run.write_plan(_fixture("plan.v1"))
                raise SpendCapReached("cap")
        except SpendCapReached as exc:
            caught.append(exc)

    worker = threading.Thread(target=work)
    worker.start()
    worker.join()

    assert (len(caught), hooks) == (1, [[]])
    assert _record(root / "t")["stop"] == {"reason": "spend_cap", "detail": "cap"}


_UNDER_THE_CLI = """
import json, signal, sys
from dokimi import process
from dokimi.runs import rundir
from dokimi.runs.rundir import open_run
process.install_signal_handlers()  # as dokimi.cli.main does
with open_run(sys.argv[1], name="t") as run:
    run.write_plan(json.loads(open(sys.argv[2], encoding="utf-8").read()))
    signal.raise_signal(signal.SIGTERM)
    sys.exit(3)  # not reached: the signal stops the block
"""


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX: a process dies of a signal only there")
def test_under_the_clis_handler_a_sigterm_still_kills_the_process_after_the_record(tmp_path):
    """docs/run-directories.md: "So the process exits as it did before (status 143 under the CLI)"."""
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps(_fixture("plan.v1")), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != RUN_ID_ENV}

    done = subprocess.run([sys.executable, "-c", _UNDER_THE_CLI, str(tmp_path / "results"), str(plan)],
                          cwd=PROJECT, env=env, capture_output=True, text=True, timeout=120, check=False)

    assert done.returncode == -signal.SIGTERM, done.stderr
    record = _record(tmp_path / "results" / "t")
    assert (record["status"], record["stop"]["reason"]) == ("incomplete", "terminated")
    assert {c["status"] for c in load_artifact("result", tmp_path / "results" / "t" / "result.v1.json")["cells"]} \
        == {"not_run"}


def test_runs_list_shows_a_run_directory_as_one_run(tmp_path, monkeypatch, capsys):
    """docs/run-directories.md: "They carry the run's envelope like any artifact, so runs list groups them,
    with run.json, as one run"."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DOKIMI_RUNS_DIR", raising=False)
    monkeypatch.setenv(RUN_ID_ENV, "other")
    save_artifact("eval", _fixture("eval.v1"), Path("results") / "eval.json")
    monkeypatch.setenv(RUN_ID_ENV, "r-dir")
    with open_run(Path("results"), name="nightly-7") as run:
        run.write_plan(_fixture("plan.v1"))
        for cid in _ids():
            run.record_cell(cid, "pass")
        save_artifact("arms", _fixture("arms.v1"), run.artifact_path("arms"))

    monkeypatch.setattr(sys, "argv", ["dokimi", "runs", "list", "--json"])
    cli.main()
    runs = json.loads(capsys.readouterr().out)["data"]["runs"]

    mine = [r for r in runs if r["run_id"] == "r-dir"]
    assert sorted(r["run_id"] for r in runs) == ["other", "r-dir"]
    assert mine[0]["kinds"] == ["arms", "plan", "result", "run"]
    assert sorted(mine[0]["paths"]) == ["nightly-7/arms.v1.json", "nightly-7/plan.v1.json",
                                        "nightly-7/result.v1.json", "nightly-7/run.json"]
