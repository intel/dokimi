# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

from dokimi import llms_txt, opportunities as opp

NOVELTY = {
    "artifact": {"kind": "skill", "ref": "skill:skills/acme"},
    "model": "m1",
    "paired": {"lenient": {"added": 10, "redundant": 20, "degraded": 4, "still_missing": 6,
                           "n_paired": 40}},
}
STATIC = {"schema": "repo_static.v1", "claims": [
    {"file": "AGENTS.md", "line": 3, "status": "ok"},
    {"file": "AGENTS.md", "line": 9, "status": "fail_missing_path"},
    {"file": "AGENTS.md", "line": 12, "status": "unresolved_link"},
    {"file": "README.md", "line": 1, "status": "ok"},
]}


def test_novelty_outcomes_become_weighted_fixes():
    found = {o.evidence["outcome"]: o for o in opp.from_novelty(NOVELTY)}
    assert set(found) == {"degraded", "still_missing", "redundant"}
    assert found["degraded"].impact == 0.2  # 4/40 doubled: a regression outranks a gap
    assert found["still_missing"].impact == 0.15
    assert found["redundant"].impact == 0.25
    assert found["degraded"].target == "skill:skills/acme"


def test_zero_counts_produce_no_opportunity():
    art = {**NOVELTY, "paired": {"lenient": {"degraded": 0, "n_paired": 5}}}
    assert opp.from_novelty(art) == []


def test_repo_claims_group_failures_per_file():
    [one] = opp.from_repo_static(STATIC, target="acme")
    assert one.target == "acme:AGENTS.md"
    assert one.evidence["lines"] == [9, 12]
    assert one.impact == 0.5
    assert "9, 12" in one.next_action


def test_rank_puts_high_impact_cheap_fixes_first():
    items = opp.from_novelty(NOVELTY) + opp.from_repo_static(STATIC)
    items += opp.from_llms_txt(llms_txt.check("no title\n"))
    ranked = opp.rank(items)
    assert [r["rank"] for r in ranked] == list(range(1, len(ranked) + 1))
    quads = [opp.QUADRANTS.index(r["quadrant"]) for r in ranked]
    assert quads == sorted(quads)
    assert ranked[0]["kind"] == "repo-claim"  # impact 0.5, effort low
    assert ranked[-1]["evidence"].get("outcome") == "still_missing"  # 0.15, medium effort


def test_source_key_is_stable_and_distinguishes_targets():
    a = opp.from_repo_static(STATIC, target="acme")[0]
    b = opp.from_repo_static(STATIC, target="acme")[0]
    c = opp.from_repo_static(STATIC, target="other")[0]
    assert a.source_key == b.source_key != c.source_key


def test_execution_prompt_names_target_problem_and_action():
    [one] = opp.from_repo_static(STATIC, target="acme")
    prompt = one.as_dict()["execution_prompt"]
    assert "acme:AGENTS.md" in prompt and "2 failing claims" in prompt and "line" in prompt


def test_markdown_table_honours_top():
    ranked = opp.rank(opp.from_novelty(NOVELTY))
    table = opp.render_markdown(ranked, top=1)
    assert len(table.strip().splitlines()) == 3
    assert ranked[0]["title"] in table


def test_source_key_survives_a_change_in_count():
    one = {"schema": "repo_static.v1", "claims": [{"file": "A.md", "line": 1, "status": "fail_x"}]}
    two = {"schema": "repo_static.v1", "claims": [{"file": "A.md", "line": 1, "status": "fail_x"},
                                                  {"file": "A.md", "line": 5, "status": "fail_x"}]}
    keys = [opp.from_repo_static(d)[0].source_key for d in (one, two)]
    assert keys[0] == keys[1]


def test_markdown_escapes_pipes():
    item = opp.Opportunity("k", "a|b", "w", "n", "t", 0.5, "low")
    ranked = opp.rank([item])
    assert r"a\|b" in opp.render_markdown(ranked)


def test_llms_txt_key_survives_a_change_in_h1_count():
    keys = [opp.from_llms_txt(llms_txt.check(f"# A\n{extra}\n> s\n"))[0].source_key
            for extra in ("# B", "# B\n# C")]
    assert keys[0] == keys[1]
