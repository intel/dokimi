# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Real-harness skill triggering: one fresh ``claude -p`` per query, no notice.

The ``skill-catalog:`` arm is a tool-calling emulation: one tool per skill, every
description in full, no system prompt, no other tools. Claude Code instead sends one
``Skill`` tool, a listing it cuts to a budget, its own system prompt and a dozen other
tools, and the model may look around before it loads anything. This module measures
that harness as shipped:

* each query runs in a new git repository under a temp dir (no ancestor ``CLAUDE.md``,
  no checkout to walk), with the catalog committed in ``.claude/skills/``, where
  Claude Code looks for project skills, so ``git status`` shows nothing unusual;
* ``HOME`` and ``CLAUDE_CONFIG_DIR`` are fresh and the env is the sandbox allowlist,
  so the operator's skills, settings and ``CLAUDE.md`` are not loaded (the model can
  still read any host path it is allowed to, the trigger set included);
* the prompt is the query verbatim: no treatment notice, no task framing;
* in ``stock`` mode (the default) the bundled skills and tools stay on, as a user
  has them; ``sealed`` switches them off like the docker-claude arms do.

The skills invoked are read from the ``stream-json`` transcript (``Skill`` tool uses);
the first assistant turn that invokes any is the choice, as for the emulated arm.
Records come out in the answers shape :func:`dokimi.eval.arm_summary.summarize_skill_selection`
reads, so both arms are scored by the same code.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

from dokimi import process
from dokimi.harnesses.agent_sandbox import (
    _SEAL_ENV, _SEAL_SETTINGS, AgentWorkspace, agent_env, operator_source_env,
)
from dokimi.harnesses.claude_json import result_event

#: Fixed identity and dates for the staging commit, so the git context the harness
#: shows the model is the same on every run.
_GIT_IDENT = {
    "GIT_AUTHOR_NAME": "user", "GIT_AUTHOR_EMAIL": "user@example.com",
    "GIT_COMMITTER_NAME": "user", "GIT_COMMITTER_EMAIL": "user@example.com",
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
}
_COMMIT_MESSAGE = "Initial commit"


@dataclass
class ProbeConfig:
    """How each query is run. ``listing_budget_fraction`` ``None`` keeps the harness default."""

    model: Optional[str] = None
    max_turns: int = 3
    timeout_sec: float = 300.0
    stock: bool = True
    listing_budget_fraction: Optional[float] = None
    claude: str = "claude"
    extra_args: List[str] = field(default_factory=list)


#: Prefix of each run's temp root. Claude Code shows the model its working directory,
#: so the name must not say what the run is (``AgentWorkspace.create`` uses a plain ``mkdtemp`` name).
WORKSPACE_PREFIX = "project-"


def new_workspace() -> AgentWorkspace:
    """A scratch ``{work,home,config}`` root whose path names no experiment."""
    root = Path(tempfile.mkdtemp(prefix=WORKSPACE_PREFIX))
    for sub in ("work", "home", "config"):
        (root / sub).mkdir()
    return AgentWorkspace(root=root)


def stage_workspace(catalog: Path, ws: AgentWorkspace, runner: process.Runner = process.run) -> Path:
    """Copy *catalog* to ``work/.claude/skills`` and commit it; return the work dir."""
    work = ws.work
    shutil.copytree(catalog, work / ".claude" / "skills", symlinks=False)
    env = process.Env.only(("PATH",), {**_GIT_IDENT, "HOME": str(ws.root / "home"),
                                       "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0"})
    for args in (("init", "-q", "-b", "main"), ("add", "-A"),
                 ("commit", "-q", "--no-gpg-sign", "-m", _COMMIT_MESSAGE)):
        done = process.git(*args, cwd=work, env=env, runner=runner)
        if done.returncode != 0:
            raise RuntimeError(f"git {args[0]} failed staging the catalog: {process.git_failure(done)}")
    return work


def probe_env(ws: AgentWorkspace, cfg: ProbeConfig, source: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """The allowlisted env, scratch HOME/config; the bundled-skill seal only when not ``stock``."""
    env = agent_env(operator_source_env() if source is None else source)
    if cfg.stock:
        for k in _SEAL_ENV:
            env.pop(k, None)
    env["HOME"] = str(ws.root / "home")
    env["CLAUDE_CONFIG_DIR"] = str(ws.root / "config")
    env["TMPDIR"] = str(ws.root / "home")
    return env


def probe_argv(query: str, cfg: ProbeConfig) -> List[str]:
    """``claude`` argv: the query verbatim, stream-json out, and nothing that steers the model."""
    argv = [cfg.claude, "-p", query, "--output-format", "stream-json", "--verbose",
            "--max-turns", str(cfg.max_turns), "--no-session-persistence"]
    if cfg.model:
        argv += ["--model", cfg.model]
    # One --settings: sealed carries the arms' plugin seal, merged with any listing budget.
    settings: Dict[str, Any] = {} if cfg.stock else json.loads(json.dumps(_SEAL_SETTINGS))
    if cfg.listing_budget_fraction is not None:
        settings["skillListingBudgetFraction"] = cfg.listing_budget_fraction
    if settings:
        argv += ["--settings", json.dumps(settings)]
    return argv + list(cfg.extra_args)


def _events(stream: str) -> Iterable[Dict[str, Any]]:
    for line in stream.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            yield obj


def _skill_name(raw: Any) -> str:
    # A plugin skill is invoked as `plugin:skill`; the catalog names the skill alone.
    return str(raw or "").rsplit(":", 1)[-1]


def parse_stream(stream: str) -> Dict[str, Any]:
    """Skills listed and invoked, per the transcript; ``first_turn_loaded`` is the choice."""
    listed: List[str] = []
    version = None
    # Claude Code emits one assistant event per content block; the blocks of one turn
    # share message.id. Subagent events (parent_tool_use_id set) are not the choice.
    by_turn: Dict[str, List[str]] = {}
    other_tools: List[str] = []
    for n, ev in enumerate(_events(stream)):
        if ev.get("type") == "system" and ev.get("subtype") == "init":
            listed = [str(s) for s in ev.get("skills") or []]
            version = ev.get("claude_code_version")
        if ev.get("type") != "assistant" or ev.get("parent_tool_use_id"):
            continue
        message = ev.get("message") or {}
        uses = [c for c in message.get("content") or [] if isinstance(c, dict) and c.get("type") == "tool_use"]
        skills = [_skill_name((c.get("input") or {}).get("skill")) for c in uses if c.get("name") == "Skill"]
        if not by_turn:  # tools after the choice are not "before" it
            other_tools += [str(c.get("name")) for c in uses if c.get("name") != "Skill"]
        if skills:
            turn = by_turn.setdefault(str(message.get("id") or f"#{n}"), [])
            turn += [s for s in dict.fromkeys(skills) if s not in turn]
    turns = list(by_turn.values())
    first = turns[0] if turns else []
    return {
        "offered": True,
        "listed": listed,
        "selected": first[0] if first else None,
        "first_turn_loaded": first,
        "loaded": [s for t in turns for s in t],
        "tools_before_choice": other_tools,
        "claude_code_version": version,
    }


def run_query(query: str, catalog: Path, cfg: ProbeConfig, *, runner: process.Runner = process.run,
              source_env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """One fresh harness run of *query*; the parsed skill block plus cost and any error."""
    ws = new_workspace()
    try:
        work = stage_workspace(catalog, ws, runner=runner)
        argv = probe_argv(query, cfg)
        done = runner(argv, cwd=work, timeout=cfg.timeout_sec,
                      env=process.Env.only((), probe_env(ws, cfg, source_env)), input="",
                      capture_limit=None)
        stream = done.stdout
        skill = parse_stream(stream)
        result = result_event(stream) or {}
        error = None
        # A Skill call seen is the choice, whatever came after it (a hang, a timeout, an
        # error). A result event read decides the rest, even if the CLI then hung.
        if skill["first_turn_loaded"]:
            pass
        elif not result:
            error = "timeout" if done.timed_out else f"no result event (exit {done.returncode})"
        # Out of turns without a Skill call is an observation (nothing triggered), not a failure.
        elif result.get("is_error") and result.get("subtype") != "error_max_turns":
            error = str(result.get("subtype") or "error")
        return {"skill": skill, "cost_usd": result.get("total_cost_usd"),
                "num_turns": result.get("num_turns"), "result_subtype": result.get("subtype"), "error": error}
    finally:
        kill_strays(ws.root)
        ws.cleanup()


def kill_strays(root: Path) -> List[int]:
    """SIGKILL every process whose cwd is under *root*; return their pids (Linux only).

    A command the model backgrounds in its Bash tool runs in a session of its own, so
    the timeout's process-group kill misses it and it outlives the run and its workspace.
    """
    if not Path("/proc").is_dir():
        return []
    prefix = str(Path(root).resolve())
    killed = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit() or int(entry) == os.getpid():
            continue
        try:
            cwd = os.readlink(f"/proc/{entry}/cwd")
        except OSError:  # exited meanwhile, or not ours to read
            continue
        if cwd == prefix or cwd.startswith(prefix + os.sep):
            try:
                os.kill(int(entry), signal.SIGKILL)
                killed.append(int(entry))
            except OSError:
                pass
    return killed


def probe_trigger_set(queries: Sequence[Any], catalog: Path, cfg: ProbeConfig, *, trials: int = 1,
                      arm_name: str = "claude_code", runner: process.Runner = process.run,
                      on_record: Optional[Callable[[Dict[str, Any]], None]] = None) -> List[Dict[str, Any]]:
    """Run every labelled query *trials* times; answers-shaped records for the selection summary.

    *queries* are :class:`dokimi.external.trigger_set.TriggerQuery`. Each trial is its own
    arm (``<arm_name>#<k>``), so the summary reports trials side by side.
    """
    out: List[Dict[str, Any]] = []
    for q in queries:
        if not getattr(q, "labelled", True):
            continue
        arms = {}
        for k in range(trials):
            try:
                r = run_query(q.text, catalog, cfg, runner=runner)
            except (OSError, RuntimeError) as exc:
                # One failed run (staging, spawn) is that trial's error, not the lost set.
                r = {"skill": parse_stream(""), "cost_usd": None, "num_turns": None, "result_subtype": None,
                     "error": f"{type(exc).__name__}: {exc}"}
            arms[f"{arm_name}#{k + 1}"] = {
                "answer": None,
                "error": r["error"],
                "tool_usage": {"skill": r["skill"]},
                "metrics": {"cost_usd": r["cost_usd"], "num_turns": r["num_turns"], "result_subtype": r.get("result_subtype")},
            }
        rec = {"question_id": q.id, "question_text": q.text, "expected_skill": q.expected_skill, "arms": arms}
        out.append(rec)
        if on_record:
            on_record(rec)
    return out
