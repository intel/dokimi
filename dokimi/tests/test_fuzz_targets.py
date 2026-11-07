# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""The fuzz targets (fuzz/targets.py) without atheris: the seed corpus and Hypothesis.

The fuzz workflow runs them under atheris; this keeps their contracts checked in
every CI run, and fails when a seed file names a target that no longer exists.
"""
import sys
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

FUZZ = Path(__file__).resolve().parents[1] / "fuzz"
sys.path.insert(0, str(FUZZ))

from targets import NAMES, TARGETS, decode, run  # noqa: E402

from dokimi.llm_json import extract_json_object  # noqa: E402

SEEDS = sorted((FUZZ / "corpus").iterdir())


def test_every_target_has_seeds() -> None:
    assert {p.name.split("-")[0] for p in SEEDS} == set(TARGETS)


@pytest.mark.parametrize("seed", SEEDS, ids=lambda p: p.name)
def test_seed(seed: Path) -> None:
    # The same decoding the atheris harness uses: the seed reaches the target it is named for.
    data = seed.read_bytes()
    assert decode(data)[0] == seed.name.split("-")[0]
    run(data)


@pytest.mark.parametrize("name", NAMES)
@settings(max_examples=200, deadline=None)
@given(text=st.text())
def test_any_text(name: str, text: str) -> None:  # test-quality: ok the target asserts its contract
    TARGETS[name](text)


@pytest.mark.parametrize("reply", ["7", "[1, 2]", '"x"', "null", "```json\n[1]\n```"])
def test_extract_json_object_refuses_a_non_object(reply: str) -> None:
    # Found by the fuzzer: these came back as int/list/str, and the judge's
    # retry-on-ValueError never saw them (AttributeError at .keys()).
    with pytest.raises(ValueError):
        extract_json_object(reply)


def test_extract_json_object_finds_the_object_after_a_non_object() -> None:
    assert extract_json_object('[1] then {"a": 1}') == {"a": 1}


def test_decode_wraps_the_target_byte_and_replaces_bad_utf8() -> None:
    assert decode(b"") == (NAMES[0], "")
    assert decode(bytes([len(NAMES) + 1]) + b"a\xffb") == (NAMES[1], "a\ufffdb")


def test_deep_nesting_is_a_parse_error_not_a_crash() -> None:
    # The JSON extractors read model replies; their callers retry on ValueError only.
    # Python 3.14's json parses this depth; older ones raise RecursionError, now a ValueError.
    from dokimi.llm_json import extract_json_array

    for extract, text, kind in (
        (extract_json_array, "x " + "[" * 100000 + "]" * 100000, list),
        (extract_json_object, "x " + "{\"a\":" * 100000 + "1" + "}" * 100000, dict),
    ):
        try:
            got = extract(text)
        except ValueError:
            continue
        assert isinstance(got, kind)


def test_a_date_that_is_not_real_is_invalid_frontmatter() -> None:
    # PyYAML raises a plain ValueError for 2026-02-30: the lenient reader reads no metadata,
    # and the strict one raises the YAML error its docstring names.
    import yaml

    from dokimi.utils import parse_frontmatter, parse_frontmatter_strict

    text = "---\ndate: 2026-02-30\n---\nbody"
    assert parse_frontmatter(text) == ({}, "body")
    with pytest.raises(yaml.YAMLError):
        parse_frontmatter_strict(text)


@pytest.mark.parametrize("contains", [False, True])
def test_parse_json_reads_deep_nesting_as_not_json(contains: bool) -> None:
    # A reply nested past the recursion limit is "not JSON", not a crash of the judge.
    from dokimi.eval.reference_checks import parse_json

    assert parse_json("[" * 20000, contains=contains) == (False, None)
