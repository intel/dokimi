#!/usr/bin/env python3
# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Claude Code PreToolUse hook: refuse backgrounded shell commands in headless runs.

``claude -p`` exits when the model ends its turn, so a build left running in the
background is killed and the run fails for a reason that has nothing to do with
the task. Mounted read-only into the agent's container at ``/eval/support`` and
run with the image's ``python3``; standard library only. Each denial is appended
to ``$DOKIMI_HOOK_LOG`` when it is set.
"""

from __future__ import annotations  # runs on the image's python3, which may be old

import json
import os
import re
import shlex
import sys

_BACKGROUND = re.compile(r"(?<![&|>])&(?![&>])|\bnohup\b|\bsetsid\b|\bdisown\b")
_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")
_SHELLS = frozenset({"sh", "bash", "dash", "zsh", "ksh"})


def reason(tool_input: dict) -> str | None:
    if tool_input.get("run_in_background"):
        return "run_in_background"
    if _backgrounds(str(tool_input.get("command", ""))):
        return "shell background"
    return None


def _backgrounds(command: str, depth: int = 0) -> bool:
    """A ``&`` outside quotes, or in the script a nested ``bash -c '...'`` runs (quoted text is
    otherwise data: ``echo "a & b"`` is allowed)."""
    if _BACKGROUND.search(_QUOTED.sub("", command)):
        return True
    return depth < 3 and any(_backgrounds(script, depth + 1) for script in _shell_scripts(command))


def _shell_scripts(command: str) -> list:
    """The ``-c`` argument of every ``sh``/``bash``/... in ``command``."""
    try:
        words = shlex.split(command)
    except ValueError:
        return []
    out = []
    for i, word in enumerate(words):
        if os.path.basename(word) not in _SHELLS:
            continue
        for j in range(i + 1, len(words) - 1):
            flag = words[j]
            if not flag.startswith("-") or flag.startswith("--"):
                break
            if "c" in flag[1:]:  # -c, -lc, -ec
                out.append(words[j + 1])
                break
    return out


def main() -> int:
    event = json.load(sys.stdin)
    tool_input = event.get("tool_input") or {}
    bad = reason(tool_input)
    if not bad:
        return 0
    log = os.environ.get("DOKIMI_HOOK_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as f:
            f.write(json.dumps({"denied": bad, "command": str(tool_input.get("command", ""))[:500]}) + "\n")
    print(f"Background commands are disabled in this environment ({bad}). Run the command in the "
          "foreground; long builds are fine, the tool timeout can be raised with the timeout parameter.",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
