# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The ``POSIXLY_CORRECT`` state the shell walker keeps for a ``head`` or ``tail`` with a zero count: closed
descriptors of a brace group, ``declare +x`` and a readonly name. No LLM."""

from __future__ import annotations

import pytest

from dokimi.repos.cli_traces import shell_reads


@pytest.mark.parametrize("command, reads", [
    ("export POSIXLY_CORRECT=1; { unset POSIXLY_CORRECT 2>&1; } 1>&-; head AGENTS.md -n 0", ["AGENTS.md"]),  # the group closed stdout first
    ("export POSIXLY_CORRECT=1; { { unset POSIXLY_CORRECT 2>&1; }; } 1>&-; head AGENTS.md -n 0", ["AGENTS.md"]),
    ("export POSIXLY_CORRECT=1; { { unset POSIXLY_CORRECT; } 2>&1; } 1>&-; head AGENTS.md -n 0", ["AGENTS.md"]),
    ("export POSIXLY_CORRECT=1; { unset POSIXLY_CORRECT 2>&1; } 2>&-; head AGENTS.md -n 0", []),  # it closed another descriptor
    ("export POSIXLY_CORRECT=1; { unset POSIXLY_CORRECT; } 1>&-; head AGENTS.md -n 0", []),  # and nothing in it copies stdout
    ("export POSIXLY_CORRECT=1; { unset POSIXLY_CORRECT 2>&1; } 1>&- 1>/dev/null; head AGENTS.md -n 0", []),  # reopened
    ("export POSIXLY_CORRECT=1; { unset POSIXLY_CORRECT 1>/dev/null 2>&1; } 1>&-; head AGENTS.md -n 0", []),  # its own ``1>`` reopens stdout first
    ("export POSIXLY_CORRECT=1; { { unset POSIXLY_CORRECT; } 1>/dev/null 2>&1; } 1>&-; head AGENTS.md -n 0", []),  # as does a nested group's
    ("export POSIXLY_CORRECT=1; { unset POSIXLY_CORRECT 2>&1 1>/dev/null; } 1>&-; head AGENTS.md -n 0", ["AGENTS.md"]),  # opened after the copy
    ("export POSIXLY_CORRECT=1; declare +x POSIXLY_CORRECT; head AGENTS.md -n 0", []),  # ``+x`` drops the export mark
    ("export POSIXLY_CORRECT=1; typeset +x POSIXLY_CORRECT; head AGENTS.md -n 0", []),
    ("export POSIXLY_CORRECT=1; declare +x OTHER; head AGENTS.md -n 0", ["AGENTS.md"]),
    ("export POSIXLY_CORRECT=1; declare +x POSIXLY_CORRECT; declare -x POSIXLY_CORRECT; head AGENTS.md -n 0", ["AGENTS.md"]),
    ("export POSIXLY_CORRECT=1; declare -g +x POSIXLY_CORRECT; head AGENTS.md -n 0", []),
    ("export POSIXLY_CORRECT=1; declare +r POSIXLY_CORRECT; head AGENTS.md -n 0", ["AGENTS.md"]),  # an option it refuses
    ("declare -r POSIXLY_CORRECT; export POSIXLY_CORRECT=1; head AGENTS.md -n 0", []),  # a readonly name has no value to export
    ("declare -r POSIXLY_CORRECT; POSIXLY_CORRECT=1; export POSIXLY_CORRECT; head AGENTS.md -n 0", []),
    ("declare -r POSIXLY_CORRECT; declare -x POSIXLY_CORRECT=1; head AGENTS.md -n 0", []),
    ("declare -r POSIXLY_CORRECT; export -n POSIXLY_CORRECT=1; export POSIXLY_CORRECT; head AGENTS.md -n 0", []),
    ("declare -rx POSIXLY_CORRECT=1; head AGENTS.md -n 0", ["AGENTS.md"]),  # declared with its value
    ("{ export POSIXLY_CORRECT=1; command unset POSIXLY_CORRECT 2>&1; } 1>&-; head AGENTS.md -n 0", ["AGENTS.md"]),  # the export ran, the copy failed
    ("{ export POSIXLY_CORRECT=1; { command unset POSIXLY_CORRECT 2>&1; }; } 1>&-; head AGENTS.md -n 0", ["AGENTS.md"]),
    ("{ export POSIXLY_CORRECT=1; command unset POSIXLY_CORRECT; } 1>&-; head AGENTS.md -n 0", []),  # nothing in it copies stdout
    ("{ export POSIXLY_CORRECT=1; command unset POSIXLY_CORRECT 2>&1; } 2>&-; head AGENTS.md -n 0", []),  # it closed another descriptor
    ("export POSIXLY_CORRECT=1; declare -p +x POSIXLY_CORRECT; head AGENTS.md -n 0", ["AGENTS.md"]),  # ``-p`` only shows it
    ("export POSIXLY_CORRECT=1; typeset +x -p POSIXLY_CORRECT; head AGENTS.md -n 0", ["AGENTS.md"]),
    ("declare -p -x POSIXLY_CORRECT=1; head AGENTS.md -n 0", []),
])
def test_shell_reads_follows_posixly_correct_through_group_closures_declare_and_readonly(command, reads):
    assert shell_reads(command) == reads


@pytest.mark.parametrize("command, reads", [
    ("true || (false) || export POSIXLY_CORRECT=1; head AGENTS.md -n 0", []),  # the subshell is skipped: true's exit stands
    ("true || (false) || (false) || export POSIXLY_CORRECT=1; head AGENTS.md -n 0", []),
    ("false || (false) || export POSIXLY_CORRECT=1; head AGENTS.md -n 0", ["AGENTS.md"]),  # it runs, its exit is not known
    ("true && (false) || export POSIXLY_CORRECT=1; head AGENTS.md -n 0", ["AGENTS.md"]),
    ("time POSIXLY_CORRECT=1; export POSIXLY_CORRECT; head AGENTS.md -n 0", ["AGENTS.md"]),  # time runs the assignment here
    ("time POSIXLY_CORRECT=1 export POSIXLY_CORRECT; head AGENTS.md -n 0", ["AGENTS.md"]),
    ("time -p POSIXLY_CORRECT=1; export POSIXLY_CORRECT; head AGENTS.md -n 0", ["AGENTS.md"]),
    ("time POSIXLY_CORRECT=1; head AGENTS.md -n 0", []),  # set, not exported
    ("time; export POSIXLY_CORRECT=1; head AGENTS.md -n 0", ["AGENTS.md"]),
])
def test_shell_reads_keeps_the_exit_across_a_skipped_subshell_and_the_assignments_of_time(command, reads):
    assert shell_reads(command) == reads
