# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Markdown frontmatter: the ``---`` YAML block that opens a skill, a task or a catalog file.

:func:`parse_frontmatter` is the lenient reader, :func:`parse_frontmatter_strict` raises the YAML
error for a validator to name, and :func:`describe_yaml_error` words one error for a person.
"""

from typing import Any, Dict, Tuple

from dokimi.yaml_unique import load_unique_keys


def parse_frontmatter(text: str) -> "Tuple[Dict[str, Any], str]":
    """Split a Markdown document into (frontmatter dict, body).

    Supports a leading YAML frontmatter block delimited by ``---`` lines, the
    same convention used by Claude Code skill files and most static-site
    generators. When no frontmatter is present, returns ``({}, text)``. Frontmatter
    that is not valid YAML also returns ``{}``; use :func:`parse_frontmatter_strict`
    to see that error.

    Args:
        text: Raw file contents.

    Returns:
        Tuple of (metadata dict, body string with frontmatter stripped).
    """
    return _split_frontmatter(text, strict=False)


def parse_frontmatter_strict(text: str) -> "Tuple[Dict[str, Any], str]":
    """Like :func:`parse_frontmatter`, but frontmatter that is not valid YAML raises.

    For a validator that must name the YAML error rather than read the document
    as having no metadata. The lenient :func:`parse_frontmatter` is unchanged for
    every other caller.

    Raises:
        yaml.YAMLError: The frontmatter block is not valid YAML, repeats a key in one mapping, or
            does not load: a value PyYAML cannot build (the date ``2026-99-99``) or nesting too
            deep for the parser, raised by PyYAML as ``ValueError`` or ``RecursionError`` and re-raised as this.
    """
    return _split_frontmatter(text, strict=True)


def describe_yaml_error(exc: Exception) -> str:
    """One line for a frontmatter YAML error: the file line it is on, and a hint for the common case.

    ``yaml`` numbers the lines of the block it parsed, and that block starts on the file's second
    line (the opening ``---`` is the first), so the file line is the YAML line plus two. The hint
    covers the usual cause, an unquoted ``: `` inside a value such as ``Use when: a task``.
    """
    problem = getattr(exc, "problem", None) or " ".join(str(exc).split()) or "not valid YAML"
    mark = getattr(exc, "problem_mark", None)
    where = f"line {mark.line + 2}" if mark is not None else "line unknown"
    hint = ""
    if "mapping values are not allowed" in problem:
        hint = '; quote a value that contains ": ", e.g. description: "Use when: ..."'
    return f"{where}: {problem}{hint}"


def _split_frontmatter(text: str, *, strict: bool) -> "Tuple[Dict[str, Any], str]":
    import yaml
    if not text:
        return {}, ""

    stripped = text.lstrip("﻿")  # tolerate a UTF-8 BOM
    lines = stripped.splitlines()
    # Same rule as the closing fence: a line that is exactly ``---``. A thematic
    # break (``-----``) or a ``--- title`` line opens nothing.
    if not lines or lines[0].rstrip() != "---":
        return {}, stripped

    # First line is the opening '---'; find the closing fence.
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            raw_meta = "\n".join(lines[1:idx])
            body = "\n".join(lines[idx + 1:]).lstrip("\n")
            try:
                meta = (load_unique_keys(raw_meta) if strict else yaml.safe_load(raw_meta)) or {}
            except yaml.YAMLError:
                if strict:
                    raise
                meta = {}
            except (AttributeError, IndexError, KeyError, TypeError, ValueError, RecursionError) as exc:
                # PyYAML raises these, not YAMLError, for a value that does not fit its tag (2026-02-30,
                # !!int with no value) or nesting too deep: that is invalid frontmatter, not a crash.
                if strict:
                    raise yaml.YAMLError(str(exc)) from exc
                meta = {}
            if not isinstance(meta, dict):
                meta = {}
            return meta, body

    # No closing fence — treat the whole thing as body.
    return {}, stripped
