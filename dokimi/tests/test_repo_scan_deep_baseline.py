# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A deeply nested ``--baseline`` file is an input error (exit 2), not a RecursionError."""

from __future__ import annotations

import argparse

import pytest

from dokimi.commands.repo_scan import baseline_from
from dokimi.utils import InputError


def test_a_deeply_nested_baseline_is_an_input_error(tmp_path):
    path = tmp_path / "deep.json"
    path.write_text("[" * 200000 + "0" + "]" * 200000, encoding="utf-8")
    with pytest.raises(InputError):
        baseline_from(argparse.Namespace(baseline=str(path)), {})
