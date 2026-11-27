# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Scans that took quadratic time on one long token or line now take one pass, with the old result.

Each section keeps the old regex as the reference and compares the new code with it on a hand table and
on seeded random texts from a small alphabet (https://github.com/intel/dokimi/issues/924, /927 and /1116).
"""

from __future__ import annotations

import random
import re
from collections.abc import Callable
from dataclasses import replace
from functools import partial
from typing import Any

import pytest

from dokimi.harnesses import swebench, swebench_grade
from dokimi.metrics import exploration
from dokimi.repos import security
from tests.growth_timing import GROWTH_LIMIT, growth

CASES = 600


def _random_texts(pieces: list[str], seed: int, longest: int = 14) -> list[str]:
    rng = random.Random(seed)
    return ["".join(rng.choice(pieces) for _ in range(rng.randint(0, longest))) for _ in range(CASES)]


def _linear(call: Callable[[str], Any], shape: Callable[[int], str], n: int = 2_000) -> None:
    ratio, _ = growth(lambda k: partial(call, shape(k)), n)
    assert ratio < GROWTH_LIMIT


# --- exploration._parse_loose (#924) -------------------------------------------------------------

_OLD_LOOSE_CITATION = re.compile(
    r"(?P<open>[(\[{<\"'])?"
    r"(?P<path>[^\s:`]+\.[A-Za-z0-9_+]+)`?"
    r":(?P<start>\d+)(?:\s*[-–:]\s*(?P<end>\d+))?"
    r"(?P<close>[)\]}>\"'])?"
)


def _old_parse_loose(text: str) -> exploration.CitationSet:
    citations = []
    for m in _OLD_LOOSE_CITATION.finditer(text):
        opener = m.group("open")
        cite = exploration._citation_from_match(m)
        assert cite is not None
        if opener in exploration._WRAPPERS and exploration._WRAPPERS[opener] in m.group("path"):
            cite = replace(cite, path=exploration.normalize_path(opener + m.group("path")))
        citations.append(cite)
    return exploration._finish(citations, [])


_WRAPPED = [f"{o}src/a.py:10-20{c}" for o, c in zip("([{<\"'", ")]}>\"'", strict=True)]

LOOSE_TABLE = [
    "",
    "no citation here",
    "see src/a.py:10",
    "see src/a.py:10-20 and src/b.py:3",
    "see src/a.py:10–20",
    "see src/a.py:10:20",
    "see src/a.py:10 - 20",
    "see src/a.py:10 -",
    "see `src/a.py`:42-58 near",
    "`src/a.py`:7",
    "src/a.py`:7",
    "x.py:1:2",
    "x.py:1:2:3",
    "x.py:1 y.py:2",
    "x.py:1y.py:2",
    "a.b.c.d:5",
    "a.b.c.:5",
    ".py:5",
    "a.:5",
    "a.b:",
    "a.b:x",
    "src/a:5",
    "noext:5",
    ":5",
    "a b.py:5",
    "(app)/page.tsx:10",
    "([id].tsx:5) and (app)/x.tsx:3.",
    "((a.py:1))",
    "(.py:1)",
    "(a.py:1",
    "a.py:1)",
    "a.py:1)b.py:2",
    "(a.py:1)(b.py:2)\"c.py:3\"d.py:4",
    "<a.py:1>",
    "\"a.py:1\" 'b.py:2'",
    "a٣.py:٣",
    "x+y_z.c++:12",
    "a.b-c:3",
    "a.py:1\nb.py:2\n",
    *_WRAPPED,
    *(f"see {w} here" for w in _WRAPPED),
]


@pytest.mark.parametrize("text", LOOSE_TABLE)
def test_loose_citations_read_as_the_old_regex_did(text: str) -> None:
    assert exploration.parse_final_answer(text) == _old_parse_loose(text)


def test_loose_citations_match_the_old_regex_on_random_text() -> None:
    texts = _random_texts(["a", ".", ":", "1", "`", "(", ")", " ", "-", "\n", "a.a", ":1"], 924, longest=16)
    assert len({_old_parse_loose(t) for t in texts}) > 20
    for text in texts:
        assert exploration.parse_final_answer(text) == _old_parse_loose(text), repr(text)


def test_loose_citations_match_the_old_regex_on_random_citation_pieces() -> None:
    pieces = ["src/a.py", ":3", "-4", "–5", ":6", "`", "(", ")", "[", "]", "\"", " ", "x.", "\n", "ab"]
    for text in _random_texts(pieces, 925, longest=10):
        assert exploration.parse_final_answer(text) == _old_parse_loose(text), repr(text)


def test_a_long_path_is_still_a_citation() -> None:
    path = "d/" * 5000 + "a.py"
    assert exploration.parse_final_answer(f"{path}:7").citations[0].path == path


def test_a_long_token_without_a_citation_is_scanned_in_linear_time() -> None:
    _linear(exploration.parse_final_answer, lambda k: "a" * k)


def test_unclosed_final_answer_openers_fall_back_in_linear_time() -> None:
    _linear(exploration.parse_final_answer, lambda k: "<final_answer>" * k)


# --- swebench_grade.parse_sympy (#927) -----------------------------------------------------------


def _old_parse_sympy(log: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for match in re.findall(r"(_*) (.*)\.py:(.*) (_*)", log):
        found[f"{match[1]}.py:{match[2]}"] = "FAILED"
    endings = {" E": "ERROR", " F": "FAILED", " ok": "PASSED"}
    for line in log.split("\n"):
        line = line.strip()
        if line.startswith("test_"):
            for suffix, status in endings.items():
                if line.endswith(suffix):
                    found[line.split()[0]] = status
    return found


SYMPY_TABLE = [
    "",
    "test_one ok\ntest_two F\ntest_three E\n",
    "_____ sympy/core/tests/test_a.py:12: test_x _____",
    "________________ sympy/core/tests/test_a.py:12: test_x ________________\nother",
    "_ a.py:1 _",
    " a.py:1 ",
    "a.py:1 ",
    " a.py:1",
    " .py:1 ",
    " .py: ",
    " x.py:",
    " x.py: y",
    "x.py:1 y",
    " a.py:1 b.py:2 c",
    " a.py:1 b.py:2 c.py:3 d_",
    "p a.py:1 q a.py:2 r",
    "__ a b.py:c d.py:e f __ g",
    "  a.py:1  ",
    "a b\nc.py:1 d\n e.py:2 f",
    " a.py:1 b\r\n c.py:2 d\r\n",
    " a.py:1 b\rc.py:2 d",
    " a.py:1 b\x0cc.py:2 d\u2028 e.py:3 f",
    "\n\n a.py:1 b\n\n",
    "no closer .py:1",
    "no opener.py:1 x",
    "_.py:1 x_",
    "x __ .py: __ y",
    "test_a.py:1 F\n x.py:1 y\ntest_b ok",
]


@pytest.mark.parametrize("log", SYMPY_TABLE)
def test_parse_sympy_reads_as_the_old_regex_did(log: str) -> None:
    assert swebench_grade.parse_sympy(log) == _old_parse_sympy(log)


def test_parse_sympy_matches_the_old_regex_on_random_logs() -> None:
    logs = _random_texts(["_", ".", "p", "y", ":", " ", "a", "b", "/", "\n", ".py:", "__"], 927, longest=24)
    assert sum(bool(_old_parse_sympy(t)) for t in logs) > 30
    for log in logs:
        assert swebench_grade.parse_sympy(log) == _old_parse_sympy(log), repr(log)


def test_parse_sympy_scans_a_long_line_in_linear_time() -> None:
    _linear(swebench_grade.parse_sympy, lambda k: "a " * k)


# --- swebench.test_directives (#927) -------------------------------------------------------------

_OLD_DIFF_HEADER = re.compile(r"diff --git a/.* b/(.*)")


def _old_test_directives(repo: str, test_patch: str) -> list[str]:
    found = _OLD_DIFF_HEADER.findall(test_patch)
    found = [d for d in found if not d.endswith(swebench._NON_TEST_EXTS)]
    if repo != "django/django":
        return found
    out = []
    for d in found:
        d = d[: -len(".py")] if d.endswith(".py") else d
        d = d[len("tests/"):] if d.startswith("tests/") else d
        out.append(d.replace("/", "."))
    return out


DIRECTIVES_TABLE = [
    "",
    "diff --git a/tests/test_a.py b/tests/test_a.py\n--- a/tests/test_a.py\n+++ b/tests/test_a.py\n",
    "diff --git a/x.py b/x.py\ndiff --git a/y.py b/y.py\n",
    "diff --git a/docs/a.rst b/docs/a.rst\ndiff --git a/t.py b/t.py",
    "diff --git a/a b/b b/c",
    "diff --git a/a b/b b/",
    "diff --git a/ b/",
    "diff --git a/b/",
    "diff --git a/ b/x",
    "diff --git a/x b/",
    "diff --git a/x b/y\r\ndiff --git a/z b/w\r\n",
    "diff --git a/x b/y\rdiff --git a/z b/w",
    "diff --git a/x b/y\x0cdiff --git a/z b/w\u2028diff --git a/q b/r",
    "diff --git a/x diff --git a/y b/z",
    "diff --git a/x b/y diff --git a/z b/w",
    "b/x diff --git a/y",
    " b/x\ndiff --git a/y",
    "diff --git a/x\n b/y",
    "xdiff --git a/x b/y",
    "diff --git a/tests/t/test_q.py b/tests/t/test_q.py\n",
]


@pytest.mark.parametrize("repo", ["x/y", "django/django"])
@pytest.mark.parametrize("patch", DIRECTIVES_TABLE)
def test_test_directives_read_as_the_old_regex_did(repo: str, patch: str) -> None:
    assert swebench.test_directives(repo, patch) == _old_test_directives(repo, patch)


def test_test_directives_match_the_old_regex_on_random_patches() -> None:
    pieces = ["diff --git a/", " b/", "a", "b", "/", " ", "\n", "\r", ".py", "diff --git a/x b/y"]
    patches = _random_texts(pieces, 928, longest=14)
    assert sum(bool(_old_test_directives("x/y", t)) for t in patches) > 100
    for patch in patches:
        for repo in ("x/y", "django/django"):
            assert swebench.test_directives(repo, patch) == _old_test_directives(repo, patch), repr(patch)


def test_test_directives_scan_a_long_header_line_in_linear_time() -> None:
    _linear(partial(swebench.test_directives, "x/y"), lambda k: "diff --git a/" * k)


# --- security container-escape and hook-bypass (#1116) ---------------------------------------------

_OLD_CONTAINER_ESCAPE = re.compile(
    r"\bdocker\s+(?:container\s+)?(?:run|create)\b.*(?:--privileged|--pid[= ]host|--net(?:work)?[= ]host|"
    r"-v\s*[\"']?/:|--volume[= ][\"']?/:|(?:-v|--volume|--mount)[= ]?\s*[\"']?[^\s\"']*docker\.sock|"
    r"--mount[= ](?:\S*,)?(?:source|src)=/(?:,|\s|[\"']|$)|nsenter\b)",
    re.IGNORECASE,
)
_OLD_HOOK_BYPASS = re.compile(
    r"\bgit\s+(?:-[Cc]\s+[^\s-]\S*\s+|-\S+\s+)*(?:commit\b[^;&|\n]*(?:\s-[aqsv]*n[aqsvn]*(?:[mFC]\S*)?(?=[\s\"';&|)`]|$)"
    r"|--no-verify\b)"
    r"|push\b.*--no-verify\b)",
    re.IGNORECASE,
)
_SECURITY_OLD = {"container-escape": _OLD_CONTAINER_ESCAPE, "hook-bypass": _OLD_HOOK_BYPASS}


def _security_search(rule: str, text: str) -> re.Match[str] | None:
    pattern = security.RULES[rule].pattern
    assert pattern is not None
    return pattern.search(text)

SECURITY_TABLE = [
    "docker run --privileged x",
    "docker run x\n--privileged",
    "docker\nrun --privileged",
    "docker run\n  --privileged",
    "docker run a; docker run b --privileged",
    "docker run a && echo --privileged",
    "docker container create -v /:/h x",
    "docker create -v/: x",
    "docker run -v /var/run/docker.sock:/s x",
    "docker run --mount source=/ x",
    "docker run x nsenter",
    "docker run docker run",
    "docker docker run --privileged",
    "docker container container run --privileged",
    "DOCKER RUN --PRIVILEGED",
    "xdocker run --privileged",
    "docker runx --privileged",
    "docker run a\ndocker run b --net=host",
    "echo docker run; docker run --pid host",
    "git push --no-verify",
    "git push origin x && y --no-verify",
    "git push; echo --no-verify",
    "git push\n--no-verify",
    "git\npush --no-verify",
    "git -c a=b push --no-verify",
    "git -c git push -x push --no-verify",
    "git -C d push origin --no-verify",
    "git push a\ngit push --no-verify",
    "git commit -n; git push --no-verify",
    "git commit --no-verify",
    "git commit -nm x",
    "git commit x; git push --no-verify",
    "git push --no-verifyx",
    "git pushx --no-verify",
    "xgit push --no-verify",
    "git push -n",
    "docker run safe; docker\nrun --privileged",
    "git push safe; git\npush --no-verify",
    "docker run safe; docker container\nrun --privileged",
    "docker run safe; docker\ncontainer run --net=host",
    "docker run safe; docker \n create -v /:/h x",
    "git push safe; git\n-c a=b push --no-verify",
    "git push safe; git -c\na=b push --no-verify",
    "git push safe; git -c a=b\npush --no-verify",
    "git push safe; git -x\npush --no-verify",
    "git -C push\npush --no-verify",
    "git push\ngit\npush a\n--no-verify",
    "docker run safe; docker\nrun safe\n--privileged",
    "git -C git\npush --no-verify",
    "git -C git -C git\npush --no-verify",
    "git\n-C a/git push --no-verify",
    "git -C\na/git push --no-verify",
    "ls -C git\npush --no-verify",
    "git -C a/git\n-c git push --no-verify",
    "git -C git\npush",
    "git -C a/git -C b/git push --no-verify",
    "git -C a/git\npush --no-verify",
    "git -C /usr/bin/git -c x\npush --no-verify",
    "git -C a.git push --no-verify",
    "git -c core.foo=git push --no-verify",
    "git -C a.git\npush --no-verify",
    "git -c core.foo=git\npush --no-verify",
    "git -C a.git\npush",
    "docker run -v/var/run/docker.sock:/x",
    "docker run -v /var/run/docker.sock:/x",
    "docker run -v=/var/run/docker.sock:/x",
    "docker run -v \"/var/run/docker.sock:/x\"",
    "docker run -v '/var/run/docker.sock:/x'",
    "docker run --volume=/var/run/docker.sock:/x",
    "docker run --volume /var/run/docker.sock:/x",
    "docker run --mount source=/var/run/docker.sock,target=/x",
    "docker run --mount type=bind,source=/var/run/docker.sock,target=/x",
    "docker run -vv/var/run/docker.sock:/x",
    "docker run -v-v/var/run/docker.sock",
    "docker run -v-vdocker.sock",
    "docker run -v-v-v/a/docker.sock:/x",
    "docker run -vx-vdocker.sock",
    "docker run --volume--mountdocker.sock",
    "docker run -v--volume=/docker.sock",
    "docker run -vvvvvvvvvv",
    "docker run -v-v-v-v x docker.sock",
    "docker run -v-v-v\"docker.sock",
    "docker run -v-v'docker.sock",
    "docker run -V/DOCKER.SOCK:/x",
    "docker run -v -v docker.sock",
    "docker run -vdocker.sockdocker.sock",
    "docker run -v a-vdocker.sock",
    "docker run -v-vx docker.sock",
    "docker run -v\n-vdocker.sock",
    "docker run -v\n-v-vdocker.sock",
    "docker run --volume\n--mountdocker.sock",
    "docker run -v=\n-v/var/run/docker.sock",
    "docker run -v \n\n 'x-v/docker.sock",
    "docker run -v\nx\n-vdocker.sock",
    "docker run x -v\n--volume docker.sock",
    "git commit -a; git commit -n",
    "git commit -a && git commit --no-verify",
    "git commit -a | git commit -n",
    "git commit -a & git commit -n",
    "git commit -a\ngit commit -n",
    "git commit -a\n git commit -n",
    "git commit git commit -n",
    "git commit -a git commit -n",
    "git commit -nm msg",
    "git commit -anm msg",
    "git commit -am msg",
    "git commit -m n",
    "git commit -F n -n",
    "git commit -n; git commit",
    "git commit -a; git commit -a; git commit -n",
    "git -C d commit -n",
    "git -C d commit -a; git -C e commit -n",
    "git -c a=b commit --no-verify",
    "git -c a=b\ncommit --no-verify",
    "git\ncommit -n",
    "git commit\n-n",
    "git -x;y commit -n",
    "git -x;git commit -n",
    "git -x/git commit -n",
    "git -C git commit -n",
    "git -C commit commit -n",
    "git -C commit -n",
    "echo git commit; git commit -n",
    "echo git; git commit --no-verify",
    "xgit commit -n",
    "git commitx -n",
    "(git commit -n)",
    "git commit -a x;git commit -n",
    "git commit -a x|git commit -n",
    "git commit -a\n-n",
    "git commit -a\r\ngit commit -n",
    "GIT COMMIT --NO-VERIFY",
    "git commit; git push --no-verify",
    "git commit -a git push --no-verify",
    "git commit -F git\n -C\n-n -x;y -C& commit -c -x/git (x -n ",
    "git commit git\n-x commit -n",
    "git commit git -x;y commit -n",
    "git commit a git -C commit\ncommit -n",
    "git commit a git\n\ncommit -n",
    "git commit a git -C x;y commit -n",
    "git commit a git -C\nx commit -n",
    "git commit a git -C x\n commit -n",
    "git commit a git -C;x commit -n",
    "git commit a git -C x\n;commit -n",
    "git commit a git -x\ncommit; git commit -n",
]


@pytest.mark.parametrize("text", SECURITY_TABLE)
@pytest.mark.parametrize("rule", sorted(_SECURITY_OLD))
def test_security_rules_read_a_line_as_the_old_pattern_did(rule: str, text: str) -> None:
    assert (_security_search(rule, text) is None) == (_SECURITY_OLD[rule].search(text) is None)


def test_security_rules_match_the_old_patterns_on_random_command_lines() -> None:
    pieces = ["docker", "run", "create", "container", "--privileged", "-v", "/:", "docker.sock", "nsenter",
              "git", "push", "--no-verify", ";", "&&", "|", "\n", "x", "-c", "a=b", "-C", "--mount", "--net=host",
              "docker run", "docker container create", "git push", "git -c a=b push", "--privileged", "--no-verify",
              "docker\nrun", "docker container\ncreate", "docker\n container run", "git\npush", "git -c\na=b push",
              "git -c a=b\npush", "git\n-x push", "-C git", "-c a/git", "git -C git", "-C\ngit"]
    rng = random.Random(1116)
    texts = [" ".join(rng.choice(pieces) for _ in range(rng.randint(0, 12))) for _ in range(CASES)]
    hits = {rule: 0 for rule in _SECURITY_OLD}
    for text in texts:
        for rule, old in _SECURITY_OLD.items():
            new_match = _security_search(rule, text) is not None
            assert new_match == (old.search(text) is not None), (rule, text)
            hits[rule] += new_match
    assert all(count > 30 for count in hits.values()), hits


def test_container_escape_matches_the_old_closer_on_random_words_of_volume_options() -> None:
    pieces = ["-v", "--volume", "--mount", "-V", "=", " ", "\n", "\"", "'", "docker.sock", "/", "/:", "x", "-", "v"]
    rng = random.Random(1160)
    hits = 0
    for _ in range(3 * CASES):
        text = "docker run " + "".join(rng.choice(pieces) for _ in range(rng.randint(0, 12)))
        new_match = _security_search("container-escape", text) is not None
        assert new_match == (_OLD_CONTAINER_ESCAPE.search(text) is not None), text
        hits += new_match
    assert hits > 100, hits


def test_hook_bypass_matches_the_old_pattern_on_random_lines_of_commits() -> None:
    pieces = ["git", "commit", "-n", "-a", "-nm", "-m", "--no-verify", "-C", "-c", "d", "a=b", ";", "&", "|", "&&", "\n", " ",
              "git commit", "git -C d commit", "git commit -a", "git commit -n", "-x;y", "x/git", "push", "(", ")", "'", "-F", "msg"]
    rng = random.Random(1161)
    hits = 0
    for _ in range(3 * CASES):
        text = " ".join(rng.choice(pieces) for _ in range(rng.randint(0, 12)))
        new_match = _security_search("hook-bypass", text) is not None
        assert new_match == (_OLD_HOOK_BYPASS.search(text) is not None), text
        hits += new_match
    assert hits > 200, hits


@pytest.mark.parametrize(
    ("rule", "shape"),
    [
        ("hook-bypass", lambda n: "git commit " * n),
        ("hook-bypass", lambda n: "git commit -a " * n),
        ("hook-bypass", lambda n: "git -C d commit " * n),
        ("hook-bypass", lambda n: "git\ncommit " * n),
        ("hook-bypass", lambda n: "git commit;" * n),
        ("hook-bypass", lambda n: "git -x;y commit " * n),
        ("hook-bypass", lambda n: "git -C a;b commit " * n),
        ("container-escape", lambda n: "docker run " + "-v" * n),
        ("container-escape", lambda n: "docker run " + "--volume" * n),
        ("container-escape", lambda n: "docker run " + "--mount" * n),
        ("container-escape", lambda n: "docker run -v" + "-vx" * n),
        ("container-escape", lambda n: "docker run -v\n" + "-v" * n),
        ("container-escape", lambda n: "docker run " + "-v\n-v" * n),
        ("container-escape", lambda n: "docker run " * n),
        ("container-escape", lambda n: "docker run a " * n),
        ("hook-bypass", lambda n: "git push " * n),
        ("container-escape", lambda n: "docker\nrun " * n),
        ("container-escape", lambda n: "docker container\n" * n),
        ("hook-bypass", lambda n: "git\npush " * n),
        ("hook-bypass", lambda n: "git -a -b\n" * n),
        ("hook-bypass", lambda n: "git " + "-a " * n),
        ("history-rewrite", lambda n: "git push " * n),
        ("history-rewrite", lambda n: "git reset " * n),
    ],
)
def test_security_rules_scan_a_line_of_openers_in_linear_time(rule: str, shape: Callable[[int], str]) -> None:
    _linear(partial(_security_search, rule), shape, n=200)


_OVERLAPPING_GIT_SHAPES = [
    lambda n: "git -C " * n + "git\npush",
    lambda n: "git\n-C " * n + "git\npush",
    lambda n: "git -C a/" * n + "git\npush",
    lambda n: "git\n-C a/" * n + "git\npush",
    lambda n: "git -C a." * n + "git\npush",
    lambda n: "git -c core.foo=" * n + "git\npush",
    lambda n: "git\n-c core.foo=" * n + "git\npush",
    lambda n: "git " + "-a/git " * n + "x",
    lambda n: "git " + "-x.git " * n + "x",
    lambda n: "git " + "-a/git\n" * n + "x",
    lambda n: "git push " + "-a/git " * n,
    lambda n: "git -x;y " + "-a/git " * n + "x",
]


@pytest.mark.parametrize("shape", _OVERLAPPING_GIT_SHAPES)
def test_overlapping_git_openers_are_scanned_in_linear_time(shape: Callable[[int], str]) -> None:
    # the push alternative alone: the commit alternative of hook-bypass is timed below, with a tail that holds no commit
    _linear(security._PUSH_NO_VERIFY.search, shape, n=200)


@pytest.mark.parametrize("shape", _OVERLAPPING_GIT_SHAPES)
def test_overlapping_git_openers_are_scanned_in_linear_time_by_the_commit_alternative(shape: Callable[[int], str]) -> None:
    _linear(security._rx(security._COMMIT_NO_VERIFY).search, shape, n=200)


@pytest.mark.parametrize("shape", _OVERLAPPING_GIT_SHAPES)
def test_overlapping_git_openers_are_scanned_in_linear_time_by_history_rewrite(shape: Callable[[int], str]) -> None:
    _linear(partial(_security_search, "history-rewrite"), shape, n=200)


_OLD_HISTORY_REWRITE = re.compile(
    r"\bgit\s+(?:-[Cc]\s+[^\s-]\S*\s+|-\S+\s+)*push\b[^;&|\n]*(?:\s-[a-z]*f[a-z]*\b|--force\b(?!-with-lease|-if-includes)"
    r"|\s\+\S)|\bgit\s+(?:-[Cc]\s+[^\s-]\S*\s+|-\S+\s+)*reset\b[^;&|\n]*\s--hard(?![\w-])",
    re.IGNORECASE,
)
_C_ARGUMENT_TABLE = [
    "git -C git commit -n",
    "git -C a/git commit -n",
    "git -C a.git commit --no-verify",
    "git -C my-dir commit -n",
    "git -C a-b/git commit -n",
    "git -C git -C git commit -n",
    "git -C a.git -C b.git commit -n",
    "git -c core.foo=git commit -n",
    "git -C git\ncommit -n",
    "git\n-C a/git commit -n",
    "git -C a/git\n-c git commit -n",
    "git -C git git commit -n",
    "git -C git; git commit -n",
    "git -C git commit -a; git -C git commit -n",
    "git -C git git -C git commit -n",
    "git -C git push -f",
    "git -C a/git push --force",
    "git -C a.git -C b.git push +main",
    "git -C a.git\npush -f",
    "git -C git reset --hard",
    "git -C a.git -C git reset --hard",
    "git -C git\nreset --hard",
    "git -C git -f push -f",
    "git -C commit commit -n",
    "git -C git; git push -f",
    "git -C git push -f; git -C git reset --hard",
    "git -C git push",
    "git -C a.git reset --soft",
]


@pytest.mark.parametrize("text", _C_ARGUMENT_TABLE)
def test_a_git_word_as_the_argument_of_c_is_read_as_the_old_patterns_did(text: str) -> None:
    assert (_security_search("hook-bypass", text) is None) == (_OLD_HOOK_BYPASS.search(text) is None)
    assert (_security_search("history-rewrite", text) is None) == (_OLD_HISTORY_REWRITE.search(text) is None)


def test_git_c_arguments_match_the_old_patterns_on_random_lines() -> None:
    pieces = ["git", "commit", "push", "reset", "-n", "-f", "--hard", "--force", "--no-verify", "-C", "-c", "a/git", "a.git",
              "core.foo=git", "d", ";", "&&", "\n", "git -C git", "git -C a/git", "-C git", "commit -n", "push -f", "x"]
    rng = random.Random(1175)
    hits = {"hook-bypass": 0, "history-rewrite": 0}
    for _ in range(3 * CASES):
        text = " ".join(rng.choice(pieces) for _ in range(rng.randint(0, 12)))
        for rule, old in (("hook-bypass", _OLD_HOOK_BYPASS), ("history-rewrite", _OLD_HISTORY_REWRITE)):
            new_match = _security_search(rule, text) is not None
            assert new_match == (old.search(text) is not None), (rule, text)
            hits[rule] += new_match
    assert all(count > 100 for count in hits.values()), hits


# --- history-rewrite: the first git push / git reset of each segment (#1212) -------------------------

_HISTORY_REWRITE_TABLE = [
    ("git push -f", True),
    ("git push --force", True),
    ("git push --force-with-lease", False),
    ("git push --force-with-lease --force", True),
    ("git push +main", True),
    ("git push origin main", False),
    ("git reset --hard", True),
    ("git reset --hard-x", False),
    ("git reset --soft", False),
    ("git -C d push -f", True),
    ("git -C d reset --hard", True),
    ("git\npush -f", True),
    ("git push\n-f", True),
    ("git\nreset --hard", True),
    ("git reset\n--hard", True),
    ("git push; git push -f", True),
    ("git push; git push", False),
    ("git push -f; git push", True),
    ("git push && git reset --hard", True),
    ("git reset --soft && git push -f", True),
    ("git push x git push -f", True),
    ("git push x git reset --hard", True),
    ("git reset x git reset --hard", True),
    ("git reset x git push -f", True),
    ("git push;-f", False),
    ("git push;\n-f", False),
    ("git reset |\n--hard", False),
    ("git reset;--hard", False),
    ("git push -x;y -f", False),
    ("git -x;y push -f", True),
    ("git -x;y push\n-f", True),
    ("git -x;y reset --hard", True),
    ("git -x;y;z reset --hard", True),
    ("git -C a;b push -f", True),
    ("git -C a;b push; git -f", False),
    ("git\n-x;y\npush -f", True),
    ("git -x\npush -f", True),
    ("git -x\n-y;z reset --hard", True),
    ("git x push -f", False),
    ("git -C push push -f", True),
    ("git -C push -f", True),
    ("git -a git push -f", True),
    ("git push --force-with-lease git push -f", True),
    ("a;git push -f", True),
    ("echo|git push -f", True),
    ("echo\ngit reset --hard", True),
    ("xgit push -f", False),
    ("GIT PUSH -F", True),
    ("git push -n; git push -fu", True),
    ("git push -n git reset; git reset --hard", True),
    ("git reset a; git push b; git reset --hard", True),
    ("git push a git push b git push c -f", True),
    ("git push a; git push b; git push c", False),
]


@pytest.mark.parametrize(("text", "expected"), _HISTORY_REWRITE_TABLE)
def test_history_rewrite_reads_a_line_as_the_old_pattern_did(text: str, expected: bool) -> None:
    assert (_OLD_HISTORY_REWRITE.search(text) is not None) is expected
    assert (_security_search("history-rewrite", text) is not None) is expected


def test_history_rewrite_matches_the_old_pattern_on_random_lines_of_pushes_and_resets() -> None:
    pieces = ["git", "push", "reset", "-f", "-fu", "-n", "--force", "--force-with-lease", "+main", "--hard", "--soft", "-C", "-c",
              "d", "a=b", ";", "&", "|", "&&", "\n", " ", "x", "git push", "git reset", "git -C d push", "git -c a=b reset", "-x;y",
              "x/git", "git\npush", "git -x\nreset", "git push -f", "git reset --hard", "(", "'"]
    rng = random.Random(1212)
    hits = 0
    for _ in range(3 * CASES):
        text = " ".join(rng.choice(pieces) for _ in range(rng.randint(0, 12)))
        new_match = _security_search("history-rewrite", text) is not None
        assert new_match == (_OLD_HISTORY_REWRITE.search(text) is not None), text
        hits += new_match
    assert hits > 200, hits


# --- an option word that ends in `git` (#1213) ---------------------------------------------------------

# `_PUSH_NO_VERIFY` as it was before an option word could not end in `git`
_OLD_PUSH_NO_VERIFY = re.compile(
    r"(?:(?m:^)(?:(?!\bgit\s+(?:-[Cc]\s+(?!\S*\bgit\s)[^\s-]\S*\s+|-\S+\s+)*push\b)[^\n])*\bgit\s+(?:-[Cc]\s+(?!\S*\bgit\s)[^\s-]\S*\s+|-\S+\s+)*push\b"
    r"|\bgit(?:[^\S\n]*\n\s*(?:-[Cc]\s+(?!\S*\bgit\s)[^\s-]\S*\s+|-\S+\s+)*|[^\S\n]+(?:-[Cc][^\S\n]+(?!\S*\bgit\s)[^\s-]\S*[^\S\n]+|-\S+[^\S\n]+)*"
    r"(?:-[Cc][^\S\n]*\n\s*(?!\S*\bgit\s)[^\s-]\S*\s+|-[Cc][^\S\n]+(?!\S*\bgit\s)[^\s-]\S*[^\S\n]*\n\s*|-\S+[^\S\n]*\n\s*)"
    r"(?:-[Cc]\s+(?!\S*\bgit\s)[^\s-]\S*\s+|-\S+\s+)*)push\b)[^\n]*(?:--no-verify\b)",
    re.IGNORECASE,
)
# (line, hook-bypass, history-rewrite, the push branch alone) matches, the same for the old patterns
_GIT_WORD_OPTION_TABLE = [
    ("git -x/git push --no-verify", 1, 0, 1),
    ("git -a/git commit -n", 1, 0, 0),
    ("git -a/git push --no-verify", 1, 0, 1),
    ("git -a/git push -f", 0, 1, 0),
    ("git -a/git reset --hard", 0, 1, 0),
    ("git --git-dir=x/git push --no-verify", 1, 0, 1),
    ("git --git-dir=x push --no-verify", 1, 0, 1),
    ("git -C a/git push -f", 0, 1, 0),
    ("git -a/git\npush --no-verify", 1, 0, 1),
    ("git -a/git\ncommit -n", 1, 0, 0),
    ("git -a/git\nreset --hard", 0, 1, 0),
    ("git -a/git; push --no-verify", 1, 0, 1),
    ("git -a/git; commit -n", 1, 0, 0),
    ("git -x.git commit --no-verify", 1, 0, 0),
    ("git -a/git -b/git commit -n", 1, 0, 0),
    ("git -a/git git commit -n", 1, 0, 0),
    ("git -a/git -b commit -n", 1, 0, 0),
    ("git -a/git commit", 0, 0, 0),
    ("git x -a/git commit -n", 1, 0, 0),
    ("git -x\n-a/git commit -n", 1, 0, 0),
    ("git -x\n-a/git\ncommit -n", 1, 0, 0),
    ("git -a/git -C d push --no-verify", 1, 0, 1),
    ("-a/git push --no-verify", 1, 0, 1),
    ("git -a/git", 0, 0, 0),
    ("git commit -a/git -n", 1, 0, 0),
    ("git push -a/git --no-verify", 1, 0, 1),
    ("git push -a/git +x", 0, 1, 0),
    ("git -x;y -a/git commit -n", 1, 0, 0),
    ("git -x;a/git commit -n", 1, 0, 0),
    ("git -x;a/git\ncommit -n", 1, 0, 0),
    ("git -a/git -x;y commit -n", 1, 0, 0),
    ("git -git commit -n", 1, 0, 0),
    ("git -git push -f", 0, 1, 0),
    ("git -a/gitx commit -n", 1, 0, 0),
    ("git -a/GIT commit -n", 1, 0, 0),
    ("git -a/git\tcommit -n", 1, 0, 0),
    ("git -a/git x commit -n", 0, 0, 0),
    ("git -C x -a/git commit -n", 1, 0, 0),
    ("git -a/git -c core.foo=git push -f", 0, 1, 0),
    ("git -a/git\n-b/git\npush --no-verify", 1, 0, 1),
    ("git -a/git\n-b/git\nreset --hard", 0, 1, 0),
    ("x;git -a/git commit -n", 1, 0, 0),
    ("git -a/git commit; git -b/git commit -n", 1, 0, 0),
]


def _git_word_option_pairs() -> list[tuple[str, re.Pattern[str], re.Pattern[str]]]:
    hook, history = security.RULES["hook-bypass"].pattern, security.RULES["history-rewrite"].pattern
    assert hook is not None and history is not None
    return [("hook-bypass", hook, _OLD_HOOK_BYPASS), ("history-rewrite", history, _OLD_HISTORY_REWRITE), ("push", security._PUSH_NO_VERIFY, _OLD_PUSH_NO_VERIFY)]


@pytest.mark.parametrize(("text", "hook", "history", "push"), _GIT_WORD_OPTION_TABLE)
def test_an_option_word_that_ends_in_git_is_read_as_the_old_patterns_did(text: str, hook: int, history: int, push: int) -> None:
    for (name, new, old), expected in zip(_git_word_option_pairs(), (hook, history, push), strict=True):
        assert (old.search(text) is not None) is bool(expected), (name, text)
        assert (new.search(text) is not None) is bool(expected), (name, text)


def test_option_words_that_end_in_git_match_the_old_patterns_on_random_lines() -> None:
    pieces = ["a/git", "-x.git", "-a/git", "git", "commit", "push", "reset", "-n", "-f", "--no-verify", "--hard", ";", "\n", "-C", "-c", "d",
              "-a", "x", "git -a/git", "git commit", "git push", "git reset", "--git-dir=x/git", "-git"]
    rng = random.Random(1213)
    hits = [0, 0, 0]
    for _ in range(4 * CASES):
        text = " ".join(rng.choice(pieces) for _ in range(rng.randint(0, 12)))
        for k, (name, new, old) in enumerate(_git_word_option_pairs()):
            new_match = new.search(text) is not None
            assert new_match == (old.search(text) is not None), (name, text)
            hits[k] += new_match
    assert all(count > 50 for count in hits), hits
