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

from targets import TARGETS  # noqa: E402

from dokimi.llm_json import extract_json_object  # noqa: E402

NAMES = sorted(TARGETS)
SEEDS = sorted((FUZZ / "corpus").iterdir())


def test_every_target_has_seeds() -> None:
    assert {p.name.split("-")[0] for p in SEEDS} == set(TARGETS)


@pytest.mark.parametrize("seed", SEEDS, ids=lambda p: p.name)
def test_seed(seed: Path) -> None:
    # The harness layout: byte 0 picks the target in sorted order, byte 1 (even)
    # makes the rest ASCII text.
    data = seed.read_bytes()
    assert NAMES[data[0]] == seed.name.split("-")[0]
    TARGETS[NAMES[data[0]]](data[2:].decode("ascii"))


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
