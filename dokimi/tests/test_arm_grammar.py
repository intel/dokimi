# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The one ``+`` splitter of treatments/specs.py, and the two arm grammars that use it.

Treatment specs (``--arms``) and ``repo run --arm`` specs both join parts with ``+``. They
split on different heads, and they differ on whitespace. Each case where they disagree is
pinned here with the outcome it has today, so a change to either grammar shows up as a
failing test rather than as a silently different arm.
"""

from __future__ import annotations

import pytest

from dokimi.repos.workspace import PrepError, parse_arm
from dokimi.treatments.specs import (
    ArmSpecError,
    parse_arm_spec,
    split_combined,
    split_parts,
)


def _is_head(piece: str) -> bool:
    return piece.startswith(("skill:", "guidance:"))


# -- the splitter itself ----------------------------------------------------------------

def test_split_parts_joins_a_piece_the_predicate_rejects():
    assert split_parts("a+b+skill:x+c", _is_head) == ["a+b", "skill:x+c"]


def test_split_parts_first_piece_is_always_a_part():
    # Whatever the predicate says, the first piece opens the first part (even an empty one).
    assert split_parts("+skill:x", _is_head) == ["", "skill:x"]
    assert split_parts("c+skill:x", lambda p: False) == ["c+skill:x"]


def test_split_parts_strip_removes_outer_and_piece_whitespace():
    assert split_parts("  skill:x +  skill:y ", _is_head) == ["skill:x", "skill:y"]


def test_split_parts_without_strip_keeps_whitespace_and_glues_a_padded_head():
    # The repo grammar: " skill:y" is not a head, so it is glued to the part before.
    assert split_parts("skill:x + skill:y", _is_head, strip=False) == ["skill:x + skill:y"]


# -- treatment specs (--arms): the edge cases the treatment grammar pins -----------------

@pytest.mark.parametrize("spec, parts", [
    # A kind after '+' starts a part; a non-kind head does not.
    ("skill:/x+guidance:none", ["skill:/x+guidance:none"]),
    ("skill:/x+baseline", ["skill:/x", "baseline"]),
    ("skill:/x+baselines", ["skill:/x+baselines"]),
    # Whitespace around '+' is dropped, so padded parts are the same parts.
    ("skill:/x + skill:/y", ["skill:/x", "skill:/y"]),
    ("  docs  +  skill:/y  ", ["docs", "skill:/y"]),
    # A trailing '+' is part of the path before it; it does not make an empty part.
    ("skill:/x+", ["skill:/x+"]),
    ("skill:/x++", ["skill:/x++"]),
    ("skill:c++-guide", ["skill:c++-guide"]),
    # An mcp: spec is never split, even padded.
    ("  mcp:http=u?q=a+skill:/x  ", ["mcp:http=u?q=a+skill:/x"]),
])
def test_treatment_split_edge_cases(spec, parts):
    assert split_combined(spec) == parts


def test_treatment_empty_leading_part_is_refused_by_parse():
    assert split_combined("+skill:/x") == ["", "skill:/x"]
    with pytest.raises(ArmSpecError, match="Unknown arm spec"):
        parse_arm_spec("+skill:/x")


def test_treatment_bare_kind_after_plus_needs_its_argument():
    # 'skill' alone is a head with no path: the split is right, the argument is refused.
    assert split_combined("skill:/x+skill") == ["skill:/x", "skill"]
    with pytest.raises(ArmSpecError, match="requires"):
        parse_arm_spec("skill:/x+skill")


# -- repo run --arm specs: the same '+', different heads ---------------------------------

def test_repo_guidance_and_skill_split_at_the_plus():
    arm = parse_arm("guidance:none+skill:/x+skill:/y")
    assert str(arm) == "guidance:none+skill:x+skill:y"
    assert arm.base == "guidance:none"
    assert arm.skills == (("skill", "/x"), ("skill", "/y"))


def test_repo_a_plus_inside_a_skill_path_stays_in_the_path():
    # 'c++-guide' is not a head, so the '+' belongs to the directory name.
    arm = parse_arm("skill:c++-guide")
    assert str(arm) == "skill:c++-guide"
    assert arm.skills == (("skill", "c++-guide"),)


def test_repo_a_plus_inside_a_patch_path_stays_in_the_path(tmp_path):
    patch = tmp_path / "a+b.patch"
    patch.write_text("--- a\n+++ b\n", encoding="utf-8")
    arm = parse_arm(f"patch:{patch}")
    assert arm.patch_path == str(patch.resolve())
    assert str(arm).startswith("patch:a_b.patch@")


# -- where the two grammars disagree (pinned as they are today) --------------------------

def test_disagreement_skill_then_guidance_head():
    # Treatments: 'guidance:' is no kind, so it is part of the path.
    assert split_combined("skill:/x+guidance:none") == ["skill:/x+guidance:none"]
    # Repo: 'guidance:none' is a head, so it starts a part and becomes the base.
    arm = parse_arm("skill:/x+guidance:none")
    assert str(arm) == "guidance:none+skill:x" and arm.base == "guidance:none"


def test_disagreement_skill_then_treatment_kind():
    # Treatments split on the kind 'baseline'; repo has no such head, so it stays in the path.
    assert split_combined("skill:/x+baseline") == ["skill:/x", "baseline"]
    arm = parse_arm("skill:/x+baseline")
    assert arm.skills == (("skill", "/x+baseline"),)
    assert str(arm) == "skill:x+baseline"


def test_disagreement_whitespace_around_plus():
    # Treatments strip the spaces around '+'; the repo grammar keeps them and refuses the spec.
    assert split_combined("guidance:none + skill:/x") == ["guidance:none", "skill:/x"]
    with pytest.raises(PrepError, match="unknown arm"):
        parse_arm("guidance:none + skill:/x")


def test_disagreement_guidance_is_not_a_treatment_kind():
    # Treatments refuse a guidance spec outright; the repo grammar accepts it.
    with pytest.raises(ArmSpecError):
        parse_arm_spec("guidance:none")
    assert str(parse_arm("guidance:none")) == "guidance:none"


def test_disagreement_repo_spaces_around_a_skill_join_name_the_wrong_arm_until_prep():
    # Kept as is: the repo grammar takes the padded text as the path. Prep, not the parser,
    # refuses it (the directory does not exist), so the name it would give is never used.
    arm = parse_arm("skill:/x + skill:/y")
    assert [kind for kind, _ in arm.skills] == ["skill"]
    assert arm.skills[0][1] == "/x + skill:/y"
