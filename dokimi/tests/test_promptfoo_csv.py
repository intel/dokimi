# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""promptfoo CSV ``__expected`` cells (:mod:`dokimi.task_formats.imports.promptfoo_csv`).

The expected asserts are promptfoo's (commit b4c2404a): the cases ``test/csv.test.ts`` pins,
and promptfoo's ``assertionFromString`` run on the other cells. A cell's checks are then the
checks of the same assert written in YAML.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest

from dokimi.eval.reference_checks import grade
from dokimi.task_formats.imports import import_path
from dokimi.task_formats.imports.promptfoo_csv import (
    assertion_from_cell, assertion_from_string, parse_comma_separated_values,
)

_EXPECTED = "Expected output"
_IS_JSON = "is-json:\n      required: ['color']\n      type:object\n      properties:\n        color:\n          type:string\n"

#: ``describe('assertionFromString')`` in promptfoo test/csv.test.ts.
_PROMPTFOO_PINNED = [
    (_EXPECTED, {"type": "equals", "value": _EXPECTED}),
    ("is-json", {"type": "is-json"}),
    (_IS_JSON, {"type": "is-json", "value": _IS_JSON[len("is-json:"):]}),
    ("contains-json", {"type": "contains-json"}),
    ('fn:output === "Expected output"', {"type": "javascript", "value": 'output === "Expected output"'}),
    ("similar(0.9):Expected output", {"type": "similar", "value": _EXPECTED, "threshold": 0.9}),
    ("contains:substring", {"type": "contains", "value": "substring"}),
    ("not-contains:substring", {"type": "not-contains", "value": "substring"}),
    ("contains-any:substring1,substring2", {"type": "contains-any", "value": ["substring1", "substring2"]}),
    ('contains-all:"say \\"hello, world\\"","a ""quoted"" value", plain',
     {"type": "contains-all", "value": ['say "hello, world"', 'a "quoted" value', "plain"]}),
    ("icontains-any: alpha, , beta,, ", {"type": "icontains-any", "value": ["alpha", "beta"]}),
    ("contains-all:substring1,substring2", {"type": "contains-all", "value": ["substring1", "substring2"]}),
    ("regex:\\d+", {"type": "regex", "value": "\\d+"}),
    ("not-regex:\\d+", {"type": "not-regex", "value": "\\d+"}),
    ("icontains:substring", {"type": "icontains", "value": "substring"}),
    ("not-icontains:substring", {"type": "not-icontains", "value": "substring"}),
    ("webhook:https://example.com", {"type": "webhook", "value": "https://example.com"}),
    ("not-webhook:https://example.com", {"type": "not-webhook", "value": "https://example.com"}),
    ("rouge-n(0.225):foo", {"type": "rouge-n", "value": "foo", "threshold": 0.225}),
    ("not-rouge-n(0.225):foo", {"type": "not-rouge-n", "value": "foo", "threshold": 0.225}),
    ("starts-with:Expected", {"type": "starts-with", "value": "Expected", "threshold": 0.75}),
    ("levenshtein(5):Expected output", {"type": "levenshtein", "value": _EXPECTED, "threshold": 5}),
    ("classifier(0.5):classA", {"type": "classifier", "value": "classA", "threshold": 0.5}),
    ("latency(1000)", {"type": "latency", "threshold": 1000}),
    ("perplexity(1.5)", {"type": "perplexity", "threshold": 1.5}),
    ("perplexity-score(0.5)", {"type": "perplexity-score", "threshold": 0.5}),
    ("cost(0.001)", {"type": "cost", "threshold": 0.001}),
    ("is-valid-function-call", {"type": "is-valid-function-call"}),
    ("is-valid-openai-function-call", {"type": "is-valid-openai-function-call"}),
    ("python: file://file.py ", {"type": "python", "value": "file://file.py"}),
    ("python:some_python_code", {"type": "python", "value": "some_python_code"}),
    ("file://script.py", {"type": "python", "value": "script.py"}),
    ("file://script.py:function_name", {"type": "python", "value": "script.py:function_name"}),
    ("javascript: x > 10", {"type": "javascript", "value": "x > 10"}),
    ("grade:Evaluate the response", {"type": "llm-rubric", "value": "Evaluate the response"}),
    ("llm-rubric:Rate the answer", {"type": "llm-rubric", "value": "Rate the answer"}),
    ('eval:output === "Expected output"', {"type": "javascript", "value": 'output === "Expected output"'}),
    ("similar:Expected output", {"type": "similar", "value": _EXPECTED, "threshold": 0.8}),
    *[(f"{t}:Expected output", {"type": t, "value": _EXPECTED, "threshold": 0.75}) for t in (
        "answer-relevance", "classifier", "context-faithfulness", "context-recall", "context-relevance",
        "cost", "latency", "levenshtein", "perplexity-score", "perplexity", "rouge-l", "rouge-n",
        "rouge-s", "starts-with")],
    *[(f"{t}(0.3):Expected output", {"type": t, "value": _EXPECTED, "threshold": 0.3}) for t in (
        "bleu", "gleu", "meteor", "tool-call-f1", "g-eval", "llm-rubric", "factuality", "javascript")],
    ("not-bleu(0.9):Expected output", {"type": "not-bleu", "value": _EXPECTED, "threshold": 0.9}),
    ("bleu:Expected output", {"type": "bleu", "value": _EXPECTED}),
    ("bleu(0):Expected output", {"type": "bleu", "value": _EXPECTED, "threshold": 0}),
    ("levenshtein(0):Expected output", {"type": "levenshtein", "value": _EXPECTED, "threshold": 0}),
    ("similar:cosine:The expected output", {"type": "similar:cosine", "value": "The expected output"}),
    ("similar:dot(0.8):The expected output",
     {"type": "similar:dot", "value": "The expected output", "threshold": 0.8}),
    ("similar:euclidean(0.5):The expected output",
     {"type": "similar:euclidean", "value": "The expected output", "threshold": 0.5}),
    ("not-similar:euclidean:The expected output",
     {"type": "not-similar:euclidean", "value": "The expected output"}),
    ("trajectory:tool-used:search", {"type": "trajectory:tool-used", "value": "search"}),
    ("similar:cosine similarity text", {"type": "similar", "value": "cosine similarity text", "threshold": 0.8}),
]


@pytest.mark.parametrize("expected, assertion", _PROMPTFOO_PINNED)
def test_assertion_from_string_matches_promptfoo_tests(expected, assertion):
    assert assertion_from_string(expected) == assertion


#: The inputs promptfoo's test/csv.test.ts runs through parseCommaSeparatedValues, with its output.
@pytest.mark.parametrize("value, fields", [
    ('"hello, world",foo', ["hello, world", "foo"]),
    ('"say \\"hi\\"",b', ['say "hi"', "b"]),
    ('a ""quoted"" value, plain', ['a ""quoted"" value', "plain"]),
    ("alpha, , beta,, ", ["alpha", "beta"]),
    ("  spaced  ,  next  ", ["spaced", "next"]),
    ('"only one"', ["only one"]),
    ("no-quotes-at-all", ["no-quotes-at-all"]),
    ('"trailing"   ', ["trailing"]),
    ("a,b,c", ["a", "b", "c"]),
    ('""', [""]),
    ('" ",x', [" ", "x"]),
    ('x,"y,z"', ["x", "y,z"]),
    ('"a","b"', ["a", "b"]),
    ('"a" , "b"', ["a", "b"]),
    ('"\\n",x', ["\\n", "x"]),
    ('"\\\\",x', ["\\", "x"]),
    ("a,", ["a"]),
    (",a", ["a"]),
    (",,,", []),
    ('"comma,inside","another, one"', ["comma,inside", "another, one"]),
    ('mix,"quoted, field",bare', ["mix", "quoted, field", "bare"]),
    ('"a""b""c"', ['a"b"c']),
])
def test_comma_separated_values_split_as_promptfoo_does(value, fields):
    assert parse_comma_separated_values(value) == fields
    assert assertion_from_string(f"contains-any:{value}")["value"] == fields


@pytest.mark.parametrize("value, message", [
    ('"a"b,c', "Expected comma after quoted field"),
    ('"unterminated', "Unterminated quoted field"),
])
def test_a_malformed_quoted_field_is_an_import_error(tmp_path, value, message):
    with pytest.raises(ValueError, match=message):
        parse_comma_separated_values(value)
    with pytest.raises(ValueError, match=message):
        _csv_checks(tmp_path, [f"contains-all:{value}"])


# --- a cell, the assert promptfoo builds from it, and the dokimi check --------------------------


def _match(values, location, *, ignore_case=False, mode="any", negate=False):
    check = {"type": "match", "values": values, "location": location, "ignore_case": ignore_case,
             "ignore_punctuation": False, "strip": False}
    if mode != "any":
        check["mode"] = mode
    if negate:
        check["negate"] = True
    return check


def _unsupported(native):
    return {"type": "unsupported", "native": native}


_CELLS = [
    ("contains-all: apple, banana", {"type": "contains-all", "value": ["apple", "banana"]},
     _match(["apple", "banana"], "any", mode="all")),
    ("contains-any: apple,,banana", {"type": "contains-any", "value": ["apple", "banana"]},
     _match(["apple", "banana"], "any")),
    ("contains-any: apple,banana,", {"type": "contains-any", "value": ["apple", "banana"]},
     _match(["apple", "banana"], "any")),
    ('contains-any: "a, b", c', {"type": "contains-any", "value": ["a, b", "c"]}, _match(["a, b", "c"], "any")),
    ("contains-any: a\\,b", {"type": "contains-any", "value": ["a\\", "b"]}, _match(["a\\", "b"], "any")),
    ("is-json", {"type": "is-json"}, {"type": "json", "values": []}),
    ("contains-json", {"type": "contains-json"}, {"type": "json", "values": [], "contains": True}),
    ("factuality: Paris is the capital of France",
     {"type": "factuality", "value": "Paris is the capital of France"},
     {"type": "judge", "reference": "Paris is the capital of France", "native": "factuality"}),
    ("model-graded-closedqa: mentions Paris", {"type": "model-graded-closedqa", "value": "mentions Paris"},
     {"type": "judge", "criterion": "mentions Paris", "native": "model-graded-closedqa"}),
    ("llm-rubric(0.5): is polite", {"type": "llm-rubric", "value": "is polite", "threshold": 0.5},
     {"type": "judge", "criterion": "is polite", "native": "llm-rubric"}),
    ("similar: x", {"type": "similar", "value": "x", "threshold": 0.8}, _unsupported("similar")),
    ("similar(0.9): x", {"type": "similar", "value": "x", "threshold": 0.9}, _unsupported("similar")),
    ("levenshtein(5): x", {"type": "levenshtein", "value": "x", "threshold": 5}, _unsupported("levenshtein")),
    ("cost: 0.01", {"type": "cost", "value": "0.01", "threshold": 0.75}, _unsupported("cost")),
    ("latency: 500", {"type": "latency", "value": "500", "threshold": 0.75}, _unsupported("latency")),
    ("fn: output.length > 3", {"type": "javascript", "value": "output.length > 3"}, _unsupported("javascript")),
    ("file://check.js", {"type": "javascript", "value": "file://check.js"}, _unsupported("javascript")),
    ("file://check.py", {"type": "python", "value": "check.py"}, _unsupported("python")),
    ("  foo  ", {"type": "equals", "value": "foo"}, _match(["foo"], "exact")),
    ("contains : foo", {"type": "equals", "value": "contains : foo"}, _match(["contains : foo"], "exact")),
    # The cases the first CSV port pinned.
    ("javascript:output.length > 3", {"type": "javascript", "value": "output.length > 3"},
     _unsupported("javascript")),
    ("python: len(output) > 3", {"type": "python", "value": "len(output) > 3"}, _unsupported("python")),
    # promptfoo keeps the text after `grade:` / `llm-rubric:` as it is, leading space included.
    ("grade: is funny", {"type": "llm-rubric", "value": " is funny"},
     {"type": "judge", "criterion": " is funny", "native": "llm-rubric"}),
    ("llm-rubric: is funny", {"type": "llm-rubric", "value": " is funny"},
     {"type": "judge", "criterion": " is funny", "native": "llm-rubric"}),
    ("not-contains: bye", {"type": "not-contains", "value": "bye"}, _match(["bye"], "any", negate=True)),
    ("regex:\\d+", {"type": "regex", "value": "\\d+"}, {"type": "regex", "values": ["\\d+"]}),
    ("starts-with: Hello", {"type": "starts-with", "value": "Hello", "threshold": 0.75}, _match(["Hello"], "begin")),
    ("equals: Paris", {"type": "equals", "value": "Paris"}, _match(["Paris"], "exact")),
    ("icontains: PARIS", {"type": "icontains", "value": "PARIS"}, _match(["PARIS"], "any", ignore_case=True)),
    ("icontains-any: PARIS, rome", {"type": "icontains-any", "value": ["PARIS", "rome"]},
     _match(["PARIS", "rome"], "any", ignore_case=True)),
    ("icontains-all: PARIS, rome", {"type": "icontains-all", "value": ["PARIS", "rome"]},
     _match(["PARIS", "rome"], "any", ignore_case=True, mode="all")),
    ("not-grade: x", {"type": "equals", "value": "not-grade: x"}, _match(["not-grade: x"], "exact")),
]


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    return path


def _csv_checks(tmp_path, cells):
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["q", *(f"__expected{i + 1}" for i in range(len(cells)))])
    writer.writerow(["hi", *cells])
    _write(tmp_path / "tests.csv", buf.getvalue())
    cfg = _write(tmp_path / "promptfooconfig.yaml", "prompts: ['{{q}}']\ntests: file://tests.csv\n")
    return import_path(cfg, fmt="promptfoo").questions[0]["grading"]["checks"]


def _yaml_checks(tmp_path, asserts):
    cfg = {"prompts": ["{{q}}"], "tests": [{"vars": {"q": "hi"}, "assert": asserts}]}
    path = _write(tmp_path / "yaml" / "promptfooconfig.yaml", json.dumps(cfg))
    return import_path(path, fmt="promptfoo").questions[0]["grading"]["checks"]


@pytest.mark.parametrize("cell, assertion, check", _CELLS)
def test_a_csv_cell_is_checked_like_the_yaml_assert_promptfoo_builds_from_it(tmp_path, cell, assertion, check):
    assert assertion_from_cell(cell) == assertion
    assert _csv_checks(tmp_path, [cell]) == [check]
    assert _yaml_checks(tmp_path, [assertion]) == [check]


@pytest.mark.parametrize("cell", ["", "   ", " \t\n "])
def test_a_blank_cell_adds_no_check(tmp_path, cell):
    assert assertion_from_cell(cell) is None
    assert _csv_checks(tmp_path, [cell, "contains: x"]) == [_match(["x"], "any")]


@pytest.mark.parametrize("kind", ["regex", "equals", "starts-with", "contains", "contains-any", "not-regex"])
def test_a_text_assert_with_no_value_is_unsupported_not_graded(tmp_path, kind):
    # A bare `regex` used to search for "None" and pass any answer that contains it.
    assert _yaml_checks(tmp_path, [{"type": kind}]) == [_unsupported(kind)]
    assert _csv_checks(tmp_path, [kind]) == [_unsupported(kind)]


def test_a_contains_all_list_is_trimmed_so_the_words_are_found_in_any_order(tmp_path):
    grading = {"checks": _csv_checks(tmp_path, ["contains-all: apple, banana"])}
    assert grade(grading, "q", "banana and apple").passed is True
    assert grade(grading, "q", "banana only").passed is False
