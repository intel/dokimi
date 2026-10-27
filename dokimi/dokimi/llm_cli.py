# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Coding-agent CLIs as LLM providers: a completion through a locally logged-in CLI.

A provider in :data:`BACKENDS` (``claude-cli``) runs one prompt through the CLI the
user is already signed in to (a Claude subscription, ``claude setup-token``, or
whatever ``claude auth status`` reports) instead of an API key. :mod:`dokimi.llm`
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
  login or a missing binary are not.

:func:`detect` reports whether a backend's binary is installed and signed in
without calling a model (``cli.py backends``).
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from dokimi import process
from dokimi.metrics.usage import UsageRecord

#: Variables a Claude Code session sets for its own children. Dropped, so a call made
#: from inside a session is a session of its own.
CLAUDE_ENV_DROP = frozenset({
    "CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SSE_PORT",
})

#: The flags that strip the CLI to a bare completion; see the module docstring.
CLAUDE_ISOLATION = ("--tools", "", "--setting-sources", "", "--strict-mcp-config",
                    "--no-session-persistence")

#: ``api_error_status`` values worth another attempt.
_RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504, 529})
_RETRYABLE_WORDS = ("rate limit", "rate_limit", "overloaded", "usage limit", "timed out")

DEFAULT_TIMEOUT_SEC = 600.0


class CliCallError(RuntimeError):
    """A CLI call that produced no reply; ``retryable`` is what :func:`dokimi.llm.is_retryable` reads."""

    def __init__(self, message: str, *, retryable: bool):
        super().__init__(message)
        self.retryable = retryable


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
    hint: str = ""


@dataclass(frozen=True)
class CliBackend:
    """One CLI: how to call it, read its reply, and ask it whether it is signed in."""

    name: str
    binary: str
    build_argv: Callable[[str, Optional[str]], list]  # (model, system) -> argv; prompt goes on stdin
    parse: Callable[[process.Completed, str, str], CliResult]  # (completed, model, provider)
    auth_argv: Tuple[str, ...]
    parse_auth: Callable[[process.Completed], Tuple[Optional[bool], str]]  # -> (authenticated, method)
    env_drop: frozenset = frozenset()
    supports: frozenset = frozenset()  # of "temperature", "seed", "max_tokens"
    login_hint: str = ""
    aliases: Tuple[str, ...] = ()


# ── claude-cli ────────────────────────────────────────────────────────────────


def _claude_argv(model: str, system: Optional[str]) -> list:
    argv = ["claude", "-p", "--input-format", "text", "--output-format", "json",
            "--model", model, *CLAUDE_ISOLATION]
    # Without --system-prompt the CLI sends its own agent prompt (~37k tokens with tools).
    return argv + ["--system-prompt", system or "You are a helpful assistant."]


def _claude_error(payload: Dict[str, Any], done: process.Completed) -> CliCallError:
    message = str(payload.get("result") or done.stderr.strip() or f"exit {done.returncode}")[:500]
    status = payload.get("api_error_status")
    lowered = message.lower()
    if any(w in lowered for w in ("authentication", "not logged in", "/login")):
        return CliCallError(f"claude-cli: {message} (sign in: claude setup-token, or claude /login)",
                            retryable=False)
    retryable = status in _RETRYABLE_STATUS or any(w in lowered for w in _RETRYABLE_WORDS)
    return CliCallError(f"claude-cli: {message}", retryable=retryable)


def _claude_parse(done: process.Completed, model: str, provider: str) -> CliResult:
    if done.timed_out:
        raise CliCallError(f"claude-cli: no reply in {done.elapsed_sec:.0f}s", retryable=True)
    try:
        payload = json.loads(done.stdout)
    except ValueError:
        payload = None
    if not isinstance(payload, dict):
        raise CliCallError(f"claude-cli: exit {done.returncode}, no JSON reply: "
                           f"{(done.stderr or done.stdout).strip()[:300]}", retryable=False)
    if payload.get("is_error") or done.returncode != 0:
        raise _claude_error(payload, done)
    u = payload.get("usage") or {}
    cache_write = int(u.get("cache_creation_input_tokens") or 0)
    cache_read = int(u.get("cache_read_input_tokens") or 0)
    prompt = int(u.get("input_tokens") or 0) + cache_write + cache_read
    completion = int(u.get("output_tokens") or 0)
    model_usage = payload.get("modelUsage") or {}
    list_cost = payload.get("total_cost_usd")
    usage = UsageRecord(
        prompt_tokens=prompt, completion_tokens=completion, total_tokens=prompt + completion,
        cache_read_tokens=cache_read, cache_write_tokens=cache_write,
        cost_usd=None, latency_sec=done.elapsed_sec, model=model, provider=provider,
        n_calls=1, cost_known_calls=0,
    )
    return CliResult(
        text=str(payload.get("result") or ""),
        usage=usage,
        model_id=next(iter(model_usage), None),
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
    build_argv=_claude_argv,
    parse=_claude_parse,
    auth_argv=("claude", "auth", "status"),
    parse_auth=_claude_auth,
    env_drop=CLAUDE_ENV_DROP,
    login_hint="claude setup-token (headless) or claude, then /login",
    aliases=("haiku", "sonnet", "opus"),
)

#: CLI backends by provider name.
BACKENDS: Dict[str, CliBackend] = {CLAUDE_CLI.name: CLAUDE_CLI}


def backend(provider: str) -> Optional[CliBackend]:
    """The CLI backend for *provider*, or ``None`` for an API provider."""
    return BACKENDS.get(provider)


def work_dir(name: str) -> Path:
    """The fixed empty working directory a backend's calls run in.

    Fixed, not fresh per call: the CLI states its working directory in the prompt, and
    a random path would make every request, and its response-cache key, differ.
    """
    path = Path(tempfile.gettempdir()) / "dokimi-cli" / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def _env(cli: CliBackend) -> process.Env:
    return process.Env.only([k for k in os.environ if k not in cli.env_drop])


def call(provider: str, *, model: str, prompt: str, system: Optional[str] = None,
         timeout: float = DEFAULT_TIMEOUT_SEC, runner: Optional[process.Runner] = None) -> CliResult:
    """One completion through *provider*'s CLI; the prompt goes on stdin (no ``ARG_MAX``).

    Raises:
        CliCallError: the CLI is missing, failed, timed out or returned no reply.
        KeyError: *provider* is not a CLI backend.
    """
    cli = BACKENDS[provider]
    try:
        done = (runner or process.run)(cli.build_argv(model, system), timeout=timeout, env=_env(cli),
                      cwd=work_dir(cli.name), input=prompt, capture_limit=None)
    except FileNotFoundError as exc:
        raise CliCallError(f"{provider}: '{cli.binary}' is not installed or not on PATH ({exc})",
                           retryable=False) from exc
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
        hint="" if authenticated else cli.login_hint,
    )
