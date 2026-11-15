# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``run_context.v1``: what a repo run's agent CLI exposed about the run, one record per cell.

Built from three things dokimi already has: the start-up and closing records the trace converter
kept on the :class:`~dokimi.repos.transcript.Transcript`, the command line dokimi started the CLI
with (the profile's ``invoke`` template, filled in), and the version it probed. Every field the CLI
did not print is ``null``; nothing is inferred from a default. Config files are recorded as SHA-256
digests of their bytes, taken before the agent starts. See docs/repo-evaluation-contract.md §4.9.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from dokimi.artifacts import ArtifactValidationError, save_artifact

from .harness_profile import HarnessProfile
from .transcript import Transcript

RUN_CONTEXT_FILE = "run_context.json"
_TURN_RECORDS = ("turn.completed", "turn.failed")  # Codex's closing record, the turn's own
_BYPASS_FLAG = "--dangerously-bypass-approvals-and-sandbox"


def config_digests(run_dir: Path, profile: HarnessProfile, present: Iterable[str]) -> dict[str, str]:
    """SHA-256 of each config file the run is given: the profile's ``config_files`` in the run's config
    directory, and the workspace files ``present`` names. A link is not read (the host never follows one)."""
    paths = {n: run_dir / "config" / n for n in profile.config_files}
    paths.update({n: run_dir / "workspace" / n for n in present})
    return {name: hashlib.sha256(path.read_bytes()).hexdigest()
            for name, path in sorted(paths.items()) if path.is_file() and not path.is_symlink()}


def build(profile: HarnessProfile, *, model: str, harness_version: str | None, argv: Sequence[str],
          transcript: Transcript, config_sha256: dict[str, str]) -> dict[str, Any]:
    """The ``run_context.v1`` payload (the envelope is added on save) for one cell."""
    start = _data(transcript.start_record)
    final = _data(transcript.final_record)
    flags = _Flags(argv)
    return {
        "harness": profile.name,
        "harness_version": _text(transcript.harness_version) or harness_version,
        "model_requested": _text(model),
        "model_served": {"init": _text(start.get("model")) or _text(start.get("selectedModel")),
                         "usage": _keys(final.get("modelUsage"))},
        "effort": (_text(start.get("per_turn_effort_active")) or flags.value("--effort")
                   or flags.config("model_reasoning_effort")),
        "thinking": flags.value("--thinking"),
        "credential_kind": _text(start.get("apiKeySource")),
        "permission_mode": _permission(start, flags),
        "tools": _tools(start, flags),
        "extensions": {
            "plugins": transcript.plugins if "plugins" in start else None,
            "skills": _names(start.get("skills")),
            "mcp_servers": _names(start.get("mcp_servers")),
            "agents": _names(start.get("agents")),
            "output_style": _text(start.get("output_style")),
        },
        "outcome": {
            "stop_reason": _text(final.get("stop_reason")),
            "terminal_reason": _terminal(final),
            "num_turns": transcript.num_turns,
            "permission_denials": len(final["permission_denials"]) if isinstance(final.get("permission_denials"), list) else None,
            "safety_stops": transcript.stop_reasons.count("refusal") if transcript.stop_reasons else None,
        },
        "config_sha256": dict(config_sha256),
    }


def save(run_dir: Path, record: dict[str, Any], errors: list[str]) -> None:
    """Write ``run_context.json`` into the cell's run directory. A record that will not save is a note in the
    cell's trace errors, not a failed cell: the record is best-effort, as the process metrics are."""
    try:
        save_artifact("run_context", record, run_dir / RUN_CONTEXT_FILE)
    except OSError as exc:  # its message names the host path: keep the errno only
        errors.append(f"{RUN_CONTEXT_FILE} not written: {exc.strerror or type(exc).__name__}")
    except ArtifactValidationError as exc:
        errors.append(f"{RUN_CONTEXT_FILE} not written: {str(exc).splitlines()[0][:200]}")


def _permission(start: dict[str, Any], flags: _Flags) -> dict[str, str | None]:
    mode = _text(start.get("permissionMode")) or flags.value("--permission-mode")
    if mode is None and flags.has(_BYPASS_FLAG):
        mode = _BYPASS_FLAG.removeprefix("--")
    return {"mode": mode, "sandbox": flags.value("--sandbox"),
            "approval": flags.value("-a") or flags.value("--ask-for-approval")}


def _tools(start: dict[str, Any], flags: _Flags) -> dict[str, list[str] | None]:
    allowed = _names(start.get("tools")) if "tools" in start else None
    if allowed is None:  # the init record does not list them: the list dokimi passed
        allowed = flags.values("--tools")
    return {"allowed": allowed, "disallowed": flags.values("--disallowedTools")}


def _terminal(final: dict[str, Any]) -> str | None:
    """The closing record's ``terminal_reason``, else (Codex) its own type; no other field stands in for it."""
    if (text := _text(final.get("terminal_reason"))) is not None:
        return text
    return final.get("type") if final.get("type") in _TURN_RECORDS else None


def _data(record: dict[str, Any]) -> dict[str, Any]:
    """A record's payload: Copilot nests its facts under ``data``; the others carry them at the top."""
    return record["data"] if isinstance(record.get("data"), dict) else record


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _keys(value: Any) -> list[str] | None:
    return sorted(str(k) for k in value) if isinstance(value, dict) else None


def _names(value: Any) -> list[str] | None:
    """Names from a list of strings or of objects with a ``name``; None when the CLI printed no list."""
    if not isinstance(value, list):
        return None
    names = [item if isinstance(item, str) else item.get("name") if isinstance(item, dict) else None for item in value]
    return [n for n in names if isinstance(n, str) and n]


class _Flags:
    """The flags of one command line. A flag's values are the words after it up to the next flag, so a
    variadic one (``--tools``, ``--disallowedTools``) takes all of its list; a flag given twice takes both."""

    def __init__(self, argv: Sequence[str]) -> None:
        self._argv = list(argv)

    def has(self, flag: str) -> bool:
        return flag in self._argv

    def values(self, flag: str) -> list[str] | None:
        """Every word the flag takes, empty words dropped; None when the flag is not on the line."""
        if not self.has(flag):
            return None
        found: list[str] = []
        for i, word in enumerate(self._argv):
            if word == flag:
                for item in self._argv[i + 1:]:
                    if item.startswith("-"):
                        break
                    found.append(item)
        return [v for v in found if v]

    def value(self, flag: str) -> str | None:
        """The flag's first value, or None."""
        values = self.values(flag)
        return values[0] if values else None

    def config(self, key: str) -> str | None:
        """The value of ``key`` in a ``-c key=value`` (or ``--config``) override."""
        for word in (self.values("-c") or []) + (self.values("--config") or []):
            if word.startswith(key + "="):
                return word[len(key) + 1:]
        return None
