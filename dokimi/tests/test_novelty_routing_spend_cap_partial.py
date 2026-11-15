# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`novelty routing --max-usd`: a run stopped by the cap keeps what it paid for."""

from __future__ import annotations

import contextlib
import errno
import json
import os
import threading
from pathlib import Path

import pytest

from dokimi.cli import build_parser
from dokimi.metrics.usage import UsageRecord
from dokimi.novelty.client import SpendCapReached


def _parse(*argv):
    return build_parser().parse_args(list(argv))


def _skill(tmp_path, name, description):
    root = tmp_path / name
    root.mkdir()
    (root / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# B\n", encoding="utf-8")
    return f"skill:{root}"


def _provider(monkeypatch, usage):
    """Every selector call answers card 1 and reports ``usage``; returns the call log."""
    from dokimi import llm

    calls: list[str] = []

    def stub(prompt, model, **_kwargs):
        calls.append(prompt)
        return '{"pick": 1}', usage

    monkeypatch.setattr(llm, "llm_call_with_usage", stub)
    return calls


def _single(tmp_path, queries, *extra):
    q = tmp_path / "q.json"
    q.write_text(json.dumps(queries), encoding="utf-8")
    out = tmp_path / "routing.json"
    args = _parse("skill", "routing",
                  _skill(tmp_path, "a", "profile a binary with perf"), "--competitors",
                  _skill(tmp_path, "b", "tune a database"), "--queries",
                  str(q), "--model", "m", "--out", str(out), *extra)
    return args, out


def _again(tmp_path, out, name, *extra):
    """A second run of ``_single`` writing to the same ``out``."""
    sub = tmp_path / name
    sub.mkdir()
    args, _ = _single(sub, QUERIES, *extra)
    args.out = str(out)
    return args


QUERIES = ["profile a binary", "tune a slow query", "record a perf profile"]


def test_a_single_run_stopped_by_the_cap_writes_what_it_scored(tmp_path, monkeypatch, capsys):
    calls = _provider(monkeypatch, UsageRecord(cost_usd=0.6))
    args, out = _single(tmp_path, [*QUERIES, "annotate the hot loop"], "--max-usd", "1")
    with pytest.raises(SystemExit, match=r"\$1\.20 of \$1\.00") as stop:
        args.func(args)
    assert str(out) in str(stop.value)
    assert len(calls) == 2
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["partial"]["reason"] == "spend_cap"
    assert (payload["partial"]["calls_done"], payload["partial"]["calls_planned"]) == (2, 4)
    assert payload["model"]["n_calls"] == 2 and len(payload["model"]["per_query"]) == 2
    assert payload["cost"]["llm_calls"] == 2
    assert "lexical" in payload
    shown = capsys.readouterr().out
    assert "cost     2 calls" in shown and "$1.2000" in shown
    assert "OK Saved" not in shown


def test_a_stopped_repeat_run_keeps_the_noise_of_the_queries_it_finished(tmp_path, monkeypatch):
    _provider(monkeypatch, UsageRecord(cost_usd=0.4))
    args, out = _single(tmp_path, QUERIES, "--reps", "2", "--max-usd", "1")
    with pytest.raises(SystemExit, match="spend cap reached"):
        args.func(args)
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert (payload["partial"]["calls_done"], payload["partial"]["calls_planned"]) == (3, 6)
    assert len(payload["model"]["per_query"]) == 3          # the half-asked query is kept
    # ... but only the query asked in full enters the repeat noise.
    assert [row["query"] for row in payload["model"]["noise"]["per_query"]] == [QUERIES[0]]


def test_a_call_with_no_price_also_leaves_the_partial_result(tmp_path, monkeypatch):
    _provider(monkeypatch, None)
    args, out = _single(tmp_path, QUERIES, "--max-usd", "5")
    with pytest.raises(SystemExit, match="no price is known"):
        args.func(args)
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["partial"]["reason"] == "spend_cap" and payload["partial"]["calls_done"] == 1
    # The reply came back and was paid for, so it is scored, not lost; no further call is made.
    row = payload["model"]["per_query"][0]
    assert "error" not in row and row["picked"] is not None
    assert len(payload["model"]["per_query"]) == 1


def test_a_run_the_cap_did_not_stop_has_no_partial_marker(tmp_path, monkeypatch):
    _provider(monkeypatch, UsageRecord(cost_usd=0.1))
    args, out = _single(tmp_path, QUERIES, "--max-usd", "5")
    args.func(args)
    assert "partial" not in json.loads(out.read_text(encoding="utf-8"))


def _catalog(tmp_path):
    for name, description in {"perf": "profile a slow binary with perf record and report",
                              "db": "tune a slow database query with an index"}.items():
        d = tmp_path / "catalog" / "skills" / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {description}\n---\n\n# B\n", encoding="utf-8")
    q = tmp_path / "queries.json"
    q.write_text(json.dumps({"perf": ["profile a slow program", "find hot functions"],
                             "db": ["add an index"]}), encoding="utf-8")
    return tmp_path / "catalog", q


def test_an_all_vs_all_run_stopped_by_the_cap_scores_the_queries_it_finished(
        tmp_path, monkeypatch, capsys):
    calls = _provider(monkeypatch, UsageRecord(cost_usd=0.6))
    root, queries = _catalog(tmp_path)
    out = tmp_path / "o.json"
    args = _parse("catalog", "routing", str(root), "--queries-by-skill", str(queries),
                  "--out", str(out), "--model", "m",
                  "--max-usd", "1")
    with pytest.raises(SystemExit, match=r"\$1\.20 of \$1\.00") as stop:
        args.func(args)
    assert str(out) in str(stop.value)
    assert len(calls) == 2
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["partial"]["reason"] == "spend_cap"
    assert (payload["partial"]["calls_done"], payload["partial"]["calls_planned"]) == (2, 3)
    model = payload["result"]["model"]
    assert sum(model["n_scored"].values()) == 2          # the two answered calls are scored
    assert sum(model["n_unscored"].values()) == 1        # the refused one is not a miss
    assert payload["cost"]["llm_calls"] == 2
    shown = capsys.readouterr().out
    assert "cost     2 calls" in shown and "OK Saved" not in shown
    # The summary of a finished run, marked partial: the field line, the attractors, the model line.
    assert "all-vs-all: 2 skills in the field, 3 queries" in shown
    assert "model    partial: 2 of 3 calls, 2 answered" in shown
    assert "WARNING: only 2 model calls answered" in shown


def test_an_all_vs_all_run_stopped_early_leaves_the_skills_it_never_reached_unscored(
        tmp_path, monkeypatch):
    _provider(monkeypatch, UsageRecord(cost_usd=0.6))
    root, queries = _catalog(tmp_path)
    extra = root / "skills" / "net"        # a third skill, so one is skipped, not just refused
    extra.mkdir(parents=True)
    (extra / "SKILL.md").write_text(
        "---\nname: net\ndescription: debug slow network sockets\n---\n\n# B\n", encoding="utf-8")
    by_skill = {**json.loads(queries.read_text()), "net": ["trace a socket", "dump packets"]}
    queries.write_text(json.dumps(by_skill), encoding="utf-8")
    out = tmp_path / "o.json"
    args = _parse("catalog", "routing", str(root), "--queries-by-skill", str(queries),
                  "--out", str(out), "--model", "m",
                  "--max-usd", "0.5")
    # The first call is allowed (nothing spent), then 0.6 >= 0.5 refuses the rest.
    with pytest.raises(SystemExit, match="spend cap reached"):
        args.func(args)
    model = json.loads(out.read_text(encoding="utf-8"))["result"]["model"]
    assert sum(model["n_scored"].values()) == 1
    unreached = [skill for skill, n in model["n_scored"].items() if n == 0]
    assert len(unreached) == 2             # the skill whose call was refused, and the one skipped
    for skill in unreached:
        assert model["own_rate"][skill] is None          # not a measured 0.0
        assert model["n_unscored"][skill] == len(by_skill[skill])


def test_a_stopped_run_is_marked_partial_and_a_finished_one_is_not(tmp_path, monkeypatch):
    _provider(monkeypatch, UsageRecord(cost_usd=0.6))
    args, out = _single(tmp_path, QUERIES, "--max-usd", "0.5")
    with pytest.raises(SystemExit):
        args.func(args)
    assert json.loads(out.read_text(encoding="utf-8"))["status"] == "partial"
    sub = tmp_path / "all"
    sub.mkdir()
    root, queries = _catalog(sub)
    all_out = sub / "o.json"
    args = _parse("catalog", "routing", str(root), "--queries-by-skill", str(queries),
                  "--out", str(all_out), "--model", "m",
                  "--max-usd", "0.5")
    with pytest.raises(SystemExit):
        args.func(args)
    assert json.loads(all_out.read_text(encoding="utf-8"))["status"] == "partial"
    finished = tmp_path / "fin"
    finished.mkdir()
    _provider(monkeypatch, UsageRecord(cost_usd=0.1))
    args, out = _single(finished, QUERIES, "--max-usd", "5")
    args.func(args)
    assert json.loads(out.read_text(encoding="utf-8"))["status"] == "ok"


def test_the_model_line_of_a_partial_run_says_how_many_calls_it_rests_on(
        tmp_path, monkeypatch, capsys):
    _provider(monkeypatch, UsageRecord(cost_usd=0.6))
    queries = [f"profile binary number {i}" for i in range(20)]
    args, _out = _single(tmp_path, queries, "--max-usd", "0.5")
    with pytest.raises(SystemExit):
        args.func(args)
    lines = capsys.readouterr().out.splitlines()
    model = next(line for line in lines if line.startswith("model "))
    assert "partial: 1 of 20 calls" in model
    # One answered call is not a rate: the warning the lexical arm gets applies here too.
    assert any(line.startswith("WARNING: only 1 model call answered") for line in lines)


def test_a_partial_run_does_not_overwrite_a_finished_result(tmp_path, monkeypatch, capsys):
    _provider(monkeypatch, UsageRecord(cost_usd=0.1))
    args, out = _single(tmp_path, QUERIES, "--max-usd", "5")
    args.func(args)
    finished = out.read_text(encoding="utf-8")
    _provider(monkeypatch, UsageRecord(cost_usd=0.6))
    again = _again(tmp_path, out, "second", "--max-usd", "0.5")
    with pytest.raises(SystemExit) as stop:
        again.func(again)
    side = out.with_suffix(".partial.json")
    assert out.read_text(encoding="utf-8") == finished       # the finished result stays
    assert json.loads(side.read_text(encoding="utf-8"))["status"] == "partial"
    assert str(side) in str(stop.value)
    # A second stopped run replaces the partial file, not the finished one.
    again2 = _again(tmp_path, out, "third", "--max-usd", "0.5")
    with pytest.raises(SystemExit):
        again2.func(again2)
    assert out.read_text(encoding="utf-8") == finished


def test_a_partial_run_writes_over_an_earlier_partial_one(tmp_path, monkeypatch):
    _provider(monkeypatch, UsageRecord(cost_usd=0.6))
    args, out = _single(tmp_path, QUERIES, "--max-usd", "0.5")
    with pytest.raises(SystemExit):
        args.func(args)
    again = _again(tmp_path, out, "second", "--max-usd", "0.5")
    with pytest.raises(SystemExit):
        again.func(again)
    assert not out.with_suffix(".partial.json").exists()


def test_a_partial_run_skips_a_sidecar_that_holds_a_finished_result(tmp_path, monkeypatch):
    _provider(monkeypatch, UsageRecord(cost_usd=0.1))
    args, out = _single(tmp_path, QUERIES, "--max-usd", "5")
    args.func(args)                                          # a finished routing.json
    side = out.with_suffix(".partial.json")
    sidecar = _again(tmp_path, side, "sidecar", "--max-usd", "5")
    sidecar.func(sidecar)                                    # a finished routing.partial.json
    finished, finished_side = out.read_text(encoding="utf-8"), side.read_text(encoding="utf-8")
    _provider(monkeypatch, UsageRecord(cost_usd=0.6))
    again = _again(tmp_path, out, "third", "--max-usd", "0.5")
    with pytest.raises(SystemExit) as stop:
        again.func(again)
    third = out.with_name("routing.partial2.json")
    assert out.read_text(encoding="utf-8") == finished        # the finished result stays
    assert side.read_text(encoding="utf-8") == finished_side  # and so does the finished sidecar
    assert json.loads(third.read_text(encoding="utf-8"))["status"] == "partial"
    assert str(third) in str(stop.value)


def test_a_single_run_capped_before_any_query_finished_has_an_empty_noise_block(tmp_path, monkeypatch):
    # noise is always an object (main's contract): with no query finished it names the repeats
    # asked for and measures nothing.
    _provider(monkeypatch, UsageRecord(cost_usd=0.6))
    args, out = _single(tmp_path, QUERIES, "--reps", "3", "--max-usd", "0.5")
    with pytest.raises(SystemExit):
        args.func(args)
    noise = json.loads(out.read_text(encoding="utf-8"))["model"]["noise"]
    assert (noise["n_repeats"], noise["n_queries_compared"], noise["mean_agreement"]) == (3, 0, None)


def test_a_command_that_writes_no_partial_still_says_nothing_was_written():
    from dokimi.commands.novelty_run import stops_at_cap

    def command(args):
        raise SpendCapReached("spend cap reached: $1.20 of $1.00 after 2 calls (next call: m)")

    with pytest.raises(SystemExit, match="nothing was written"):
        stops_at_cap(command)(None)


def test_a_partial_run_skips_a_sidecar_it_cannot_read(tmp_path, monkeypatch):
    _provider(monkeypatch, UsageRecord(cost_usd=0.1))
    args, out = _single(tmp_path, QUERIES, "--max-usd", "5")
    args.func(args)                                          # a finished routing.json
    side = out.with_suffix(".partial.json")
    side.write_text("not json", encoding="utf-8")            # a sidecar this cannot read is no partial result
    _provider(monkeypatch, UsageRecord(cost_usd=0.6))
    again = _again(tmp_path, out, "third", "--max-usd", "0.5")
    with pytest.raises(SystemExit):
        again.func(again)
    assert side.read_text(encoding="utf-8") == "not json"    # it is left as it is
    third = out.with_name("routing.partial2.json")
    assert json.loads(third.read_text(encoding="utf-8"))["status"] == "partial"


PARTIAL = {"reason": "spend_cap", "calls_done": 1, "calls_planned": 4, "message": "spend cap reached"}


def _finished(tmp_path):
    out = tmp_path / "routing.json"
    out.write_text('{"status": "ok"}', encoding="utf-8")
    return out


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFOs are POSIX")
def test_a_partial_run_skips_a_sidecar_that_is_a_fifo_without_opening_it(tmp_path):
    from dokimi.commands.novelty_routing_report import save_result

    out = _finished(tmp_path)
    fifo = tmp_path / "routing.partial.json"
    os.mkfifo(fifo)
    saved: list = []
    writer = threading.Thread(target=lambda: saved.append(save_result({}, out, {"partial": PARTIAL})),
                              daemon=True)  # a read of a FIFO waits for a writer: the thread is kept apart
    writer.start()
    writer.join(10)
    blocked = writer.is_alive()
    for _ in range(30):  # open the write end so a blocked read ends
        if not writer.is_alive():
            break
        with contextlib.suppress(OSError):  # ENXIO: no read of the FIFO is waiting right now
            os.close(os.open(fifo, os.O_WRONLY | os.O_NONBLOCK))
        writer.join(1)
    assert not blocked, "a sidecar that is a FIFO blocked the save of the paid calls"
    assert saved == [tmp_path / "routing.partial2.json"]
    assert json.loads(saved[0].read_text(encoding="utf-8"))["partial"] == PARTIAL


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0,
                    reason="POSIX permissions; root writes a read-only file")
def test_a_partial_run_skips_a_partial_sidecar_it_cannot_write(tmp_path):
    from dokimi.commands.novelty_routing_report import save_result

    out = _finished(tmp_path)
    side = tmp_path / "routing.partial.json"
    earlier = json.dumps({"status": "partial", "partial": PARTIAL})
    side.write_text(earlier, encoding="utf-8")
    side.chmod(0o444)
    try:
        saved = save_result({"model": {}}, out, {"partial": PARTIAL})
    finally:
        side.chmod(0o644)
    assert saved == tmp_path / "routing.partial2.json"
    assert side.read_text(encoding="utf-8") == earlier
    assert json.loads(saved.read_text(encoding="utf-8")) == {"model": {}, "status": "partial",
                                                             "partial": PARTIAL}


def _refusing(monkeypatch, refuses) -> list[str]:
    """``Path.write_text`` raising ``PermissionError`` where ``refuses(path)``, as an NTFS ACL refuses
    a file whose mode allows the write; returns the names it refused."""
    real_write, refused = Path.write_text, []

    def write_text(self, *args, **kwargs):
        if refuses(self):
            refused.append(self.name)
            assert len(refused) < 50, "the sidecar search kept trying names refused alike"
            raise PermissionError(errno.EACCES, "access is denied", os.fspath(self))
        return real_write(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", write_text)
    return refused


def test_a_partial_run_skips_a_partial_sidecar_whose_write_is_refused(tmp_path, monkeypatch):
    from dokimi.commands.novelty_routing_report import save_result

    out = _finished(tmp_path)
    side = tmp_path / "routing.partial.json"
    earlier = json.dumps({"status": "partial", "partial": PARTIAL})
    side.write_text(earlier, encoding="utf-8")  # its mode allows the write: os.access says yes
    refused = _refusing(monkeypatch, lambda path: path == side)
    saved = save_result({"model": {}}, out, {"partial": PARTIAL})
    assert refused == [side.name]
    assert saved == tmp_path / "routing.partial2.json"
    assert side.read_text(encoding="utf-8") == earlier
    assert json.loads(saved.read_text(encoding="utf-8")) == {"model": {}, "status": "partial",
                                                             "partial": PARTIAL}


def test_a_partial_run_stops_when_its_directory_refuses_a_new_sidecar(tmp_path, monkeypatch):
    from dokimi.commands.novelty_routing_report import save_result

    out = _finished(tmp_path)
    refused = _refusing(monkeypatch, lambda path: path.name.startswith("routing.partial"))
    with pytest.raises(PermissionError):
        save_result({}, out, {"partial": PARTIAL})
    assert refused == ["routing.partial.json"]
    assert [p.name for p in tmp_path.iterdir()] == [out.name]


@pytest.mark.skipif(os.name == "nt", reason="a symlink needs a privilege on Windows")
def test_a_partial_run_skips_a_sidecar_that_is_a_symlink_loop(tmp_path):
    from dokimi.commands.novelty_routing_report import save_result

    out = _finished(tmp_path)
    (tmp_path / "routing.partial.json").symlink_to("routing.partial.json")
    saved = save_result({}, out, {"partial": PARTIAL})
    assert saved == tmp_path / "routing.partial2.json"
    assert json.loads(saved.read_text(encoding="utf-8"))["partial"] == PARTIAL


@pytest.mark.skipif(os.name == "nt", reason="a symlink needs a privilege on Windows")
@pytest.mark.parametrize("earlier", [None, {"status": "partial", "partial": PARTIAL}],
                         ids=["dangling", "to-a-partial-result"])
def test_a_partial_run_does_not_write_through_a_sidecar_that_is_a_symlink(tmp_path, earlier):
    from dokimi.commands.novelty_routing_report import save_result

    (tmp_path / "out").mkdir()
    out = _finished(tmp_path / "out")
    target = tmp_path / "elsewhere.json"  # outside the directory of --out
    if earlier is not None:
        target.write_text(json.dumps(earlier), encoding="utf-8")
    (tmp_path / "out" / "routing.partial.json").symlink_to(Path("..") / target.name)
    saved = save_result({}, out, {"partial": PARTIAL})
    assert saved == tmp_path / "out" / "routing.partial2.json"
    assert json.loads(saved.read_text(encoding="utf-8"))["partial"] == PARTIAL
    assert (json.loads(target.read_text(encoding="utf-8")) if target.exists() else None) == earlier


class _WindowsError(OSError):
    """An ``OSError`` as Windows raises it: ``errno`` from the Windows code, which ``winerror`` keeps."""

    winerror: int


def _windows_error(winerror: int, path) -> _WindowsError:
    exc = _WindowsError(errno.EINVAL, "windows error", os.fspath(path))
    exc.winerror = winerror
    return exc


def test_a_partial_run_stops_on_another_windows_error_of_its_sidecar(tmp_path, monkeypatch):
    from dokimi.commands.novelty_routing_report import save_result

    out = _finished(tmp_path)
    real_lstat, names = os.lstat, []

    def lstat(path, *args, **kwargs):  # EINVAL with WinError 123 (a bad name) fails every later name too
        names.append(path)
        assert len(names) < 50, "the sidecar search kept trying names that fail alike"
        if os.path.basename(os.fspath(path)).startswith("routing.partial"):
            raise _windows_error(123, path)
        return real_lstat(path, *args, **kwargs)

    with monkeypatch.context() as patch, pytest.raises(OSError) as error:
        patch.setattr(os, "lstat", lstat)
        save_result({}, out, {"partial": PARTIAL})
    assert getattr(error.value, "winerror", None) == 123
    assert [p.name for p in tmp_path.iterdir()] == [out.name]


@pytest.mark.skipif(not hasattr(os, "pathconf"), reason="NAME_MAX is POSIX")
def test_a_partial_run_whose_out_name_is_near_name_max_is_saved_under_a_shortened_name(tmp_path):
    from dokimi.commands.novelty_routing_report import save_result

    name_max = os.pathconf(tmp_path, "PC_NAME_MAX")
    out = tmp_path / ("é" * ((name_max - len(".json")) // 2) + ".json")
    out.write_text('{"status": "ok"}', encoding="utf-8")
    first = save_result({}, out, {"partial": PARTIAL})
    second = save_result({}, out, {"partial": PARTIAL})
    assert first.parent == tmp_path and first.name.endswith(".partial.json")
    assert second.name.endswith(".partial.json")  # an earlier partial result is replaced in place
    assert len(first.name.encode("utf-8")) <= name_max
    assert first.name != out.with_suffix("").name + ".partial.json"
    assert json.loads(first.read_text(encoding="utf-8"))["partial"] == PARTIAL
    assert out.read_text(encoding="utf-8") == '{"status": "ok"}'


@pytest.mark.skipif(not hasattr(os, "pathconf"), reason="NAME_MAX is POSIX")
@pytest.mark.skipif(os.name != "posix", reason="a non-UTF-8 file name is POSIX")
def test_a_partial_run_whose_out_name_holds_an_undecodable_byte_is_saved(tmp_path):
    from dokimi.commands.novelty_routing_report import save_result

    out = tmp_path / os.fsdecode(b"routing-\xff.json")
    out.write_text('{"status": "ok"}', encoding="utf-8")
    saved = save_result({}, out, {"partial": PARTIAL})
    assert os.fsencode(saved.name) == b"routing-\xff.partial.json"
    assert json.loads(saved.read_text(encoding="utf-8"))["partial"] == PARTIAL


@pytest.mark.skipif(os.name != "posix", reason="a non-UTF-8 file name is POSIX")
def test_a_long_out_name_with_undecodable_bytes_is_shortened_within_name_max(tmp_path):
    from dokimi.commands.novelty_routing_report import _partial_destinations

    name_max = os.pathconf(tmp_path, "PC_NAME_MAX")
    out = tmp_path / (os.fsdecode(b"\xff" * (name_max - len(".json"))) + ".json")
    out.write_text('{"status": "ok"}', encoding="utf-8")
    name = next(_partial_destinations(out)).name
    assert len(os.fsencode(name)) <= name_max
    assert os.fsencode(name).startswith(b"\xff\xff") and name.endswith(".partial.json")


@pytest.mark.skipif(not hasattr(os, "pathconf"), reason="NAME_MAX is POSIX")
def test_two_long_outs_that_differ_only_at_the_end_keep_their_own_partial_results(tmp_path):
    from dokimi.commands.novelty_routing_report import save_result

    long = "x" * (os.pathconf(tmp_path, "PC_NAME_MAX") - len("A.json"))
    outs = [tmp_path / f"{long}{letter}.json" for letter in "AB"]
    for out in outs:
        out.write_text('{"status": "ok"}', encoding="utf-8")
    first = save_result({}, outs[0], {"partial": {**PARTIAL, "calls_done": 1}})
    second = save_result({}, outs[1], {"partial": {**PARTIAL, "calls_done": 2}})
    assert first != second
    assert json.loads(first.read_text(encoding="utf-8"))["partial"]["calls_done"] == 1
    assert json.loads(second.read_text(encoding="utf-8"))["partial"]["calls_done"] == 2


def test_a_short_out_named_like_a_cut_long_out_keeps_its_own_partial_result(tmp_path, monkeypatch):
    from dokimi.commands.novelty_routing_report import save_result

    monkeypatch.setattr(os, "pathconf", lambda *args: 255, raising=False)
    long = tmp_path / f"{'x' * 250}.json"
    long.write_text('{"status": "ok"}', encoding="utf-8")
    cut = save_result({}, long, {"partial": {**PARTIAL, "calls_done": 1}})
    tag = cut.name.removesuffix(".partial.json")[-13:]
    short = tmp_path / f"{'x' * 223}{tag}.json"
    assert len(short.stem) == len(cut.name.removesuffix(".partial.json"))
    short.write_text('{"status": "ok"}', encoding="utf-8")
    other = save_result({}, short, {"partial": {**PARTIAL, "calls_done": 2}})
    assert other != cut
    assert json.loads(cut.read_text(encoding="utf-8"))["partial"]["calls_done"] == 1
    assert json.loads(other.read_text(encoding="utf-8"))["partial"]["calls_done"] == 2


def test_a_short_out_with_an_uppercase_digest_tag_does_not_share_a_cut_outs_sidecar_name(tmp_path, monkeypatch):
    from dokimi.commands.novelty_routing_report import save_result

    monkeypatch.setattr(os, "pathconf", lambda *args: 255, raising=False)
    long = tmp_path / f"{'x' * 250}.json"
    long.write_text('{"status": "ok"}', encoding="utf-8")
    cut = save_result({}, long, {"partial": {**PARTIAL, "calls_done": 1}})
    tag = cut.name.removesuffix(".partial.json")[-13:]
    short = tmp_path / f"{'x' * 223}{tag.upper()}.json"
    short.write_text('{"status": "ok"}', encoding="utf-8")
    other = save_result({}, short, {"partial": {**PARTIAL, "calls_done": 2}})
    assert other.name.lower() != cut.name.lower()
    assert json.loads(cut.read_text(encoding="utf-8"))["partial"]["calls_done"] == 1


def test_a_long_out_name_is_shortened_to_255_bytes_where_the_limit_is_unknown(tmp_path, monkeypatch):
    from dokimi.commands.novelty_routing_report import _sidecar_stem

    def unknown(*args):
        raise OSError(errno.EINVAL, "no limit")

    monkeypatch.setattr(os, "pathconf", unknown, raising=False)
    monkeypatch.setattr("dokimi.commands.novelty_routing_report._UTF16_NAMES", False)
    stem = _sidecar_stem(tmp_path / ("é" * 200 + ".json"))
    assert len((stem + ".partial999999.json").encode("utf-8")) <= 255
    assert stem.startswith("é" * 50)


@pytest.mark.parametrize("stem", ["测" * 80, "测" * 40 + "\udcff"], ids=["cjk", "lone-surrogate"])
def test_a_windows_out_stem_that_fits_255_utf16_units_keeps_its_name(tmp_path, monkeypatch, stem):
    from dokimi.commands import novelty_routing_report

    monkeypatch.delattr(os, "pathconf", raising=False)
    monkeypatch.setattr(novelty_routing_report, "_UTF16_NAMES", True)
    assert novelty_routing_report._sidecar_stem(tmp_path / f"{stem}.json") == stem


def test_a_windows_out_stem_too_long_for_255_utf16_units_is_cut_to_fit(tmp_path, monkeypatch):
    from dokimi.commands import novelty_routing_report

    monkeypatch.delattr(os, "pathconf", raising=False)
    monkeypatch.setattr(novelty_routing_report, "_UTF16_NAMES", True)
    stem = novelty_routing_report._sidecar_stem(tmp_path / ("测" * 250 + ".json"))
    assert len(stem + ".partial999999.json") <= 255 and stem.startswith("测" * 200)


def test_an_indeterminate_name_limit_is_taken_as_255_bytes(tmp_path, monkeypatch):
    from dokimi.commands.novelty_routing_report import _sidecar_stem

    monkeypatch.setattr(os, "pathconf", lambda *args: -1, raising=False)
    stem = _sidecar_stem(tmp_path / ("x" * 300 + ".json"))
    assert 200 < len(stem) and len(stem + ".partial999999.json") <= 255


def test_a_name_limit_too_small_for_a_sidecar_name_is_an_error_not_a_hang(tmp_path, monkeypatch):
    from dokimi.commands.novelty_routing_report import save_result

    out = _finished(tmp_path)
    monkeypatch.setattr(os, "pathconf", lambda *args: 14, raising=False)
    with pytest.raises(OSError) as error:
        save_result({}, out, {"partial": PARTIAL})
    assert error.value.errno == errno.ENAMETOOLONG
    assert [p.name for p in tmp_path.iterdir()] == [out.name]


@pytest.mark.skipif(not hasattr(os, "pathconf"), reason="NAME_MAX is POSIX")
def test_a_second_long_name_partial_save_goes_to_the_next_numbered_shortened_name(tmp_path):
    from dokimi.commands.novelty_routing_report import _partial_destinations

    out = tmp_path / ("x" * (os.pathconf(tmp_path, "PC_NAME_MAX") - len(".json")) + ".json")
    out.write_text('{"status": "ok"}', encoding="utf-8")
    names = _partial_destinations(out)
    first = next(names)
    first.write_text('{"status": "ok"}', encoding="utf-8")  # a finished result there: taken
    second = next(names)
    assert second.name.endswith(".partial2.json")
    assert second.name.removesuffix(".partial2.json") == first.name.removesuffix(".partial.json")
    assert len(second.name) <= os.pathconf(tmp_path, "PC_NAME_MAX")
    assert out.read_text(encoding="utf-8") == '{"status": "ok"}'


def test_a_short_out_at_a_small_name_limit_still_saves_its_partial_result(tmp_path, monkeypatch):
    from dokimi.commands.novelty_routing_report import save_result

    monkeypatch.setattr(os, "pathconf", lambda *args: 20, raising=False)
    out = tmp_path / "abc.json"
    out.write_text('{"status": "ok"}', encoding="utf-8")
    saved = save_result({}, out, {"partial": PARTIAL})
    assert saved.name == "abc.partial.json"
    assert json.loads(saved.read_text(encoding="utf-8"))["partial"] == PARTIAL


def test_a_stem_that_fits_its_unnumbered_sidecar_keeps_it_and_its_numbered_ones_fit(tmp_path, monkeypatch):
    from dokimi.commands.novelty_routing_report import _partial_destinations

    monkeypatch.setattr(os, "pathconf", lambda *args: 255, raising=False)
    stem = "x" * 240
    out = tmp_path / f"{stem}.json"
    out.write_text('{"status": "ok"}', encoding="utf-8")
    names = _partial_destinations(out)
    first = next(names)
    first.write_text('{"status": "ok"}', encoding="utf-8")  # a finished result there: taken
    second = next(names)
    assert first.name == f"{stem}.partial.json"
    assert second.name == f"{stem}.partial2.json" and len(second.name) <= 255


def test_a_numbered_sidecar_is_cut_only_when_its_counter_makes_it_too_long(tmp_path, monkeypatch):
    from dokimi.commands.novelty_routing_report import _sidecar_stem

    monkeypatch.setattr(os, "pathconf", lambda *args: 255, raising=False)
    out = tmp_path / f"{'x' * 240}.json"
    assert _sidecar_stem(out, ".partial99.json") == "x" * 240  # 254 bytes
    cut = _sidecar_stem(out, ".partial100000.json")
    assert cut != "x" * 240 and len(cut + ".partial100000.json") <= 255
    assert _sidecar_stem(out, ".partial999999.json") == cut


def test_a_long_stem_at_a_small_name_limit_is_cut_for_the_unnumbered_name_alone(tmp_path, monkeypatch):
    from dokimi.commands.novelty_routing_report import save_result

    out = tmp_path / f"{'x' * 20}.json"
    out.write_text('{"status": "ok"}', encoding="utf-8")
    monkeypatch.setattr(os, "pathconf", lambda *args: 28, raising=False)  # 15 bytes left for the stem
    saved = save_result({}, out, {"partial": PARTIAL})
    assert saved.name.startswith("xx-") and saved.name.endswith(".partial.json") and len(saved.name) == 28


def test_a_name_limit_with_no_room_for_the_digest_tag_is_an_error_for_a_long_stem(tmp_path, monkeypatch):
    from dokimi.commands.novelty_routing_report import save_result

    out = tmp_path / "abcdefghijklmn.json"  # 14 bytes: over the 12 left by a limit of 25
    out.write_text('{"status": "ok"}', encoding="utf-8")
    monkeypatch.setattr(os, "pathconf", lambda *args: 25, raising=False)  # 12 < the 13-byte tag
    with pytest.raises(OSError) as error:
        save_result({}, out, {"partial": PARTIAL})
    assert error.value.errno == errno.ENAMETOOLONG
    assert [p.name for p in tmp_path.iterdir()] == [out.name]


def test_a_partial_model_line_with_five_answered_calls_has_no_warning(capsys):
    from dokimi.commands.novelty_routing_report import print_all_vs_all_model

    print_all_vs_all_model({"partial": {"calls_done": 6, "calls_planned": 9},
                            "n_scored": {"a": 3, "b": 2}}, None)
    out = capsys.readouterr().out
    assert "partial: 6 of 9 calls, 5 answered" in out
    assert "WARNING" not in out


def test_the_blind_skill_warning_needs_a_lexical_arm(capsys):
    from dokimi.commands.novelty_routing_report import warn_blind_skills

    warn_blind_skills({}, {"a": ["q"]})
    assert capsys.readouterr().err == ""


def test_an_all_vs_all_repeat_run_stopped_mid_query_pins_its_counts(tmp_path, monkeypatch):
    # Skills db (1 query), net (2), perf (2); two repeats; 0.4 per call and a 1.00 cap. db runs
    # both its repeats, net's first query gets one of its two, and perf is never reached.
    _provider(monkeypatch, UsageRecord(cost_usd=0.4))
    root, queries = _catalog(tmp_path)
    extra = root / "skills" / "net"
    extra.mkdir(parents=True)
    (extra / "SKILL.md").write_text(
        "---\nname: net\ndescription: debug slow network sockets\n---\n\n# B\n", encoding="utf-8")
    by_skill = {**json.loads(queries.read_text()), "net": ["trace a socket", "dump packets"]}
    queries.write_text(json.dumps(by_skill), encoding="utf-8")
    out = tmp_path / "o.json"
    args = _parse("catalog", "routing", str(root), "--queries-by-skill", str(queries),
                  "--out", str(out), "--model", "m",
                  "--reps", "2", "--max-usd", "1")
    with pytest.raises(SystemExit, match="spend cap reached"):
        args.func(args)
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert (payload["partial"]["calls_done"], payload["partial"]["calls_planned"]) == (3, 10)
    model = payload["result"]["model"]
    assert model["n_unscored"]["net"] == 3             # 4 planned, 1 made: the refused repeats
    assert model["n_unscored"]["perf"] == 4            # never reached: 2 queries x 2 repeats
    noise = model["noise"]
    assert noise["n_queries_compared"] == 1            # only the query asked in full (db's)
    assert [row["query"] for row in noise["per_query"]] == ["add an index"]


def test_a_last_call_with_no_price_that_ends_the_run_leaves_no_partial_marker(tmp_path, monkeypatch):
    # One query, one call, no price: the run is complete, so it is not partial and does not exit.
    calls = _provider(monkeypatch, None)
    args, out = _single(tmp_path, QUERIES[:1], "--max-usd", "5")
    args.func(args)
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert "partial" not in payload and payload["status"] == "ok"
    assert len(calls) == 1 and payload["model"]["n_calls"] == 1


def test_an_all_vs_all_last_call_with_no_price_leaves_no_partial_marker(tmp_path, monkeypatch):
    # Two skills, one query for db: the last planned call has no price, so the run is complete.
    calls = _provider(monkeypatch, None)
    root, queries = _catalog(tmp_path)
    queries.write_text(json.dumps({"db": ["add an index"]}), encoding="utf-8")
    out = tmp_path / "o.json"
    args = _parse("catalog", "routing", str(root), "--queries-by-skill", str(queries),
                  "--out", str(out), "--model", "m",
                  "--max-usd", "5")
    args.func(args)
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert "partial" not in payload and payload.get("status") != "partial"
    assert len(calls) == 1


def test_an_unpriced_call_stops_the_later_repeats_and_skills_too(tmp_path, monkeypatch):
    calls = _provider(monkeypatch, None)
    root, queries = _catalog(tmp_path)
    out = tmp_path / "o.json"
    args = _parse("catalog", "routing", str(root), "--queries-by-skill", str(queries),
                  "--out", str(out), "--model", "m",
                  "--reps", "3", "--max-usd", "5")
    with pytest.raises(SystemExit, match="no price is known"):
        args.func(args)
    # The first unpriced reply stops the run: no repeat of that query and no later skill is asked.
    assert len(calls) == 1
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert (payload["partial"]["calls_done"], payload["partial"]["calls_planned"]) == (1, 9)


def _cut_and_unpriced():
    """A reply cut at the output ceiling that has no price: the cap stops on it (#517)."""
    return UsageRecord(cost_usd=None, truncated=True, completion_tokens=50)


def test_an_unpriced_reply_cut_at_the_ceiling_abstains_and_stops_the_run(tmp_path, monkeypatch):
    calls = _provider(monkeypatch, _cut_and_unpriced())
    args, out = _single(tmp_path, QUERIES, "--max-usd", "5")
    with pytest.raises(SystemExit, match="no price is known"):
        args.func(args)
    assert len(calls) == 1
    model = json.loads(out.read_text(encoding="utf-8"))["model"]
    # The cut '{"pick": 1}' is a decline, as a priced cut reply is, not a pick of card 1.
    assert model["per_query"] == [{"query": QUERIES[0], "repeat": 0, "picked": None,
                                   "abstained": True, "raw_reply": '{"pick": 1}'}]
    assert (model["n_abstained"], model["selection_rate"]) == (1, 0.0)


def test_an_all_vs_all_unpriced_reply_cut_at_the_ceiling_is_no_pick(tmp_path, monkeypatch):
    calls = _provider(monkeypatch, _cut_and_unpriced())
    root, queries = _catalog(tmp_path)
    out = tmp_path / "o.json"
    args = _parse("catalog", "routing", str(root), "--queries-by-skill", str(queries),
                  "--out", str(out), "--model", "m",
                  "--max-usd", "5")
    with pytest.raises(SystemExit, match="no price is known"):
        args.func(args)
    assert len(calls) == 1
    model = json.loads(out.read_text(encoding="utf-8"))["result"]["model"]
    assert model["n_abstained"] == 1
    assert {gold: picks for gold, picks in model["confusion"].items() if picks} == {"db": {"(none)": 1}}


def test_an_unpriced_call_stops_the_repeats_of_a_single_artifact_run(tmp_path, monkeypatch):
    calls = _provider(monkeypatch, None)
    args, out = _single(tmp_path, QUERIES[:2], "--reps", "3", "--max-usd", "5")
    with pytest.raises(SystemExit, match="no price is known"):
        args.func(args)
    assert len(calls) == 1
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert (payload["partial"]["calls_done"], payload["partial"]["calls_planned"]) == (1, 6)


def test_a_partial_single_run_rates_its_selections_over_the_calls_it_made(tmp_path, monkeypatch):
    # Three queries, the cap stops after two calls: the rate is over the two asked, not all three.
    _provider(monkeypatch, UsageRecord(cost_usd=0.6))
    args, out = _single(tmp_path, QUERIES, "--max-usd", "1")
    with pytest.raises(SystemExit, match="spend cap reached"):
        args.func(args)
    model = json.loads(out.read_text(encoding="utf-8"))["model"]
    hits = sum(1 for row in model["per_query"] if row.get("correct"))
    assert model["n_calls"] == 2
    assert model["selection_rate"] == round(hits / 2, 4)


def test_a_file_marked_partial_by_status_alone_is_a_partial_result_to_write_over(tmp_path):
    from dokimi.commands.novelty_routing_report import _is_partial_file, save_result

    marked = tmp_path / "routing.json"
    marked.write_text('{"status": "partial"}', encoding="utf-8")
    assert _is_partial_file(marked)
    assert save_result({}, marked, {"partial": PARTIAL}) == marked
    assert json.loads(marked.read_text(encoding="utf-8"))["partial"] == PARTIAL
