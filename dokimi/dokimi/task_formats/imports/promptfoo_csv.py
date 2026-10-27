# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A promptfoo CSV ``__expected`` cell as the assert promptfoo builds from it.

A port of ``assertionFromString`` (``src/csv.ts``) and ``parseCommaSeparatedValues``
(``src/validation/parseCommaSeparatedValues.ts``), promptfoo commit b4c2404a. The result is
a ``{type, value, threshold}`` assert, graded like the same assert written in YAML.
"""

from __future__ import annotations

import math
import re
from typing import Any, Optional

#: ``BaseAssertionTypesSchema`` in promptfoo ``src/types/index.ts`` at b4c2404a, in its order.
ASSERTION_TYPES = (
    "agent-rubric", "answer-relevance", "bleu", "classifier", "contains", "contains-all",
    "contains-any", "contains-html", "contains-json", "contains-sql", "contains-xml",
    "context-faithfulness", "context-recall", "context-relevance", "conversation-relevance",
    "cost", "equals", "factuality", "finish-reason", "g-eval", "gleu", "guardrails", "icontains",
    "icontains-all", "icontains-any", "is-html", "is-json", "is-refusal", "is-sql",
    "is-valid-function-call", "is-valid-openai-function-call", "is-valid-openai-tools-call",
    "is-xml", "javascript", "latency", "levenshtein", "llm-rubric", "pi", "meteor",
    "model-graded-closedqa", "model-graded-factuality", "moderation", "perplexity",
    "perplexity-score", "python", "regex", "rouge-l", "rouge-n", "rouge-s", "ruby", "similar",
    "similar:cosine", "similar:dot", "similar:euclidean", "starts-with", "tool-call-f1",
    "skill-used", "trajectory:goal-success", "trajectory:tool-args-match",
    "trajectory:step-count", "trajectory:tool-sequence", "trajectory:tool-used",
    "trace-error-spans", "trace-span-count", "trace-span-duration", "search-rubric", "webhook",
    "word-count",
)

# JavaScript's \s, which is also the set String.prototype.trim removes.
_JS_SPACE = "\t\n\v\f\r \u00a0\u1680" + "".join(map(chr, range(0x2000, 0x200B))) + "\u2028\u2029\u202f\u205f\u3000\ufeff"

# Longest names first, so `similar:cosine` is not read as `similar` with the value `cosine...`.
_ASSERTION = re.compile(
    r"(not-)?(" + "|".join(re.escape(t) for t in sorted(ASSERTION_TYPES, key=len, reverse=True))
    + r")(?:\(([0-9]+(?:\.[0-9]+)?)\))?(?::(.*))?",
    re.DOTALL,
)
_JS_FILE = re.compile(r"\.(js|cjs|mjs|ts|cts|mts)\Z", re.IGNORECASE)
_LIST_TYPES = ("contains-all", "contains-any", "icontains-all", "icontains-any")
_DEFAULT_THRESHOLD_TYPES = (
    "answer-relevance", "classifier", "context-faithfulness", "context-recall",
    "context-relevance", "cost", "latency", "levenshtein", "perplexity-score", "perplexity",
    "rouge-l", "rouge-n", "rouge-s", "similar", "starts-with",
)


def js_trim(text: str) -> str:
    return text.strip(_JS_SPACE)


def _finite(text: Optional[str]) -> Optional[float]:
    if text is None:
        return None
    number = float(text)
    return number if math.isfinite(number) else None


def _skip_space(value: str, i: int) -> int:
    while i < len(value) and value[i] in _JS_SPACE:
        i += 1
    return i


def _quoted(value: str, i: int) -> tuple[str, int]:
    """The quoted field opening at ``value[i]``, and the index after its closing quote."""
    field, i = "", i + 1
    while i < len(value):
        if value[i] == "\\" and value[i + 1:i + 2] in ('"', "\\"):
            field, i = field + value[i + 1], i + 2
        elif value[i:i + 2] == '""':
            field, i = field + '"', i + 2
        elif value[i] == '"':
            return field, i + 1
        else:
            field, i = field + value[i], i + 1
    raise ValueError("Invariant failed: Unterminated quoted field in contains assertion value")


def parse_comma_separated_values(value: str) -> list[str]:
    """promptfoo's ``parseCommaSeparatedValues``: quoted fields, trimmed, empty fields skipped."""
    out: list[str] = []
    i = 0
    while i < len(value):
        i = _skip_space(value, i)
        while i < len(value) and value[i] == ",":
            i = _skip_space(value, i + 1)
        if i >= len(value):
            break
        quoted = value[i] == '"'
        if quoted:
            field, i = _quoted(value, i)
        else:
            end = value.find(",", i)
            end = len(value) if end == -1 else end
            field, i = js_trim(value[i:end]), end
        out.append(field)
        i = _skip_space(value, i)
        if quoted and i < len(value) and value[i] != ",":
            raise ValueError("Invariant failed: Expected comma after quoted field in contains assertion value")
    return out


def _legacy(expected: str) -> Optional[dict[str, Any]]:
    """The ``javascript:``/``fn:``/``eval:``/``file://``, ``grade:`` and ``python:`` forms."""
    if (expected.startswith(("javascript:", "fn:", "eval:"))
            or (expected.startswith("file://") and _JS_FILE.search(expected[len("file://"):]))):
        prefix = next((p for p in ("javascript:", "fn:", "eval:") if expected.startswith(p)), "")
        return {"type": "javascript", "value": js_trim(expected[len(prefix):])}
    if expected.startswith(("grade:", "llm-rubric:")):
        return {"type": "llm-rubric", "value": expected[6 if expected.startswith("grade:") else 11:]}
    if expected.startswith("python:") or (
            expected.startswith("file://") and (expected.endswith(".py") or ".py:" in expected)):
        prefix = "python:" if expected.startswith("python:") else "file://"
        return {"type": "python", "value": js_trim(expected[len(prefix):])}
    return None


def assertion_from_string(expected: str) -> dict[str, Any]:
    """promptfoo's ``assertionFromString``; a ``value`` promptfoo leaves undefined is absent."""
    legacy = _legacy(expected)
    if legacy is not None:
        return legacy
    m = _ASSERTION.fullmatch(expected)
    if m is None:
        return {"type": "equals", "value": expected}
    negate, base, threshold_text, value = m.groups()
    out: dict[str, Any] = {"type": f"not-{base}" if negate else base}
    threshold = _finite(threshold_text)
    if base in _LIST_TYPES:
        out["value"] = parse_comma_separated_values(value) if value else value
    elif base in ("contains-json", "is-json"):
        out["value"] = value
    else:
        out["value"] = None if value is None else js_trim(value)
        if base in _DEFAULT_THRESHOLD_TYPES and threshold is None:
            threshold = 0.8 if base == "similar" else 0.75
        if threshold is not None:
            out["threshold"] = threshold
    if out["value"] is None:
        del out["value"]
    return out


def assertion_from_cell(cell: Optional[str]) -> Optional[dict[str, Any]]:
    """What ``testCaseFromCsvRow`` adds for an ``__expected`` cell: none for a blank one."""
    text = js_trim(cell or "")
    return assertion_from_string(text) if text else None
