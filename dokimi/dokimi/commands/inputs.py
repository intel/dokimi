# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Reading a file a command was pointed at, with an exit that names it."""

from __future__ import annotations

from pathlib import Path


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
        raise SystemExit(f"cannot read {flag} {path}: {exc}") from None
