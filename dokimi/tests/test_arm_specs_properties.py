# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Property-based tests for the arm-spec grammar: any text in, a parse or ArmSpecError out."""

from __future__ import annotations

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from dokimi.treatments.specs import (
    COMBINE,
    KINDS,
    ArmSpecError,
    combine_specs,
    content_refs,
    parse_arm_spec,
    split_combined,
)

# Text built from the grammar's own punctuation, so separators, prefixes and
# empty parts come up far more often than in arbitrary unicode.
_TOKENS = st.sampled_from([*"+:, /.-_ab", *(k.prefix for k in KINDS)])
_GRAMMAR_TEXT = st.lists(_TOKENS, max_size=20).map("".join)
_ANY_TEXT = st.one_of(st.text(max_size=60), _GRAMMAR_TEXT)

# No deadline: CI runs the suite under xdist on shared runners.
_SETTINGS = settings(deadline=None, max_examples=300)


@_SETTINGS
@given(_ANY_TEXT)
def test_parse_raises_only_arm_spec_error(spec: str) -> None:
    try:
        parts = parse_arm_spec(spec)
    except ArmSpecError:
        return
    assert parts and all(p.raw == p.raw.strip() for p in parts)


@_SETTINGS
@given(_ANY_TEXT)
def test_content_refs_never_raises(spec: str) -> None:
    for raw, ref in content_refs(spec):
        assert raw in split_combined(spec)
        assert ref


@_SETTINGS
@given(_ANY_TEXT)
def test_split_combined_is_idempotent(spec: str) -> None:
    for part in split_combined(spec):
        assert split_combined(part) == [part]


_ARG = st.text(alphabet=st.sampled_from([*"abc019/._-"]), min_size=1, max_size=12)


@st.composite
def _part(draw: st.DrawFn) -> str:
    kind = draw(st.sampled_from(KINDS))
    if kind.arg == "none":
        return kind.prefix
    if kind.arg == "optional" and draw(st.booleans()):
        return kind.prefix
    return f"{kind.prefix}:{draw(_ARG)}"


@_SETTINGS
@given(st.lists(_part(), min_size=1, max_size=5))
def test_combine_then_split_round_trips(parts: list[str]) -> None:
    # All-mcp combinations cannot be expressed (combine_specs docstring).
    assume(len(parts) == 1 or not all(p.startswith("mcp:") for p in parts))
    combined = combine_specs(parts)
    split = split_combined(combined)
    assert sorted(split) == sorted(parts)
    assert COMBINE.join(split) == combined
    assert [p.raw for p in parse_arm_spec(combined)] == split
