# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The ``POSIXLY_CORRECT`` state the shell walker keeps for a ``head`` or ``tail`` with a zero count: closed
descriptors of a brace group, ``declare +x`` and a readonly name. No LLM."""

from __future__ import annotations

import pytest

from dokimi.repos.cli_traces import shell_reads
from dokimi.repos.shell_redirects import copied_fds, reopened_fds


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


@pytest.mark.parametrize("command, reads", [
    ("false; f() { :; } || export POSIXLY_CORRECT=1; head AGENTS.md -n 0", []),  # the definition exits 0: the export is skipped
    ("false; f() { :; } && export POSIXLY_CORRECT=1; head AGENTS.md -n 0", ["AGENTS.md"]),
    ("false; f() {\n:\n} || export POSIXLY_CORRECT=1; head AGENTS.md -n 0", []),  # a body on several lines
    ("false; f()\n{\n:\n} || export POSIXLY_CORRECT=1; head AGENTS.md -n 0", []),
    ("false; function f { :; } || export POSIXLY_CORRECT=1; head AGENTS.md -n 0", []),
    ("false; f() ( : ) || export POSIXLY_CORRECT=1; head AGENTS.md -n 0", []),  # a body in parentheses
    ("false && f() { :; } || export POSIXLY_CORRECT=1; head AGENTS.md -n 0", ["AGENTS.md"]),  # a skipped definition keeps the exit
    ("false; true && f() { :; } || export POSIXLY_CORRECT=1; head AGENTS.md -n 0", []),
    ("false; ls && f() { :; } || export POSIXLY_CORRECT=1; head AGENTS.md -n 0", ["AGENTS.md"]),  # it may not have run
])
def test_shell_reads_takes_the_exit_of_a_function_definition_as_zero(command, reads):
    assert shell_reads(command) == reads


@pytest.mark.parametrize("command, reads", [
    ("export POSIXLY_CORRECT=1; { { unset POSIXLY_CORRECT 2>&1; } 1>/dev/null; } 1>&-; head AGENTS.md -n 0", []),  # reopened inside
    ("export POSIXLY_CORRECT=1; { { { unset POSIXLY_CORRECT 2>&1; } 1>/dev/null; }; } 1>&-; head AGENTS.md -n 0", []),
    ("export POSIXLY_CORRECT=1; { { unset POSIXLY_CORRECT 2>&1; } 2>/dev/null; } 1>&-; head AGENTS.md -n 0", ["AGENTS.md"]),  # another one
    ("export POSIXLY_CORRECT=1; { { unset POSIXLY_CORRECT 2>&1; } 1>&-; } 1>/dev/null; head AGENTS.md -n 0", ["AGENTS.md"]),  # closed inside
    ("export POSIXLY_CORRECT=1; { { unset POSIXLY_CORRECT 2>&1; } 1>&- 1>/dev/null; } 1>&-; head AGENTS.md -n 0", []),
])
def test_shell_reads_drops_the_copy_a_nested_groups_redirect_reopens(command, reads):
    assert shell_reads(command) == reads


@pytest.mark.parametrize("command, reads", [
    ("f() { export POSIXLY_CORRECT=1; }; f; head AGENTS.md -n 0", ["AGENTS.md"]),  # the function exports it here
    ("f() { export POSIXLY_CORRECT=1; }; g() { f; }; g; head AGENTS.md -n 0", ["AGENTS.md"]),  # also through another
    ("f() { set -a; }; f; head AGENTS.md -n 0", ["AGENTS.md"]),
    ("f() { eval \"$X\"; }; f; head /w/AGENTS.md -n 0", ["/w/AGENTS.md"]),
    ("f() { source ./env.sh; }; f; head /w/AGENTS.md -n 0", ["/w/AGENTS.md"]),
    ("export POSIXLY_CORRECT=1; f() { unset POSIXLY_CORRECT; }; f; head AGENTS.md -n 0", ["AGENTS.md"]),  # it may not unset it
    ("f() { echo hi; }; f; head AGENTS.md -n 0", []),  # a body that names no such change
    ("f() ( export POSIXLY_CORRECT=1 ); f; head AGENTS.md -n 0", []),  # a subshell's
    ("f() { export POSIXLY_CORRECT=1; }; f | cat; head AGENTS.md -n 0", []),
    ("f() { export POSIXLY_CORRECT=1; }; command f; head AGENTS.md -n 0", []),  # ``command`` runs no function
    ("f() { export POSIXLY_CORRECT=1; }; f; head -n 0 AGENTS.md", []),  # no state prints a line
])
def test_shell_reads_keeps_the_states_a_function_call_may_leave(command, reads):
    assert shell_reads(command) == reads


def test_copied_fds_counts_a_copy_after_a_close_in_the_same_command_and_not_after_a_reopen():
    assert copied_fds("unset X 1>/dev/null 1>&- 2>&1") == {"1"}
    assert copied_fds("unset X 1>/dev/null 2>&1") == set()
    assert reopened_fds("1>&- 2>/dev/null") == {"2"}
    assert reopened_fds("&>/dev/null 1>&-") == {"2"}
