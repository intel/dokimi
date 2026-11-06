# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`trace prefilter` on shell text: quoted brackets, comments, here-strings, redirections and heredocs."""

from __future__ import annotations

import time

import pytest

from dokimi.context.prefilter import _without_heredoc_bodies
from tests.test_context_prefilter import CI, _prompt, _report, _row, _session, _tool


def test_a_stop_word_only_rule_with_an_unmatchable_key_is_a_claim_that_is_not_measurable():
    rule = "- Use `tests/outcomes/*.json`.\n"
    row = _row(_report([_session(_prompt("anything at all"))], rule), "Use")
    assert row["acted"] is None and "acted_not_measurable" in row["flags"]


def test_a_quoted_bracket_in_a_command_key_is_literal_so_the_command_is_counted():
    rule = "- Run `pip install '.[dev]'` when installing dependencies.\n"
    session = _session(_prompt("install the dependencies"), _tool("Bash", command="pip install '.[dev]'"))
    assert _row(_report([session], rule), "Run")["acted"] == 1


def test_a_comment_with_an_apostrophe_does_not_hide_the_here_string_after_it():
    rule = "- Update `docs/guide.md` when behaviour changes.\n"
    command = "# don't expand input\ncat <<< docs/guide.md\n# don't open files"
    session = _session(_prompt("update behaviour changes"), _tool("Bash", command=command))
    assert _row(_report([session], rule), "Update")["acted"] == 0


# review round 6: combined-output redirections, quoted operator-like words, heredoc delimiter words

@pytest.mark.parametrize("command", ["make &>results.txt ci-local", "make &>>results.txt ci-local",
                                     "make ci-local &>results.txt"])
def test_a_combined_output_redirection_among_the_words_does_not_split_the_command(command):
    session = _session(_prompt("push the branch now"), _tool("Bash", command=command))
    assert _row(_report([session]), CI)["acted"] == 1


@pytest.mark.parametrize("command", ["make '>results.txt' ci-local", 'make ">results.txt" ci-local',
                                     "make '&>results.txt' ci-local"])
def test_a_quoted_operator_like_argument_is_an_argument_not_a_redirection(command):
    session = _session(_prompt("push the branch now"), _tool("Bash", command=command))
    assert _row(_report([session]), CI)["acted"] == 0


def test_a_guidance_key_with_a_quoted_operator_like_argument_matches_the_same_command():
    rule = "- Run `make '>results.txt' ci-local` before pushing a branch.\n"
    session = _session(_prompt("push the branch now"), _tool("Bash", command="make '>results.txt' ci-local"))
    assert _row(_report([session], rule), "Run")["acted"] == 1


@pytest.mark.parametrize(("rule", "prompt", "command", "marker"), [
    ("- Update `docs/guide.md` when behaviour changes.\n", "update behaviour changes",
     "cat <<docs/guide.md\nnotes\ndocs/guide.md", "Update"),
    ("- Run `Makefile` checks when behaviour changes.\n", "run checks when behaviour changes",
     "cat <<Makefile\nnotes\nMakefile", "Run"),
    # a write target ends at the heredoc sign glued to it: the delimiter after `<<` is not part of the target
    ("- Update `docs/guide.md` when behaviour changes.\n", "update behaviour changes",
     "cat >notes.txt<<docs/guide.md\nnotes\ndocs/guide.md", "Update"),
])
def test_a_heredoc_delimiter_word_is_not_a_path_the_command_touched(rule, prompt, command, marker):
    session = _session(_prompt(prompt), _tool("Bash", command=command))
    assert _row(_report([session], rule), marker)["acted"] == 0


def test_a_heredoc_redirected_to_a_path_is_still_a_write_to_that_path():
    rule = "- Update `docs/guide.md` when behaviour changes.\n"
    session = _session(_prompt("update behaviour changes"),
                       _tool("Bash", command="cat <<EOF >docs/guide.md\nnotes\nEOF"))
    assert _row(_report([session], rule), "Update")["acted"] == 1


@pytest.mark.parametrize("command", ["make 2 >results.txt ci-local", "make 2 > results.txt ci-local"])
def test_a_number_before_a_spaced_redirection_is_an_argument_not_a_descriptor(command):
    rule = "- Run `make 2 ci-local` before pushing a branch.\n"
    session = _session(_prompt("push the branch now"), _tool("Bash", command=command))
    assert _row(_report([session], rule), "Run")["acted"] == 1


# review: a command substitution inside double quotes runs its command; in single quotes or escaped it is text

@pytest.mark.parametrize("command", ['echo "$(make ci-local)"', 'echo "x $(make ci-local)" > log.txt',
                                     'echo "$(echo "$(make ci-local)")"', 'echo "$(date) $(make ci-local)"'])
def test_a_command_substitution_inside_double_quotes_is_a_command_that_runs(command):
    session = _session(_prompt("push the branch now"), _tool("Bash", command=command))
    assert _row(_report([session]), CI)["acted"] == 1


@pytest.mark.parametrize("command", ["echo '$(make ci-local)'", 'echo "\\$(make ci-local)"',
                                     'echo "\\\\" "$(true)" make ci-local'])
def test_a_substitution_in_single_quotes_or_escaped_is_text_not_a_run(command):
    session = _session(_prompt("push the branch now"), _tool("Bash", command=command))
    assert _row(_report([session]), CI)["acted"] == 0


@pytest.mark.parametrize("command", ["echo `make ci-local`", 'echo "`make ci-local`"', "out=`make ci-local`",
                                     "echo \"it's\" `make ci-local`", "echo `make ci-local 2>&1 | tail -1`",
                                     "echo 'a`b' `make ci-local`", "echo \\` `make ci-local`",
                                     "# don't\necho `make ci-local`"])
def test_a_backtick_substitution_is_a_command_that_runs(command):
    session = _session(_prompt("push the branch now"), _tool("Bash", command=command))
    assert _row(_report([session]), CI)["acted"] == 1


@pytest.mark.parametrize("command", ["echo '`make ci-local`'", "echo \\`make ci-local\\`",
                                     "echo hi # `make ci-local`", "echo 'a' \"\\`make ci-local\\`\""])
def test_a_backtick_in_single_quotes_a_comment_or_escaped_is_text_not_a_run(command):
    session = _session(_prompt("push the branch now"), _tool("Bash", command=command))
    assert _row(_report([session]), CI)["acted"] == 0


def test_a_path_read_by_a_substitution_inside_double_quotes_counts_as_touched():
    rule = "- Update `docs/guide.md` when behaviour changes.\n"
    session = _session(_prompt("update behaviour changes"), _tool("Bash", command='echo "$(cat docs/guide.md)"'))
    assert _row(_report([session], rule), "Update")["acted"] == 1


def test_a_quote_that_closes_on_a_later_line_hides_the_heredoc_sign_inside_it():
    command = 'echo "note\ncat <<EOF\nend"\nmake ci-local'
    session = _session(_prompt("push the branch now"), _tool("Bash", command=command))
    assert _row(_report([session]), CI)["acted"] == 1


def test_the_heredoc_scan_takes_linear_time_in_the_lines_with_a_heredoc_sign():
    command = "\n".join([": <<< x"] * 6000 + ["cat <<EOF", "body", "EOF", "make ci-local"])
    start = time.perf_counter()
    kept = _without_heredoc_bodies(command)
    assert time.perf_counter() - start < 1.0  # a re-scan of the command per line took about 5 s here
    assert kept.endswith("cat <<EOF\nmake ci-local")
