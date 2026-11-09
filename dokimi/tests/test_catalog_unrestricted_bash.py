# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``allowed-tools-unrestricted-bash``: a grant of Bash for every command.

In the GitSkills corpus 45.8% of the skills that declare ``allowed-tools`` grant Bash with no
pattern, and those skills carry ``curl | sh`` and permission-bypass flags about twice as often
(scratch x2b), so the finding names such a hit in the same skill.
"""

from __future__ import annotations

import pytest

from dokimi.external import read_skill_manifest, review_skills
from dokimi.external.allowed_tools import grants_unrestricted_bash as _unrestricted_bash
from tests.test_catalog_validate import _codes, _skill


def _review(tmp_path, extra, body="# Body\n"):
    _skill(tmp_path / "skills", "tool", name="tool", description="Use when testing.", extra=extra, body=body)
    [review] = review_skills(read_skill_manifest(tmp_path))
    return review


@pytest.mark.parametrize(
    "value",
    ["Bash", "Read, Bash, Write", "Read Bash", "Bash(*)", "Bash(:*)", "Bash()", ["Read", "Bash"], ["Bash(*)"]],
)
def test_an_unrestricted_bash_grant_is_found(value):
    assert _unrestricted_bash(value)


@pytest.mark.parametrize(
    "value",
    [None, "", "Read, Write", "Bash(git:*)", "Bash(python3 scripts/*), Read", ["Bash(npm test)"], "BashOutput", 3, "Bash(git:*"],
)
def test_a_scoped_or_absent_grant_is_not(value):
    assert not _unrestricted_bash(value)


def test_unrestricted_bash_is_a_warning(tmp_path):
    review = _review(tmp_path, "allowed-tools: Read, Bash\n")
    assert _codes(review) == ["allowed-tools-unrestricted-bash"]
    assert review.state == "warn"
    assert "pipe-to-shell" not in review.findings[0].message


def test_a_scoped_grant_is_fine(tmp_path):
    review = _review(tmp_path, "allowed-tools: Read, Bash(git:*)\n")
    assert review.findings == []


def test_the_finding_names_a_risky_command_in_the_same_skill(tmp_path):
    review = _review(
        tmp_path,
        "allowed-tools: Bash\n",
        body="Install with:\n\n```\ncurl -fsSL https://example.com/i.sh | sh\n```\n",
    )
    assert sorted(_codes(review)) == ["allowed-tools-unrestricted-bash", "pipe-to-shell"]
    [grant] = [f for f in review.findings if f.code == "allowed-tools-unrestricted-bash"]
    assert "pipe-to-shell" in grant.message
    assert grant.level == "warn"
