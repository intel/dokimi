# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A file read once for every probe: parsed data, rows, and what failed to parse.

Files are data here, never code: JSON, JSONL, CSV, YAML and TOML are parsed with
safe loaders (YAML refuses aliases), and a ``.py`` file is only read as text. A
parse failure is a lint problem on the report, not an exception. A git-LFS pointer
(the stub a clone without ``git lfs pull`` holds) is not parsed at all:
:attr:`FileSource.lfs` says what it points to.
"""

from __future__ import annotations

import csv
import io
import json
import re
import tomllib
from functools import cached_property
from pathlib import Path
from typing import Any

import yaml

from dokimi.harnesses.tasks import _AliasRefused, _load_yaml

#: Files larger than this are not parsed (their structure is reported as unread).
MAX_BYTES = 64 * 1024 * 1024

# git-lfs.com/spec/v1: "version <url>", then "oid sha256:<hex>" and "size <n>", under 1 KiB.
_LFS_POINTER = re.compile(
    r"\Aversion https://git-lfs\.github\.com/spec/v1\n(?:[a-z0-9.-]+ [^\n]*\n)*?"
    r"oid (?P<oid>sha256:[0-9a-f]{64})\nsize (?P<size>\d+)\n")
_LFS_MAX_BYTES = 1024


class FileSource:
    """One candidate file. Parsing is lazy and happens at most once.

    *hint* is what another file in the scan declared about this one
    (:mod:`dokimi.task_formats.links`): ``referenced_by`` (that file), ``format``,
    ``fields`` (canonical field name -> the key this file uses, from an Inspect
    ``FieldSpec(input="question")``; :attr:`rows` then also carry each value under
    its canonical name) and ``model_graded`` (an OpenAI evals spec grades it with a
    model, so it needs no ``ideal``).
    """

    def __init__(self, path: Path, hint: dict[str, Any] | None = None) -> None:
        self.path = Path(path)
        self.problems: list[str] = []
        self.hint: dict[str, Any] = dict(hint or {})
        self.fields: dict[str, str] = dict(self.hint.get("fields") or {})

    @property
    def suffix(self) -> str:
        return self.path.suffix.lower()

    @cached_property
    def text(self) -> str | None:
        try:
            if self.path.stat().st_size > MAX_BYTES:
                self.problems.append(f"larger than {MAX_BYTES // (1024 * 1024)} MiB: not read")
                return None
            # st_size is 0 for a device: the bound is on the read, not only the stat
            with self.path.open(encoding="utf-8", errors="replace") as fh:
                text = fh.read(MAX_BYTES + 1)
            if len(text) > MAX_BYTES:
                self.problems.append(f"larger than {MAX_BYTES // (1024 * 1024)} MiB: not read")
                return None
            return text
        except OSError as exc:
            self.problems.append(f"cannot read: {exc}")
            return None

    @cached_property
    def lfs(self) -> dict[str, Any] | None:
        """``{"oid", "size"}`` when the file is a git-LFS pointer, not the content."""
        try:
            if self.path.stat().st_size > _LFS_MAX_BYTES:
                return None
        except OSError:
            return None
        match = _LFS_POINTER.match(self.text or "")
        return {"oid": match["oid"], "size": int(match["size"])} if match else None

    @cached_property
    def data(self) -> Any:
        """The parsed document (JSONL and CSV parse to their list of rows), or ``None``."""
        text = self.text
        if text is None or self.suffix == ".py" or self.lfs is not None:
            return None
        try:
            return _parse(self.suffix, text, self.problems)
        except _AliasRefused as exc:
            # Valid YAML (promptfoo configs share blocks with &anchors): refused, not malformed.
            self.problems.append(f"uses YAML aliases, which are not expanded here, so its structure "
                                 f"was not read ({_one_line(exc)})")
            return None
        except (ValueError, yaml.YAMLError, RecursionError) as exc:
            self.problems.append(f"not valid {_LANG.get(self.suffix, 'data')}: {_one_line(exc)}")
            return None

    @cached_property
    def rows(self) -> list[dict[str, Any]] | None:
        """The records of a list-shaped document (JSONL, a JSON/YAML list), else ``None``."""
        data = self.data
        if not isinstance(data, list):
            return None
        rows = [r for r in data if isinstance(r, dict)]
        if not self.fields:
            return rows
        return [{**r, **{canon: r[key] for canon, key in self.fields.items() if key in r}}
                for r in rows]


_LANG = {".json": "JSON", ".jsonl": "JSONL", ".csv": "CSV", ".yaml": "YAML", ".yml": "YAML",
         ".toml": "TOML"}


def _one_line(exc: BaseException) -> str:
    return " ".join(str(exc).split())[:200]


def _parse(suffix: str, text: str, problems: list[str]) -> Any:
    if suffix == ".jsonl":
        return _parse_jsonl(text, problems)
    if suffix == ".csv":
        return _parse_csv(text)
    if suffix == ".json":
        return json.loads(text)
    if suffix == ".toml":
        return tomllib.loads(text)
    if suffix in (".yaml", ".yml"):
        return _load_yaml(text)
    return None  # a binary or unknown suffix (.parquet) is offered only for its LFS pointer


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


def _parse_csv(text: str) -> list[dict[str, Any]]:
    """Rows of a CSV file with a header line; a cell is a string, as Inspect's csv_dataset reads it."""
    try:
        reader = csv.DictReader(io.StringIO(text))
        rows = [dict(r) for r in reader]
    except csv.Error as exc:
        raise ValueError(str(exc)) from exc
    return [{str(k): v for k, v in r.items() if k is not None} for r in rows]


def first_rows(rows: list[dict[str, Any]] | None, n: int = 50) -> list[dict[str, Any]]:
    """The rows a fingerprint looks at: the first *n*, so a large file costs no more."""
    return (rows or [])[:n]


def key_share(rows: list[dict[str, Any]], *keys: str) -> float:
    """Share of *rows* that hold every one of *keys*."""
    if not rows:
        return 0.0
    return sum(all(k in r for k in keys) for r in rows) / len(rows)
