# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``repos/cli_traces.py`` stays under the growth cap by keeping the shell walker in ``repos/shell_walk.py`` (#1065)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from dokimi.repos import cli_traces, shell_walk

_MODULE = Path(cli_traces.__file__)
_MOVED = ["shell_reads", "shell_reads_moved", "shell_walk", "shell_walked", "unwrap_shell", "Walked", "_LONG_VALUE", "_HEREDOC", "_HEREDOC_DELIMITER"]


def test_cli_traces_is_below_the_growth_cap():
    """#1065 asks for room in both files: the parsers are 401 lines, 799 with the walker."""
    assert len(_MODULE.read_text(encoding="utf-8").splitlines()) < 600


@pytest.mark.parametrize("name", _MOVED)
def test_a_moved_name_is_the_walkers_object_not_a_copy(name):
    assert getattr(cli_traces, name) is getattr(shell_walk, name)


def test_the_walked_result_type_is_still_importable_from_cli_traces():
    """``Walked`` was defined in ``cli_traces`` before the move: an import of it from there keeps working."""
    assert cli_traces.Walked is shell_walk.Walked
    assert isinstance(cli_traces.shell_walked("cat a.txt"), cli_traces.Walked)


def test_a_wrapper_with_an_unclosed_quote_is_left_as_written():
    assert shell_walk.unwrap_shell("bash -lc 'cat a") == "bash -lc 'cat a"


def test_a_command_that_is_no_wrapper_is_left_as_written():
    assert shell_walk.unwrap_shell("rg foo") == "rg foo"


@pytest.mark.parametrize("script", ["env", "time", "env -i", "env --"])
def test_a_command_prefix_with_no_command_after_it_reads_nothing(script):
    assert shell_walk.shell_reads(script) == []
    assert shell_walk.shell_reads(f"cat a.txt && {script}") == ["a.txt"]


def test_the_parsers_stay_in_cli_traces():
    assert cli_traces.codex_json.__module__ == cli_traces.__name__
    assert cli_traces.copilot_json.__module__ == cli_traces.__name__


def test_the_walker_does_not_import_the_parsers():
    tree = ast.parse(Path(shell_walk.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            imported |= {n.module or ""} | {f"{n.module or ''}.{a.name}" for a in n.names}
        elif isinstance(n, ast.Import):
            imported |= {a.name for a in n.names}
    assert not [m for m in imported if m.endswith("cli_traces")]
