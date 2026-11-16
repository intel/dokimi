# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Operation telemetry extraction for external coding harnesses."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .models import OperationRecord

_MARKER = "DOKIMI_OP"
_KEYWORD_TYPES = ("tool", "subagent", "loop")


def load_operations(output_dir: Path | None, stdout: str, stderr: str) -> list[OperationRecord]:
    """Collect operation telemetry from structured files and output markers.

    Harness wrappers can emit JSONL records into ``operations.jsonl`` or print
    lines prefixed with ``DOKIMI_OP`` followed by a JSON object.
    """

    operations: list[OperationRecord] = []
    if output_dir:
        for name in ("operations.jsonl", "operation-log.jsonl"):
            operations.extend(_read_jsonl(output_dir / name))
    operations.extend(_parse_markers(stdout))
    operations.extend(_parse_markers(stderr))
    operations.extend(_parse_keyword_lines(stdout, "stdout"))
    operations.extend(_parse_keyword_lines(stderr, "stderr"))
    return operations


def _read_jsonl(path: Path) -> list[OperationRecord]:
    if not path.is_file():
        return []
    out: list[OperationRecord] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        return [
            OperationRecord(
                type="log_parse_error",
                name=path.name,
                status="error",
                metadata={"error": str(exc)},
            )
        ]
    for line in lines:
        line = line.strip()
        if not line:
            continue
        out.append(_parse_record(line, path.name))
    return out


def _parse_markers(text: str) -> list[OperationRecord]:
    out: list[OperationRecord] = []
    for line in text.splitlines():
        if _MARKER not in line:
            continue
        out.append(_parse_record(line.split(_MARKER, 1)[1].strip(), "marker"))
    return out


def _parse_record(text: str, source: str) -> OperationRecord:
    """The operation in ``text``, a JSON object; anything else is a ``log_parse_error`` named ``source``."""
    try:
        data = json.loads(text)
    except (ValueError, RecursionError) as exc:  # not JSON, an int over 4300 digits, nesting too deep
        return OperationRecord(type="log_parse_error", name=source, status="error",
                               metadata={"error": f"not JSON: {exc}"})
    if not isinstance(data, dict):  # the documented shape is an object (#687)
        return OperationRecord(type="log_parse_error", name=source, status="error",
                               metadata={"error": f"expected a JSON object, got {type(data).__name__}"})
    return _op_from_dict(data)


def _parse_keyword_lines(text: str, stream: str) -> list[OperationRecord]:
    out: list[OperationRecord] = []
    for line in text.splitlines():
        if _MARKER in line:
            continue
        lowered = line.lower()
        for op_type in _KEYWORD_TYPES:
            if re.search(rf"\b{op_type}s?\b", lowered):
                out.append(
                    OperationRecord(
                        type=op_type,
                        name="unstructured-log",
                        metadata={"stream": stream, "line": line[:300]},
                    )
                )
                break
    return out


def _op_from_dict(data: dict[str, Any]) -> OperationRecord:
    op_type = str(data.get("type") or data.get("kind") or "operation")
    name = str(data.get("name") or data.get("tool") or data.get("subagent") or op_type)
    status = str(data.get("status") or "ok")
    try:
        elapsed = float(data.get("elapsed_sec") or data.get("duration_sec") or 0.0)
    except (TypeError, ValueError, OverflowError):  # OverflowError: an int too large for a float
        elapsed = 0.0
    metadata = {
        k: v
        for k, v in data.items()
        if k
        not in {"type", "kind", "name", "tool", "subagent", "status", "elapsed_sec", "duration_sec"}
    }
    return OperationRecord(
        type=op_type, name=name, status=status, elapsed_sec=elapsed, metadata=metadata
    )
