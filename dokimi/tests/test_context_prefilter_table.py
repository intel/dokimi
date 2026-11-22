# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`trace prefilter`: the text table prints the counts the --json report carries."""

from __future__ import annotations

from dokimi.context.prefilter import render_table
from tests.test_context_prefilter import _prompt, _report, _row, _session, _tool


def test_the_table_prints_the_sessions_a_rule_was_acted_on_in():
    """#937: docs/session-prefilter.md says the table prints the same counts as --json, `sessions_acted` included."""
    rule = "- Zebra quokka `make ci-local`\n"
    acting = [_session(_prompt("hello there"), _tool("Bash", command="make ci-local"), name=f"a{i}") for i in range(3)]
    idle = [_session(_prompt("hello there"), name=f"i{i}") for i in range(6)]
    report = _report(acting + idle, rule)
    row = _row(report, "Zebra")
    assert (row["status"], row["sessions_triggered"], row["sessions_acted"]) == ("in_use", 0, 3)
    head, line = render_table(report).splitlines()[:2]
    assert "acted.sess" in head
    assert line.split()[2:4] == ["0/9", "3/9"]


def test_the_table_prints_n_a_for_the_acted_sessions_of_a_rule_that_names_no_key():
    """#937: a rule with no command or path has `sessions_acted` null; the column says n/a, as `acted` does."""
    report = _report([_session(_prompt("hello there"))], "- Keep the code tidy and short.\n")
    assert _row(report, "tidy")["sessions_acted"] is None
    assert render_table(report).splitlines()[1].split()[2:4] == ["0/1", "n/a"]


def test_the_counts_of_a_candidate_row_end_where_their_headers_end():
    """`candidate_for_ablation` is the longest status: its row keeps every count under its header."""
    rules = "- Walrus quokka `make lint-all`\n- Zebra quokka `make ci-local`\n"
    acting = [_session(_prompt("hello there"), _tool("Bash", command="make ci-local"), name=f"a{i}") for i in range(3)]
    idle = [_session(_prompt("hello there"), name=f"i{i}") for i in range(6)]
    report = _report(acting + idle, rules)
    assert _row(report, "Walrus")["status"] == "candidate_for_ablation"
    head, *rows = render_table(report).splitlines()[:3]
    for line in rows:
        for header, value in zip(("trig.sess", "acted.sess"), line.split()[2:4], strict=True):
            end = head.index(header) + len(header)
            assert line[end - len(value):end] == value
