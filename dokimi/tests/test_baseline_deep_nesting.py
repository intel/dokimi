# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``--baseline`` with a deeply nested unrelated key never leaks a RecursionError."""

import argparse
from typing import Any

import pytest

from dokimi.commands.repo_scan import baseline_from
from dokimi.utils import InputError


def _baseline_with_nesting(tmp_path, depth: int) -> argparse.Namespace:
    path = tmp_path / "b.json"
    path.write_text(
        '{"claims":[],"extra":' + "[" * depth + "0" + "]" * depth + "}", encoding="utf-8"
    )
    return argparse.Namespace(baseline=str(path))


def test_deeply_nested_baseline_extra_key_is_read_without_recursion_error(tmp_path):
    """400 levels parse on every supported Python: 3.11's decoder stops near 1000, 3.12+ much later."""
    payload: dict[str, Any] = {"claims": []}
    baseline_from(_baseline_with_nesting(tmp_path, 400), payload)
    assert payload["claims"] == []


def test_nested_baseline_past_every_decoder_limit_is_an_input_error_not_a_traceback(tmp_path):
    with pytest.raises(InputError, match=r"--baseline .*b\.json: maximum recursion depth exceeded"):
        baseline_from(_baseline_with_nesting(tmp_path, 200_000), {"claims": []})
