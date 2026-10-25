# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Real-harness trigger probe: staging, argv/env hygiene, transcript parsing, scoring."""

from __future__ import annotations

import json
from pathlib import Path

from dokimi import process
from dokimi.eval.arm_summary import summarize_skill_selection
from dokimi.external.trigger_set import TriggerQuery
from dokimi.harnesses import trigger_probe as tp
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
