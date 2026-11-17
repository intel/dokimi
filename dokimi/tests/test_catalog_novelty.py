# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`catalog novelty ROOT`: the paid sweep, run end to end on a fake LLM, offline."""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal

import pytest

from dokimi.cli import build_parser
from dokimi.metrics.usage import UsageRecord
from dokimi.novelty import cost as novelty_cost
from dokimi.utils import InputError
from tests.test_novelty_analyze import SKILL, _scripted

COST = 0.01  # every fake call is priced at this, so a budget is a number of calls


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.delenv("DOKIMI_RESPONSE_CACHE", raising=False)
    base = _scripted("informed")

    def transport(prompt, system):
        text, _ = base(prompt, system)
        return text, UsageRecord(cost_usd=COST)

    real_client = novelty_cost.client

    def fake_client(*args, **kwargs):
        client = real_client(*args, **kwargs)
        client.transport = transport
        return client

    monkeypatch.setattr(novelty_cost, "client", fake_client)


def _root(tmp_path, names=("accel-tuning", "other-skill")):
    root = tmp_path / "catalog"
    for name in names:
        (root / name).mkdir(parents=True)
        (root / name / "SKILL.md").write_text(SKILL.replace("accel-tuning", name), encoding="utf-8")
    return root


def _sweep(root, out, *extra):
    argv = ["catalog", "novelty", str(root), "--out-dir", str(out),
            "--model", "answerer-m", "--judge-model", "judge-m", *extra]
    args = build_parser().parse_args(argv)
    args.func(args)


def _rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_each_skill_gets_one_measured_row_and_a_novelty_file(tmp_path, capsys):
    root, out = _root(tmp_path), tmp_path / "out"
    _sweep(root, out, "--max-usd", "5")
    rows = _rows(out / "sweep.jsonl")
    assert sorted(r["key"] for r in rows) == ["accel-tuning", "other-skill"]
    assert all(r["status"] == "ok" and r["new_lenient"] is not None for r in rows)
    assert all(r["usd"] == pytest.approx(r["llm_calls"] * COST) for r in rows)
    assert len(list(out.glob("*/novelty.json"))) == 2
    assert "# novelty sweep: 2 measured, 0 partial, 0 error" in capsys.readouterr().out


def test_the_summary_leads_with_the_already_known_share(tmp_path, capsys):
    _sweep(_root(tmp_path), tmp_path / "out", "--max-usd", "5")
    out = capsys.readouterr().out
    assert "already known by the model:" in out
    assert "new to it:" in out


def test_a_per_skill_cap_leaves_a_partial_row_and_the_sweep_goes_on(tmp_path):
    out = tmp_path / "out"
    _sweep(_root(tmp_path), out, "--max-usd", "5", "--max-usd-per-skill", "0.02")
    rows = _rows(out / "sweep.jsonl")
    assert [r["status"] for r in rows] == ["partial", "partial"]
    assert {r["reason"] for r in rows} == {"spend_cap"}
    assert not list(out.glob("*/novelty.json"))


def test_the_run_budget_stops_the_skills_it_cannot_pay_for(tmp_path, capsys):
    out = tmp_path / "out"
    _sweep(_root(tmp_path), out, "--max-usd", "0.05", "--jobs", "1")
    rows = _rows(out / "sweep.jsonl")
    assert len(rows) == 1  # the second skill was never started
    assert rows[0]["status"] == "partial"
    assert "1 skill(s) not run: the budget was spent" in capsys.readouterr().err


def test_resume_keeps_the_measured_rows_and_runs_only_the_rest(tmp_path, capsys):
    root, out = _root(tmp_path), tmp_path / "out"
    _sweep(root, out, "--max-usd", "5", "--limit", "1")
    first = _rows(out / "sweep.jsonl")
    assert len(first) == 1
    _sweep(root, out, "--max-usd", "5", "--resume")
    rows = _rows(out / "sweep.jsonl")
    assert sorted(r["key"] for r in rows) == ["accel-tuning", "other-skill"]
    assert rows[0] == first[0]
    assert "1 measured and kept, 1 to run" in capsys.readouterr().err


def test_resume_runs_again_a_skill_whose_directory_changed(tmp_path):
    root, out = _root(tmp_path), tmp_path / "out"
    _sweep(root, out, "--max-usd", "5", "--limit", "1")
    before = _rows(out / "sweep.jsonl")[0]["skill_sha256"]
    (root / "accel-tuning" / "SKILL.md").write_text(SKILL + "\nMore text.\n", encoding="utf-8")
    _sweep(root, out, "--max-usd", "5", "--resume")
    rows = _rows(out / "sweep.jsonl")
    assert sorted(r["key"] for r in rows) == ["accel-tuning", "other-skill"]  # the stale row is gone
    changed = [r for r in rows if r["key"] == "accel-tuning"]
    assert changed[0]["skill_sha256"] != before and changed[0]["status"] == "ok"


def test_an_existing_jsonl_is_not_overwritten_without_resume(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "sweep.jsonl").write_text("", encoding="utf-8")
    with pytest.raises(InputError, match="pass --resume"):
        _sweep(_root(tmp_path), out, "--max-usd", "5")


def test_a_paid_sweep_needs_a_whole_run_budget(tmp_path):
    with pytest.raises(InputError, match="pass --max-usd"):
        _sweep(_root(tmp_path), tmp_path / "out")


def test_summary_only_reads_the_jsonl_and_makes_no_call(tmp_path, monkeypatch, capsys):
    out, root = tmp_path / "out", _root(tmp_path)
    _sweep(root, out, "--max-usd", "5")
    capsys.readouterr()

    def no_client(*args, **kwargs):
        raise AssertionError("summary-only must not build a client")

    monkeypatch.setattr(novelty_cost, "client", no_client)
    _sweep(root, out, "--summary-only")
    assert "# novelty sweep: 2 measured" in capsys.readouterr().out


def test_strata_add_a_line_per_stratum(tmp_path, capsys):
    out = tmp_path / "out"
    strata = tmp_path / "strata.csv"
    strata.write_text("skill,stratum\naccel-tuning,ops\nother-skill,dev\n", encoding="utf-8")
    _sweep(_root(tmp_path), out, "--max-usd", "5", "--strata", str(strata))
    text = capsys.readouterr().out
    assert "stratum" in text and "\nops " in text and "\ndev " in text


def test_an_unloadable_skill_is_an_error_row_and_costs_nothing(tmp_path):
    root, out = _root(tmp_path), tmp_path / "out"
    (root / "broken").mkdir()
    (root / "broken" / "SKILL.md").write_text("no frontmatter here\n", encoding="utf-8")
    _sweep(root, out, "--max-usd", "5")
    broken = [r for r in _rows(out / "sweep.jsonl") if r["key"] == "broken"]
    assert broken and broken[0]["status"] == "error"
    assert broken[0]["usd"] == 0


def test_a_narrower_resume_keeps_the_paid_rows_of_the_skills_it_does_not_select(tmp_path):
    root, out = _root(tmp_path), tmp_path / "out"
    _sweep(root, out, "--max-usd", "5")
    before = {r["key"]: r for r in _rows(out / "sweep.jsonl")}
    _sweep(root, out, "--max-usd", "5", "--resume", "--limit", "1")
    after = {r["key"]: r for r in _rows(out / "sweep.jsonl")}
    assert after == before  # other-skill is outside this run's --limit: its paid row stays


def test_a_skill_with_no_paired_probe_is_an_error_not_a_share_of_one(tmp_path, monkeypatch):
    """The baseline arm answered nothing: k0 is 0.0 by default, so the new share reads 1.0 unless no pair is checked."""
    from types import SimpleNamespace

    import dokimi.artifacts as artifacts
    import dokimi.commands.catalog_novelty as catalog_novelty
    import dokimi.novelty.report as report

    artifact = {"k0": {"lenient": 0.0, "strict": 0.0}, "paired": {"lenient": {"n_paired": 0}},
                "artifact": {"tokens": 100}}
    monkeypatch.setattr(catalog_novelty, "_analyze_skill",
                        lambda *a, **k: SimpleNamespace(partial=None, artifact=artifact))
    monkeypatch.setattr(artifacts, "save_artifact", lambda kind, art, path: path.parent.mkdir(parents=True))
    monkeypatch.setattr(report, "render_novelty_markdown", lambda artifact: "")
    root, out = _root(tmp_path, names=("accel-tuning",)), tmp_path / "out"
    _sweep(root, out, "--max-usd", "5")
    [row] = _rows(out / "sweep.jsonl")
    assert row["status"] == "error" and row["reason"] == "no_pairs"
    assert row["new_lenient"] is None


def test_a_save_that_fails_is_that_skills_error_row_and_the_sweep_goes_on(tmp_path, monkeypatch):
    import dokimi.artifacts as artifacts

    real_save = artifacts.save_artifact

    def save(kind, artifact, path, *a, **k):
        if "accel-tuning" in str(path):
            raise OSError("disk full")
        return real_save(kind, artifact, path, *a, **k)

    monkeypatch.setattr(artifacts, "save_artifact", save)
    root, out = _root(tmp_path), tmp_path / "out"
    _sweep(root, out, "--max-usd", "5")  # must not raise: one skill's save is its row's error
    rows = {r["key"]: r for r in _rows(out / "sweep.jsonl")}
    assert rows["accel-tuning"]["status"] == "error"
    assert "disk full" in rows["accel-tuning"]["message"]
    assert rows["other-skill"]["status"] == "ok"


@pytest.mark.parametrize("flag", [["--max-file-words", "100"], ["--max-resource-words", "100"]])
def test_resume_runs_again_the_rows_measured_under_other_input_limits(tmp_path, capsys, flag):
    root, out = _root(tmp_path), tmp_path / "out"
    _sweep(root, out, "--max-usd", "5")
    capsys.readouterr()
    _sweep(root, out, "--max-usd", "5", "--resume", *flag)
    assert "0 measured and kept, 2 to run" in capsys.readouterr().err


@pytest.mark.parametrize("signum, code", [(signal.SIGINT, 130), (signal.SIGTERM, 143)])
def test_an_interrupt_cancels_the_queued_skills_keeps_the_rows_done_and_exits_nonzero(
        tmp_path, monkeypatch, signum, code):
    """Ctrl-C or SIGTERM mid-sweep: the skill in flight finishes and keeps its row, the queued skills
    never start (no paid call), and the process exits nonzero. The JSONL stays resumable."""
    import time
    from types import SimpleNamespace

    import dokimi.artifacts as artifacts
    import dokimi.commands.catalog_novelty as catalog_novelty
    import dokimi.novelty.report as report
    import dokimi.process as process

    kills: list[bool] = []
    monkeypatch.setattr(process, "kill_children", lambda: kills.append(True))
    artifact = {"k0": {"lenient": 0.2, "strict": 0.1}, "paired": {"lenient": {"n_paired": 3}},
                "artifact": {"tokens": 100}}
    started = []

    def analyze(args, *a, **k):
        name = args.artifact.rsplit("/", 1)[-1]
        started.append(name)
        if len(started) == 1:  # the signal arrives while the first skill is in flight; it finishes
            os.kill(os.getpid(), signum)
            time.sleep(0.5)  # the main thread cancels the queued skills well inside this
        return SimpleNamespace(partial=None, artifact=artifact)

    monkeypatch.setattr(catalog_novelty, "_analyze_skill", analyze)
    monkeypatch.setattr(artifacts, "save_artifact", lambda kind, art, path: path.parent.mkdir(parents=True))
    monkeypatch.setattr(report, "render_novelty_markdown", lambda artifact: "")
    root = _root(tmp_path, names=("accel-tuning", "beta", "gamma", "other-skill"))
    out = tmp_path / "out"
    # a SIGTERM the sweep does not handle would end pytest itself: the stray handler keeps that a failure
    stray: list[int] = []
    previous = signal.signal(signal.SIGTERM, lambda s, f: stray.append(s))
    try:
        try:
            _sweep(root, out, "--max-usd", "5", "--jobs", "1")
        except SystemExit as exc:
            code_seen = exc.code
        except KeyboardInterrupt:  # not turned into an exit: fail here, not as an aborted pytest run
            pytest.fail("the interrupt escaped the sweep as KeyboardInterrupt, not an exit")
        else:
            pytest.fail("the sweep returned after an interrupt")
    finally:
        signal.signal(signal.SIGTERM, previous)
    assert code_seen == code
    assert started == ["accel-tuning"]  # the queued skills were cancelled before they started
    assert not kills  # the first signal kills nothing: the call in flight finishes
    assert not stray
    rows = _rows(out / "sweep.jsonl")
    assert [(r["key"], r["status"]) for r in rows] == [("accel-tuning", "ok")]


def test_a_second_signal_kills_the_calls_in_flight_and_stops_at_once(tmp_path, monkeypatch):
    """The first Ctrl-C lets the call in flight finish; a second one kills the calls in flight
    (process.kill_children) and stops the sweep at once, with the same exit code as the first."""
    import time
    from types import SimpleNamespace

    import dokimi.artifacts as artifacts
    import dokimi.commands.catalog_novelty as catalog_novelty
    import dokimi.novelty.report as report
    import dokimi.process as process

    kills: list[bool] = []
    monkeypatch.setattr(process, "kill_children", lambda: kills.append(True))
    artifact = {"k0": {"lenient": 0.2, "strict": 0.1}, "paired": {"lenient": {"n_paired": 3}},
                "artifact": {"tokens": 100}}
    started = []

    def analyze(args, *a, **k):
        started.append(args.artifact.rsplit("/", 1)[-1])
        if len(started) == 1:  # two Ctrl-Cs while the first skill is in flight
            os.kill(os.getpid(), signal.SIGINT)
            time.sleep(0.1)
            os.kill(os.getpid(), signal.SIGINT)
            time.sleep(0.5)
        return SimpleNamespace(partial=None, artifact=artifact)

    monkeypatch.setattr(catalog_novelty, "_analyze_skill", analyze)
    monkeypatch.setattr(artifacts, "save_artifact", lambda kind, art, path: path.parent.mkdir(parents=True))
    monkeypatch.setattr(report, "render_novelty_markdown", lambda artifact: "")
    root = _root(tmp_path, names=("accel-tuning", "beta", "gamma", "other-skill"))
    out = tmp_path / "out"
    with pytest.raises(SystemExit) as exc:
        _sweep(root, out, "--max-usd", "5", "--jobs", "1")
    assert exc.value.code == 130
    assert kills == [True]
    assert started == ["accel-tuning"]


def test_a_row_is_written_once_even_when_its_write_is_interrupted():
    """A second Ctrl-C landing in write(): the row the sink already took is not written again by the drain."""
    from dokimi.commands.catalog_novelty import _Item, _run_sweep

    written: list[str] = []

    def write(row):
        written.append(row["key"])
        raise KeyboardInterrupt  # after the append, as a second signal lands in the sink

    items = [_Item(key="a", name="a", skill_md=Path("a/SKILL.md"), sha256=None),
             _Item(key="b", name="b", skill_md=Path("b/SKILL.md"), sha256=None)]
    with pytest.raises(KeyboardInterrupt):
        _run_sweep(items, jobs=1, review=lambda item: {"key": item.key}, write=write,
                   out_of_budget=lambda: False, stop_requested=lambda: False)
    assert written == ["a", "b"]  # each row once; b finished and is kept too


def test_settle_keeps_the_rows_done_before_it_waits_for_the_skills_in_flight(monkeypatch):
    """An interrupt while the drain waits for the skills in flight keeps the rows already done."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    import dokimi.commands.catalog_novelty as catalog_novelty

    release = threading.Event()
    pool = ThreadPoolExecutor(max_workers=2)
    done = pool.submit(lambda: {"key": "a"})
    done.result()
    slow = pool.submit(lambda: (release.wait(5), {"key": "b"})[1])
    running = {done, slow}
    written: list[str] = []

    def interrupted_wait(*args, **kwargs):
        release.set()
        raise KeyboardInterrupt

    monkeypatch.setattr(catalog_novelty, "wait", interrupted_wait)
    with pytest.raises(KeyboardInterrupt):
        catalog_novelty._settle(pool, running, lambda row: written.append(row["key"]))
    assert written == ["a"]


def test_identical_skill_copies_are_one_skill_and_the_help_says_so(tmp_path, capsys):
    """Two directories with the same SKILL.md are one skill: the shallowest path keeps the row."""
    root = _root(tmp_path, names=("accel-tuning",))
    copy = root / "mirror" / "accel-tuning"
    copy.mkdir(parents=True)
    (copy / "SKILL.md").write_text((root / "accel-tuning" / "SKILL.md").read_text(encoding="utf-8"),
                                   encoding="utf-8")
    out = tmp_path / "out"
    _sweep(root, out, "--max-usd", "5")
    assert [r["key"] for r in _rows(out / "sweep.jsonl")] == ["accel-tuning"]
    capsys.readouterr()
    with pytest.raises(SystemExit):
        build_parser().parse_args(["catalog", "novelty", "--help"])
    help_text = " ".join(capsys.readouterr().out.split())
    assert "identical" in help_text
    assert "Ctrl-C or SIGTERM" in help_text
