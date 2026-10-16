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
    except (json.JSONDecodeError, RecursionError, ValueError):
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
            else:
                hooks = _hook_entries(doc)
                capabilities = []
                # Check for hooks (original behavior)
                if hooks:
                    capabilities.append(("hook", "high", "hooks on " + ", ".join(sorted(hooks))))
                # Check for statusLine commands (THREAD 4)
                status_line = doc.get("statusLine") if isinstance(doc, dict) else None
                if isinstance(status_line, dict) and status_line.get("type") == "command" and isinstance(status_line.get("command"), str):
                    capabilities.append(("hook", "high", "statusLine command"))
                # Check for apiKeyHelper (THREAD 4)
                if isinstance(doc, dict) and isinstance(doc.get("apiKeyHelper"), str):
                    capabilities.append(("hook", "high", "apiKeyHelper command"))
                # Check for enableAllProjectMcpServers (THREAD 4)
                if isinstance(doc, dict) and doc.get("enableAllProjectMcpServers") is True:
                    capabilities.append(("mcp", "high", "enables all MCP servers"))
                # Add capability records for all detected capabilities
                for kind, risk, note in capabilities:
                    out.append(Capability(path, kind, "claude-code", risk, note))
    return sorted(out, key=lambda c: c.path)


_INSTALL_HOOKS = ("preinstall", "install", "postinstall", "prepare", "prepublish")
_SCRIPT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("destructive", re.compile(r"\brm\s+-[a-z]*(?:\s+-[a-z]*){0,4}\s+(?:/|~|\$HOME|\*)", re.I)),
    ("destructive", re.compile(r"\brm\s+-(rf|fr|r\s+-f|f\s+-r)\b", re.I)),
    ("destructive", re.compile(r"\bgit\s+clean\s+-[a-z]*f", re.I)),
    ("destructive", re.compile(r"\b(?:mkfs|dd\s+if=|>\s*/dev/sd)", re.I)),
    ("network-exec", re.compile(r"\b(?:curl|wget|fetch)\b[^|]{0,200}\|\s*(?:sudo\s+)?(?:sh|bash|zsh|node|python3?)\b", re.I)),
    ("deploy", re.compile(r"\b(?:npm|yarn|pnpm)\s+publish\b", re.I)),
    ("deploy", re.compile(r"\b(?:vercel|netlify|wrangler|firebase)\s+deploy\b", re.I)),
    ("deploy", re.compile(r"\bgh\s+release\s+(?:create|upload)\b", re.I)),
    ("deploy", re.compile(r"\b(?:kubectl\s+apply|terraform\s+apply|aws\s+s3\s+(?:cp|sync|rm)|gcloud\s+\w+\s+deploy)\b",
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
AUTOMATIC_EVENTS = ("SessionStart", "SessionEnd", "PreCompact", "Notification", "UserPromptSubmit", "Stop", "SubagentStop")
_OPT = r"(?:\s+-{1,2}[A-Za-z][\w-]*(?:=\S+)?){0,8}"
_INSTALL = re.compile(rf"(?<![\w-])(?:npm|yarn|pnpm|bun){_OPT}\s+install\b|(?<![\w-])(?:npm|pnpm){_OPT}\s+ci\b"
                      rf"|(?<![\w-])(?:npm|pnpm|bun){_OPT}\s+i\b", re.I)
_RUN_COMMAND = re.compile(r"(?<![\w-])(?:npm|yarn|pnpm|bun)\s+run\s+[\w:-]+\b", re.I)
_MAKE = re.compile(r"^(?:\w+=\S*\s+)*(?:sudo\s+)?g?make\b")
_HOOK_SPLIT = re.compile(r"&&|\|\||;|\n")  # not a single `|`: `npm ci | tee log` still installs


def _ignores_scripts(segment: str) -> bool:
    # Strip comments only after whitespace or at the start of a segment
    segment = re.sub(r"(?:^|\s)#.*$", "", segment).strip()
    # Find all --ignore-scripts or --no-ignore-scripts flags (not just matches)
    ignore_flags = list(re.finditer(r"--no-ignore-scripts|--ignore-scripts(?:[= ]+(true|false|1|0))?", segment, re.I))
    if not ignore_flags:
        return False
    last = ignore_flags[-1]
    flag = last.group(0).lower()
    # --no-ignore-scripts always means scripts WILL run (not safe)
    if flag.startswith("--no-ignore-scripts"):
        return False
    # --ignore-scripts with explicit false means scripts WILL run
    if "false" in flag or "0" in flag:
        return False
    # --ignore-scripts with true/1 or no value defaults to true (safe)
    return True


def detect_hook_risks(scan: Scan) -> list[HookRisk]:
    """Automatic Claude Code hooks that execute repository code (install with lifecycle scripts, run, make, paths)."""
    import shlex

    out = set()
    tracked_files = set(scan.files)
    for path in CLAUDE_SETTINGS:
        if path not in scan.file_set:
            continue
        doc = _json(scan, path)
        for event, commands in _hook_entries(doc).items():
            if event not in AUTOMATIC_EVENTS:
                continue
            for command in commands:
                for segment in (s.strip() for s in _HOOK_SPLIT.split(command)):
                    if not segment:
                        continue
                    # Check for install with lifecycle scripts
                    if _INSTALL.search(segment) and not _ignores_scripts(segment):
                        out.add(HookRisk(path, event, segment))
                    # Check for npm/yarn/pnpm/bun run commands (THREAD 0)
                    elif _RUN_COMMAND.search(segment):
                        out.add(HookRisk(path, event, segment))
                    # Check for make commands (THREAD 0)
                    elif _MAKE.search(segment):
                        out.add(HookRisk(path, event, segment))
                    # Check for paths into the tree (THREAD 0): ./path, $CLAUDE_PROJECT_DIR/path, or tracked files
                    else:
                        # Shlex-split the segment to extract the command word
                        try:
                            argv = shlex.split(segment)
                        except ValueError:
                            argv = segment.split()

                        if argv:
                            # Get the command word (argv[0] or argv[1] after interpreter)
                            cmd_idx = 0
                            if argv[0] in ('bash', 'sh', 'zsh', 'python', 'python3', 'node', 'ruby', 'perl', 'bun', 'deno', 'tsx'):
                                cmd_idx = 1

                            if cmd_idx < len(argv):
                                cmd_word = argv[cmd_idx]
                                # Check if it starts with ./ or $CLAUDE_PROJECT_DIR or is a tracked file
                                if cmd_word.startswith('./') or cmd_word.startswith('$CLAUDE_PROJECT_DIR') or cmd_word.startswith('${CLAUDE_PROJECT_DIR}') or cmd_word in tracked_files:
                                    out.add(HookRisk(path, event, segment))
    return sorted(out, key=lambda r: (r.path, r.event, r.command))
