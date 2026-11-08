# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""Fuzz targets: the parsers that read model replies and user-written Markdown.

A judge or agent reply is untrusted text, so each parser here must, for any
input, return a value of its documented type or raise its documented error, and
nothing else. Every target takes one ``str`` and asserts that contract.
:func:`run` maps one fuzz input to a target and its text; the atheris harness
(``fuzz_parsers.py``) and the corpus replay in ``tests/test_fuzz_targets.py``
both call it, so a seed reaches the same target in either.
"""
from __future__ import annotations

from typing import Callable

import yaml

from dokimi.eval.checklist import parse_yes_no
from dokimi.eval.pairwise import parse_choice
from dokimi.eval.reference_checks import parse_json
from dokimi.llm_json import extract_json_array, extract_json_object
from dokimi.novelty.retention import parse_verdict
from dokimi.utils import parse_frontmatter, parse_frontmatter_strict


def yes_no(text: str) -> None:
    assert parse_yes_no(text) in (True, False, None)


def choice(text: str) -> None:
    assert parse_choice(text) in ("A", "B", "TIE", None)


def verdict(text: str) -> None:
    got = parse_verdict(text)
    assert got is None or isinstance(got, str)


def json_answer(text: str) -> None:
    for contains in (False, True):
        ok, _value = parse_json(text, contains=contains)
        assert isinstance(ok, bool)


def json_object(text: str) -> None:
    try:
        got = extract_json_object(text)
    except ValueError:
        return
    assert isinstance(got, dict), type(got)


def json_array(text: str) -> None:
    try:
        got = extract_json_array(text)
    except ValueError:
        return
    assert isinstance(got, list), type(got)


def frontmatter(text: str) -> None:
    # Nesting past the interpreter's limit is invalid frontmatter: no metadata, or yaml.YAMLError.
    meta, body = parse_frontmatter(text)
    assert isinstance(meta, dict) and isinstance(body, str)
    try:
        strict = parse_frontmatter_strict(text)
    except yaml.YAMLError:
        return
    try:
        assert strict == (meta, body)
    except RecursionError:
        # A YAML alias can make both metadata dicts cyclic; comparing two cyclic dicts recurses.
        return


TARGETS: dict[str, Callable[[str], None]] = {
    "yes_no": yes_no,
    "choice": choice,
    "verdict": verdict,
    "json_answer": json_answer,
    "json_object": json_object,
    "json_array": json_array,
    "frontmatter": frontmatter,
}

# Append-only: byte 0 of a seed indexes this tuple, so a new target goes at the end.
NAMES = ("choice", "frontmatter", "json_answer", "json_array", "json_object", "verdict", "yes_no")


def decode(data: bytes) -> tuple[str, str]:
    """``(target name, text)`` of a fuzz input: byte 0 picks the target (modulo the
    number of targets, in ``NAMES`` order), the rest is UTF-8 with bad bytes replaced."""
    if not data:
        return NAMES[0], ""
    return NAMES[data[0] % len(NAMES)], data[1:].decode("utf-8", errors="replace")


def run(data: bytes) -> None:
    name, text = decode(data)
    TARGETS[name](text)
