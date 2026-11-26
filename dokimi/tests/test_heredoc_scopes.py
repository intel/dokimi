# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A heredoc opener next to backtick substitutions, ``${...}`` expansions and long continuation chains is read apart from the quotes, comments and bodies around it (#930), in both consumers of the heredoc pattern."""

from __future__ import annotations

from functools import partial

import pytest

from dokimi.context import shell
from dokimi.repos import cli_traces
from tests.growth_timing import GROWTH_LIMIT, growth


def _cli_heredocs(text: str) -> str:
    return cli_traces._HEREDOC.sub(lambda m: m.group(3), text)


@pytest.mark.parametrize("text", ['echo "`printf "%s" "<<EOF"`"\ncat AGENTS.md\n', "echo `printf '%s' \"a <<EOF\" \"b\"` x\ncat AGENTS.md\n"])
def test_quotes_in_a_backtick_substitution_are_apart_from_the_quote_around_it_so_a_shift_in_them_opens_no_heredoc(text: str) -> None:
    assert [c for c, _ in shell.split_commands(text)][-1] == "cat AGENTS.md"
    assert _cli_heredocs(text) == text


@pytest.mark.parametrize("text", ["echo `cat <<EOF\nbody\nEOF\n`\nrm f\n", 'echo "`cat <<EOF\nbody\nEOF\n`"\nrm f\n'])
def test_a_heredoc_inside_a_backtick_substitution_is_still_cut(text: str) -> None:
    assert [c for c, _ in shell.split_commands(text)][-1] == "rm f"
    assert "body" not in _cli_heredocs(text)


@pytest.mark.parametrize("text", ["echo ${x:- #}; cat <<EOF\nsed -i s/x/y/ f\nEOF\ncat AGENTS.md\n", 'echo "${x:-#} ${#y}"; cat <<EOF\nsed -i s/x/y/ f\nEOF\ncat AGENTS.md\n',
                                  "echo ${x:-'}' #}; cat <<EOF\nsed -i s/x/y/ f\nEOF\ncat AGENTS.md\n"])
def test_a_hash_in_a_parameter_expansion_starts_no_comment_so_a_heredoc_after_it_is_cut(text: str) -> None:
    assert [c for c, _ in shell.split_commands(text)][-1] == "cat AGENTS.md"
    assert not any(c.startswith("sed") for c, _ in shell.split_commands(text))
    assert "sed" not in _cli_heredocs(text)


def test_a_hash_after_a_parameter_expansion_still_starts_a_comment() -> None:
    text = "echo ${x} # <<EOF\ncat AGENTS.md\n"
    assert [c for c, _ in shell.split_commands(text)][-1] == "cat AGENTS.md"
    assert _cli_heredocs(text) == text


@pytest.mark.parametrize("text", ['echo "$(cat <<EOF\nbody\nEOF\n)"\n((x=1<<3))\ncat AGENTS.md\n', 'echo "$(cat <<EOF\nbody\nEOF\n)"\necho $((1<<3))\ncat AGENTS.md\n',
                                  'echo "`cat <<EOF\nbody\nEOF\n`"\n((x=1<<3))\ncat AGENTS.md\n'])
def test_a_heredoc_body_in_a_quoted_substitution_leaves_the_quotes_around_it_open_for_what_follows(text: str) -> None:
    assert [c for c, _ in shell.split_commands(text)][-1] == "cat AGENTS.md"
    assert "body" not in _cli_heredocs(text)
    assert "cat AGENTS.md" in _cli_heredocs(text)


def test_a_backtick_in_single_quotes_inside_a_substitution_is_plain_text() -> None:
    text = "echo \"$(echo '`')\" <<EOF\ncat AGENTS.md\nEOF\nrm f\n"
    assert [c for c, _ in shell.split_commands(text)][-1] == "rm f"
    assert "cat AGENTS.md" not in _cli_heredocs(text)


def test_a_dollar_paren_in_single_quotes_inside_a_substitution_is_plain_text() -> None:
    text = "echo \"$(echo '$(')\" <<EOF\ncat AGENTS.md\nEOF\nrm f\n"
    assert [c for c, _ in shell.split_commands(text)][-1] == "rm f"
    assert "cat AGENTS.md" not in _cli_heredocs(text)


def test_a_comment_in_a_backtick_substitution_opens_no_heredoc() -> None:
    text = "echo `echo a # <<EOF\n`\ncat AGENTS.md\n"
    assert [c for c, _ in shell.split_commands(text)][-1] == "cat AGENTS.md"
    assert _cli_heredocs(text) == text


@pytest.mark.parametrize("text", ["echo \"${x:-it's}\"; cat <<EOF\nsed -i s/x/y/ f\nEOF\nrm f\n", "echo \"${x:-it's \"a b\"}\"; cat <<EOF\nsed -i s/x/y/ f\nEOF\nrm f\n"])
def test_a_single_quote_in_a_double_quoted_expansion_is_plain_text_so_a_heredoc_after_it_is_cut(text: str) -> None:
    assert [c for c, _ in shell.split_commands(text)][-1] == "rm f"
    assert not any(c.startswith("sed") for c, _ in shell.split_commands(text))
    assert "sed" not in _cli_heredocs(text)


def test_a_single_quote_in_an_expansion_nested_in_a_double_quoted_expansion_is_plain_text() -> None:
    text = "echo \"${x:-${y:-it's}}\"; cat <<EOF\nsed -i s/x/y/ f\nEOF\nrm f\n"
    assert [c for c, _ in shell.split_commands(text)][-1] == "rm f"
    assert not any(c.startswith("sed") for c, _ in shell.split_commands(text))
    assert "sed" not in _cli_heredocs(text)


def test_a_single_quote_in_an_expansion_nested_in_a_substitution_in_a_double_quoted_expansion_still_quotes() -> None:
    text = "echo \"${x:-$(echo ${y:-'}'})}\" <<EOF\ncat AGENTS.md\nEOF\nrm f\n"
    assert [c for c, _ in shell.split_commands(text)][-1] == "rm f"
    assert "cat AGENTS.md" not in _cli_heredocs(text)


def test_a_single_quote_in_an_unquoted_expansion_still_quotes() -> None:
    text = "echo ${x:-'}'} <<EOF\ncat AGENTS.md\nEOF\nrm f\n"
    assert [c for c, _ in shell.split_commands(text)][-1] == "rm f"
    assert "cat AGENTS.md" not in _cli_heredocs(text)


def test_a_heredoc_in_a_substitution_in_arithmetic_is_cut_and_the_shift_after_it_opens_none() -> None:
    text = "echo $(( $(cat <<EOF >/dev/null; echo 1\n))\nEOF\n) << 3 ))\ncat AGENTS.md\n"
    assert [c for c, _ in shell.split_commands(text)][-1] == "cat AGENTS.md"
    assert "cat AGENTS.md" in _cli_heredocs(text)
    assert "))\nEOF" not in _cli_heredocs(text)


def test_a_heredoc_in_two_nested_substitutions_in_arithmetic_is_cut_and_the_shift_after_it_opens_none() -> None:
    text = "echo $(( $(echo $(cat <<EOF\n))\nEOF\n) >/dev/null; echo 1) << 3 ))\ncat AGENTS.md\n"
    assert [c for c, _ in shell.split_commands(text)][-1] == "cat AGENTS.md"
    assert "cat AGENTS.md" in _cli_heredocs(text)
    assert "))\nEOF" not in _cli_heredocs(text)


def test_a_shift_in_arithmetic_nested_two_deep_in_a_substitution_in_arithmetic_opens_no_heredoc() -> None:
    text = "echo $(( $(echo $(echo $(( (1) << 3 )))) + 1 ))\ncat AGENTS.md\n"
    assert [c for c, _ in shell.split_commands(text)][-1] == "cat AGENTS.md"
    assert _cli_heredocs(text) == text


def test_a_heredoc_body_after_an_arithmetic_expansion_is_skipped_before_its_quote_is_read() -> None:
    text = "echo $((1<<3))\ncat <<EOF\nit's\nEOF\n((x=1<<3))\ncat AGENTS.md\n"
    assert [c for c, _ in shell.split_commands(text)][-1] == "cat AGENTS.md"
    assert "it's" not in _cli_heredocs(text)
    assert "cat AGENTS.md" in _cli_heredocs(text)


def test_a_shift_in_arithmetic_nested_in_a_substitution_in_arithmetic_opens_no_heredoc() -> None:
    text = "echo $(( $(echo $((1 << 3))) + 1 ))\ncat AGENTS.md\n"
    assert [c for c, _ in shell.split_commands(text)][-1] == "cat AGENTS.md"
    assert _cli_heredocs(text) == text


def test_a_shift_before_a_substitution_whose_heredoc_body_has_a_quote_opens_no_heredoc() -> None:
    text = "echo $((1 << 3 + $(cat <<EOF >/dev/null; echo 0\nit's\nEOF\n) ))\n((x=1<<3))\ncat AGENTS.md\n"
    assert [c for c, _ in shell.split_commands(text)][-2:] == ["((x=1<<3))", "cat AGENTS.md"]
    assert "it's" not in _cli_heredocs(text)
    assert "<< 3" in _cli_heredocs(text)
    assert "cat AGENTS.md" in _cli_heredocs(text)


def test_an_escaped_opener_before_the_real_one_on_a_substitution_line_in_arithmetic_names_no_body() -> None:
    text = "echo $((1 << 3 + $(echo \\<<X; cat <<EOF >/dev/null; echo 0\nit's\nEOF\n) ))\n((x=1<<3))\ncat AGENTS.md\n"
    assert [c for c, _ in shell.split_commands(text)][-2:] == ["((x=1<<3))", "cat AGENTS.md"]
    assert "it's" not in _cli_heredocs(text)
    assert "cat AGENTS.md" in _cli_heredocs(text)


@pytest.mark.parametrize("text", [
    'echo $((1 << 3 + $(cat <<"${" >/dev/null; echo 0\nbody\n${\n) ))\ncat AGENTS.md\n',
    "echo $((1 << 3 + $(cat <<EOF >/dev/null; printf '%s' 0\nit's\nEOF\n) ))\n((x=1<<3))\ncat AGENTS.md\n",
    "echo $((1 << 3 + $(cat <<EOF >/dev/null # note\nit's\nEOF\n) ))\ncat AGENTS.md\n",
])
def test_a_substitution_in_arithmetic_whose_heredoc_has_a_literal_delimiter_a_quote_or_a_comment_on_its_line_names_its_body(text: str) -> None:
    assert [c for c, _ in shell.split_commands(text)][-1] == "cat AGENTS.md"
    assert "body" not in _cli_heredocs(text)
    assert "it's\nEOF" not in _cli_heredocs(text)
    assert _cli_heredocs(text).endswith("cat AGENTS.md\n")
    assert "<< 3" in _cli_heredocs(text)


@pytest.mark.parametrize("text", ["echo $(( $( ((x=1<<3)); echo 1) + 1 ))\ncat AGENTS.md\n", "echo $(( `((x=1<<3)); echo 1` + 1 ))\ncat AGENTS.md\n"])
def test_a_bare_arithmetic_command_in_a_substitution_in_arithmetic_opens_no_heredoc(text: str) -> None:
    assert [c for c, _ in shell.split_commands(text)][-1] == "cat AGENTS.md"
    assert _cli_heredocs(text) == text


@pytest.mark.parametrize(("opener", "closer"), [('"${"', "${"), ("'${'", "${"), ('"$("', "$("), ('"$(("', "$((")])
def test_a_delimiter_that_looks_like_expansion_syntax_leaves_the_next_heredoc_cut(opener: str, closer: str) -> None:
    text = f"cat <<{opener}\nbody\n{closer}\ncat <<EOF\nsed -i s/x/y/ f\nEOF\nrm f\n"
    assert [c for c, _ in shell.split_commands(text)][-1] == "rm f"
    assert not any(c.startswith("sed") for c, _ in shell.split_commands(text))
    assert "sed" not in _cli_heredocs(text)


def test_a_delimiter_that_looks_like_expansion_syntax_leaves_arithmetic_after_it_read_apart() -> None:
    text = 'cat <<"${"\nbody\n${\n((x=1<<3))\ncat AGENTS.md\n'
    assert [c for c, _ in shell.split_commands(text)][-1] == "cat AGENTS.md"
    assert "cat AGENTS.md" in _cli_heredocs(text)


def test_a_long_chain_of_line_continuations_before_a_comment_is_read_without_recursion() -> None:
    text = "echo hi " + "\\\n" * 1500 + "# <<EOF\ncat AGENTS.md\n"
    assert [c for c, _ in shell.split_commands(text)][-1] == "cat AGENTS.md"
    assert _cli_heredocs(text) == text


@pytest.mark.parametrize("text", ["((cat <<EOF\nsed -i s/x/y/ f\nEOF\n) | cat)\nrm g\n", "echo $(( $( ((cat <<EOF\nsed -i s/x/y/ f\nEOF\n) | cat) ) + 1 ))\nrm g\n"])
def test_a_double_parenthesis_that_no_double_parenthesis_closes_on_its_line_is_a_subshell_and_its_heredoc_is_cut(text: str) -> None:
    assert not [c for c, _ in shell.split_commands(text) if c.startswith("sed")]
    assert "sed" not in _cli_heredocs(text)
    assert [c for c, _ in shell.split_commands(text)][-1] == "rm g"


def test_two_arithmetic_commands_on_one_substitution_line_inside_arithmetic_both_close_on_that_line() -> None:
    text = "echo $(( $( ((a=1<<3)); ((b=1<<3)); echo 1) + 1 ))\ncat <<EOF\nsed -i s/x/y/ f\nEOF\nrm g\n"
    assert not [c for c, _ in shell.split_commands(text) if c.startswith("sed")]
    assert _cli_heredocs(text) == "echo $(( $( ((a=1<<3)); ((b=1<<3)); echo 1) + 1 ))\ncat \nrm g\n"


def test_many_double_parentheses_on_a_line_with_no_closer_are_scanned_in_linear_time() -> None:
    ratio, _ = growth(lambda k: partial(shell.split_commands, "(" * k + "\ncat <<EOF\nbody\nEOF\n"), 2_000)
    assert ratio < GROWTH_LIMIT


def test_an_escaped_apostrophe_in_an_ansi_c_string_does_not_hide_the_heredoc_after_it() -> None:
    text = "printf '%s\\n' $'don\\'t'\ncat <<EOF\nsed -i s/x/y/ f\nEOF\nrm g\n"
    assert _cli_heredocs(text) == "printf '%s\\n' $'don\\'t'\ncat \nrm g\n"
    assert not [c for c, _ in shell.split_commands(text) if "sed" in c]
    assert _cli_heredocs("echo $'a\\\\' <<EOF\nbody\nEOF\nrm g\n") == "echo $'a\\\\' \nrm g\n"


@pytest.mark.parametrize("shift", ["$((1 << EOF))", "$((1 << 3))"])
def test_a_shift_in_arithmetic_nested_on_a_substitution_line_inside_arithmetic_opens_no_heredoc_and_the_commands_after_it_stay(shift: str) -> None:
    text = f"echo $(( $(echo {shift}\nfoo\n) + 1 ))\nsed -i s/x/y/ f\nrm g\n"
    assert [c for c, _ in shell.split_commands(text)][-2:] == ["sed -i s/x/y/ f", "rm g"]
    assert _cli_heredocs(text) == text
