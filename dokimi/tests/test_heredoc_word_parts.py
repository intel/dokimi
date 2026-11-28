# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A heredoc delimiter word of several quoted and unquoted pieces closes on its quote-removed text (#1146).

The readers of a heredoc opener (``shell.split_commands``, the heredoc strip of ``shell_walk`` and the prefilter's ``_without_heredoc_bodies``) must keep the commands after the heredoc.
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


SED = "sed -i s/x/y/ f"


@pytest.mark.parametrize("word", ['EOF"-END"', "'EOF'-END", '"EOF"-END', "EOF'-END'", '"EO"F-END', "E'O'F-END", '"EOF-"END'])
def test_a_word_of_several_pieces_closes_on_its_text_without_quotes(word: str) -> None:
    text = f"cat <<{word}\nbody\nEOF-END\n{SED}\n"
    assert _split(text) == [f"cat <<{word}", SED]
    assert _walk(text) == f"cat \n{SED}\n"
    assert _without_heredoc_bodies(text) == f"cat <<{word}\n{SED}\n"


def test_quoted_pieces_join_into_one_word() -> None:
    text = f"cat <<'A''B'\nbody\nAB\n{SED}\n"
    assert _split(text) == ["cat <<'A''B'", SED]
    assert _walk(text) == f"cat \n{SED}\n"
    assert _without_heredoc_bodies(text) == f"cat <<'A''B'\n{SED}\n"


def test_a_quoted_piece_with_a_blank_is_part_of_the_word() -> None:
    text = f'cat <<"a b"-c\nbody\na b-c\n{SED}\n'
    assert _split(text) == ['cat <<"a b"-c', SED]
    assert _walk(text) == f"cat \n{SED}\n"
    assert _without_heredoc_bodies(text) == f'cat <<"a b"-c\n{SED}\n'


def test_a_line_naming_the_first_piece_does_not_close_the_heredoc() -> None:
    text = "cat <<EOF'-END'\nEOF\nrm x\nEOF-END\nls\n"
    assert _split(text) == ["cat <<EOF'-END'", "ls"]
    assert _walk(text) == "cat \nls\n"
    assert _without_heredoc_bodies(text) == "cat <<EOF'-END'\nls\n"


def test_a_heredoc_of_several_pieces_that_no_line_closes_runs_to_the_end() -> None:
    text = "cat <<EOF'-END'\nbody\nEOF\nrm x\n"
    assert _split(text) == ["cat <<EOF'-END'"]
    assert _walk(text) == "cat "
    assert _without_heredoc_bodies(text) == "cat <<EOF'-END'"


def test_a_quote_that_never_closes_is_no_piece_of_the_word() -> None:
    text = f"cat <<AB'\nbody\nAB\n{SED}\n"
    assert _split(text) == [f"cat <<AB'\n{SED}"]
    assert _walk(text) == f"cat '\n{SED}\n"
    assert _without_heredoc_bodies(text) == f"cat <<AB'\n{SED}\n"


def test_a_word_of_empty_quotes_opens_nothing() -> None:
    text = "cat <<''\nls\n"
    assert _split(text) == ["cat <<''", "ls"]
    assert _walk(text) == text
