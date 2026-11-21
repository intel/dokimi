# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The tests run on a git config of their own, so a scratch repository can be committed to on a machine with no identity."""

import os
import subprocess

_IDENTITY_ENV = ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL", "EMAIL")


def test_the_global_git_config_is_the_one_of_the_test_session():
    """Not the machine's: ``git config --global`` lists only the identity the session fixture wrote."""
    listed = subprocess.run(["git", "config", "--global", "--list"], capture_output=True, text=True, check=True).stdout
    assert listed.split() == ["user.name=dokimi-tests", "user.email=dokimi-tests@example.com"]


def test_a_commit_needs_no_identity_from_the_machine(tmp_path):
    """With git forbidden to guess a name (as for a user the system has no name for), a commit still works."""
    env = {key: value for key, value in os.environ.items() if key not in _IDENTITY_ENV}
    env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="user.useConfigOnly", GIT_CONFIG_VALUE_0="true")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, env=env)
    done = subprocess.run(["git", "-C", str(tmp_path), "commit", "-q", "--allow-empty", "-m", "x"],
                          capture_output=True, text=True, env=env)
    assert done.returncode == 0, done.stderr
