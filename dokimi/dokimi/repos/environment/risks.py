# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""What an agent session, or a command it runs, could do beyond editing files.

Three layers, kept apart: a capability surface says the harness *can* act (an MCP config, a
hook, a plugin); a script signal says a command the agent is likely to run does something
risky; a hook risk is the concrete path: a hook the harness runs on its own runs an install
that runs the checked-out branch's lifecycle scripts. None of it is proof of a vulnerability.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from dokimi.repos.l0.core import Scan
from dokimi.repos.source import SourceError

from .checks import package_scripts

MAX_SETTINGS_BYTES = 1_000_000


@dataclass(frozen=True)
class Capability:
    path: str
    kind: str  # mcp | hook | plugin | skill | editor
    harness: str
    risk: str  # high | medium | low
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "kind": self.kind, "harness": self.harness, "risk": self.risk, "note": self.note}


@dataclass(frozen=True)
class ScriptSignal:
    category: str  # install-hook | destructive | network-exec | deploy
    source: str  # package.json#scripts.<name>
    command: str

    def to_dict(self) -> dict[str, Any]:
        return {"category": self.category, "source": self.source, "command": self.command}


@dataclass(frozen=True)
class HookRisk:
    path: str
    event: str
    command: str

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "event": self.event, "command": self.command}


_EXACT = {
    ".mcp.json": ("mcp", "claude-code", "high"),
    ".cursor/mcp.json": ("mcp", "cursor", "high"),
    ".vscode/mcp.json": ("mcp", "vscode", "high"),
    ".claude-plugin/plugin.json": ("plugin", "claude-code", "high"),
    ".claude-plugin/marketplace.json": ("plugin", "claude-code", "high"),
    ".vscode/settings.json": ("editor", "vscode", "low"),
    ".vscode/extensions.json": ("editor", "vscode", "low"),
}
CLAUDE_SETTINGS = (".claude/settings.json", ".claude/settings.local.json")


def _json(scan: Scan, path: str) -> Any:
    try:
        if scan.tree.resolve(path) != path:  # a link: do not follow it out of the reviewed file
            return None
    except SourceError:
        return None
    text = scan.text(path)
    if not text or len(text) > MAX_SETTINGS_BYTES:
        return None
    try:
        return json.loads(text.lstrip("\ufeff"))
    except (json.JSONDecodeError, RecursionError):
        return None


def _hook_entries(doc: Any) -> dict[str, list[str]]:
    """event -> hook commands of a Claude Code settings document."""
    hooks = doc.get("hooks") if isinstance(doc, dict) else None
    out: dict[str, list[str]] = {}
    for event, groups in (hooks.items() if isinstance(hooks, dict) else ()):
        for group in groups if isinstance(groups, list) else []:
            inner = group.get("hooks") if isinstance(group, dict) else None
            for h in inner if isinstance(inner, list) else [group]:
                if isinstance(h, dict) and isinstance(h.get("command"), str):
                    out.setdefault(event, []).append(h["command"])
    return out


def detect_capabilities(scan: Scan) -> list[Capability]:
    out = []
    for path in scan.files:
        if path in _EXACT:
            kind, harness, risk = _EXACT[path]
            out.append(Capability(path, kind, harness, risk))
        elif path.startswith(".claude/skills/") and path.endswith("/SKILL.md"):
            out.append(Capability(path, "skill", "claude-code", "low"))
        elif path.startswith(".claude/hooks/"):
            out.append(Capability(path, "hook", "claude-code", "high", "hook script"))
        elif path in CLAUDE_SETTINGS:
            doc = _json(scan, path)
            if doc is None:
                out.append(Capability(path, "hook", "claude-code", "medium", "not read: invalid, large or a link"))
            elif _hook_entries(doc):
                out.append(Capability(path, "hook", "claude-code", "high",
                                      "hooks on " + ", ".join(sorted(_hook_entries(doc)))))
    return sorted(out, key=lambda c: c.path)


_INSTALL_HOOKS = ("preinstall", "install", "postinstall", "prepare", "prepublish")
_SCRIPT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("destructive", re.compile(r"\brm\s+-[a-z]*\s*(-[a-z]*\s+)*(/|~|\$HOME|\*)", re.I)),
    ("destructive", re.compile(r"\brm\s+-(rf|fr|r\s+-f|f\s+-r)\b", re.I)),
    ("destructive", re.compile(r"\bgit\s+clean\s+-[a-z]*f", re.I)),
    ("destructive", re.compile(r"\b(mkfs|dd\s+if=|>\s*/dev/sd)", re.I)),
    ("network-exec", re.compile(r"\b(curl|wget|fetch)\b[^|]*\|\s*(sudo\s+)?(sh|bash|zsh|node|python3?)\b", re.I)),
    ("deploy", re.compile(r"\b(npm|yarn|pnpm)\s+publish\b", re.I)),
    ("deploy", re.compile(r"\b(vercel|netlify|wrangler|firebase)\s+deploy\b", re.I)),
    ("deploy", re.compile(r"\bgh\s+release\s+(create|upload)\b", re.I)),
    ("deploy", re.compile(r"\b(kubectl\s+apply|terraform\s+apply|aws\s+s3\s+(cp|sync|rm)|gcloud\s+\w+\s+deploy)\b",
                          re.I)),
)


def detect_script_signals(scan: Scan) -> list[ScriptSignal]:
    """At most one signal per category per root ``package.json`` script."""
    out = []
    for name, body in package_scripts(scan).items():
        source = f"package.json#scripts.{name}"
        if name in _INSTALL_HOOKS:
            out.append(ScriptSignal("install-hook", source, body))
        seen = set()
        for category, rx in _SCRIPT_PATTERNS:
            if category not in seen and rx.search(body):
                seen.add(category)
                out.append(ScriptSignal(category, source, body))
    return sorted(out, key=lambda s: (s.category, s.source))


# Events the harness fires without the agent choosing to run anything.
AUTOMATIC_EVENTS = ("SessionStart", "SessionEnd", "PreCompact", "Notification")
_OPT = r"(?:\s+-{1,2}[A-Za-z][\w-]*(?:=\S+)?)*"
_INSTALL = re.compile(rf"\b(?:npm|yarn|pnpm|bun){_OPT}\s+install\b|\b(?:npm|pnpm){_OPT}\s+ci\b"
                      rf"|\b(?:npm|pnpm|bun){_OPT}\s+i\b", re.I)
_IGNORE_SCRIPTS = re.compile(r"--(no-)?ignore-scripts\b(?:[= ]+(true|false|1|0)\b)?", re.I)
_HOOK_SPLIT = re.compile(r"&&|\|\||;|\n")  # not a single `|`: `npm ci | tee log` still installs


def _ignores_scripts(segment: str) -> bool:
    flags = list(_IGNORE_SCRIPTS.finditer(segment))
    last = flags[-1] if flags else None  # the last flag wins, as on a command line
    if last is None:
        return False
    if last.group(2) is not None:
        return last.group(2).lower() in ("true", "1")
    return last.group(1) is None


def detect_hook_risks(scan: Scan) -> list[HookRisk]:
    """Automatic Claude Code hooks whose command installs packages with lifecycle scripts on."""
    out = set()
    for path in CLAUDE_SETTINGS:
        if path not in scan.file_set:
            continue
        doc = _json(scan, path)
        for event, commands in _hook_entries(doc).items():
            if event not in AUTOMATIC_EVENTS:
                continue
            for command in commands:
                for segment in (s.strip() for s in _HOOK_SPLIT.split(command)):
                    if _INSTALL.search(segment) and not _ignores_scripts(segment):
                        out.add(HookRisk(path, event, segment))
    return sorted(out, key=lambda r: (r.path, r.event, r.command))
