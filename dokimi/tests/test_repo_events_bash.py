# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Claude Code ``Bash`` calls that print files give ``read`` events, as Codex and Copilot shell commands do."""

from __future__ import annotations

import json

import pytest

from dokimi.repos.cli_traces import shell_reads_moved, shell_walk
from dokimi.repos.events import convert
from dokimi.repos.process_metrics import process_metrics
from dokimi.repos.useful import first_useful
from dokimi.repos.useful_spec import parse_useful

WS = "/ws"


def _call(tool_id: str, command: str, agent: str | None = None, name: str = "Bash") -> dict:
    rec = {"type": "assistant", "timestamp": "2026-01-01T00:00:05Z", "message": {"content": [
        {"type": "tool_use", "id": tool_id, "name": name, "input": {"command": command}}]}}
    return {**rec, "parent_tool_use_id": agent} if agent else rec


def _result(tool_id: str, text: str = "ok", is_error: bool = False) -> dict:
    return {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": tool_id, "is_error": is_error, "content": text}]}}


def _events(*lines: dict, cwd: str = "") -> list[dict]:
    first = {"type": "system", "subtype": "init", "timestamp": "2026-01-01T00:00:00Z"}
    return convert("claude-stream-json", "\n".join(json.dumps(x) for x in (first, *lines)), WS, cwd).events


def _reads(events: list[dict]) -> list[str]:
    return [e["path"] for e in events if e["kind"] == "read"]


def test_a_cd_then_cat_in_one_call_reads_the_file_in_that_directory():
    events = _events(_call("a", "cd benchmarks; cat AGENTS.md"), _result("a", "# rules"))
    assert events == [
        {"t": 5.0, "kind": "read", "path": "benchmarks/AGENTS.md", "rc": 0},
        {"t": 5.0, "kind": "command", "text": "cd benchmarks; cat AGENTS.md", "rc": 0, "output": "# rules",
         "output_truncated": False},
    ]


def test_an_absolute_path_inside_the_workspace_is_made_relative():
    assert _reads(_events(_call("a", "head -n 40 /ws/sub/AGENTS.md"), _result("a"))) == ["sub/AGENTS.md"]


def test_a_failed_cat_of_one_file_is_no_read():
    events = _events(_call("a", "cat AGENTS.md"), _result("a", "cat: AGENTS.md: No such file or directory", True))
    assert events == [{"t": 5.0, "kind": "command", "text": "cat AGENTS.md", "rc": 1,
                       "output": "cat: AGENTS.md: No such file or directory", "output_truncated": False}]


@pytest.mark.parametrize("command", ["! cat AGENTS.md", "if ! cat AGENTS.md; then echo none; fi"])
@pytest.mark.parametrize("is_error", [False, True])
def test_a_negated_read_is_no_read(command, is_error):
    # ``! cat`` of a missing file exits 0, of a present one 1: its status says nothing of the file
    assert _reads(_events(_call("a", command), _result("a", "", is_error))) == []


@pytest.mark.parametrize("command", ["cd sub && cat AGENTS.md", "cd sub; cat AGENTS.md", "cd a && cd b && cat AGENTS.md",
                                     "cd $(git rev-parse --show-toplevel) && cat /ws/AGENTS.md",
                                     "cd /ws/sub || cat /ws/AGENTS.md", "cd sub & cat AGENTS.md",  # the exit is the cat's
                                     "time cd sub && cat AGENTS.md", "command cd sub && cat AGENTS.md",
                                     "builtin cd sub && cat AGENTS.md", "X=1 cd sub && cat AGENTS.md",
                                     '"cd" sub && cat AGENTS.md', r"\cd sub && cat AGENTS.md",
                                     "builtin 'cd' sub && cat AGENTS.md",  # a quoted or escaped name is the builtin
                                     "cd \\\nsub && cat AGENTS.md", "cd sub \\\n&& cat AGENTS.md"])  # continued lines
def test_a_failed_cd_then_cat_of_one_file_is_no_read(command):
    events = _events(_call("a", command), _result("a", "cat: AGENTS.md: No such file or directory", True))
    assert [(e["kind"], e["rc"]) for e in events] == [("command", 1)]


@pytest.mark.parametrize("command, path", [("{ cat AGENTS.md; }", "AGENTS.md"),
                                           ("cd sub && { cat AGENTS.md; }", "sub/AGENTS.md"),
                                           ("{ { cat AGENTS.md; }; }", "AGENTS.md"),
                                           ("{ cd sub; cat AGENTS.md; }", "sub/AGENTS.md")])
def test_a_brace_group_holding_one_read_is_that_read(command, path):
    # failed, it read nothing; succeeded, it read its file
    events = _events(_call("a", command), _result("a", "cat: AGENTS.md: No such file or directory", True))
    assert [(e["kind"], e["rc"]) for e in events] == [("command", 1)]
    assert _reads(_events(_call("a", command), _result("a"))) == [path]


def test_a_failed_brace_group_with_two_commands_keeps_the_read():
    events = _events(_call("a", "{ make; cat AGENTS.md; }"), _result("a", "error", True))
    assert [(e["kind"], e["rc"]) for e in events] == [("read", 0), ("command", 1)]


@pytest.mark.parametrize("command", ["cat AGENTS.md && cd sub", "make && cat AGENTS.md", "(cd sub) && cat AGENTS.md",
                                     "env cd sub && cat AGENTS.md",  # ``env cd`` runs no builtin: some program failed
                                     "X=1 time cd sub && cat AGENTS.md", r"\time cd sub && cat AGENTS.md"])  # /usr/bin/time
def test_a_failed_list_whose_other_part_is_not_a_cd_keeps_the_read(command):
    # the failure may be the other part's: the file may have been shown
    events = _events(_call("a", command), _result("a", "error", True))
    assert [(e["kind"], e["rc"]) for e in events] == [("read", 0), ("command", 1)]


def test_a_failed_cat_of_two_files_keeps_both_reads():
    events = _events(_call("a", "cat AGENTS.md missing.md"), _result("a", "# rules\ncat: missing.md: No such file", True))
    assert [(e["path"], e["rc"]) for e in events if e["kind"] == "read"] == [("AGENTS.md", 0), ("missing.md", 0)]


@pytest.mark.parametrize("command", ["cd /ws/sub; cd -; cat missing.md /ws/AGENTS.md",
                                     "cd -; head -n 5 missing.md /ws/AGENTS.md"])
def test_a_failed_read_of_two_files_keeps_the_one_known_after_a_lost_directory(command):
    # the relative operand is at a directory not known, so no read of it: cat still named two files
    events = _events(_call("a", command), _result("a", "# rules\ncat: missing.md: No such file", True))
    assert [(e["path"], e["rc"]) for e in events if e["kind"] == "read"] == [("AGENTS.md", 0)]


@pytest.mark.parametrize("refusal", [
    lambda: _result("a", "PreToolUse:Bash hook error: shell background", True),
    lambda: {"type": "result", "result": "ok", "permission_denials": [{"tool_name": "Bash", "tool_use_id": "a"}]},
])
def test_a_refused_bash_call_marks_its_reads_denied(refusal):
    events = _events(_call("a", "cat AGENTS.md"), refusal())
    assert [e.get("denied") for e in events] == [True, True]
    assert process_metrics(events, lambda p: p.endswith("AGENTS.md"))["guidance_read"] == []


def _denials(*tool_ids: str) -> dict:
    return {"type": "result", "result": "ok",
            "permission_denials": [{"tool_name": "Bash", "tool_use_id": i} for i in tool_ids]}


def test_a_permission_error_then_its_denial_keeps_the_read_denied_in_place():
    events = _events(_call("a", "cat AGENTS.md"), _result("a", "Permission to use Bash has been denied", True),
                     _call("b", "cat README.md"), _result("b"), _denials("a"))
    assert [(e["kind"], e.get("path"), e.get("denied")) for e in events] == [
        ("read", "AGENTS.md", True), ("command", None, True), ("read", "README.md", None), ("command", None, None)]
    assert process_metrics(events, lambda p: p.endswith("AGENTS.md"))["tool_calls"] == 4


@pytest.mark.parametrize("refusal, reads", [
    ("hook", ["AGENTS.md"]), ("permissions", ["AGENTS.md"]), (None, []),  # None: the cd ran
])
def test_a_refused_cd_moves_no_shell(refusal, reads):
    text = {"hook": "PreToolUse:Bash hook error: no cd", "permissions": "Permission to use Bash has been denied"}
    events = _events(_call("a", "cd sub"), _result("a", text.get(refusal, ""), bool(refusal)),
                     _call("b", "cat AGENTS.md"), _result("b"), _denials("a") if refusal == "permissions" else {})
    assert _reads(events) == reads
    assert [e["kind"] for e in events] == ["command", *(["read"] if reads else []), "command"]


@pytest.mark.parametrize("refusal", [
    lambda: _result("b", "PreToolUse:Bash hook error: no", True),
    lambda: _denials("b"),
])
def test_a_refused_relative_read_after_a_cd_stays_denied(refusal):
    events = _events(_call("a", "cd sub"), _result("a"), _call("b", "cat AGENTS.md /ws/README.md"), refusal())
    # the relative read's file is not known after the cd: it has no path; the absolute one keeps its own
    assert [(e["kind"], e.get("path"), e.get("denied")) for e in events] == [
        ("command", None, None), ("read", None, True), ("read", "README.md", True), ("command", None, True)]
    assert "path" not in events[1]
    got = process_metrics(events, lambda p: p.endswith("AGENTS.md"))
    assert got["tool_calls"] == 4 and got["guidance_read"] == []


def test_a_read_with_no_path_is_no_read_to_any_consumer():
    from dokimi.context.consistency import run_from_events
    from dokimi.repos.arms import skill_usage
    from dokimi.repos.transcript import Transcript
    from dokimi.repos.uptake import arm_uptake
    events = _events(_call("a", "cd sub"), _result("a"), _call("b", "cat AGENTS.md"), _denials("b"))
    assert events[1] == {"t": 5.0, "kind": "read", "denied": True}
    assert Transcript(events=events).reads() == set()
    assert skill_usage(events, ["lint"], None)["skills_used"] == []
    assert arm_uptake(events, ["AGENTS.md"], [], WS)["reads"] == 0
    assert [(e.action, e.input) for e in run_from_events(events).events if e.tool == "Read"] == [("Read", {})]


@pytest.mark.parametrize("command", ["cat AGENTS.md | head", "grep -n rule AGENTS.md", "cat AGENTS.md > copy.md"])
def test_a_pipe_a_search_or_a_redirect_is_no_read(command):
    assert _reads(_events(_call("a", command), _result("a"))) == []


def test_a_powershell_command_is_not_parsed():
    assert _reads(_events(_call("a", "cat AGENTS.md", name="PowerShell"), _result("a"))) == []


def test_after_a_cd_in_an_earlier_call_only_absolute_reads_are_kept():
    events = _events(
        _call("a", "cat AGENTS.md"), _result("a"),
        _call("b", "cd sub && make"), _result("b"),
        _call("c", "cat AGENTS.md"), _result("c"),  # in sub, or wherever the cd left the shell: not known
        _call("d", "cat /ws/sub/AGENTS.md"), _result("d"),
        _call("e", "cd /ws/docs && cat README.md"), _result("e"))  # a cd to an absolute path is followed
    assert _reads(events) == ["AGENTS.md", "sub/AGENTS.md", "docs/README.md"]


@pytest.mark.parametrize("first", ["(cd sub && make)", "make -C sub", "env -C sub make"])
def test_a_call_that_moves_no_shell_keeps_the_next_relative_read(first):
    events = _events(_call("a", first), _result("a"), _call("b", "cat AGENTS.md"), _result("b"))
    assert _reads(events) == ["AGENTS.md"]


@pytest.mark.parametrize("command, moves", [
    ("cd sub &", False), ("{ cd sub; } | cat", False), ("{ cd sub; } &", False), ("cd sub && make &", False),
    ("(cd sub)", False), ("cd sub", True), ("cd sub && make", True), ('cd "$X"', True), ("pushd sub", True),
    ("popd", True), ("cd sub & cd docs", True), ("{ cd sub; } & cd docs", True),
    ("cd sub && { echo a; echo b; } &", False), ("cd sub && { cd x; cat a; } &", False),
    ("cd sub && { echo a; }", True),
    ('cd "$X" &', False), ('cd "$X" | cat', False), ("cd sub | cat", False), ("echo | cd sub", False),
    ('cd "$X" && make', True), ("cd $(pwd)", True), (r"cd a\|b", True),
    ("cd $(pwd) | cat", False), ("cd $(pwd) &", False), ("cd `pwd` | cat", False), ("cd $(echo a | tr a b)", True),
    ('eval "cd sub"', True), ('eval "$CMD"', True), ("source env.sh", True), (". env.sh", True),  # may run a cd
    ("time cd sub", True), ("command cd sub", True), ('eval "cd sub" &', False), ("env source env.sh", False),
    ("{ cd $(pwd); } | cat", False), ("{ cd $(pwd); } &", False), ("{ cd `pwd`; } | cat", False),
    ("{ source $(x); } &", False), ("{ { cd $(pwd); }; } | cat", False), ("(true; cd sub)", False),
    ("{ cd $(pwd); }", True), ("{ { cd sub; }; }", True),
    ('"source" "$ENV"', True), ("'cd' \"$D\"", True), (r'\eval "$X"', True), ('"." env.sh', True),  # quoted names
    ('"cd" "$D" &', False), ('"source" "$E" | cat', False), ('"env" cd "$D"', False), ("'\\cd' \"$D\"", False),
    ('X="a b" cd "$D"', True), ("time -p cd sub", True), ("command -p cd sub", True), ("builtin -- cd sub", True),
    ("time -p cd sub &", False), ('X="a b" cd "$D" | cat', False), ("time -p make", False),  # not followed: may move
    ('time -p cd "$D', True), ('X="a b c" cd "$D', True),  # shlex cannot split it: the command word, else any word
])
def test_only_a_directory_builtin_in_the_calls_own_shell_moves_it(command, moves):
    assert shell_reads_moved(command)[1] is moves


@pytest.mark.parametrize("first", ["cd sub &", "{ cd sub; } | cat", "{ cd sub; } &", "cd sub && make &",
                                   "cd sub && { echo a; echo b; } &", "cd sub && { cd x; cat a; } &",
                                   'cd "$X" &', 'cd "$X" | cat', "cd sub | cat", "echo | cd sub",
                                   "cd $(pwd) | cat", "cd $(pwd) &", "{ cd $(pwd); } | cat", "{ cd $(pwd); } &",
                                   "source env.sh &", 'eval "$CMD" | cat', ". env.sh &", "pushd sub &"])
def test_a_cd_in_a_job_or_a_pipeline_keeps_the_next_calls_relative_read(first):
    events = _events(_call("a", first), _result("a"), _call("b", "cat AGENTS.md"), _result("b"))
    assert _reads(events) == ["AGENTS.md"]


@pytest.mark.parametrize("first", ["pushd sub", 'eval "cd sub"', "source env.sh", ". env.sh",
                                   "time cd sub", '"source" "$ENV"', "'cd' \"$D\"", r'\eval "$X"', '"." env.sh',
                                   'c"d" "$D"', '"pushd" "$D"', 'builtin "cd" "$D"', '"cd" $(pwd)'])
def test_a_pushd_eval_or_source_in_an_earlier_call_also_loses_the_directory(first):
    events = _events(_call("a", first), _result("a"), _call("b", "cat AGENTS.md"), _result("b"))
    assert _reads(events) == []


@pytest.mark.parametrize("command, reads", [
    ("time cd sub && cat AGENTS.md", ["sub/AGENTS.md"]), ("command cd sub && cat AGENTS.md", ["sub/AGENTS.md"]),
    ('eval "cd sub" && cat AGENTS.md', []), ("source env.sh && cat AGENTS.md", []), (". env.sh; cat AGENTS.md", []),
])
def test_time_runs_a_cd_and_eval_or_source_loses_the_directory_within_a_call(command, reads):
    assert _reads(_events(_call("a", command), _result("a"))) == reads


@pytest.mark.parametrize("command", ["source env.sh & cat AGENTS.md", 'eval "$CMD" | cat; cat AGENTS.md',
                                     'cd "$X" & cat AGENTS.md', "source env.sh & false || cat AGENTS.md",
                                     "source env.sh | false || cat AGENTS.md"])
def test_a_source_eval_or_computed_cd_in_a_job_or_a_pipeline_keeps_the_calls_later_read(command):
    events = _events(_call("a", command), _result("a"), _call("b", "cat README.md"), _result("b"))
    assert _reads(events) == ["AGENTS.md", "README.md"]


_UNFOLLOWED = ['X="a b" cd "$D"', "time -p cd sub", "command -p cd sub", "builtin -- cd sub"]


@pytest.mark.parametrize("first", _UNFOLLOWED)
def test_a_directory_builtin_the_parser_does_not_follow_moves_the_shell(first):
    # an assignment with a blank, or an option before the builtin: the shell may have moved, so no relative read
    assert _reads(_events(_call("a", f"{first} && cat AGENTS.md"), _result("a"))) == []
    events = _events(_call("a", first), _result("a"), _call("b", "cat AGENTS.md"), _result("b"))
    assert _reads(events) == []


@pytest.mark.parametrize("first", ['X="a b" cd sub', "source 'missing env/bin/activate'", "cd sub\\ "])
def test_a_failed_read_after_a_quoted_cd_or_activate_is_no_read(first):
    # ``_is_cd`` / ``_is_activate`` split as the walker does (shlex; at blanks when it cannot: ``cd sub\``)
    events = _events(_call("a", f"{first} && cat AGENTS.md"), _result("a", "error", True))
    assert [(e["kind"], e.get("path"), e["rc"]) for e in events] == [("command", None, 1)]


@pytest.mark.parametrize("first", ["time find . -name x", "time wc -l AGENTS.md .", "time git add .", "time make -C .",
                                   "command -v cd", "command -V cd", "builtin printf '%s' ."])
def test_a_builtins_name_as_an_argument_moves_no_shell(first):
    # only the command word, past ``time`` / ``command`` / ``builtin`` and their options, names the builtin
    assert shell_reads_moved(first)[1] is False
    assert _reads(_events(_call("a", f"{first}; cat AGENTS.md"), _result("a"))) == ["AGENTS.md"]
    assert _reads(_events(_call("a", first), _result("a"), _call("b", "cat AGENTS.md"), _result("b"))) == ["AGENTS.md"]


def test_a_builtins_name_after_a_quote_shlex_cannot_split_moves_no_shell():
    # the command word is told: grep (the splitter does not know ``$'...'``, so only the next call is checked)
    first = r"grep $'it\'s' ."
    assert shell_reads_moved(first)[1] is False
    assert _reads(_events(_call("a", first), _result("a"), _call("b", "cat AGENTS.md"), _result("b"))) == ["AGENTS.md"]


@pytest.mark.parametrize("first", ["X=1 time cd sub", r"\time cd sub", '"time" cd sub', "X=1 time -p cd sub"])
def test_a_time_that_is_not_the_keyword_runs_a_child_and_moves_no_shell(first):
    # after an assignment, or quoted or escaped, ``time`` is /usr/bin/time: the cd runs in a child process
    assert shell_reads_moved(f"{first}; cat a") == (["a"], False)
    assert _reads(_events(_call("a", first), _result("a"), _call("b", "cat AGENTS.md"), _result("b"))) == ["AGENTS.md"]


@pytest.mark.parametrize("command", ["case x in a) cd sub;; esac", "case x in a) :;; b) cd sub;; esac",
                                     "case $x in\n  a) echo\n     cd sub;;\nesac", "case x in a) source env.sh;; esac"])
def test_a_directory_builtin_in_a_case_arm_may_move_the_shell(command):
    # a case is not followed: the arm may have run
    assert shell_reads_moved(f"{command}; cat a") == ([], True)
    assert _reads(_events(_call("a", command), _result("a"), _call("b", "cat AGENTS.md"), _result("b"))) == []


def test_a_case_with_no_directory_builtin_keeps_the_next_read():
    assert shell_reads_moved("case x in a) echo a;; esac; cat a") == (["a"], False)


def test_assignments_after_the_time_keyword_still_run_the_cd_in_this_shell():
    # bash's ``time`` times the whole simple command ``X=1 cd sub``: the cd runs here
    assert shell_walk("time X=1 cd sub; cat AGENTS.md") == (["sub/AGENTS.md"], True, 0)
    assert _reads(_events(_call("a", "time X=1 cd sub"), _result("a"), _call("b", "cat AGENTS.md"), _result("b"))) == []


def test_a_nested_case_keeps_the_outer_case_open_past_the_inner_esac():
    # the cd is still in the outer case's arm: not followed, so the cat after the case is at an unknown directory
    command = "case $x in a) case $y in b) :;; esac; cd sub;; esac; cat AGENTS.md"
    assert shell_walk(command) == ([], True, 1)
    assert _reads(_events(_call("a", command), _result("a"))) == []


def test_a_brace_opened_on_a_case_part_is_tracked_so_the_outer_redirect_hides_the_group():
    assert shell_walk("{ cat AGENTS.md; { case $x in a) :;; esac; }; } > /dev/null") == ([], False, 0)
    assert shell_walk("{ cat AGENTS.md; { case $x in a) :;; esac; }; }") == (["AGENTS.md"], False, 0)


@pytest.mark.parametrize("first", ["> /tmp/cd.log cd sub", ">/tmp/cd.log cd sub", "</dev/null cd sub", "&>log cd sub",
                                   "X=1 > log cd sub", "command > log cd sub", "command -p > log cd sub",
                                   "> log source env.sh"])
def test_a_leading_redirection_is_no_command_word_so_the_cd_moves_the_shell(first):
    # a redirect is not followed (as ``cd sub > log``): the shell moved, to a directory not known here
    assert shell_walk(f"{first}; cat AGENTS.md") == ([], True, 1)
    assert _reads(_events(_call("a", first), _result("a"), _call("b", "cat AGENTS.md"), _result("b"))) == []


def test_a_leading_redirection_of_a_cd_in_a_job_or_a_pipeline_moves_no_shell():
    assert shell_walk("> log cd sub & cat AGENTS.md") == (["AGENTS.md"], False, 0)
    assert shell_walk("> log cd sub | cat; cat AGENTS.md") == (["AGENTS.md"], False, 0)


@pytest.mark.parametrize("first", ["command > /tmp/cd.log -p cd sub", "time > log -p cd sub",
                                   "builtin > log -- cd sub", "time -p > log X=1 cd sub"])
def test_a_redirection_before_a_prefixs_options_still_finds_the_cd(first):
    # the prefix's options, redirections and (past ``time``) assignments come in any order before the command word
    assert shell_walk(f"{first}; cat AGENTS.md") == ([], True, 1)
    assert _reads(_events(_call("a", first), _result("a"), _call("b", "cat AGENTS.md"), _result("b"))) == []


def test_a_redirection_before_command_v_still_runs_nothing():
    assert shell_walk("command > log -v cd; cat AGENTS.md") == (["AGENTS.md"], False, 0)


def test_a_parents_cd_drops_a_subagents_later_relative_read():
    # a subagent may share the parent's shell, so its relative read after the parent's cd is not known
    events = _events(_call("s1", "cat AGENTS.md", agent="t1"), _result("s1"),
                     _call("a", "cd sub"), _result("a"),
                     _call("s2", "cat AGENTS.md", agent="t1"), _result("s2"),
                     _call("s3", "cat /ws/sub/AGENTS.md", agent="t1"), _result("s3"))
    assert [(e["path"], e.get("agent")) for e in events if e["kind"] == "read"] == [
        ("AGENTS.md", "t1"), ("sub/AGENTS.md", "t1")]


def test_a_subagents_cd_drops_the_parents_later_relative_read():
    events = _events(_call("p1", "cat AGENTS.md"), _result("p1"),
                     _call("s1", "cd sub", agent="t1"), _result("s1"),
                     _call("p2", "cat AGENTS.md"), _result("p2"))
    assert [(e["path"], e.get("agent")) for e in events if e["kind"] == "read"] == [("AGENTS.md", None)]


def test_a_subagent_bash_read_carries_the_agent():
    events = _events(_call("s1", "cat sub/AGENTS.md", agent="t1"), _result("s1"))
    assert [(e["kind"], e.get("agent")) for e in events] == [("read", "t1"), ("command", "t1")]


def test_the_shell_starts_in_the_directory_the_harness_started_in():
    assert _reads(_events(_call("a", "cat AGENTS.md"), _result("a"), cwd="sub")) == ["sub/AGENTS.md"]


def test_a_bash_call_with_no_result_keeps_its_read_without_an_rc():
    events = _events(_call("a", "cat AGENTS.md"))
    assert events[0] == {"t": 5.0, "kind": "read", "path": "AGENTS.md"}


def _process(*lines: dict) -> dict:
    first = {"type": "system", "subtype": "init", "timestamp": "2026-01-01T00:00:00Z"}
    t = convert("claude-stream-json", "\n".join(json.dumps(x) for x in (first, *lines)), WS)
    return process_metrics(t.events, lambda p: p.endswith("AGENTS.md"), unknown_reads=t.unknown_reads)


@pytest.mark.parametrize("lines, unknown, guidance", [
    ((_call("a", "source env.sh && pytest"), _result("a"), _call("b", "cat AGENTS.md"), _result("b")), 1, []),
    ((_call("a", "cd - && cat AGENTS.md README.md"), _result("a")), 2, []),  # within the call: cd - is not followed
    ((_call("a", "cd -; cat missing.md"), _result("a")), 1, []),
    ((_call("a", "cd -; cat missing.md"), _result("a", "No such file", True)), 0, []),  # one file, failed: not read
    ((_call("a", "cd sub"), _result("a"), _call("b", "cat AGENTS.md /ws/a.md"), _result("b", "x", True)), 1, []),
    ((_call("a", "cd sub"), _result("a"), _call("b", "cat AGENTS.md"), _result("b", "x", True)), 0, []),  # it failed
    ((_call("a", "cd sub"), _result("a"), _call("b", "cd -; cat AGENTS.md"), _denials("b")), 0, []),  # nothing ran
    ((_call("a", "cat AGENTS.md"), _result("a"), _call("b", "cd sub && make"), _result("b")), 0, ["AGENTS.md"]),
    ((_call("a", "{ source env.sh; cat AGENTS.md; } > /dev/null"), _result("a")), 0, []),  # its output is hidden
    ((_call("a", "{ source env.sh; cat AGENTS.md; } | head"), _result("a")), 0, []),
    ((_call("a", "{ source env.sh; cat AGENTS.md; }"), _result("a")), 1, []),
])
def test_a_read_at_a_directory_not_known_is_counted(lines, unknown, guidance):
    got = _process(*lines)
    assert (got["unknown_reads"], got["guidance_read"]) == (unknown, guidance)


def test_guidance_printed_through_bash_is_guidance_read():
    events = _events(_call("b", "cat AGENTS.md"), _result("b", "cat: AGENTS.md: No such file or directory", True),
                     _call("c", "cat README.md"), _result("c"),
                     _call("a", "cd benchmarks; cat AGENTS.md"), _result("a"))
    got = process_metrics(events, lambda p: p.endswith("AGENTS.md"))
    assert got["guidance_read"] == ["benchmarks/AGENTS.md"]
    assert got["commands"] == 3 and got["failed_commands"] == 1


def test_a_claude_bash_read_is_one_more_step_before_the_first_useful_command():
    def call(i: str, command: str, t: str) -> list[dict]:
        use = {"type": "tool_use", "id": i, "name": "Bash", "input": {"command": command}}
        res = {"type": "tool_result", "tool_use_id": i, "is_error": False, "content": "ok"}
        return [
            {"type": "assistant", "timestamp": t, "message": {"content": [use]}},
            {"type": "user", "timestamp": t, "message": {"content": [res]}},
        ]

    lines = call("a", "cat AGENTS.md", "2026-10-31T10:00:01.000Z")
    lines += call("b", "pytest tests/test_x.py", "2026-10-31T10:00:05.000Z")
    raw = "\n".join(json.dumps(x) for x in lines)
    events = convert("claude-stream-json", raw, "/ws").events
    assert [e["kind"] for e in events] == ["read", "command", "command"]
    assert first_useful(parse_useful({"command": r"pytest tests/test_x\.py"}), events)["steps"] == 3


@pytest.mark.parametrize("command", ["cd $(printf sub; true) | cat; cat AGENTS.md", "cd $(printf sub; true) & cat AGENTS.md",
                                     "cd `printf sub; true` | cat; cat AGENTS.md", "cd `printf sub; true` & cat AGENTS.md",
                                     "cd $(printf sub && true) | cat; cat AGENTS.md", "cd $(printf sub\ntrue) & cat AGENTS.md"])
def test_a_separator_inside_a_substitution_leaves_the_cd_piped_or_backgrounded(command):
    # the substitution's own ``;`` does not end the part: the ``|`` or ``&`` after it puts the cd in a subshell (#374)
    assert shell_walk(command) == (["AGENTS.md"], False, 0)


@pytest.mark.parametrize("command", ["cd $(printf sub; true); cat AGENTS.md", "cd `printf sub; true`; cat AGENTS.md"])
def test_a_cd_to_a_substitution_holding_a_separator_in_the_foreground_loses_the_directory(command):
    assert shell_walk(command) == ([], True, 1)


def test_a_subshell_list_records_no_reads_and_moves_nothing():
    assert shell_walk("( cd sub; cat a ); cat AGENTS.md") == (["AGENTS.md"], False, 0)


@pytest.mark.parametrize("command", ["source .venv/bin/activate; cat AGENTS.md", ". .venv/bin/activate && cat AGENTS.md",
                                     'source "/opt/venv/bin/activate"; cat AGENTS.md', "source bin/activate\ncat AGENTS.md",
                                     "builtin source venv/bin/activate; cat AGENTS.md"])
def test_sourcing_a_virtualenvs_activate_script_keeps_the_directory(command):
    # venv, virtualenv and uv activate scripts set variables and PATH and run no cd (#374)
    assert shell_walk(command) == (["AGENTS.md"], False, 0)


@pytest.mark.parametrize("command", ["source env.sh; cat AGENTS.md", "source x/bin/activate extra; cat AGENTS.md",
                                     "source $V/bin/activate; cat AGENTS.md", "source bin/activate.fish; cat AGENTS.md",
                                     "source activate; cat AGENTS.md", "eval bin/activate; cat AGENTS.md"])
def test_sourcing_anything_but_an_activate_script_alone_still_loses_the_directory(command):
    assert shell_walk(command) == ([], True, 1)


def test_an_activate_in_an_earlier_call_keeps_the_next_calls_relative_read():
    got = _process(_call("a", "source .venv/bin/activate && pytest"), _result("a"), _call("b", "cat AGENTS.md"), _result("b"))
    assert (got["unknown_reads"], got["guidance_read"]) == (0, ["AGENTS.md"])


@pytest.mark.parametrize("command", ["time cat AGENTS.md", "time -p cat AGENTS.md", "time X=1 cat AGENTS.md",
                                     "cd sub && time cat AGENTS.md", "source .venv/bin/activate && cat AGENTS.md"])
def test_a_failed_timed_or_activated_read_of_one_file_is_no_read(command):
    events = _events(_call("a", command), _result("a", "No such file", True))
    assert [(e["kind"], e["rc"]) for e in events] == [("command", 1)]


@pytest.mark.parametrize("command", [
    "f() { cd sub; }; f; cat AGENTS.md", "function f { cd sub; }; f; cat AGENTS.md",
    "function f() {\n  pushd sub\n}\nf\ncat AGENTS.md", "f() { cd sub; }; g() { f; }; g; cat AGENTS.md",
    "g() { f; }; f() { cd sub; }; g; cat AGENTS.md", "f() { cd sub; }; f > log; cat AGENTS.md",
    "f() { cd sub; }; time f; cat AGENTS.md", "f() { case $1 in a) cd sub;; esac; }; f a; cat AGENTS.md",
    "f() { cd sub; }; case x in x) f;; esac; cat AGENTS.md", 'f() { "cd" sub; }; X=1 f; cat AGENTS.md',
])
def test_a_call_of_a_function_that_names_a_directory_builtin_loses_the_directory(command):
    # the call runs the body's cd in this shell, as an eval may (#374)
    assert shell_walk(command) == ([], True, 1)


@pytest.mark.parametrize("command", ["g() { echo hi; }; g; cat AGENTS.md", "f() { cd sub; }; f | cat; cat AGENTS.md",
                                     "f() { cd sub; }; f & cat AGENTS.md", "f() { cd sub; }; env f; cat AGENTS.md",
                                     "f() { cd sub; }; command f; cat AGENTS.md", "f() { cd sub; }; cat AGENTS.md",
                                     "g() { echo hi; }; f() { cd sub; }; g; cat AGENTS.md"])
def test_a_function_naming_no_directory_builtin_or_called_in_a_subshell_moves_nothing(command):
    assert shell_walk(command) == (["AGENTS.md"], False, 0)


def test_a_function_an_earlier_call_defined_moves_the_shell_when_a_later_call_runs_it():
    # Claude Code keeps the shell's functions between Bash calls, as it keeps its directory
    got = _process(_call("a", "f() { cd sub; }"), _result("a"), _call("b", "f; cat AGENTS.md"), _result("b"),
                   _call("c", "cat README.md"), _result("c"))
    assert (got["unknown_reads"], got["guidance_read"]) == (2, [])
    events = _events(_call("a", "f() { cd sub; }"), _result("a"), _call("b", "f"), _result("b"),
                     _call("c", "cat AGENTS.md"), _result("c"))
    assert _reads(events) == []


def test_a_function_an_earlier_call_defined_with_no_cd_keeps_the_later_read():
    events = _events(_call("a", "g() { echo hi; }"), _result("a"), _call("b", "g; cat AGENTS.md"), _result("b"))
    assert _reads(events) == ["AGENTS.md"]


_LATER_BRACE = ["f()\n{\n  cd sub\n}\n", "function f\n{\n  cd sub\n}\n", "f()\n\n# it's the body\n{\n  cd sub\n}\n",
                "f() # c\n{ cd sub; }\n", "function f()\n{\n  pushd sub\n}\n"]


@pytest.mark.parametrize("definition", _LATER_BRACE)
def test_a_function_whose_brace_is_on_a_later_line_is_only_defined(definition):
    # bash only defines f: its cd does not run, so the cat is at the start directory
    assert shell_walk(definition + "cat AGENTS.md") == (["AGENTS.md"], False, 0)


@pytest.mark.parametrize("definition", _LATER_BRACE)
def test_a_call_of_a_function_whose_brace_is_on_a_later_line_loses_the_directory(definition):
    assert shell_walk(definition + "f\ncat AGENTS.md") == ([], True, 1)


@pytest.mark.parametrize("command", [
    "cat() { echo hi; }; cat AGENTS.md", r"cat() { echo hi; }; \cat AGENTS.md", 'cat() { echo hi; }; "cat" AGENTS.md',
    "function head { echo hi; }; head AGENTS.md", "cat()\n{\n  echo hi\n}\ncat AGENTS.md",
    "cat() { echo hi; }; X=1 cat AGENTS.md", "cat() { echo hi; }; time cat AGENTS.md",
    "cat() { echo hi; }; { cat AGENTS.md; }", "cat AGENTS.md; cat() { echo hi; }",
])
def test_a_call_of_a_function_named_like_a_printer_is_no_read(command):
    # bash looks a function up after quote removal (``\cat`` too): it prints hi, not the file
    assert shell_walk(command) == ([], False, 0)


@pytest.mark.parametrize("command", [
    "cat() { echo hi; }; command cat AGENTS.md", "cat() { echo hi; }; env cat AGENTS.md",
    "cat() { echo hi; }; /bin/cat AGENTS.md", "command cat AGENTS.md", "time command cat AGENTS.md",
])
def test_command_env_or_a_path_runs_the_printer_not_the_function(command):
    assert shell_walk(command) == (["AGENTS.md"], False, 0)


@pytest.mark.parametrize("command", ["env command cat AGENTS.md; true", "/usr/bin/env command cat AGENTS.md; true",
                                     "env builtin cat AGENTS.md; true", "env env command cat AGENTS.md"])
def test_command_or_builtin_after_env_is_a_programs_name_so_no_read(command):
    # env runs a PATH program, never a builtin: ``env: 'command': No such file or directory``, cat never runs
    assert shell_walk(command) == ([], False, 0)


@pytest.mark.parametrize("command", ["env time cat AGENTS.md", "command env cat AGENTS.md", "/usr/bin/env cat AGENTS.md",
                                     "X=1 env cat AGENTS.md"])
def test_env_before_a_real_program_or_after_command_still_reads(command):
    assert shell_walk(command) == (["AGENTS.md"], False, 0)


@pytest.mark.parametrize("first", ["env command -p cd sub", "time env command -p cd sub", "env command cd sub",
                                   "env builtin cd sub"])
def test_a_directory_builtin_behind_command_after_env_moves_no_shell(first):
    # the child runs the program ``command`` or ``builtin``: no cd runs in this shell
    assert shell_walk(f"{first}; cat AGENTS.md") == (["AGENTS.md"], False, 0)


@pytest.mark.parametrize("command", ["command -v cat AGENTS.md", "command -p cat AGENTS.md",
                                     "command X=1 cat AGENTS.md", "command builtin cat AGENTS.md"])
def test_command_before_an_option_an_assignment_or_a_prefix_reads_nothing(command):
    assert shell_walk(command) == ([], False, 0)


@pytest.mark.parametrize("command", ["command builtin cd sub; cat AGENTS.md", "command > log builtin cd sub; cat AGENTS.md"])
def test_command_before_another_prefix_still_runs_the_directory_builtin(command):
    # ``command`` is not taken as running a program there: the builtin's cd may move this shell
    assert shell_walk(command) == ([], True, 1)


@pytest.mark.parametrize("command", [
    "cat() { cd sub; }; cat x; head AGENTS.md", "cd() { echo hi; }; cd sub; cat AGENTS.md",
    'cd() { builtin cd "$@"; }; cd sub; cat AGENTS.md', r"cd() { echo hi; }; \cd sub; cat AGENTS.md",
])
def test_a_function_named_like_a_directory_builtin_or_naming_one_loses_the_directory(command):
    # the function, not the builtin, runs; a definition later in the script counts too, so its cd may not be the one
    assert shell_walk(command) == ([], True, 1)


def test_a_function_an_earlier_call_defined_shadows_a_printer_in_a_later_call():
    events = _events(_call("a", "cat() { echo hi; }"), _result("a"), _call("b", "cat AGENTS.md"), _result("b"),
                     _call("c", "command cat README.md"), _result("c"))
    assert _reads(events) == ["README.md"]


_REFUSAL_TEXT = {"hook": "PreToolUse:Bash hook error: no", "permissions": "Permission to use Bash has been denied"}


def _refused_first(refusal: str | None, first: str, then: str) -> tuple[dict, ...]:
    """``first`` refused by a hook's result or the final record's ``permission_denials`` (None: it ran), then ``then``."""
    return (_call("a", first), _result("a", _REFUSAL_TEXT.get(refusal, ""), bool(refusal)), _call("b", then),
            _result("b"), _denials("a") if refusal == "permissions" else {})


@pytest.mark.parametrize("refusal, later", [("hook", [("read", "AGENTS.md", None)]),
                                            ("permissions", [("read", "AGENTS.md", None)]), (None, [])])
def test_a_refused_call_defines_no_function_that_shadows_a_later_printer(refusal, later):
    events = _events(*_refused_first(refusal, "cat() { echo hi; }", "cat AGENTS.md"))
    assert [(e["kind"], e.get("path"), e.get("denied")) for e in events] == [
        ("command", None, True if refusal else None), *later, ("command", None, None)]


@pytest.mark.parametrize("refusal, got", [("hook", (0, ["AGENTS.md"])), ("permissions", (0, ["AGENTS.md"])),
                                          (None, (1, []))])  # None: f ran in this shell, its cd may have moved it
def test_a_refused_call_defines_no_function_that_moves_the_shell(refusal, got):
    metrics = _process(*_refused_first(refusal, "f() { cd sub; }", "f; cat AGENTS.md"))
    assert (metrics["unknown_reads"], metrics["guidance_read"]) == got


def test_a_refused_call_keeps_the_functions_an_earlier_call_defined():
    events = _events(_call("a", "cat() { echo hi; }"), _result("a"), _call("b", "g() { echo; }"),
                     _result("b", "PreToolUse:Bash hook error: no", True), _call("c", "cat AGENTS.md"), _result("c"))
    assert _reads(events) == []


@pytest.mark.parametrize("command", [
    "case $x in a) echo case;; esac; cat AGENTS.md", "case $x in a|case) echo;; esac; cat AGENTS.md",
    'case $x in a) echo "case";; esac; cat AGENTS.md', r"case $x in a) echo \case;; esac; cat AGENTS.md",
    "case $x in case) echo;; esac; cat AGENTS.md", "case $x in a) echo;;\n  case | b) echo;; esac; cat AGENTS.md",
    'case $x in a) echo "x | case y";; esac; cat AGENTS.md',
])
def test_a_case_word_that_is_no_command_word_opens_no_case(command):
    # an argument, a pattern or a quoted ``case`` is no keyword: the esac ends the case, and the cat runs at the root
    assert shell_walk(command) == (["AGENTS.md"], False, 0)


@pytest.mark.parametrize("command", [
    "case $x in a) case $y in b) cd sub;; esac;; esac; cat AGENTS.md",
    "case $x in a) (case $y in b) echo;; esac); cd sub;; esac; cat AGENTS.md",
    "case $x in a) echo | case $y in b) echo;; esac; cd sub;; esac; cat AGENTS.md",
    'case $x in a) echo; "esac"; cd sub;; esac; cat AGENTS.md',
])
def test_a_nested_case_still_nests_and_a_quoted_esac_closes_nothing(command):
    # the cd is in an arm of the outer case, which may have run: the cat is at a directory not known
    assert shell_walk(command) == ([], True, 1)


@pytest.mark.parametrize("command", ["time cat AGENTS.md", "time -p cat AGENTS.md", "time X=1 cat AGENTS.md",
                                     "time -p X=1 cat AGENTS.md", "X=1 time cat AGENTS.md", r"\time cat AGENTS.md",
                                     "/usr/bin/time cat AGENTS.md"])
def test_a_timed_printing_command_reads_its_file(command):
    # the keyword reports on stderr, and /usr/bin/time passes the child's stdout through (#374)
    assert shell_walk(command) == (["AGENTS.md"], False, 0)


@pytest.mark.parametrize("command", [r"\time -o log cat AGENTS.md", "X=1 time -p cat AGENTS.md",
                                     r"\time X=1 cat AGENTS.md"])
def test_usr_bin_time_with_an_option_or_an_assignment_after_it_reads_nothing(command):
    # an option is not parsed (``-o log`` takes a value), and /usr/bin/time runs ``X=1`` as a program
    assert shell_walk(command) == ([], False, 0)


@pytest.mark.parametrize("command", ["cd() { echo hi; }; command cd sub; cat AGENTS.md",
                                     "cd() { echo hi; }; builtin cd sub; cat AGENTS.md",
                                     "cd() { echo hi; }; time command cd sub; cat AGENTS.md"])
def test_command_or_builtin_runs_the_cd_builtin_past_a_function_named_cd(command):
    # bash runs the builtin, not the function: the shell is in sub (a plain ``cd sub`` still loses the directory)
    assert shell_walk(command) == (["sub/AGENTS.md"], True, 0)


@pytest.mark.parametrize("command", [
    "command() { echo F; }; command cat AGENTS.md", "env() { echo F; }; env cat AGENTS.md",
    "time() { echo F; }; X=1 time cat AGENTS.md", r"time() { echo F; }; \time cat AGENTS.md",
    "command() { echo F; }; time command cat AGENTS.md",
])
def test_a_function_named_like_a_wrapper_runs_instead_of_the_wrapper(command):
    # the function prints F: the printer after its name is only an argument
    assert shell_walk(command) == ([], False, 0)


@pytest.mark.parametrize("command", ["command() { echo F; }; command cd sub; cat AGENTS.md",
                                     "builtin() { echo F; }; builtin cd sub; cat AGENTS.md"])
def test_a_function_named_like_a_wrapper_runs_no_cd_after_its_name(command):
    assert shell_walk(command) == (["AGENTS.md"], False, 0)


@pytest.mark.parametrize("command", [
    "command() { echo F; }; /usr/bin/env cat AGENTS.md", "time() { echo F; }; time cat AGENTS.md",
    "time() { echo F; }; time -p cat AGENTS.md", "env() { echo F; }; command env cat AGENTS.md",
])
def test_the_time_keyword_or_a_wrapper_no_function_shadows_runs_the_printer(command):
    # the keyword is no function call, ``/usr/bin/env`` is a path, and ``command env`` passes over a function ``env``
    assert shell_walk(command) == (["AGENTS.md"], False, 0)


@pytest.mark.parametrize("command", [
    "command() { cd sub; }; command cat x; cat AGENTS.md", "time() { cd sub; }; X=1 time cat x; cat AGENTS.md",
    'command() { cd sub; }; command cat "$X"; cat AGENTS.md', "time() { cd sub; }; time -p time; cat AGENTS.md",
])
def test_a_function_named_like_a_wrapper_that_names_a_cd_loses_the_directory(command):
    assert shell_walk(command) == ([], True, 1)


@pytest.mark.parametrize("first, second", [("command() { echo F; }", "command cat AGENTS.md"),
                                           ("cat() ( echo FN )", "time cat AGENTS.md")])
def test_a_wrapper_or_subshell_function_an_earlier_call_defined_shadows_a_later_read(first, second):
    events = _events(_call("a", first), _result("a"), _call("b", second), _result("b"),
                     _call("c", "head README.md"), _result("c"))
    assert _reads(events) == ["README.md"]


@pytest.mark.parametrize("command", ["cat() ( echo FN ); time cat AGENTS.md", "cat() ( echo FN ); cat AGENTS.md",
                                     "function cat() ( echo FN ); cat AGENTS.md", "cat()\n(\n  echo FN\n)\ncat AGENTS.md"])
def test_a_function_with_a_subshell_body_named_like_a_printer_is_no_read(command):
    assert shell_walk(command) == ([], False, 0)


@pytest.mark.parametrize("command", ["f() ( cd sub ); f; cat AGENTS.md", "cd() ( echo hi ); cat AGENTS.md",
                                     "f() ( cd sub ); g() { f; }; g; cat AGENTS.md", "f()\n(\n  cd sub\n)\nf\ncat AGENTS.md"])
def test_a_function_with_a_subshell_body_moves_nothing(command):
    # the body runs in a subshell, so its cd leaves this shell where it was; defining ``cd`` runs nothing
    assert shell_walk(command) == (["AGENTS.md"], False, 0)


@pytest.mark.parametrize("command", ["time -x cat AGENTS.md; true", "time --help cat AGENTS.md; true",
                                     "time -pp cat AGENTS.md; true", "time -p -p cat AGENTS.md", "time -- -p cat AGENTS.md"])
def test_a_dash_word_the_time_keyword_takes_as_no_option_is_the_command_name(command):
    # bash's ``time`` takes one ``-p``, then one ``--``: ``-x`` is run as the command, a non-printer
    assert shell_walk(command) == ([], False, 0)


@pytest.mark.parametrize("command", ["time -- cat AGENTS.md", "time -p -- cat AGENTS.md"])
def test_the_time_keywords_end_of_options_reads_the_file(command):
    assert shell_walk(command) == (["AGENTS.md"], False, 0)


@pytest.mark.parametrize("first", ["time -x cd sub", "time -p -p cd sub"])
def test_a_cd_after_a_dash_word_the_time_keyword_takes_as_no_option_still_loses_the_directory(first):
    # bash runs ``-x``, but every dash word is taken as an option where only a directory may be lost
    assert shell_walk(f"{first}; cat AGENTS.md") == ([], True, 1)


def test_a_definition_whose_close_is_not_seen_keeps_the_rest_of_the_script_as_its_body():
    # bash closes the body at ``}>``, which is not seen here: the body runs to the end, its cd included
    events = _events(_call("a", "f() { cd sub; }>/dev/null"), _result("a"), _call("b", "f; cat AGENTS.md"), _result("b"))
    assert _reads(events) == []
