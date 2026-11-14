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


def test_the_frontmatter_target_holds_deep_nesting_to_its_contract() -> None:  # test-quality: ok the target asserts its contract
    # Both parsers read nesting past the recursion limit as invalid frontmatter, so the target skips no RecursionError.
    TARGETS["frontmatter"]("---\nx: " + "[" * 100000 + "\n---\nbody")


@pytest.mark.parametrize(
    "text",
    [
        # Malformed explicit tags: PyYAML's constructors raise AttributeError, IndexError, KeyError.
        "---\n!!timestamp 2026-13-01{\n\n---\n",
        "---\n!!int \n---\n'9999'\n",
        "---\nx: !!bool x\n---\nbody",
    ],
)
def test_a_malformed_tagged_scalar_is_invalid_frontmatter(text: str) -> None:
    # Lenient readers return no metadata; strict ones raise the YAML error the docstring names.
    import yaml

    from dokimi.utils import parse_frontmatter, parse_frontmatter_strict

    meta, body = parse_frontmatter(text)
    assert meta == {}
    assert isinstance(body, str)
    with pytest.raises(yaml.YAMLError):
        parse_frontmatter_strict(text)


@pytest.mark.parametrize("contains", [False, True])
def test_parse_json_reads_deep_nesting_as_not_json(contains: bool) -> None:
    # A deeply nested reply is "not JSON", not a crash of the judge. The contains scan is bounded
    # (see test_parse_json_contains_tries_a_bounded_number_of_starts), so both modes take 20000.
    from dokimi.eval.reference_checks import parse_json

    assert parse_json("[" * 20000, contains=contains) == (False, None)


def test_parse_json_contains_tries_a_bounded_number_of_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    from dokimi.eval import reference_checks
    from dokimi.eval.reference_checks import CONTAINS_JSON_MAX_STARTS, parse_json

    calls: list[int] = []
    real = json.JSONDecoder.raw_decode

    def spy(self: json.JSONDecoder, s: str, idx: int = 0) -> tuple[object, int]:
        calls.append(idx)
        return real(self, s, idx)

    monkeypatch.setattr(reference_checks.json.JSONDecoder, "raw_decode", spy)
    assert parse_json("[" * 20000, contains=True) == (False, None)
    assert 0 < len(calls) <= CONTAINS_JSON_MAX_STARTS + 1  # + the json.loads of the whole text
    calls.clear()
    # A decoder that reports a JSONDecodeError at the depth limit (Python 3.14) never raises
    # RecursionError: only the cap bounds the scan there, whatever this Python does.
    def reject(self: json.JSONDecoder, s: str, idx: int = 0) -> tuple[object, int]:
        calls.append(idx)
        raise json.JSONDecodeError("too deep", s, idx)

    monkeypatch.setattr(reference_checks.json.JSONDecoder, "raw_decode", reject)
    assert parse_json("[" * 20000, contains=True) == (False, None)
    assert len(calls) == 1 + CONTAINS_JSON_MAX_STARTS
    monkeypatch.setattr(reference_checks.json.JSONDecoder, "raw_decode", spy)
    calls.clear()
    # A value after fewer stray brackets than the cap, in prose, is still found.
    assert parse_json("{ [ " * 10 + 'then {"a": [1]} done', contains=True) == (True, {"a": [1]})
    assert len(calls) == 1 + 21  # json.loads of the whole text, then one try per bracket up to the value
    # Past the cap the scan gives up.
    assert parse_json("[ " * (CONTAINS_JSON_MAX_STARTS + 5) + '{"a": 1}', contains=True) == (False, None)


def test_parse_json_contains_finds_a_value_after_a_start_too_deep_to_decode() -> None:
    from dokimi.eval.reference_checks import parse_json

    # The first "[" runs into the decoder's depth limit; the object after it is still the first JSON found.
    assert parse_json('[ {"a": 1}, ' + "[1," * 10000, contains=True) == (True, {"a": 1})


def test_parse_json_contains_bounds_the_characters_failed_tries_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    from dokimi.eval import reference_checks
    from dokimi.eval.reference_checks import CONTAINS_JSON_MAX_SCAN, parse_json

    calls: list[int] = []
    real = json.JSONDecoder.raw_decode

    def spy(self: json.JSONDecoder, s: str, idx: int = 0) -> tuple[object, int]:
        calls.append(idx)
        return real(self, s, idx)

    monkeypatch.setattr(reference_checks.json.JSONDecoder, "raw_decode", spy)
    # 900 brackets, then a tail every try parses to its end before it fails.
    assert parse_json("[" * 900 + "1," * 100000, contains=True) == (False, None)
    # Each failed try scans the 200000-character tail: the budget allows 1 + CONTAINS_JSON_MAX_SCAN // 200000 + 1 of them.
    assert len(calls) <= 1 + CONTAINS_JSON_MAX_SCAN // 200000 + 1
    calls.clear()
    # A value after a start that failed late, within the budget, is still found.
    text = "[ " + "1," * 1000 + ' {"a": 1} ' + "[1,"
    assert parse_json("[ " * 3 + '{"a": [1, 2]}' + " [1," * 100, contains=True) == (True, {"a": [1, 2]})
    assert parse_json(text, contains=True) == (True, {"a": 1})


@pytest.mark.parametrize(
    "text",
    [
        "[" * 900 + "1" * 200000,  # int() refuses the digits: a ValueError without a position
        "[" * 900 + '"' + "a" * 200000,  # an unterminated string reports its opening quote
        "[" * 100 + '"' + "a" * 200000 + '",' + "[" * 10000,  # a long read, then the depth limit
    ],
    ids=["overlong-int", "unterminated-string", "depth-limit"],
)
def test_parse_json_contains_charges_failures_without_a_position(text: str, monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    from dokimi.eval import reference_checks
    from dokimi.eval.reference_checks import CONTAINS_JSON_MAX_SCAN, parse_json

    calls: list[int] = []
    real = json.JSONDecoder.raw_decode

    def spy(self: json.JSONDecoder, s: str, idx: int = 0) -> tuple[object, int]:
        calls.append(idx)
        return real(self, s, idx)

    monkeypatch.setattr(reference_checks.json.JSONDecoder, "raw_decode", spy)
    assert parse_json(text, contains=True) == (False, None)
    assert len(calls) <= 1 + CONTAINS_JSON_MAX_SCAN // 200000 + 1


def test_parse_json_contains_charges_a_failed_try_from_its_start() -> None:
    from dokimi.eval.reference_checks import CONTAINS_JSON_MAX_SCAN, parse_json

    # The failed "[1,]" sits past the budget's length but scans 4 characters.
    text = "x" * (CONTAINS_JSON_MAX_SCAN + 500000) + '[1,] {"a": 1}'
    assert parse_json(text, contains=True) == (True, {"a": 1})


def test_parse_json_contains_stops_when_the_failed_tries_reach_the_budget_exactly() -> None:
    from dokimi.eval.reference_checks import CONTAINS_JSON_MAX_SCAN, parse_json

    # The first try fails at the "2", position CONTAINS_JSON_MAX_SCAN (a missing comma, reported at
    # the same place on every Python), charging exactly the budget.
    at_budget = "[1" + " " * (CONTAINS_JSON_MAX_SCAN - 2) + '2] {"a":1}'
    assert parse_json(at_budget, contains=True) == (False, None)
    # One character less leaves a character of budget: the object is found.
    under = "[1" + " " * (CONTAINS_JSON_MAX_SCAN - 3) + '2] {"a":1}'
    assert parse_json(under, contains=True) == (True, {"a": 1})


def test_parse_json_contains_does_not_charge_the_first_whole_text_parse() -> None:
    from dokimi.eval.reference_checks import CONTAINS_JSON_MAX_SCAN, parse_json

    # json.loads fails after reading past the budget; the first bracket still parses.
    text = '["' + "a" * (CONTAINS_JSON_MAX_SCAN + 1) + '"] trailing'
    assert parse_json(text, contains=True) == (True, ["a" * (CONTAINS_JSON_MAX_SCAN + 1)])


def test_names_pin_the_byte_to_target_order() -> None:
    # A seed stores its target's index in byte 0: this order is append-only, so a new
    # target goes at the end and the seeds already saved keep their target.
    assert NAMES == ("choice", "frontmatter", "json_answer", "json_array", "json_object", "verdict", "yes_no")
    assert set(NAMES) == set(TARGETS)
