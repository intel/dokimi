# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`repo run` on a batch whose finished cell holds an unreadable `result.json` (contract §4). No docker, no LLM."""

from __future__ import annotations

import json

import pytest

from dokimi.cli import build_parser
from repo_eval_fixture import FakeRunner, fake_harness, make_repo
from test_repo_host import AMD_CPUINFO, HostRunner, _probe_out
from test_repo_host import _repo as _race_repo
from test_repo_host import _run as _run_on


@pytest.fixture
def setup(tmp_path, monkeypatch):
    root, _ = make_repo(tmp_path)
    exe, profile = fake_harness(tmp_path)
    runner = FakeRunner()
    monkeypatch.setattr("dokimi.repos.container.DockerRunner", lambda *a, **k: runner)
    monkeypatch.setenv("FAKE_ACTION", "fix")
    return {"root": root, "exe": exe, "profile": profile, "runner": runner, "out": tmp_path / "runs"}


def _run(s):
    argv = ["repo", "run", f"{s['root']}@HEAD", "--manifest", ".agent-eval/repo-eval.yaml", "--harness",
            str(s["profile"]), "--bin", str(s["exe"]), "--model", "m-1", "--batch", "b1", "--out", str(s["out"]),
            "--cache-root", str(s["out"] / "caches"), "--agent-network", "host", "--arm", "guidance:raw",
            "--task", "fix_add", "--reps", "3"]
    args = build_parser().parse_args(argv)
    args.func(args)


@pytest.mark.parametrize(("content", "problem"), [
    (None, "not valid JSON"),  # None: the cell's own result.json, cut in half
    (b"[]", "not a JSON object"),
    (b"\xff{}", "not valid UTF-8 text"),
])
def test_an_unreadable_result_is_that_cells_error_and_the_other_cells_still_run(setup, capsys, content, problem):
    _run(setup)
    capsys.readouterr()
    batch = setup["out"] / "b1"
    bad = batch / "fix_add__guidance-raw__m-1__r1" / "result.json"
    content = bad.read_bytes()[:len(bad.read_bytes()) // 2] if content is None else content
    bad.write_bytes(content)
    (batch / "fix_add__guidance-raw__m-1__r3" / "result.json").unlink()
    setup["runner"].specs.clear()
    with pytest.raises(SystemExit) as exit_:
        _run(setup)
    assert exit_.value.code == 1
    out = capsys.readouterr().out
    assert f"✗ fix_add__guidance-raw__m-1__r1: {bad}: {problem}" in out and "--force" in out
    assert "1 ran, 1 already done, 1 crashed" in out  # r2 reused, r3 run
    assert bad.read_bytes() == content  # left for the user to inspect
    assert len([s for s in setup["runner"].specs if "-p" in s.argv]) == 1


@pytest.mark.parametrize(("force", "fix_add"), [(False, "0 ran, 2 already done"), (True, "2 ran, 0 already done")])
def test_an_unreadable_kept_result_of_a_task_this_host_cannot_run_is_that_cells_error(
        tmp_path, monkeypatch, capsys, force, fix_add):
    root = _race_repo(tmp_path)
    out = tmp_path / "runs"
    _run_on(root, out, HostRunner(_probe_out(affinity=8)), monkeypatch, "--task", "fix_add", "--task", "fix_race")
    capsys.readouterr()
    listed, cut = (out / "b1" / f"fix_race__guidance-raw__m-1__r{r}" / "result.json" for r in (1, 2))
    listed.write_text("[]")
    cut.write_bytes(cut.read_bytes()[:len(cut.read_bytes()) // 2])
    before = [listed.read_bytes(), cut.read_bytes()]
    small = HostRunner(_probe_out(affinity=2, cpuinfo=AMD_CPUINFO))
    with pytest.raises(SystemExit) as exit_:  # --force keeps the cells of a task this host cannot run
        _run_on(root, out, small, monkeypatch, "--task", "fix_add", "--task", "fix_race", *(["--force"] * force))
    assert exit_.value.code == 1
    logged = capsys.readouterr().out
    assert f"✗ fix_race__guidance-raw__m-1__r1: {listed}: not a JSON object; this host cannot run" in logged
    assert f"✗ fix_race__guidance-raw__m-1__r2: {cut}: not valid JSON" in logged
    assert f"{fix_add}, 2 crashed, 0 not started (batch budget), 0 unsupported" in logged
    assert [listed.read_bytes(), cut.read_bytes()] == before  # neither rerun nor marked unsupported
    assert not list((out / "b1").glob("*/unsupported.json"))


def test_a_cell_whose_result_cannot_be_written_exits_5_and_the_other_cells_still_run(setup, monkeypatch, capsys):
    """A CellCrash is infra (exit 5): flipping its `infra` flag must fail this test, not pass unnoticed."""
    from dokimi.repos import runner

    write_json = runner._write_json

    def refuse_r1(path, data):
        if path.name == "result.json" and path.parent.name.endswith("__r1"):
            raise OSError("disk full")
        write_json(path, data)

    monkeypatch.setattr(runner, "_write_json", refuse_r1)
    with pytest.raises(SystemExit) as exit_:
        _run(setup)
    assert exit_.value.code == 5
    out = capsys.readouterr().out
    assert "✗ fix_add__guidance-raw__m-1__r1: cannot write result.json: disk full" in out
    assert "2 ran, 0 already done, 1 crashed" in out


def test_a_batch_spend_file_that_cannot_be_written_exits_5(setup, monkeypatch, capsys):
    """The spend file is the batch cap's record: a write failure is infra (exit 5), not a cell's input error."""
    from dokimi.repos import runner

    def refuse(batch_dir, spent):
        raise OSError("disk full")

    monkeypatch.setattr(runner, "_write_batch_spend", refuse)
    with pytest.raises(SystemExit) as exit_:
        _run(setup)
    assert exit_.value.code == 5
    captured = capsys.readouterr()
    assert "3 ran, 0 already done, 3 crashed" in captured.out  # one spend error per charged cell


def test_a_harness_that_exits_nonzero_before_any_trace_is_a_crash_exit_5(setup, monkeypatch, capsys):
    """A nonzero exit with no trace is the harness failing, not a verdict on the agent: exit 5, no result.json, so
    the next run executes the cell again (flipping the CellCrash for it must fail this test)."""
    monkeypatch.setenv("FAKE_ACTION", "silent")
    with pytest.raises(SystemExit) as exit_:
        _run(setup)
    assert exit_.value.code == 5
    assert not list((setup["out"] / "b1").glob("fix_add__*/result.json"))
    out = capsys.readouterr().out
    assert "0 ran, 0 already done, 3 crashed" in out  # the three reps of the cell
    assert "the harness exited 1 before its first trace line" in out
    setup["runner"].specs.clear()
    with pytest.raises(SystemExit) as again:  # the next run executes the cells again: none is kept
        _run(setup)
    assert again.value.code == 5 and "0 ran, 0 already done, 3 crashed" in capsys.readouterr().out
    assert len([s for s in setup["runner"].specs if "-p" in s.argv]) == 3


def test_a_nonzero_timed_out_harness_with_no_trace_is_a_fail_cell_not_a_crash(setup, monkeypatch):
    """The cap ran out before the agent wrote anything: a verdict on the cell (a fail), not a crash of the harness."""
    from dokimi.repos.container import ContainerResult

    fake = setup["runner"].run

    def time_out(spec, **kwargs):
        res = fake(spec, **kwargs)
        return ContainerResult(returncode=res.returncode, timed_out=True, wall_s=res.wall_s) if "-p" in spec.argv else res

    monkeypatch.setattr(setup["runner"], "run", time_out)
    monkeypatch.setenv("FAKE_ACTION", "silent")
    _run(setup)  # no SystemExit: the batch holds results
    rec = json.loads(next((setup["out"] / "b1").glob("fix_add__*__r1/result.json")).read_text(encoding="utf-8"))
    assert rec["agent"]["timed_out"] is True and rec["grade"]["success"] is False
