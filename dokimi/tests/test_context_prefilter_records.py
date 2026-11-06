# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`trace prefilter`: which rows are claims, which records are the user's words, and when user_overrides fires."""

from __future__ import annotations

import pytest

from dokimi.context.prefilter import claims_from_guidance
from tests.test_context_prefilter import CI, GUIDE, _prompt, _report, _row, _session, _tool



@pytest.mark.parametrize(("corrected", "plain", "flagged"),
                         [(1, 0, False), (1, 1, True), (1, 2, False), (2, 0, True), (2, 2, True)])
def test_user_overrides_needs_two_episodes_and_corrections_on_half_of_them(corrected, plain, flagged):
    pushback = [_session(_prompt("push the branch"), _prompt("no, the branch must not go before ci"), name=f"c{i}")
                for i in range(corrected)]
    agreed = [_session(_prompt("push the branch"), _prompt("thanks, carry on"), name=f"p{i}") for i in range(plain)]
    row = _row(_report(pushback + agreed), CI)
    assert row["episodes"] == corrected + plain and row["corrected"] == corrected
    assert any(f.startswith("user_overrides") for f in row["flags"]) is flagged


def test_a_table_header_row_is_no_claim_and_its_body_rows_are():
    texts = [c.text for c in claims_from_guidance(GUIDE)]
    assert "| Command | Purpose |" not in texts and "| `pytest tests/test_x.py` | Run one file |" in texts
    aligned = "| Command | Purpose |\n|:---|---:|\n| `make ci-local` | the full gate |\n"
    assert [c.text for c in claims_from_guidance(aligned)] == ["| `make ci-local` | the full gate |"]
    broken = "| `make ci-local` | the full gate |\n---\n"  # a thematic break, not a delimiter row
    assert [c.text for c in claims_from_guidance(broken)] == ["| `make ci-local` | the full gate |"]


def test_a_subagent_brief_in_the_task_call_is_not_a_trigger():
    rule = "- Commit with `git commit -s`: the repo is public and takes DCO sign-off.\n"
    brief = "Commit with git commit -s: the repo is public and takes DCO sign-off"
    session = _session(_prompt("please fix the failing runner test"),
                       _tool("Task", description="Fix test", prompt=brief, subagent_type="general-purpose"))
    assert [e.text for e in session.events if e.kind == "tool"] == ["Fix test general-purpose"]
    assert _row(_report([session], rule), "Commit")["sessions_triggered"] == 0


@pytest.mark.parametrize("marker", ["[Request interrupted by user for tool use]", "[Request interrupted by user]"])
def test_an_interruption_marker_is_not_the_next_prompt_so_the_pushback_after_it_is_read(marker):
    rule = "- Run `make ci-local` before pushing a branch.\n"
    session = _session(_prompt("push the branch now"), _tool("Bash", command="git push origin HEAD"),
                       _prompt([{"type": "text", "text": marker}]),
                       _prompt("no, run make ci-local before you push"))
    assert [e.text for e in session.events if e.kind == "prompt"] == [
        "push the branch now", "no, run make ci-local before you push"]
    assert _row(_report([session], rule), "Run")["corrected"] == 1


@pytest.mark.parametrize(("acting", "status", "rank"), [(5, "in_use", None), (2, "in_use", None),
                                                        (1, "candidate_for_ablation", 1)])
def test_a_rule_acted_on_in_the_in_use_share_of_sessions_is_in_use_though_its_words_never_come_up(acting, status,
                                                                                                    rank):
    rule = "- Run `make ci-local` when pushing a branch.\n"
    ran = [_session(_prompt("fix the typo"), _tool("Bash", command="make ci-local"), name=f"r{i}") for i in range(acting)]
    idle = [_session(_prompt("fix the typo"), name=f"i{i}") for i in range(10 - acting if acting < 5 else 0)]
    row = _row(_report(ran + idle, rule, min_sessions=5, rare_max=3, in_use_rate=0.2), "Run")
    assert (row["status"], row["rank"], row["sessions_triggered"], row["sessions_acted"]) == (status, rank, 0, acting)


@pytest.mark.parametrize(("rule", "acting", "triggering", "total", "rate", "status"), [
    ("- Never run `make ci-local` when pushing a branch.\n", 2, 0, 10, 0.2, "kept_protected"),
    ("- Run `make ci-local` when pushing a branch.\n", 3, 0, 3, 0.2, "insufficient_corpus"),
    ("- Run `make ci-local` when pushing a branch.\n", 0, 0, 10, 0.0, "candidate_for_ablation"),
    ("- Run `make ci-local` when pushing a branch.\n", 2, 1, 10, 0.2, "in_use"),  # outranks rare_not_ranked
])
def test_the_acted_share_comes_after_protection_and_corpus_size_and_before_rarity(rule, acting, triggering, total,
                                                                                    rate, status):
    ran = [_session(_prompt("fix the typo"), _tool("Bash", command="make ci-local"), name=f"r{i}") for i in range(acting)]
    pushed = [_session(_prompt("push the branch now"), name=f"p{i}") for i in range(triggering)]
    idle = [_session(_prompt("fix the typo"), name=f"i{i}") for i in range(total - acting - triggering)]
    row = _row(_report(ran + pushed + idle, rule, min_sessions=5, rare_max=3, in_use_rate=rate), "ci-local")
    assert (row["status"], row["sessions_acted"], row["sessions_triggered"]) == (status, acting, triggering)
