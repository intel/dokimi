# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Robust JSON extraction from LLM output; re-exported by :mod:`dokimi.llm`."""

from __future__ import annotations

import json
import re


def extract_json_object(text: str) -> dict:
    """Extract a JSON object from LLM output, handling markdown fences and leading text."""
    text = (text or "").strip()
    if not text:
        raise ValueError("LLM returned empty response")
    # Direct parse
    try:
        return json.loads(text)
    except Exception:
        pass
    # Markdown fence
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        try:
            return json.loads(fence.group(1).strip())
        except Exception:
            pass
    # First { ... } block
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        return json.loads(m.group(0))
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
    except Exception:
        pass
    # Markdown fence
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        try:
            result = json.loads(fence.group(1).strip())
            if isinstance(result, list):
                return result
        except Exception:
            pass
    # First [ ... ] block
    m = re.search(r"\[[\s\S]*\]", text)
    if m:
        result = json.loads(m.group(0))
        if isinstance(result, list):
            return result
    raise ValueError(f"No JSON array found in LLM response: {text[:200]!r}")
