# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Coding-agent CLIs as LLM providers: a completion through a locally logged-in CLI.

A provider in :data:`BACKENDS` (``claude-cli``, ``codex-cli``, ``copilot-cli``) runs one prompt through
the CLI the user is already signed in to (a Claude subscription, ``claude setup-token``, a ``codex login``,
a ``copilot login``) instead of an API key. :mod:`dokimi.llm`
routes a :class:`~dokimi.llm.Request` for such a provider here, so retry, the
response cache and usage totals work as for any provider.

What differs from an API provider, and what a caller can rely on:

- **No sampling control.** The CLI takes no temperature, seed or output ceiling.
  :data:`CliBackend.supports` lists none of them, so
  :func:`dokimi.llm.unsupported_sampling_params` names a requested ``temperature``
  or ``seed`` and a run refuses before it starts. ``max_tokens`` is dropped: every
  caller sets one, and refusing it would refuse every run.
- **Hidden context.** Even with no tools, no settings and its own system prompt, the
  CLI adds a fixed preamble (about 0.5k-1.3k tokens, by version) and states the
  working directory, git status, platform and date. The child runs in one fixed
  empty directory per backend (:func:`work_dir`), so that text is the same for
  every call of a run, with and without docs. Do not pool its scores with the same
  model through an API provider.
- **Isolation.** ``--tools "" --setting-sources "" --strict-mcp-config
  --no-session-persistence``: no tools, no user or project settings (so no
  ``CLAUDE.md``), no MCP servers, no session written. Never ``--bare``: it reads only
  ``ANTHROPIC_API_KEY``, so a subscription login fails with "Authentication error".
  The parent's environment is passed on except :data:`CLAUDE_ENV_DROP`; without
  that, a child started inside a Claude Code session joins the parent's session.
- **Usage.** Tokens and the resolved model id come from the CLI's JSON.
  ``cost_usd`` is ``None``: ``total_cost_usd`` is a list-price estimate, not what a
  subscription is charged; it is kept as :attr:`CliResult.list_cost_usd`.
- **Errors.** A failed call raises :class:`CliCallError` with ``retryable`` set:
  rate limits, overload, 5xx and timeouts are retried; an unknown model, a missing
  login or a missing binary are not. Those, and a usage limit, also set ``run_wide``.

:func:`detect` reports whether a backend's binary is installed and signed in
without calling a model (``cli.py backends``).
"""

from __future__ import annotations

import functools
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from dokimi import process
from dokimi.metrics.usage import UsageRecord

#: Variables a Claude Code session sets for its own children, plus the API key. Dropped, so
#: a call made from inside a session is a session of its own and runs on the CLI's login.
CLAUDE_ENV_DROP = frozenset({
    "CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SSE_PORT",
    "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_PID",
    "CLAUDE_EFFORT", "CLAUDE_CODE_SESSION_ATTENDED",
    # Set, they win over the CLI's login: the run would bill the key or token, not the subscription.
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
})
#: Dropped by prefix, so a variable not yet listed (an endpoint, a model alias, a provider switch
#: such as CLAUDE_CODE_USE_BEDROCK) cannot change who is billed or which session the child joins.
CLAUDE_ENV_DROP_PREFIXES = ("ANTHROPIC_", "CLAUDE_", "CLAUDECODE")
#: Kept despite a prefix: the login's location, the ``claude setup-token`` token, and Git Bash
#: for Claude Code on Windows (without it ``claude -p`` cannot start there).
CLAUDE_ENV_KEEP = frozenset({"CLAUDE_CONFIG_DIR", "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_GIT_BASH_PATH"})

#: The flags that strip the CLI to a bare completion; see the module docstring.
CLAUDE_ISOLATION = ("--tools", "", "--setting-sources", "", "--strict-mcp-config",
                    "--no-session-persistence")

#: ``api_error_status`` values worth another attempt.
_RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504, 529})
_RETRYABLE_WORDS = ("rate limit", "rate_limit", "overloaded", "timed out")
#: A status the CLI prints as text, with no ``api_error_status``: ``API Error: 503 ...``. A status word has to
#: sit before the number, so a 500 inside a message about tokens does not count.
_STATUS_TEXT = re.compile(r"\b(?:status|code|http|error)\W{0,3}(\d{3})\b")


def _text_retryable(lowered: str) -> bool:
    """Whether a failure with no structured status is worth another attempt. The first status the text
    names decides alone: ``API Error: 400 ...; error code 500`` and a 400 naming ``rate_limit`` are 400s."""
    found = _STATUS_TEXT.search(lowered)
    if found is None:
        return any(w in lowered for w in _RETRYABLE_WORDS)
    return int(found.group(1)) in _RETRYABLE_STATUS

DEFAULT_TIMEOUT_SEC = 600.0


class CliCallError(RuntimeError):
    """A CLI call that produced no reply; ``retryable`` is what :func:`dokimi.llm.is_retryable` reads.

    ``run_wide`` marks a failure every later call would meet too (no login, a usage limit, an unknown
    model, no binary), which :func:`dokimi.novelty.routing_stop.is_run_wide` reads.
    """

    def __init__(self, message: str, *, retryable: bool, run_wide: bool = False):
        super().__init__(message)
        self.retryable = retryable
        self.run_wide = run_wide


@dataclass(frozen=True)
class CliResult:
    """One CLI reply: the text, its usage, and the model id the CLI resolved."""

    text: str
    usage: UsageRecord
    model_id: Optional[str] = None
    list_cost_usd: Optional[float] = None
    finish_reason: Optional[str] = None


@dataclass(frozen=True)
class AuthStatus:
    """What :func:`detect` found. ``authenticated`` is ``None`` when the CLI cannot tell."""

    backend: str
    binary: str
    installed: bool
    version: str = ""
    authenticated: Optional[bool] = None
    method: str = ""
    source: str = ""  # "settings file" for a route the Claude Code settings file sets (Bedrock, Vertex)
    hint: str = ""


class SamplingError(ValueError):
    """A temperature or seed given to a CLI provider, which takes neither; ``cli.main`` reports it (exit 2)."""


@dataclass(frozen=True)
class CliBackend:
    """One CLI: how to call it, read its reply, and ask it whether it is signed in."""

    name: str
    binary: str
    build_argv: Callable[[str, str, str], list]  # (model, system-prompt file, prompt text) -> argv
    parse: Callable[[process.Completed, str, str], CliResult]  # (completed, model, provider)
    auth_argv: Tuple[str, ...]
    parse_auth: Callable[[process.Completed], Tuple[Optional[bool], str]]  # -> (authenticated, method)
    env_drop: frozenset = frozenset()
    env_drop_prefixes: Tuple[str, ...] = ()
    env_keep: frozenset = frozenset()
    supports: frozenset = frozenset()  # of "temperature", "seed", "max_tokens"
    login_hint: str = ""
    aliases: Tuple[str, ...] = ()
    model_prefixes: Tuple[str, ...] = ()  # full model ids the CLI takes, by prefix
    any_model: bool = False  # the CLI's own catalogue decides: any model name is passed on
    prompt_on_stdin: bool = True  # False: the prompt is in the argv (Copilot reads no prompt from stdin)
    system_in_prompt: bool = False  # True: no system-prompt option, so the system text leads the prompt


# ── claude-cli ────────────────────────────────────────────────────────────────


#: The system prompt of a call that gives none: never the CLI's own agent prompt (~37k tokens with tools).
DEFAULT_SYSTEM = "You are a helpful assistant."


def _claude_argv(model: str, system_file: str) -> list:
    argv = ["claude", "-p", "--input-format", "text", "--output-format", "json",
            "--model", model, *CLAUDE_ISOLATION]
    # A file, not an argument: one argument is limited to 128 KiB on Linux, and a system prompt may be longer.
    return argv + ["--system-prompt-file", system_file]


def refuse_chat_completion(provider: str) -> None:
    """The compatibility entry point returns a response object a CLI reply does not have."""
    if provider in BACKENDS:
        raise CliCallError(f"chat_completion does not take {provider}: use LLM.chat or LLM.complete",
                           retryable=False)


def _claude_error(payload: Dict[str, Any], done: process.Completed) -> CliCallError:
    message = str(payload.get("result") or done.stderr.strip() or f"exit {done.returncode}")[:500]
    status = payload.get("api_error_status")
    lowered = message.lower()
    if any(w in lowered for w in ("authentication", "not logged in", "/login")):
        return CliCallError(f"claude-cli: {message} (sign in: claude setup-token, or claude /login)",
                            retryable=False, run_wide=True)
    if "usage limit" in lowered:  # a subscription cap: it resets in hours, a retry cannot help
        return CliCallError(f"claude-cli: {message}", retryable=False, run_wide=True)
    if status is None:  # no structured status: the words, and a status the CLI printed as text
        retryable = _text_retryable(lowered)
    else:  # a structured status decides: a 400 that quotes "rate_limit" is still a 400
        retryable = status in _RETRYABLE_STATUS
    return CliCallError(f"claude-cli: {message}", retryable=retryable, run_wide=status == 404)


def _main_model(model_usage: Dict[str, Any], requested: str) -> Optional[str]:
    """The model the CLI answered with: the requested family if listed, else the one with most output.

    ``modelUsage`` also lists the helper models a call used (a title or a classifier), so
    its first key is not always the answering model.
    """
    names = [n for n in model_usage if isinstance(n, str)]
    if not names:
        return None
    wanted = family(requested)
    for name in names:
        if wanted is not None and family(name) == wanted:
            return name
    def output(name: str) -> int:
        stats = model_usage.get(name)
        return int(stats.get("outputTokens") or 0) if isinstance(stats, dict) else 0
    return max(names, key=output)


def _claude_parse(done: process.Completed, model: str, provider: str) -> CliResult:
    if done.timed_out:
        raise CliCallError(f"claude-cli: no reply in {done.elapsed_sec:.0f}s", retryable=True)
    try:
        payload = json.loads(done.stdout)
    except ValueError:
        payload = None
    if not isinstance(payload, dict):
        text = (done.stderr or done.stdout).strip()
        # No JSON, so no status field: the transient words and the status printed as text are checked.
        # A subscription cap is not transient, as on the JSON path, whatever status it prints.
        lowered = text.lower()
        capped = "usage limit" in lowered
        raise CliCallError(f"claude-cli: exit {done.returncode}, no JSON reply: {text[:300]}",
                           retryable=not capped and _text_retryable(lowered), run_wide=capped)
    if payload.get("is_error") or done.returncode != 0:
        raise _claude_error(payload, done)
    u = payload.get("usage") or {}
    cache_write = int(u.get("cache_creation_input_tokens") or 0)
    cache_read = int(u.get("cache_read_input_tokens") or 0)
    prompt = int(u.get("input_tokens") or 0) + cache_write + cache_read
    completion = int(u.get("output_tokens") or 0)
    model_usage = payload.get("modelUsage") or {}
    list_cost = payload.get("total_cost_usd")
    resolved = _main_model(model_usage, model)
    usage = UsageRecord(
        prompt_tokens=prompt, completion_tokens=completion, total_tokens=prompt + completion,
        cache_read_tokens=cache_read, cache_write_tokens=cache_write,
        cost_usd=None, latency_sec=done.elapsed_sec, model=model, provider=provider,
        n_calls=1, cost_known_calls=0, resolved_model=resolved or "",
    )
    return CliResult(
        text=str(payload.get("result") or ""),
        usage=usage,
        model_id=resolved,
        list_cost_usd=float(list_cost) if isinstance(list_cost, (int, float)) else None,
        finish_reason="length" if payload.get("stop_reason") == "max_tokens" else "stop",
    )


def _claude_auth(done: process.Completed) -> Tuple[Optional[bool], str]:
    try:
        status = json.loads(done.stdout)
    except ValueError:
        return None, ""
    if not isinstance(status, dict):
        return None, ""
    method = str(status.get("authMethod") or "")
    if status.get("apiProvider"):
        method = f"{method} ({status['apiProvider']})" if method else str(status["apiProvider"])
    return bool(status.get("loggedIn")), method


CLAUDE_CLI = CliBackend(
    name="claude-cli",
    binary="claude",
    build_argv=lambda model, system_file, _text: _claude_argv(model, system_file),
    parse=_claude_parse,
    # The settings policy of a call: a route set only in settings.json is not a login the call can use.
    auth_argv=("claude", "--setting-sources", "", "auth", "status"),
    parse_auth=_claude_auth,
    env_drop=CLAUDE_ENV_DROP,
    env_drop_prefixes=CLAUDE_ENV_DROP_PREFIXES,
    env_keep=CLAUDE_ENV_KEEP,
    login_hint="claude setup-token (headless) or claude, then /login",
    aliases=("haiku", "sonnet", "opus"),
    model_prefixes=("claude-",),
)

# ── codex-cli ─────────────────────────────────────────────────────────────────


def _codex_argv(model: str, system_file: str) -> list:
    """``codex exec`` with the prompt on stdin: a read-only sandbox, no user config (``config.toml``), no rules,
    no ``AGENTS.md`` (``project_doc_max_bytes=0``), no session written; the system file replaces the base
    instructions. ``CODEX_HOME`` stays, because the login lives there."""
    return ["codex", "exec", "--json", "--ephemeral", "--ignore-user-config", "--ignore-rules",
            "--skip-git-repo-check", "--sandbox", "read-only", "--color", "never", "--model", model,
            "-c", f"model_instructions_file={json.dumps(system_file)}", "-c", "project_doc_max_bytes=0", "-"]


def _codex_events(stdout: str) -> list:
    """The JSON objects of a ``codex exec --json`` stream; a line that is not one is skipped."""
    events = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _codex_error(done: process.Completed, failed: str) -> CliCallError:
    message = (failed or done.stderr.strip() or done.stdout.strip() or f"exit {done.returncode}")[:500]
    lowered = message.lower()
    # A cap, a missing login or an unknown model is the same for every later call: run-wide, never retried.
    run_wide = any(w in lowered for w in ("usage_limit", "usage limit", "not logged in", "login", "unknown model",
                                          "model not found"))
    return CliCallError(f"codex-cli: {message}", retryable=not run_wide and _text_retryable(lowered),
                        run_wide=run_wide)


def _codex_parse(done: process.Completed, model: str, provider: str) -> CliResult:
    if done.timed_out:
        raise CliCallError(f"codex-cli: no reply in {done.elapsed_sec:.0f}s", retryable=True)
    text: Optional[str] = None
    usage: Dict[str, Any] = {}
    failed = ""
    for event in _codex_events(done.stdout):
        item = event.get("item") if isinstance(event.get("item"), dict) else {}
        if event.get("type") == "item.completed" and item.get("type") == "agent_message":
            text = str(item.get("text") or "")
        elif event.get("type") == "turn.completed" and isinstance(event.get("usage"), dict):
            usage = event["usage"]
        elif event.get("type") == "turn.failed":
            error = event.get("error")
            failed = str(error.get("message") if isinstance(error, dict) else error or "turn failed")
    if not failed and done.returncode == 0 and not (text or "").strip():
        failed = "no reply text"  # an empty, blank or null agent message is a failed call, not an answer
    if failed or done.returncode != 0 or text is None:
        raise _codex_error(done, failed)
    prompt = int(usage.get("input_tokens") or 0)  # includes the cached part, which cached_input_tokens counts again
    completion = int(usage.get("output_tokens") or 0)
    record = UsageRecord(
        prompt_tokens=prompt, completion_tokens=completion, total_tokens=prompt + completion,
        cache_read_tokens=int(usage.get("cached_input_tokens") or 0), cache_write_tokens=0,
        cost_usd=None, latency_sec=done.elapsed_sec, model=model, provider=provider,
        n_calls=1, cost_known_calls=0, resolved_model=model,
    )
    return CliResult(text=text, usage=record, model_id=model, finish_reason="stop")


def _codex_auth(done: process.Completed) -> Tuple[Optional[bool], str]:
    lines = (done.stdout or done.stderr).strip().splitlines()
    return done.returncode == 0, lines[0].strip() if lines else ""


CODEX_CLI = CliBackend(
    name="codex-cli",
    binary="codex",
    build_argv=lambda model, system_file, _text: _codex_argv(model, system_file),
    parse=_codex_parse,
    auth_argv=("codex", "login", "status"),
    parse_auth=_codex_auth,
    # A key or endpoint set in the environment wins over the login: the run would bill that, not the login.
    env_drop=frozenset({"OPENAI_API_KEY", "OPENAI_BASE_URL"}),
    env_drop_prefixes=("CODEX_",),  # a Codex session's own variables; CODEX_HOME (the login) is kept
    env_keep=frozenset({"CODEX_HOME"}),
    login_hint="codex login",
    any_model=True,
)

# ── copilot-cli ───────────────────────────────────────────────────────────────

#: Copilot takes its prompt as the ``-p`` argument, which one Linux argument caps at 128 KiB; a margin is kept.
COPILOT_ARG_LIMIT = 100_000
WINDOWS_CMDLINE_LIMIT = 32_767  # CreateProcessW's cap on the whole command line, in UTF-16 units


def _is_windows() -> bool:
    return sys.platform == "win32"


def _copilot_argv(model: str, system_file: str, text: str) -> list:
    """``copilot -p`` with no tools, no custom instructions (AGENTS.md and the like), no MCP servers, no
    remote session, no auto-update, no temp dir. The system file is not used: Copilot has no system-prompt
    option, so the system text leads the prompt (``system_in_prompt``)."""
    del system_file
    if len(text.encode("utf-8")) > COPILOT_ARG_LIMIT:
        raise CliCallError(f"copilot-cli: the prompt is over {COPILOT_ARG_LIMIT} bytes, the most one argument "
                           "carries", retryable=False)
    # --allow-all-tools is what non-interactive mode requires; "__none__" is the measured no-tool allow-list
    # (an empty list leaves every tool on, see repos/profiles/copilot.yaml).
    # The prompt is bound to --prompt= so a leading "-" (a bullet-list system text) is not read as an option.
    argv = ["copilot", f"--prompt={text}", "-s", "--model", model, "--allow-all-tools", "--no-custom-instructions",
            "--disable-builtin-mcps", "--no-remote", "--no-auto-update", "--no-ask-user", "--disallow-temp-dir",
            "--no-color", "--available-tools", "__none__"]
    if _is_windows():
        units = len(subprocess.list2cmdline(argv).encode("utf-16-le")) // 2 + 1  # the terminating NUL counts
        if units > WINDOWS_CMDLINE_LIMIT:
            raise CliCallError(f"copilot-cli: the command line is {units} characters, over Windows' "
                               f"{WINDOWS_CMDLINE_LIMIT}; shorten the prompt or use a stdin-capable provider",
                               retryable=False, run_wide=False)
    return argv


def _copilot_parse(done: process.Completed, model: str, provider: str) -> CliResult:
    if done.timed_out:
        raise CliCallError(f"copilot-cli: no reply in {done.elapsed_sec:.0f}s", retryable=True)
    text = done.stdout.strip()
    if done.returncode != 0 or not text:
        message = (done.stderr.strip() or text or f"exit {done.returncode}")[:500]
        lowered = message.lower()
        run_wide = any(w in lowered for w in ("not logged in", "login", "authenticat", "unknown model",
                                              "usage limit"))
        raise CliCallError(f"copilot-cli: {message}", retryable=not run_wide and _text_retryable(lowered),
                           run_wide=run_wide)
    # ``-s`` prints the reply alone; the token counts are not on that output, so the record carries none.
    record = UsageRecord(
        prompt_tokens=0, completion_tokens=0, total_tokens=0, cache_read_tokens=0, cache_write_tokens=0,
        cost_usd=None, latency_sec=done.elapsed_sec, model=model, provider=provider,
        n_calls=1, cost_known_calls=0, resolved_model=model,
    )
    return CliResult(text=text, usage=record, model_id=model, finish_reason="stop")


def _copilot_auth(done: process.Completed) -> Tuple[Optional[bool], str]:
    """Copilot has no status command: a version line says the binary runs, not that it is signed in."""
    return None, ""


COPILOT_CLI = CliBackend(
    name="copilot-cli",
    binary="copilot",
    build_argv=_copilot_argv,
    parse=_copilot_parse,
    auth_argv=("copilot", "--no-auto-update", "--version"),
    parse_auth=_copilot_auth,
    # A token or BYOK endpoint in the environment wins over the login (as ANTHROPIC_API_KEY does for claude-cli).
    env_drop=frozenset({"COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN", "COPILOT_ALLOW_ALL"}),
    env_drop_prefixes=("COPILOT_PROVIDER_",),
    login_hint="copilot login",
    any_model=True,
    prompt_on_stdin=False,
    system_in_prompt=True,
)

#: CLI backends by provider name.
BACKENDS: Dict[str, CliBackend] = {b.name: b for b in (CLAUDE_CLI, CODEX_CLI, COPILOT_CLI)}


def backend(provider: str) -> Optional[CliBackend]:
    """The CLI backend for *provider*, or ``None`` for an API provider."""
    return BACKENDS.get(provider)


#: The 1M-context suffix ``claude --model`` takes after an alias or a full id: ``sonnet[1m]``.
CONTEXT_SUFFIX = re.compile(r"\[1m\]$")


def _without_context(model: str) -> str:
    """*model* with a trailing ``[1m]`` (the 1M-context form) removed, and blanks stripped."""
    return CONTEXT_SUFFIX.sub("", model.strip())


def accepts_model(provider: str, model: str) -> bool:
    """Whether *provider*'s CLI takes *model*: one of its aliases, or a full id it names (either may
    carry a ``[1m]``-style context suffix)."""
    cli = BACKENDS[provider]
    bare = _without_context(model)
    if cli.any_model:
        return bool(bare)
    return bare in cli.aliases or bare.startswith(cli.model_prefixes)


#: The Bedrock revision ending a pinned id (``-v1:0``), only after a version digit: a version inside
#: the name (``claude-3-5-sonnet-20240620``) is the model, and ``claude-v2:1`` is Claude 2 itself.
_BEDROCK_REVISION = re.compile(r"(?<=\d)-v\d+:\d+$")


def _full_id(model: str) -> str:
    """A model ref as its bare name: lower case, no ``[1m]`` suffix, no routing prefix
    (``anthropic/claude-…`` through a gateway, ``anthropic.claude-…`` in a Bedrock id), no
    Bedrock revision suffix (``-v1:0``)."""
    name = _without_context(model.lower().rsplit("/", 1)[-1].rsplit("anthropic.", 1)[-1])
    return _BEDROCK_REVISION.sub("", name)


def is_alias(model: object) -> bool:
    """Whether *model* is a CLI alias itself (``sonnet``, ``opus[1m]``), not a full id."""
    return isinstance(model, str) and _full_id(model) in CLAUDE_CLI.aliases


def family(model: str) -> Optional[str]:
    """The alias a Claude model name belongs to: ``sonnet`` for the alias or for ``claude-sonnet-5-5``."""
    name = _full_id(model)
    if name in CLAUDE_CLI.aliases:
        return name
    if name.startswith("claude-"):
        for part in re.split(r"[-@]", name):
            if part in CLAUDE_CLI.aliases:
                return part
    return None


def check_sampling(provider: str, temperature: Optional[float], seed: Optional[int]) -> None:
    """Raise :class:`SamplingError` before a run when a CLI *provider* is given a temperature or seed."""
    if provider in BACKENDS and (temperature is not None or seed is not None):
        raise SamplingError(f"{provider} takes no temperature or seed: leave out the judge temperature "
                            "and seed options")


def work_dir(name: str) -> Path:
    """The fixed empty working directory a backend's calls run in.

    Fixed, not fresh per call: the CLI states its working directory in the prompt, and
    a random path would make every request, and its response-cache key, differ.
    Under the user's cache dir, not the shared temp dir: another account there could
    pre-create it, and the CLI reads the git status of its working directory into the prompt.
    """
    base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    path = base / "dokimi" / "cli" / name
    try:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as exc:
        raise CliCallError(f"{name}: cannot create the work dir {path} ({exc})", retryable=False) from exc
    return path


def _passed_on(name: str, cli: CliBackend) -> bool:
    if name in cli.env_keep:
        return True
    return name not in cli.env_drop and not name.startswith(cli.env_drop_prefixes)


def _env(cli: CliBackend, extra: Optional[Dict[str, str]] = None) -> process.Env:
    return process.Env.only([k for k in os.environ if _passed_on(k, cli)], set=extra)


def _call_env(cli: CliBackend, cwd: Path) -> process.Env:
    """The env of a call: the parent's, minus the drops, and git stopped at the work dir's parent.

    The CLI reads the git status of its working directory into the prompt. The work dir sits
    under the user's cache dir, which can lie inside a repository (a home directory under git),
    so git must not search above that cache directory for a repository. A repository named by
    ``GIT_DIR`` or ``GIT_WORK_TREE`` is not searched for at all, so those are dropped too.
    """
    ceiling = [str(cwd.parent)] + [p for p in os.environ.get("GIT_CEILING_DIRECTORIES", "").split(os.pathsep) if p]
    passed = [k for k in os.environ if _passed_on(k, cli) and k not in process.GIT_REPO_OVERRIDES]
    return process.Env.only(passed, set={"GIT_CEILING_DIRECTORIES": os.pathsep.join(ceiling)})


def cache_extra(provider: str) -> Dict[str, str]:
    """What the response-cache key takes from a CLI backend: its version, and nothing otherwise.

    An alias resolves inside the CLI, so a new release can answer ``sonnet`` with another
    model, and its replies must not be replayed for the new one. The resolved id is known
    only after the call, so the version stands in for it; a move the CLI does not ship
    with is not seen.
    """
    return {"cli_version": cli_version(provider)} if backend(provider) is not None else {}


class _Unreadable(Exception):
    """The version probe gave no version line. Raised, not returned, so ``lru_cache`` keeps nothing."""


def cli_version(provider: str) -> str:
    """The CLI's version line, or ``""`` when it cannot be read. Read once per process, once it reads."""
    try:
        return _version_line(provider)
    except _Unreadable:
        return ""


@functools.lru_cache(maxsize=None)
def _version_line(provider: str) -> str:
    cli = BACKENDS[provider]
    try:
        done = process.run([cli.binary, "--version"], timeout=30, env=_env(cli))
    except OSError as exc:
        raise _Unreadable from exc
    lines = done.stdout.strip().splitlines() if done.ok else []
    if not lines:
        raise _Unreadable
    return lines[0]


def call(provider: str, *, model: str, prompt: str, system: Optional[str] = None,
         timeout: float = DEFAULT_TIMEOUT_SEC, runner: Optional[process.Runner] = None) -> CliResult:
    """One completion through *provider*'s CLI; the prompt goes on stdin (no ``ARG_MAX``) where the CLI reads it so.

    Raises:
        CliCallError: the CLI is missing, failed, timed out or returned no reply.
        KeyError: *provider* is not a CLI backend.
    """
    cli = BACKENDS[provider]
    cwd = work_dir(cli.name)
    system_text = system or DEFAULT_SYSTEM
    text = f"{system_text}\n\n{prompt}" if cli.system_in_prompt else prompt
    # A private temp dir (mkdtemp, mode 0700) for the one call: the system prompt goes by file.
    with tempfile.TemporaryDirectory(prefix="dokimi-cli-") as tmp:
        system_file = Path(tmp) / "system-prompt.txt"
        system_file.write_text(system_text, encoding="utf-8")
        try:
            argv = cli.build_argv(model, str(system_file), text)
            done = (runner or process.run)(argv, timeout=timeout, env=_call_env(cli, cwd), cwd=cwd,
                                           input=text if cli.prompt_on_stdin else None, capture_limit=None)
        except FileNotFoundError as exc:
            raise CliCallError(f"{provider}: '{cli.binary}' is not installed or not on PATH ({exc})",
                               retryable=False, run_wide=True) from exc
    return cli.parse(done, model, provider)


def detect(provider: str, runner: Optional[process.Runner] = None) -> AuthStatus:
    """Whether *provider*'s binary is installed and signed in. Calls no model."""
    cli = BACKENDS[provider]
    runner = runner or process.run
    env = _env(cli)
    try:
        version = runner([cli.binary, "--version"], timeout=30, env=env)
    except OSError:
        return AuthStatus(provider, cli.binary, installed=False, hint=f"install '{cli.binary}'")
    try:
        auth = runner(list(cli.auth_argv), timeout=30, env=env)
        authenticated, method = cli.parse_auth(auth)
    except OSError:
        authenticated, method = None, ""
    return AuthStatus(
        provider, cli.binary, installed=True, version=version.stdout.strip().splitlines()[0]
        if version.stdout.strip() else "", authenticated=authenticated, method=method,
        source=_login_source(method), hint="" if authenticated else cli.login_hint,
    )


def _login_source(method: str) -> str:
    """Where a login comes from, as far as its method says: a third-party route is set in the settings file.

    The child's environment drops the ``CLAUDE_*`` and ``ANTHROPIC_*`` variables, so a route that
    ``claude auth status`` reports as ``third_party`` can only come from ``settings.json``.
    """
    return "settings file" if method.startswith("third_party") else ""
