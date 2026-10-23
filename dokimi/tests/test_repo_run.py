# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`repo run` and `repo report` with a fake runner and a fake harness (contract §1.1, §4). No docker, no LLM."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest
import yaml

from dokimi.cli import build_parser
from dokimi.repos.container import CONTENT_DIR, HARNESS_DIR, RUN_DIR, SOURCE_DIR, WORKSPACE
from dokimi.repos.report import format_report, model_matches, summarize, wilson
from repo_eval_fixture import FakeRunner, fake_harness, git, make_repo


@pytest.fixture
def setup(tmp_path, monkeypatch):
    root, _ = make_repo(tmp_path)
    exe, profile = fake_harness(tmp_path)
    runner = FakeRunner()
    monkeypatch.setattr("dokimi.repos.container.DockerRunner", lambda *a, **k: runner)
    monkeypatch.setenv("SECRET_NOT_ALLOWED", "x")
    monkeypatch.setenv("FAKE_ACTION", "fix")
    return {"root": root, "exe": exe, "profile": profile, "runner": runner, "out": tmp_path / "runs"}


def _run(s, *extra, network=True, source=None):
    argv = ["repo", "run", source or f"{s['root']}@HEAD", "--manifest", ".agent-eval/repo-eval.yaml", "--harness",
            str(s["profile"]), "--bin", str(s["exe"]), "--model", "m-1", "--batch", "b1", "--out", str(s["out"]),
            "--cache-root", str(s["out"] / "caches"), *(["--agent-network", "host"] if network else []), *extra]
    args = build_parser().parse_args(argv)
    args.func(args)


def _report(s):
    return summarize(s["out"] / "b1")


def test_run_isolates_the_agent_and_grades_each_cell(setup, capsys):
    _run(setup, "--arm", "guidance:none,guidance:raw", "--reps", "2", "--task", "fix_add")
    batch = setup["out"] / "b1"
    cells = sorted(p.parent.name for p in batch.glob("*/result.json"))
    assert cells == [f"fix_add__guidance-{a}__m-1__r{r}" for a in ("none", "raw") for r in (1, 2)]
    rd = batch / cells[0]
    for name in ("task.json", "meta.json", "events.jsonl", "answer.txt", "grade.json", "agent.stdout"):
        assert (rd / name).is_file(), name
    meta = json.loads((rd / "meta.json").read_text())
    assert meta["arm"] == "guidance:none" and meta["harness"] == "fake" and meta["harness_version"] == "1.2.3"
    assert meta["model"] == "m-1" and meta["model_resolved"] == "m-1" and meta["rep"] == 1
    assert meta["agent_network"] == "host"
    assert meta["egress_proxy"] is False and len(meta["start_sha"]) == 40
    events = [json.loads(line) for line in (rd / "events.jsonl").read_text().splitlines()]
    assert events == [{"kind": "edit", "path": "src/calc.py", "t": None}]  # relative to /eval/run/workspace
    assert (rd / "answer.txt").read_text() == "done: src/calc.py"
    assert (rd / "config/settings.json").read_text() == '{"hook": "python3 /eval/support/no_background.py"}'
    result = json.loads((rd / "result.json").read_text())
    assert result["grade"]["status"] == "graded" and result["grade"]["success"] is True
    assert result["agent"]["cost_usd"] == 0.01 and result["agent"]["num_turns"] == 3
    assert result["model_resolved"] == "m-1"

    agents = [s for s in setup["runner"].specs if s.argv and s.argv[0] == f"{HARNESS_DIR}/fake-agent"
              and "-p" in s.argv]
    assert len(agents) == 4
    spec = agents[0]
    mounted = {m.container for m in spec.mounts}
    assert mounted == {WORKSPACE, f"{RUN_DIR}/home", f"{RUN_DIR}/config", f"{RUN_DIR}/state", "/cache/build",
                       f"{HARNESS_DIR}/fake-agent", "/eval/support"}
    assert CONTENT_DIR not in mounted and SOURCE_DIR not in mounted and RUN_DIR not in mounted
    assert all(m.read_only for m in spec.mounts if m.container.startswith(("/eval/harness", "/eval/support")))
    assert spec.network == "host" and spec.workdir == WORKSPACE
    assert spec.env["FAKE_CONFIG_DIR"] == f"{RUN_DIR}/config" and "SECRET_NOT_ALLOWED" not in spec.env
    assert "PATH" not in spec.env and spec.env["TOY_STATE"] == f"{RUN_DIR}/state"
    prompt = spec.argv[spec.argv.index("-p") + 1]
    assert prompt == "add() is wrong; fix it.\n\nStay in the foreground."
    assert spec.argv[spec.argv.index("--max-budget-usd") + 1] == "2"
    grades = [s for s in setup["runner"].specs if "grade" in s.argv]
    assert grades and all(s.network == "none" for s in grades)

    summary = _report(setup)
    rows = {(r["task"], r["arm"]): r for r in summary["rows"]}
    raw = rows[("fix_add", "guidance:raw")]
    assert raw["n"] == 2 and raw["successes"] == 2 and raw["grader_failures"] == 0 and raw["cost_usd"] == 0.01
    assert summary["total_cost_usd"] == 0.04 and summary["unproxied_network"] == ["host"]
    assert raw["model_resolved"] == ["m-1"] and summary["model_mismatch"] == {}


def test_a_run_answered_by_another_model_is_recorded_and_reported(setup, monkeypatch, capsys):
    # The record must carry what the harness reported, not the request, or the mismatch warning never fires.
    monkeypatch.setenv("FAKE_ANSWER_MODEL", "m-0")
    _run(setup, "--arm", "guidance:raw", "--task", "fix_add")
    rec = json.loads((setup["out"] / "b1/fix_add__guidance-raw__m-1__r1/result.json").read_text())
    assert rec["model_resolved"] == "m-0"
    summary = _report(setup)
    assert summary["rows"][0]["model_resolved"] == ["m-0"]
    assert summary["model_mismatch"] == {"m-1": ["m-0"]}


@pytest.mark.outcome("cjm-repo-run-resume")
def test_already_graded_cells_are_skipped_unless_forced(setup, capsys):
    _run(setup, "--arm", "guidance:raw", "--task", "fix_add")
    assert "1 ran, 0 already done" in capsys.readouterr().out
    _run(setup, "--arm", "guidance:raw", "--task", "fix_add")
    assert "0 ran, 1 already done" in capsys.readouterr().out
    _run(setup, "--arm", "guidance:raw", "--task", "fix_add", "--force")
    assert "1 ran, 0 already done" in capsys.readouterr().out


def test_a_cell_is_reused_only_by_the_same_execution(setup, capsys):
    raw = yaml.safe_load(setup["profile"].read_text())
    raw["delegation"] = {"none": {"all": [*raw["tools"]["all"], "--no-agents"]}}
    setup["profile"].write_text(yaml.safe_dump(raw))
    _run(setup, "--arm", "guidance:raw", "--task", "fix_add")
    capsys.readouterr()
    rd = setup["out"] / "b1/fix_add__guidance-raw__m-1__r1"
    ran = json.loads((rd / "result.json").read_text())["execution"]
    assert ran["delegation"] == "native" and ran["tools"] == "all"
    assert json.loads((rd / "meta.json").read_text())["execution"] == ran
    with pytest.raises(SystemExit):  # refused before any cell runs
        _run(setup, "--arm", "guidance:raw", "--task", "fix_add", "--delegation", "none")
    assert "holds cells of another execution" in capsys.readouterr().err
    _run(setup, "--arm", "guidance:raw", "--task", "fix_add", "--delegation", "none", "--force")
    assert "1 ran" in capsys.readouterr().out
    rec = json.loads((rd / "result.json").read_text())
    assert rec["execution"]["delegation"] == "none" and rec["execution"]["id"] != ran["id"]
    agent = [s for s in setup["runner"].specs if "-p" in s.argv][-1]
    assert "--no-agents" in agent.argv


def test_force_does_not_pool_executions_across_the_plan(setup, capsys):
    raw = yaml.safe_load(setup["profile"].read_text())
    raw["delegation"] = {"none": {"all": [*raw["tools"]["all"], "--no-agents"]}}
    setup["profile"].write_text(yaml.safe_dump(raw))
    _run(setup, "--arm", "guidance:raw", "--task", "fix_add", "--reps", "2")
    capsys.readouterr()
    setup["runner"].specs.clear()
    with pytest.raises(SystemExit):  # r2 stays native next to a new r1 under none
        _run(setup, "--arm", "guidance:raw", "--task", "fix_add", "--reps", "1", "--delegation", "none", "--force")
    assert "holds cells of another execution" in capsys.readouterr().err
    assert not [s for s in setup["runner"].specs if "-p" in s.argv]
    # the cells of the plan itself are still replaced
    _run(setup, "--arm", "guidance:raw", "--task", "fix_add", "--reps", "2", "--delegation", "none", "--force")
    assert "2 ran" in capsys.readouterr().out


def test_a_result_from_before_execution_ids_is_not_reused(setup, capsys):
    _run(setup, "--arm", "guidance:raw", "--task", "fix_add")
    rd = setup["out"] / "b1/fix_add__guidance-raw__m-1__r1"
    rec = json.loads((rd / "result.json").read_text())
    del rec["execution"]  # written by an earlier version
    (rd / "result.json").write_text(json.dumps(rec))
    capsys.readouterr()
    with pytest.raises(SystemExit):
        _run(setup, "--arm", "guidance:raw", "--task", "fix_add")
    assert "unrecorded" in capsys.readouterr().err
    _run(setup, "--arm", "guidance:raw", "--task", "fix_add", "--force")
    assert "1 ran" in capsys.readouterr().out


def test_a_cell_finished_with_another_execution_is_not_reused_even_if_the_batch_check_is_skipped(
        setup, capsys, monkeypatch):
    """``run_cell`` guards itself: the up-front batch check is not the only thing between a cell and its reuse."""
    raw = yaml.safe_load(setup["profile"].read_text())
    raw["delegation"] = {"none": {"all": [*raw["tools"]["all"], "--no-agents"]}}
    setup["profile"].write_text(yaml.safe_dump(raw))
    _run(setup, "--arm", "guidance:raw", "--task", "fix_add")
    capsys.readouterr()
    setup["runner"].specs.clear()
    monkeypatch.setattr("dokimi.repos.runner.check_batch_executions", lambda *a, **k: None)
    with pytest.raises(SystemExit):
        _run(setup, "--arm", "guidance:raw", "--task", "fix_add", "--delegation", "none")
    assert "finished with another execution" in capsys.readouterr().out
    assert not [s for s in setup["runner"].specs if "-p" in s.argv]


def test_a_cell_finished_without_an_execution_is_not_reused_even_if_the_batch_check_is_skipped(
        setup, capsys, monkeypatch):
    _run(setup, "--arm", "guidance:raw", "--task", "fix_add")
    capsys.readouterr()
    result = setup["out"] / "b1/fix_add__guidance-raw__m-1__r1/result.json"
    done = json.loads(result.read_text())
    del done["execution"]
    result.write_text(json.dumps(done))
    setup["runner"].specs.clear()
    monkeypatch.setattr("dokimi.repos.runner.check_batch_executions", lambda *a, **k: None)
    with pytest.raises(SystemExit):
        _run(setup, "--arm", "guidance:raw", "--task", "fix_add")
    assert "unrecorded" in capsys.readouterr().out
    assert not [s for s in setup["runner"].specs if "-p" in s.argv]


def test_the_same_harness_bytes_resume_from_any_path(setup, capsys, tmp_path):
    _run(setup, "--arm", "guidance:raw", "--task", "fix_add")
    capsys.readouterr()
    moved = tmp_path / "elsewhere" / "fake-harness"
    moved.parent.mkdir()
    moved.write_bytes(setup["exe"].read_bytes())
    moved.chmod(0o755)
    setup["exe"].unlink()
    setup["exe"].symlink_to(moved)
    _run(setup, "--arm", "guidance:raw", "--task", "fix_add")
    assert "1 already done" in capsys.readouterr().out


def test_a_changed_harness_binary_is_refused_on_resume(setup, capsys):
    _run(setup, "--arm", "guidance:raw", "--task", "fix_add")
    capsys.readouterr()
    # Same name, same reported version, other bytes: the id is the executable's digest
    setup["exe"].write_text(setup["exe"].read_text() + "\n# upgraded in place\n")
    with pytest.raises(SystemExit):
        _run(setup, "--arm", "guidance:raw", "--task", "fix_add")
    assert "holds cells of another execution" in capsys.readouterr().err


def test_a_batch_dir_with_cells_of_another_execution_is_refused_up_front(setup, capsys):
    _run(setup, "--arm", "guidance:raw", "--task", "fix_add")
    capsys.readouterr()
    setup["runner"].specs.clear()
    with pytest.raises(SystemExit):
        _run(setup, "--arm", "guidance:raw,guidance:none", "--task", "fix_add", "--tools", "read")
    assert "holds cells of another execution" in capsys.readouterr().err
    assert not [s for s in setup["runner"].specs if "-p" in s.argv]


def test_the_cell_records_the_tools_mode_it_ran_with(setup):
    rd = setup["out"] / "b1/fix_add__guidance-raw__m-1__r1"
    _run(setup, "--arm", "guidance:raw", "--task", "fix_add", "--tools", "read")
    for name in ("result.json", "meta.json"):
        assert json.loads((rd / name).read_text())["execution"]["tools"] == "read", name


def test_an_unreadable_or_unparsable_result_does_not_stop_the_batch_check(setup, capsys):
    from dokimi.repos.harness_profile import load_profile
    from dokimi.repos.runner import AgentOptions, ExecutionMismatch, check_batch_executions

    _run(setup, "--arm", "guidance:raw", "--task", "fix_add")
    capsys.readouterr()
    batch = setup["out"] / "b1"
    (batch / "broken").mkdir()
    (batch / "broken" / "result.json").write_text("{not json", encoding="utf-8")  # ValueError
    (batch / "adir").mkdir()
    (batch / "adir" / "result.json").mkdir()  # OSError (a directory)
    options = [AgentOptions(profile=load_profile(setup["profile"]), model="m-1", harness_bin=setup["exe"],
                            harness_version="1.2.3")]
    check_batch_executions(batch, options)  # skipped, not raised
    (batch / "fix_add__guidance-raw__m-1__r1" / "result.json").unlink()
    (batch / "other").mkdir()
    (batch / "other" / "result.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ExecutionMismatch, match="unrecorded"):  # a parsable one is still checked
        check_batch_executions(batch, options)


def test_a_refused_delegation_none_starts_no_harness_version_probe(setup, monkeypatch, capsys):
    probes = []
    monkeypatch.setattr("dokimi.repos.runner.harness_version", lambda *a, **k: probes.append(a) or "1")
    with pytest.raises(SystemExit):
        _run(setup, "--arm", "guidance:raw", "--task", "fix_add", "--delegation", "none")
    capsys.readouterr()
    assert probes == []


def test_the_cell_records_the_delegation_whose_arguments_ran(setup, capsys):
    raw = yaml.safe_load(setup["profile"].read_text())
    raw["delegation"] = {"none": {"all": [*raw["tools"]["all"], "--no-agents"], "none": raw["tools"]["none"]}}
    setup["profile"].write_text(yaml.safe_dump(raw))
    rd = setup["out"] / "b1/fix_add__guidance-raw__m-1__r1"
    _run(setup, "--arm", "guidance:raw", "--task", "fix_add", "--tools", "none", "--delegation", "none")
    assert json.loads((rd / "result.json").read_text())["execution"]["delegation"] == "native"
    _run(setup, "--arm", "guidance:raw", "--task", "fix_add", "--tools", "all", "--delegation", "none", "--force")
    assert json.loads((rd / "result.json").read_text())["execution"]["delegation"] == "none"


def test_delegation_none_is_refused_before_anything_runs_when_the_profile_cannot(setup, capsys):
    with pytest.raises(SystemExit):
        _run(setup, "--arm", "guidance:raw", "--task", "fix_add", "--delegation", "none")
    assert "declares no `delegation: none`" in capsys.readouterr().err
    assert not [s for s in setup["runner"].specs if "-p" in s.argv]


def test_a_refused_delegation_none_leaves_no_batch_behind(setup, capsys):
    with pytest.raises(SystemExit):
        _run(setup, "--arm", "guidance:raw", "--task", "fix_add", "--delegation", "none")
    capsys.readouterr()
    assert not (setup["out"] / "b1").exists()  # no pin, no eval content, no image build for a refused run


@pytest.mark.outcome("cjm-repo-grader-crash")
def test_a_grader_crash_is_a_failure_not_an_exclusion(setup, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_ACTION", "break")  # the agent deletes src/calc.py, the grader cannot import it
    _run(setup, "--arm", "guidance:raw", "--task", "fix_add", "--reps", "2")
    rec = json.loads((setup["out"] / "b1/fix_add__guidance-raw__m-1__r1/result.json").read_text())
    assert rec["grade"]["status"] == "grader_failure" and rec["grade"]["success"] is False
    assert rec["grade"]["reasons"][0] == "grade exited 1"
    row = _report(setup)["rows"][0]
    assert row["n"] == 2 and row["successes"] == 0 and row["grader_failures"] == 2


@pytest.mark.outcome("cjm-repo-run-resume")
def test_score_only_task_and_missing_cells(setup, monkeypatch, capsys):
    _run(setup, "--arm", "guidance:raw", "--reps", "3")
    batch = setup["out"] / "b1"
    review = json.loads((batch / "review_add__guidance-raw__m-1__r1/result.json").read_text())
    assert review["grade"]["score_only"] and review["grade"]["score"] == 1.0 and review["grade"]["success"] is None
    (batch / "fix_add__guidance-raw__m-1__r3/result.json").unlink()   # crashed before it finished
    shutil.rmtree(batch / "fix_add__guidance-raw__m-1__r2")            # never started (only in the plan)
    summary = _report(setup)
    assert summary["missing"] == ["fix_add__guidance-raw__m-1__r2", "fix_add__guidance-raw__m-1__r3"]
    rows = {r["task"]: r for r in summary["rows"]}
    assert rows["fix_add"]["n"] == 1 and rows["fix_add"]["successes"] == 1 and rows["fix_add"]["missing"] == 2
    assert rows["review_add"]["kind"] == "score" and rows["review_add"]["score"] == 1.0
    _run(setup, "--arm", "guidance:raw", "--reps", "3")  # a rerun fills exactly the missing cells
    assert "2 ran, 4 already done" in capsys.readouterr().out
    args = build_parser().parse_args(["repo", "report", str(batch)])
    args.func(args)
    text = capsys.readouterr().out
    assert "fix_add" in text and "3/3 pass" in text and "score 1.00" in text and "instead of the egress proxy" in text


def test_agent_timeout_is_killed_and_still_graded(setup, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_ACTION", "sleep")
    _run(setup, "--arm", "guidance:raw", "--task", "fix_add", "--timeout", "2")
    rec = json.loads((setup["out"] / "b1/fix_add__guidance-raw__m-1__r1/result.json").read_text())
    assert rec["agent"]["timed_out"] is True and rec["agent"]["rc"] is None and rec["agent"]["wall_s"] < 30
    assert rec["grade"]["status"] == "graded" and rec["grade"]["success"] is False  # the bug is still there
    unmeasured = {k: rec["agent"][k] for k in ("input_tokens", "cache_read_tokens", "usage_scope", "subagent_calls")}
    assert unmeasured == {k: None for k in unmeasured}


def test_run_refuses_to_open_the_network_silently(setup, capsys):
    with pytest.raises(SystemExit) as exc:
        _run(setup, "--arm", "guidance:raw", network=False)
    assert exc.value.code == 1
    assert "egress proxy not implemented" in capsys.readouterr().err
    assert not list((setup["out"] / "b1").glob("*/meta.json"))


@pytest.mark.outcome("cjm-repo-run-egress")
def test_run_refuses_an_egress_task_before_any_cell(tmp_path, monkeypatch, capsys):
    root, _ = make_repo(tmp_path, {"fix_pip": {"family": "fix", "grader": "calc", "egress": ["pypi.org"],
                                               "prompt": "x"}})
    exe, profile = fake_harness(tmp_path)
    monkeypatch.setattr("dokimi.repos.container.DockerRunner", lambda *a, **k: FakeRunner())
    s = {"root": root, "exe": exe, "profile": profile, "out": tmp_path / "runs"}
    with pytest.raises(SystemExit) as exc:
        _run(s, "--arm", "guidance:raw", "--task", "fix_pip")  # --agent-network host does not open grading
    assert exc.value.code == 1 and "egress proxy not implemented" in capsys.readouterr().err
    assert not list((s["out"] / "b1").glob("*/meta.json"))


@pytest.mark.outcome("cjm-repo-run-egress")
def test_an_egress_task_refuses_a_batch_that_also_holds_ordinary_tasks(tmp_path, monkeypatch, capsys):
    root, _ = make_repo(tmp_path, {"fix_pip": {"family": "fix", "grader": "calc", "egress": ["pypi.org"],
                                               "prompt": "x"}})
    exe, profile = fake_harness(tmp_path)
    runner = FakeRunner()
    monkeypatch.setattr("dokimi.repos.container.DockerRunner", lambda *a, **k: runner)
    s = {"root": root, "exe": exe, "profile": profile, "out": tmp_path / "runs"}
    with pytest.raises(SystemExit) as exc:
        _run(s, "--arm", "guidance:raw")  # no --task: fix_add and review_add run unless the batch is refused first
    assert exc.value.code == 1 and "egress proxy not implemented" in capsys.readouterr().err
    assert not runner.specs  # no ordinary cell started, so none spent
    assert not [p for p in (s["out"] / "b1").glob("*") if p.name != "_eval"]  # only the batch's eval snapshot


def test_a_batch_keeps_its_eval_tree(setup, capsys):
    _run(setup, "--arm", "guidance:raw", "--task", "fix_add")
    (setup["root"] / "NEW").write_text("x")
    git(setup["root"], "add", "-A")
    git(setup["root"], "commit", "-qm", "later")
    with pytest.raises(SystemExit):
        _run(setup, "--arm", "guidance:raw", "--task", "fix_add")
    assert "use another --batch" in capsys.readouterr().err


def test_wilson_interval():
    assert wilson(0, 0) == (0.0, 1.0)
    lo, hi = wilson(3, 3)
    assert hi == 1.0 and 0.4 < lo < 0.5
    lo, hi = wilson(5, 10)
    assert lo < 0.5 < hi and round(lo + hi, 3) == 1.0


# ------------------------------------------------------------------ review of #82, round 3


def _host(spec, inside: str):
    """The host directory a container path of ``spec`` is, through its longest matching mount."""
    m = max((m for m in spec.mounts if inside == m.container or inside.startswith(m.container + "/")),
            key=lambda m: len(m.container))
    return (m.host / inside[len(m.container):].lstrip("/")).resolve()


def test_grade_home_is_not_agent_home(setup, monkeypatch, tmp_path):
    marker = tmp_path / "fsmonitor-ran"
    monkeypatch.setenv("FAKE_ACTION", "tamper")
    monkeypatch.setenv("FAKE_MARKER", str(marker))
    _run(setup, "--arm", "guidance:raw", "--task", "fix_add")
    specs = setup["runner"].specs
    agent = next(s for s in specs if "-p" in s.argv)
    grade = next(s for s in specs if "grade" in s.argv)
    agent_home, grade_home = _host(agent, agent.env["HOME"]), _host(grade, grade.env["HOME"])
    assert agent_home != grade_home and (agent_home / "agent-was-here").is_file()
    assert not grade_home.is_relative_to(agent_home) and not list(grade_home.iterdir())
    assert grade.env["PYTHONNOUSERSITE"] == "1"
    # the agent's core.fsmonitor did not run when the grader ran git, and its config is kept aside
    assert not marker.exists() and not marker.with_name(marker.name + ".pth").exists()
    rd = setup["out"] / "b1/fix_add__guidance-raw__m-1__r1"
    assert "fsmonitor" in (rd / "agent-git/config").read_text()
    assert "fsmonitor" not in (rd / "workspace/.git/config").read_text()


def test_timed_out_cell_counts_toward_batch_budget(setup, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_ACTION", "sleep")
    with pytest.raises(SystemExit) as exc:
        _run(setup, "--arm", "guidance:raw", "--task", "fix_add", "--reps", "2", "--timeout", "2",
             "--budget", "0.5", "--max-batch-usd", "0.01")
    assert exc.value.code == 1
    assert "1 ran" in (out := capsys.readouterr().out) and "1 not started (batch budget); $0.5000" in out
    rec = json.loads((setup["out"] / "b1/fix_add__guidance-raw__m-1__r1/result.json").read_text())
    assert rec["agent"]["cost_usd"] is None and rec["agent"]["charged_usd"] == 0.5
    assert _report(setup)["rows"][0]["cost_unknown"] == 1


def test_second_invocation_on_a_live_batch_is_refused(setup, capsys):
    runner, nested = setup["runner"], []
    first_run = runner.run

    def run(spec, **kw):
        if "-p" in spec.argv and not nested:  # while the first cell's agent is live
            nested.append(None)
            with pytest.raises(SystemExit) as exc:
                _run(setup, "--arm", "guidance:raw", "--task", "fix_add", "--force")
            nested[0] = (exc.value.code, capsys.readouterr().err)
        return first_run(spec, **kw)

    runner.run = run
    _run(setup, "--arm", "guidance:raw", "--task", "fix_add")
    assert nested[0][0] == 1 and "is in use by another `repo run`" in nested[0][1]
    rec = json.loads((setup["out"] / "b1/fix_add__guidance-raw__m-1__r1/result.json").read_text())
    assert rec["grade"]["success"] is True and sum("-p" in s.argv for s in runner.specs) == 1


def test_grade_exit_125_is_recorded_as_grader_failure(setup, capsys):
    from dokimi.repos.container import ContainerError

    runner = setup["runner"]
    first_run = runner.run

    def run(spec, **kw):
        if "grade" in spec.argv:  # what DockerRunner.run raises when `docker run` exits 125
            raise ContainerError("docker run failed: exit 125")
        return first_run(spec, **kw)

    runner.run = run
    _run(setup, "--arm", "guidance:raw")
    for task in ("fix_add", "review_add"):
        rec = json.loads((setup["out"] / f"b1/{task}__guidance-raw__m-1__r1/result.json").read_text())
        assert rec["grade"]["status"] == "grader_failure" and rec["grade"]["success"] is False
        assert rec["grade"]["reasons"][0] == "grade run failed: docker run failed: exit 125"
    summary = _report(setup)
    assert summary["missing"] == []
    rows = {r["task"]: r for r in summary["rows"]}
    assert rows["fix_add"]["kind"] == "gate" and rows["fix_add"]["successes"] == 0
    # test_report_kind_follows_task_score: the score task's only cell failed grading
    assert rows["review_add"]["kind"] == "score" and rows["review_add"]["score"] == 0.0


def test_worktree_image_tag_follows_the_build_context(setup, capsys):
    root, runner = setup["root"], setup["runner"]

    def check():
        argv = ["repo", "tasks", "check", str(root), "--manifest", ".agent-eval/repo-eval.yaml",
                "--out", str(setup["out"] / "chk"), "--cache-root", str(setup["out"] / "caches"), "--task", "fix_add"]
        args = build_parser().parse_args(argv)
        args.func(args)

    check()
    check()
    assert len(runner.builds) == 1  # unchanged context: the image is reused
    (root / ".agent-eval/Dockerfile").write_text("FROM scratch\nENV X=1\n")
    check()
    assert len(runner.builds) == 2 and len({t for t in runner.images if ":worktree-" in t}) == 2


def test_the_image_build_gets_the_build_timeout(setup):
    """--build-timeout bounds the image build; without it the build gets BUILD_TIMEOUT_S."""
    from dokimi.repos.container import BUILD_TIMEOUT_S

    root, runner = setup["root"], setup["runner"]

    def check(*extra):
        argv = ["repo", "tasks", "check", str(root), "--manifest", ".agent-eval/repo-eval.yaml",
                "--out", str(setup["out"] / "chk"), "--cache-root", str(setup["out"] / "caches"),
                "--task", "fix_add", "--rebuild-image", *extra]
        args = build_parser().parse_args(argv)
        args.func(args)

    check()
    check("--build-timeout", "36000")
    assert runner.build_timeouts == [BUILD_TIMEOUT_S, 36000]
    for parser_argv in (["repo", "run", str(root), "--arm", "guidance:none", "--model", "m", "--batch", "b"],
                        ["repo", "tasks", "check", str(root)]):
        assert build_parser().parse_args(parser_argv).build_timeout == BUILD_TIMEOUT_S


# ------------------------------------------------------------------ review of #82, round 4


def test_a_working_tree_batch_keeps_its_eval_dir(setup, capsys):
    root = setup["root"]
    _run(setup, "--arm", "guidance:raw", "--task", "fix_add", source=str(root))
    grader = root / ".agent-eval/grade.py"
    original = grader.read_text()
    grader.write_text(original + "# edited\n")
    with pytest.raises(SystemExit):
        _run(setup, "--arm", "guidance:raw", "--task", "review_add", source=str(root))
    assert "use another --batch" in capsys.readouterr().err
    batch = setup["out"] / "b1"
    assert (batch / "_eval/content/grade.py").read_text() == original  # the refused run changed nothing
    assert not list(batch.glob("review_add__*"))
    grader.write_text(original)  # back on the tree the batch started with
    _run(setup, "--arm", "guidance:raw", "--task", "review_add", source=str(root))
    assert (batch / "review_add__guidance-raw__m-1__r1/result.json").is_file()
    assert set(json.loads((batch / "_eval/batch.json").read_text())) == {"tree", "eval_dir", "image"}


def test_the_host_never_writes_or_reads_through_a_link_the_grader_left(setup, tmp_path, capsys):
    runner = setup["runner"]
    victim, planted = tmp_path / "victim.txt", tmp_path / "planted.json"
    victim.write_text("keep")
    planted.write_text(json.dumps({"schema": "repo_grade.v1", "task": "fix_add", "grader": "calc", "pass": True,
                                   "metrics": {"tests_pass": True}, "details": {}, "errors": []}))
    first_run = runner.run

    def run(spec, **kw):
        result = first_run(spec, **kw)
        if "grade" in spec.argv:  # what the agent's code could do while the grader runs it
            rd = Path(_host(spec, RUN_DIR))
            (rd / "result.json").symlink_to(victim)
            (rd / "grade.json").unlink()
            (rd / "grade.json").symlink_to(planted)
        return result

    runner.run = run
    _run(setup, "--arm", "guidance:raw", "--task", "fix_add")
    rd = setup["out"] / "b1/fix_add__guidance-raw__m-1__r1"
    assert victim.read_text() == "keep" and not (rd / "result.json").is_symlink()
    grade = json.loads((rd / "result.json").read_text())["grade"]
    assert grade["status"] == "grader_failure" and grade["pass"] is None
    assert "grade.json is not a regular file" in grade["reasons"]
    assert "result.json is not a regular file after grading" in grade["reasons"]


def test_a_docker_cli_error_in_one_cell_does_not_abort_the_batch(setup, capsys):
    import subprocess

    runner = setup["runner"]
    first_run = runner.run

    def run(spec, **kw):
        if "-p" in spec.argv and "fix_add" in spec.name:
            raise subprocess.TimeoutExpired(["docker", "kill", spec.name], 60)
        return first_run(spec, **kw)

    runner.run = run
    with pytest.raises(SystemExit):
        _run(setup, "--arm", "guidance:raw")
    out = capsys.readouterr()
    assert "1 ran" in out.out and "1 crashed" in out.out and "timed out after 60 seconds" in out.out


def _commit_manifest_env(root, env):
    import yaml

    path = root / ".agent-eval/repo-eval.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["image"]["env"] = env
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    git(root, "commit", "-qam", "env")


@pytest.mark.parametrize("value", ["{run_dir}//tmp", "{run_dir}/agent-git/x", "{run_dir}/../x"])
def test_run_refuses_a_scratch_dir_that_is_not_a_plain_name_before_any_container(setup, capsys, value):
    _commit_manifest_env(setup["root"], {"TMPDIR": value})
    with pytest.raises(SystemExit) as exc:
        _run(setup, "--arm", "guidance:raw")
    assert exc.value.code == 1 and "image/env/TMPDIR" in capsys.readouterr().err
    assert setup["runner"].specs == [] and not list((setup["out"] / "b1").glob("*/meta.json"))


def test_the_agent_container_never_mounts_a_host_directory_for_a_hostile_env_value(setup):
    from types import SimpleNamespace

    from dokimi.repos.runner import agent_layout, scratch_dirs
    from dokimi.repos.workspace import PrepError

    for value in ("{run_dir}//tmp", "{run_dir}/agent-git/x"):  # a manifest that skipped parse_manifest
        ctx = SimpleNamespace(manifest=SimpleNamespace(image=SimpleNamespace(env={"TMPDIR": value})))
        with pytest.raises(PrepError, match="image.env TMPDIR"):
            scratch_dirs(ctx)
        with pytest.raises(PrepError):
            agent_layout(ctx, setup["out"] / "rd", None)
    assert not (setup["out"] / "rd" / "agent-git").exists()


def test_every_entry_a_cell_leaves_in_its_run_dir_is_reserved_or_mounted(setup):
    from dokimi.repos.manifest import RESERVED_RUN_ENTRIES

    _run(setup, "--arm", "guidance:raw", "--task", "fix_add")
    rd = setup["out"] / "b1/fix_add__guidance-raw__m-1__r1"
    mounted = {"workspace", "home", "config", "state"}  # state: the fixture's image.env scratch dir
    assert {p.name for p in rd.iterdir()} <= RESERVED_RUN_ENTRIES | mounted


def _only_cell(setup):
    (path,) = (setup["out"] / "b1").glob("*/result.json")
    return json.loads(path.read_text())


def test_result_json_carries_the_token_fields_of_the_whole_tree(setup, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_USAGE", "1")
    _run(setup, "--arm", "guidance:raw", "--task", "fix_add")
    agent = _only_cell(setup)["agent"]
    assert (agent["input_tokens"], agent["output_tokens"], agent["cache_read_tokens"],
            agent["cache_write_tokens"]) == (37, 11, 100, 5)
    assert agent["usage_scope"] == "tree" and agent["subagent_calls"] == 1


def test_a_host_error_after_the_agent_is_a_charged_grader_failure(setup, monkeypatch, capsys):
    def boom(*a, **kw):
        raise PermissionError(13, "Permission denied", "workspace/.git/config")

    monkeypatch.setattr("dokimi.repos.runner.run_entry", boom)  # what _set_git_config_aside raises
    _run(setup, "--arm", "guidance:raw", "--task", "fix_add")
    rec = _only_cell(setup)
    assert rec["grade"]["status"] == "grader_failure" and rec["grade"]["success"] is False
    assert rec["grade"]["reasons"][0].startswith("host error after the agent ran (PermissionError)")
    assert rec["agent"]["charged_usd"] == 0.01  # the cost the harness reported
    out = capsys.readouterr().out
    assert "1 ran" in out and "0 crashed" in out and "$0.0100" in out
    assert _report(setup)["missing"] == [] and _report(setup)["rows"][0]["grader_failures"] == 1


def test_a_trace_that_cannot_be_converted_charges_the_cell_cap(setup, monkeypatch, capsys):
    def boom(*a, **kw):
        raise ValueError("unreadable trace")

    monkeypatch.setattr("dokimi.repos.runner.convert", boom)
    _run(setup, "--arm", "guidance:raw", "--task", "fix_add", "--budget", "0.5")
    rec = _only_cell(setup)
    assert rec["grade"]["status"] == "grader_failure" and "unreadable trace" in rec["grade"]["reasons"][0]
    assert rec["agent"]["cost_usd"] is None and rec["agent"]["charged_usd"] == 0.5
    assert all(rec["agent"][k] is None for k in ("input_tokens", "usage_scope", "subagent_calls"))
    assert "1 ran" in (out := capsys.readouterr().out) and "$0.5000" in out


def test_a_batch_budget_counts_a_cell_whose_grading_failed(setup, monkeypatch, capsys):
    monkeypatch.setattr("dokimi.repos.runner.run_entry", lambda *a, **kw: 1 / 0)
    with pytest.raises(SystemExit):
        _run(setup, "--arm", "guidance:raw", "--task", "fix_add", "--reps", "2", "--budget", "0.5",
             "--max-batch-usd", "0.01")
    out = capsys.readouterr().out
    assert "1 ran" in out and "1 not started (batch budget)" in out


posix_only = pytest.mark.skipif(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                                reason="needs POSIX permissions and a non-root user")


@posix_only
def test_an_agent_that_makes_git_read_only_fails_its_cell_and_a_forced_rerun_clears_it(setup, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_ACTION", "lock")
    _run(setup, "--arm", "guidance:raw", "--task", "fix_add")
    rec = _only_cell(setup)
    assert rec["grade"]["status"] == "grader_failure" and "PermissionError" in rec["grade"]["reasons"][0]
    assert rec["agent"]["charged_usd"] == 0.01 and "1 ran" in capsys.readouterr().out
    _run(setup, "--arm", "guidance:raw", "--task", "fix_add", "--force")  # rmtree of the read-only tree
    assert "1 ran" in capsys.readouterr().out and _only_cell(setup)["grade"]["status"] == "grader_failure"


@posix_only
def test_rmtree_clears_directories_an_agent_made_read_only(tmp_path):
    from dokimi.repos.runner import _rmtree

    deep = tmp_path / "rd/workspace/.git/objects"
    deep.mkdir(parents=True)
    (deep / "x").write_text("x")
    for d in (deep, deep.parent, tmp_path / "rd/workspace"):
        d.chmod(0o555)
    _rmtree(tmp_path / "rd")
    assert not (tmp_path / "rd").exists()


@posix_only
def test_a_grader_that_makes_the_run_dir_read_only_still_gets_a_result(setup, capsys):
    runner = setup["runner"]
    first_run = runner.run

    def run(spec, **kw):
        result = first_run(spec, **kw)
        if "grade" in spec.argv:
            Path(_host(spec, RUN_DIR)).chmod(0o555)
        return result

    runner.run = run
    _run(setup, "--arm", "guidance:raw", "--task", "fix_add")
    rec = json.loads((setup["out"] / "b1/fix_add__guidance-raw__m-1__r1/result.json").read_text())
    assert rec["grade"]["success"] is True


def test_report_kind_is_the_runners_not_the_cells_task_json(setup, capsys):
    runner = setup["runner"]
    first_run = runner.run

    def run(spec, **kw):
        result = first_run(spec, **kw)
        if "grade" in spec.argv:  # the grader runs the agent's code with /eval/run writable
            task_json = Path(_host(spec, RUN_DIR)) / "task.json"
            task_json.write_text(json.dumps({**json.loads(task_json.read_text()),
                                             "score": {"metric": "tests_pass", "max": 1}}))
        return result

    runner.run = run
    _run(setup, "--arm", "guidance:raw", "--task", "fix_add")
    rec = json.loads((setup["out"] / "b1/fix_add__guidance-raw__m-1__r1/result.json").read_text())
    assert rec["score_task"] is False
    row = _report(setup)["rows"][0]
    assert row["kind"] == "gate" and row["successes"] == 1


def test_report_warns_when_another_model_answered(setup, capsys):
    _run(setup, "--arm", "guidance:none,guidance:raw", "--task", "fix_add")
    path = setup["out"] / "b1/fix_add__guidance-raw__m-1__r1/result.json"
    path.write_text(json.dumps({**json.loads(path.read_text()), "model_resolved": ["m-0", "m-1"]}))
    summary = _report(setup)
    assert [r["model_resolved"] for r in summary["rows"]] == [["m-1"], ["m-0", "m-1"]]
    assert summary["model_mismatch"] == {"m-1": ["m-0"]}
    assert ("warning: cells run with model m-1 were answered by m-0; check the harness's model aliases"
            in format_report(summary).splitlines())


@pytest.mark.parametrize("requested, resolved, same", [
    ("m-1", "m-1", True), ("us.anthropic.claude-sonnet-5-5", "claude-sonnet-5-5", True),
    ("us.anthropic.claude-haiku-4-5-20251001-v1:0", "claude-haiku-4-5-20251001", True),
    ("haiku", "claude-haiku-3", False), ("us.anthropic.claude-opus-5-5", "claude-opus-5", False),
    ("claude-sonnet-4-5", "claude-sonnet-4-5-20250929", True), ("claude-sonnet-4-5", "claude-sonnet-4-5@20250929", True),
    ("claude-sonnet-4-5-20250929", "claude-sonnet-4-5", True),
    ("claude-3-5-sonnet-20240620", "claude-3-5-sonnet-20241022", False),
    ("deepseek-v2", "deepseek-v3", False), ("x.claude-haiku", "haiku", False),
    ("claude-sonnet-4-5@20250929", "claude-sonnet-4-5-20250929", True),
    ("claude-sonnet-4-5", "us.anthropic.claude-sonnet-4-5-20250929-v1:0", True),
    ("claude-sonnet-4-5@20250929", "claude-sonnet-4-5-20241022", False),
    ("us.anthropic.claude-x", "eu.anthropic.claude-x", True), ("apac.anthropic.claude-x-v1:0", "us-gov.anthropic.claude-x", True),
    ("m-v1:0", "m-v3:0", True), ("gpt-4.1", "gpt-4.2", False), ("us.anthropic.claude-x", "eu.anthropic.claude-y", False),
    ("vertex_ai/claude-x", "claude-x", True), ("claude-sonnet-4-5", "claude-sonnet-4-5-1", False)])
def test_a_provider_prefix_is_not_another_model(requested, resolved, same):
    assert model_matches(requested, resolved) is same


@pytest.mark.parametrize("key, value", [("budget_usd", float("nan")), ("timeout_s", float("inf")),
                                        ("max_turns", 0)])
def test_run_refuses_a_task_cap_that_caps_nothing(tmp_path, monkeypatch, capsys, key, value):
    root, _ = make_repo(tmp_path, {"capped": {"family": "fix", "grader": "calc", "prompt": "x", key: value}})
    exe, profile = fake_harness(tmp_path)
    monkeypatch.setattr("dokimi.repos.container.DockerRunner", lambda *a, **k: FakeRunner())
    s = {"root": root, "exe": exe, "profile": profile, "out": tmp_path / "runs"}
    with pytest.raises(SystemExit) as exc:
        _run(s, "--arm", "guidance:raw")
    assert exc.value.code == 1 and f"task capped: {key} must be a positive, finite number" in capsys.readouterr().err
    assert not list((s["out"] / "b1").glob("*/meta.json"))  # refused before any cell


def test_a_cell_whose_result_cannot_be_written_is_still_charged(setup, monkeypatch, capsys):
    from dokimi.repos import runner as runner_mod

    write = runner_mod._write_json

    def failing(path, data):
        if path.name == "result.json":
            raise OSError(28, "No space left on device")
        write(path, data)

    monkeypatch.setattr(runner_mod, "_write_json", failing)
    with pytest.raises(SystemExit):
        _run(setup, "--arm", "guidance:raw", "--task", "fix_add")
    out = capsys.readouterr()
    assert "0 ran, 0 already done, 1 crashed" in out.out and "$0.0100" in out.out
    assert "cannot write result.json" in out.out + out.err



def test_a_batch_refuses_resume_after_a_built_in_grader_changes_only_if_a_task_names_one(setup, monkeypatch,
                                                                                         capsys):
    _run(setup, "--arm", "guidance:raw", "--task", "fix_add")
    monkeypatch.setattr("dokimi.repos.graders.graders_digest", lambda: "edited")
    _run(setup, "--arm", "guidance:none", "--task", "fix_add")  # no task names one: no score depends on them
    assert "graders" not in json.loads((setup["out"] / "b1/_eval/batch.json").read_text())
    root = setup["root"]
    shutil.copytree(root / ".agent-eval/tasks/review_add", root / ".agent-eval/tasks/seeds_add")
    task = root / ".agent-eval/tasks/seeds_add/task.json"
    task.write_text(json.dumps({**json.loads(task.read_text()), "grader": "dokimi:review_seeds"}))
    (task.parent / "manifest.json").write_text(json.dumps({"seeds": [], "decoys": []}))
    git(root, "add", "-A")
    git(root, "commit", "-qm", "a built-in task")
    _run(setup, "--arm", "guidance:raw", "--task", "fix_add", "--batch", "b2")
    assert json.loads((setup["out"] / "b2/_eval/batch.json").read_text())["graders"] == "edited"
    monkeypatch.setattr("dokimi.repos.graders.graders_digest", lambda: "edited again")
    with pytest.raises(SystemExit):
        _run(setup, "--arm", "guidance:none", "--task", "fix_add", "--batch", "b2")
    err = capsys.readouterr().err
    assert "graders" in err and "use another --batch" in err
    assert not list((setup["out"] / "b2").glob("fix_add__guidance-none__*"))


@pytest.mark.parametrize(("text", "pinned"), [("{", True), ("[]", False)])
def test_a_task_json_the_pin_cannot_read_pins_the_graders_and_a_non_object_one_does_not(setup, text, pinned):
    d = setup["root"] / ".agent-eval/tasks/zz_other"
    d.mkdir()
    (d / "task.json").write_text(text)
    git(setup["root"], "add", "-A")
    git(setup["root"], "commit", "-qm", "an unreadable task the batch does not run")
    _run(setup, "--arm", "guidance:raw", "--task", "fix_add")
    assert ("graders" in json.loads((setup["out"] / "b1/_eval/batch.json").read_text())) is pinned


def test_graders_digest_follows_the_grader_sources(tmp_path, monkeypatch):
    from dokimi.repos import graders

    (tmp_path / "review.py").write_text("VERSION = '1'\n")
    monkeypatch.setattr(graders, "HOST_DIR", tmp_path)
    before = graders.graders_digest()
    (tmp_path / "review.py").write_text("VERSION = '2'\n")
    assert graders.graders_digest() != before


def test_graders_digest_covers_main_and_pairs_names_with_contents(tmp_path, monkeypatch):
    from dokimi.repos import graders

    monkeypatch.setattr(graders, "HOST_DIR", tmp_path)
    (tmp_path / "main.py").write_text("ENTRY = 1\n")
    (tmp_path / "review.py").write_text("VERSION = 1\n")
    base = graders.graders_digest()
    assert graders.graders_digest() == base  # deterministic
    (tmp_path / "main.py").write_text("ENTRY = 2\n")  # the entry point is part of the score
    edited = graders.graders_digest()
    assert edited != base
    (tmp_path / "main.py").write_text("VERSION = 1\n")  # swap the contents between the files
    (tmp_path / "review.py").write_text("ENTRY = 1\n")
    swapped = graders.graders_digest()
    assert swapped not in (base, edited)
    (tmp_path / "main.py").write_text("ENTRY = 1\n")  # same contents, names swapped back and forth
    (tmp_path / "review.py").write_text("VERSION = 1\n")
    assert graders.graders_digest() == base
    (tmp_path / "main.py").rename(tmp_path / "other.py")  # same bytes under another name
    assert graders.graders_digest() != base
