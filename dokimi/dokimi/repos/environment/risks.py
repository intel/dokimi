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


def _hook_events(doc: Any) -> list[str]:
    """Events with at least one hook of any type (``command``, ``http``, ``prompt``, ``agent``)."""
    hooks = doc.get("hooks") if isinstance(doc, dict) else None
    return sorted(e for e, groups in (hooks.items() if isinstance(hooks, dict) else ())
                  if isinstance(groups, list) and any(isinstance(g, dict) for g in groups))


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
            if path.lower().endswith((".md", ".txt", ".rst")):
                out.append(Capability(path, "hook", "claude-code", "low", "hook documentation"))
            else:
                out.append(Capability(path, "hook", "claude-code", "high", "hook script"))
        elif path in CLAUDE_SETTINGS:
            doc = _json(scan, path)
            if doc is None:
                out.append(Capability(path, "hook", "claude-code", "medium", "not read: invalid, large or a link"))
            else:
                events = _hook_events(doc)
                capabilities = []
                if events:
                    capabilities.append(("hook", "high", "hooks on " + ", ".join(events)))
                status_line = doc.get("statusLine") if isinstance(doc, dict) else None
                if isinstance(status_line, dict) and status_line.get("type") == "command" and isinstance(status_line.get("command"), str):
                    capabilities.append(("command", "high", "statusLine command"))
                if isinstance(doc, dict) and isinstance(doc.get("apiKeyHelper"), str):
                    capabilities.append(("command", "high", "apiKeyHelper command"))
                if isinstance(doc, dict) and doc.get("enableAllProjectMcpServers") is True:
                    capabilities.append(("mcp", "high", "enables all MCP servers"))
                for kind, risk, note in capabilities:
                    out.append(Capability(path, kind, "claude-code", risk, note))
    return sorted(out, key=lambda c: c.path)


_INSTALL_HOOKS = ("preinstall", "install", "postinstall", "prepare", "prepublish")
_SCRIPT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # rm of the root, home, a glob or the whole tree; ``rm -rf dist`` is ordinary cleanup.
    ("destructive", re.compile(r"\brm(?:\s+-{1,2}[a-z-]*){1,6}\s+[\"']?(?:/|~|\$\{?HOME|\*|\./?\*?(?=[\s\"';&|]|$))",
                               re.I)),
    # git clean that forces, but not a dry run (``-n``/``--dry-run``)
    ("destructive", re.compile(r"\bgit\s+clean(?![^\n;&|]{0,200}?\s-(?:[a-z]*n|-dry-run))(?:\s+-{1,2}[a-z-]*){0,6}?"
                               r"\s+(?:-[a-z]*f|--force)", re.I)),
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
_PACKAGE_MANAGERS = ("npm", "yarn", "pnpm", "bun")
# npm subcommands that run a script from the checked-out package.json.
_NPM_SCRIPT_COMMANDS = frozenset({"run", "run-script", "rum", "urn", "test", "t", "tst", "start", "stop", "restart",
                                  "rebuild", "rb", "exec", "x"})
# yarn/pnpm/bun run a package.json script by bare name, so anything that is not one of their own
# commands counts. Install forms are read by ``_installs_with_scripts``.
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
_SEPARATORS = frozenset({";", "&&", "||", "|", "&", ";;", "|&"})
# Install subcommands per package manager; ``None`` is a bare ``yarn``.
_INSTALL_COMMANDS = {
    "npm": frozenset({"install", "i", "in", "ins", "inst", "insta", "instal", "isnt", "isnta", "isntal", "isntall",
                      "ci", "clean-install", "ic", "install-clean", "isntall-clean", "add"}),
    "yarn": frozenset({None, "install", "add"}),
    "pnpm": frozenset({"install", "i", "add"}),
    "bun": frozenset({"install", "i", "add"}),
}
# Package-manager options that take a separate value (``npm --prefix web ci``).
_PM_VALUE_OPTS = frozenset({"--prefix", "-C", "--dir", "--cwd", "--filter", "-F", "--workspace", "--registry",
                            "--cache", "--userconfig", "--loglevel"})
_INFO_OPTS = frozenset({"-v", "--version", "-h", "--help"})
_GLOBAL_OPTS = frozenset({"-g", "--global", "--location=global"})


def _ignores_scripts(segment: str) -> bool:
    """Whether the last ``--ignore-scripts``-style flag in a comment-free command turns scripts off."""
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


def _segments(command: str) -> list[list[str]]:
    """The simple commands of a hook command line, as argv lists.

    Quotes and comments are honoured, so ``echo 'a; npm ci'`` and ``npm ci # --ignore-scripts`` are
    one command each; ``;``, ``&&``, ``||``, ``|``, ``&`` and a newline separate commands.
    """
    out: list[list[str]] = []
    for line in command.replace("\\\n", " ").split("\n"):
        if len(line) > 4096:  # shlex is slow on a long unbalanced quote
            tokens = re.sub(r"(&&|\|\||[;|&])", r" \1 ", line).split()
        else:
            lexer = shlex.shlex(line, posix=True, punctuation_chars=";&|")
            lexer.whitespace_split = True
            lexer.commenters = ""  # shlex would start a comment inside a word (``https://r/#x``)
            try:
                tokens = list(lexer)
                if any(t.startswith("#") for t in tokens):  # a word that starts with `#` starts a comment
                    tokens = tokens[:next(k for k, t in enumerate(tokens) if t.startswith("#"))]
            except ValueError:
                tokens = re.sub(r"(&&|\|\||[;|&])", r" \1 ", line).split()
        current: list[str] = []
        for token in tokens:
            if token == "&" and current and current[-1].endswith((">", "<")):  # 2>&1
                current[-1] += token
            elif token in _SEPARATORS:
                if current:
                    out.append(current)
                current = []
            else:
                current.append(token)
        if current:
            out.append(current)
    return out


def _command_start(argv: list[str]) -> tuple[dict[str, str], int]:
    """``VAR=value`` assignments before the command word, and the command word's index.

    Wrappers such as ``sudo``, ``timeout N``, ``env`` and ``source`` are skipped too.
    """
    env: dict[str, str] = {}
    i = 0
    while i < len(argv):
        word = argv[i]
        if _VAR_ASSIGN.match(word):
            name, _, value = word.partition("=")
            env[name.lower()] = value
            i += 1
        elif word in _WRAPPERS:
            i += 1 + _WRAPPERS[word]
            while i < len(argv) and argv[i].startswith("-"):
                i += 1
        else:
            break
    return env, i


def _subcommand(manager: str, rest: list[str]) -> str | None:
    """A package manager's subcommand: the first word that is not an option or an option's value."""
    j = 0
    while j < len(rest):
        word = rest[j]
        if word in _PM_VALUE_OPTS or (word == "-w" and manager == "npm"):
            j += 2
        elif word.startswith("-"):
            j += 1
        else:
            return word
    return None


def _installs_with_scripts(argv: list[str], npmrc_ignores: bool) -> bool:
    """Whether a command installs the checked-out project with its lifecycle scripts on."""
    env, i = _command_start(argv)
    if i >= len(argv):
        return False
    name = argv[i].rsplit("/", 1)[-1]
    if name not in _INSTALL_COMMANDS:
        return False
    rest = argv[i + 1:]
    sub = _subcommand(name, rest)
    if sub not in _INSTALL_COMMANDS[name] or any(w in _GLOBAL_OPTS or w in _INFO_OPTS for w in rest):
        return False
    if _ignores_scripts(" ".join(rest)):
        return False
    if env.get("npm_config_ignore_scripts", "").lower() in ("true", "1"):
        return False
    return not (npmrc_ignores and name in ("npm", "pnpm") and "--no-ignore-scripts" not in rest
                and not any(w.startswith("--ignore-scripts=") for w in rest))


def _npmrc_ignores_scripts(scan: Scan) -> bool:
    if ".npmrc" not in scan.file_set:
        return False
    return any(re.fullmatch(r"\s*ignore-scripts\s*=\s*true\s*", line, re.I)
               for line in scan.text(".npmrc").splitlines())


def _in_tree(word: str, tracked: set[str]) -> bool:
    word = word.lstrip("'\"")
    return word.startswith(("./", "$CLAUDE_PROJECT_DIR", "${CLAUDE_PROJECT_DIR}")) or word in tracked


def _runs_repository_code(argv: list[str], tracked: set[str], depth: int = 0) -> bool:
    """Whether a hook command runs a script, makefile or file from the checked-out tree."""
    _, i = _command_start(argv)
    if i >= len(argv):
        return False
    cmd, rest = argv[i], argv[i + 1:]
    name = cmd.rsplit("/", 1)[-1]
    if name in ("make", "gmake"):
        return True
    if name in _PACKAGE_MANAGERS:
        sub = _subcommand(name, rest)
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
                    _runs_repository_code(part, tracked, depth + 1) for part in _segments(rest[j + 1]))
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
    npmrc_ignores = _npmrc_ignores_scripts(scan)
    for path in CLAUDE_SETTINGS:
        if path not in scan.file_set:
            continue
        doc = _json(scan, path)
        for event, commands in _hook_entries(doc).items():
            if event not in AUTOMATIC_EVENTS:
                continue
            for command in commands:
                for argv in _segments(command):
                    if (_installs_with_scripts(argv, npmrc_ignores)
                            or _runs_repository_code(argv, tracked_files)):
                        out.add(HookRisk(path, event, shlex.join(argv)))
    return sorted(out, key=lambda r: (r.path, r.event, r.command))
