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
import tracemalloc
from collections.abc import Callable
from functools import partial
from typing import Any

import pytest

from dokimi import text_spans
from dokimi.context import session_log, shell
from dokimi.harnesses import claude_json, fidelity, swebench_grade
from dokimi.metrics import exploration
from dokimi.repos import cli_traces
from dokimi.repos.graders import review
from dokimi.repos.shell_words import _stdout_after_stderr
from dokimi.text_spans import pairs, replace_pairs
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

_REF_CLI_HEREDOC = re.compile(r"(?<!<)<<-?[ \t]*(['\"]?)(\w++)\1([^\n]*)\n.*?^[ \t]*\2[ \t]*$", re.S | re.M)
_REF_SHELL_HEREDOC = re.compile(r"(?<!<)<<-?\s*(['\"]?)(\w++)\1([^\n]*)\n.*?\n\s*\2\s*(?=\n|$)", re.S)

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
    "<<A",
    "<<AB\nx\nAB\nA\n",
    "<<A rest\nx\n  A  \n tail",
    "<<A\nx\nAB\nA\nz",
    "<<'A\nx\nA\n",
    "<<A\nx\nA \n\n\nB",
    "<<A\nx\n\n  A\n\n",
    "<<\nA\nx\nA\n",
    "echo <<A\n<<B\nx\nB\nA\n",
    '<<"AB"\nx\nA\nAB\nz',
    "<<A1\nA1\n<<A2\nA2\nx\nA2\n",
]

# an opener no line closes: its body runs to the end of the input, as in a shell (#930); (text, cli_traces result, shell result)
UNCLOSED_TABLE = [
    ("<<A\nbody\nB\n", "", "<<A"),
    ("<<A\nbody", "", "<<A"),
    ("A\n<<A\nbody", "A\n", "A\n<<A"),
    ("B\n<<A\nbody\nB\n", "B\n", "B\n<<A"),
    ("<<AB\nx\nA\n", "", "<<AB"),
    ("<<'AB'\nx\nA\n", "", "<<AB"),
    ("<<AB'\nx\nA\n", "'", "<<AB'"),
    ("<<'AB\nx\nA\n", "<<'AB\nx\nA\n", "<<'AB\nx\nA\n"),
    ("cat <<AB\nsed -i s/x/y/ f\nA", "cat ", "cat <<AB"),
    ("cat <<AB\necho hi\nA\nrm x\n", "cat ", "cat <<AB"),
]


def _cli_heredocs(text: str) -> str:
    return cli_traces._HEREDOC.sub(lambda m: m.group(3), text)


def _ref_cli_heredocs(text: str) -> str:
    return _REF_CLI_HEREDOC.sub(lambda m: m.group(3), text)


def _shell_heredocs(text: str) -> str:
    return shell._HEREDOC.sub(lambda m: f"<<{m.group(2)}{m.group(3)}", text)


@pytest.mark.parametrize("text", HEREDOC_TABLE)
def test_cli_trace_heredocs_match_the_reference_regex(text: str) -> None:
    assert _cli_heredocs(text) == _ref_cli_heredocs(text)


@pytest.mark.parametrize("text", HEREDOC_TABLE)
def test_shell_heredocs_match_the_reference_regex(text: str) -> None:
    assert _shell_heredocs(text) == _REF_SHELL_HEREDOC.sub(r"<<\2\3", text)


@pytest.mark.parametrize(("text", "cli", "shell_cut"), UNCLOSED_TABLE)
def test_an_unclosed_heredoc_runs_to_the_end_of_the_input(text: str, cli: str, shell_cut: str) -> None:
    assert _cli_heredocs(text) == cli
    assert _shell_heredocs(text) == shell_cut


_OPENER = re.compile(r"<<-?\s*['\"]?\w")
_QUOTE_BEFORE_OPENER = re.compile(r"['\"][^\n]*<<")


def test_heredocs_match_the_reference_regex_on_random_scripts_whose_openers_are_all_closed() -> None:
    pieces = ["<<", "<<-", "A", "B", "AB", "'", '"', "\n", "\n", " ", "\t", "\r", "x", "\n  ", "A\n", "B\n", "<<A\n",
              "<<'B'\n", "echo "]
    compared = 0
    for text in _random_texts(pieces, 4):
        if _QUOTE_BEFORE_OPENER.search(text):  # a quote may hide the opener from the new code only
            continue
        cli, shell_cut = _ref_cli_heredocs(text), _REF_SHELL_HEREDOC.sub(r"<<\2\3", text)
        if not _OPENER.search(cli):
            assert _cli_heredocs(text) == cli, repr(text)
            compared += 1
        if not _OPENER.search(shell_cut):
            assert _shell_heredocs(text) == shell_cut, repr(text)
            compared += 1
    assert compared > 100


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
    _linear(_cli_heredocs, _unrelated_closing_lines, limit=32)  # k times the lengths grows 64-fold at 16k, linear work 16-fold


def _long_delimiters_unrelated_closing_lines(k: int) -> str:
    """About ``k`` characters: as many openers of an ``A`` word as its length m, then lines of 1 to m ``B``."""
    m = math.isqrt(k)
    return ("<<" + "A" * m + "\n") * m + "".join("B" * i + "\n" for i in range(1, m + 1))


def _long_delimiters_one_closing_length(k: int) -> str:
    """The text of :func:`_long_delimiters_unrelated_closing_lines` in size, with ``m`` closing lines of one length."""
    m = math.isqrt(k)
    return ("<<" + "A" * m + "\n") * m + ("B" * m + "\n") * m


def test_cli_trace_heredoc_scans_long_delimiters_among_closing_lines_of_many_lengths_in_linear_time() -> None:
    """The growth is set against linear work on a text of the same size: a big text slows both alike (cache misses), work per opener and delimiter length only the first."""
    def relative() -> float:
        ratio, _ = growth(lambda k: partial(_cli_heredocs, _long_delimiters_unrelated_closing_lines(k)), 25_000)
        linear, _ = growth(lambda k: partial(_cli_heredocs, _long_delimiters_one_closing_length(k)), 25_000)
        return ratio / linear

    assert any(relative() < 1.3 for _ in range(3))  # a busy host spoils some attempts, and an attempt that passes ends the test


@pytest.mark.parametrize(("text", "cut"), [("<<ABC\nx\nAB\ny", ""), ("AB\n<<ABC\nx\n", "AB\n"), ("cat <<AB\necho hi\nA\nrm x\n", "cat "), ("<<'AB'\nx\nA\ny", "")])
def test_cli_trace_heredoc_is_not_closed_by_a_line_naming_part_of_its_delimiter(text: str, cut: str) -> None:
    assert cli_traces._HEREDOC.sub(lambda m: m.group(3), text) == cut  # the body runs to the end, not to the prefix line (#930)


def test_cli_trace_heredoc_closes_by_the_whole_delimiter_after_a_prefix_line() -> None:
    assert cli_traces._HEREDOC.sub(lambda m: m.group(3), "cat <<AB\necho hi\nA\nrm x\nAB\nls\n") == "cat \nls\n"


def test_cli_trace_heredoc_is_closed_by_a_line_with_a_carriage_return() -> None:
    assert cli_traces._HEREDOC.sub(lambda m: m.group(3), "cat <<EOF\r\nx\r\nEOF\r\nls\r\n") == "cat \r\nls\r\n"


def test_split_commands_cuts_an_unclosed_heredoc_whose_body_has_a_prefix_line() -> None:
    assert shell.split_commands("cat <<AB\nsed -i s/x/y/ f\nA\n") == [("cat <<AB", "")]
    assert shell.split_commands("cat <<AB\necho hi\nA\nrm x\nAB\nls\n") == [("cat <<AB", "\n"), ("ls", "\n")]


def test_split_commands_keeps_the_commands_before_an_unclosed_heredoc() -> None:
    assert shell.split_commands("ls\ncat <<AB\nrm x\nA") == [("ls", "\n"), ("cat <<AB", "")]


@pytest.mark.parametrize("delimiter", ["EOF-END", "EOF.d", "a-b-c", "EOF/END", "EOF+1", "EOF:x"])
def test_a_delimiter_of_more_than_word_characters_is_closed_by_its_whole_line_and_the_commands_after_it_stay(delimiter: str) -> None:
    text = f"cat <<{delimiter}\nbody\n{delimiter}\nsed -i s/x/y/ f\n"
    assert shell.split_commands(text) == [(f"cat <<{delimiter}", "\n"), ("sed -i s/x/y/ f", "\n")]
    assert _cli_heredocs(text) == "cat \nsed -i s/x/y/ f\n"


@pytest.mark.parametrize("opener", ["cat <<EOF\\-END", "cat <<EOF\\-END > f", "cat <<'EOF'\\-END", "cat <<EOF\\\\-END"])
def test_a_delimiter_word_that_is_only_partly_read_opens_no_heredoc_and_the_closing_line_and_commands_after_it_stay(opener: str) -> None:
    text = f"{opener}\nbody\nEOF-END\nsed -i s/x/y/ f\n"
    assert [c for c, _ in shell.split_commands(text)][-1] == "sed -i s/x/y/ f"
    assert _cli_heredocs(text) == text


@pytest.mark.parametrize("opener", ["cat <<$(printf EOF)", "cat <<$(printf EOF) > f", "cat <<'EOF'(x)"])
def test_a_delimiter_word_cut_by_a_parenthesis_opens_no_heredoc_and_the_commands_after_it_stay(opener: str) -> None:
    text = f"{opener}\nbody\n$(printf EOF)\nsed -i s/x/y/ f\ncat AGENTS.md\n"
    assert [c for c, _ in shell.split_commands(text)][-2:] == ["sed -i s/x/y/ f", "cat AGENTS.md"]
    assert _cli_heredocs(text) == text


@pytest.mark.parametrize("text", ["echo \\<< /dev/null\nsed -i s/x/y/ f\ncat AGENTS.md\n", "echo \\<<EOF\nsed -i s/x/y/ f\ncat AGENTS.md\n", "echo a\\\\ \\<<EOF\nsed -i s/x/y/ f\ncat AGENTS.md\n"])
def test_an_escaped_first_angle_bracket_opens_no_heredoc_and_the_commands_after_it_stay(text: str) -> None:
    assert [c for c, _ in shell.split_commands(text)][-2:] == ["sed -i s/x/y/ f", "cat AGENTS.md"]
    assert _cli_heredocs(text) == text


def test_an_opener_after_an_escaped_backslash_still_opens_a_heredoc() -> None:
    text = "echo \\\\<<EOF\nbody\nEOF\nrm f\n"
    assert [c for c, _ in shell.split_commands(text)][-1] == "rm f"
    assert _cli_heredocs(text) == "echo \\\\\nrm f\n"


@pytest.mark.parametrize(("core", "line"), [("a", "a"), ("", " " * 9)])
def test_delimiters_that_share_a_core_are_not_each_tried_against_every_closing_line(monkeypatch: pytest.MonkeyPatch, core: str, line: str) -> None:
    """512 delimiters of one core (a blank prefix of 9 characters each, the core ``a`` or none) and 512 lines of that core: 262144 pairs on the code that tried each."""
    prefixes = [format(i, "09b").replace("0", " ").replace("1", "\t") for i in range(512)]
    reads = [0]

    def counted(*args: int) -> range:
        r = range(*args)
        reads[0] += 1 + len(r)
        return r

    monkeypatch.setattr(text_spans, "range", counted, raising=False)
    text = "".join(f"cat <<'{p}{core}'\n" for p in prefixes) + f"{line}\n" * 512
    assert [c for c, _ in shell.split_commands(text)][0].startswith("cat <<")
    assert _cli_heredocs(text).startswith("cat ")
    assert reads[0] < 40 * 1024


@pytest.mark.parametrize("text", ["cat <<<word\nsed -i s/x/y/ f\n", "cat <<<'a b' > f\nsed -i s/x/y/ f\n", "cat <<<$X\nsed -i s/x/y/ f\n"])
def test_a_here_string_is_no_heredoc_and_the_commands_after_it_stay(text: str) -> None:
    assert [c for c, _ in shell.split_commands(text)][-1] == "sed -i s/x/y/ f"
    assert _cli_heredocs(text) == text


@pytest.mark.parametrize("text", ["grep '<<EOF' x.txt\nrm f\n", "echo \"a <<EOF\"\nrm f\n", "echo 'it''s' '<<EOF'\nrm f\n", "'<<EOF' x\nrm f\n"])
def test_a_heredoc_opener_inside_a_quote_opens_nothing_and_the_commands_after_it_stay(text: str) -> None:
    assert [c for c, _ in shell.split_commands(text)][-1] == "rm f"
    assert _cli_heredocs(text) == text


@pytest.mark.parametrize("text", ["echo $((1<<3))\ncat AGENTS.md\n", "echo $(( 1 << 3 ))\ncat AGENTS.md\n", "x=$((a<<b)) y=$((1<<2))\ncat AGENTS.md\n"])
def test_a_shift_in_arithmetic_opens_no_heredoc_and_the_commands_after_it_stay(text: str) -> None:
    assert [c for c, _ in shell.split_commands(text)][-1] == "cat AGENTS.md"
    assert _cli_heredocs(text) == text


@pytest.mark.parametrize("text", ["# usage: cat <<EOF\ncat AGENTS.md\n", "echo hi # cat <<EOF\ncat AGENTS.md\n", "  \t# cat <<'a b'\ncat AGENTS.md\n", "# 'x' cat <<EOF\ncat AGENTS.md\n"])
def test_a_heredoc_opener_inside_a_comment_opens_nothing_and_the_commands_after_it_stay(text: str) -> None:
    assert [c for c, _ in shell.split_commands(text)][-1] == "cat AGENTS.md"
    assert _cli_heredocs(text) == text


@pytest.mark.parametrize("text", ["# note\ncat <<EOF\nbody\nEOF\nrm f\n", "echo a#b <<EOF\nbody\nEOF\nrm f\n", "echo $# <<EOF\nbody\nEOF\nrm f\n",
                                  "echo '# x' <<EOF\nbody\nEOF\nrm f\n", "echo \\# <<EOF\nbody\nEOF\nrm f\n", "echo 'a' # '\ncat <<EOF\nbody\nEOF\nrm f\n",
                                  "# a comment ends at its line, a backslash ending it continues nothing\\\ncat <<EOF\nbody\nEOF\nrm f\n"])
def test_a_hash_that_starts_no_comment_or_a_comment_ended_by_its_line_does_not_hide_an_opener(text: str) -> None:
    assert [c for c, _ in shell.split_commands(text)][-1] == "rm f"
    assert _cli_heredocs(text).endswith("\nrm f\n")
    assert "body" not in _cli_heredocs(text)


def test_a_heredoc_after_arithmetic_is_still_cut_and_an_unclosed_arithmetic_opener_hides_nothing() -> None:
    text = "echo $((1<<3)); cat <<EOF\nbody\nEOF\nrm f\n"
    assert [c for c, _ in shell.split_commands(text)][-1] == "rm f"
    assert _cli_heredocs(text) == "echo $((1<<3)); cat \nrm f\n"
    assert [c for c, _ in shell.split_commands("echo $((1 <<EOF\nrm f\n")] == ["echo $((1 <<EOF"]  # no `))`: nothing is blanked


def test_a_quote_closed_before_an_opener_does_not_hide_it() -> None:
    text = "echo 'a' ; cat <<EOF\nbody\nEOF\nrm f\n"
    assert shell.split_commands(text)[-1] == ("rm f", "\n")
    assert _cli_heredocs(text) == "echo 'a' ; cat \nrm f\n"
    assert _cli_heredocs("echo \\' <<EOF\nbody\nEOF\nrm f\n") == "echo \\' \nrm f\n"
    assert _cli_heredocs("echo '\\' <<EOF\nbody\nEOF\nrm f\n") == "echo '\\' \nrm f\n"


@pytest.mark.parametrize("quote", ["'", '"'])
def test_a_quoted_delimiter_with_a_space_is_closed_by_its_whole_line_and_the_commands_after_it_stay(quote: str) -> None:
    text = f"cat <<{quote}a b{quote}\nx\na b\nrm f\n"
    assert [c for c, _ in shell.split_commands(text)] == [f"cat <<{quote}a b{quote}", "rm f"]
    assert _cli_heredocs(text) == "cat \nrm f\n"
    assert [c for c, _ in shell.split_commands(f"cat <<{quote}a b{quote}\nx\na\nrm f\n")] == [f"cat <<{quote}a b{quote}"]  # `a` closes nothing
    assert _cli_heredocs(f"cat <<{quote}a b{quote}\nx\na\nrm f\n") == "cat "


@pytest.mark.parametrize(("opener", "closing"), [("<<\"a'b\"", "a'b"), ("<<'a\"b'", 'a"b'), ('<<"a;b"', "a;b"), ("<<'a&&b'", "a&&b")])
def test_a_quoted_delimiter_keeps_its_quotes_in_the_command_and_the_commands_after_it_are_split_out(opener: str, closing: str) -> None:
    text = f"cat {opener}\nx\n{closing}\nsed -i s/x/y/ f\n"
    assert shell.split_commands(text) == [(f"cat {opener}", "\n"), ("sed -i s/x/y/ f", "\n")]
    assert _cli_heredocs(text) == "cat \nsed -i s/x/y/ f\n"


@pytest.mark.parametrize("opener", ['cat <<"a\\$b"', 'cat <<"a\\"b"', 'cat <<"a\\\\b"', 'cat <<"a\\$b" > f'])
def test_a_double_quoted_delimiter_with_a_backslash_opens_no_heredoc_and_the_commands_after_it_stay(opener: str) -> None:
    text = f"{opener}\nx\na$b\nsed -i s/x/y/ f\ncat AGENTS.md\n"
    assert [c for c, _ in shell.split_commands(text)][-2:] == ["sed -i s/x/y/ f", "cat AGENTS.md"]
    assert _cli_heredocs(text) == text


@pytest.mark.parametrize(("opener", "closing"), [("<<' a'", " a"), ("<<'a '", "a "), ("<<' '", " "), ('<<" a b "', " a b "), ("<<' a'", " a\r")])
def test_a_quoted_delimiter_with_outer_whitespace_is_closed_by_its_whole_line_and_the_commands_after_it_stay(opener: str, closing: str) -> None:
    text = f"cat {opener}\nx\n{closing}\nsed -i s/x/y/ f\n"
    assert [c for c, _ in shell.split_commands(text)][-1] == "sed -i s/x/y/ f"
    assert _cli_heredocs(text) == "cat \nsed -i s/x/y/ f\n"


@pytest.mark.parametrize(("opener", "closing"), [("<<'a '", "a \r"), ("<<' '", " \r"), ("<<' a '", " a \r"), ("<<'a '", "a  "), ("<<'a '", "  a \r\r"), ("<<' a'", "  a")])
def test_a_quoted_delimiter_with_outer_whitespace_is_closed_by_a_line_with_more_whitespace_or_a_carriage_return(opener: str, closing: str) -> None:
    text = f"cat {opener}\nx\n{closing}\nsed -i s/x/y/ f\n"
    assert [c for c, _ in shell.split_commands(text)][-1] == "sed -i s/x/y/ f"
    assert _cli_heredocs(text) == "cat \nsed -i s/x/y/ f\n"


def test_cli_trace_heredoc_is_not_closed_by_a_line_whose_outer_character_is_no_blank_it_allows() -> None:
    text = "cat <<EOF\nx\n\u00a0EOF\ncat AGENTS.md\n"
    assert _cli_heredocs(text) == "cat "  # the body runs to the end: a no-break space is no blank of the closing line
    assert shell.split_commands(text) == [("cat <<EOF", "\n"), ("cat AGENTS.md", "\n")]  # the `\s` of that regex reads it as one


def test_a_quote_inside_the_other_quote_does_not_close_it_before_an_opener() -> None:
    text = "echo \"it's\" <<EOF\nbody\nEOF\nrm f\n"
    assert _cli_heredocs(text) == "echo \"it's\" \nrm f\n"


def test_a_heredoc_that_a_line_closes_right_after_its_opener_leaves_the_commands_after_it() -> None:
    assert shell.split_commands("cat <<A\nA\nrm x\n") == [("cat <<A", "\n"), ("A", "\n"), ("rm x", "\n")]


@pytest.mark.parametrize("delimiter", ["EOF", "Z" * 80])
def test_cli_trace_heredoc_keeps_no_line_that_is_no_delimiter_in_memory(delimiter: str) -> None:
    """A closed heredoc of 52000 one-word lines of 80 characters (4.2 MB) took 1.1 GB when every line word was kept, also under an 80-character delimiter (#933 review)."""
    rng = random.Random(0)
    body = "\n".join(rng.randbytes(40).hex() for _ in range(52_000))
    text = f"cat <<{delimiter} > f\n{body}\n{delimiter}\n"
    tracemalloc.start()
    try:
        cut = _cli_heredocs(text)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert cut == _ref_cli_heredocs(text) == "cat  > f\n"
    assert peak < 40 * len(text)  # keeping every line word took 1.1 GB, 260 bytes per character


def test_a_delimiter_in_the_rest_of_an_earlier_opener_line_does_not_close_that_opener() -> None:
    text = "<<A <<LONGER\nx\nLONGER\n"
    assert _cli_heredocs(text) == " <<LONGER"
    assert _shell_heredocs(text) == "<<A <<LONGER"


def test_cli_trace_heredoc_scans_many_openers_on_one_line_of_a_closed_heredoc_in_linear_time() -> None:
    """Every ``<<A`` has a newline after it, so each opener used to scan the rest of the line for it (#933 review)."""
    _linear(_cli_heredocs, lambda k: "<<A " * k + "\nbody\nA\n", n=40_000)
    _linear(_cli_heredocs, lambda k: "cat <<EOF\n" + "<<A " * k + "\nEOF\n", n=40_000)


def test_split_commands_scans_many_openers_on_one_line_of_a_closed_heredoc_in_linear_time() -> None:
    _linear(shell.split_commands, lambda k: "cat <<EOF\n" + "<<A " * k + "\nEOF\n", n=20_000)


def test_heredocs_with_openers_on_one_line_match_the_reference_regex() -> None:
    for text in ("<<A <<B <<A \nbody\nA\n", "cat <<EOF\n<<A <<A \nEOF\nx", "<<A <<B", "x <<A <<B \nA\nB\n"):
        assert _cli_heredocs(text) == _ref_cli_heredocs(text), repr(text)
        assert _shell_heredocs(text) == _REF_SHELL_HEREDOC.sub(r"<<\2\3", text), repr(text)


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


# text_spans.pairs: closer positions read per opener ---------------------------------------------------------------------


class _CountedMatch:
    """A closer match that counts each read of its position."""

    def __init__(self, m: re.Match[str], reads: list[int]) -> None:
        self._m, self._reads, self.re = m, reads, m.re

    def start(self) -> int:
        self._reads[0] += 1
        return self._m.start()

    def end(self) -> int:
        return self._m.end()

    def group(self, i: int) -> str:
        return self._m.group(i)


class _CountedCloser:
    def __init__(self, closer: re.Pattern[str], reads: list[int]) -> None:
        self._closer, self._reads = closer, reads

    def finditer(self, text: str) -> list[_CountedMatch]:
        return [_CountedMatch(m, self._reads) for m in self._closer.finditer(text)]


def _closer_reads(text: str, opener: str, closer: str) -> int:
    reads = [0]
    list(pairs(text, re.compile(opener), _CountedCloser(re.compile(closer), reads)))  # type: ignore[arg-type]
    return reads[0]


@pytest.mark.parametrize("n", [1_000, 4_000])
def test_pairs_reads_each_closer_position_a_bounded_number_of_times_for_openers_after_all_closers(n: int) -> None:
    reads = _closer_reads("</r>" * n + "<r>" * n, "<r>", "</r>")
    assert reads < 3 * n  # a binary search per opener reads about n * log2(n)


def test_pairs_reads_each_closer_position_a_bounded_number_of_times_for_complete_pairs() -> None:
    n = 4_000
    assert _closer_reads("<r></r>" * n, "<r>", "</r>") < 3 * n


def test_pairs_pairs_an_opener_that_ends_before_an_earlier_skipped_one_did() -> None:
    found = [(o.group(), c.group()) for o, c in pairs("abcd", re.compile("abcd|b"), re.compile("c"))]
    assert found == [("b", "c")]
