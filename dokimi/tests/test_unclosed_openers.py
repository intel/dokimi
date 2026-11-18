# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Text with openers and no closer is scanned in linear time, and a text with closers reads as it did (#888).

Each site keeps its old regex here as the reference: the new code gives the same result on a table of
inputs and on random strings built from the pieces of that site's grammar, and its CPU time grows
linearly from n to 4n openers.
"""

from __future__ import annotations

import json
import math
import random
import re
from collections.abc import Callable
from functools import partial
from typing import Any

import pytest

from dokimi.context import session_log, shell
from dokimi.harnesses import claude_json, fidelity, swebench_grade
from dokimi.metrics import exploration
from dokimi.repos import cli_traces
from dokimi.repos.graders import review
from dokimi.repos.shell_words import _stdout_after_stderr
from dokimi.text_spans import replace_pairs
from tests.growth_timing import GROWTH_LIMIT, growth

FUZZ_CASES = 3000


def _random_texts(pieces: list[str], seed: int, longest: int = 14) -> list[str]:
    rng = random.Random(seed)
    return ["".join(rng.choice(pieces) for _ in range(rng.randint(0, longest))) for _ in range(FUZZ_CASES)]


def _letters(i: int) -> str:
    """``i`` in base 26 as lower-case letters: a distinct tag name per number."""
    out = "a"
    while i:
        i, rest = divmod(i, 26)
        out += chr(97 + rest)
    return out


def _linear(call: Callable[[str], Any], shape: Callable[[int], str], n: int = 2_000, limit: float = GROWTH_LIMIT) -> None:
    ratio, _ = growth(lambda k: partial(call, shape(k)), n)
    assert ratio < limit


# session_log.prompt_text ----------------------------------------------------------------------------------------------

_OLD_INJECTED = re.compile(
    r"<(system-reminder|command-[a-z-]+|local-command-[a-z-]+|bash-stdout|bash-stderr)>.*?</\1>", re.S)


def _old_prompt_text(raw: str) -> str:
    text = _OLD_INJECTED.sub(" ", raw).strip()
    return "" if session_log._INTERRUPTED.fullmatch(text) else text


INJECTED_TABLE = [
    "",
    "plain words",
    "a <system-reminder>x</system-reminder> b",
    "<system-reminder>one</system-reminder> and <system-reminder>two</system-reminder>",
    "<system-reminder>\nmulti\nline\n</system-reminder>words",
    "<system-reminder><system-reminder>nested</system-reminder> tail</system-reminder>",
    "<bash-stdout>out</bash-stdout><bash-stderr>err</bash-stderr>hi",
    "<command-name>/x</command-name><command-args>y</command-args>",
    "<local-command-stdout>o</local-local-command-stdout>",
    "<system-reminder><bash-stdout>x</bash-stdout>",
    "<system-reminder>x</bash-stdout>",
    "<command-a><command-b>x</command-b>y</command-a>",
    "</system-reminder><system-reminder>x",
    "<system-reminder>kept? no closer, then words",
    "<command-a>x</command-b> y</command-a>",
    "[Request interrupted by user]",
    "<system-reminder>r</system-reminder>[Request interrupted by user for tool use]",
]


@pytest.mark.parametrize("raw", INJECTED_TABLE)
def test_prompt_text_drops_the_same_markup_as_before(raw: str) -> None:
    assert session_log.prompt_text(raw) == _old_prompt_text(raw)


def test_prompt_text_matches_the_old_regex_on_random_markup() -> None:
    pieces = ["<system-reminder>", "</system-reminder>", "<bash-stdout>", "</bash-stdout>", "<command-x>",
              "</command-x>", "<command-y>", "</command-y>", "<local-command-z>", "</local-command-z>", "w", " ", "\n"]
    for raw in _random_texts(pieces, 1):
        assert session_log.prompt_text(raw) == _old_prompt_text(raw), repr(raw)


@pytest.mark.parametrize("opener", ["<system-reminder>", "<bash-stdout>", "<command-name>"])
def test_prompt_text_scans_openers_without_a_closer_in_linear_time(opener: str) -> None:
    _linear(session_log.prompt_text, lambda k: opener * k)


def test_prompt_text_scans_openers_of_distinct_tags_in_linear_time() -> None:
    _linear(session_log.prompt_text, lambda k: "".join(f"<command-{_letters(i)}>" for i in range(k)))


# fidelity: the reminder element and the token budget ------------------------------------------------------------------

_OLD_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)
_OLD_TOKEN_BUDGET = re.compile(r"\n*<total_tokens>.*?</total_tokens>", re.DOTALL)


def _drop_attributed(span: str) -> str:
    return "" if "Attribution for git commits" in span else span


def _reminders(text: str) -> str:
    return replace_pairs(text, fidelity._REMINDER_OPEN, fidelity._REMINDER_CLOSE, _drop_attributed)


FIDELITY_TABLE = [
    "",
    "no markup",
    "<system-reminder>Attribution for git commits</system-reminder>",
    "a <system-reminder>keep</system-reminder> b <system-reminder>Attribution for git commits\nx</system-reminder> c",
    "<system-reminder>one <system-reminder>Attribution for git commits</system-reminder> two</system-reminder>",
    "<system-reminder>Attribution for git commits",
    "Attribution for git commits</system-reminder><system-reminder>x</system-reminder>",
    "text\n\n<total_tokens>12</total_tokens>",
    "\n\n\n<total_tokens>1</total_tokens>\n\n<total_tokens>2</total_tokens>tail",
    "a\n<total_tokens>unclosed",
    "<total_tokens>x</total_tokens>\n\n<total_tokens>y",
    "\n</total_tokens>\n<total_tokens>z</total_tokens>",
]


@pytest.mark.parametrize("text", FIDELITY_TABLE)
def test_reminder_elements_are_dropped_as_before(text: str) -> None:
    old = _OLD_REMINDER.sub(lambda m: _drop_attributed(m.group(0)), text)
    assert _reminders(text) == old


@pytest.mark.parametrize("text", FIDELITY_TABLE)
def test_the_token_budget_is_dropped_as_before(text: str) -> None:
    assert fidelity._without_token_budget(text) == _OLD_TOKEN_BUDGET.sub("", text)


def test_reminders_and_token_budget_match_the_old_regexes_on_random_text() -> None:
    pieces = ["<system-reminder>", "</system-reminder>", "<total_tokens>", "</total_tokens>", "\n", "\n\n", " ",
              "Attribution for git commits", "w"]
    for text in _random_texts(pieces, 2):
        assert _reminders(text) == _OLD_REMINDER.sub(lambda m: _drop_attributed(m.group(0)), text), repr(text)
        assert fidelity._without_token_budget(text) == _OLD_TOKEN_BUDGET.sub("", text), repr(text)


def test_strip_system_drops_the_token_budget_of_each_text_block() -> None:
    system = [{"type": "text", "text": "rules\n\n<total_tokens>9</total_tokens>"}, {"type": "text", "text": 5}, "raw"]
    assert fidelity._strip_system(system) == [{"type": "text", "text": "rules"}, {"type": "text", "text": 5}, "raw"]


def test_keep_text_keeps_a_block_of_unclosed_reminders_whole() -> None:
    text = "<system-reminder>" * 3 + "words"
    assert fidelity._keep_text(text, ["x"]) == text


def test_keep_text_scans_unclosed_reminders_in_linear_time() -> None:
    _linear(lambda text: fidelity._keep_text(text, ["x"]), lambda k: "<system-reminder>" * k)


def test_the_token_budget_scans_unclosed_openers_in_linear_time() -> None:
    _linear(fidelity._without_token_budget, lambda k: "\n<total_tokens>" * k)


# exploration.parse_final_answer ---------------------------------------------------------------------------------------

_OLD_FINAL_ANSWER = re.compile(r"<final_answer>(.*?)</final_answer>", re.IGNORECASE | re.DOTALL)


def _old_parse_final_answer(text: str) -> exploration.CitationSet:
    blocks = _OLD_FINAL_ANSWER.findall(text or "")
    return exploration._parse_block(blocks[-1]) if blocks else exploration._parse_loose(text or "")


FINAL_ANSWER_TABLE = [
    "",
    "src/a.py:1-2 outside any block",
    "<final_answer>\nsrc/a.py:10-20\n</final_answer>",
    "<FINAL_ANSWER>\nsrc/a.py:1\n</Final_Answer>",
    "<final_answer>\nsrc/a.py:1\n</final_answer> then <final_answer>\nsrc/b.py:2-3\nnot a cite\n</final_answer>",
    "<final_answer>\nsrc/a.py:1\n<final_answer>\nsrc/b.py:2\n</final_answer>\n</final_answer>",
    "<final_answer>\nsrc/a.py:1\n</final_answer>\n<final_answer>\nsrc/b.py:2",
    "<final_answer>\nsrc/b.py:2 never closed",
    "</final_answer><final_answer>\nsrc/c.py:3\n</final_answer>",
    "<final_answer></final_answer>",
]


@pytest.mark.parametrize("text", FINAL_ANSWER_TABLE)
def test_final_answer_blocks_are_read_as_before(text: str) -> None:
    assert exploration.parse_final_answer(text) == _old_parse_final_answer(text)


def test_final_answer_matches_the_old_regex_on_random_text() -> None:
    pieces = ["<final_answer>", "</final_answer>", "<FINAL_ANSWER>", "</Final_Answer>", "\n", " ", "src/a.py:3-4", "x"]
    for text in _random_texts(pieces, 3):
        assert exploration.parse_final_answer(text) == _old_parse_final_answer(text), repr(text)


def test_final_answer_scans_unclosed_openers_in_linear_time() -> None:
    _linear(exploration.parse_final_answer, lambda k: "<final_answer> " * k)


# cli_traces._HEREDOC and shell.split_commands -------------------------------------------------------------------------

_OLD_CLI_HEREDOC = re.compile(r"<<-?[ \t]*(['\"]?)(\w+)\1([^\n]*)\n.*?^[ \t]*\2[ \t]*$", re.S | re.M)
_OLD_SHELL_HEREDOC = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?([^\n]*)\n.*?\n\s*\1\s*(?=\n|$)", re.S)

HEREDOC_TABLE = [
    "",
    "cat a",
    "cat <<EOF\nbody\nEOF\necho done",
    "cat <<-'EOF'\n\tbody\n\tEOF\nnext",
    'cat <<"EOF" > f\nx\nEOF\n',
    "cat <<EOF\nbody\nEOF",
    "<<A\n<<A\nA\n",
    "<<A\nx\nA\n<<B\ny\nB\nz",
    "<<A\nA\n",
    "<<A\n\nA\n",
    "<<A\nbody\nB\n",
    "<<A\nbody",
    "<<A",
    "A\n<<A\nbody",
    "B\n<<A\nbody\nB\n",
    "<<AB\nx\nA\n",
    "<<AB\nx\nAB\nA\n",
    "<<A rest\nx\n  A  \n tail",
    "<<A\nx\nAB\nA\nz",
    "<<'A\nx\nA\n",
    "<<A\r\nx\r\nA\r\n",
    "<<A\nx\nA \n\n\nB",
    "<<A\nx\n\n  A\n\n",
    "<<\nA\nx\nA\n",
    "echo <<A\n<<B\nx\nB\nA\n",
    "<<'AB'\nx\nA\n",
    '<<"AB"\nx\nA\nAB\nz',
    "<<'AB\nx\nA\n",
    "<<AB'\nx\nA\n",
    "<<A1\nA1\n<<A2\nA2\nx\nA2\n",
]


def _cli_heredocs(text: str) -> str:
    return cli_traces._HEREDOC.sub(lambda m: m.group(3), text)


def _old_cli_heredocs(text: str) -> str:
    return _OLD_CLI_HEREDOC.sub(lambda m: m.group(3), text)


def _shell_heredocs(text: str) -> str:
    return shell._HEREDOC.sub(lambda m: f"<<{m.group(1)}{m.group(2)}", text)


@pytest.mark.parametrize("text", HEREDOC_TABLE)
def test_cli_trace_heredocs_are_cut_as_before(text: str) -> None:
    assert _cli_heredocs(text) == _old_cli_heredocs(text)


@pytest.mark.parametrize("text", HEREDOC_TABLE)
def test_shell_heredocs_are_cut_as_before(text: str) -> None:
    assert _shell_heredocs(text) == _OLD_SHELL_HEREDOC.sub(r"<<\1\2", text)


def test_heredocs_match_the_old_regexes_on_random_scripts() -> None:
    pieces = ["<<", "<<-", "A", "B", "AB", "'", '"', "\n", "\n", " ", "\t", "\r", "x", "\n  ", "A\n", "B\n", "<<A\n",
              "<<'B'\n", "echo "]
    for text in _random_texts(pieces, 4):
        assert _cli_heredocs(text) == _old_cli_heredocs(text), repr(text)
        assert _shell_heredocs(text) == _OLD_SHELL_HEREDOC.sub(r"<<\1\2", text), repr(text)


def test_cli_trace_heredoc_scans_openers_without_a_closer_in_linear_time() -> None:
    _linear(_cli_heredocs, lambda k: "<<A\n" * k)


def test_cli_trace_heredoc_scans_openers_whose_closer_comes_before_them_in_linear_time() -> None:
    _linear(_cli_heredocs, lambda k: "A\n" + "<<A\n" * k)


def test_cli_trace_heredoc_scans_openers_of_distinct_words_in_linear_time() -> None:
    _linear(_cli_heredocs, lambda k: "".join(f"<<A{i}\n" for i in range(k)) + "B\n")


def _unrelated_closing_lines(k: int) -> str:
    """``k`` openers of ``A``, then lines of 1, 2, ... ``B``: a number of distinct lengths of the order of the text's square root."""
    return "<<A\n" * k + "".join("B" * i + "\n" for i in range(1, 16 * math.isqrt(k) + 1))


def test_cli_trace_heredoc_scans_openers_in_linear_time_among_closing_lines_of_many_lengths() -> None:
    _linear(_cli_heredocs, _unrelated_closing_lines, limit=6)  # k times the lengths grows 8-fold, linear work 4-fold


def _long_delimiters_unrelated_closing_lines(k: int) -> str:
    """About ``k`` characters: as many openers of an ``A`` word as its length m, then lines of 1 to m ``B``."""
    m = math.isqrt(k)
    return ("<<" + "A" * m + "\n") * m + "".join("B" * i + "\n" for i in range(1, m + 1))


def _long_delimiters_one_closing_length(k: int) -> str:
    """The text of :func:`_long_delimiters_unrelated_closing_lines` in size, with ``m`` closing lines of one length."""
    m = math.isqrt(k)
    return ("<<" + "A" * m + "\n") * m + ("B" * m + "\n") * m


def test_cli_trace_heredoc_scans_long_delimiters_among_closing_lines_of_many_lengths_in_linear_time() -> None:
    """The growth is set against linear work on a text of the same size: a big text slows both alike (cache misses), copying prefixes only the first."""
    def relative() -> float:
        ratio, _ = growth(lambda k: partial(_cli_heredocs, _long_delimiters_unrelated_closing_lines(k)), 200_000)
        linear, _ = growth(lambda k: partial(_cli_heredocs, _long_delimiters_one_closing_length(k)), 200_000)
        return ratio / linear

    assert min(relative() for _ in range(5)) < 1.3  # copying prefixes: 1.6 in every attempt; a busy host spoils some attempts


def test_cli_trace_heredoc_closes_an_unquoted_opener_by_a_line_of_a_prefix_of_its_delimiter_after_it() -> None:
    assert _cli_heredocs("<<ABC\nx\nAB\ny") == _old_cli_heredocs("<<ABC\nx\nAB\ny")
    assert _cli_heredocs("AB\n<<ABC\nx\n") == _old_cli_heredocs("AB\n<<ABC\nx\n")


def test_cli_trace_heredoc_scans_quoted_openers_a_shorter_closing_line_does_not_close_in_linear_time() -> None:
    _linear(_cli_heredocs, lambda k: "<<'AB'\n" * k + "A\n")


def test_split_commands_scans_heredoc_openers_without_a_closer_in_linear_time() -> None:
    _linear(shell.split_commands, lambda k: "<<A\n" * k)


def test_split_commands_scans_heredoc_openers_whose_closing_line_has_no_body_before_it_in_linear_time() -> None:
    _linear(shell.split_commands, lambda k: "".join(f"<<A{i}\nA{i}\n" for i in range(k)))


def test_split_commands_cuts_a_heredoc_body_that_has_a_closer() -> None:
    assert shell.split_commands("cat <<EOF > out.txt\nrm -rf x; y\nEOF\nls") == [("cat <<EOF > out.txt", "\n"), ("ls", "")]


# shell_words._stdout_after_stderr --------------------------------------------------------------------------------------

_OLD_STDOUT_AFTER_STDERR = re.compile(r"(?<![^\s;&|(])2>>?(?!&)[ \t]*[^\s;&|<>()]+.*?(?<![^\s;&|(])1?>&2(?!\d)")

STDERR_TABLE = [
    "",
    "cat a 2>/dev/null 1>&2",
    "cat a 2>e >&2",
    "cat a 2>e\n>&2",
    "cat a 2>e && echo >&2",
    "cat a 2>&1 >&2",
    "cat a >&2 2>e",
    "cat a 2> e 1>&2",
    "cat a 2>e;1>&2",
    "cat a 2>e 1>&22",
    "cat a 2>e x1>&2",
    "cat a 2>a1>&2",
    "2>a 2>b\n1>&2",
    "2>a\n2>b 1>&2",
    "(2>a) >&2",
    "2>>log >&2",
    "2>a >&2 >&2",
    "2>a >&2\nx",
    "x\n2>a >&2\ny",
    "2>a\nx 2>b >&2",
]


@pytest.mark.parametrize("text", STDERR_TABLE)
def test_stdout_after_stderr_reads_as_before(text: str) -> None:
    assert _stdout_after_stderr(text) == bool(_OLD_STDOUT_AFTER_STDERR.search(text))


def test_stdout_after_stderr_matches_the_old_regex_on_random_redirects() -> None:
    pieces = ["2>", "2>>", "a", "b", " ", "\n", ">&2", "1>&2", "&", ";", "|", "(", "2>&1", "1", "2", "<", ">", ")",
              "2>a", " 1>&2"]
    for text in _random_texts(pieces, 5, longest=12):
        assert _stdout_after_stderr(text) == bool(_OLD_STDOUT_AFTER_STDERR.search(text)), repr(text)


@pytest.mark.parametrize("shape, n", [(lambda k: " 2>a" * k, 2_000), (lambda k: " 2>a" * k + "\n>&2", 30_000),  # n: the
                                      (lambda k: " 2>a\n" * k + " >&2", 2_000)])  # search for a newline is fast
def test_stdout_after_stderr_scans_redirects_without_a_closer_on_their_line_in_linear_time(shape, n: int) -> None:
    _linear(_stdout_after_stderr, shape, n)


# review.last_json -----------------------------------------------------------------------------------------------------

JSON_FENCE_TABLE = [
    "",
    "no fence",
    "```json\n[1]\n```",
    "```json\n[1]\n``` and ```json\n[2]\n``` then ```json\n{broken\n```",
    "```json[1]```",
    "```json   \n\t [1]```",
    "```json\n[1]\n```json\n[2]\n```",
    "```json\n[1]",
    "```json\n[1]\n``` ```json\n[2]",
    "```JSON\n[1]\n```",
    "``` json\n[1]\n```",
    "```json```json\n[3]\n```",
    "```json\n[1]\n````json\n[2]\n```",
]


@pytest.mark.parametrize("text", JSON_FENCE_TABLE)
def test_json_fences_are_found_as_before(text: str) -> None:
    assert review._json_fences(text) == re.findall(r"```json\s*(.*?)```", text, re.S)


def test_json_fences_match_the_old_regex_on_random_text() -> None:
    pieces = ["```json", "```", "`", "\n", " ", "\t", "\x0b", " ", " ", "[1]", "x", "json"]
    for text in _random_texts(pieces, 6):
        assert review._json_fences(text) == re.findall(r"```json\s*(.*?)```", text, re.S), repr(text)


def test_last_json_takes_the_last_fence_with_a_closer() -> None:
    assert review.last_json("```json\n[1]\n``` ```json\n[2]\n``` ```json\n[3]") == [2]


def test_last_json_scans_a_fence_without_a_closer_in_linear_time() -> None:
    _linear(review.last_json, lambda k: "```json" + " " * k)


# swebench_grade.parse_pytest_options ----------------------------------------------------------------------------------


def _old_parse_pytest_options(log: str) -> dict[str, str]:
    option = re.compile(r"(.*?)\[(.*)\]")
    found: dict[str, str] = {}
    for line in log.split("\n"):
        if not swebench_grade._starts_with_status(line):
            continue
        if line.startswith("FAILED"):
            line = line.replace(" - ", " ")
        parts = line.split()
        if len(parts) <= 1:
            continue
        match = option.search(parts[1])
        name = parts[1]
        if match:
            main, opt = match.groups()
            if opt.startswith("/") and not opt.startswith("//") and "*" not in opt:
                opt = "/" + opt.split("/")[-1]
            name = f"{main}[{opt}]"
        found[name] = parts[0]
    return found


OPTION_NAMES = ["t.py::f", "t.py::f[x]", "t.py::f[a][b]", "t.py::f[a]b]", "t.py::f]a[b", "t.py::f[a[b]", "t.py::f[", "t.py::f]",
                "[x]", "[/tmp/a/b.txt]", "[", "]", "[]", "t.py::f[/tmp/a/b.txt]", "t.py::f[//srv/x]", "t.py::f[/a/*]", "t.py::f[x]y[z]",
                "t.py::f[1]-[2]", "a]b[c", "t.py::f[x-1]"]


@pytest.mark.parametrize("name", OPTION_NAMES)
def test_pytest_options_are_split_as_before(name: str) -> None:
    log = f"PASSED {name}\nFAILED {name} - boom\n"
    assert swebench_grade.parse_pytest_options(log) == _old_parse_pytest_options(log)


def test_pytest_options_match_the_old_regex_on_random_names() -> None:
    pieces = ["[", "]", "a", "/", "*", "-", "::", "x"]
    for name in _random_texts(pieces, 7, longest=10):
        log = f"PASSED t.py::{name}\n"
        assert swebench_grade.parse_pytest_options(log) == _old_parse_pytest_options(log), repr(name)


def test_pytest_options_scan_a_run_of_brackets_in_linear_time() -> None:
    _linear(lambda name: swebench_grade.parse_pytest_options(f"PASSED {name}"), lambda k: "[" * k, n=200)  # cubic


# claude_json._last_json_object ----------------------------------------------------------------------------------------


def _old_last_json_object(text: str) -> dict | None:
    if not text:
        return None
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    matches = re.findall(r"\{.*\}", text, re.DOTALL)
    if matches:
        try:
            return json.loads(matches[-1])
        except json.JSONDecodeError:
            return None
    return None


JSON_OBJECT_TABLE = [
    "",
    "no object",
    '{"a": 1}',
    'log\n{"a": 1}\ntail',
    'pre {"a": 1} mid {"b": 2} post',
    'x {"a":\n1,\n"b": {"c": 2}} y',
    '{"a": 1} }',
    '} {"a": 1}',
    "{ no closer",
    "closer only }",
    '{"a": 1} {',
    '{"a": [1, {"b": 2}]} trailing {',
    "{{}}",
    "} {",
    "{bad} {\"ok\": 1}",
]


@pytest.mark.parametrize("text", JSON_OBJECT_TABLE)
def test_the_last_json_object_is_found_as_before(text: str) -> None:
    assert claude_json._last_json_object(text) == _old_last_json_object(text)


def test_the_last_json_object_matches_the_old_regex_on_random_text() -> None:
    pieces = ["{", "}", '"a"', ":", "1", " ", "\n", ",", '{"a": 1}', "x"]
    for text in _random_texts(pieces, 8):
        assert claude_json._last_json_object(text) == _old_last_json_object(text), repr(text)


def test_the_last_json_object_scans_a_run_of_openers_in_linear_time() -> None:
    _linear(claude_json._last_json_object, lambda k: "{" * k, n=20_000)
