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

from dokimi.repos import security_patterns
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
