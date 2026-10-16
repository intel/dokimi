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
import shlex
from dataclasses import dataclass
from typing import Any

from dokimi.repos.l0.core import Scan
from dokimi.repos.source import SourceError

from .checks import package_scripts

MAX_SETTINGS_BYTES = 1_000_000


@dataclass(frozen=True)
class Capability:
    path: str
    kind: str  # mcp | hook | command | plugin | skill | editor
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
                # Check for hooks
                if hooks:
                    capabilities.append(("hook", "high", "hooks on " + ", ".join(sorted(hooks))))
                # Check for statusLine commands
                status_line = doc.get("statusLine") if isinstance(doc, dict) else None
                if isinstance(status_line, dict) and status_line.get("type") == "command" and isinstance(status_line.get("command"), str):
                    capabilities.append(("command", "high", "statusLine command"))
                # Check for apiKeyHelper
                if isinstance(doc, dict) and isinstance(doc.get("apiKeyHelper"), str):
                    capabilities.append(("command", "high", "apiKeyHelper command"))
                # Check for enableAllProjectMcpServers
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
_OPT = r"(?:\s+-{1,2}[A-Za-z][\w-]*(?:=\S+)?){0,16}"
_INSTALL = re.compile(rf"(?<![\w-])(?:npm|yarn|pnpm|bun){_OPT}\s+install\b|(?<![\w-])(?:npm|pnpm){_OPT}\s+ci\b"
                      rf"|(?<![\w-])(?:npm|pnpm|bun){_OPT}\s+i\b", re.I)
_PACKAGE_MANAGERS = ("npm", "yarn", "pnpm", "bun")
# npm subcommands that run a script from the checked-out package.json.
_NPM_SCRIPT_COMMANDS = frozenset({"run", "run-script", "rum", "urn", "test", "t", "tst", "start", "stop", "restart",
                                  "rebuild", "rb", "exec", "x"})
# yarn/pnpm/bun run a package.json script by bare name, so anything that is not one of their own
# commands counts. Install forms are matched by ``_INSTALL`` first.
_PM_BUILTINS = frozenset({"install", "i", "ci", "add", "remove", "rm", "uninstall", "un", "ls", "list", "info", "view",
                          "config", "why", "outdated", "help", "audit", "init", "create", "link", "unlink", "upgrade",
                          "update", "up", "cache", "store", "dlx", "login", "logout", "whoami", "pack", "publish",
                          "version", "set", "dedupe", "prune", "env", "bin", "root", "-v", "--version", "-h", "--help"})
# Words that run the command after them: wrappers, with how many of their own arguments to skip.
_WRAPPERS = {"sudo": 0, "exec": 0, "nohup": 0, "env": 0, "command": 0, "nice": 0, "time": 0, "timeout": 1,
             "source": 0, ".": 0}
_INTERPRETERS = frozenset({"bash", "sh", "zsh", "dash", "python", "python3", "node", "ruby", "perl", "bun", "deno",
                           "tsx", "ts-node"})
# Interpreter options that take a separate value (``python3 -W ignore x.py``).
_INTERPRETER_VALUE_OPTS = frozenset({"-W", "-X", "--require", "-r", "--import", "-O0"})
_VAR_ASSIGN = re.compile(r"^[A-Za-z_]\w*=")
_MAX_SHELL_DEPTH = 3
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
    # --ignore-scripts with an explicit false value means scripts WILL run
    if (last.group(1) or "").lower() in ("false", "0"):
        return False
    # --ignore-scripts with true/1 or no value defaults to true (safe)
    return True


def _argv(segment: str) -> list[str]:
    if len(segment) > 4096:  # shlex is slow on a long unbalanced quote
        return segment.split()
    try:
        return shlex.split(segment, comments=True)
    except ValueError:
        return segment.split()


def _in_tree(word: str, tracked: set[str]) -> bool:
    word = word.lstrip("'\"")
    return word.startswith(("./", "$CLAUDE_PROJECT_DIR", "${CLAUDE_PROJECT_DIR}")) or word in tracked


def _runs_repository_code(segment: str, tracked: set[str], depth: int = 0) -> bool:
    """Whether a hook command runs a script, makefile or file from the checked-out tree."""
    argv = _argv(segment)
    i = 0
    while i < len(argv):  # skip VAR=value words and wrappers such as sudo, timeout N, env, source
        word = argv[i]
        if _VAR_ASSIGN.match(word):
            i += 1
        elif word in _WRAPPERS:
            i += 1 + _WRAPPERS[word]
            while i < len(argv) and argv[i].startswith("-"):
                i += 1
        else:
            break
    if i >= len(argv):
        return False
    cmd, rest = argv[i], argv[i + 1:]
    name = cmd.rsplit("/", 1)[-1]
    if name in ("make", "gmake"):
        return True
    if name in _PACKAGE_MANAGERS:
        sub = next((w for w in rest if not w.startswith("-")), None)
        if sub is None:
            return False
        if name == "npm":
            return sub in _NPM_SCRIPT_COMMANDS
        return sub not in _PM_BUILTINS
    if name in _INTERPRETERS:
        j = 0
        while j < len(rest):
            word = rest[j]
            if word == "-c" and name in ("bash", "sh", "zsh", "dash"):
                return j + 1 < len(rest) and depth < _MAX_SHELL_DEPTH and any(
                    _runs_repository_code(part.strip(), tracked, depth + 1)
                    for part in _HOOK_SPLIT.split(rest[j + 1]) if part.strip())
            if word in _INTERPRETER_VALUE_OPTS:
                j += 2
            elif word.startswith("-"):
                j += 1
            else:
                return _in_tree(word, tracked)
        return False
    return _in_tree(cmd, tracked)


def detect_hook_risks(scan: Scan) -> list[HookRisk]:
    """Automatic Claude Code hooks that execute repository code (install with lifecycle scripts, run, make, paths)."""
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
                    elif _runs_repository_code(segment, tracked_files):
                        out.add(HookRisk(path, event, segment))
    return sorted(out, key=lambda r: (r.path, r.event, r.command))
