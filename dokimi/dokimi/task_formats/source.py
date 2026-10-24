# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A file read once for every probe: parsed data, rows, and what failed to parse.

Files are data here, never code: JSON, JSONL, YAML and TOML are parsed with safe
loaders (YAML refuses aliases), and a ``.py`` file is only read as text. A parse
failure is a lint problem on the report, not an exception.
"""

from __future__ import annotations

import json
import tomllib
from functools import cached_property
from pathlib import Path
from typing import Any

import yaml

from dokimi.harnesses.tasks import _load_yaml

#: Files larger than this are not parsed (their structure is reported as unread).
MAX_BYTES = 64 * 1024 * 1024


class FileSource:
    """One candidate file. Parsing is lazy and happens at most once."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.problems: list[str] = []

    @property
    def suffix(self) -> str:
        return self.path.suffix.lower()

    @cached_property
    def text(self) -> str | None:
        try:
            if self.path.stat().st_size > MAX_BYTES:
                self.problems.append(f"larger than {MAX_BYTES // (1024 * 1024)} MiB: not read")
                return None
            return self.path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            self.problems.append(f"cannot read: {exc}")
            return None

    @cached_property
    def data(self) -> Any:
        """The parsed document (a JSONL file parses to its list of rows), or ``None``."""
        text = self.text
        if text is None or self.suffix == ".py":
            return None
        try:
            return _parse(self.suffix, text, self.problems)
        except (ValueError, yaml.YAMLError, RecursionError) as exc:
            self.problems.append(f"not valid {_LANG.get(self.suffix, 'data')}: {_one_line(exc)}")
            return None

    @cached_property
    def rows(self) -> list[dict[str, Any]] | None:
        """The records of a list-shaped document (JSONL, a JSON/YAML list), else ``None``."""
        data = self.data
        if not isinstance(data, list):
            return None
        return [r for r in data if isinstance(r, dict)]


_LANG = {".json": "JSON", ".jsonl": "JSONL", ".yaml": "YAML", ".yml": "YAML", ".toml": "TOML"}


def _one_line(exc: BaseException) -> str:
    return " ".join(str(exc).split())[:200]


def _parse(suffix: str, text: str, problems: list[str]) -> Any:
    if suffix == ".jsonl":
        return _parse_jsonl(text, problems)
    if suffix == ".json":
        return json.loads(text)
    if suffix == ".toml":
        return tomllib.loads(text)
    return _load_yaml(text)


def _parse_jsonl(text: str, problems: list[str]) -> list[Any]:
    rows: list[Any] = []
    bad = 0
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except ValueError as exc:
            bad += 1
            if bad <= 3:
                problems.append(f"line {number}: not valid JSON: {_one_line(exc)}")
    if bad > 3:
        problems.append(f"{bad - 3} more lines are not valid JSON")
    return rows


def first_rows(rows: list[dict[str, Any]] | None, n: int = 50) -> list[dict[str, Any]]:
    """The rows a fingerprint looks at: the first *n*, so a large file costs no more."""
    return (rows or [])[:n]


def key_share(rows: list[dict[str, Any]], *keys: str) -> float:
    """Share of *rows* that hold every one of *keys*."""
    if not rows:
        return 0.0
    return sum(all(k in r for k in keys) for r in rows) / len(rows)
