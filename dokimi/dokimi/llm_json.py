# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Robust JSON extraction from LLM output; re-exported by :mod:`dokimi.llm`."""

from __future__ import annotations

import json
import re

# No \s* before the body: callers strip it, and \s* retries the lazy body once per space (#883).
_FENCE = re.compile(r"```(?:json)?([\s\S]*?)```")


def _outer_block(text: str, opener: str, closer: str) -> str | None:
    """From the first ``opener`` to the last ``closer`` after it; a regex backtracks quadratically on a run of openers."""
    i, j = text.find(opener), text.rfind(closer)
    return text[i : j + 1] if i != -1 and j > i else None


def extract_json_object(text: str) -> dict:
    """Extract a JSON object from LLM output, handling markdown fences and leading text.

    Always a ``dict``: a reply that parses to another JSON value (``7``, ``[1, 2]``,
    ``"x"``) raises ``ValueError`` like any unusable reply, so callers that retry on
    ``ValueError`` retry it instead of failing at a ``.get``. The last fallback is
    still the outermost ``{...}`` in the text, so ``[{"a": 1}]`` gives ``{"a": 1}``.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("LLM returned empty response")
    # Direct parse
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except (ValueError, RecursionError):  # not JSON here: try the next form
        pass
    # Markdown fence
    fence = _FENCE.search(text)
    if fence:
        try:
            result = json.loads(fence.group(1).strip())
            if isinstance(result, dict):
                return result
        except (ValueError, RecursionError):
            pass
    # First { ... } block
    block = _outer_block(text, "{", "}")
    if block is not None:
        try:
            result = json.loads(block)
        except RecursionError as exc:
            raise ValueError(f"JSON in LLM response is nested too deeply: {text[:200]!r}") from exc
        if isinstance(result, dict):
            return result
    raise ValueError(f"No JSON object found in LLM response: {text[:200]!r}")


def extract_json_array(text: str) -> list:
    """Extract a JSON array from LLM output, handling markdown fences and leading text."""
    text = (text or "").strip()
    if not text:
        raise ValueError("LLM returned empty response")
    # Direct parse
    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
    except (ValueError, RecursionError):  # not JSON here: try the next form
        pass
    # Markdown fence
    fence = _FENCE.search(text)
    if fence:
        try:
            result = json.loads(fence.group(1).strip())
            if isinstance(result, list):
                return result
        except (ValueError, RecursionError):
            pass
    # First [ ... ] block
    block = _outer_block(text, "[", "]")
    if block is not None:
        try:
            result = json.loads(block)
        except RecursionError as exc:
            raise ValueError(f"JSON in LLM response is nested too deeply: {text[:200]!r}") from exc
        if isinstance(result, list):
            return result
    raise ValueError(f"No JSON array found in LLM response: {text[:200]!r}")
