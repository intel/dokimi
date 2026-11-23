# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`repo scan --baseline FILE` with a deeply nested file is an input error, not a traceback."""
from __future__ import annotations

import argparse

import pytest

from dokimi.commands.repo_scan import baseline_from
from dokimi.utils import InputError


def test_a_deeply_nested_baseline_is_an_input_error(tmp_path):
    deep = tmp_path / "deep.json"
    deep.write_text("[" * 2000 + "0" + "]" * 2000, encoding="utf-8")
    with pytest.raises(InputError, match="--baseline"):
        baseline_from(argparse.Namespace(baseline=str(deep)), {})


def test_a_baseline_past_the_decoder_nesting_limit_is_an_input_error(tmp_path, monkeypatch):
    """Whatever the interpreter's limit, 2,000 nested brackets that the decoder cannot take are exit 2, not exit 5."""
    deep = tmp_path / "deep.json"
    deep.write_text("[" * 2000 + "0" + "]" * 2000, encoding="utf-8")
    monkeypatch.setattr("json.loads", _too_deep)
    with pytest.raises(InputError, match="--baseline"):
        baseline_from(argparse.Namespace(baseline=str(deep)), {})


def _too_deep(text, *args, **kwargs):
    raise RecursionError("maximum recursion depth exceeded while decoding a JSON array")
