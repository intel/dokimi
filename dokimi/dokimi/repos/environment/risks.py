# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""What an agent session, or a command it runs, could do beyond editing files.

Three layers, kept apart: a capability surface says the harness *can* act (an MCP config, a
hook, a plugin); a script signal says a command the agent is likely to run does something
risky; a hook risk is the concrete path: a hook the harness runs on its own runs an install
that runs the checked-out branch's lifecycle scripts. None of it is proof of a vulnerability.
"""

from __future__ import annotations

import bisect
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
    ".vscode/settings.json": ("editor", "vscode", "low"),
    ".vscode/extensions.json": ("editor", "vscode", "low"),
}
PLUGIN_MANIFESTS = (".claude-plugin/plugin.json", ".claude-plugin/marketplace.json")
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


def _declares_code(doc: dict[str, Any]) -> list[str]:
    """Manifest keys that bring hooks or MCP servers (inline, or a path such as ``./hooks/hooks.json``)."""
    entries = [doc]
    if isinstance(doc.get("plugins"), list):
        entries.extend(p for p in doc["plugins"] if isinstance(p, dict))
    return [k for k in ("hooks", "mcpServers") if any(e.get(k) for e in entries)]


def unreadable_plugin_manifests(scan: Scan) -> list[str]:
    return [p for p in PLUGIN_MANIFESTS if p in scan.file_set and not isinstance(_json(scan, p), dict)]


def unreadable_claude_settings(scan: Scan) -> list[str]:
    return [p for p in CLAUDE_SETTINGS if p in scan.file_set and not isinstance(_json(scan, p), dict)]


def detect_capabilities(scan: Scan) -> list[Capability]:
    out = []
    for path in scan.files:
        if path in _EXACT:
            kind, harness, risk = _EXACT[path]
            out.append(Capability(path, kind, harness, risk))
        elif path in PLUGIN_MANIFESTS:
            doc = _json(scan, path)
            if not isinstance(doc, dict):
                out.append(Capability(path, "plugin", "claude-code", "high", "not read: invalid, large, a link or not an object"))
            elif keys := _declares_code(doc):
                out.append(Capability(path, "plugin", "claude-code", "high", "declares " + ", ".join(keys)))
            else:
                out.append(Capability(path, "plugin", "claude-code", "medium"))
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
                          "version", "set", "dedupe", "prune", "env", "bin", "root", "global", "-v", "--version", "-h",
                          "--help", "fetch", "self-update", "setup", "plugin", "pm", "check", "licenses", "npm"})
# Words that run the command after them: wrappers, with how many positional arguments of their own
# to skip after their options (``timeout 5 cmd``).
_WRAPPERS = {"sudo": 0, "exec": 0, "nohup": 0, "env": 0, "command": 0, "nice": 0, "time": 0, "timeout": 1,
             "source": 0, ".": 0, "doas": 0, "stdbuf": 0}
# Wrapper options that take a separate value (``sudo -u nobody``, ``nice -n 10``, ``timeout -s KILL``).
_WRAPPER_VALUE_OPTS = {
    "sudo": frozenset({"-u", "-g", "-C", "-D", "-h", "-p", "-r", "-t", "-U", "-T", "--user", "--group",
                       "--chdir", "--host", "--prompt", "--role", "--type", "--other-user", "--command-timeout"}),
    "doas": frozenset({"-u", "-C"}),
    "nice": frozenset({"-n", "--adjustment"}),
    "timeout": frozenset({"-s", "-k", "--signal", "--kill-after"}),
    "env": frozenset({"-u", "-C", "-S", "--unset", "--chdir", "--split-string"}),
    "exec": frozenset({"-a"}),
    "stdbuf": frozenset({"-i", "-o", "-e"}),
}
_INTERPRETERS = frozenset({"bash", "sh", "zsh", "dash", "python", "python3", "node", "ruby", "perl", "bun", "deno",
                           "tsx", "ts-node"})
# Interpreter options that take a separate value (``python3 -W ignore x.py``).
_INTERPRETER_VALUE_OPTS = frozenset({"-W", "-X", "--require", "-r", "--import", "-O0"})
_SHELL_VALUE_OPTS = frozenset({"-o", "+o", "-O", "+O"})  # ``bash -o pipefail -c ...``
_SHELLS = frozenset({"bash", "sh", "zsh", "dash"})
_VAR_ASSIGN = re.compile(r"^[A-Za-z_]\w*=")
_MAX_SHELL_DEPTH = 3
# A token made only of these separates commands: `;`, `&&`, `||`, `|`, `&`, a subshell, a substitution.
_PUNCTUATION = ";&|()`\n"
# Shell words before a command (`if x; then npm ci; fi`, `{ npm ci; }`).
_RESERVED = frozenset({"if", "then", "elif", "else", "while", "until", "do", "!", "{"})
# Install subcommands per package manager; ``None`` is a bare ``yarn``.
_INSTALL_COMMANDS = {
    "npm": frozenset({"install", "i", "in", "ins", "inst", "insta", "instal", "isnt", "isnta", "isntal", "isntall",
                      "ci", "clean-install", "ic", "install-clean", "isntall-clean", "install-ci-test", "cit", "it",
                      "install-test", "add", "update", "up", "upgrade", "udpate", "dedupe", "ddp"}),
    "yarn": frozenset({None, "install", "add", "upgrade", "up", "upgrade-interactive", "dedupe"}),
    "pnpm": frozenset({"install", "i", "add", "update", "up", "upgrade", "dedupe", "rebuild", "rb"}),
    "bun": frozenset({"install", "i", "add", "update"}),
}
# Package-manager options that take a separate value (``npm --prefix web ci``).
_PM_VALUE_OPTS = frozenset({"--prefix", "-C", "--dir", "--cwd", "--filter", "-F", "--workspace", "--registry",
                            "--cache", "--userconfig", "--loglevel"})
_INFO_OPTS = frozenset({"-v", "--version", "-h", "--help"})
# Package-manager options known to take no value. Any other ``--opt`` without ``=`` followed by a word
# that is not a known subcommand is read as taking that word as its value (``npm --fetch-retries 3 ci``).
_PM_FLAGS = frozenset({"--silent", "--verbose", "--quiet", "--offline", "--prefer-offline", "--frozen-lockfile",
                       "--immutable", "--ignore-scripts", "--production", "--prod", "--dev", "--force",
                       "--legacy-peer-deps", "--json", "--recursive", "--global", "--save", "--save-dev",
                       "--save-exact", "--yes", "--pure-lockfile", "--strict-peer-deps", "--if-present", "--workspaces",
                       "--include-workspace-root", "--non-interactive", "--color", "--progress"})
_PM_SUBCOMMANDS = frozenset(w for c in _INSTALL_COMMANDS.values() for w in c if w) | _PM_BUILTINS | _NPM_SCRIPT_COMMANDS
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


def _uncommented(text: str) -> str:
    """``text`` without comments, in linear time.

    A ``#`` that is unquoted, unescaped and at the start of a word runs to the end of its line.
    Quotes are tracked across lines, so a multi-line ``bash -c '…'`` keeps its body.
    """
    if "#" not in text:
        return text
    out = []
    quote = ""
    k = 0
    n = len(text)
    while k < n:
        ch = text[k]
        if ch == "\\" and quote != "'":
            out.append(text[k:k + 2])
            k += 2
            continue
        if quote:
            quote = "" if ch == quote else quote
        elif ch in "'\"":
            quote = ch
        elif ch == "#" and (k == 0 or text[k - 1] in " \t\n;&|()"):
            end = text.find("\n", k)
            k = n if end < 0 else end
            continue
        out.append(ch)
        k += 1
    return "".join(out)


def _double_quoted_substitutions(text: str) -> list[str]:
    """Bodies of ``$(…)`` and backtick spans inside double quotes, which the tokenizer keeps in one word.

    Unquoted ones are split by the tokenizer itself; single-quoted ones are literal text.
    """
    out = []
    quote = ""
    k = 0
    n = len(text)
    while k < n:
        ch = text[k]
        if ch == "\\" and quote != "'":
            k += 2
            continue
        if quote == '"' and ch == "`":
            end = text.find("`", k + 1)
            if end < 0:
                break
            out.append(text[k + 1:end])
            k = end + 1
            continue
        if quote == '"' and text.startswith("$(", k):
            depth, j = 1, k + 2
            while j < n and depth:
                depth += {"(": 1, ")": -1}.get(text[j], 0)
                j += 1
            if depth:
                break
            out.append(text[k + 2:j - 1])
            k = j
            continue
        if quote:
            quote = "" if ch == quote else quote
        elif ch in "'\"":
            quote = ch
        k += 1
    return out


# A heredoc start word (``<<EOF``, ``<<-'EOF'``); its body is data, not commands.
_HEREDOC = re.compile(r"^<<-?['\"]?([A-Za-z_][\w.-]*)['\"]?$")
_MAX_SHLEX_LINE = 4096  # shlex is slow on a long unbalanced quote


def _tokens(text: str) -> list[str]:
    """Words and separators of a comment-free command; a newline is a separator token."""
    if all(len(line) <= _MAX_SHLEX_LINE for line in text.split("\n")):
        lexer = shlex.shlex(text, posix=True, punctuation_chars=_PUNCTUATION)
        lexer.whitespace = " \t\r"
        lexer.whitespace_split = True
        lexer.commenters = ""  # comments are already gone; shlex would cut ``https://r/#x``
        try:
            return list(lexer)
        except ValueError:
            pass
    return re.sub(r"(&&|\|\||[;|&()`\n])", r" \1 ", text).replace("\t", " ").split(" ")


def _segments(command: str, depth: int = 0) -> list[list[str]]:
    """The simple commands of a hook command line, as argv lists.

    Quotes and comments are honoured across lines, so ``echo 'a; npm ci'``, ``npm ci # --ignore-scripts``
    and a quoted string spanning lines are one command each; ``;``, ``&&``, ``||``, ``|``, ``&`` and a
    newline separate commands. A heredoc body is skipped. ``$(…)`` inside double quotes, ``eval`` and
    an ``echo``/``printf`` piped into a bare shell are read as the commands they run.
    """
    text = _uncommented(command.replace("\\\n", " "))
    out: list[list[str]] = []
    seps: list[str] = []  # the separator before each segment
    current: list[str] = []
    sep = ""
    heredocs: list[str] = []
    in_heredoc: str | None = None
    line: list[str] = []
    for token in _tokens(text):
        if in_heredoc is not None:
            if token == "\n":
                if line == [in_heredoc]:
                    in_heredoc = heredocs.pop(0) if heredocs else None
                line = []
            elif token:
                line.append(token)
            continue
        if token == "&" and current and current[-1].endswith((">", "<")):  # 2>&1
            current[-1] += token
        elif current and current[-1].endswith((">&", "<&")) and (token.isdigit() or token == "-"):
            current[-1] += token  # the fd after 2>& stays in the same word
        elif token and not token.strip(_PUNCTUATION):
            if current:
                out.append(current)
                seps.append(sep)
            current = []
            sep = token
            if token == "\n" and heredocs:
                in_heredoc = heredocs.pop(0)
                line = []
        elif token:
            heredoc = _HEREDOC.match(token)
            if heredoc:
                heredocs.append(heredoc.group(1))
            elif current and current[-1] in ("<<", "<<-") and token.isidentifier():
                heredocs.append(token)
            current.append(token)
    if current:
        out.append(current)
        seps.append(sep)
    if depth >= _MAX_SHELL_DEPTH:
        return out
    extra: list[list[str]] = []
    for body in _double_quoted_substitutions(text):
        extra += _segments(body, depth + 1)
    # Here-strings and heredoc bodies are commands only when a shell reads them as its stdin.
    shell_starts: dict[str, list[bool]] = {}
    for k, argv in enumerate(out):
        _, i = _command_start(argv)
        is_shell = i < len(argv) and argv[i].rsplit("/", 1)[-1] in _SHELLS and _reads_stdin(argv, i)
        if is_shell:
            for here_string in _here_strings(argv[i:]):
                extra += _segments(here_string, depth + 1)
        # A shell piped after this command reads its heredoc (`cat <<EOF | sh`)
        piped_to_shell = False
        if k + 1 < len(out) and seps[k + 1] == "|":
            _, next_i = _command_start(out[k + 1])
            piped_to_shell = (next_i < len(out[k + 1]) and out[k + 1][next_i].rsplit("/", 1)[-1] in _SHELLS
                              and _reads_stdin(out[k + 1], next_i))
        for label in _heredoc_labels(argv):
            shell_starts.setdefault(label, []).append(is_shell or piped_to_shell)
    for body in _heredoc_bodies(text, shell_starts):
        extra += _segments(body, depth + 1)
    for k, argv in enumerate(out):
        _, i = _command_start(argv)
        if i >= len(argv):
            continue
        if argv[i] == "eval":
            extra += _segments(" ".join(argv[i + 1:]), depth + 1)
        elif k and seps[k] == "|" and argv[i].rsplit("/", 1)[-1] in _SHELLS:
            prev = out[k - 1]
            _, p = _command_start(prev)
            if p < len(prev) and prev[p] in ("echo", "printf") and all(w.startswith("-") and "c" not in w for w in argv[i + 1:]):
                extra += _segments(" ".join(w for w in prev[p + 1:] if not w.startswith("-")), depth + 1)
        # When the command is a shell that takes a heredoc as input, parse the body
        elif argv[i].rsplit("/", 1)[-1] in _SHELLS and any(w.startswith(("<<", "<<-")) for w in argv[i:]):
            # Heredoc bodies are already extracted above
            pass
    return out + extra


def _reads_stdin(argv: list[str], i: int) -> bool:
    """Whether the shell at ``argv[i]`` reads its script from stdin, so a heredoc or here-string feeds it.

    ``bash -c '…'`` runs its own script and ignores stdin.
    """
    return not any(w[:1] == "-" and w[1:2] != "-" and "c" in w for w in argv[i + 1:])


def _here_strings(argv: list[str]) -> list[str]:
    """The word of each here-string on a command: ``<<<word`` or ``<<< word``, quotes already removed."""
    out = []
    for j, word in enumerate(argv):
        if not word.startswith("<<<"):
            continue
        text = word[3:] if word != "<<<" else (argv[j + 1] if j + 1 < len(argv) else "")
        if text:
            out.append(text)
    return out


def _heredoc_labels(argv: list[str]) -> list[str]:
    """The terminator labels of the heredocs a command opens: ``<<EOF``, ``<<-EOF`` or ``<< EOF``."""
    labels = []
    for j, word in enumerate(argv):
        if word in ("<<", "<<-") and j + 1 < len(argv):
            labels.append(argv[j + 1])
        else:
            m = _HEREDOC.match(word)
            if m:
                labels.append(m.group(1))
    return labels


# A heredoc start in the text, ``<<EOF``, ``<< 'EOF'`` or ``<<-EOF``; ``<<<`` is a here-string.
_HEREDOC_START = re.compile(r"(?<!<)<<-?[ \t]*['\"]?([A-Za-z_][\w.-]*)['\"]?(?![\w.-])")


def _heredoc_bodies(text: str, shell_starts: dict[str, list[bool]]) -> list[str]:
    """The bodies of the heredocs a shell reads, in the order the shell reads them.

    ``shell_starts[label]`` says, for each start of that label in text order, whether a shell is fed
    by it. Starts on one line take their bodies in turn; a start inside an earlier body is data. An
    unclosed heredoc runs to the end of the text, so reading stops there. One pass, so linear.
    """
    if not shell_starts:
        return []
    lines = text.split("\n")
    line_starts: list[int] = []
    offset = 0
    for line in lines:
        line_starts.append(offset)
        offset += len(line) + 1
    starts = [(bisect.bisect_right(line_starts, m.start()) - 1, m.group(1))
              for m in _HEREDOC_START.finditer(text)]
    terminators: dict[str, list[int]] = {}
    wanted = {label for _, label in starts}
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped in wanted:
            terminators.setdefault(stripped, []).append(idx)
    out = []
    ordinal: dict[str, int] = {}
    last_line = next_free = -1
    for line_idx, label in starts:
        if last_line < line_idx <= next_free:
            continue
        n = ordinal.get(label, 0)
        ordinal[label] = n + 1
        ends = terminators.get(label, [])
        begin = max(line_idx, next_free) + 1
        k = bisect.bisect_left(ends, begin)
        if k == len(ends):
            break
        flags = shell_starts.get(label, [])
        if n < len(flags) and flags[n]:
            body = "\n".join(lines[begin:ends[k]])
            if body.strip():
                out.append(body)
        last_line, next_free = line_idx, ends[k]
    return out


def _command_start(argv: list[str]) -> tuple[dict[str, str], int]:
    """``VAR=value`` assignments before the command word, and the command word's index.

    Wrappers such as ``sudo``, ``timeout N``, ``env`` and ``source`` are skipped too.
    For ``command``, ``-v``/``-V`` is a lookup and no actual command follows it.
    Only match wrappers by basename for absolute paths, not relative paths like ``./scripts/env``.
    """
    env: dict[str, str] = {}
    i = 0
    while i < len(argv):
        word = argv[i]
        if word in _RESERVED:
            i += 1
        elif _VAR_ASSIGN.match(word):
            name, _, value = word.partition("=")
            env[name.lower()] = value
            i += 1
        elif word.startswith(("./", "$CLAUDE_PROJECT_DIR")):
            # A relative path is code, not a wrapper
            break
        elif word.rsplit("/", 1)[-1] in _WRAPPERS and ("/" not in word or word.startswith("/")):
            # Match wrappers by basename only for absolute paths or bare names, not relative paths
            name = word.rsplit("/", 1)[-1]
            # ``command -v`` / ``command -V`` is a lookup, not command execution
            if name == "command" and i + 1 < len(argv) and argv[i + 1] in ("-v", "-V"):
                return env, len(argv)  # no command word to find
            value_opts = _WRAPPER_VALUE_OPTS.get(name, frozenset())
            i += 1
            while i < len(argv) and argv[i].startswith("-") and argv[i] != "-":
                i += 2 if argv[i] in value_opts else 1
            i += _WRAPPERS[name]
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
        elif (word.startswith("--") and "=" not in word and word not in _PM_FLAGS and not word.startswith("--no-")
              and j + 1 < len(rest) and not rest[j + 1].startswith("-") and rest[j + 1] not in _PM_SUBCOMMANDS):
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
    return word.startswith(("./", "$CLAUDE_PROJECT_DIR", "${CLAUDE_PROJECT_DIR")) or word in tracked


def _executes_repository_code(argv: list[str], tracked: set[str], npmrc_ignores: bool, depth: int = 0) -> bool:
    return _installs_with_scripts(argv, npmrc_ignores) or _runs_repository_code(argv, tracked, npmrc_ignores, depth)


def _runs_repository_code(argv: list[str], tracked: set[str], npmrc_ignores: bool, depth: int = 0) -> bool:
    """Whether a hook command runs a script, makefile or file from the checked-out tree."""
    _, i = _command_start(argv)
    if i >= len(argv):
        return False
    cmd, rest = argv[i], argv[i + 1:]
    # A path that starts with ./ or $CLAUDE_PROJECT_DIR, or is a tracked file, is repository code before it is a wrapper
    if _in_tree(cmd, tracked):
        return True
    name = cmd.rsplit("/", 1)[-1]
    # Only match wrappers by basename for absolute paths (``/usr/bin/env``) or common interpreters
    # For relative paths or repository-named scripts, check if it's a tracked file or path first
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
            # ``-c`` alone or among short options (``bash -lc``, ``sh -ec``)
            if name in _SHELLS and word[:1] == "-" and word[1:2] != "-" and "c" in word:
                return j + 1 < len(rest) and depth < _MAX_SHELL_DEPTH and any(
                    _executes_repository_code(part, tracked, npmrc_ignores, depth + 1)
                    for part in _segments(rest[j + 1]))
            if word in _INTERPRETER_VALUE_OPTS or (name in _SHELLS and word in _SHELL_VALUE_OPTS):
                j += 2
            elif word.startswith("-"):
                j += 1
            else:
                return _in_tree(word, tracked)
        return False
    return False


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
                    if _executes_repository_code(argv, tracked_files, npmrc_ignores):
                        out.add(HookRisk(path, event, shlex.join(argv)))
    return sorted(out, key=lambda r: (r.path, r.event, r.command))
