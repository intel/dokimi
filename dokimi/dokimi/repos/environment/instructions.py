# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Instruction files coding harnesses read, and the part of the tree each one applies to.

The table is what each harness documents, not what a given version does: ``repo reach``
measures that. Every rule that matches a path gives its own record, so ``AGENTS.md`` and
``CLAUDE.md`` at the root are two records, and a nested ``CLAUDE.md`` is path-scoped.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field
from typing import Any, Callable

LARGE_INSTRUCTION_BYTES = 200_000  # a file this big costs a session a large share of its context

AGENTS_MD = ("codex", "github-copilot", "cursor", "windsurf", "cline", "generic")


@dataclass(frozen=True)
class Rule:
    match: Callable[[str], bool]
    harnesses: tuple[str, ...]
    scope: str  # root | path-specific | mode-specific | local-private | legacy | capability | unknown
    activation: str  # always | path-scoped | mode-scoped | manual | on-demand | unknown
    directory: Callable[[str], str | None] = lambda p: None
    mode: Callable[[str], str | None] = lambda p: None
    legacy: bool = False
    local_private: bool = False
    nested: tuple[str, str] | None = None  # (scope, activation) when the file is below the root
    notes: tuple[str, ...] = ()


@dataclass
class Instruction:
    path: str
    harnesses: list[str]
    scope: str
    activation: str
    directory: str | None = None
    mode: str | None = None
    legacy: bool = False
    local_private: bool = False
    size_bytes: int | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "harnesses": self.harnesses, "scope": self.scope, "activation": self.activation,
                "directory": self.directory, "mode": self.mode, "legacy": self.legacy,
                "local_private": self.local_private, "size_bytes": self.size_bytes, "notes": self.notes}


def _named(name: str) -> Callable[[str], bool]:
    return lambda p: p == name


def _nested(name: str, but: tuple[str, ...] = ()) -> Callable[[str], bool]:
    return lambda p: p.endswith("/" + name) and p not in but


def _either(name: str) -> Callable[[str], bool]:
    return lambda p: p == name or p.endswith("/" + name)


def _under(prefix: str, pattern: str) -> Callable[[str], bool]:
    rx = re.compile(pattern)
    return lambda p: p.startswith(prefix) and rx.search(p[len(prefix):]) is not None


def _parent(p: str) -> str:
    return posixpath.dirname(p) or "."


def _roo_mode(p: str) -> str | None:
    if p.startswith(".roo/rules-"):
        return p.split("/")[1].removeprefix("rules-")
    if p.startswith(".roorules-"):
        return re.sub(r"\.(md|txt)$", "", p.removeprefix(".roorules-"))
    return None


RULES: tuple[Rule, ...] = (
    Rule(_named("AGENTS.md"), AGENTS_MD, "root", "always"),
    Rule(_nested("AGENTS.md"), AGENTS_MD, "path-specific", "path-scoped", directory=_parent),
    Rule(_either("AGENTS.override.md"), ("codex",), "root", "always", directory=_parent,
         nested=("path-specific", "path-scoped")),
    Rule(_named("CLAUDE.md"), ("claude-code", "github-copilot"), "root", "always"),
    Rule(_named(".claude/CLAUDE.md"), ("claude-code",), "root", "always"),
    Rule(_nested("CLAUDE.md", but=(".claude/CLAUDE.md",)), ("claude-code",), "path-specific", "path-scoped",
         directory=_parent),
    Rule(_either("CLAUDE.local.md"), ("claude-code",), "local-private", "always", directory=_parent,
         local_private=True, notes=("personal file; a session of someone else does not see it",)),
    Rule(_under(".claude/rules/", r"\.md$"), ("claude-code",), "unknown", "unknown",
         notes=("activation depends on the rule's frontmatter",)),
    Rule(_under(".claude/skills/", r"(^|/)SKILL\.md$"), ("claude-code",), "capability", "on-demand",
         directory=_parent),
    Rule(_named(".github/copilot-instructions.md"), ("github-copilot",), "root", "always"),
    Rule(_under(".github/instructions/", r"\.instructions\.md$"), ("github-copilot",), "path-specific",
         "path-scoped", notes=("the paths it applies to are in its applyTo frontmatter",)),
    Rule(_under(".github/agents/", r"\.agent\.md$"), ("github-copilot",), "capability", "manual",
         directory=_parent),
    Rule(_under(".cursor/rules/", r"\.mdc$"), ("cursor",), "unknown", "unknown",
         notes=("activation depends on the rule's frontmatter",)),
    Rule(_named(".cursorrules"), ("cursor", "cline"), "legacy", "always", legacy=True),
    Rule(_named("GEMINI.md"), ("gemini", "github-copilot"), "root", "always"),
    Rule(_nested("GEMINI.md"), ("gemini",), "path-specific", "path-scoped", directory=_parent),
    Rule(_under(".windsurf/rules/", r"\.md$"), ("windsurf",), "unknown", "unknown"),
    Rule(_named(".windsurfrules"), ("windsurf", "cline"), "legacy", "always", legacy=True),
    Rule(_under(".clinerules/", r"\.(md|txt)$"), ("cline",), "root", "unknown"),
    Rule(_named(".clinerules"), ("cline",), "legacy", "always", legacy=True),
    Rule(_under(".roo/rules/", r"\.(md|txt)$"), ("roo-code",), "root", "always"),
    Rule(lambda p: re.match(r"^\.roo/rules-[^/]+/.+\.(md|txt)$", p) is not None, ("roo-code",), "mode-specific",
         "mode-scoped", directory=_parent, mode=_roo_mode),
    Rule(_named(".roorules"), ("roo-code",), "legacy", "always", legacy=True),
    Rule(lambda p: re.match(r"^\.roorules-[^/]+$", p) is not None, ("roo-code",), "mode-specific", "mode-scoped",
         mode=_roo_mode, legacy=True),
    Rule(_named(".roomodes"), ("roo-code",), "mode-specific", "mode-scoped"),
)


def detect_instructions(files: list[str], sizes: dict[str, int] | None = None) -> list[Instruction]:
    """One record per (file, matching rule), sorted by path."""
    out: list[Instruction] = []
    for path in files:
        for rule in RULES:
            if not rule.match(path):
                continue
            nested = rule.nested is not None and "/" in path
            scope, activation = rule.nested if nested and rule.nested else (rule.scope, rule.activation)
            item = Instruction(path, list(rule.harnesses), scope, activation, directory=rule.directory(path),
                               mode=rule.mode(path), legacy=rule.legacy, local_private=rule.local_private,
                               notes=list(rule.notes))
            size = (sizes or {}).get(path)
            if size is not None:
                item.size_bytes = size
                if size > LARGE_INSTRUCTION_BYTES:
                    item.notes.append(f"{size} bytes: loading it costs a large share of a session's context")
            out.append(item)
    return sorted(out, key=lambda i: (i.path, i.scope))
