# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""#1317: which directory an ``npx tsc`` runs in, and which installs leave a usable ``tsc`` there."""

from __future__ import annotations

from dokimi.external.command_checks import _findings_in


def _reports_npx_tsc(script: str) -> bool:
    return any(code == "install-unpinned" and "npx tsc" in msg for code, msg in _findings_in([("run.sh", script)]))


def test_an_install_and_npx_after_the_same_variable_cd_meet():
    """`cd "$PROJECT_ROOT"` is followed as that name, so the install and `npx tsc` run in the same directory."""
    assert not _reports_npx_tsc('cd "$PROJECT_ROOT"\nnpm install typescript@5.9.3\nnpx tsc --version\n')


def test_an_install_and_npx_after_different_variable_cds_do_not_meet():
    assert _reports_npx_tsc('cd "$A"\nnpm install typescript@5.9.3\ncd "$B"\nnpx tsc --version\n')


def test_a_save_dev_flag_turned_off_does_not_omit_the_package():
    """`--save-dev=false` saves a regular dependency, which `--omit=dev` does not skip."""
    assert not _reports_npx_tsc("npm install --save-dev=false --omit=dev typescript@5.9.3\nnpx tsc --version\n")


def test_a_child_directory_named_with_two_leading_dots_is_below_the_install():
    """`..src` is a name, not `..`: `npx tsc` in it finds the parent's typescript."""
    assert not _reports_npx_tsc("npm install typescript@5.9.3\nmkdir -p ..src\ncd ..src\nnpx tsc --version\n")
