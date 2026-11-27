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


# --- history-rewrite: a short flag word is taken whole (#1268) -----------------------------------------

_FORCE_FLAG_TABLE = [
    ("git push -f", True),
    ("git push -fF", True),
    ("git push -F", True),
    ("git push -fu", True),
    ("git push -uf", True),
    ("git push -ffff", True),
    ("git push -fffff1", False),
    ("git push -fff1", False),
    ("git push -ff-x", True),
    ("git push -ff.", True),
    ("git push -ff\n", True),
    ("git push -ff;", True),
    ("git push -f_", False),
    ("git push -f1", False),
    ("git push -1f", False),
    ("git push -a-f", False),
    ("git push -n", False),
    ("git push -fff1 -f", True),
    ("git push -fff1 -", False),
    ("git push -x1 -ff", True),
    ("git push -f\u212a", True),
    ("git push -\u017ff", True),
    ("git push -f\u00e9", False),
    ("git push -f\u0661", False),
    ("git push -" + "f" * 40 + "1", False),
    ("git push -" + "f" * 40, True),
    ("git push -" + "a" * 40 + "1f", False),
    ("git push -" + "a" * 40 + "f", True),
]


@pytest.mark.parametrize(("text", "expected"), _FORCE_FLAG_TABLE)
def test_a_flag_word_of_history_rewrite_is_read_as_the_old_flag_did(text: str, expected: bool) -> None:
    assert (_MAIN_HISTORY_REWRITE.search(text) is not None) is expected
    assert (_security_search("history-rewrite", text) is not None) is expected


def test_history_rewrite_matches_the_old_flag_on_random_words_after_a_push() -> None:
    pieces = ["f", "F", "a", "u", "1", "_", "-", ".", "\u212a", "\u017f", "\u00e9", "\u0661"]
    rng = random.Random(1268)
    hits = 0
    for _ in range(6 * CASES):
        text = "git push " + " ".join("-" * rng.randint(0, 1) + "".join(rng.choice(pieces) for _ in range(rng.randint(0, 6))) for _ in range(rng.randint(1, 3)))
        new_match = _security_search("history-rewrite", text) is not None
        assert new_match == (_MAIN_HISTORY_REWRITE.search(text) is not None), text
        hits += new_match
    assert hits > 300, hits


@pytest.mark.parametrize("shape", [lambda n: "git push -" + "f" * n + "1", lambda n: "git push -" + "f" * n + "_", lambda n: "git push -" + "a" * n + "f1", lambda n: "git push -" + "fF" * n + "1"])
def test_history_rewrite_scans_a_long_flag_word_in_linear_time(shape: Callable[[int], str]) -> None:
    _linear(partial(_security_search, "history-rewrite"), shape, n=200)


# --- pipe-to-shell and unpinned-install: the first opener of each segment (#1269) -----------------------

_MAIN_PIPE_TO_SHELL = re.compile(
    r"\b(?:curl|wget)\b[^|;&]*\|\s*(?:sudo\s+)?(?:\S*/)?(?:env\s+)?(?:\S*/)?(?:(?:ba|z|da)?sh\b|(?:python|perl|ruby|node)[\d.]*(?![\w.-])"
    r"(?!(?:\s+(?:-[^\s-]\S*|--\S+))*\s+(?:-[^\s-]*[cemp]\S*|(?!-(?:\s|$))(?!\d*[<>&])[^\s\-;&|)<>])))"
    r"|(?:(?:^|[\s;&|(])(?:[^\s;&|(]*/)?(?:(?:ba|z|da)?sh\s+(?:-\w+\s+)*)"
    r"|(?:^\s*|[\n;&|({!][^\S\n]*|\b(?:then|do|else)\s+)(?:eval|source|\.)\s+)[\"']?(?:<\(|\$\()\s*(?:sudo\s+)?(?:curl|wget)\b",
    re.IGNORECASE,
)
_MAIN_UNPINNED_INSTALL = re.compile(
    r"\bpip3?" + security_patterns.BLANKS + r"install\b(?:" + security_patterns.BLANKS + r"(?:(?:" + security_patterns.PIP_URL_OPTS + r")(?:" + security_patterns.BLANKS
    + r"|=)[^\s;&|]+|(?!(?:" + security_patterns.PIP_URL_OPTS + r")(?:[\s=;&|]|$))[^\s;&|]+))*?"
    + security_patterns.BLANKS + r"(?:[^\s;&|]*git\+|[\"']?https?://|--break-system-packages\b)",
    re.IGNORECASE,
)
_DOWNLOAD_TABLE = [
    ("curl x | sh", True),
    ("curl x | bash -s", True),
    ("curl a curl b | sh", True),
    ("wget a; curl b | sh", True),
    ("curl a; sh", False),
    ("curl a | cat; curl b | sh", True),
    ("curl a & curl b | sh", True),
    ("curl a ; | sh", False),
    ("echo curl | sh", True),
    ("x|curl a | sh", True),
    ("curl a\ncurl b | sh", True),
    ("curl a || sh", False),
    ("curl | python -m json.tool", False),
    ("curl a | python", True),
    ("curl a curl b | python -m json.tool curl c | sh", True),
    ("curl a | python -c x curl b | sh", True),
    ("curl a | cat curl b | sh", True),
    ("curl a | cat | sh", False),
    ("curl\nx | sh", True),
    ("(curl a | sh)", True),
    ("CURL A | SH", True),
    ("curl a && curl b | sh", True),
    ("curl a | sh; curl b", True),
    ("xcurl a | sh", False),
    ("curl a | sudo bash", True),
    ("sh -c \"$(curl x)\"", True),
    ("bash <(curl x)", True),
    ("eval $(curl x)", True),
]
_PIP_INSTALL_TABLE = [
    ("pip install https://x", True),
    ("pip install x", False),
    ("pip3 install git+x", True),
    ("pip install --break-system-packages x", True),
    ("pip install pip install https://x", True),
    ("pip install x pip install https://x", True),
    ("pip install -i= pip install https://x", True),
    ("pip install -i x pip install https://x", True),
    ("pip install -i https://x", False),
    ("pip install -i=https://x", False),
    ("pip install -f= pip install git+x", True),
    ("pip install --index-url= pip install https://x", True),
    ("pip install -i= x -f= pip install git+y", True),
    ("pip install -i x -f= pip install https://x", True),
    ("pip install -i -i= pip install https://x", True),
    ("pip install-x pip install https://x", True),
    ("pip install\npip install https://x", True),
    ("pip install -i\npip install https://x", True),
    ("pip install a; pip install https://x", True),
    ("pip install a | pip install https://x", True),
    ("pip install -i= \n pip install https://x", True),
    ("pip install a\tpip install \"https://x", True),
    ("pip install a x-i= pip install https://x", True),
    ("x-i= pip install https://x", True),
    ("-i= pip install https://x", True),
    ("pip install -ix pip install https://x", True),
    ("PIP INSTALL HTTPS://X", True),
    ("pip install a b c", False),
    ("pip install -r req.txt -i x https://y", True),
    ("pip install pip", False),
    ("pip install", False),
]


_MAIN_DOWNLOAD_AND_INSTALL = {"pipe-to-shell": _MAIN_PIPE_TO_SHELL, "unpinned-install": _MAIN_UNPINNED_INSTALL}
_DOWNLOAD_AND_INSTALL_TABLE = [("pipe-to-shell", text, expected) for text, expected in _DOWNLOAD_TABLE] + [
    ("unpinned-install", text, expected) for text, expected in _PIP_INSTALL_TABLE
]


@pytest.mark.parametrize(("rule", "text", "expected"), _DOWNLOAD_AND_INSTALL_TABLE)
def test_download_and_install_rules_read_a_line_as_the_old_patterns_did(rule: str, text: str, expected: bool) -> None:
    assert (_MAIN_DOWNLOAD_AND_INSTALL[rule].search(text) is not None) is expected
    assert (_security_search(rule, text) is not None) is expected


def test_pipe_to_shell_matches_the_old_pattern_on_random_lines_of_downloads() -> None:
    pieces = ["curl", "wget", "x", "|", "sh", "bash", "-c", ";", "&", "\n", "sudo", "python", "-", "$(", "<(", "eval", "source", ".", "(", "env",
              "/bin/sh", "-m", "2>&1", "&&", "curl x", "wget x |", "| sh", "curl x | sh", "xcurl", "||"]
    rng = random.Random(1269)
    hits = 0
    for _ in range(6 * CASES):
        text = rng.choice([" ", " ", ""]).join(rng.choice(pieces) for _ in range(rng.randint(0, 13)))
        new_match = _security_search("pipe-to-shell", text) is not None
        assert new_match == (_MAIN_PIPE_TO_SHELL.search(text) is not None), text
        hits += new_match
    assert hits > 300, hits


def test_unpinned_install_matches_the_old_pattern_on_random_lines_of_installs() -> None:
    pieces = ["pip", "pip3", "install", "-i", "-f", "-i=", "--index-url", "--index-url=", "--find-links=", "x", "https://x", "git+x",
              "--break-system-packages", ";", "\n", "|", "&", "-e", "=", "-I", "-F=", "pip install", "pip install -i", "\"https://y", "-ix",
              "--extra-index-url=", "-i=x", "pip install -i="]
    rng = random.Random(1269_1)
    hits = 0
    for _ in range(6 * CASES):
        text = rng.choice([" ", " ", ""]).join(rng.choice(pieces) for _ in range(rng.randint(0, 13)))
        new_match = _security_search("unpinned-install", text) is not None
        assert new_match == (_MAIN_UNPINNED_INSTALL.search(text) is not None), text
        hits += new_match
    assert hits > 150, hits


@pytest.mark.parametrize(
    ("rule", "shape"),
    [
        ("pipe-to-shell", lambda n: "curl x " * n),
        ("pipe-to-shell", lambda n: "wget x " * n),
        ("pipe-to-shell", lambda n: "curl " * n),
        ("pipe-to-shell", lambda n: "curl x;" * n),
        ("pipe-to-shell", lambda n: "curl x | cat " * n),
        ("unpinned-install", lambda n: "pip install " * n),
        ("unpinned-install", lambda n: "pip install x " * n),
        ("unpinned-install", lambda n: "pip install -i= " * n),
        ("unpinned-install", lambda n: "-i= " * n + "pip install x"),
        ("unpinned-install", lambda n: "pip install x;" * n),
    ],
)
def test_a_line_of_repeated_downloads_or_installs_is_scanned_in_linear_time(rule: str, shape: Callable[[int], str]) -> None:
    _linear(partial(_security_search, rule), shape, n=200)
