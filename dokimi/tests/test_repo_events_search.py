# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``searched``: the guidance files a ``grep``, ``rg``, ``git grep`` or Claude Code ``Grep`` call showed lines of
(contract §4.1, ``guidance_searched``), from the three harness traces to ``process``."""

from __future__ import annotations

import json

import pytest

from dokimi.artifacts import schema_errors
from dokimi.repos.cli_traces import failed_search, shell_walk, shell_walked
from dokimi.repos.events import convert
from dokimi.repos.probes import _operands
from dokimi.repos.process_metrics import process_metrics
from test_repo_events_bash import WS, _call, _denials, _events, _result


def _process(*lines: dict, cwd: str = "") -> dict:
    first = {"type": "system", "subtype": "init", "timestamp": "2026-01-01T00:00:00Z"}
    t = convert("claude-stream-json", "\n".join(json.dumps(x) for x in (first, *lines)), WS, cwd)
    return process_metrics(t.events, lambda p: p.endswith("AGENTS.md"), unknown_reads=t.unknown_reads)


def _grep_tool(tool_id: str, **given: str) -> dict:
    return {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": tool_id, "name": "Grep", "input": {"pattern": "warn", **given}}]}}


def _jsonl(*records: dict) -> str:
    return "\n".join(json.dumps(r) for r in records) + "\n"


@pytest.mark.parametrize("command, searched", [
    ('grep -n -i "deprecat\\|warn" dpnp/AGENTS.md dpnp/tests/AGENTS.md', ["dpnp/AGENTS.md", "dpnp/tests/AGENTS.md"]),
    ("rg -n warn dpnp/tests/AGENTS.md", ["dpnp/tests/AGENTS.md"]),
    ("git grep -n warn -- dpnp/AGENTS.md", ["dpnp/AGENTS.md"]),
    ("git grep -e warn -- dpnp/AGENTS.md", ["dpnp/AGENTS.md"]),
    ("git grep -h warn -- AGENTS.md", ["AGENTS.md"]),  # git's -h is --no-filename, a search
    ("git grep --max-depth 0 warn -- AGENTS.md", ["AGENTS.md"]),  # 0 is the value of --max-depth, not a file
    ("git grep --threads 2 -m 1 warn AGENTS.md", ["AGENTS.md"]),
    ("egrep warn AGENTS.md", ["AGENTS.md"]),
    ("fgrep warn AGENTS.md", ["AGENTS.md"]),
    ("/bin/grep warn AGENTS.md", ["AGENTS.md"]),
    ("command grep warn AGENTS.md", ["AGENTS.md"]),
    ("grep --line-number --color=always warn AGENTS.md", ["AGENTS.md"]),  # --line-number is not -l, --color not -c
    ("time -p grep warn AGENTS.md | head", ["AGENTS.md"]),  # time is bash's keyword: it times the pipeline
    ("time grep warn AGENTS.md", ["AGENTS.md"]),
    ("rg -L warn AGENTS.md", ["AGENTS.md"]),  # rg's -L follows links; it does not list files without a match
    ("grep warn AGENTS.md -", ["AGENTS.md"]),  # - is stdin, no file
    ("grep -m 1 warn AGENTS.md", ["AGENTS.md"]),  # 1 is the value of -m, not a file
    ("grep -nA2 warn AGENTS.md", ["AGENTS.md"]),
    ("grep -e warn -f patterns.txt AGENTS.md", ["AGENTS.md"]),  # the pattern file is read, not searched
    ("grep warn AGENTS.md 2>/dev/null", ["AGENTS.md"]),  # stderr only
    ("cd sub && grep warn AGENTS.md", ["sub/AGENTS.md"]),
    ("grep warn AGENTS.md | head -n 20", ["AGENTS.md"]),
    ("grep 'a|b' AGENTS.md | head -n 20", ["AGENTS.md"]),  # a pipe in quotes is no pipe
    ("rg warn AGENTS.md | tail -5", ["AGENTS.md"]),
    ("grep warn AGENTS.md | head -n 20 | tail --lines=5", ["AGENTS.md"]),
    ("grep warn AGENTS.md 2>&1 | head", ["AGENTS.md"]),
    ("if grep warn AGENTS.md; then echo y; fi", ["AGENTS.md"]),
    ("if grep warn AGENTS.md | head -n 20; then echo yes; fi", ["AGENTS.md"]),  # a keyword before the piped search
    ("while grep warn AGENTS.md | tail -3; do break; done", ["AGENTS.md"]),
    ("grep warn AGENTS.md | head -n+5", ["AGENTS.md"]),
    ("grep warn AGENTS.md | tail -n +0", ["AGENTS.md"]),  # from line 0 on: all of them
    ("grep warn AGENTS.md | head -n -0", ["AGENTS.md"]),  # all but the last 0
    ("grep warn AGENTS.md | head -c 100 -q", ["AGENTS.md"]),
    ("grep warn AGENTS.md | head --li=5", ["AGENTS.md"]),  # an unambiguous prefix of --lines
    ("grep warn AGENTS.md | head -n 20 2>/dev/null", ["AGENTS.md"]),  # stderr of the cut stage leaves its lines alone
    ("rg warn AGENTS.md 2>&1 | tail -n 5 2>/dev/null", ["AGENTS.md"]),
    ("grep warn AGENTS.md | tail -3 2>&1", ["AGENTS.md"]),
    ("grep warn AGENTS.md | head --quiet", ["AGENTS.md"]),  # a long option that takes no value
    ("grep -ewarn AGENTS.md", ["AGENTS.md"]),  # the expression attached to -e
    ("rg -ewarn AGENTS.md", ["AGENTS.md"]),
    ("grep -nefoo AGENTS.md", ["AGENTS.md"]),  # in a cluster
    ("grep -ne foo AGENTS.md", ["AGENTS.md"]),
    ("grep -fpatterns.txt AGENTS.md", ["AGENTS.md"]),  # the pattern file attached to -f is read, not searched
    ("grep -nf patterns.txt AGENTS.md", ["AGENTS.md"]),
    ("grep -m1 -ewarn AGENTS.md", ["AGENTS.md"]),
    ("grep -e --help AGENTS.md", ["AGENTS.md"]),  # --help is the text searched for, not an option
    ("rg -e --version AGENTS.md", ["AGENTS.md"]),
    ("grep -ne --help AGENTS.md", ["AGENTS.md"]),
    ("grep --regexp --help AGENTS.md", ["AGENTS.md"]),
    ("grep -m 5 -e warn AGENTS.md", ["AGENTS.md"]),
    ("grep -e warn --exclude AGENTS.md other.txt", ["other.txt"]),  # AGENTS.md is the value of --exclude
    ("grep -e warn --include '*.md' --exclude-dir sub AGENTS.md", ["AGENTS.md"]),
    ("rg warn --ignore-file AGENTS.md other.txt", ["other.txt"]),
    ("rg --color never warn AGENTS.md", ["AGENTS.md"]),
    ("grep warn AGENTS.md | head -n 20 --", ["AGENTS.md"]),  # the end of options, and stdin named explicitly
    ("grep warn AGENTS.md | tail -n 20 -", ["AGENTS.md"]),
    ("grep warn AGENTS.md | head -n 20 -- -", ["AGENTS.md"]),
    ("grep warn AGENTS.md | head -n 1k", ["AGENTS.md"]),  # size suffixes the tools read
    ("grep warn AGENTS.md | head -c 2KiB", ["AGENTS.md"]),
    ("grep warn AGENTS.md | tail -n +3MB", ["AGENTS.md"]),
    ("grep warn AGENTS.md | tail --follow=descriptor", ["AGENTS.md"]),  # the one valueless option with an optional value
    ("grep warn AGENTS.md | tail --follow=d", ["AGENTS.md"]),  # a prefix of descriptor
    ("grep warn AGENTS.md | tail --follow", ["AGENTS.md"]),  # descriptor by default
    ("grep warn AGENTS.md | tail --pid=123 -s 0.5", ["AGENTS.md"]),
    ("grep warn AGENTS.md | tail --pid 123 --sleep-interval=2", ["AGENTS.md"]),
    ("grep warn AGENTS.md | tail --max-unchanged-stats=3 -s1", ["AGENTS.md"]),
    ("grep --line-n warn AGENTS.md", ["AGENTS.md"]),  # an abbreviation of --line-number is no output mode
    ("grep --co warn AGENTS.md", ["AGENTS.md"]),  # ambiguous: color, colour, context, count
    ("rg --cou warn AGENTS.md", ["AGENTS.md"]),  # rg reads no abbreviation, so this is no --count
    ("grep warn AGENTS.md |& head -n 2", ["AGENTS.md"]),  # |& sends stdout to head as | does
    ("grep warn AGENTS.md | /usr/bin/head", ["AGENTS.md"]),
    ("grep warn AGENTS.md | LC_ALL=C head -n 3", ["AGENTS.md"]),
    ("git --no-pager grep -n warn -- AGENTS.md", ["AGENTS.md"]),
    ("git -P grep -n warn -- AGENTS.md", ["AGENTS.md"]),
    ("git -c grep.lineNumber=true --no-pager grep warn -- AGENTS.md", ["AGENTS.md"]),
    ("grep -h warn AGENTS.md", ["AGENTS.md"]),  # grep's -h is --no-filename
])
def test_a_search_names_the_files_it_shows_lines_of(command, searched):
    walked = shell_walked(command)
    assert walked.searched == searched
    assert walked.reads == []  # a search is no read: guidance_read keeps its meaning


@pytest.mark.parametrize("command", [
    "grep -q warn AGENTS.md", "grep --quiet warn AGENTS.md", "grep --silent warn AGENTS.md",
    "grep -l warn AGENTS.md", "grep --files-with-matches warn AGENTS.md", "rg -l warn AGENTS.md",
    "grep -L warn AGENTS.md", "grep --files-without-match warn AGENTS.md", "git grep -L warn -- AGENTS.md",
    "grep -c warn AGENTS.md", "grep -nc warn AGENTS.md", "grep --count warn AGENTS.md",
    "rg -c warn AGENTS.md", "rg --count-matches warn AGENTS.md", "rg --files AGENTS.md sub/AGENTS.md", "git grep -l warn -- AGENTS.md",
    "grep -r warn docs", "grep -R warn .", "rg warn docs",  # a directory names no guidance file
    "grep warn *.md", "grep warn $FILE", "grep warn $(ls AGENTS.md)", "grep warn AGENTS.{md,txt}",  # computed operands
    "grep warn AGENTS.md > hits.txt", "grep warn AGENTS.md >/dev/null", "grep warn AGENTS.md &>/dev/null",
    "grep warn AGENTS.md > /dev/null 2>&1",
    "! grep warn AGENTS.md", "grep warn - < AGENTS.md", "cat AGENTS.md | grep warn", "grep -f AGENTS.md warn",
    "grep warn AGENTS.md | wc -l", "grep warn AGENTS.md | sort", "grep warn AGENTS.md | head -n 5 other.md",
    "grep warn AGENTS.md | head > out.txt", "(grep warn AGENTS.md)", "git -C sub grep warn -- AGENTS.md",
    "grep() { :; }; grep warn AGENTS.md", "grep() { :; }; grep warn AGENTS.md | head", "grep --help AGENTS.md AGENTS.md",
    "cd -; grep warn AGENTS.md",  # the directory is not known
    "{ grep warn AGENTS.md; } > /dev/null",
    "grep warn AGENTS.md | head -n 0", "grep warn AGENTS.md | head -n0", "grep warn AGENTS.md | tail -0",  # no line passes
    "grep warn AGENTS.md | head -c 0", "grep warn AGENTS.md | head --lines=0", "grep warn AGENTS.md | tail -n 0",
    "grep warn AGENTS.md | head -n +0", "grep warn AGENTS.md | head --lines 0",
    "grep warn AGENTS.md | head --help", "grep warn AGENTS.md | tail --version", "grep warn AGENTS.md | head --v",
    "grep warn AGENTS.md | head -n x", "grep warn AGENTS.md | head --bogus", "grep warn AGENTS.md | head -x",
    "grep warn AGENTS.md | head -n", "grep warn AGENTS.md | head -n 5 | head -n 0",
    "head() { :; }; grep warn AGENTS.md | head", "tail() { :; }; grep warn AGENTS.md | tail -n 5",  # the function runs
    "if ! grep warn AGENTS.md | head; then echo no; fi",
    "grep warn AGENTS.md | head 2>/dev/null > out.txt", "grep warn AGENTS.md | tail -n 5 2>/dev/null 1>&2",  # stdout goes away
    "grep warn AGENTS.md | head -n 'x",  # a stage shlex cannot split
    "grep -fAGENTS.md warn", "grep -nf AGENTS.md warn",  # AGENTS.md is the pattern file
    "grep -m0 warn AGENTS.md | head", "grep -m 0 warn AGENTS.md | head", "grep --max-count=0 warn AGENTS.md | head",
    "grep --max-count 0 warn AGENTS.md", "grep -nm0 warn AGENTS.md", "rg -m0 warn AGENTS.md", "grep -m 5 -m 0 warn AGENTS.md",
    "grep -m +0 warn AGENTS.md", "grep -m+0 warn AGENTS.md", "grep --max-count=+00 warn AGENTS.md", "rg -m +0 warn AGENTS.md",  # a leading + is accepted
    "grep --help -e warn AGENTS.md", "rg -e warn --version AGENTS.md",  # --help and --version as options
    "grep -e warn --exclude AGENTS.md", "rg warn --ignore-file AGENTS.md",  # AGENTS.md is an option's value, stdin is searched
    "grep warn AGENTS.md sub/AGENTS.md | head --quiet=bad", "grep warn AGENTS.md sub/AGENTS.md | head --verbose=1",
    "grep warn AGENTS.md sub/AGENTS.md | tail --zero-terminated=x", "grep warn AGENTS.md sub/AGENTS.md | head -n 1qq",
    "grep warn AGENTS.md sub/AGENTS.md | head -c 2kb", "grep warn AGENTS.md | head -n 20 -- other.md",
    "grep warn AGENTS.md | tail -n 5 - other.md",  # a file after the end of options or beside - is read instead of stdin
    "grep --qui warn AGENTS.md", "grep --cou warn AGENTS.md", "grep --sil warn AGENTS.md",  # GNU grep reads a prefix
    "grep --files-witho warn AGENTS.md", "grep --files-with-m warn AGENTS.md", "grep --help warn AGENTS.md",
    "grep --vers warn AGENTS.md", "git grep --cou warn -- AGENTS.md",
    "grep -V warn AGENTS.md", "rg -V warn AGENTS.md", "rg -h warn AGENTS.md",  # the version or the help, no search
    "grep warn AGENTS.md | ./head", "grep warn AGENTS.md | /opt/bin/head", "git -C sub --no-pager grep warn -- AGENTS.md",
    "git -c grep warn -- AGENTS.md",  # -c takes grep as its value: no command
])
def test_a_search_that_shows_no_line_of_a_file_is_not_searched(command):
    got = _process(_call("a", command), _result("a"))
    assert got["guidance_searched"] == [] and got["guidance_read"] == []
    assert got["unknown_reads"] == 0  # unknown_reads counts reads, a search never adds to it


@pytest.mark.parametrize("cut", [
    "tail --follow=bad", "tail --follow=", "tail --follow=name", "tail --follow=n", "tail --follow=nam",  # stdin by name
    "tail --pid=bad", "tail --pid", "tail --pid -1", "tail -s bad", "tail -sbad",
    "tail --sleep-interval=x", "tail --sleep-interval", "tail --max-unchanged-stats=-1", "tail -n bad -n 5",
    "head -c 1qq -c 5",
])
def test_a_cut_stage_the_tool_refuses_before_it_prints_keeps_no_search(cut):
    command = f"grep warn AGENTS.md sub/AGENTS.md | {cut}"
    assert shell_walked(command).searched == []
    assert _process(_call("a", command), _result("a", "", True))["guidance_searched"] == []


def test_the_issues_grep_over_two_nested_files_is_searched_and_not_read():
    command = 'grep -n -i "deprecat\\|warn" dpnp/AGENTS.md dpnp/tests/AGENTS.md'
    events = _events(_call("a", command), _result("a", "dpnp/AGENTS.md:3:warn"))
    assert events == [{"t": 5.0, "kind": "command", "text": command, "rc": 0, "output": "dpnp/AGENTS.md:3:warn",
                       "output_truncated": False, "searched": ["dpnp/AGENTS.md", "dpnp/tests/AGENTS.md"]}]
    got = process_metrics(events, lambda p: p.endswith("AGENTS.md"))
    assert (got["guidance_read"], got["guidance_searched"]) == ([], ["dpnp/AGENTS.md", "dpnp/tests/AGENTS.md"])
    assert got["tool_calls"] == 1  # the search adds no event: tool_calls counts the same


def test_a_search_of_one_file_that_failed_shows_no_line():
    # grep exits 1 when no line matched and 2 on an error; Claude Code reports both as is_error
    assert _process(_call("a", "grep warn AGENTS.md"), _result("a", "", True))["guidance_searched"] == []
    assert _process(_call("a", "cd sub && grep warn AGENTS.md"), _result("a", "", True))["guidance_searched"] == []
    assert _process(_call("a", "grep warn AGENTS.md"), _result("a", "AGENTS.md:1:warn"))["guidance_searched"] == ["AGENTS.md"]


@pytest.mark.parametrize("command", [
    "grep warn AGENTS.md sub/AGENTS.md", "echo hi; grep zzz AGENTS.md", "cd sub && grep zzz AGENTS.md",
    "echo hi; grep zzz AGENTS.md sub/AGENTS.md",
])
def test_a_failed_search_whose_exit_is_the_only_searchs_shows_no_line_of_any_file(command):
    assert _process(_call("a", command), _result("a"))["guidance_searched"] != []  # it ran fine
    assert _process(_call("a", command), _result("a", "x", True))["guidance_searched"] == []


@pytest.mark.parametrize("command", ["set -o pipefail; grep x AGENTS.md | head -n 1", "cd sub && grep x AGENTS.md | tail -3"])
def test_a_failed_claude_pipeline_that_printed_lines_keeps_its_search_as_its_exit_is_unknown(command):
    # under pipefail the failure may be grep killed by the closed pipe (exit 141), after head showed a line
    files = _process(_call("a", command), _result("a"))["guidance_searched"]
    assert _process(_call("a", command), _result("a", "AGENTS.md:1:x", True))["guidance_searched"] == files != []
    assert _process(_call("a", command), _result("a", "", True))["guidance_searched"] == []  # no text: nothing shown
    assert failed_search(command, 1) is True  # a known exit 1 of the pipeline's search is a search that matched nothing


def test_git_grep_reads_the_options_of_git_not_of_gnu_grep():
    # git takes --max-depth's next word as its value: the pattern is AGENTS.md and the only file is other.txt
    command = "git grep --max-depth 0 AGENTS.md -- other.txt"
    assert shell_walked(command).searched == ["other.txt"]
    assert _process(_call("a", command), _result("a", "other.txt:1:see AGENTS.md"))["guidance_searched"] == []


@pytest.mark.parametrize("command", ["git grep --max-d 0 AGENTS.md -- other.txt", "git grep --max-depth=0 AGENTS.md -- other.txt"])
def test_git_grep_reads_an_abbreviated_max_depth_as_git_does(command):
    assert shell_walked(command).searched == ["other.txt"]
    assert _process(_call("a", command), _result("a", "other.txt:1:see AGENTS.md"))["guidance_searched"] == []


def test_git_grep_of_an_older_revision_searches_no_workspace_file():
    # HEAD~1:AGENTS.md is the old commit's file; the workspace AGENTS.md was not searched
    command = "git grep old_instruction HEAD~1 -- AGENTS.md"
    assert shell_walked(command).searched == []
    result = _result("a", "HEAD~1:AGENTS.md:old_instruction")
    assert _process(_call("a", command), result)["guidance_searched"] == []
    assert shell_walked("git grep old_instruction -- AGENTS.md").searched == ["AGENTS.md"]


def test_rg_hostname_bin_takes_the_next_word_as_its_value():
    command = "rg --hostname-bin /bin/hostname AGENTS.md other.txt"
    assert shell_walked(command).searched == ["other.txt"]
    assert _process(_call("a", command), _result("a", "other.txt:1:see AGENTS.md"))["guidance_searched"] == []


@pytest.mark.parametrize("command", ["grep a AGENTS.md; grep b sub/AGENTS.md", "grep a AGENTS.md && grep b sub/AGENTS.md"])
def test_a_failed_command_with_an_earlier_search_keeps_the_searches_as_its_exit_is_the_last_ones(command):
    got = _process(_call("a", command), _result("a", "x", True))
    assert got["guidance_searched"] == ["AGENTS.md", "sub/AGENTS.md"]


def test_a_codex_search_of_several_files_that_exited_1_shows_no_line_but_exit_2_keeps_them():
    command = "grep warn AGENTS.md sub/AGENTS.md"
    for rc, searched in ((1, []), (2, ["AGENTS.md", "sub/AGENTS.md"]), (0, ["AGENTS.md", "sub/AGENTS.md"])):
        item = {"id": "i", "type": "command_execution", "command": command, "aggregated_output": "x", "exit_code": rc}
        raw = _jsonl({"type": "item.started", "item": item}, {"type": "item.completed", "item": item})
        (event,) = [e for e in convert("codex-json", raw, WS).events if e["kind"] == "command"]
        assert event.get("searched", []) == searched, rc


@pytest.mark.parametrize("command, files", [
    ("grep warn AGENTS.md | head", ["AGENTS.md"]),  # head exits 0 whether or not grep matched
    ("grep warn AGENTS.md sub/AGENTS.md", ["AGENTS.md", "sub/AGENTS.md"]),
])
def test_a_search_whose_result_has_no_text_showed_no_line(command, files):
    assert _process(_call("a", command), _result("a", ""))["guidance_searched"] == []
    (event,) = _events(_call("a", command), _result("a", ""))
    assert "searched" not in event and event["rc"] == 0
    assert _process(_call("a", command), _result("a", "AGENTS.md:1:warn"))["guidance_searched"] == files


def test_a_refused_search_searched_nothing():
    got = _process(_call("a", "grep warn AGENTS.md"), _denials("a"))
    assert got["guidance_searched"] == [] and got["denied_commands"] == 1


def test_a_relative_search_after_an_earlier_calls_cd_is_not_counted_and_is_not_an_unknown_read():
    lines = (_call("a", "cd sub"), _result("a"), _call("b", "grep warn AGENTS.md /ws/docs/AGENTS.md"), _result("b"))
    got = _process(*lines)
    assert (got["guidance_searched"], got["unknown_reads"]) == (["docs/AGENTS.md"], 0)  # the absolute one stays
    # a read in the same position is counted unknown, as before
    assert _process(_call("a", "cd sub"), _result("a"), _call("b", "cat AGENTS.md"), _result("b"))["unknown_reads"] == 1


def test_the_search_starts_in_the_directory_the_harness_started_in():
    got = _process(_call("a", "grep warn AGENTS.md"), _result("a"), cwd="sub")
    assert got["guidance_searched"] == ["sub/AGENTS.md"]


def test_a_relative_grep_tool_path_starts_in_the_directory_the_harness_started_in():
    given = {"output_mode": "content"}
    (event,) = _events(_grep_tool("g", path="AGENTS.md", **given), _result("g", "AGENTS.md:1:x"), cwd="sub")
    assert event["searched"] == ["sub/AGENTS.md"]
    (event,) = _events(_grep_tool("g", path=f"{WS}/AGENTS.md", **given), _result("g", "x"), cwd="sub")
    assert event["searched"] == ["AGENTS.md"]  # an absolute path stays where it is
    got = _process(_grep_tool("g", path="AGENTS.md", **given), _result("g", "x"), cwd="sub")
    assert got["guidance_searched"] == ["sub/AGENTS.md"]


def test_a_relative_grep_tool_path_after_a_bash_cd_is_dropped_and_an_absolute_one_stays():
    given = {"output_mode": "content"}
    abs_path = f"{WS}/AGENTS.md"
    lines = (_call("a", "cd sub"), _result("a"), _grep_tool("g", path="AGENTS.md", **given), _result("g", "3:x"),
             _grep_tool("h", path=abs_path, **given), _result("h", "3:x"))
    assert _process(*lines)["guidance_searched"] == ["AGENTS.md"]  # only the absolute one
    assert [e.get("searched") for e in _events(*lines) if e["kind"] == "tool"] == [None, ["AGENTS.md"]]
    before = (_grep_tool("g", path="AGENTS.md", **given), _result("g", "3:x"), _call("a", "cd sub"), _result("a"))
    assert _process(*before)["guidance_searched"] == ["AGENTS.md"]  # the cd came after the Grep call
    denied = (_call("a", "cd sub"), _denials("a"), _grep_tool("g", path="AGENTS.md", **given), _result("g", "3:x"))
    assert _process(*denied)["guidance_searched"] == ["AGENTS.md"]  # a refused cd moved nothing


def test_a_grep_tool_call_in_content_mode_over_a_file_is_searched():
    events = _events(_grep_tool("g", path=f"{WS}/dpnp/AGENTS.md", output_mode="content"), _result("g", "dpnp/AGENTS.md:1:x"))
    assert [(e["kind"], e["name"], e["searched"]) for e in events] == [("tool", "Grep", ["dpnp/AGENTS.md"])]
    got = process_metrics(events, lambda p: p.endswith("AGENTS.md"))
    assert (got["guidance_searched"], got["guidance_read"], got["tool_calls"]) == (["dpnp/AGENTS.md"], [], 1)


@pytest.mark.parametrize("given", [
    {"path": f"{WS}/AGENTS.md"},  # Claude Code's default mode lists the names of files and shows no line
    {"path": f"{WS}/AGENTS.md", "output_mode": "files_with_matches"},
    {"path": f"{WS}/AGENTS.md", "output_mode": "count"},
    {"output_mode": "content"},  # no path: it searches the working directory
    {"path": f"{WS}/docs", "output_mode": "content"},  # a directory is no guidance file
])
def test_a_grep_tool_call_that_shows_no_line_of_a_guidance_file_is_not_searched(given):
    got = _process(_grep_tool("g", **given), _result("g"))
    assert got["guidance_searched"] == []
    if given.get("output_mode") != "content" or "path" not in given:  # it names no file: the event has no list
        assert all("searched" not in e for e in _events(_grep_tool("g", **given), _result("g")))


@pytest.mark.parametrize("text", ["No matches found", "", "  \n"])
def test_a_grep_tool_call_that_printed_no_line_searched_nothing(text):
    given = {"path": f"{WS}/AGENTS.md", "output_mode": "content", "offset": "50"}
    (event,) = _events(_grep_tool("g", **given), _result("g", text))
    assert "searched" not in event  # the persisted event says nothing was searched
    assert _process(_grep_tool("g", **given), _result("g", text))["guidance_searched"] == []


@pytest.mark.parametrize("command, searched", [
    ("grep -e warn --exclude-d AGENTS.md other.txt", ["other.txt"]),  # --exclude-d is --exclude-dir: AGENTS.md is its value
    ("grep -e warn --exclude-f=AGENTS.md other.txt", ["other.txt"]),
    ("grep --max=1 warn AGENTS.md", ["AGENTS.md"]),  # --max=1 is --max-count=1
    ("grep --max 1 warn AGENTS.md", ["AGENTS.md"]),
    ("grep --max=0 warn AGENTS.md", []),  # a zero match limit prints no line (#1013)
    ("grep --aft 2 warn AGENTS.md", ["AGENTS.md"]),
    ("grep -e warn --incl '*.md' AGENTS.md", ["AGENTS.md"]),
    ("rg -e warn --exclude-d AGENTS.md other.txt", ["AGENTS.md", "other.txt"]),  # rg reads no prefix
])
def test_a_gnu_grep_long_option_abbreviation_takes_its_value_word(command, searched):
    assert shell_walked(command, "").searched == searched
    process = _process(_call("a", command), _result("a"))
    assert process["guidance_searched"] == [p for p in searched if p.endswith("AGENTS.md")]


def test_a_failed_or_refused_grep_tool_call_searched_nothing():
    given = {"path": f"{WS}/AGENTS.md", "output_mode": "content"}
    assert _process(_grep_tool("g", **given), _result("g", "error", True))["guidance_searched"] == []
    refused = {"type": "result", "result": "ok", "permission_denials": [{"tool_name": "Grep", "tool_use_id": "g"}]}
    assert _process(_grep_tool("g", **given), refused)["guidance_searched"] == []
    (event,) = _events(_grep_tool("g", **given), refused)
    assert event["denied"] is True and "searched" not in event  # the persisted event says nothing was searched


def _codex(command: str, rc: int | None, started: bool, output: str | None = None) -> str:
    item = {"id": "a", "type": "command_execution", "command": command}
    shown = {} if output is None else {"aggregated_output": output}
    done = {"type": "item.completed", "item": {**item, "exit_code": rc, **shown}}
    return _jsonl(*([{"type": "item.started", "item": item}] if started else []), done)


@pytest.mark.parametrize("started", [True, False], ids=["started", "completed only"])
def test_codex_searches_follow_the_same_rules(started):
    def searched(command, rc):
        t = convert("codex-json", _codex(command, rc, started), WS)
        return [e.get("searched") for e in t.events if e["kind"] == "command"]

    assert searched("/usr/bin/bash -lc 'rg -n warn dpnp/AGENTS.md'", 0) == [["dpnp/AGENTS.md"]]
    assert searched("rg -n warn dpnp/AGENTS.md", 1) == [None]  # one file, failed
    assert searched("grep warn AGENTS.md sub/AGENTS.md", 1) == [None]  # exit 1: no line matched in either
    assert searched("grep warn AGENTS.md sub/AGENTS.md", 2) == [["AGENTS.md", "sub/AGENTS.md"]]  # an error, perhaps in one
    assert searched("grep -q warn AGENTS.md", 0) == [None]
    assert searched("grep -e --help AGENTS.md", 0) == [["AGENTS.md"]]
    assert searched("grep -m0 warn AGENTS.md | head", 0) == [None]
    assert searched("grep -m +0 warn AGENTS.md; echo done", 0) == [None]
    assert searched("grep -m +1 warn AGENTS.md; echo done", 0) == [["AGENTS.md"]]
    assert searched("grep warn AGENTS.md sub/AGENTS.md | head --quiet=bad", 1) == [None]
    assert searched("grep warn AGENTS.md sub/AGENTS.md | tail --follow=bad", 1) == [None]
    assert searched("grep --qui warn AGENTS.md", 0) == [None]
    assert searched("grep warn AGENTS.md sub/AGENTS.md | tail --follow=name", 1) == [None]


@pytest.mark.parametrize("started", [True, False], ids=["started", "completed only"])
def test_a_codex_search_with_an_empty_output_showed_no_line(started):
    def searched(output):
        t = convert("codex-json", _codex("grep warn AGENTS.md | head", 0, started, output), WS)
        return [e.get("searched") for e in t.events if e["kind"] == "command"]

    assert searched("") == [None]
    assert searched("AGENTS.md:1:warn") == [["AGENTS.md"]]
    assert searched(None) == [["AGENTS.md"]]  # no output field: not known to be empty


@pytest.mark.parametrize("started", [True, False], ids=["started", "completed only"])
def test_a_codex_search_piped_into_head_that_exited_141_keeps_the_lines_it_showed(started):
    command = "grep warn AGENTS.md | head -n 1"
    t = convert("codex-json", _codex(command, 141, started, "AGENTS.md:1:warn"), WS)
    assert [e.get("searched") for e in t.events if e["kind"] == "command"] == [["AGENTS.md"]]
    t = convert("codex-json", _codex(command, 1, started, "x"), WS)
    assert [e.get("searched") for e in t.events if e["kind"] == "command"] == [None]  # exit 1 stays a search of no line


def test_a_copilot_search_piped_into_head_that_exited_141_keeps_the_lines_it_showed():
    raw = _jsonl({"type": "tool.execution_start", "data": {"toolCallId": "a", "toolName": "bash",
                                                            "arguments": {"command": "grep warn AGENTS.md | head -n 1"}}},
                 {"type": "tool.execution_complete", "data": {"toolCallId": "a", "success": False,
                                                              "shellExecution": {"exitCode": 141},
                                                              "result": {"content": "AGENTS.md:1:warn"}}})
    assert [e.get("searched") for e in convert("copilot-json", raw, WS).events if e["kind"] == "command"] == [["AGENTS.md"]]


def test_copilot_bash_searches_follow_the_same_rules():
    def searched(command, rc, content=None):
        result = {} if content is None else {"result": {"content": content}}
        raw = _jsonl({"type": "tool.execution_start", "data": {"toolCallId": "a", "toolName": "bash",
                                                                "arguments": {"command": command}}},
                     {"type": "tool.execution_complete", "data": {"toolCallId": "a", "success": rc == 0,
                                                                  "shellExecution": {"exitCode": rc}, **result}})
        return [e.get("searched") for e in convert("copilot-json", raw, WS).events if e["kind"] == "command"]

    assert searched("grep warn AGENTS.md | head", 0, "") == [None]  # it printed nothing
    assert searched("grep warn AGENTS.md | head", 0, "AGENTS.md:1:warn") == [["AGENTS.md"]]
    assert searched("grep warn AGENTS.md sub/AGENTS.md | tail --follow=n", 1) == [None]
    assert searched("grep -n warn AGENTS.md", 0) == [["AGENTS.md"]]
    assert searched("grep -n warn AGENTS.md", 1) == [None]
    assert searched("grep warn AGENTS.md sub/AGENTS.md", 1) == [None]  # exit 1: no line matched in either
    assert searched("grep warn AGENTS.md sub/AGENTS.md", 2) == [["AGENTS.md", "sub/AGENTS.md"]]  # an error, perhaps in one
    assert searched("grep -e --help AGENTS.md", 0) == [["AGENTS.md"]]
    assert searched("grep -m0 warn AGENTS.md | head", 0) == [None]
    assert searched("grep warn AGENTS.md sub/AGENTS.md | head --quiet=bad", 1) == [None]
    assert searched("grep warn AGENTS.md sub/AGENTS.md | tail -s bad", 1) == [None]
    assert searched("grep --cou warn AGENTS.md", 0) == [None]
    powershell = _jsonl({"type": "tool.execution_start", "data": {"toolCallId": "a", "toolName": "powershell",
                                                                   "arguments": {"command": "grep warn AGENTS.md"}}})
    assert "searched" not in convert("copilot-json", powershell, WS).events[0]  # PowerShell is not parsed


def _searched_by_each_harness(command: str, rc: int, output: str) -> list[list[str] | None]:
    """The ``searched`` of the one command a Claude Code, a Codex and a Copilot trace ran with this exit and output."""
    claude = [e.get("searched") for e in _events(_call("a", command), _result("a", output, rc != 0))
              if e["kind"] == "command"]
    codex = [e.get("searched") for e in convert("codex-json", _codex(command, rc, True, output), WS).events
             if e["kind"] == "command"]
    raw = _jsonl({"type": "tool.execution_start", "data": {"toolCallId": "a", "toolName": "bash",
                                                            "arguments": {"command": command}}},
                 {"type": "tool.execution_complete", "data": {"toolCallId": "a", "success": rc == 0,
                                                              "shellExecution": {"exitCode": rc},
                                                              "result": {"content": output}}})
    copilot = [e.get("searched") for e in convert("copilot-json", raw, WS).events if e["kind"] == "command"]
    return [*claude, *codex, *copilot]


@pytest.mark.parametrize("command, files", [
    ("rg --passthru zzz AGENTS.md sub/AGENTS.md", ["AGENTS.md", "sub/AGENTS.md"]),
    ("rg --passthru zzz AGENTS.md", ["AGENTS.md"]),  # one file: its exit is the read-failure rule's too
    ("rg --passthrough zzz AGENTS.md", ["AGENTS.md"]),
    ("cd sub && rg --passthru zzz AGENTS.md | head", ["sub/AGENTS.md"]),
])
def test_an_rg_passthru_that_matched_nothing_still_showed_every_line(command, files):
    assert _searched_by_each_harness(command, 1, "# a\nline\n") == [files] * 3  # exit 1 with the file's lines printed
    plain = command.replace("--passthru ", "").replace("--passthrough ", "")
    assert _searched_by_each_harness(plain, 1, "") == [None] * 3  # without it exit 1 is no line


@pytest.mark.parametrize("command", [
    'grep x AGENTS.md; cd "$D"; grep y sub/AGENTS.md',  # the second search's directory is lost; the first's is not
    "grep x AGENTS.md; cd $(dirname $F) && grep y sub/AGENTS.md",
])
def test_a_failed_search_after_a_lost_directory_keeps_an_earlier_search_that_showed_lines(command):
    assert _searched_by_each_harness(command, 1, "AGENTS.md:1:x") == [["AGENTS.md"]] * 3
    assert failed_search(command, 1) is False


@pytest.mark.parametrize("command", [
    "echo hi; { grep zzz AGENTS.md docs/AGENTS.md; }",  # the closing brace has no exit of its own
    "echo hi; { grep zzz AGENTS.md docs/AGENTS.md; } 2>/dev/null",
    "echo hi; rg --passthru -q x other.txt; grep nomatch AGENTS.md",  # a quiet passthru shows no line
    "echo hi; rg --passthru --count x other.txt; grep nomatch AGENTS.md",
    "{ rg --passthru zzz other.txt; } >/dev/null; echo hi; grep nomatch AGENTS.md",  # the dropped group's passthru
    "{ rg --passthru zzz other.txt; } | cat; echo hi; grep nomatch AGENTS.md",
])
def test_a_no_match_search_in_a_brace_group_or_after_a_quiet_passthru_shows_no_line(command):
    assert _searched_by_each_harness(command, 1, "hi\n") == [None] * 3
    assert failed_search(command, 1) is True


@pytest.mark.parametrize("limit", ["9" * 5000, "0" * 5000, "0" * 5000 + "1"])
def test_a_numeric_option_past_the_integer_string_limit_is_read_not_raised(limit):
    shown = limit.strip("0") != ""
    head = f"grep x AGENTS.md | head -n {limit}"
    assert _searched_by_each_harness(head, 0, "AGENTS.md:1:x") == [["AGENTS.md"] if shown else None] * 3
    match = f"grep -m {limit} x AGENTS.md"
    assert _searched_by_each_harness(match, 0, "AGENTS.md:1:x") == [["AGENTS.md"] if shown else None] * 3


@pytest.mark.parametrize("command", ['cd "$D"; grep y sub/AGENTS.md', 'echo hi; cd "$D"; grep y /ws/AGENTS.md'])
def test_a_failed_search_whose_only_known_search_is_the_last_part_shows_no_line(command):
    assert failed_search(command, 1) is (command.endswith("/ws/AGENTS.md"))


@pytest.mark.parametrize("path", ["C:/ws/AGENTS.md", "C:\\ws\\AGENTS.md", "\\\\host\\share\\AGENTS.md"])
def test_an_absolute_windows_grep_tool_path_after_a_bash_cd_stays_searched(path):
    given = {"output_mode": "content", "path": path}
    lines = (_call("a", "cd sub"), _result("a"), _grep_tool("g", **given), _result("g", "3:x"))
    first = {"type": "system", "subtype": "init", "timestamp": "2026-01-01T00:00:00Z"}
    events = convert("claude-stream-json", "\n".join(json.dumps(x) for x in (first, *lines)), "C:/ws", "").events
    (grep,) = [e for e in events if e["kind"] == "tool"]
    assert grep["searched"] == ([path] if path.startswith("\\\\") else ["AGENTS.md"])
    relative = _grep_tool("g", output_mode="content", path="AGENTS.md")
    events = convert("claude-stream-json", "\n".join(json.dumps(x) for x in (
        first, *lines[:2], relative, _result("g", "3:x"))), "C:/ws", "").events
    assert "searched" not in [e for e in events if e["kind"] == "tool"][0]  # a relative one still is not known


@pytest.mark.parametrize("script", [
    "cd $D | head; cat AGENTS.md",
    "source $ENV 2>&1 | tail -3; cat AGENTS.md",
    'eval "$(conda shell.bash hook)" 2>&1 | tail -2; cat AGENTS.md',
    'source "$HOME/.bashrc" 2>&1 | tail -1; cat AGENTS.md',
])
def test_a_computed_directory_change_piped_into_head_or_tail_moves_no_shell(script):
    assert shell_walk(script) == (["AGENTS.md"], False, 0)  # the stage ran in a pipeline's subshell
    assert shell_walked(script).searched == []


def test_a_piped_computed_directory_change_keeps_the_next_calls_reads():
    lines = (_call("a", 'eval "$(conda shell.bash hook)" 2>&1 | tail -2'), _result("a"), _call("b", "cat AGENTS.md"), _result("b"))
    assert _process(*lines)["guidance_read"] == ["AGENTS.md"]
    # not piped, the same command does move the shell: the next call's relative read is dropped
    assert _process(_call("a", 'eval "$(conda shell.bash hook)"'), _result("a"), _call("b", "cat AGENTS.md"),
                    _result("b"))["guidance_read"] == []


@pytest.mark.parametrize("command, searched", [
    ("rg -tc warn AGENTS.md", ["AGENTS.md"]),  # -t takes the type c; it is not -c
    ("rg '-g*.lock' warn AGENTS.md", ["AGENTS.md"]),  # -g takes the glob; l and c are not flags (unquoted, the shell expands it)
    ("grep -m1 warn AGENTS.md", ["AGENTS.md"]),
    ("grep -cm1 warn AGENTS.md", []),  # the count flag comes before the value option
    ("grep -nc warn AGENTS.md", []),
    ("rg -ntc warn AGENTS.md", ["AGENTS.md"]),
    ("rg -ct warn AGENTS.md", []),
])
def test_the_value_attached_to_a_short_option_is_no_flags(command, searched):
    assert shell_walked(command).searched == searched


@pytest.mark.parametrize("program, args, operands", [
    ("rg", ("-tc", "warn", "AGENTS.md"), ("AGENTS.md",)),
    ("rg", ("-g*.lock", "warn", "AGENTS.md"), ("AGENTS.md",)),
    ("grep", ("-m1", "warn", "AGENTS.md"), ("AGENTS.md",)),
    ("grep", ("-cm1", "warn", "AGENTS.md"), ("AGENTS.md",)),
    ("grep", ("-nc", "warn", "AGENTS.md"), ("AGENTS.md",)),
    ("rg", ("-g*.md", "warn", "AGENTS.md"), ("AGENTS.md",)),  # ends in d, --max-depth: still the value of -g
    ("rg", ("-g*.C", "warn", "AGENTS.md"), ("AGENTS.md",)),  # ends in C, --context
    ("rg", ("-tm", "pat", "AGENTS.md"), ("AGENTS.md",)),
    ("grep", ("-nA", "2", "warn", "AGENTS.md"), ("AGENTS.md",)),  # the value is the next word
    ("grep", ("-A2", "warn", "AGENTS.md"), ("AGENTS.md",)),
    ("grep", ("-ewarn", "AGENTS.md"), ("AGENTS.md",)),  # the expression is attached: AGENTS.md is the file
    ("grep", ("-nfpats", "AGENTS.md"), ("pats", "AGENTS.md")),  # the pattern file is attached; it is read, not an expression
    ("grep", ("-f", "pats", "AGENTS.md"), ("pats", "AGENTS.md")),
])
def test_the_operands_of_a_searcher_do_not_depend_on_how_its_cluster_is_read(program, args, operands):
    assert _operands(program, args) == operands


@pytest.mark.parametrize("command", ["rg -g'*.md' warn AGENTS.md", "rg -g'*.C' warn AGENTS.md"])
def test_a_glob_attached_to_rg_g_that_ends_in_a_value_letter_keeps_the_file(command):
    assert shell_walked(command).searched == ["AGENTS.md"]


def test_the_schema_takes_a_list_of_paths_in_searched():
    event = {"v": 2, "kind": "command", "t": None, "text": "grep x AGENTS.md", "rc": 0}
    assert schema_errors("repo_event.v2", {**event, "searched": ["AGENTS.md"]}) == []
    assert schema_errors("repo_event.v2", {**event, "searched": "AGENTS.md"}) != []
    assert schema_errors("repo_event.v2", {**event, "searched": [1]}) != []
    tool = {"v": 2, "kind": "tool", "t": None, "name": "Grep"}
    assert schema_errors("repo_event.v2", {**tool, "searched": ["AGENTS.md"]}) == []
    assert schema_errors("repo_event.v2", {**tool, "searched": "AGENTS.md"}) != []


def test_the_retired_version_1_schema_is_unchanged_by_searched():
    event = {"kind": "command", "t": None, "text": "grep x AGENTS.md", "rc": 0}
    assert schema_errors("repo_event.v1", {**event, "searched": "AGENTS.md"}) == []


@pytest.mark.parametrize("lines", [
    (_call("a", "grep warn AGENTS.md"), _result("a", "AGENTS.md:1:warn")),
    (_grep_tool("a", path="/ws/AGENTS.md", output_mode="content"), _result("a", "1:warn")),
])
def test_the_searched_events_the_converter_writes_validate_under_the_version_it_writes(lines):
    first = {"type": "system", "subtype": "init", "timestamp": "2026-01-01T00:00:00Z"}
    written = convert("claude-stream-json", _jsonl(first, *lines), WS, "").events_jsonl()
    events = [e for e in map(json.loads, written.splitlines()) if "searched" in e]
    assert events and all(e["v"] == 2 and schema_errors("repo_event.v2", e) == [] for e in events)


def test_a_multi_file_grep_lists_every_file_it_named_when_only_one_showed_a_line():
    command = "grep -n visible AGENTS.md docs/AGENTS.md"
    got = _process(_call("a", command), _result("a", "AGENTS.md:1:visible"))
    assert got["guidance_searched"] == ["AGENTS.md", "docs/AGENTS.md"]
