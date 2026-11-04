# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Intel Python CI (ci/ and .github/workflows/intel-ci.yml) keeps what Jenkins relies on."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

_REPO = Path(__file__).resolve().parents[2]
_WORKFLOWS = _REPO / ".github" / "workflows"
pytestmark = pytest.mark.skipif(not (_REPO / "ci" / "build.py").is_file(),
                                reason="not a checkout: ci/ is next to the project")


def _workflow(name: str) -> dict:
    return yaml.safe_load((_WORKFLOWS / name).read_text(encoding="utf-8"))


def test_every_push_and_tag_run_keeps_its_own_concurrency_group() -> None:
    """Jenkins tests every main push: a shared group would drop a queued one, so group as ci.yml does."""
    assert _workflow("intel-ci.yml")["concurrency"] == _workflow("ci.yml")["concurrency"]
