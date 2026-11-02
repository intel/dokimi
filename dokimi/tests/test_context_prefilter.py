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
    Tally,
    build_report,
    claims_from_guidance,
    render_table,
    status_of,
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



# --- review fixes: keyless rules, separators, shell commands, corrections, ranking, sources --------

def test_a_rule_of_named_commands_only_is_kept_and_triggers_on_its_command():
    guide = "- Run `pytest`.\n- Do not use `sudo`.\n"
    session = _session(_prompt("the tests: pytest"), _tool("Bash", command="uv run pytest -q"))
    rows = _report([session], guide)
    run = _row(rows, "Run `pytest`")
    assert run["episodes"] == 1 and run["acted"] == 1
    assert _row(rows, "sudo")["status"] == KEPT


def test_windows_separators_match_a_path_and_a_backslash_directory_is_a_path():
    rule = "- Keep `changelog.d/` current.\n- Update `docs/guide.md` before release.\n"
    write = _session(_prompt("keep the changelog current"), _tool("Write", file_path="C:\\w\\changelog.d\\x.md"))
    assert _row(_report([write], rule), "Keep")["acted"] == 1
    edit = _session(_prompt("update the release"), _tool("Edit", file_path="C:\\work\\docs\\guide.md"))
    assert _row(_report([edit], rule), "Update")["acted"] == 1
    (dir_claim,) = claims_from_guidance("- Keep `C:\\repo\\docs\\` clean.\n")
    assert dir_claim.paths == ("C:\\repo\\docs\\",) and dir_claim.commands == ()


def test_a_shell_command_that_writes_a_named_directory_counts_as_acting():
    rule = "- Keep `changelog.d/` current.\n"
    session = _session(_prompt("keep the changelog current"), _tool("Bash", command="printf 'entry' > changelog.d/x.md"))
    assert _row(_report([session], rule), "Keep")["acted"] == 1


@pytest.mark.parametrize("command", ["echo 'make ci-local'", "grep -F 'make ci-local' AGENTS.md", "# make ci-local",
                                     "echo 'a; make ci-local'", "ls # x; make ci-local"])
def test_a_mention_of_the_command_is_not_an_action(command):
    session = _session(_prompt("push the branch now"), _tool("Bash", command=command))
    assert _row(_report([session]), CI)["acted"] == 0


@pytest.mark.parametrize("command", ["make ci-local", "uv run make ci-local", "cd x && make ci-local",
                                     "make lint & make ci-local", "timeout 600 make ci-local", "make  ci-local"])
def test_a_command_run_directly_after_a_runner_or_in_a_chain_is_an_action(command):
    session = _session(_prompt("push the branch now"), _tool("Bash", command=command))
    assert _row(_report([session]), CI)["acted"] == 1


@pytest.mark.parametrize("command", ["make ci-local.debug", "make ci-local/x", "make ci-local=1"])
def test_a_longer_target_of_the_named_command_is_not_an_action(command):
    session = _session(_prompt("push the branch now"), _tool("Bash", command=command))
    assert _row(_report([session]), CI)["acted"] == 0


def test_a_quoted_argument_of_the_named_command_is_an_action():
    rule = "- Run `pytest tests/test_x.py` before pushing a branch.\n"
    session = _session(_prompt("push the branch now"), _tool("Bash", command='pytest "tests/test_x.py" -q'))
    assert _row(_report([session], rule), "Run")["acted"] == 1


@pytest.mark.parametrize("command", ["echo 'changelog.d/x.md'", "ls # changelog.d/x.md", "echo changelog.d/x.md"])
def test_a_path_printed_or_commented_by_a_shell_command_is_not_acted_on(command):
    rule = "- Keep `changelog.d/` current.\n"
    session = _session(_prompt("keep the changelog current"), _tool("Bash", command=command))
    assert _row(_report([session], rule), "Keep")["acted"] == 0


def test_a_quoted_separator_or_hash_in_a_path_argument_is_kept_as_text():
    rule = "- Keep `ab.py` current.\n"
    session = _session(_prompt("keep ab.py current"), _tool("Bash", command='cat "a#b.py"'))
    assert _row(_report([session], rule), "Keep")["acted"] == 0


def test_a_path_read_by_a_shell_command_is_acted_on():
    rule = "- Keep `changelog.d/` current.\n"
    session = _session(_prompt("keep the changelog current"), _tool("Bash", command="cat changelog.d/x.md"))
    assert _row(_report([session], rule), "Keep")["acted"] == 1


def test_a_correction_is_read_from_the_next_prompt_only():
    later = _session(_prompt("push the branch"), _prompt("thanks, continue"),
                     _prompt("no, the branch must not go before ci"))
    assert _row(_report([later]), CI)["corrected"] == 0


def test_goose_edit_bodies_are_not_event_text():
    from dokimi.context.session_log import tool_event
    event = tool_event({"path": "/w/README.md", "file_text": "Keep the changelog fragment current",
                        "old_str": "changelog", "new_str": "changelog fragment"})
    assert "changelog" not in event.text and event.paths == ("/w/README.md",)


def test_candidates_with_equal_rounded_rates_rank_by_exact_count():
    from dokimi.context.prefilter import _rank_key
    more_triggered = {"sessions_triggered": 3, "trigger_rate": 0.001, "episodes": 3, "acted": 0, "acted_rate": 0.0}
    fewer_triggered = {"sessions_triggered": 2, "trigger_rate": 0.001, "episodes": 2, "acted": 2, "acted_rate": 1.0}
    assert min([more_triggered, fewer_triggered], key=_rank_key) is fewer_triggered


def test_a_repeated_or_aliased_source_is_read_once(tmp_path):
    from dokimi.commands.context import _read_sessions
    log = _write(tmp_path / "s.jsonl", [_prompt("push the branch now")])
    alias = tmp_path / "alias.jsonl"
    alias.symlink_to(log)
    sessions, failed = _read_sessions([str(log)] * 5 + [str(alias), f"{tmp_path}/*.jsonl"])
    assert len(sessions) == 1 and not failed


@pytest.mark.parametrize("flag,value", [("--rare-max", "-1"), ("--in-use-rate", "nan"), ("--in-use-rate", "inf"),
                                        ("--in-use-rate", "1.5"), ("--in-use-rate", "-0.1")])
def test_the_thresholds_refuse_values_outside_their_domain(tmp_path, flag, value):
    guide = tmp_path / "AGENTS.md"
    guide.write_text(GUIDE, encoding="utf-8")
    log = _write(tmp_path / "s.jsonl", [_prompt("push the branch now")])
    with pytest.raises(SystemExit):
        build_parser().parse_args(["context", "prefilter", str(guide), str(log), flag, value])


def test_the_thresholds_accept_their_edge_values(tmp_path):
    guide = tmp_path / "AGENTS.md"
    guide.write_text(GUIDE, encoding="utf-8")
    log = _write(tmp_path / "s.jsonl", [_prompt("push the branch now")])
    args = build_parser().parse_args(["context", "prefilter", str(guide), str(log),
                                      "--rare-max", "0", "--in-use-rate", "1"])
    assert args.rare_max == 0 and args.in_use_rate == 1.0


# --- review round 3: quoting, heredocs, bare names, boundaries, shapes -------------------------

@pytest.mark.parametrize("command", ["echo '> changelog.d/x.md'", 'echo "a > changelog.d/x.md"',
                                     "printf '%s' '>>changelog.d/x'", "git commit -m '> changelog.d/x.md'"])
def test_a_quoted_redirection_sign_is_text_not_a_redirect(command):
    rule = "- Keep `changelog.d/` current.\n"
    session = _session(_prompt("keep the changelog current"), _tool("Bash", command=command))
    assert _row(_report([session], rule), "Keep")["acted"] == 0


def test_a_redirect_after_quoted_text_is_still_a_write_to_its_target():
    rule = "- Keep `changelog.d/` current.\n"
    session = _session(_prompt("keep the changelog current"),
                       _tool("Bash", command="echo 'a > b' > changelog.d/x.md"))
    assert _row(_report([session], rule), "Keep")["acted"] == 1


def test_a_heredoc_body_naming_the_command_is_text_not_a_run():
    session = _session(_prompt("push the branch now"),
                       _tool("Bash", command="cat > notes.txt <<'EOF'\nmake ci-local\nEOF"))
    assert _row(_report([session]), CI)["acted"] == 0


def test_a_heredoc_body_naming_a_path_does_not_act_on_it_but_a_heredoc_target_does():
    rule = "- Keep `changelog.d/` current.\n"
    body = _session(_prompt("keep the changelog current"),
                    _tool("Bash", command="cat > notes.txt <<EOF\nchangelog.d/x.md\nEOF"))
    target = _session(_prompt("keep the changelog current"),
                      _tool("Bash", command="cat > changelog.d/x.md <<'EOF'\nentry\nEOF"))
    assert _row(_report([body], rule), "Keep")["acted"] == 0
    assert _row(_report([target], rule), "Keep")["acted"] == 1


@pytest.mark.parametrize("name", ["Makefile", "Dockerfile", "LICENSE"])
def test_a_bare_file_name_claim_is_acted_on_when_an_edit_touches_that_file(name):
    rule = f"- Edit `{name}` carefully.\n"
    session = _session(_prompt("edit the file carefully"), _tool("Edit", file_path=f"/repo/{name}"))
    assert _row(_report([session], rule), "Edit")["acted"] == 1


@pytest.mark.parametrize("command", ['git commit -m "update docs/guide.md"', "gh pr create --body 'see docs/guide.md'",
                                     'git commit -m "docs/guide.md updated"'])
def test_a_quoted_sentence_that_names_a_path_is_not_an_action(command):
    rule = "- Update `docs/guide.md` when behaviour changes.\n"
    session = _session(_prompt("update the guide behaviour"), _tool("Bash", command=command))
    assert _row(_report([session], rule), "Update")["acted"] == 0


def test_a_quoted_path_argument_without_spaces_is_still_an_action():
    rule = "- Update `docs/guide.md` when behaviour changes.\n"
    session = _session(_prompt("update the guide behaviour"), _tool("Bash", command='cat "docs/guide.md"'))
    assert _row(_report([session], rule), "Update")["acted"] == 1


def test_a_rule_at_exactly_min_sessions_is_measured_and_one_session_short_is_insufficient():
    claim = claims_from_guidance("- Keep `changelog.d/` current.\n")[0]
    assert status_of(claim, Tally(), 3, min_sessions=3, rare_max=0, in_use_rate=0.5) == CANDIDATE
    assert status_of(claim, Tally(), 2, min_sessions=3, rare_max=0, in_use_rate=0.5) == INSUFFICIENT


def test_exactly_rare_max_triggered_sessions_is_rare_and_one_more_is_not():
    claim = claims_from_guidance("- Keep `changelog.d/` current.\n")[0]
    assert status_of(claim, Tally(sessions_triggered=3), 10, min_sessions=1, rare_max=3, in_use_rate=0.9) == RARE
    assert status_of(claim, Tally(sessions_triggered=4), 10, min_sessions=1, rare_max=3, in_use_rate=0.9) == CANDIDATE


def test_a_trigger_share_exactly_at_in_use_rate_is_in_use():
    claim = claims_from_guidance("- Keep `changelog.d/` current.\n")[0]
    assert status_of(claim, Tally(sessions_triggered=5), 10, min_sessions=1, rare_max=3, in_use_rate=0.5) == IN_USE
    assert status_of(claim, Tally(sessions_triggered=4), 10, min_sessions=1, rare_max=3, in_use_rate=0.5) == CANDIDATE


def test_a_session_with_two_episodes_counts_once_in_sessions_triggered():
    session = _session(_prompt("push the branch now"), _tool("Read", file_path="/w/a.py"),
                       _tool("Read", file_path="/w/b.py"), _prompt("push the branch now again"))
    row = _row(_report([session], window=2), CI)
    assert row["episodes"] == 2 and row["sessions_triggered"] == 1


def test_an_action_counts_only_within_the_window_of_its_trigger():
    near = _session(_prompt("push the branch now"), _tool("Read", file_path="/w/a.py"),
                    _tool("Bash", command="make ci-local"))
    far = _session(_prompt("push the branch now"), _tool("Read", file_path="/w/a.py"),
                   _tool("Read", file_path="/w/b.py"), _tool("Bash", command="make ci-local"))
    assert _row(_report([near], window=2), CI)["acted"] == 1
    assert _row(_report([far], window=2), CI)["acted"] == 0


def test_candidates_with_the_same_trigger_count_rank_by_the_lower_acted_share():
    from dokimi.context.prefilter import _rank_key
    low = {"sessions_triggered": 2, "episodes": 2, "acted": 0, "acted_rate": 0.0}
    high = {"sessions_triggered": 2, "episodes": 2, "acted": 2, "acted_rate": 1.0}
    assert min([high, low], key=_rank_key) is low


def test_a_line_that_is_json_but_not_an_object_is_reported_and_the_rest_still_reads():
    raw = "\n".join([json.dumps(_prompt("hello there")), "[]", "42",
                     json.dumps({"type": "summary", "summary": "x"})]) + "\n"
    session = parse_session(raw, "x")
    assert [e.text for e in session.events] == ["hello there"]
    assert session.errors == ["line 2: not a JSON object", "line 3: not a JSON object"]


def test_a_record_whose_message_or_content_has_the_wrong_shape_is_reported_and_tool_results_are_not():
    tool_result = {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t", "content": "ok"}]}}
    raw = "\n".join([json.dumps(_prompt("hello there")), json.dumps({"type": "user", "message": "oops"}),
                     json.dumps({"type": "user", "message": {"role": "user", "content": {"text": "x"}}}),
                     json.dumps(tool_result)]) + "\n"
    session = parse_session(raw, "x")
    assert [e.text for e in session.events] == ["hello there"]
    assert session.errors == ["line 2: message is not an object",
                              "line 3: message content is not text or a list of blocks"]


def test_an_apostrophe_inside_double_quotes_does_not_hide_the_path_after_it():
    rule = "- Update `docs/guide.md` when behaviour changes.\n"
    command = "echo \"isn't\"; cat docs/guide.md; echo 'a b'"
    session = _session(_prompt("update the guide behaviour"), _tool("Bash", command=command))
    assert _row(_report([session], rule), "Update")["acted"] == 1


@pytest.mark.parametrize("command", ["sudo -n make ci-local", "env -u PYTHONPATH make ci-local",
                                     "timeout --signal=TERM 600 make ci-local", "timeout -s TERM 600 make ci-local",
                                     "env A=1 sudo -u root make ci-local", "uv run --frozen make ci-local",
                                     "make ci-local>results.txt", "make ci-local</dev/null"])
def test_a_command_run_under_wrapper_options_or_a_redirect_is_an_action(command):
    session = _session(_prompt("push the branch now"), _tool("Bash", command=command))
    assert _row(_report([session]), CI)["acted"] == 1


@pytest.mark.parametrize("command", [r'echo "say \" ; make ci-local ; \""', "echo \\\nmake ci-local",
                                     "echo 'a\\\nmake ci-local'", r"echo \; make ci-local"])
def test_an_escaped_quote_or_a_continued_or_printed_line_is_not_a_run(command):
    session = _session(_prompt("push the branch now"), _tool("Bash", command=command))
    assert _row(_report([session]), CI)["acted"] == 0


def test_a_line_continuation_joins_the_command_it_splits():
    session = _session(_prompt("push the branch now"), _tool("Bash", command="make \\\nci-local"))
    assert _row(_report([session]), CI)["acted"] == 1


def test_a_redirect_directly_after_the_named_command_is_an_action():
    rule = "- Run `pytest` when testing changes.\n"
    session = _session(_prompt("testing changes"), _tool("Bash", command="pytest>results.txt"))
    assert _row(_report([session], rule), "Run")["acted"] == 1


def test_a_path_in_an_escaped_quote_is_not_a_path_argument():
    rule = "- Keep `changelog.d/` current.\n"
    command = r'echo "say \" ; cat changelog.d/x.md ; \""'
    session = _session(_prompt("keep the changelog current"), _tool("Bash", command=command))
    assert _row(_report([session], rule), "Keep")["acted"] == 0


# --- round 6: keys and commands read by the same shell rules; pipelines; heredocs; bare names; window ----
@pytest.mark.parametrize(("key", "command"), [("python -m pytest", "python -m pytest"),
                                              ("uv run pytest", "uv run pytest"),
                                              ("sudo make install", "sudo make install"),
                                              ("timeout 300 make test", "timeout 300 make test")])
def test_a_wrapper_in_the_guidance_key_matches_the_same_wrapped_command(key, command):
    rule = f"- Run `{key}` when testing changes.\n"
    session = _session(_prompt("testing changes"), _tool("Bash", command=command))
    assert _row(_report([session], rule), "Run")["acted"] == 1


@pytest.mark.parametrize("command", ["true | make ci-local", "(make ci-local)", "if true; then make ci-local; fi",
                                     "for f in a; do make ci-local; done", "true && (make ci-local)"])
def test_a_command_run_as_a_pipeline_stage_or_inside_compound_syntax_is_an_action(command):
    session = _session(_prompt("push the branch now"), _tool("Bash", command=command))
    assert _row(_report([session]), CI)["acted"] == 1


@pytest.mark.parametrize("command", ['echo "<<true"\ncat changelog.d/x.md\ntrue',
                                     'echo "<<true"\nprintf "x" > changelog.d/x.md\ntrue'])
def test_a_quoted_heredoc_sign_does_not_erase_the_commands_after_it(command):
    rule = "- Keep `changelog.d/` current.\n"
    session = _session(_prompt("keep the changelog current"), _tool("Bash", command=command))
    assert _row(_report([session], rule), "Keep")["acted"] == 1


def test_a_quoted_heredoc_sign_before_the_named_command_still_runs_it():
    session = _session(_prompt("push the branch now"), _tool("Bash", command='echo "<<true"\nmake ci-local\ntrue'))
    assert _row(_report([session]), CI)["acted"] == 1


def test_a_key_with_a_quoted_argument_with_a_space_matches_the_same_argument():
    rule = '- Run `pytest "tests/my test.py"` when testing changes.\n'
    session = _session(_prompt("testing changes"), _tool("Bash", command='pytest "tests/my test.py"'))
    assert _row(_report([session], rule), "Run")["acted"] == 1


def test_a_path_key_with_a_space_in_the_file_name_is_a_path_and_an_edit_of_it_is_an_action():
    rule = "- Update `docs/my guide.md` when behaviour changes.\n"
    session = _session(_prompt("update behaviour changes"), _tool("Edit", file_path="/repo/docs/my guide.md"))
    row = _row(_report([session], rule), "Update")
    assert row["keys"]["paths"] == ["docs/my guide.md"] and row["acted"] == 1


@pytest.mark.parametrize("command", ["cat Makefile", "sed -i 's/old/new/' Makefile", "truncate -s 0 Makefile"])
def test_shell_access_to_a_bare_file_name_key_is_an_action(command):
    rule = "- Edit `Makefile` carefully.\n"
    session = _session(_prompt("edit the file carefully"), _tool("Bash", command=command))
    assert _row(_report([session], rule), "Edit")["acted"] == 1


@pytest.mark.parametrize("command", ["grep pytest src/", "which pytest", "echo pytest"])
def test_a_lowercase_command_key_named_as_an_argument_is_not_an_action(command):
    rule = "- Run `pytest` before pushing a branch.\n"
    session = _session(_prompt("push the branch now"), _tool("Bash", command=command))
    assert _row(_report([session], rule), "Run")["acted"] == 0


def test_a_printed_bare_file_name_key_is_not_an_action():
    rule = "- Edit `Makefile` carefully.\n"
    session = _session(_prompt("edit the file carefully"), _tool("Bash", command="echo Makefile"))
    assert _row(_report([session], rule), "Edit")["acted"] == 0


@pytest.mark.parametrize("name", ["pytest", "make"])
def test_an_edit_of_a_file_named_like_a_lowercase_command_key_is_not_an_action(name):
    rule = f"- Run `{name}` before pushing a branch.\n"
    session = _session(_prompt("push the branch now"), _tool("Edit", file_path=f"/repo/{name}"))
    assert _row(_report([session], rule), "Run")["acted"] == 0


def test_an_action_before_the_trigger_inside_the_window_counts():
    session = _session(_tool("Bash", command="make ci-local"), _prompt("push the branch now"))
    assert _row(_report([session], window=2), CI)["acted"] == 1


def test_an_action_exactly_window_events_before_the_trigger_counts_and_one_more_does_not():
    edge = _session(_tool("Bash", command="make ci-local"), _tool("Read", file_path="/w/a.py"),
                    _prompt("push the branch now"))
    past = _session(_tool("Bash", command="make ci-local"), _tool("Read", file_path="/w/a.py"),
                    _tool("Read", file_path="/w/b.py"), _prompt("push the branch now"))
    assert _row(_report([edge], window=2), CI)["acted"] == 1
    assert _row(_report([past], window=2), CI)["acted"] == 0


def test_a_glob_path_with_brackets_under_home_is_read_as_the_literal_file(tmp_path, monkeypatch):
    from dokimi.commands.context import _read_sessions

    monkeypatch.setenv("HOME", str(tmp_path))
    logs = tmp_path / "logs"
    logs.mkdir()
    literal = _write(logs / "session[1].jsonl", [_prompt("one")])
    _write(logs / "session1.jsonl", [_prompt("two")])
    loaded, failed = _read_sessions(["~/logs/session[1].jsonl"])
    assert not failed and [s.name for s in loaded] == [str(literal)]


def test_a_home_relative_literal_path_is_read_when_only_that_file_exists(tmp_path, monkeypatch):
    from dokimi.commands.context import _read_sessions

    monkeypatch.setenv("HOME", str(tmp_path))
    logs = tmp_path / "logs"
    logs.mkdir()
    literal = _write(logs / "session[1].jsonl", [_prompt("one")])
    loaded, failed = _read_sessions(["~/logs/session[1].jsonl"])
    assert not failed and [s.name for s in loaded] == [str(literal)]


# --- round 8: heredoc delimiters, comments, compound printing, quoting, functions, redirections, windows ----

@pytest.mark.parametrize(("command", "acted"), [
    ("cat <<EOF-TEXT\nnotes\nEOF-TEXT\nmake ci-local", 1),       # an unquoted delimiter with punctuation ends its body
    ("cat <<'EOF-TEXT'\nmake ci-local\nEOF-TEXT", 0),            # a quoted one: the body is data, not a run
    ('cat <<"EOF.md"\nmake ci-local\nEOF.md', 0),
    ("cat <<-EOF\n\tbody\n\tEOF\nmake ci-local", 1),             # <<- closes on a tab-indented WORD
    ("cat <<EOF\nbody\n  EOF\nmake ci-local", 0),               # plain << closes only on a WORD alone: never closed here
    ("echo $((1<<2))\nmake ci-local", 1),                       # an arithmetic shift opens no heredoc
    ("# <<EOF\nmake ci-local", 1),                              # a comment that names a heredoc opens none
    ("true # <<EOF\nmake ci-local", 1),
    ("true;# ignored; make ci-local", 0),                       # a comment starts after a control operator too
])
def test_heredoc_delimiters_comments_and_arithmetic_are_read_as_the_shell_reads_them(command, acted):
    session = _session(_prompt("push the branch now"), _tool("Bash", command=command))
    assert _row(_report([session]), CI)["acted"] == acted


@pytest.mark.parametrize(("command", "acted"), [
    ("if true; then echo changelog.d/x.md; fi", 0),
    ("for i in 1; do echo changelog.d/x.md; done", 0),
    ("if false; then :; else printf 'changelog.d/x.md\\n'; fi", 0),
    ("if true; then cat changelog.d/x.md; fi", 1),              # a read under compound syntax is still an action
])
def test_a_printed_path_inside_compound_syntax_is_not_touched(command, acted):
    rule = "- Keep `changelog.d/` current.\n"
    session = _session(_prompt("keep the changelog current"), _tool("Bash", command=command))
    assert _row(_report([session], rule), "Keep")["acted"] == acted


def test_an_input_redirection_of_a_printing_command_is_a_read_of_its_file():
    rule = "- Update `docs/guide.md` when behaviour changes.\n"
    session = _session(_prompt("update behaviour changes"), _tool("Bash", command="printf 'done\\n' < docs/guide.md"))
    assert _row(_report([session], rule), "Update")["acted"] == 1


@pytest.mark.parametrize(("key", "acted"), [("docs/oreilly.md", 0), ("docs/o'reilly.md", 1)])
def test_a_quoted_file_name_keeps_its_literal_apostrophe(key, acted):
    rule = f"- Update `{key}` when behaviour changes.\n"
    session = _session(_prompt("update behaviour changes"), _tool("Bash", command='cat "docs/o\'reilly.md"'))
    assert _row(_report([session], rule), "Update")["acted"] == acted


@pytest.mark.parametrize("command", ["check_ci() { make ci-local; }", "check_ci() {\n  make ci-local\n}"])
def test_a_function_body_that_is_only_defined_is_not_an_action(command):
    session = _session(_prompt("push the branch now"), _tool("Bash", command=command))
    assert _row(_report([session]), CI)["acted"] == 0


def test_a_path_in_an_uncalled_function_body_is_not_touched():
    rule = "- Keep `changelog.d/` current.\n"
    session = _session(_prompt("keep the changelog current"),
                       _tool("Bash", command="write_note() { cat changelog.d/x.md; }"))
    assert _row(_report([session], rule), "Keep")["acted"] == 0


@pytest.mark.parametrize(("corrected_at", "corrected"), [(3, 1), (4, 0)])  # window is 3: the edge and one more
def test_a_correction_exactly_window_events_after_the_trigger_counts_and_one_more_does_not(corrected_at, corrected):
    events = [_prompt("push the branch now")]
    events += [_tool("Read", file_path=f"/w/{i}.py") for i in range(corrected_at - 1)]
    events.append(_prompt("no, do not push the branch yet"))
    row = _row(_report([_session(*events)], window=3), CI)
    assert row["corrected"] == corrected


def test_two_triggers_window_apart_are_one_episode_and_window_plus_one_apart_are_two():
    near = _session(_prompt("push the branch now"), _tool("Read", file_path="/w/a.py"),
                    _tool("Read", file_path="/w/b.py"), _prompt("push the branch again"))
    far = _session(_prompt("push the branch now"), _tool("Read", file_path="/w/a.py"),
                   _tool("Read", file_path="/w/b.py"), _tool("Read", file_path="/w/c.py"),
                   _prompt("push the branch again"))
    assert _row(_report([near], window=3), CI)["episodes"] == 1
    assert _row(_report([far], window=3), CI)["episodes"] == 2


def test_a_home_glob_reads_every_match(tmp_path, monkeypatch):
    from dokimi.commands.context import _read_sessions

    monkeypatch.setenv("HOME", str(tmp_path))
    logs = tmp_path / "logs"
    logs.mkdir()
    first = _write(logs / "a.jsonl", [_prompt("one")])
    second = _write(logs / "b.jsonl", [_prompt("two")])
    loaded, failed = _read_sessions(["~/logs/*.jsonl"])
    assert not failed and sorted(s.name for s in loaded) == sorted([str(first), str(second)])
