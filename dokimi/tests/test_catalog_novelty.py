# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`catalog novelty ROOT`: the paid sweep, run end to end on a fake LLM, offline."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import signal
import threading

import pytest

from dokimi.cli import build_parser
from dokimi.metrics.usage import UsageRecord
from dokimi.novelty.client import SpendCapReached
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
    with pytest.raises(SystemExit, match="2 of 2 skill"):  # every skill partial: the run exits nonzero
        _sweep(_root(tmp_path), out, "--max-usd", "5", "--max-usd-per-skill", "0.02")
    rows = _rows(out / "sweep.jsonl")
    assert [r["status"] for r in rows] == ["partial", "partial"]
    assert {r["reason"] for r in rows} == {"spend_cap"}
    assert not list(out.glob("*/novelty.json"))


def test_the_run_budget_stops_the_skills_it_cannot_pay_for(tmp_path, capsys):
    out = tmp_path / "out"
    with pytest.raises(SystemExit, match="2 of 2 skill") as stopped:
        _sweep(_root(tmp_path), out, "--max-usd", "0.05", "--jobs", "1", "--summary-out", str(tmp_path / "s.json"))
    assert stopped.value.code not in (0, None)  # a sweep that measured nothing is not a success
    rows = _rows(out / "sweep.jsonl")
    assert len(rows) == 1  # the second skill was never started
    assert rows[0]["status"] == "partial"
    assert json.loads((tmp_path / "s.json").read_text(encoding="utf-8"))["counts"]["partial"] == 1
    assert "1 skill(s) not run: the budget was spent" in capsys.readouterr().err


class AuthenticationError(Exception):
    """Named as the provider's error is: a permanent error every later call meets too."""


@pytest.mark.parametrize("failure, reason", [("auth", "permanent_error"), ("unpriced", "no_price")])
def test_a_stop_a_higher_budget_does_not_cure_starts_no_further_skill(tmp_path, monkeypatch, capsys,
                                                                     failure, reason):
    base, offline = _scripted("informed"), novelty_cost.client

    def transport(prompt, system):
        if failure == "unpriced":
            return base(prompt, system)[0], UsageRecord(cost_usd=None)
        if "Extract the checkable knowledge" in prompt or "Does the source text below support" in prompt:
            return base(prompt, system)[0], UsageRecord(cost_usd=COST)
        raise AuthenticationError("invalid api key")

    def client(*args, **kwargs):
        made = offline(*args, **kwargs)
        made.transport = transport
        return made

    monkeypatch.setattr(novelty_cost, "client", client)
    out = tmp_path / "out"
    with pytest.raises(SystemExit, match="3 of 3 skill"):
        _sweep(_root(tmp_path, names=("accel-tuning", "other-skill", "third-skill")), out,
               "--max-usd", "5", "--jobs", "1")
    assert [(r["key"], r["reason"]) for r in _rows(out / "sweep.jsonl")] == [("accel-tuning", reason)]
    assert "2 skill(s) not run" in capsys.readouterr().err


def test_a_resume_whose_judge_is_the_answerer_is_refused_before_it_drops_a_row_or_a_file(tmp_path):
    root, out = _root(tmp_path), tmp_path / "out"
    _sweep(root, out, "--max-usd", "5")
    before = (out / "sweep.jsonl").read_bytes()
    argv = ["catalog", "novelty", str(root), "--out-dir", str(out), "--model", "judge-m",
            "--judge-model", "judge-m", "--max-usd", "5", "--resume"]
    args = build_parser().parse_args(argv)
    with pytest.raises(InputError, match="answering model"):
        args.func(args)
    assert (out / "sweep.jsonl").read_bytes() == before
    assert len(list(out.glob("*/novelty.json"))) == 2


@pytest.mark.parametrize("panel", [["--judge", "openai/judge-a", "--judge", "openai/judge-b"],
                                   ["--judge", "openai/judge-a", "--judge-aggregate", "unanimous"]])
def test_a_judge_panel_is_refused_rather_than_graded_by_its_first_judge(tmp_path, panel):
    argv = ["catalog", "novelty", str(_root(tmp_path)), "--out-dir", str(tmp_path / "out"),
            "--model", "answerer-m", "--max-usd", "5", *panel]
    args = build_parser().parse_args(argv)
    with pytest.raises(InputError, match="judge panel"):
        args.func(args)
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("limit", [(), ("--limit", "1")])
@pytest.mark.parametrize("inside", ["out_dir", "jsonl"])
def test_an_output_inside_a_skill_is_refused_before_a_paid_call(tmp_path, inside, limit):
    root = _root(tmp_path)
    target = root / "other-skill" / "out"
    out, extra = (target, ()) if inside == "out_dir" else (tmp_path / "out", ("--jsonl", str(target / "s.jsonl")))
    with pytest.raises(InputError, match="inside the skill 'other-skill'"):
        _sweep(root, out, "--max-usd", "5", *extra, *limit)
    assert not target.exists()


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


def test_summary_only_needs_no_root(tmp_path, capsys):
    out, root = tmp_path / "out", _root(tmp_path)
    _sweep(root, out, "--max-usd", "5")
    capsys.readouterr()
    shutil.rmtree(root)
    _sweep(root, out, "--summary-only")
    assert "# novelty sweep: 2 measured" in capsys.readouterr().out


def test_summary_only_refuses_to_pool_rows_measured_under_two_models(tmp_path):
    root, out = _root(tmp_path), tmp_path / "out"
    _sweep(root, out, "--max-usd", "5")
    _sweep(root, out, "--max-usd", "5", "--resume", "--rerun-changed", "--limit", "1", "--model", "other-m")
    with pytest.raises(InputError, match="2 different settings"):
        _sweep(root, out, "--summary-only")


def test_a_template_scaffold_is_not_a_skill_and_costs_nothing(tmp_path, capsys):
    root, out = _root(tmp_path), tmp_path / "out"
    for scaffold, text in (("template", "one"), ("templates/nested", "two")):  # distinct: no copy is left out
        (root / scaffold).mkdir(parents=True)
        (root / scaffold / "SKILL.md").write_text(SKILL.replace("accel-tuning", f"scaffold-{text}"),
                                                  encoding="utf-8")
    _sweep(root, out, "--max-usd", "5")
    assert sorted(r["key"] for r in _rows(out / "sweep.jsonl")) == ["accel-tuning", "other-skill"]
    assert len(list(out.glob("*/novelty.json"))) == 2
    assert "# novelty sweep: 2 measured, 0 partial, 0 error" in capsys.readouterr().out


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
    with pytest.raises(SystemExit, match="1 of 3 skill"):
        _sweep(root, out, "--max-usd", "5")
    broken = [r for r in _rows(out / "sweep.jsonl") if r["key"] == "broken"]
    assert broken and broken[0]["status"] == "error"
    assert broken[0]["usd"] == 0


@pytest.mark.skipif(os.name == "nt", reason="chmod does not stop a read on Windows")
def test_an_unreadable_skill_md_is_an_error_row_with_no_digest(tmp_path):
    from dokimi.commands.catalog_novelty import _items

    root = _root(tmp_path)
    (root / "other-skill" / "SKILL.md").chmod(0)
    items = {item.key: item for item in _items(root, None)}
    assert items["other-skill"].error is not None
    assert items["other-skill"].sha256 is None


@pytest.mark.skipif(os.name == "nt", reason="creating a symlink needs a privilege on Windows")
def test_a_skill_md_symlink_to_a_missing_file_is_an_error_row_not_a_skipped_skill(tmp_path):
    from dokimi.commands.catalog_novelty import _items

    root = _root(tmp_path)
    (root / "broken").mkdir()
    (root / "broken" / "SKILL.md").symlink_to(tmp_path / "missing.md")
    items = {item.key: item for item in _items(root, None)}
    assert items["broken"].error is not None
    assert items["broken"].sha256 is None


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
    with pytest.raises(SystemExit, match="1 of 1 skill"):
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
    with pytest.raises(SystemExit, match="1 of 2 skill"):  # the other skill is still measured and saved
        _sweep(root, out, "--max-usd", "5")
    rows = {r["key"]: r for r in _rows(out / "sweep.jsonl")}
    assert rows["accel-tuning"]["status"] == "error"
    assert "disk full" in rows["accel-tuning"]["message"]
    assert rows["other-skill"]["status"] == "ok"


@pytest.mark.parametrize("flag", [
    ["--max-file-words", "100"], ["--max-resource-words", "100"], ["--model", "other-m"],
    ["--judge-model", "other-judge"], ["--arms", "no_artifact,full,shuffled"], ["--budgets", "0.5"], ["--seed", "1"],
    ["--include-resources"], ["--context-framing", "neutral"], ["--no-paraphrase"], ["--max-atoms", "3"],
    ["--no-reversal"], ["--max-tokens", "77"],
])
def test_resume_runs_again_the_rows_measured_under_other_input_limits(tmp_path, capsys, flag):
    root, out = _root(tmp_path), tmp_path / "out"
    _sweep(root, out, "--max-usd", "5")
    capsys.readouterr()
    _sweep(root, out, "--max-usd", "5", "--resume", "--rerun-changed", *flag)
    assert "0 measured and kept, 2 to run" in capsys.readouterr().err


def test_a_resume_under_other_settings_is_refused_and_keeps_every_paid_row(tmp_path):
    root, out = _root(tmp_path), tmp_path / "out"
    _sweep(root, out, "--max-usd", "5")
    before = (out / "sweep.jsonl").read_text(encoding="utf-8")
    with pytest.raises(InputError, match=r"max_tokens.*--rerun-changed"):
        _sweep(root, out, "--max-usd", "0.02", "--resume", "--max-tokens", "999")
    assert (out / "sweep.jsonl").read_text(encoding="utf-8") == before


@pytest.mark.skipif(os.name == "nt", reason="os.kill with SIGINT terminates the process on Windows")
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


@pytest.mark.skipif(os.name == "nt", reason="os.kill with SIGINT terminates the process on Windows")
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
    next_call: list[str] = []
    next_done = threading.Event()

    def analyze(args, *a, **k):
        started.append(args.artifact.rsplit("/", 1)[-1])
        if len(started) == 1:  # two Ctrl-Cs while the first skill is in flight
            os.kill(os.getpid(), signal.SIGINT)
            time.sleep(0.1)
            os.kill(os.getpid(), signal.SIGINT)
            time.sleep(0.5)
            try:
                args._spend_cap.before_call("m")  # the skill's next call, after the sweep stopped
                next_call.append("started")
            except SpendCapReached:
                next_call.append("refused")
            finally:
                next_done.set()
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
    assert next_done.wait(5) and next_call == ["refused"]


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
    assert written.count("a") == 1  # written once; b may or may not have started before the cancel


def test_settle_keeps_the_rows_done_before_it_waits_for_the_skills_in_flight(monkeypatch):
    """An interrupt while the drain waits for the skills in flight keeps the rows already done."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    import dokimi.commands.catalog_novelty as catalog_novelty

    release = threading.Event()
    slow_started = threading.Event()
    pool = ThreadPoolExecutor(max_workers=2)
    done = pool.submit(lambda: {"key": "a"})
    done.result()

    def slow_skill():
        slow_started.set()  # running, not queued: the cancel of the queue leaves it alone
        release.wait(5)
        return {"key": "b"}

    slow = pool.submit(slow_skill)
    assert slow_started.wait(5)
    running = {done, slow}
    written: list[str] = []

    def interrupted_wait(*args, **kwargs):
        release.set()
        raise KeyboardInterrupt

    monkeypatch.setattr(catalog_novelty, "wait", interrupted_wait)
    with pytest.raises(KeyboardInterrupt):
        catalog_novelty._settle(pool, running, lambda row: written.append(row["key"]))
    assert written == ["a"]


def test_a_skill_queued_when_the_stop_lands_makes_no_call(monkeypatch):
    """The stop lands while a skill is in flight and that skill finishes at once: the skill the freed
    worker takes next must not start, since the stop came first (no new paid call)."""
    import time

    import dokimi.commands.catalog_novelty as catalog_novelty
    from dokimi.commands.catalog_novelty import _Item, _run_sweep

    real_wait = catalog_novelty.wait

    def late_wait(futures, **kwargs):  # the main thread wakes late: the freed worker takes the next skill first
        done = real_wait(futures, **kwargs)
        time.sleep(0.3)
        return done

    monkeypatch.setattr(catalog_novelty, "wait", late_wait)
    stop = [False]
    started: list[str] = []

    def review(item):
        started.append(item.key)
        if item.key == "a":  # b is queued behind a by now; the stop lands while a is in flight
            time.sleep(0.05)
            stop[0] = True
        return {"key": item.key}

    items = [_Item(key=k, name=k, skill_md=Path(f"{k}/SKILL.md"), sha256=None) for k in "abc"]
    _run_sweep(items, jobs=1, review=review, write=lambda row: None, out_of_budget=lambda: False,
               stop_requested=lambda: stop[0])
    assert started == ["a"]


def test_a_row_finished_during_the_drain_is_written_before_the_next_wait(monkeypatch):
    """A skill that finishes while the drain waits for another is written then, not after all of them:
    an interrupt during the wait for the rest keeps it."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    import dokimi.commands.catalog_novelty as catalog_novelty

    import time

    release = threading.Event()
    started = threading.Barrier(3)  # the two skills and this test: both are running before the drain
    pool = ThreadPoolExecutor(max_workers=2)

    def fast_skill():
        started.wait(5)
        time.sleep(0.1)  # still in flight when the drain starts
        return {"key": "fast"}

    def slow_skill():
        started.wait(5)
        release.wait(5)
        return {"key": "slow"}

    fast = pool.submit(fast_skill)
    slow = pool.submit(slow_skill)
    started.wait(5)
    real_wait = catalog_novelty.wait
    waits: list[int] = []

    def wait_then_interrupt(futures, **kwargs):
        waits.append(1)
        if len(waits) > 1:
            raise KeyboardInterrupt  # a second signal while the drain waits for the slow skill
        return real_wait(futures, **kwargs)

    monkeypatch.setattr(catalog_novelty, "wait", wait_then_interrupt)
    written: list[str] = []
    try:
        with pytest.raises(KeyboardInterrupt):
            catalog_novelty._settle(pool, {fast, slow}, lambda row: written.append(row["key"]))
        assert written == ["fast"]
    finally:
        release.set()
        pool.shutdown(wait=True)


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


def test_resume_runs_again_the_rows_measured_under_another_placebo_text(tmp_path, capsys):
    root, out = _root(tmp_path), tmp_path / "out"
    placebo = tmp_path / "placebo.txt"
    placebo.write_text("A neutral paragraph about the weather. " * 40, encoding="utf-8")
    _sweep(root, out, "--max-usd", "5", "--arms", "no_artifact,full,placebo",
           "--placebo-text", str(placebo))
    capsys.readouterr()
    placebo.write_text("A different neutral paragraph about tea. " * 40, encoding="utf-8")
    _sweep(root, out, "--max-usd", "5", "--resume", "--rerun-changed", "--arms", "no_artifact,full,placebo",
           "--placebo-text", str(placebo))
    assert "0 measured and kept, 2 to run" in capsys.readouterr().err


def test_two_bundles_with_one_skill_md_and_other_resources_are_two_skills(tmp_path, capsys):
    root = _root(tmp_path, names=("accel-tuning",))
    for side, text in (("a", "one"), ("b", "two")):
        bundle = root / side / "accel-tuning"
        (bundle / "references").mkdir(parents=True)
        (bundle / "SKILL.md").write_text((root / "accel-tuning" / "SKILL.md").read_text(encoding="utf-8"),
                                         encoding="utf-8")
        (bundle / "references" / "ref.md").write_text(f"Reference {text}.\n", encoding="utf-8")
    twin = root / "c" / "accel-tuning"
    shutil.copytree(root / "a" / "accel-tuning", twin)
    out = tmp_path / "out"
    _sweep(root, out, "--max-usd", "5", "--include-resources")
    keys = [r["key"] for r in _rows(out / "sweep.jsonl")]
    assert keys == ["a/accel-tuning", "accel-tuning", "b/accel-tuning"]  # c/ is a copy of a/: left out
    assert "left out" in capsys.readouterr().err


def test_resume_runs_again_a_symlinked_skill_md_whose_target_changed(tmp_path):
    root, out = _root(tmp_path, names=("other-skill",)), tmp_path / "out"
    shared = tmp_path / "shared" / "SKILL.md"
    shared.parent.mkdir()
    shared.write_text(SKILL, encoding="utf-8")
    (root / "linked").mkdir()
    try:
        (root / "linked" / "SKILL.md").symlink_to(shared)
    except OSError:
        pytest.skip("symlinks are not available")
    _sweep(root, out, "--max-usd", "5")
    before = {r["key"]: r["skill_sha256"] for r in _rows(out / "sweep.jsonl")}
    shared.write_text(SKILL + "\nMore text.\n", encoding="utf-8")
    _sweep(root, out, "--max-usd", "5", "--resume")
    after = {r["key"]: r["skill_sha256"] for r in _rows(out / "sweep.jsonl")}
    assert after["linked"] != before["linked"] and after["other-skill"] == before["other-skill"]


def test_resume_runs_again_a_skill_root_reached_through_a_symlink_whose_bundled_file_changed(tmp_path, capsys):
    real = _root(tmp_path, names=("accel-tuning",)) / "accel-tuning"
    (real / "ref.md").write_text("Reference.\n", encoding="utf-8")
    link = tmp_path / "link"
    try:
        link.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are not available")
    out = tmp_path / "out"
    _sweep(link, out, "--max-usd", "5", "--include-resources")
    capsys.readouterr()
    (real / "ref.md").write_text("Reference, changed.\n", encoding="utf-8")
    _sweep(link, out, "--max-usd", "5", "--include-resources", "--resume")
    assert "0 measured and kept, 1 to run" in capsys.readouterr().err


def test_a_skill_measured_again_that_ends_partial_or_error_leaves_no_old_novelty_file(tmp_path):
    root, out = _root(tmp_path, names=("accel-tuning",)), tmp_path / "out"
    _sweep(root, out, "--max-usd", "5")
    old = Path(_rows(out / "sweep.jsonl")[0]["novelty_json"])
    assert old.is_file() and old.with_suffix(".md").is_file()
    (root / "accel-tuning" / "SKILL.md").write_text(SKILL + "\nMore text.\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        _sweep(root, out, "--max-usd", "5", "--resume", "--max-usd-per-skill", "0.02")
    assert _rows(out / "sweep.jsonl")[0]["status"] == "partial"
    assert not old.exists() and not old.with_suffix(".md").exists()
    (root / "accel-tuning" / "SKILL.md").write_text("no frontmatter at all", encoding="utf-8")
    with pytest.raises(SystemExit):
        _sweep(root, out, "--max-usd", "5", "--resume")
    assert _rows(out / "sweep.jsonl")[0]["status"] == "error"
    assert not old.exists()


def test_the_output_directory_of_a_long_nested_key_stays_a_valid_name(tmp_path):
    from dokimi.commands.catalog_novelty import _dirname

    key = "/".join(["a" * 100] * 3)
    name = _dirname(key)
    assert len(name) <= 100
    assert name != _dirname(key[:-1] + "b")  # the hash still tells long keys apart
    (tmp_path / name).mkdir()


@pytest.mark.parametrize("summary_only", [False, True])
def test_a_summary_out_that_is_the_jsonl_is_refused_before_any_work(tmp_path, summary_only):
    root, out = _root(tmp_path), tmp_path / "out"
    _sweep(root, out, "--max-usd", "5")
    before = (out / "sweep.jsonl").read_text(encoding="utf-8")
    alias = out / ".." / "out" / "sweep.jsonl"  # the same file by another path
    extra = ["--summary-only"] if summary_only else ["--max-usd", "5", "--resume"]
    with pytest.raises(InputError, match="--summary-out"):
        _sweep(root, out, *extra, "--summary-out", str(alias))
    assert (out / "sweep.jsonl").read_text(encoding="utf-8") == before


def test_a_summary_out_in_a_missing_directory_is_made_and_written(tmp_path):
    root, out = _root(tmp_path), tmp_path / "out"
    target = tmp_path / "new" / "dir" / "summary.json"
    _sweep(root, out, "--max-usd", "5", "--summary-out", str(target))
    assert json.loads(target.read_text(encoding="utf-8"))["counts"]["ok"] == 2


def test_a_summary_out_whose_directory_cannot_be_made_is_refused_before_a_paid_call(tmp_path):
    root, out = _root(tmp_path), tmp_path / "out"
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    with pytest.raises(InputError, match="--summary-out"):
        _sweep(root, out, "--max-usd", "5", "--summary-out", str(blocker / "summary.json"))
    assert not (out / "sweep.jsonl").exists()


def test_a_second_signal_inside_the_poll_wait_waits_for_no_skill_and_writes_no_row():
    import threading
    import time

    from dokimi.commands.catalog_novelty import _Item, _run_sweep

    started, release = threading.Event(), threading.Event()
    written: list[str] = []

    def review(item):
        started.set()
        release.wait(5)
        return {"key": item.key}

    def stop_requested():  # the main thread's poll sees the second signal while the skill is in flight
        if threading.current_thread() is threading.main_thread() and started.is_set():
            raise KeyboardInterrupt
        return False

    items = [_Item(key="a", name="a", skill_md=Path("a/SKILL.md"), sha256=None)]
    begun = time.monotonic()
    try:
        with pytest.raises(KeyboardInterrupt):
            _run_sweep(items, jobs=1, review=review, write=lambda row: written.append(row["key"]),
                       out_of_budget=lambda: False, stop_requested=stop_requested)
        assert time.monotonic() - begun < 2  # the skill in flight is not waited for
        assert written == []
    finally:
        release.set()


def test_the_hard_stop_ends_the_next_call_of_a_skill_in_flight():
    import threading

    from dokimi.novelty.client import NestedSpendCap, SpendCap, SpendCapReached

    hard = threading.Event()
    cap = NestedSpendCap(limit_usd=1.0, parent=SpendCap(limit_usd=5.0), hard_stop=hard)
    cap.before_call("m")  # a call in flight started before the signal
    hard.set()
    with pytest.raises(SpendCapReached) as stop:
        cap.before_call("m")
    assert stop.value.reason == "stopped"
    assert cap.parent.calls == 0
