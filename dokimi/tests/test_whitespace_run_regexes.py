# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Regexes with two whitespace runs around an optional token read a long run of spaces in linear time (#1074).

Each section keeps the old regex as the reference and compares the new one with it on a hand table and on
seeded random strings, then times the new one on a run of whitespace that ends in a failure.
"""

from __future__ import annotations

import random
import re
from collections.abc import Callable
from typing import Any

import pytest

from dokimi.context.prefilter import _TABLE_DELIMITER
from dokimi.external.frontmatter_shape import YAML_KEY_RE
from dokimi.harnesses.tbench_v1 import _SIZE_RE
from dokimi.memory.store import _EMPTY_LINK
from dokimi.novelty.spans import _PROTECTION_RULES
from dokimi.repos.l0.adapters import _BZL_BINDING, _BZL_TUPLE_BINDING, _bzl_public
from dokimi.repos.shell_sed import _PRINT_ONLY
from dokimi.task_formats.imports.inspect_ai import _DOC_SECTION
from tests.growth_timing import GROWTH_LIMIT, growth

CASES = 1500
LONGEST = 12


def _random_texts(pieces: list[str], seed: int, longest: int = LONGEST) -> list[str]:
    rng = random.Random(seed)
    return ["".join(rng.choice(pieces) for _ in range(rng.randint(0, longest))) for _ in range(CASES)]


def _compare(old: re.Pattern[str], new: re.Pattern[str], texts: list[str], read: Callable[[re.Pattern[str], str], Any]) -> int:
    """Assert ``read`` gives the same result for both patterns on every text; the number of texts where it is true."""
    hits = 0
    for text in texts:
        expected = read(old, text)
        assert read(new, text) == expected, repr(text)
        hits += bool(expected)
    return hits


def _span(m: re.Match[str] | None) -> tuple[Any, ...] | None:
    return None if m is None else (m.span(), m.groups())


def _ratio(read: Callable[[str], Any], text_of: Callable[[int], str], n: int = 100) -> float:
    return growth(lambda size: (lambda text: lambda: read(text))(text_of(size)), n)[0]


# --- context/prefilter._TABLE_DELIMITER ----------------------------------------------------------

_OLD_TABLE_DELIMITER = re.compile(r"\s*\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)*\|?\s*")

_TABLE_ROWS = [
    "| --- | --- |", "| --- | :-: | --- |", "|:---|---:|", "---", "--", "-", ":-:", "|-|", "|---|", "| --- |",
    "--- | ---", "---|---|", "|---|---", "| :--- | ---: |", "  | --- |  ", "\t|---|\t", "|---\n", "\n|---|", "| --- |\n",
    "| --- | | --- |", "| --- |  | --- |", "||", "|", "| |", "| | |", " | ", "| -- x |", "| --- | x", "x | ---",
    "| ::--- |", "|:-:-:|", "| - - |", "| -\n- |", "|\n---\n|", "", " ", "\t", "\n", "| --- |||", "|||---",
    "|---" + " " * 40 + "|", " " * 40 + "|---", "|---" + " " * 40 + "x", " " * 40 + "|",
]


def _delimiter_texts() -> list[str]:
    return [*_TABLE_ROWS, *_random_texts(["|", "-", ":", " ", "\t", "a"], seed=1074),
            *_random_texts(["|", "--", ":", " ", "\t", "\n"], seed=1075)]


def test_table_delimiter_accepts_the_strings_the_old_regex_did() -> None:
    texts = _delimiter_texts()
    hits = _compare(_OLD_TABLE_DELIMITER, _TABLE_DELIMITER, texts, lambda rx, t: rx.fullmatch(t) is not None)
    assert 20 < hits < len(texts) - 20  # both outcomes are covered


# --- repos/l0/adapters._BZL_BINDING --------------------------------------------------------------

_OLD_BZL_BINDING = re.compile(
    r"^def\s+([A-Za-z]\w*)\s*\(|^([A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*)\s*,?\s*=(?!=)", re.M)

_BZL_LINES = [
    "a = 1", "a, b = 1", "a ,= 1", "a == 1", "a,b ,  = 1", " a = 1", "a=1", "a,= 1", "a , = 1", "a, , = 1", "a,\n= 1",
    "a\n= 1", "def f(x):", "def  f (x)", "def f", "_x = 1", "a, _b, c = d", "a, b", "a,", "x = y = z", "a !=1", "a <= 1",
    "a" + " " * 40 + "=", "a" + " " * 40 + ",=", "a" + " " * 40 + "x",
]


def _bindings(rx: re.Pattern[str], text: str) -> list[Any] | None:
    return [[m.group(1), m.group(2), m.end()] for m in rx.finditer(text)] or None


def test_bzl_binding_finds_the_assignments_the_old_regex_did() -> None:
    texts = ["\n".join(_BZL_LINES), *_BZL_LINES,
             *_random_texts(["a", "b", ",", "=", " ", "\n", "def f(", "\t"], seed=7405, longest=14)]
    hits = _compare(_OLD_BZL_BINDING, _BZL_BINDING, texts, _bindings)
    assert 20 < hits < len(texts) - 20


# --- memory/store._EMPTY_LINK --------------------------------------------------------------------

_OLD_EMPTY_LINK = re.compile(r"\]\(\s*(?:<>)?\s*\)")


def test_empty_link_reads_the_destinations_the_old_regex_did() -> None:
    texts = ["](", "]()", "]( )", "](<>)", "]( <> )", "](<>", "](< >)", "](\n<>\n)", "]()x", "]( x)", "](<><>)",
             *_random_texts(["](", ")", "<>", " ", "\n", "\t", "x", "<"], seed=7406)]
    hits = _compare(_OLD_EMPTY_LINK, _EMPTY_LINK, texts, lambda rx, t: _span(rx.match(t)))
    assert 20 < hits < len(texts) - 20


# --- external/frontmatter_shape.YAML_KEY_RE ------------------------------------------------------

_OLD_YAML_KEY = re.compile(r"""\s*(?:-\s+)?[{\[]?\s*["']?[\w][\w.-]*["']?\s*:""")


def test_yaml_key_reads_the_keys_the_old_regex_did() -> None:
    texts = ["title: x", "  name: x", '"name": x', "{name: x", "- name: x", "- { name: x", "[ a: b", "-name: x", "-  [ \"a\" : 1",
             "{  'a.b-c' :", "name", ":", "a b: c", "\tkey:\n", "- - a:", "{{a:", *_random_texts(
                 ["-", "{", "[", "a", ":", " ", "\t", '"', "'", "."], seed=7407)]
    hits = _compare(_OLD_YAML_KEY, YAML_KEY_RE, texts, lambda rx, t: _span(rx.match(t)))
    assert 20 < hits < len(texts) - 20


# --- repos/shell_sed._PRINT_ONLY -----------------------------------------------------------------

_OLD_PRINT_ONLY = re.compile(r"\s*(?:(?:\d+|\$)(?:\s*,\s*(?:\d+|\$))?)?\s*p\s*")


def test_print_only_script_reads_the_scripts_the_old_regex_did() -> None:
    texts = ["p", "5p", "1,80p", "$p", " p ", "1 , 3 p", "1,$p", "1,p", ",3p", "5 p", " 5p ", "p p", "1\t,\t$ p", "5", "",
             *_random_texts(["p", "1", "$", ",", " ", "\t", "d", "5"], seed=7408)]
    hits = _compare(_OLD_PRINT_ONLY, _PRINT_ONLY, texts, lambda rx, t: rx.fullmatch(t) is not None)
    assert 20 < hits < len(texts) - 20


# --- task_formats/imports/inspect_ai._DOC_SECTION ------------------------------------------------

_OLD_DOC_SECTION = re.compile(r"^\s*(?:Args|Arguments|Parameters|Returns|Return|Yields|Raises|Examples?|Notes?|"
                              r"Attributes|See Also|Keyword Args)\s*:?\s*$|^\s*-{3,}\s*$", re.I)


def test_doc_section_reads_the_headings_the_old_regex_did() -> None:
    texts = ["Args:", "Args", "  Returns :  ", "args : ", "See Also", "Keyword Args:", "Args: x", "Args x", "---", " ---- ",
             "--", "Note:", "Notes", "Examples\n", "Args\n:", "Args:\n", "Args::", "",
             *_random_texts(["Args", "Note", "---", ":", " ", "\t", "\n", "x"], seed=7409)]
    hits = _compare(_OLD_DOC_SECTION, _DOC_SECTION, texts, lambda rx, t: _span(rx.match(t)))
    assert 20 < hits < len(texts) - 20


# --- novelty/spans version_gate ------------------------------------------------------------------

_OLD_VERSION_GATE = re.compile(
    r"(\bv?\d+\.\d+(\.\d+)?\b|\bsince\b|\bas of\b|\brequires?\b\s+\S*\s*\d|"
    r"\bonly (?:on|in|with)\b|\bfrom\s+\d|\b(?:>=|<=|>|<)\s*\d)", re.I)
_VERSION_GATE = dict(_PROTECTION_RULES)["version_gate"]


def test_version_gate_reads_the_phrases_the_old_regex_did() -> None:
    texts = ["requires 3", "requires Python 3", "requires python3", "Requires  >=  3", "require 12", "requires x", "requires",
             "requires 3.11", "requires\n3", "requires \t Python \t 3", "requires a b 3", "requires 3a", "requires  ",
             "requires !" + " " * 40, "requires" + " " * 40 + "7", "requires" + " " * 40 + "py" + " " * 40 + "7",
             *_random_texts(["requires", "require", " ", "\t", "3", "x", ">=", "."], seed=7410)]
    hits = _compare(_OLD_VERSION_GATE, _VERSION_GATE, texts, lambda rx, t: _span(rx.search(t)))
    assert 20 < hits < len(texts) - 20


# --- harnesses/tbench_v1._SIZE_RE (#1119) --------------------------------------------------------

_OLD_SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([kmgt]?)i?b?\s*$", re.IGNORECASE)


def test_size_reads_the_values_the_old_regex_did() -> None:
    texts = ["512", "512 ", " 512", "512m", "512 MB", "1.5g", "1.5 GiB", "2T", "10k", "10kb", "10 b", "512x", "1.", ".5", "",
             "1\n", "1\t m \n", "1 m\nb", "1" + " " * 40 + "m", "1" + " " * 40 + "x", "1 m b",
             *_random_texts(list("0123456789.  \t\n") + ["k", "m", "g", "t", "i", "b", "K", "M", "B", "x"], seed=1119)]
    hits = _compare(_OLD_SIZE_RE, _SIZE_RE, texts, lambda rx, t: _span(rx.match(t)))
    assert 20 < hits < len(texts) - 20


@pytest.mark.parametrize("value", ["512", "512 ", " 512\t"])
def test_size_without_a_unit_keeps_an_empty_unit_group(value: str) -> None:
    m = _SIZE_RE.match(value)
    assert m is not None
    assert m.group(2) == ""


# --- repos/l0/adapters._bzl_public, the form it does not read (#1120) ----------------------------

_OLD_BZL_TUPLE_BINDING = re.compile(r"^[(\[][^\n]*\)?\]?\s*=(?!=)", re.M)

_BZL_TUPLE_TEXTS = [
    "(a, b) = f()", "[a, b] = f()", "(a) = 1", "[a]=1", "(a, b)\n= f()", "(a, b)\n\n  \n = f()", "(a == b", "(a == b)", "(a ==\n= b",
    "(a\n==", "(a\n= =", "(\n=", "(x\n(y) = 1", "x = (1)", "a = 1\n(b) = 2", "a = 1\n(b)", "def f(): pass\n[x]\n", "(a)\r\n= 1",
    "(a)\f= 1", "(a)\x0b\n= 1", "(a) \n= 1", "(a) \n \n x = 1", "(a) \n \n", "(a" + " " * 40 + "=", "(" + " " * 40 + "x",
    "[" + " " * 40 + "\n" + " " * 40 + "=", "(a" + " " * 40 + "\n" + " " * 40 + "x = 1", "[a] = =", "(a)=",
]


def test_tuple_binding_reports_the_files_the_old_regex_did() -> None:
    texts = [*_BZL_TUPLE_TEXTS, "\n".join(_BZL_TUPLE_TEXTS),
             *_random_texts(["(", "[", "x", ")", "]", "=", "==", " ", "\t", "\n", "\n\n", "\r", "\f", " "], seed=1120, longest=14)]
    hits = _compare(_OLD_BZL_TUPLE_BINDING, _BZL_TUPLE_BINDING, texts, lambda rx, t: rx.search(t) is not None)
    assert 20 < hits < len(texts) - 20


def test_bzl_public_flags_a_form_it_does_not_read() -> None:
    assert _bzl_public("a = 1\n(b, c) = f()\n") == ({"a"}, True)
    assert _bzl_public("a = 1\nb = (2)\n") == ({"a", "b"}, False)


# --- growth: a run of whitespace that ends in a failure ------------------------------------------

_GROWTH = [
    ("table, spaces then a pipe", lambda t: _TABLE_DELIMITER.fullmatch(t), lambda n: " " * n + "|"),
    ("table, cell then spaces then x", lambda t: _TABLE_DELIMITER.fullmatch(t), lambda n: "|--" + " " * n + "x"),
    ("table, a cell then spaces then x", lambda t: _TABLE_DELIMITER.fullmatch(t), lambda n: "-" + " " * n + "x"),
    ("table, spaces either side of a pipe", lambda t: _TABLE_DELIMITER.fullmatch(t),
     lambda n: "-" + " " * n + "|" + " " * n + "x"),
    ("assignment, name then spaces then x", lambda t: _BZL_BINDING.search(t), lambda n: "a" + " " * n + "x"),
    ("assignment, name then spaces then comma", lambda t: _BZL_BINDING.search(t), lambda n: "a" + " " * n + "," + " " * n + "x"),
    ("empty link", lambda t: _EMPTY_LINK.match(t), lambda n: "](" + " " * n + "x"),
    ("yaml key, spaces then x", lambda t: YAML_KEY_RE.match(t), lambda n: " " * n + "x"),
    ("yaml key, dash then spaces", lambda t: YAML_KEY_RE.match(t), lambda n: "-" + " " * n + "x"),
    ("sed script, spaces then x", lambda t: _PRINT_ONLY.fullmatch(t), lambda n: " " * n + "x"),
    ("sed script, spaces then a number", lambda t: _PRINT_ONLY.fullmatch(t), lambda n: " " * n + "1x"),
    ("docstring heading, spaces then x", lambda t: _DOC_SECTION.match(t), lambda n: "Args" + " " * n + "x"),
    ("version gate, requires then spaces", lambda t: _VERSION_GATE.search(t), lambda n: "requires" + " " * n + "!"),
    ("size, number then spaces then x", lambda t: _SIZE_RE.match(t), lambda n: "1" + " " * n + "x"),
    ("bzl tuple binding, paren then spaces", lambda t: _bzl_public(t), lambda n: "(" + " " * n + "x"),
    ("bzl tuple binding, bracket then spaces", lambda t: _bzl_public(t), lambda n: "[" + " " * n + "x"),
]


@pytest.mark.parametrize(("read", "text_of"), [pytest.param(r, t, id=name) for name, r, t in _GROWTH])
def test_a_run_of_whitespace_is_read_in_linear_time(read: Callable[[str], Any], text_of: Callable[[int], str]) -> None:
    ratio = _ratio(read, text_of)
    assert ratio < GROWTH_LIMIT, f"CPU time grew {ratio:.0f}x for 16x the whitespace"
