# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A heredoc opener next to backtick substitutions, ``${...}`` expansions and long continuation chains is read apart from the quotes, comments and bodies around it (#930), in both consumers of the heredoc pattern."""

from __future__ import annotations

import pytest

from dokimi.context import shell
from dokimi.repos import cli_traces


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


def test_a_comment_in_a_backtick_substitution_opens_no_heredoc() -> None:
    text = "echo `echo a # <<EOF\n`\ncat AGENTS.md\n"
    assert [c for c, _ in shell.split_commands(text)][-1] == "cat AGENTS.md"
    assert _cli_heredocs(text) == text


def test_a_long_chain_of_line_continuations_before_a_comment_is_read_without_recursion() -> None:
    text = "echo hi " + "\\\n" * 1500 + "# <<EOF\ncat AGENTS.md\n"
    assert [c for c, _ in shell.split_commands(text)][-1] == "cat AGENTS.md"
    assert _cli_heredocs(text) == text
