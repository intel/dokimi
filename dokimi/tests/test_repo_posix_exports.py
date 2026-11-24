# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The ``POSIXLY_CORRECT`` state the shell walker keeps for a ``head`` or ``tail`` with a zero count: an export whose
value expands, a prefixed assignment a bare ``export`` keeps, and an array, which is never exported. No LLM."""

from __future__ import annotations

import pytest

from dokimi.repos.cli_traces import shell_reads


@pytest.mark.parametrize("command, reads", [
    ('X=1; export POSIXLY_CORRECT="$X"; head AGENTS.md -n 0', ["AGENTS.md"]),  # set, whatever $X is
    ("export POSIXLY_CORRECT=`date`; head AGENTS.md -n 0", ["AGENTS.md"]),
    ("export POSIXLY_CORRECT=$(echo 1); head AGENTS.md -n 0", ["AGENTS.md"]),
    ("export POSIXLY_CORRECT=1; unset POSIXLY_CORRECT $(echo X); head AGENTS.md -n 0", []),
    ("export POSIXLY_CORRECT=$(echo 1) | cat; head AGENTS.md -n 0", []),
    ("if false; then export POSIXLY_CORRECT=$(echo 1); fi; head AGENTS.md -n 0", ["AGENTS.md"]),  # the branch may not run
    ("{ export POSIXLY_CORRECT=$(echo 1); }; head AGENTS.md -n 0", ["AGENTS.md"]),
    ("echo $(export POSIXLY_CORRECT=1); head AGENTS.md -n 0", []),  # set in the substitution's subshell
    ("(export POSIXLY_CORRECT=1); head AGENTS.md -n 0", []),
    ("POSIXLY_CORRECT=$X; export POSIXLY_CORRECT; head AGENTS.md -n 0", ["AGENTS.md"]),
    ("export POSIXLY_CORRECT=1; unset POSIXLY_CORRECT $X; head AGENTS.md -n 0", []),
    ('export POSIXLY_CORRECT="$X" | cat; head AGENTS.md -n 0', []),  # a pipeline stage runs in a subshell
    ('export OTHER="$X"; head AGENTS.md -n 0', []),
    ("POSIXLY_CORRECT=1 export POSIXLY_CORRECT; head AGENTS.md -n 0", ["AGENTS.md"]),  # export keeps the prefixed value
    ("POSIXLY_CORRECT=1 export OTHER; head AGENTS.md -n 0", []),
    ("POSIXLY_CORRECT=1 env head AGENTS.md -n 0; head AGENTS.md -n 0", ["AGENTS.md"]),  # only the first has it
    ("declare -ax POSIXLY_CORRECT=1; head AGENTS.md -n 0", []),  # an array is not passed to head
    ("typeset -Ax POSIXLY_CORRECT=1; head AGENTS.md -n 0", []),
    ("declare -ax POSIXLY_CORRECT=1; export POSIXLY_CORRECT; head AGENTS.md -n 0", []),
    ("declare -a POSIXLY_CORRECT; export POSIXLY_CORRECT=1; head AGENTS.md -n 0", []),
    ("declare -ax POSIXLY_CORRECT=1; unset POSIXLY_CORRECT; export POSIXLY_CORRECT=1; head AGENTS.md -n 0", ["AGENTS.md"]),
    ("declare -x POSIXLY_CORRECT=1; head AGENTS.md -n 0", ["AGENTS.md"]),  # a scalar is
])
def test_shell_reads_follows_posixly_correct_through_expansions_prefixes_and_arrays(command, reads):
    assert shell_reads(command) == reads
