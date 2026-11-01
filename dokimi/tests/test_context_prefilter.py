# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`context prefilter`: guidance rules matched against Claude Code session logs, candidates for ablation.

Every session here is a small synthetic fixture written under ``tmp_path``; no real transcript is read.
"""

from __future__ import annotations

import json
import re

import pytest

from dokimi.cli import build_parser
from dokimi.context.prefilter import (
    CANDIDATE,
    IN_USE,
    INSUFFICIENT,
    KEPT,
    RARE,
    STATUSES,
    build_report,
    claims_from_guidance,
    render_table,
)
from dokimi.context.session_log import SessionError, parse_session, read_session

GUIDE = """# Guide

- Run `make ci-local` before pushing a branch.
- Keep the changelog fragment current.
- Check the onboarding wiki page for newcomers.
- Use `ruff format` when touching Python files.

| Command | Purpose |
| --- | --- |
| `pytest tests/test_x.py` | Run one file |

```bash
echo "this line is code and no claim"
```
"""

CHANGELOG = "Keep the changelog fragment current"
CI = "Run `make ci-local` before pushing a branch"


def _prompt(text, **extra):
    return {"type": "user", "message": {"role": "user", "content": text}, **extra}


def _tool(name, **args):
    return {"type": "assistant", "message": {"role": "assistant",
            "content": [{"type": "tool_use", "id": "t", "name": name, "input": args}]}}


def _session(*records, name="s"):
    return parse_session("\n".join(json.dumps(r) for r in records), name)


def _write(path, records):
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return path


def _row(report, text):
    return next(r for r in report["claims"] if text in r["text"])


def _report(sessions, guide=GUIDE, **kw):
    params = {"window": 10, "min_sessions": 1, "rare_max": 0, "in_use_rate": 0.2}
    params.update(kw)
    return build_report("AGENTS.md", guide, sessions, **params)


# --- session_log: what becomes an event --------------------------------------------------------

def test_prompts_are_typed_text_and_injected_records_are_not():
    session = _session(
        _prompt("CLAUDE.md echo: keep the changelog fragment current", isMeta=True),
        _prompt("<system-reminder>keep the changelog</system-reminder> please ship it"),
        _prompt("the subagent brief", isSidechain=True),
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t", "content": "ok"}]}},
    )
    assert [e.text for e in session.events] == ["please ship it"]


def test_tool_call_keeps_command_and_paths_and_drops_file_bodies():
    session = _session(_tool("Edit", file_path="/w/docs/guide.md", old_string="keep the changelog",
                             new_string="rewritten body", description="tidy"))
    (event,) = session.events
    assert event.kind == "tool"
    assert event.paths == ("/w/docs/guide.md",)
    assert "tidy" in event.text and "/w/docs/guide.md" in event.text
    assert "keep the changelog" not in event.text and "rewritten body" not in event.text


def test_bash_command_is_its_own_field_and_a_subagent_call_still_counts():
    record = _tool("Bash", command="make ci-local --offline")
    record["isSidechain"] = True
    (event,) = _session(record).events
    assert event.command == "make ci-local --offline"


def test_a_bad_line_is_reported_and_the_rest_still_reads():
    raw = "not json\n" + json.dumps(_prompt("hello there")) + "\n"
    session = parse_session(raw, "x")
    assert [e.text for e in session.events] == ["hello there"]
    assert session.errors == ["line 1: not JSON"]


def test_a_line_separator_inside_a_prompt_does_not_split_the_record():
    raw = json.dumps(_prompt("keep the changelog fragment"), ensure_ascii=False) + "\n"
    session = parse_session(raw, "x")
    assert session.errors == [] and [e.text for e in session.events] == ["keep the changelog fragment"]


def test_a_line_nested_too_deep_to_parse_is_reported_and_skipped():
    raw = "[" * 100_000 + "\n" + json.dumps(_prompt("still here")) + "\n"
    session = parse_session(raw, "x")
    assert [e.text for e in session.events] == ["still here"]
    assert session.errors == ["line 1: not JSON"]


def test_read_session_refuses_a_file_it_cannot_read_or_that_holds_no_event(tmp_path):
    with pytest.raises(SessionError, match="cannot read"):
        read_session(tmp_path / "missing.jsonl")
    empty = _write(tmp_path / "empty.jsonl", [{"type": "summary", "summary": "x"}])
    with pytest.raises(SessionError, match="no prompt and no tool call"):
        read_session(empty)


# --- claims: what a guidance file's rules are --------------------------------------------------

def test_claims_are_rule_lines_and_skip_headings_and_code():
    texts = [c.text for c in claims_from_guidance(GUIDE)]
    assert any(CI in t for t in texts) and any("ruff format" in t for t in texts)
    assert not any(t.startswith("# ") or "this line is code" in t for t in texts)
    assert not any(t == "Guide" for t in texts)


def test_backticked_names_split_into_commands_and_paths():
    claims = {c.text: c for c in claims_from_guidance(GUIDE)}
    ci = next(c for t, c in claims.items() if CI in t)
    assert ci.commands == ("make ci-local",) and ci.paths == ()
    table_row = next(c for t, c in claims.items() if "pytest" in t)
    assert table_row.commands == ("pytest tests/test_x.py",) and table_row.paths == ()
    changelog = next(c for c in claims_from_guidance("- Keep `changelog.d/` current.\n"))
    assert changelog.paths == ("changelog.d/",) and changelog.commands == ()


def test_a_rule_with_a_negation_carries_its_protection_tag():
    claims = claims_from_guidance("- Never commit secrets to the repository.\n")
    assert claims and claims[0].protection


# --- matching: trigger, acted, corrected ---------------------------------------------------------

def test_an_episode_counts_once_inside_its_window():
    session = _session(_prompt("update the changelog fragment"),
                       _prompt("also the changelog fragment for the second part"))
    row = _row(_report([session]), CHANGELOG)
    assert row["episodes"] == 1 and row["sessions_triggered"] == 1


def test_claim_is_acted_on_when_the_named_command_runs_near_the_trigger():
    acting = _session(_prompt("push the branch now"), _tool("Bash", command="make ci-local"),
                      _tool("Bash", command="git push origin b"), name="acting")
    idle = _session(_prompt("push the branch now"), _tool("Bash", command="git push origin b"), name="idle")
    row = _row(_report([acting, idle]), CI)
    assert row["episodes"] == 2 and row["acted"] == 1 and row["acted_rate"] == 0.5


def test_a_longer_command_is_not_the_named_one():
    session = _session(_prompt("push the branch now"), _tool("Bash", command="make ci-local-extra"))
    assert _row(_report([session]), CI)["acted"] == 0


def test_a_user_pushback_after_the_trigger_is_a_correction():
    corrected = _session(_prompt("push the branch"), _prompt("no, the branch must not go before ci"))
    agreed = _session(_prompt("push the branch"), _prompt("push the branch again, thanks"))
    row = _row(_report([corrected, agreed]), CI)
    assert row["corrected"] == 1 and row["correction_rate"] == 0.5


def test_a_path_matches_from_a_segment_start_not_inside_a_longer_name():
    rule = "- Update `a.md` before the release.\n"
    inside = _session(_prompt("update the release"), _tool("Edit", file_path="/w/data.md"))
    named = _session(_prompt("update the release"), _tool("Edit", file_path="/w/docs/a.md"))
    assert _row(_report([inside, named], rule), "Update")["acted"] == 1


def test_a_file_name_does_not_match_a_longer_extension_or_a_backup_of_it():
    rule = "- Update `a.md` before the release.\n"
    longer = _session(_prompt("update the release"), _tool("Edit", file_path="/w/a.mdx"))
    backup = _session(_prompt("update the release"), _tool("Edit", file_path="/w/a.md.bak"))
    assert _row(_report([longer, backup], rule), "Update")["acted"] == 0


def test_a_directory_name_matches_the_entries_under_it():
    rule = "- Keep `changelog.d/` current.\n"
    inside = _session(_prompt("keep the changelog current"), _tool("Write", file_path="/w/changelog.d/x.md"))
    assert _row(_report([inside], rule), "Keep")["acted"] == 1


def test_a_claim_without_a_named_command_is_not_measured_as_acted():
    session = _session(_prompt("keep the changelog fragment current"))
    row = _row(_report([session]), CHANGELOG)
    assert row["acted"] is None and "acted_not_measurable" in row["flags"]


def test_one_shared_word_of_a_three_word_rule_is_not_a_trigger():
    partial = _session(_prompt("keep the readme current"), _prompt("fix the readme typo"))
    assert _row(_report([partial]), CHANGELOG)["sessions_triggered"] == 0


def test_a_memory_echo_of_the_rule_is_not_a_trigger():
    echo = _session(_prompt("Keep the changelog fragment current", isMeta=True), _prompt("fix the readme"))
    assert _row(_report([echo]), CHANGELOG)["sessions_triggered"] == 0


# --- status and ranking --------------------------------------------------------------------------

def _ranking_corpus():
    sessions = []
    for i in range(6):
        events = [_prompt("fix the readme typo")]
        if i < 4:
            events.append(_prompt("keep the changelog fragment current"))
        if i < 2:
            events.append(_prompt("touching python files"))
        if i == 5:
            events.append(_prompt("release the standup notes"))
        sessions.append(_session(*events, name=f"s{i}"))
    return sessions


GUIDE_RANK = """- Keep the changelog fragment current.
- Use `ruff format` when touching Python files.
- Never commit secrets to the repository.
- Check the onboarding wiki page for newcomers.
- Mention the release train in standup notes.
"""


def test_statuses_split_in_use_rare_protected_and_candidates():
    # Triggered in 4, 2, 0 and 1 of 6 sessions; rare_max=3 makes 2 and 1 rare, and 4 is over the in-use rate.
    report = _report(_ranking_corpus(), GUIDE_RANK, rare_max=3, in_use_rate=0.5)
    assert _row(report, CHANGELOG)["status"] == IN_USE
    assert _row(report, "Use `ruff")["status"] == RARE
    assert _row(report, "Mention the release")["status"] == RARE
    assert _row(report, "Never commit")["status"] == KEPT
    assert _row(report, "Check the")["status"] == CANDIDATE


def test_rare_and_protected_rules_are_listed_but_never_ranked():
    report = _report(_ranking_corpus(), GUIDE_RANK, rare_max=3, in_use_rate=0.5)
    assert all(r["rank"] is None for r in report["claims"] if r["status"] != CANDIDATE)
    assert _row(report, "Check the")["rank"] == 1  # the only candidate left: zero triggers


def test_candidates_are_ranked_by_trigger_rate_lowest_first():
    report = _report(_ranking_corpus(), GUIDE_RANK, rare_max=0, in_use_rate=0.5)
    assert [_row(report, t)["rank"] for t in ("Check the", "Mention the release", "Use `ruff")] == [1, 2, 3]
    assert _row(report, CHANGELOG)["status"] == IN_USE and _row(report, CHANGELOG)["rank"] is None


def test_a_protected_rule_is_kept_even_with_no_trigger_at_all():
    report = _report(_ranking_corpus(), GUIDE_RANK, rare_max=3, in_use_rate=0.5)
    row = _row(report, "Never commit")
    assert row["status"] == KEPT and row["rank"] is None and row["sessions_triggered"] == 0


def test_too_few_sessions_rank_nothing():
    report = _report(_ranking_corpus()[:2], GUIDE_RANK, min_sessions=5)
    assert {r["status"] for r in report["claims"]} == {INSUFFICIENT, KEPT}
    assert all(r["rank"] is None for r in report["claims"])


def test_the_wording_never_says_cut_or_remove():
    report = _report(_ranking_corpus(), GUIDE_RANK, rare_max=3, in_use_rate=0.5)
    text = json.dumps(report) + render_table(report)
    assert not re.search(r"\b(cut|remove|delete|drop)\b", text, re.I)
    assert set(r["status"] for r in report["claims"]) <= set(STATUSES)


def test_the_table_lists_each_claim_and_states_it_is_a_prefilter():
    report = _report(_ranking_corpus(), GUIDE_RANK, rare_max=3, in_use_rate=0.5)
    table = render_table(report)
    assert "candidate_for_ablation" in table and "kept_protected" in table
    assert "not verdicts" in table


# --- command line -------------------------------------------------------------------------------

def test_the_command_reads_a_quoted_glob_and_prints_json(tmp_path, capsys):
    guide = tmp_path / "AGENTS.md"
    guide.write_text(GUIDE, encoding="utf-8")
    logs = tmp_path / "logs"
    logs.mkdir()
    for i in range(3):
        _write(logs / f"s{i}.jsonl", [_prompt("fix the readme typo")])
    args = build_parser().parse_args(["context", "prefilter", str(guide), f"{logs}/*.jsonl",
                                      "--min-sessions", "1", "--json"])
    args.func(args)
    report = json.loads(capsys.readouterr().out)
    assert report["format"] == "session_prefilter" and report["format_version"] == 1
    assert report["sessions"] == 3


def test_the_command_prints_a_table_and_still_reports_a_bad_session(tmp_path, capsys):
    guide = tmp_path / "AGENTS.md"
    guide.write_text(GUIDE, encoding="utf-8")
    good = _write(tmp_path / "good.jsonl", [_prompt("push the branch now")])
    bad = tmp_path / "bad.jsonl"
    bad.write_text("not json at all\n", encoding="utf-8")
    args = build_parser().parse_args(["context", "prefilter", str(guide), str(good), str(bad),
                                      "--min-sessions", "1"])
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    out, err = capsys.readouterr()
    assert exc.value.code == 1
    assert "rank" in out and "1 session(s)" in out
    assert "no prompt and no tool call" in err


def test_the_command_refuses_a_guidance_file_it_cannot_read(tmp_path):
    args = build_parser().parse_args(["context", "prefilter", str(tmp_path / "none.md"), str(tmp_path / "x.jsonl")])
    with pytest.raises(SystemExit, match="cannot read"):
        args.func(args)

