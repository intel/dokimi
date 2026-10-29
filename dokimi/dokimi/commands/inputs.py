# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Reading a file a command was pointed at, with an exit that names it."""

from __future__ import annotations

from pathlib import Path
from dokimi.exit_codes import usage_exit


def read_text_or_exit(path: str | None, flag: str) -> str | None:
    """The text of the file `flag` names, None when the flag is unset.

    A file that cannot be read stops the command with a message naming the flag and the
    path, instead of a traceback.
    """
    if not path:
        return None
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        usage_exit(f"cannot read {flag} {path}: {exc}")


def placebo_text_for_arms_or_exit(path: str | None, arms: tuple[str, ...]) -> str | None:
    """The ``--placebo-text`` of an analyze run, checked against its arms before any model call.

    A typo in --arms or --placebo-text would otherwise cost the extraction calls that run
    before the measurement checks the same thing.
    """
    from dokimi.novelty.measure import check_arms

    placebo_text = read_text_or_exit(path, "--placebo-text")
    try:
        check_arms(arms, placebo_text)
    except ValueError as exc:
        usage_exit(str(exc))
    return placebo_text
