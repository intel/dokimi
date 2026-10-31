# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Real-harness trigger probe: staging, argv/env hygiene, transcript parsing, scoring."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from dokimi import process
from dokimi.cli import build_parser
from dokimi.eval.arm_summary import summarize_skill_selection
from dokimi.external.trigger_set import TriggerQuery
from dokimi.harnesses import trigger_probe as tp
from dokimi.harnesses.agent_sandbox import solver_cli_flags
from dokimi.harnesses.payload_audit import audit_payload, markers_for


def _catalog(tmp_path: Path) -> Path:
    for name in ("sum-fast", "io-read"):
        d = tmp_path / "cat" / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: Use for {name}.\n---\nbody\n",
                                    encoding="utf-8")
    return tmp_path / "cat"


def _stream(*skills_per_turn, other: str = "", version: str = "2.1.304") -> str:
    lines = [{"type": "system", "subtype": "init", "skills": ["sum-fast", "io-read", "dataviz"],
              "claude_code_version": version}]
    if other:
        lines.append({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": other, "input": {}}]}})
    for turn in skills_per_turn:
        lines.append({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "ok"}] + [
            {"type": "tool_use", "name": "Skill", "input": {"skill": s}} for s in turn]}})
    lines.append({"type": "result", "subtype": "success", "is_error": False,
                  "total_cost_usd": 0.01, "num_turns": 2})
    return "\n".join(json.dumps(x) for x in lines) + "\n"


def test_parse_stream_takes_the_first_skill_turn_as_the_choice():
    s = tp.parse_stream(_stream(["plug:sum-fast", "io-read"], ["io-read"], other="Bash"))
    assert s["first_turn_loaded"] == ["sum-fast", "io-read"]
    assert s["selected"] == "sum-fast"
    assert s["loaded"] == ["sum-fast", "io-read", "io-read"]
    assert s["tools_before_choice"] == ["Bash"]
    assert s["listed"] == ["sum-fast", "io-read", "dataviz"] and s["claude_code_version"] == "2.1.304"
    assert tp.parse_stream(_stream())["selected"] is None


def test_tools_after_the_choice_are_not_counted_before_it():
    events = [{"type": "assistant", "message": {"id": "m1", "content": [
                  {"type": "tool_use", "name": "Skill", "input": {"skill": "sum-fast"}}]}},
              {"type": "assistant", "message": {"id": "m2", "content": [
                  {"type": "tool_use", "name": "Bash", "input": {}}]}}]
    s = tp.parse_stream("\n".join(json.dumps(e) for e in events))
    assert s["first_turn_loaded"] == ["sum-fast"] and s["tools_before_choice"] == []


def test_parse_stream_groups_per_block_events_by_message_id():
    # Claude Code streams each content block as its own event; one turn shares message.id.
    def block(mid, block, parent=None):
        return {"type": "assistant", "parent_tool_use_id": parent, "message": {"id": mid, "content": [block]}}

    def skill(s):
        return {"type": "tool_use", "name": "Skill", "input": {"skill": s}}

    events = [block("m1", {"type": "thinking", "thinking": "..."}), block("m1", skill("sum-fast")),
              block("m1", skill("io-read")), {"type": "user", "message": {"content": []}},
              block("m2", skill("dataviz"), parent="toolu_1"), block("m3", skill("sum-fast")),
              {"type": "result", "subtype": "success", "is_error": False}]
    s = tp.parse_stream("\n".join(json.dumps(e) for e in events))
    assert s["first_turn_loaded"] == ["sum-fast", "io-read"]
    assert s["loaded"] == ["sum-fast", "io-read", "sum-fast"]
    rec = {"question_id": "a", "expected_skill": "sum-fast",
           "arms": {"cc": {"answer": None, "error": None, "tool_usage": {"skill": s}}}}
    summary = summarize_skill_selection([rec])["cc"]
    assert summary["recall"] == 0.0 and summary["multi_load_rate"] == 1.0


def test_argv_is_the_query_verbatim_and_pins_only_what_is_asked():
    cfg = tp.ProbeConfig(model="haiku", max_turns=2)
    argv = tp.probe_argv("How do I sum?", cfg)
    assert argv[argv.index("-p") + 1] == "How do I sum?"
    assert "--settings" not in argv and "--tools" not in argv and "--plugin-dir" not in argv
    pinned = tp.probe_argv("q", tp.ProbeConfig(listing_budget_fraction=0.03))
    assert json.loads(pinned[pinned.index("--settings") + 1]) == {"skillListingBudgetFraction": 0.03}


def test_sealed_argv_switches_off_the_builtin_plugins_as_the_arms_do():
    seal = json.loads(solver_cli_flags()[solver_cli_flags().index("--settings") + 1])
    for frac, extra in ((None, {}), (0.05, {"skillListingBudgetFraction": 0.05})):
        argv = tp.probe_argv("q", tp.ProbeConfig(stock=False, listing_budget_fraction=frac))
        assert argv.count("--settings") == 1
        assert json.loads(argv[argv.index("--settings") + 1]) == {**seal, **extra}


def test_env_keeps_operator_config_out_and_bundled_skills_on_in_stock_mode(tmp_path):
    ws = tp.new_workspace()
    try:
        source = {"PATH": "/bin", "AWS_REGION": "us-east-2", "CLAUDECODE": "1",
                  "CLAUDE_CONFIG_DIR": "/home/op/.claude", "GITHUB_TOKEN": "x"}
        stock = tp.probe_env(ws, tp.ProbeConfig(), source)
        sealed = tp.probe_env(ws, tp.ProbeConfig(stock=False), source)
        assert stock["CLAUDE_CONFIG_DIR"] == str(ws.root / "config") and stock["HOME"] == str(ws.root / "home")
        assert "GITHUB_TOKEN" not in stock and "CLAUDECODE" not in stock
        assert "CLAUDE_CODE_DISABLE_BUNDLED_SKILLS" not in stock
        assert sealed["CLAUDE_CODE_DISABLE_BUNDLED_SKILLS"] == "1"
    finally:
        ws.cleanup()


def test_run_query_stages_a_clean_repo_whose_path_names_no_experiment(tmp_path):
    seen = {}

    def handler(call: process.Call):
        if call.argv[0] == "git":
            return None if call.argv[1] != "status" else process.reply("")
        seen["cwd"] = call.cwd
        seen["skills"] = sorted(p.name for p in (call.cwd / ".claude" / "skills").iterdir())
        seen["env"] = call.env
        return process.reply(_stream(["io-read"]))

    fake = process.FakeRunner(handler)
    # Real git for staging (it is the clean-repo claim under test), fake claude.
    def runner(argv, **kw):
        return process.run(argv, **kw) if argv[0] == "git" else fake(argv, **kw)

    r = tp.run_query("read a csv", _catalog(tmp_path), tp.ProbeConfig(), runner=runner,
                     source_env={"PATH": "/usr/bin:/bin"})
    assert r["skill"]["first_turn_loaded"] == ["io-read"] and r["error"] is None and r["cost_usd"] == 0.01
    assert seen["skills"] == ["io-read", "sum-fast"]
    assert audit_payload(str(seen["cwd"]), markers_for()) == []
    assert not seen["cwd"].exists()  # cleaned up


def test_out_of_turns_without_a_skill_is_no_trigger_not_a_failure(tmp_path):
    stream = _stream().replace('"subtype": "success", "is_error": false',
                               '"subtype": "error_max_turns", "is_error": true')

    def runner(argv, **kw):
        return process.run(argv, **kw) if argv[0] == "git" else process.reply(stream)

    r = tp.run_query("q", _catalog(tmp_path), tp.ProbeConfig(), runner=runner, source_env={"PATH": "/usr/bin:/bin"})
    assert r["error"] is None and r["skill"]["first_turn_loaded"] == []
    broken = tp.run_query("q", _catalog(tmp_path / "b"), tp.ProbeConfig(),
                          runner=lambda a, **kw: process.run(a, **kw) if a[0] == "git" else process.reply("", returncode=1),
                          source_env={"PATH": "/usr/bin:/bin"})
    assert broken["error"] == "no result event (exit 1)"


def test_a_hang_after_the_choice_is_scored_not_dropped(tmp_path):
    # A stock-tool Bash call can outlive the turn; the CLI is then killed at --timeout.
    with_result = _stream(["sum-fast"])
    no_result = with_result.rsplit('{"type": "result"', 1)[0]
    cases = {"after-result": (with_result, None, ["sum-fast"]), "no-result": (no_result, None, ["sum-fast"]),
             "no-skill-after-result": (_stream(), None, []), "nothing": ("", "timeout", [])}
    for name, (stream, error, loaded) in cases.items():
        def runner(argv, stream=stream, **kw):
            return process.run(argv, **kw) if argv[0] == "git" else process.reply(stream, timed_out=True)

        r = tp.run_query("q", _catalog(tmp_path / name), tp.ProbeConfig(), runner=runner,
                         source_env={"PATH": "/usr/bin:/bin"})
        assert r["error"] == error, name
        assert r["skill"]["first_turn_loaded"] == loaded, name


def test_records_score_with_the_same_summary_as_the_emulated_arm(tmp_path):
    answers = {"sum it": ["sum-fast"], "read it": ["sum-fast"], "hello": []}

    def runner(argv, **kw):
        if argv[0] == "git":
            return process.run(argv, **kw)
        q = argv[list(argv).index("-p") + 1]
        return process.reply(_stream(answers[q]) if answers[q] else _stream())

    queries = [TriggerQuery("a", "sum it", "sum-fast"), TriggerQuery("b", "read it", "io-read"),
               TriggerQuery("c", "hello", None), TriggerQuery("d", "unlabelled", None, labelled=False)]
    recs = tp.probe_trigger_set(queries, _catalog(tmp_path), tp.ProbeConfig(), trials=2, runner=runner)
    assert [r["question_id"] for r in recs] == ["a", "b", "c"]
    s = summarize_skill_selection(recs)
    assert set(s) == {"claude_code#1", "claude_code#2"}
    assert s["claude_code#1"]["recall"] == 0.5 and s["claude_code#1"]["misroute_rate"] == 0.5
    assert s["claude_code#1"]["false_trigger_rate"] == 0.0


def test_one_failed_run_is_that_trials_error_not_the_lost_set(tmp_path):
    def runner(argv, **kw):
        if argv[0] == "git":
            return process.run(argv, **kw)
        if argv[list(argv).index("-p") + 1] == "read it":
            raise FileNotFoundError(2, "No such file or directory", argv[0])
        return process.reply(_stream(["sum-fast"]))

    queries = [TriggerQuery("a", "sum it", "sum-fast"), TriggerQuery("b", "read it", "io-read")]
    recs = tp.probe_trigger_set(queries, _catalog(tmp_path), tp.ProbeConfig(), runner=runner)
    assert [r["question_id"] for r in recs] == ["a", "b"]
    assert recs[1]["arms"]["claude_code#1"]["error"].startswith("FileNotFoundError")
    s = summarize_skill_selection(recs)["claude_code#1"]
    assert s["recall"] == 1.0 and s["n_failed"] == 1


@pytest.mark.skipif(os.name == "nt", reason="a shell-script stand-in for claude")
def test_cli_runs_a_relative_claude_from_the_scratch_cwd(tmp_path, monkeypatch, capsys):
    (tmp_path / "stream.jsonl").write_text(_stream(["sum-fast"]), encoding="utf-8")
    fake = tmp_path / "bin" / "claude"
    fake.parent.mkdir()
    fake.write_text("#!/bin/sh\nexec cat \"$STREAM\"\n".replace("$STREAM", str(tmp_path / "stream.jsonl")),
                    encoding="utf-8")
    fake.chmod(0o755)
    (tmp_path / "ts.json").write_text(json.dumps({"questions": [
        {"id": "a", "question": "sum it", "expected_skill": "sum-fast"}]}), encoding="utf-8")
    _catalog(tmp_path)
    monkeypatch.chdir(tmp_path)
    args = build_parser().parse_args(["catalog", "trigger-probe", "--catalog", "cat", "--trigger-set",
                                      "ts.json", "--claude", "bin/claude", "--target", "sum-fast",
                                      "--out", "probe.json"])
    args.func(args)
    out = json.loads((tmp_path / "probe.json").read_text(encoding="utf-8"))
    assert out["status"] == "ok" and out["known_positive"]["passed"] is True
    arm = out["answers"][0]["arms"]["claude_code#1"]
    assert arm["error"] is None and arm["tool_usage"]["skill"]["first_turn_loaded"] == ["sum-fast"]


def test_run_query_passes_argv_env_timeout_and_empty_stdin(tmp_path):
    seen = {}

    def runner(argv, **kw):
        if argv[0] == "git":
            return process.run(argv, **kw)
        seen.update(kw, argv=list(argv))
        return process.reply(_stream(["sum-fast"]))

    cfg = tp.ProbeConfig(model="haiku", max_turns=7, timeout_sec=12.5, stock=False,
                         listing_budget_fraction=0.02, claude="/opt/claude")
    tp.run_query("sum it", _catalog(tmp_path), cfg, runner=runner, source_env={"PATH": "/bin"})
    argv = seen["argv"]
    assert argv[:3] == ["/opt/claude", "-p", "sum it"]
    for flag, value in (("--output-format", "stream-json"), ("--max-turns", "7"), ("--model", "haiku")):
        assert argv[argv.index(flag) + 1] == value
    assert "--verbose" in argv and "--no-session-persistence" in argv
    assert json.loads(argv[argv.index("--settings") + 1])["skillListingBudgetFraction"] == 0.02
    env = seen["env"].resolve()
    root = Path(env["HOME"]).parent
    assert env["TMPDIR"] == env["HOME"] == str(root / "home") and env["CLAUDE_CONFIG_DIR"] == str(root / "config")
    assert env["CLAUDE_CODE_DISABLE_BUNDLED_SKILLS"] == "1"
    assert seen["timeout"] == 12.5 and seen["input"] == "" and seen["cwd"] == root / "work"


def test_staging_copies_symlinked_skills_as_files(tmp_path):
    cat = _catalog(tmp_path)
    (tmp_path / "ext").mkdir()
    (tmp_path / "ext" / "ref.md").write_text("ref", encoding="utf-8")
    (cat / "io-read" / "ref.md").symlink_to(tmp_path / "ext" / "ref.md")
    ws = tp.new_workspace()
    try:
        copied = tp.stage_workspace(cat, ws) / ".claude" / "skills" / "io-read" / "ref.md"
        assert not copied.is_symlink() and copied.read_text(encoding="utf-8") == "ref"
    finally:
        ws.cleanup()


@pytest.mark.skipif(not Path("/proc/self/cwd").exists(), reason="reads /proc")
def test_kill_strays_ends_a_backgrounded_process_left_in_the_workspace(tmp_path):
    import subprocess

    (tmp_path / "work").mkdir()
    # Its own session, as the Bash tool's background job is: a group kill does not reach it.
    proc = subprocess.Popen(["sleep", "60"], cwd=tmp_path / "work", start_new_session=True)
    try:
        assert tp.kill_strays(tmp_path) == [proc.pid]
        assert proc.wait(timeout=5) == -9
    finally:
        proc.kill()


@pytest.mark.skipif(not Path("/proc/self/cwd").exists(), reason="reads /proc")
def test_run_query_kills_what_the_run_left_in_its_workspace_only(tmp_path):
    import shutil
    import subprocess

    procs = {}

    def runner(argv, **kw):
        if argv[0] == "git":
            return process.run(argv, **kw)
        sibling = Path(str(kw["cwd"].parent) + "d")  # a concurrent run's root, same prefix
        sibling.mkdir()
        procs["sibling_dir"] = sibling
        # Own sessions, as the Bash tool's background jobs: the timeout's group kill misses them.
        for name, cwd in (("left", kw["cwd"]), ("sibling", sibling)):
            procs[name] = subprocess.Popen(["sleep", "60"], cwd=cwd, start_new_session=True)
        return process.reply(_stream(["sum-fast"]))

    try:
        r = tp.run_query("q", _catalog(tmp_path), tp.ProbeConfig(), runner=runner,
                         source_env={"PATH": "/usr/bin:/bin"})
        assert r["error"] is None
        assert procs["left"].wait(timeout=5) == -9
        assert procs["sibling"].poll() is None
    finally:
        for name in ("left", "sibling"):
            if name in procs:
                procs[name].kill()
                procs[name].wait()
        if "sibling_dir" in procs:
            shutil.rmtree(procs["sibling_dir"], ignore_errors=True)


def _cli(tmp_path, monkeypatch, *extra, init_skills=("sum-fast", "io-read"), expected="sum-fast", known_passed=True):
    from dokimi.commands import catalog_probe

    seen = {}

    def fake_known(skill, catalog, cfg, **kw):
        seen.setdefault("known", []).append(skill)
        return {"skill": skill, "query": f"Use the {skill} skill.", "passed": known_passed,
                "loaded": [skill] if known_passed else [], "cost_usd": 0.01, "error": None}

    def fake_probe(queries, catalog, cfg, *, trials=1, **kw):
        seen.update(cfg=cfg, trials=trials, catalog=catalog)
        seen.setdefault("queries", []).extend(q.id for q in queries)
        skill = tp.parse_stream(_stream(["sum-fast"]).replace(
            '["sum-fast", "io-read", "dataviz"]', json.dumps(list(init_skills))))
        return [{"question_id": q.id, "question_text": q.text, "expected_skill": q.expected_skill,
                 "arms": {"claude_code#1": {"answer": None, "error": None, "tool_usage": {"skill": skill},
                                            "metrics": {"cost_usd": 0.01, "num_turns": 3,
                                                        "result_subtype": "error_max_turns"}}}} for q in queries]

    class Pool(catalog_probe.ThreadPoolExecutor):
        def __init__(self, n):
            seen["concurrency"] = n
            super().__init__(n)

    monkeypatch.setattr(tp, "probe_trigger_set", fake_probe)
    monkeypatch.setattr(tp, "known_positive", fake_known)
    monkeypatch.setattr(catalog_probe, "ThreadPoolExecutor", Pool)
    (tmp_path / "ts.json").write_text(json.dumps({"questions": [
        {"id": q, "question": f"q {q}", "expected_skill": expected} for q in ("a", "b", "c")]}),
        encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    if not (tmp_path / "cat").exists():
        _catalog(tmp_path)
    args = build_parser().parse_args(["catalog", "trigger-probe", "--catalog", "cat", "--trigger-set",
                                      "ts.json", "--claude", "sh", *extra])
    args.func(args)
    return seen


def test_cli_passes_every_flag_to_the_probe(tmp_path, monkeypatch):
    seen = _cli(tmp_path, monkeypatch, "--model", "haiku", "--limit", "2", "--trials", "3",
                "--max-turns", "6", "--timeout", "9", "--listing-budget-fraction", "0.04",
                "--concurrency", "5", "--sealed", "--out", "probe.json")
    cfg = seen["cfg"]
    assert (cfg.model, cfg.max_turns, cfg.timeout_sec, cfg.stock, cfg.listing_budget_fraction) == (
        "haiku", 6, 9.0, False, 0.04)
    assert os.path.isabs(cfg.claude) and seen["trials"] == 3 and seen["concurrency"] == 5
    assert sorted(seen["queries"]) == ["a", "b"]
    out = json.loads((tmp_path / "probe.json").read_text(encoding="utf-8"))
    assert not (tmp_path / "probe.json.partial").exists()
    assert out["mode"] == "sealed" and out["max_turns_hit"] == 2 and out["not_listed"] == []


def test_cli_refuses_a_bad_out_or_target_before_any_run(tmp_path, monkeypatch):
    from dokimi.utils import InputError

    with pytest.raises(InputError, match="not a writable file path"):
        _cli(tmp_path, monkeypatch, "--out", "nodir/probe.json")
    with pytest.raises(InputError, match="not a skill in --catalog"):
        _cli(tmp_path, monkeypatch, "--target", "sum-fsat", "--out", "probe.json")


def test_cli_refuses_a_label_that_names_no_catalog_skill(tmp_path, monkeypatch):
    from dokimi.utils import InputError

    with pytest.raises(InputError, match="names no skill in --catalog: sum-fsat"):
        _cli(tmp_path, monkeypatch, "--out", "probe.json", expected="sum-fsat")
    assert not (tmp_path / "probe.json.partial").exists()
    _cli(tmp_path, monkeypatch, "--out", "probe.json", expected=["sum-fast", "io-read"])


@pytest.mark.parametrize("trigger_set, extra", [
    ({"questions": [{"id": "a", "question": "q a"}]}, ()),
    ({"questions": [{"id": "a", "question": "q a"}]}, ("--target", "sum-fast")),
    ({"queries": []}, ()),
], ids=["unlabelled", "unlabelled-with-target", "empty-reach-set"])
def test_cli_exits_2_on_a_set_with_no_labelled_query_before_any_run(tmp_path, monkeypatch, capsys,
                                                                    trigger_set, extra):
    from dokimi import cli

    runs: list = []
    monkeypatch.setattr(tp, "probe_trigger_set", lambda queries, *a, **kw: runs.append(queries) or [])
    _catalog(tmp_path)
    (tmp_path / "ts.json").write_text(json.dumps(trigger_set), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["dokimi", "catalog", "trigger-probe", "--catalog", "cat", "--trigger-set",
                                     "ts.json", "--claude", "sh", "--out", "probe.json", *extra])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert err.startswith("Error: --trigger-set has no labelled queries: ts.json")
    assert ("--target 'sum-fast'" in err) == bool(extra)
    assert runs == [] and not (tmp_path / "probe.json").exists()
    assert not (tmp_path / "probe.json.partial").exists()


def test_cli_warns_when_no_run_listed_any_skill(tmp_path, monkeypatch, capsys):
    warning = "warning: Claude Code listed no skills in any run; not_listed was not checked"
    _cli(tmp_path, monkeypatch, "--out", "probe.json", init_skills=())
    assert warning in capsys.readouterr().err
    assert json.loads((tmp_path / "probe.json").read_text(encoding="utf-8"))["not_listed"] == []
    _cli(tmp_path, monkeypatch, "--out", "probe.json")
    assert "warning" not in capsys.readouterr().err


def test_cli_fails_when_claude_code_did_not_list_a_catalog_skill(tmp_path, monkeypatch):
    with pytest.raises(SystemExit, match="did not list: io-read"):
        _cli(tmp_path, monkeypatch, "--out", "probe.json", init_skills=("sum-fast", "dataviz"))
    assert json.loads((tmp_path / "probe.json").read_text(encoding="utf-8"))["not_listed"] == ["io-read"]


@pytest.mark.parametrize("stream, passed", [(_stream(["sum-fast"]), True), (_stream(), False)],
                         ids=["loads-the-skill", "never-loads-it"])
def test_known_positive_passes_only_when_the_explicit_request_loads_the_skill(tmp_path, stream, passed):
    def runner(argv, **kw):
        if argv[0] == "git":
            return process.run(argv, **kw)
        assert argv[list(argv).index("-p") + 1] == "Use the sum-fast skill."
        return process.reply(stream)

    r = tp.known_positive("sum-fast", _catalog(tmp_path), tp.ProbeConfig(), runner=runner,
                          source_env={"PATH": "/usr/bin:/bin"})
    assert r["passed"] is passed and r["error"] is None


def test_known_positive_that_cannot_run_fails_without_raising(tmp_path):
    def runner(argv, **kw):
        if argv[0] == "git":
            return process.run(argv, **kw)
        raise FileNotFoundError(2, "No such file or directory", argv[0])

    r = tp.known_positive("sum-fast", _catalog(tmp_path), tp.ProbeConfig(), runner=runner,
                          source_env={"PATH": "/usr/bin:/bin"})
    assert r["passed"] is False and r["error"].startswith("FileNotFoundError")


def test_cli_probes_the_target_or_the_first_skill_before_the_set(tmp_path, monkeypatch):
    assert _cli(tmp_path, monkeypatch, "--out", "probe.json")["known"] == ["io-read"]
    assert _cli(tmp_path, monkeypatch, "--target", "sum-fast", "--out", "probe.json")["known"] == ["sum-fast"]
    out = json.loads((tmp_path / "probe.json").read_text(encoding="utf-8"))
    assert out["status"] == "ok" and out["known_positive"]["passed"] is True


def test_cli_stops_before_the_labelled_runs_when_the_skill_is_not_visible(tmp_path, monkeypatch, capsys):
    with pytest.raises(SystemExit, match="invalid: skill not visible"):
        _cli(tmp_path, monkeypatch, "--out", "probe.json", known_passed=False)
    out = json.loads((tmp_path / "probe.json").read_text(encoding="utf-8"))
    assert out["status"] == "invalid: skill not visible" and out["answers"] == []
    assert out["known_positive"]["passed"] is False
    assert not (tmp_path / "probe.json.partial").exists()
    assert '"status": "invalid: skill not visible"' in capsys.readouterr().out


def test_no_known_positive_skips_the_guard(tmp_path, monkeypatch):
    seen = _cli(tmp_path, monkeypatch, "--no-known-positive", "--out", "probe.json", known_passed=False)
    assert "known" not in seen and sorted(seen["queries"]) == ["a", "b", "c"]
    out = json.loads((tmp_path / "probe.json").read_text(encoding="utf-8"))
    assert out["status"] == "unchecked" and out["known_positive"] is None


def test_cli_refuses_an_empty_catalog_before_the_guard_picks_a_skill(tmp_path, monkeypatch):
    # A negative-only set has no label naming a skill, so only the guard's default target could
    # name one; with no skill in the catalog that was an IndexError, not an input error.
    from dokimi.utils import InputError

    (tmp_path / "cat").mkdir()
    with pytest.raises(InputError, match="--catalog holds no skills"):
        _cli(tmp_path, monkeypatch, "--out", "probe.json", expected=None)
    assert not (tmp_path / "probe.json.partial").exists() and not (tmp_path / "probe.json").exists()


def test_no_known_positive_still_runs_a_negative_only_set_on_an_empty_catalog(tmp_path, monkeypatch):
    # The guard is what needs a skill; with it skipped the run is what it was before the refusal.
    (tmp_path / "cat").mkdir()
    seen = _cli(tmp_path, monkeypatch, "--no-known-positive", "--out", "probe.json", expected=None)
    assert "known" not in seen and sorted(seen["queries"]) == ["a", "b", "c"]


def test_negative_only_set_on_a_catalog_with_skills_still_runs_the_guard(tmp_path, monkeypatch):
    # names come from the catalog's skills, not the labels: a negative-only set on a non-empty
    # catalog is not refused, and the guard probes the first catalog skill as before.
    assert _cli(tmp_path, monkeypatch, "--out", "probe.json", expected=None)["known"] == ["io-read"]
