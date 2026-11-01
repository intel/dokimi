# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The ``useful`` key of ``task.json``: the event that is a run's first useful result (contract §3, §4.1).

``{"command": "<regex>"}`` names the test run that passes the target tests; ``{"edit": "<glob>"}`` (or a list
of globs) the first edit of a target file. :mod:`.useful` reads it. No parser imports :mod:`.manifest`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .errors import PrepError

SHAPE = 'useful must be {"command": <regex>} or {"edit": <glob>} (or a list of globs)'


class UsefulSpecError(PrepError):
    """A ``task.json`` ``useful`` value of the wrong shape."""


@dataclass(frozen=True)
class Useful:
    """One task's definition: ``command`` (a regex over command text) or ``edit`` (globs over edited paths)."""

    kind: str
    command: re.Pattern[str] | None = None
    globs: tuple[str, ...] = ()


def parse_useful(value: Any) -> Useful | None:
    """The task's definition, or None when it names none; :class:`UsefulSpecError` when the shape is wrong."""
    if value is None:
        return None
    if not (isinstance(value, dict) and len(value) == 1):
        raise UsefulSpecError(SHAPE)
    ((kind, pattern),) = value.items()
    if kind == "command" and isinstance(pattern, str) and pattern:
        try:
            return Useful("command", command=re.compile(pattern))
        except (re.error, RecursionError) as exc:  # a deeply nested pattern overflows the parser
            raise UsefulSpecError(f"useful command is not a valid regex ({exc})") from exc
    globs = [pattern] if isinstance(pattern, str) else pattern
    if (
        kind == "edit"
        and isinstance(globs, list)
        and globs
        and all(isinstance(g, str) and g for g in globs)
    ):
        return Useful("edit", globs=tuple(globs))
    raise UsefulSpecError(SHAPE)
