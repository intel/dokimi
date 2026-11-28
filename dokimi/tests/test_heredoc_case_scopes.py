# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The ``)`` of a ``case`` pattern in a ``$(...)`` closes no substitution, so a quote after it is still the substitution's own (#1233).

The readers of a heredoc opener (``shell.split_commands``, the heredoc strip of ``shell_walk`` and the prefilter's ``_without_heredoc_bodies``) must keep the commands after it.
"""

from __future__ import annotations

import pytest

from dokimi.context import shell
from dokimi.context.prefilter import _without_heredoc_bodies
from dokimi.repos import shell_walk


def _split(text: str) -> list[str]:
    return [command for command, _ in shell.split_commands(text)]


def _walk(text: str) -> str:
    return shell_walk._HEREDOC.sub(lambda m: m.group(3), text)


CASE_LINES = [
    pytest.param("""echo "$(case x in x) printf '%s' "<<EOF";; esac)\"""", id="a pattern ends in a )"),
    pytest.param("""echo "$(case x in (x) printf '%s' "<<EOF";; esac)\"""", id="a parenthesised pattern balances"),
    pytest.param("""echo "$(case x in x) case y in y) printf '%s' "<<EOF";; esac;; esac)\"""", id="a case in a case"),
    pytest.param("""echo "$(case x in x) echo esac "<<EOF";; esac)\"""", id="esac as an argument ends nothing"),
    pytest.param("""echo "$(if true; then case x in x) printf '%s' "<<EOF";; esac; fi)\"""", id="a case after then"),
    pytest.param("""echo "$(printf '%s' "<<EOF")\"""", id="no case: the quotes of a substitution are its own"),
]


@pytest.mark.parametrize("line", CASE_LINES)
def test_a_quote_in_a_substitution_hides_the_heredoc_opener_after_a_case_pattern(line: str) -> None:
    text = f"{line}\ncat AGENTS.md\n"
    assert _split(text) == [line, "cat AGENTS.md"]
    assert _walk(text) == text
    assert _without_heredoc_bodies(text) == text


@pytest.mark.parametrize("lead", ["if x; then", "if x; then :; else", "if x; then :; elif y; then", "while x; do", "until x; do", "while", "true;", "true;    ", "true\t;\t"])
def test_a_case_after_a_keyword_or_a_separator_starts_a_command(lead: str) -> None:
    line = f"""echo "$({lead} case x in x) printf '%s' "<<EOF";; esac)\""""
    text = f"{line}\ncat AGENTS.md\n"
    assert _split(text) == [line, "cat AGENTS.md"]
    assert _walk(text) == text
    assert _without_heredoc_bodies(text) == text


def test_an_operator_without_a_word_opens_no_heredoc_in_the_prefilter() -> None:
    assert _without_heredoc_bodies("echo <<\nls\n") == "echo <<\nls\n"


def test_a_heredoc_after_the_esac_of_a_substitution_is_opened() -> None:
    line = 'echo "$(case x in x) :;; esac)" <<EOF'
    text = f"{line}\nbody\nEOF\nls\n"
    assert _split(text) == [line, "ls"]
    assert _walk(text) == 'echo "$(case x in x) :;; esac)" \nls\n'
    assert _without_heredoc_bodies(text) == f"{line}\nls\n"


def test_case_as_an_argument_opens_no_case() -> None:
    line = 'echo "$(echo case)" <<EOF'
    text = f"{line}\nbody\nEOF\nls\n"
    assert _split(text) == [line, "ls"]
    assert _walk(text) == 'echo "$(echo case)" \nls\n'
    assert _without_heredoc_bodies(text) == f"{line}\nls\n"
