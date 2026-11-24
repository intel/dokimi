# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The licenses check requires the copyright and SPDX header on `tests/exists_mock.py` (#1086)."""

from pathlib import Path


def test_exists_mock_starts_with_the_license_header():
    lines = (Path(__file__).parent / "exists_mock.py").read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("# Copyright (C) 2026 Intel Corporation")
    assert lines[1] == "# SPDX-License-Identifier: Apache-2.0"
