# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`context prefilter` on shell text: a key's quoted brackets, stop-word-only rules and comments before a here-string."""

from __future__ import annotations

from tests.test_context_prefilter import _prompt, _report, _row, _session, _tool


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
