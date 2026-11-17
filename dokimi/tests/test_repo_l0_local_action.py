# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""A path claim written as a local Action reference (``./dir/.github/actions/x``) a workflow or action step uses."""

from __future__ import annotations

from tests.test_repo_l0_cases import _CHECKOUT, _by_file, _scan

_LOCAL = "./source/.github/actions/x"
_LOCAL_USE = "jobs:\n  t:\n    steps:\n      - uses: " + _LOCAL + "\n"


def test_a_local_action_a_workflow_step_uses_is_ok(tmp_path):
    """https://github.com/intel/dokimi/issues/857: `./source/.github/actions/x` is what a checkout into `source/` makes."""
    agents = f"Use `{_LOCAL}`, `source/.github/actions/x` and the typo `./source/.github/actions/y`.\n"
    got = _by_file(_scan(tmp_path, agents, {".github/workflows/ci.yml": _LOCAL_USE}))
    assert got[("AGENTS.md", _LOCAL)] == ("ok", "used by .github/workflows/ci.yml")
    assert got[("AGENTS.md", "source/.github/actions/x")] == ("ok", "used by .github/workflows/ci.yml")
    assert got[("AGENTS.md", "./source/.github/actions/y")] == ("unresolved", "tried source/.github/actions/y")


def test_a_local_action_no_workflow_uses_stays_unresolved(tmp_path):
    got = _by_file(_scan(tmp_path, f"Use `{_LOCAL}`.\n", {".github/workflows/ci.yml": _CHECKOUT}))
    assert got[("AGENTS.md", _LOCAL)] == ("unresolved", "tried source/.github/actions/x")


def test_a_local_action_a_composite_action_step_uses_is_ok(tmp_path):
    action = "runs:\n  using: composite\n  steps:\n    - uses: " + _LOCAL + "\n"
    got = _by_file(_scan(tmp_path, f"Use `{_LOCAL}`.\n", {".github/actions/setup/action.yml": action}))
    assert got[("AGENTS.md", _LOCAL)] == ("ok", "used by .github/actions/setup/action.yml")


def test_a_remote_use_is_no_use_of_a_local_claim_of_the_same_spelling(tmp_path):
    flow = "jobs:\n  t:\n    steps:\n      - uses: actions/checkout@v6\n"
    got = _by_file(_scan(tmp_path, "Use `./actions/checkout` and `actions/checkout`.\n", {".github/workflows/ci.yml": flow}))
    assert got[("AGENTS.md", "./actions/checkout")][0] == "unresolved"
    assert got[("AGENTS.md", "actions/checkout")] == ("ok", "used by .github/workflows/ci.yml")


def test_a_claim_spelled_with_extra_slashes_checks_the_parent_of_its_normalized_path(tmp_path):
    flow = "jobs:\n  t:\n    steps:\n      - uses: ./tools/gone\n"
    got = _by_file(_scan(tmp_path, "Use `./tools//gone` and `././tools/gone`.\n",
                         {".github/workflows/ci.yml": flow, "tools/keep": ""}))
    for claim in ("./tools//gone", "././tools/gone"):
        assert got[("AGENTS.md", claim)] == ("fail", "tools/ exists but tools/gone does not")


def test_a_local_use_with_a_ref_below_a_root_directory_or_in_a_submodule_keeps_its_verdict(tmp_path):
    flow = ("jobs:\n  t:\n    steps:\n      - uses: ./source/.github/actions/x@v1\n      - uses: ./tools/gone\n"
            "      - uses: ./third/dep/act\n")
    got = _by_file(_scan(tmp_path, "Use `./source/.github/actions/x`, `./tools/gone` and `./third/dep/act`.\n",
                         {".github/workflows/ci.yml": flow, "tools/keep": "", "third/README": "",
                          ".gitmodules": '[submodule "d"]\n\tpath = third/dep\n'}))
    assert got[("AGENTS.md", _LOCAL)][0] == "unresolved"  # `@v1` makes it no local reference
    assert got[("AGENTS.md", "./tools/gone")] == ("fail", "tools/ exists but tools/gone does not")  # a path
    assert got[("AGENTS.md", "./third/dep/act")] == ("unresolved", "inside the submodule third/dep, "
                                                     "which this snapshot does not hold")
