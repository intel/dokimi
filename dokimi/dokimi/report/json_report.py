# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""JSON report writers."""

from __future__ import annotations

import json
from pathlib import Path

from dokimi.process_io import checked_json_text


def write_json(data: dict, path: Path) -> None:
    """Write JSON data to path, creating parent directories. Raises ValueError, writing nothing, for an object that
    would hold two equal keys once read back (:func:`dokimi.process_io.checked_json_text`)."""
    text = checked_json_text(json.dumps(data, indent=2))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
