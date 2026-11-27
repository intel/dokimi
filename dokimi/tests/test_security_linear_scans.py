# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The command rules of ``repo security`` read a line of repeated keywords in linear time, and read the same lines as before.

Each rule's pattern is checked against a copy of the pattern before the change: a table of lines and a random-lines comparison. Growth is measured as in tests/test_linear_scan_sites.py.
"""

from __future__ import annotations

import random
import re
from collections.abc import Callable
from functools import partial
from typing import Any

import pytest

from dokimi.repos import security, security_patterns
from tests.growth_timing import GROWTH_LIMIT, growth
from tests.test_linear_scan_sites import CASES, _security_search


def _linear(call: Callable[[str], Any], shape: Callable[[int], str], n: int) -> None:
    ratio, _ = growth(lambda k: partial(call, shape(k)), n)
    assert ratio < GROWTH_LIMIT


# --- destructive-delete: the first `rm` with a recursive flag of each segment (#688) -------------------

_OLD_DESTRUCTIVE_DELETE = re.compile(
    r"\brm" + security_patterns.BLANKS + r"(?:-\S+" + security_patterns.BLANKS + r")*?(?:-[a-z]*r[a-z]*|--recursive)" + security_patterns.BLANKS
    + r"(?:[^\s;&|]+" + security_patterns.BLANKS + r")*?[\"']?"
    r"(?:/\*?|(?:~|\$HOME|\$\{HOME(?:[:#%/^,?+=-][^}\s;&|\"'`()]*)?\})(?:/\.\.)*/?\*?|/(?:" + security_patterns.SYSTEM_DIRS + r")"
    r"(?!(?![^\s;&|\"']*\.\.)[^\s;&|\"']*/(?:" + security_patterns.CLEANUP_PARTS + r")(?:/|[\s;&|\"')`]|$))(?:/[^\s;&|\"']*)?)"
    r"[\"']?(?=[\s;&|)`]|$)",
    re.IGNORECASE,
)
_DESTRUCTIVE_DELETE_TABLE = [
    ("rm -rf /", True),
    ("rm -r a rm -rf /", True),
    ("rm a rm -rf /", True),
    ("rm -f a rm -r /", True),
    ("rm a b rm c rm -r ~", True),
    ("rm -r a rm -a;b -r /", True),
    ("rm -r -a;b -r /", True),
    ("rm -a;b -r /", True),
    ("rm -x;y /", False),
    ("rm -r x;y /", False),
    ("rm -r a; rm -rf /", True),
    ("rm -r a; rm a; rm -rf $HOME", True),
    ("rm -r a && rm -r b | rm -r /etc", True),
    ("rm -r a\nrm -rf /", True),
    ("rm -r a\n/", False),
    ("rm -r a rm\n-rf /", False),
    ("rm -r a /\n", True),
    ("rm -r -r -r /", True),
    ("rm --recursive --force /usr", True),
    ("rm -r /var/cache/apt", False),
    ("rm -r build rm -r /var/cache/apt", False),
    ("rm -r build rm -r /var/lib", True),
    ("rm -x-rm -rf /", True),
    ("echo -rm -rf /", True),
    ("xrm -rf /", False),
    ("rm -r a rm -r b", False),
    ("RM -RF /", True),
    ("rm -r \"/\"", True),
    ("rm -r a \"~\"", True),
    ("rm -rf /;", True),
    ("rm -r a rm\t-rf\t/", True),
]


@pytest.mark.parametrize(("text", "expected"), _DESTRUCTIVE_DELETE_TABLE)
def test_destructive_delete_reads_a_line_as_the_old_pattern_did(text: str, expected: bool) -> None:
    assert (_OLD_DESTRUCTIVE_DELETE.search(text) is not None) is expected
    assert (_security_search("destructive-delete", text) is not None) is expected


def test_destructive_delete_matches_the_old_pattern_on_random_lines_of_removals() -> None:
    pieces = ["rm", "-r", "-rf", "-f", "-a", "a", "/", "~", "$HOME", ";", "&&", "|", "\n", "-a;b", "-x|y", "--recursive", "/etc", "/usr/lib/x",
              "\"", "'", "rm -r", "rm -rf", "-rm", "x-rm", "/*", "/var/cache", "-R", "-x&y", "\t", "-rr", "-1", ")", "-rf;", "/home/x/.cache",
              "rm -rf /", "rm a", "rm -x", "rm -r -a;b", "/", "~", "-r"]
    rng = random.Random(688)
    hits = 0
    for _ in range(6 * CASES):
        text = rng.choice([" ", " ", ""]).join(rng.choice(pieces) for _ in range(rng.randint(0, 13)))
        new_match = _security_search("destructive-delete", text) is not None
        assert new_match == (_OLD_DESTRUCTIVE_DELETE.search(text) is not None), text
        hits += new_match
    assert hits > 300, hits


@pytest.mark.parametrize(
    ("rule", "shape"),
    [
        ("destructive-delete", lambda n: "rm -r " * n),
        ("destructive-delete", lambda n: "rm -rf " * n),
        ("destructive-delete", lambda n: "rm -r a " * n),
        ("destructive-delete", lambda n: "rm a " * n),
        ("destructive-delete", lambda n: "rm " + "-r " * n),
        ("destructive-delete", lambda n: "rm -a;b -r " * n),
        ("destructive-delete", lambda n: "rm -r\n" * n),
        ("history-rewrite", lambda n: "git -c " * n),
        ("hook-bypass", lambda n: "git -c " * n),
        ("secret-read", lambda n: "a/ " * n),
    ],
)
def test_keyword_repeats_on_one_line_are_scanned_in_linear_time(rule: str, shape: Callable[[int], str]) -> None:
    _linear(partial(_security_search, rule), shape, n=200)


# --- history-rewrite and hook-bypass: the closer is tried from the first end of the opener only (#1267) ---

_MAIN_PUSH_FORCE = r"[^;&|\n]*(?:\s-[a-z]*f[a-z]*\b|--force\b(?!-with-lease|-if-includes)|\s\+\S)"
_MAIN_RESET_HARD = r"[^;&|\n]*\s--hard(?![\w-])"
_MAIN_GIT_PUSH = r"\bgit\s+" + security._GIT_OPT + r"*push\b"


def _main_first_in_segment(sub: str, closers: str) -> str:
    """``security._first_in_segment`` before the opener was cut at its first end: every end of ``git -C push -C push ...`` read the closers again."""
    local = r"\bgit" + security._NL + r"+" + security._COMMIT_OPT_LOCAL + r"*" + sub + r"\b"
    across = (
        r"\bgit(?:" + security._NL + r"*\n\s*" + security._COMMIT_OPT + r"*|" + security._NL + r"+" + security._COMMIT_OPT_LOCAL + r"*(?:"
        r"-[Cc]" + security._NL + r"*\n\s*" + security._COMMIT_ARG + r"\s+|-[Cc]" + security._NL + r"+(?=\S*[;&|])" + security._COMMIT_ARG + r"\s+|"
        r"-[Cc]" + security._NL + r"+" + security._COMMIT_ARG_LOCAL + security._NL + r"*\n\s*|-(?=\S*[;&|])\S+\s+|-[^\s;&|]+" + security._NL + r"*\n\s*"
        r")" + security._COMMIT_OPT + r"*)" + sub + r"\b")
    return r"(?:(?m:^)|[;&|])(?:(?!" + local + r")[^;&|\n])*" + local + closers + r"|" + across + closers


_MAIN_PUSH_NO_VERIFY = security._after_first_opener(_MAIN_GIT_PUSH, security._GIT_PUSH_ACROSS_LINES, r"--no-verify\b")
_MAIN_COMMIT_NO_VERIFY = _main_first_in_segment("commit", security._COMMIT_CLOSERS)
_MAIN_HISTORY_REWRITE = re.compile(_main_first_in_segment("push", _MAIN_PUSH_FORCE) + r"|" + _main_first_in_segment("reset", _MAIN_RESET_HARD), re.IGNORECASE)
_MAIN_HOOK_BYPASS = re.compile(_MAIN_COMMIT_NO_VERIFY + r"|" + _MAIN_PUSH_NO_VERIFY.pattern, re.IGNORECASE)
# (line, history-rewrite, hook-bypass, the push branch of hook-bypass alone) matches
_SUBCOMMAND_AS_C_ARGUMENT_TABLE = [
    ("git -C push push -f", 1, 0, 0),
    ("git -C reset reset --hard", 1, 0, 0),
    ("git -C commit commit --no-verify", 0, 1, 0),
    ("git -C push -f", 1, 0, 0),
    ("git -C push -C push", 0, 0, 0),
    ("git -C push -C push -f", 1, 0, 0),
    ("git -C push -f push", 1, 0, 0),
    ("git -C push --no-verify push", 0, 1, 1),
    ("git -C push push --no-verify", 0, 1, 1),
    ("git -C push -C push push --no-verify", 0, 1, 1),
    ("git -C commit -C commit -n", 0, 1, 0),
    ("git -C commit commit -n", 0, 1, 0),
    ("git -C commit -n commit", 0, 1, 0),
    ("git -C reset -C reset --hard", 1, 0, 0),
    ("git -C reset --hard reset", 1, 0, 0),
    ("git -C push -C push push -f", 1, 0, 0),
    ("git -C push\npush -f", 1, 0, 0),
    ("git -C push\npush --no-verify", 0, 1, 1),
    ("git -C push -C push\npush --no-verify", 0, 1, 1),
    ("git -C push; git -C push -f", 1, 0, 0),
    ("git -C push push; git -C push push -f", 1, 0, 0),
    ("git -C push -x push", 0, 0, 0),
    ("git -C a push -C push -f", 1, 0, 0),
    ("git -c push push --no-verify", 0, 1, 1),
    ("git -C push git push -f", 1, 0, 0),
    ("git -C push --no-verify", 0, 1, 1),
    ("git -C push -C push --no-verify", 0, 1, 1),
    ("git -C reset -C reset", 0, 0, 0),
    ("git -C commit -C commit", 0, 0, 0),
    ("git -C push push", 0, 0, 0),
    ("git -C push -C a push -f", 1, 0, 0),
    ("git -C push -x -C push -f", 1, 0, 0),
    ("git -C push\n-C push -f", 1, 0, 0),
    ("git\n-C push push -f", 1, 0, 0),
]


def _subcommand_pairs() -> list[tuple[str, re.Pattern[str], re.Pattern[str]]]:
    hook, history = security.RULES["hook-bypass"].pattern, security.RULES["history-rewrite"].pattern
    assert hook is not None and history is not None
    return [("history-rewrite", history, _MAIN_HISTORY_REWRITE), ("hook-bypass", hook, _MAIN_HOOK_BYPASS), ("push", security._PUSH_NO_VERIFY, _MAIN_PUSH_NO_VERIFY)]


@pytest.mark.parametrize(("text", "history", "hook", "push"), _SUBCOMMAND_AS_C_ARGUMENT_TABLE)
def test_a_subcommand_name_as_the_argument_of_c_is_read_as_the_old_patterns_did(text: str, history: int, hook: int, push: int) -> None:
    for (name, new, old), expected in zip(_subcommand_pairs(), (history, hook, push), strict=True):
        assert (old.search(text) is not None) is bool(expected), (name, text)
        assert (new.search(text) is not None) is bool(expected), (name, text)


def test_subcommand_names_as_arguments_of_c_match_the_old_patterns_on_random_lines() -> None:
    pieces = ["git", "push", "reset", "commit", "-C", "-c", "-f", "-n", "--hard", "--no-verify", "--force", "d", "x", ";", "&", "\n", "-a/git", "-x",
              "git -C", "-C push", "-C reset", "-C commit", "git -C push", "git -C commit", "push -f", "commit -n", "reset --hard"]
    rng = random.Random(1267)
    hits = [0, 0, 0]
    for _ in range(6 * CASES):
        text = " ".join(rng.choice(pieces) for _ in range(rng.randint(0, 12)))
        for k, (name, new, old) in enumerate(_subcommand_pairs()):
            new_match = new.search(text) is not None
            assert new_match == (old.search(text) is not None), (name, text)
            hits[k] += new_match
    assert all(count > 100 for count in hits), hits


@pytest.mark.parametrize(
    ("rule", "shape"),
    [
        ("history-rewrite", lambda n: "git " + "-C push " * n),
        ("history-rewrite", lambda n: "git " + "-C reset " * n),
        ("history-rewrite", lambda n: "git " + "-C push -C reset " * n),
        ("hook-bypass", lambda n: "git " + "-C push " * n),
        ("hook-bypass", lambda n: "git " + "-C commit " * n),
        ("hook-bypass", lambda n: "git " + "-C push -C commit " * n),
    ],
)
def test_a_chain_of_c_with_the_subcommand_name_is_scanned_in_linear_time(rule: str, shape: Callable[[int], str]) -> None:
    _linear(partial(_security_search, rule), shape, n=200)


def test_the_push_and_commit_alternatives_scan_a_chain_of_c_with_the_subcommand_name_in_linear_time() -> None:
    _linear(security._PUSH_NO_VERIFY.search, lambda n: "git " + "-C push " * n, n=200)
    _linear(security._rx(security._COMMIT_NO_VERIFY).search, lambda n: "git " + "-C commit " * n, n=200)
