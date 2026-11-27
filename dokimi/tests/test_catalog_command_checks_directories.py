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


def test_an_include_dev_keeps_a_dev_install_that_omits_dev():
    """npm installs a type both `--omit` and `--include` name, in either order and either spelling."""
    assert not _reports_npx_tsc("npm install -D --omit=dev --include=dev typescript@5.9.3\nnpx tsc --version\n")
    assert not _reports_npx_tsc("npm install -D --include dev --omit dev typescript@5.9.3\nnpx tsc --version\n")


def test_a_separate_only_production_omits_a_dev_install_like_the_equals_form():
    """`--only production` takes its value as the next word: no `npm production` finding, and `npx tsc` is reported."""
    script = "npm install --save-dev --only production typescript@5.9.3\nnpx tsc\n"
    found = _findings_in([("run.sh", script)])
    assert _reports_npx_tsc(script)
    assert not any("npm production" in msg for _, msg in found)


def test_a_quoted_cd_target_with_a_space_is_one_directory():
    assert _reports_npx_tsc('npm install --prefix tools typescript@5.9.3\ncd "tools other"\nnpx tsc\n')
    assert _reports_npx_tsc("npm install --prefix tools typescript@5.9.3\ncd tools\\ other\nnpx tsc\n")


def test_a_subshell_cd_dash_does_not_lose_the_parent_directory():
    assert not _reports_npx_tsc("npm install typescript@5.9.3\n(cd -)\nnpx tsc --version\n")


def test_an_absolute_cd_after_an_unknown_directory_is_known_again():
    assert not _reports_npx_tsc("cd /srv/app\nnpm install typescript@5.9.3\ncd -\ncd /srv/app\nnpx tsc --version\n")


def test_a_variable_or_tilde_directory_is_not_under_a_literal_install():
    assert _reports_npx_tsc("npm install typescript@5.9.3\ncd $OTHER_PROJECT\nnpx tsc\n")
    assert _reports_npx_tsc("npm install typescript@5.9.3\ncd ~\nnpx tsc\n")
    assert not _reports_npx_tsc("cd $P\nnpm install typescript@5.9.3\ncd $P/sub\nnpx tsc\n")


def test_a_quoted_dollar_directory_is_a_literal_child_of_the_install():
    """`'$PROJECT'` and `\\$PROJECT` are directories of that name, so `npx tsc` there finds the parent's typescript."""
    assert not _reports_npx_tsc("npm install typescript@5.9.3\nmkdir -p '$PROJECT'\ncd '$PROJECT'\nnpx tsc --version\n")
    assert not _reports_npx_tsc("npm install typescript@5.9.3\ncd \\$P\ncd sub\nnpx tsc --version\n")


def test_a_prefix_install_into_a_quoted_dollar_directory_is_found_from_it():
    """`--prefix '$PROJECT'` installs into a literal directory, which `cd '$PROJECT'` enters."""
    assert not _reports_npx_tsc("npm install --prefix '$PROJECT' typescript@5.9.3\ncd '$PROJECT'\nnpx tsc --version\n")
    assert _reports_npx_tsc("npm install --prefix '$PROJECT' typescript@5.9.3\ncd '$OTHER'\nnpx tsc --version\n")
    assert _reports_npx_tsc("npm install --prefix '$PROJECT' typescript@5.9.3\ncd $PROJECT\nnpx tsc --version\n")
