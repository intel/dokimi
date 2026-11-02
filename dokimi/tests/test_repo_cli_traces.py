# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Codex and Copilot CLI JSON traces: shell reads and trace events (contract §6). No LLM."""

from __future__ import annotations

import json
import shlex
from pathlib import Path

import pytest

from dokimi.repos.cli_traces import _LONG_VALUE, shell_reads, unwrap_shell
from dokimi.repos.events import convert

# --- traces ------------------------------------------------------------------


@pytest.mark.parametrize("command, reads", [
    ("cat AGENTS.md", ["AGENTS.md"]),
    ("cat a b && sed -n '1,80p' cpp/AGENTS.md", ["a", "b", "cpp/AGENTS.md"]),
    ("head -n 40 x.md; tail -c 10 y", ["x.md", "y"]),
    ("nl -ba src/a.c", ["src/a.c"]),
    ("cat a > b", []),            # writes b
    ("cat a | grep x", []),       # a pipeline is not a plain print
    ("sed -i 's/a/b/' f", []),    # edits f
    ("sed 's/a/b/' f", []),       # no -n: a transform, not a range print
    ("ls -la", []),
    ("cat 'unterminated", []),
    ("sed -n '1p;2p' AGENTS.md", ["AGENTS.md"]),  # regression: quoted program should not be split
    ("cd sub && cat AGENTS.md", ["sub/AGENTS.md"]),  # regression: cd changes cwd for following commands
    ("cd sub && cd inner && cat AGENTS.md", ["sub/inner/AGENTS.md"]),  # successive cds compose
    ("cat ./AGENTS.md", ["AGENTS.md"]),                   # dot components are normalised
    ("cd sub && cat ../AGENTS.md", ["AGENTS.md"]),
    ("cd - && cat X", []),                                # the previous directory is not tracked
    ("cd missing || cat X", []),                          # runs in an unknown directory
    ("cd /abs && cat X", ["/abs/X"]),
    ("cd $D && cat X", []),
    ("cat a || cat b", ["a", "b"]),                       # no cd: the directory is known
    ("cat a & cat b", ["a", "b"]),                        # a single & separates commands
    ("cat a >> b", []),
    ("cat a < b", []),
    ("cat a 2>&1", ["a"]),                                # a stderr dup or a stderr discard leaves stdout as printed
    ("cat a 2>/dev/null", ["a"]),
    ("sed -n '1,5p' a 2>/dev/null", ["a"]),
    ("cat a &>/dev/null", []),                            # stdout is discarded too: no content reaches the agent
    ("cat a 2> /dev/null", ["a"]),
    ("cat a\\\n b", ["a", "b"]),                          # a line continuation joins the command
    ("cat <<'EOF'\ncat secret\nEOF", []),                 # a heredoc body is input, not commands
    ("cat > f <<'EOF'\nhead other\nEOF", []),
    ("cat a <<EOF\nx\nEOF\ncat b", ["a", "b"]),
    ("(cd x && cat f)", []),                              # a subshell is not tracked
    ("head --lines 5 a", ["a"]),                          # option values are not files
    ("head -qn 5 a", ["a"]),
    ("head -n5 a", ["a"]),
    ("nl -w 3 a", ["a"]),
    ("nl -ba a", ["a"]),
    ("sed -n -e '1p' f", ["f"]),
    ("sed -n -E '1,3p' f", ["f"]),
    ("sed -n -i.bak 1p f", []),                           # in-place edit
    ("sed -n --in-place 1p f", []),
    ("sed -n '$p' f", ["f"]),                             # $ inside single quotes is the last line
    ("sed -n '1,$p' AGENTS.md", ["AGENTS.md"]),
    ("cat \"$HOME/f\"", []),                              # an expansion is not a path
    ("{ cd x; cat f; }", ["x/f"]),                        # a brace group runs in this shell
    ("{ cat f; }", ["f"]),
    ("cat a >/dev/null2", []),                            # /dev/null must end the word
    ("cat a 2>/dev/nullfile", ["a"]),                     # stderr to a file: a is still read, nullfile is no operand
    ("tail -s 2 a", ["a"]),                               # -s takes the sleep interval
    ("tail -f -s 2 a", ["a"]),
    ("tail --sleep-interval 2 a", ["a"]),
    ("nl -ba -v 2 a", ["a"]),
    ("nl --number-format ln a", ["a"]),
    ("nl --number-format=ln a", ["a"]),
    ("{ cd x; } & cat f", ["f"]),                         # a backgrounded group runs in a subshell
    ("{ cd x; } 2>&1 | cat; cat f", ["f"]),               # so does a piped one
    ("{ cd x; cat f; } & cat g", ["x/f", "g"]),
    ("cat a | { cd x; cat b; }", []),                     # a group in a pipeline: directory unknown after it
    ("if true; then cd sub; fi; cat AGENTS.md", []),      # a conditional cd may not run
    ("for d in sub; do cd sub; done; cat AGENTS.md", []),
    ("pushd sub; cat AGENTS.md", []),
    ("popd; cat AGENTS.md", []),
    ("(cd sub && cat a && cat b && cat c)", []),          # every part of a subshell is skipped
    ("(cd sub; cat a; cat b)", []),
    ("cat other.txt # see CLAUDE.md", ["other.txt"]),     # a comment is not an operand
    ("true # ; cat AGENTS.md", []),                       # a comment hides the operators in it
    ("cat 'x # y'", ["x # y"]),                           # a # inside quotes is text
    ("cat a#b", ["a#b"]),                                 # a # inside a word is not a comment
    ("FOO=1 cat a", ["a"]),                               # assignments before the command
    ("LC_ALL=C head -n 5 a", ["a"]),
    ("/bin/cat a", ["a"]),                                # the command's basename decides
    ("/usr/bin/sed -n 1p f", ["f"]),
    ("env cat a", ["a"]),                                 # env runs the command it is given
    ("env -i FOO=1 cat a", ["a"]),
    ("env -u FOO cat a", ["a"]),                          # -u takes the next word as its value
    ("builtin cd x && cat f", ["x/f"]),                   # builtin and command run the cd builtin
    ("command cd x && cat f", ["x/f"]),
    ("if true; then builtin cd x; fi; cat f", []),        # still conditional after a keyword
    ("env --chdir=x cat f", []),                          # env --chdir runs cat in x, not here
    ("env --chdir x cat f", []),
    ("env --chdir x cat /abs", ["/abs"]),
    ("env --unset X cat a", ["a"]),
    ("env -C x cat f", []),
    ("env -Cx cat f", []),
    ("env -C x cat /abs", ["/abs"]),                      # an absolute path is the same from any directory
    ("cat a 2>err.txt", ["a"]),                           # stderr to a file does not change what cat prints
    ("cat a 2>>err.txt", ["a"]),
    ("sed -n 1p f 2>err.txt", ["f"]),
    ("cat a 1>out.txt", []),                              # stdout to a file is still no read, as with >
    ("cat 'x 2>y'", ["x 2>y"]),                           # a redirect inside quotes is text
    ("cat 'a'2>x", []),                                   # the word is a2; stdout goes to x
    ("cat a 2>x'y'", ["a"]),                              # the stderr target is xy, not a read
    ("cat a 2> err", ["a"]),                              # a space after 2> is still stderr to a file
    ("echo 2>x", []),
    ("cat a 12>x", []),                                   # fd 12, not 2: a redirect, so no read
    ("FOO=1 cd x && cat f", ["x/f"]),                     # an assignment before the builtin cd
    ("/bin/cd x && cat f", ["f"]),                        # /bin/cd is a program: the shell stays put
    ("env cd x && cat f", ["f"]),                         # so is the cd env runs
    ("./cat a", []),                                      # a repo-local cat is not the system tool
    ("tools/sed -n 1p f", []),
    ("sed -n 1,5p -", []),                                # `-` is stdin, not a file
    ("sed -n 1p - f", ["f"]),
    ("cat 'a 2>/dev/null b'", ["a 2>/dev/null b"]),       # a /dev/null redirect inside quotes is text
    ("cat 'a 2>&1 b'", ["a 2>&1 b"]),
    ('cat \\" 2>&1 "z"', ['"', "z"]),                    # an escaped quote opens no quoted text
    ('cat "a\\" 2>&1 b"', []),                          # an escape inside quotes is computed: no read
    ('builtin cd "$D"; cat AGENTS.md', []),               # a computed cd after a prefix: the directory is lost
    ('command cd "$D"; cat AGENTS.md', []),
    ('FOO=1 cd "$D"; cat AGENTS.md', []),
    ('then cd "$D"; cat AGENTS.md', []),
    ('env cd "$D"; cat AGENTS.md', ["AGENTS.md"]),        # env runs no builtin: the shell stays put
    ('cat AGENTS.md2 2>/dev/null', ["AGENTS.md2"]),      # a stderr discard after a space: AGENTS.md2 is still read
    ('cat AGENTS.md2>/dev/null', []),                     # the 2 is part of the file name; stdout is discarded
    ('cat a12>/dev/null', []),
    ('cat a2>&1 b', ["a2", "b"]),
    ('cd $(dirname sub/file); cat AGENTS.md', []),        # a computed cd: the directory is unknown after it
    ('(cd x; cat f); cat g', ["g"]),                      # a subshell's cd does not reach the outer shell
    # a group whose output feeds a pipe: its reads are not the agent's (the left side of a pipe)
    ("{ cat AGENTS.md; } | head -n 0", []),
    ("{ cat /abs; } | head -n 0", []),
    # a group whose stdout is redirected or discarded: its reads are not the agent's
    ("{ cat a; } >/dev/null", []),
    ("{ cat a; } > f", []),
    ("{ cat a; } &>/dev/null", []),
    ("{ cat a; } >>log", []),
    ("{ cat a; } >&f", []),                                # stdout and stderr to a file
    ("{ cat a; } 2>/dev/null", ["a"]),                     # a stderr discard leaves stdout as printed
    ("{ cat a; } 2>>err.log", ["a"]),
    ("{ cat a; } 3>>x", ["a"]),
    ("{ cat a; } 1>&2", ["a"]),
    ("{ cat a; } >&2", ["a"]),
    ("{ cat /abs; } & cat g", ["/abs", "g"]),              # a backgrounded group still prints to the agent
    # glob and brace expansions: the files are not the words written
    ("cd sub*; cat AGENTS.md", []),                       # the glob may match another directory
    ("cat AGENTS.*", []),
    ("cat a?b", []),
    ("cat {a,b}", []),
    ("cat 'AGENTS.*'", ["AGENTS.*"]),                     # quoted: a literal file name
    ("cat AGENTS\\*", ["AGENTS*"]),                       # escaped: a literal file name
    # a directory change after && or || may not run
    ("false && cd sub; cat AGENTS.md", []),
    ("false || cd sub; cat AGENTS.md", []),
    ("cat a && cd sub; cat b", ["a"]),                    # b is read at an unknown directory
    # --help and --version print and open no file
    ("cat --help AGENTS.md", []),
    ("head --version AGENTS.md", []),
    ("tail -n 5 --help a", []),
    ("nl --help a", []),
    ("cat -- --help", ["--help"]),                        # after --, it is a file name
    # sed prints only for a print-only script; w, d, s and a script file are not reads
    ("sed -n 'd' AGENTS.md", []),
    ("sed -n 'w out' AGENTS.md", []),
    ("sed -n '1p;d' f", []),
    ("sed -n -e 1p -e 'w x' f", []),
    ("sed -n -f script.sed f", []),
    ("sed -n --file=script.sed f", []),
    ("sed -n '/x/p' f", []),                              # a regex address: not in the print-only forms
    ("sed -n -e 1p -e 2p f", ["f"]),
    ("sed -n --expression=1p f", ["f"]),
    ("sed -n 'p' f", ["f"]),
    ("sed -n '1p;' f", ["f"]),
    # a builtin or a system wrapper is recognised by its bare name, not by a repo-local program of that name
    ("./command cd sub; cat AGENTS.md", ["AGENTS.md"]),
    ("./builtin cd sub; cat AGENTS.md", ["AGENTS.md"]),
    ("./env cat AGENTS.md", []),
    ("/usr/bin/env cat a", ["a"]),
])
def test_shell_reads(command, reads):
    assert shell_reads(command) == reads


@pytest.mark.parametrize("cmd,option", [(c, o) for c, opts in _LONG_VALUE.items() for o in sorted(opts)])
def test_shell_reads_skips_the_value_of_every_long_option(cmd, option):
    assert shell_reads(f"{cmd} {option} 5 a") == ["a"]


@pytest.mark.parametrize("command", [
    '{ cd "$D"; } && cat a',
    '{ builtin cd "$D"; cat a; }',
    'pushd "$D" && cat a',
    'popd "$D" && cat a',
])
def test_a_computed_directory_change_in_a_group_or_a_stack_loses_the_directory(command):
    # the cd runs in this shell, so the cat after it is relative to a directory not known here
    assert shell_reads(command, "sub") == []


def test_shell_reads_start_in_the_given_directory():
    assert shell_reads("cat AGENTS.md", "sub") == ["sub/AGENTS.md"]
    assert shell_reads("cd inner && cat ../x", "sub") == ["sub/x"]
    assert shell_reads("cat /ws/a", "sub") == ["/ws/a"]
    assert shell_reads("{ cd x && cat f; }", "sub") == ["sub/x/f"]


@pytest.mark.parametrize(("command", "reads"), [
    ("cat AGENTS\\\n.md", ["AGENTS.md"]), ("ca\\\nt AGENTS.md", ["AGENTS.md"]), ("cat a \\\n  b", ["a", "b"])])
def test_shell_reads_joins_a_line_continuation_without_a_space(command, reads):
    assert shell_reads(command) == reads


@pytest.mark.parametrize(("command", "reads"), [
    ("echo `true; cd sub; pwd`; cat AGENTS.md", []),  # as with $( ): a cd inside it is not this shell's
    ("echo `pwd`; cat AGENTS.md", ["AGENTS.md"]), ("cd `dirname a/b`; cat AGENTS.md", []),
    ("echo \\`; cd sub; cat AGENTS.md", ["sub/AGENTS.md"])])  # an escaped backtick opens nothing
def test_shell_reads_a_backtick_substitution_is_a_subshell(command, reads):
    assert shell_reads(command) == reads


@pytest.mark.parametrize(("command", "reads"), [
    ("cd sub && cat f & cat g", ["sub/f", "g"]), ("cd sub || cat f & cat g", ["g"]),
    ("true && cd sub & cat g", ["g"]), ("cd sub && cat f; cat g", ["sub/f", "sub/g"])])
def test_shell_reads_a_backgrounded_list_leaves_the_directory(command, reads):
    assert shell_reads(command) == reads


@pytest.mark.parametrize(("command", "reads"), [
    ("f() { :; cd sub; }; cat AGENTS.md", ["AGENTS.md"]), ("f() { :; cat AGENTS.md; }", []),
    ("function f { :; cd sub; }; cat AGENTS.md", ["AGENTS.md"]), ("f() {\n  cd sub\n}\ncat a", ["a"]),
    ("f() { g() { cd x; }; cd y; }; cat a", ["a"]), ("echo 'f() {'; cd sub; cat a", ["sub/a"])])
def test_shell_reads_skips_a_function_definition(command, reads):
    assert shell_reads(command) == reads


@pytest.mark.parametrize(("command", "reads"), [
    ("cat AGENTS.md 2>/dev/null 1>&2", []), ("cat AGENTS.md 2>err >&2", []),
    ("cat AGENTS.md 1>&2 2>/dev/null", ["AGENTS.md"]), ("cat AGENTS.md 2>/dev/null", ["AGENTS.md"]),
    ("cat AGENTS.md 2>&1 >&2", ["AGENTS.md"]), ("cat AGENTS.md 2>/dev/null 12>&2", ["AGENTS.md"]),
    ("{ cat AGENTS.md; } 2>/dev/null 1>&2", []), ("{ cat AGENTS.md; } 1>&2 2>/dev/null", ["AGENTS.md"])])
def test_shell_reads_follows_redirects_in_order(command, reads):
    # stdout copied onto a stderr already sent to /dev/null or a file never reaches the agent
    assert shell_reads(command) == reads


def test_shell_reads_of_an_empty_script_reads_nothing():
    assert shell_reads("") == [] and shell_reads("  \n") == []
    assert unwrap_shell("") == ""  # an empty command is its own script, not a wrapper


def test_cli_traces_resolve_relative_reads_from_the_starting_directory():
    codex = _jsonl({"type": "item.completed", "item": {"id": "1", "type": "command_execution",
                                                       "command": "cat AGENTS.md", "exit_code": 0}})
    assert convert("codex-json", codex, "/ws", "sub").reads() == {"sub/AGENTS.md"}
    assert convert("codex-json", codex, "/ws").reads() == {"AGENTS.md"}
    copilot = _jsonl({"type": "tool.execution_start", "data": {"toolCallId": "c", "toolName": "bash",
                                                               "arguments": {"command": "cat AGENTS.md"}}})
    assert convert("copilot-json", copilot, "/ws", "sub").reads() == {"sub/AGENTS.md"}


def test_copilot_powershell_is_not_parsed_as_a_shell_script():
    # only bash is a POSIX script: a PowerShell path is not a read of the mangled "C:repoa.py"
    ps = _jsonl({"type": "tool.execution_start", "data": {"toolCallId": "c", "toolName": "powershell",
                                                       "arguments": {"command": "cat C:\\repo\\a.py"}}})
    t = convert("copilot-json", ps, "/ws")
    assert t.reads() == set()
    assert [e["kind"] for e in t.events] == ["command"]


def test_copilot_view_without_a_path_records_no_read_event():
    view = _jsonl({"type": "tool.execution_start", "data": {"toolCallId": "v", "toolName": "view",
                                                         "arguments": {}}})
    t = convert("copilot-json", view, "/ws")
    assert [e for e in t.events if e["kind"] == "read"] == []


@pytest.mark.parametrize("data", [
    {"toolCallId": "c", "toolName": "bash", "arguments": "cat a.py"},  # arguments is not an object
    {"toolCallId": "c", "toolName": "view", "arguments": None},
    {"toolCallId": "c", "toolName": "bash"},  # no arguments at all
])
def test_copilot_malformed_arguments_read_nothing(data):
    t = convert("copilot-json", _jsonl({"type": "tool.execution_start", "data": data}), "/ws")
    assert t.reads() == set()


def test_copilot_completion_without_a_started_tool_is_ignored():
    raw = _jsonl({"type": "tool.execution_complete", "data": {"toolCallId": "missing", "success": True}})
    assert convert("copilot-json", raw, "/ws").reads() == set()


def test_unwrap_shell():
    assert unwrap_shell("/usr/bin/bash -lc 'cat a.txt'") == "cat a.txt"
    assert unwrap_shell("bash -lc \"git show HEAD | head\"") == "git show HEAD | head"
    assert unwrap_shell("rg foo") == "rg foo"
    assert unwrap_shell("/bin/bash -lc cat a") == "cat a"  # an unquoted script of several words


def _jsonl(*records):
    return "\n".join(json.dumps(r) for r in records) + "\nnot json\n"


def test_codex_file_change_without_a_usable_path_records_no_edit():
    for changes in ([], [{"path": None, "kind": "update"}], [{"kind": "update"}]):
        line = json.dumps({"type": "item.completed", "item": {"id": "1", "type": "file_change", "changes": changes}})
        assert [e for e in convert("codex-json", line, "/ws").events if e["kind"] == "edit"] == [], changes


def test_a_codex_file_change_with_a_malformed_entry_is_one_dropped_line_and_no_edits():
    for changes in (42, [42], [{"path": None, "kind": "update"}], [{"path": "/ws/src/a.c"}, 42],
                    {}, "", 0, False, [{"path": 42}], [{"path": True}], [{"path": ["/ws/a.c"]}]):
        line = json.dumps({"type": "item.completed", "item": {"id": "1", "type": "file_change", "changes": changes}})
        transcript = convert("codex-json", line, "/ws")
        assert transcript.dropped_lines == 1, changes
        assert [e for e in transcript.events if e["kind"] == "edit"] == [], changes


def test_codex_json_to_events():
    raw = _jsonl(
        {"type": "thread.started", "thread_id": "x"},
        {"type": "error", "message": "Reconnecting... 2/5 (request timed out)"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "I will look."}},
        {"type": "item.started", "item": {"type": "command_execution", "command": "bash -lc 'cat /ws/AGENTS.md'"}},
        {"type": "item.completed", "item": {"type": "command_execution", "command": "/usr/bin/bash -lc 'cat /ws/AGENTS.md'",
                                            "aggregated_output": "# guide\n", "exit_code": 0}},
        {"type": "item.completed", "item": {"type": "command_execution", "command": "bazel test //x",
                                            "aggregated_output": "", "exit_code": 3}},
        {"type": "item.completed", "item": {"type": "file_change", "changes": [{"path": "/ws/src/a.c", "kind": "update"}]}},
        {"type": "item.completed", "item": {"type": "mcp_tool_call", "server": "gh", "tool": "search"}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": " DONE "}},
        {"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 7}},
    )
    t = convert("codex-json", raw, "/ws")
    assert t.answer == "DONE" and t.num_turns == 1 and (t.input_tokens, t.output_tokens) == (100, 7)
    assert t.cost_usd is None  # Codex reports no cost: the batch budget counts the cap
    assert t.events == [
        {"t": None, "kind": "read", "path": "AGENTS.md"},
        {"t": None, "kind": "command", "text": "cat /ws/AGENTS.md", "rc": 0, "output": "# guide\n",
         "output_truncated": False},
        {"t": None, "kind": "command", "text": "bazel test //x", "rc": 3},
        {"t": None, "kind": "edit", "path": "src/a.c"},
        {"t": None, "kind": "tool", "name": "gh.search"},
    ]
    assert t.errors == ["line 11: not JSON"] and not t.is_error  # a reconnect is not an error


@pytest.mark.parametrize(("fmt", "record"), [
    ("codex-json", lambda s: {"type": "item.completed", "item": {"type": "agent_message", "text": s}}),
    ("copilot-json", lambda s: {"type": "assistant.message", "data": {"content": s, "phase": "final_answer"}}),
    ("claude-stream-json", lambda s: {"type": "result", "result": s}),
])
@pytest.mark.parametrize("sep", ["\u2028", "\u2029", "\x85"])
def test_a_jsonl_record_with_a_unicode_line_separator_is_one_record(fmt, record, sep):
    # JSON may hold these unescaped (serde_json and JSON.stringify do not escape them); only \n ends a record
    raw = json.dumps(record(f"first{sep}second"), ensure_ascii=False) + "\n"
    t = convert(fmt, raw, None)
    assert t.answer == f"first{sep}second" and t.errors == []


def test_codex_json_keeps_every_agent_message_and_the_last_is_the_answer():
    raw = _jsonl(
        {"type": "item.completed", "item": {"type": "agent_message", "text": "first"}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": " last "}},
    )
    t = convert("codex-json", raw, "/ws")
    assert t.agent_messages == ["first", " last "] and t.answer == "last"


def test_codex_usage_is_summed_and_a_missing_or_zero_usage_is_not_a_measured_zero():
    two = _jsonl({"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 7}},
                 {"type": "turn.completed", "usage": {"input_tokens": 20, "output_tokens": 3}})
    t = convert("codex-json", two, None)
    assert (t.input_tokens, t.output_tokens, t.num_turns, t.usage_scope) == (120, 10, 2, "main")
    for raw in (_jsonl({"type": "turn.completed"}),
                _jsonl({"type": "turn.completed", "usage": {"input_tokens": 0, "output_tokens": 0}})):
        t = convert("codex-json", raw, None)
        assert (t.input_tokens, t.output_tokens) == (None, None)
        assert t.usage_scope is None  # no usage was reported


def test_codex_cached_input_is_split_out_of_input_tokens_as_cache_reads():
    # OpenAI counts cached_input_tokens inside input_tokens; Claude cells report cache reads apart from input
    cached = _jsonl({"type": "turn.completed",
                     "usage": {"input_tokens": 500, "cached_input_tokens": 400, "output_tokens": 50}})
    t = convert("codex-json", cached, None)
    assert (t.input_tokens, t.cache_read_tokens, t.output_tokens) == (100, 400, 50)
    no_cache_field = convert("codex-json", _jsonl({"type": "turn.completed",
                                                   "usage": {"input_tokens": 9, "output_tokens": 1}}), None)
    assert no_cache_field.cache_read_tokens is None  # not reported, so not a measured zero
    null_field = convert("codex-json", _jsonl({"type": "turn.completed",
                                               "usage": {"input_tokens": 9, "cached_input_tokens": None}}), None)
    assert null_field.cache_read_tokens is None
    malformed = convert("codex-json", _jsonl({"type": "turn.completed",
                                              "usage": {"input_tokens": 100, "cached_input_tokens": 400}}), None)
    assert (malformed.input_tokens, malformed.cache_read_tokens) == (0, 400)  # never a negative count
    negative = convert("codex-json", _jsonl({"type": "turn.completed",
                                             "usage": {"input_tokens": 500, "cached_input_tokens": -50}}), None)
    assert (negative.input_tokens, negative.cache_read_tokens) == (500, 0)


def test_codex_cache_reads_sum_over_turns_and_a_reported_zero_is_measured():
    two = _jsonl({"type": "turn.completed",
                  "usage": {"input_tokens": 400, "cached_input_tokens": 300, "output_tokens": 5}},
                 {"type": "turn.completed",
                  "usage": {"input_tokens": 200, "cached_input_tokens": 100, "output_tokens": 5}})
    t = convert("codex-json", two, None)
    assert (t.input_tokens, t.cache_read_tokens) == (200, 400)  # summed, not the last turn's 100
    zero = convert("codex-json", _jsonl({"type": "turn.completed",
                                         "usage": {"input_tokens": 9, "cached_input_tokens": 0}}), None)
    assert (zero.input_tokens, zero.cache_read_tokens) == (9, 0)  # reported as zero: a measured zero


def test_codex_turn_failure_is_an_error_result():
    t = convert("codex-json", _jsonl({"type": "turn.failed", "error": {"message": "quota"}}), None)
    assert t.is_error and "turn.failed: quota" in t.errors and t.answer == ""


def test_codex_overlapping_commands_are_matched_by_item_id():
    def cmd(kind, i, **extra):
        return {"type": kind, "item": {"id": i, "type": "command_execution", "command": "make build", **extra}}

    raw = _jsonl(cmd("item.started", "a"), cmd("item.started", "b"),
                 cmd("item.completed", "b", exit_code=1, aggregated_output="bad"),
                 cmd("item.completed", "a", exit_code=0, aggregated_output="ok"))
    events = convert("codex-json", raw, None).events
    assert [(e["rc"], e["output"]) for e in events] == [(0, "ok"), (1, "bad")]  # in start order


def test_codex_turn_failed_is_not_cleared_by_a_later_error():
    raw = _jsonl({"type": "turn.failed", "message": "x"}, {"type": "error", "message": "boom"})
    t = convert("codex-json", raw, None)
    assert t.is_error and t.errors[:2] == ["turn.failed: x", "error: boom"]


def test_codex_incomplete_command_is_recorded_with_rc_null():
    """Regression: a command with item.started but no item.completed (timeout) should have rc: null."""
    raw = _jsonl(
        {"type": "item.started", "item": {"type": "command_execution", "command": "make build"}},
        # No item.completed (timeout scenario)
        {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 1}},
    )
    t = convert("codex-json", raw, None)
    assert len(t.events) == 1
    assert t.events[0] == {"t": None, "kind": "command", "text": "make build", "rc": None}


def test_codex_failed_command_does_not_count_as_a_read():
    cmd = {"type": "command_execution", "command": "cat missing.md"}
    raw = _jsonl(
        {"type": "item.started", "item": {"id": "a", **cmd}},
        {"type": "item.completed", "item": {"id": "a", **cmd, "exit_code": 1}},
        {"type": "item.started", "item": {"id": "b", "type": "command_execution", "command": "cat AGENTS.md"}},
        {"type": "item.completed", "item": {"id": "b", "type": "command_execution", "command": "cat AGENTS.md",
                                            "exit_code": 0}},
        {"type": "item.completed", "item": {"type": "command_execution", "command": "cat nope.md", "exit_code": 2}},
    )
    t = convert("codex-json", raw, None)
    assert [e["path"] for e in t.events if e["kind"] == "read"] == ["AGENTS.md"]
    assert [e["rc"] for e in t.events if e["kind"] == "command"] == [1, 0, 2]


def test_codex_failed_command_drops_only_its_own_reads():
    """A read that succeeded earlier stays when a later command fails: reach would class the file `fetched`."""
    ok = {"type": "command_execution", "command": "cat AGENTS.md"}
    bad = {"type": "command_execution", "command": "cat missing.md"}
    raw = _jsonl(
        {"type": "item.started", "item": {"id": "a", **ok}},
        {"type": "item.completed", "item": {"id": "a", **ok, "exit_code": 0}},
        {"type": "item.started", "item": {"id": "b", **bad}},
        {"type": "item.completed", "item": {"id": "b", **bad, "exit_code": 1}},
    )
    t = convert("codex-json", raw, None)
    assert [e["path"] for e in t.events if e["kind"] == "read"] == ["AGENTS.md"]
    assert [e["rc"] for e in t.events if e["kind"] == "command"] == [0, 1]


@pytest.mark.parametrize("done", [{}, {"exit_code": None}], ids=["missing", "null"])
def test_codex_command_with_an_unknown_exit_code_keeps_its_read(done):
    """No exit code (a cut-off run or a stream without the field) is not a failure: the read stays."""
    cmd = {"type": "command_execution", "command": "cat AGENTS.md"}
    raw = _jsonl(
        {"type": "item.started", "item": {"id": "a", **cmd}},
        {"type": "item.completed", "item": {"id": "a", **cmd, **done}},
    )
    t = convert("codex-json", raw, None)
    assert [e["path"] for e in t.events if e["kind"] == "read"] == ["AGENTS.md"]
    assert [e["rc"] for e in t.events if e["kind"] == "command"] == [None]


def test_codex_failed_command_keeps_the_reads_it_may_have_made():
    """``cat a missing`` prints ``a`` and exits 1 (codex-cli 0.163): the exit code does not say which read failed."""
    def cmd(i, script):
        return {"id": i, "type": "command_execution", "command": f"/bin/bash -lc {shlex.quote(script)}"}
    raw = _jsonl(
        {"type": "item.started", "item": cmd("a", "cat sub/AGENTS.md sub/CLAUDE.md")},
        {"type": "item.completed", "item": {**cmd("a", "cat sub/AGENTS.md sub/CLAUDE.md"), "exit_code": 1,
                                            "aggregated_output": "TOKEN\ncat: sub/CLAUDE.md: No such file or directory\n"}},
        {"type": "item.started", "item": cmd("b", "cat b.md; false")},
        {"type": "item.completed", "item": {**cmd("b", "cat b.md; false"), "exit_code": 1}},
        {"type": "item.completed", "item": {**cmd("c", "head c.md d.md"), "exit_code": 1}},
    )
    reads = [e["path"] for e in convert("codex-json", raw, None).events if e["kind"] == "read"]
    assert reads == ["sub/AGENTS.md", "sub/CLAUDE.md", "b.md", "c.md", "d.md"]


def test_answer_format_lists_agree():
    from dokimi.repos import answers
    schema = json.loads((Path(answers.__file__).parents[1] / "schemas" / "harness_profile.v1.json").read_text())
    enum = schema["properties"]["answer_format"]["enum"]
    assert sorted(enum) == sorted(answers.ANSWER_FORMATS) == sorted(answers._CONVERTERS)


def test_copilot_json_to_events():
    raw = _jsonl(
        {"type": "assistant.turn_start", "timestamp": "2026-01-01T00:00:00Z", "data": {}},
        {"type": "tool.execution_start", "timestamp": "2026-01-01T00:00:01Z",
         "data": {"toolCallId": "a", "toolName": "view", "arguments": {"path": "/ws/AGENTS.md"}}},
        {"type": "tool.execution_start", "timestamp": "2026-01-01T00:00:01Z",
         "data": {"toolCallId": "b", "toolName": "bash", "arguments": {"command": "cat sub/AGENTS.md && make"}}},
        {"type": "tool.execution_complete", "data": {"toolCallId": "b", "success": False,
                                                     "shellExecution": {"exitCode": 2},
                                                     "result": {"content": "make: *** error"}}},
        {"type": "assistant.turn_start", "timestamp": "2026-01-01T00:00:03Z", "data": {}},
        {"type": "tool.execution_start", "timestamp": "2026-01-01T00:00:04Z",
         "data": {"toolCallId": "c", "toolName": "apply_patch",
                  "arguments": "*** Begin Patch\n*** Update File: /ws/src/a.c\n@@\n-x\n+y\n*** End Patch\n"}},
        {"type": "tool.execution_start", "timestamp": "2026-01-01T00:00:05Z",
         "data": {"toolCallId": "d", "toolName": "web_fetch", "arguments": {"url": "x"}}},
        {"type": "assistant.message", "data": {"content": "", "toolRequests": [{}]}},
        {"type": "assistant.message", "data": {"content": "final", "phase": "final_answer"}},
        {"type": "assistant.message", "data": {"content": "a late aside"}},
        {"type": "result", "exitCode": 0, "usage": {"premiumRequests": 0}},
    )
    t = convert("copilot-json", raw, "/ws")
    assert t.answer == "final" and t.num_turns == 2 and not t.is_error and t.cost_usd is None
    assert t.events == [
        {"t": 1.0, "kind": "read", "path": "AGENTS.md"},
        {"t": 1.0, "kind": "read", "path": "sub/AGENTS.md"},
        {"t": 1.0, "kind": "command", "text": "cat sub/AGENTS.md && make", "rc": 2, "output": "make: *** error",
         "output_truncated": False},
        {"t": 4.0, "kind": "edit", "path": "src/a.c"},
        {"t": 5.0, "kind": "tool", "name": "web_fetch"},
    ]
    assert t.reads() == {"AGENTS.md", "sub/AGENTS.md"}


@pytest.mark.parametrize(("done", "rc"), [({"success": True}, 0), ({"success": False}, 1), ({}, None)],
                         ids=["success", "failure", "unknown"])
def test_copilot_command_exit_code_falls_back_to_success(done, rc):
    """Without `shellExecution.exitCode` the rc comes from `success`; with neither it stays unknown."""
    raw = _jsonl(
        {"type": "tool.execution_start",
         "data": {"toolCallId": "a", "toolName": "bash", "arguments": {"command": "make"}}},
        {"type": "tool.execution_complete", "data": {"toolCallId": "a", **done}},
    )
    t = convert("copilot-json", raw, None)
    assert [e["rc"] for e in t.events if e["kind"] == "command"] == [rc]


@pytest.mark.parametrize(("command", "done", "reads"), [
    ("cat AGENTS.md", {"shellExecution": {"exitCode": 1}}, set()),
    ("cat AGENTS.md", {"success": False}, set()),
    ("cat AGENTS.md", {"shellExecution": {"exitCode": 0}}, {"AGENTS.md"}),
    ("cat a; cat b", {"shellExecution": {"exitCode": 1}}, {"a", "b"}),  # which part failed is not known
], ids=["exit-1", "success-false", "exit-0", "compound"])
def test_copilot_failed_bash_read_is_not_a_read(command, done, reads):
    raw = _jsonl(
        {"type": "tool.execution_start",
         "data": {"toolCallId": "a", "toolName": "bash", "arguments": {"command": command}}},
        {"type": "tool.execution_complete", "data": {"toolCallId": "a", **done}},
    )
    assert convert("copilot-json", raw, None).reads() == reads


@pytest.mark.parametrize(("success", "reads"), [(False, set()), (True, {"AGENTS.md"}), (None, {"AGENTS.md"})])
def test_copilot_failed_view_is_not_a_read(success, reads):
    done = {"toolCallId": "v"} if success is None else {"toolCallId": "v", "success": success}
    raw = _jsonl(
        {"type": "tool.execution_start",
         "data": {"toolCallId": "v", "toolName": "view", "arguments": {"path": "/ws/AGENTS.md"}}},
        {"type": "tool.execution_complete", "data": done},
    )
    assert convert("copilot-json", raw, "/ws").reads() == reads


@pytest.mark.parametrize(("success", "rc"), [(False, 1), (True, 0), (None, None)])
def test_copilot_edit_rc_is_the_completion_success(success, rc):
    done = {"toolCallId": "e"} if success is None else {"toolCallId": "e", "success": success}
    raw = _jsonl(
        {"type": "tool.execution_start",
         "data": {"toolCallId": "e", "toolName": "edit", "arguments": {"path": "/ws/src/a.py"}}},
        {"type": "tool.execution_complete", "data": done},
    )
    edits = [e for e in convert("copilot-json", raw, "/ws").events if e["kind"] == "edit"]
    assert [e.get("rc") for e in edits] == [rc]


def test_copilot_result_without_an_exit_code_is_not_an_error():
    t = convert("copilot-json", _jsonl({"type": "assistant.message", "data": {"content": "x"}}, {"type": "result"}),
                None)
    assert not t.is_error


def test_copilot_a_result_record_alone_marks_the_run_as_started():
    # no turn_start, but the final result record came: the agent ran, so it is not never-started
    t = convert("copilot-json", _jsonl({"type": "assistant.message", "data": {"content": "done", "phase": "final_answer"}},
                                       {"type": "result", "exitCode": 0}), None)
    assert t.result_seen and t.answer == "done"


def test_copilot_answer_falls_back_to_the_last_message_and_a_failed_exit_is_an_error():
    t = convert("copilot-json", _jsonl({"type": "assistant.message", "data": {"content": "only"}},
                                       {"type": "result", "exitCode": 1}), None)
    assert t.answer == "only" and t.is_error
