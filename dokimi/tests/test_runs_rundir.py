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

from dokimi import cli, process
from dokimi.artifacts import RUN_ID_ENV, load_artifact, save_artifact
from dokimi.eval.arm_stop import RunStopped
from dokimi.novelty.client import SpendCapReached
from dokimi.runner.plan import completeness
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


def test_a_run_ends_once(root):
    """docs/run-directories.md: "A run ends once: a second finish is refused"."""
    run = open_run(root)
    run.finish("failed")
    with pytest.raises(ValueError, match="already ended: failed"):
        run.finish("complete")
    assert _record(run.path)["status"] == "failed"


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


@pytest.fixture
def restore_sigterm(monkeypatch):
    """The process's SIGTERM handler and ``process._closing`` (``kill_children`` sets it) put back."""
    monkeypatch.setattr(process, "_closing", False)
    before = signal.getsignal(signal.SIGTERM)
    yield
    signal.signal(signal.SIGTERM, before)


def test_sigterm_in_the_block_writes_the_record_then_reaches_the_handler_it_replaced(root, restore_sigterm):
    """docs/run-directories.md: on SIGTERM "the record is written, then the signal goes back to the handler
    that was there before"."""
    seen: list[str] = []

    def previous(signum, frame):
        seen.append(_record(root / "n")["status"])

    signal.signal(signal.SIGTERM, previous)
    with pytest.raises(process.ShuttingDown):
        with open_run(root, name="n") as run:
            run.write_plan(_fixture("plan.v1"))
            run.record_cell(_ids()[0], "pass")
            assert signal.getsignal(signal.SIGTERM) != previous
            signal.raise_signal(signal.SIGTERM)

    assert seen == ["incomplete"]
    assert signal.getsignal(signal.SIGTERM) is previous
    assert _record(run.path)["stop"]["reason"] == "terminated"
    cells = load_artifact("result", run.path / "result.v1.json")["cells"]
    assert [c["status"] for c in cells] == ["pass", *["not_run"] * 5]


def test_an_ignored_sigterm_stays_ignored(root, restore_sigterm):
    """docs/run-directories.md: "a SIGTERM the process ignores stays ignored"."""
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    with open_run(root) as run:
        assert signal.getsignal(signal.SIGTERM) == signal.SIG_IGN
    assert run.status == "complete"


def test_a_run_opened_off_the_main_thread_records_its_stop_too(root, restore_sigterm):
    """signal.signal works only on the main thread: a worker's run leaves the handler alone."""
    before = signal.getsignal(signal.SIGTERM)
    caught: list[BaseException] = []

    def work():
        try:
            with open_run(root, name="t") as run:
                assert signal.getsignal(signal.SIGTERM) == before
                run.write_plan(_fixture("plan.v1"))
                raise SpendCapReached("cap")
        except SpendCapReached as exc:
            caught.append(exc)

    worker = threading.Thread(target=work)
    worker.start()
    worker.join()

    assert len(caught) == 1
    assert _record(root / "t")["stop"] == {"reason": "spend_cap", "detail": "cap"}


_UNDER_THE_CLI = """
import json, signal, sys
from dokimi import process
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
