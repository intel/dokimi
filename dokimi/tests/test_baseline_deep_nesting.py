# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``--baseline`` with a deeply nested unrelated key does not raise RecursionError."""

import argparse
from typing import Any

from dokimi.commands.repo_scan import baseline_from


def test_deeply_nested_baseline_extra_key_is_read_without_recursion_error(tmp_path):
    path = tmp_path / "b.json"
    path.write_text('{"claims":[],"extra":' + "[" * 2000 + "0" + "]" * 2000 + "}", encoding="utf-8")
    payload: dict[str, Any] = {"claims": []}
    baseline_from(argparse.Namespace(baseline=str(path)), payload)
    assert payload["claims"] == []
