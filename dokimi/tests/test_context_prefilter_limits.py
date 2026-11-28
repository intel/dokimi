# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`trace prefilter`: a limit written in words protects its rule like a threshold does."""

from __future__ import annotations

import pytest

from dokimi.context.prefilter import claims_from_guidance
from tests.test_context_prefilter import _prompt, _report, _row, _session


@pytest.mark.parametrize("rule", [
    "Keep functions under 50 lines.",
    "Use at most 3 retries.",
    "Limit a pull request to 400 changed lines.",
    "Keep test coverage above 80%.",
    "Keep functions under `50` lines.",
    "Allow at\n  most 3 concurrent connections.",
    "Keep functions under `50 lines`.",
    "Limit the total number of retries to 3.",
    "Keep each file below the 800-line cap.",
])
def test_a_limit_in_words_with_a_number_is_kept_protected(rule):
    """#937: docs/session-prefilter.md says a rule with a threshold is `kept_protected` and never ranked as waste."""
    row = _row(_report([_session(_prompt("hello there"))], f"- {rule}\n"), rule)
    assert (row["status"], row["rank"], row["protection"]) == ("kept_protected", None, ["threshold"])


@pytest.mark.parametrize("rule", [
    "Keep the changelog fragment current.",
    "Run `make test-50` after each change to the build.",
    "Keep 50 as the default.",
    "Store reports under reports/2026/.",
    "Store reports under `2026.d/`.",
    "Store reports under `2026\\archive`.",
    "Store the 2026-11 reports in the archive.",
])
def test_a_rule_with_no_limit_is_not_protected_by_a_number_alone(rule):
    """#937: only a limit word with a number, or a counted noun, reads as a threshold."""
    claims = claims_from_guidance(f"- {rule}\n")
    assert claims and "threshold" not in claims[0].protection
