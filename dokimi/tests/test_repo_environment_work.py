# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The linear-scan tests of the repository inspector that can count their work, beside the timings of test_repo_environment (#1061)."""

from __future__ import annotations

from dokimi.repos.environment import risks
from tests.test_repo_environment import _hook_files, _inspected
from tests.work_count import WORK_LIMIT, count_calls, count_pattern, work_ratio


def test_heredoc_starts_are_scanned_once_whatever_their_number(monkeypatch):
    """The text is searched for heredoc starts once per command, so the characters searched grow with the text."""
    unclosed = lambda k: "bash <<A\n" + "<<A\n" * k + "".join(f"<<L{i}\n" for i in range(k))
    one_line = lambda n: "bash " + " ".join(f"<<L{i}" for i in range(n)) + "\n" + "".join(f"echo x{i}\nL{i}\n" for i in range(n))
    searched = count_pattern(monkeypatch, risks, "_HEREDOC_START")
    assert work_ratio(searched, lambda k: risks._segments(unclosed(k)), 625) <= WORK_LIMIT
    assert work_ratio(searched, lambda k: risks._segments(one_line(k)), 125) <= WORK_LIMIT


def test_an_overlong_quoted_line_is_never_handed_to_shlex(tmp_path, monkeypatch):
    """A hook line over the cap is split without shlex, whose scan of an unbalanced quote is quadratic."""
    import shlex
    lexed, tokenized = count_calls(monkeypatch, shlex, "shlex"), count_calls(monkeypatch, risks, "_tokens")
    for k in (15_000, 240_000):
        _inspected(tmp_path, lambda size: _hook_files("echo '" + "a" * size))(k)()
    assert tokenized.calls >= 2 and lexed.size == 0
