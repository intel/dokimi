# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`context prefilter` on shell text: quoted brackets, comments, here-strings, redirections and heredocs."""

from __future__ import annotations

import pytest

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
