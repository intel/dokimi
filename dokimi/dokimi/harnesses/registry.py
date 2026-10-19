# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Known coding harness aliases and command-template resolution."""

from __future__ import annotations

from dataclasses import fields, replace
from pathlib import Path

from dokimi import process

from .benchflow import (
    SKILL_MODE_NO_SKILL,
    SKILL_MODE_SELF_GEN,
    SKILL_MODE_WITH_SKILL,
    BenchflowHarness,
)
from .command import CommandHarness
from .docker_solver import DockerSolverHarness


def known_harnesses() -> dict[str, str]:
    """Human-readable descriptions of built-in harness aliases."""

    return {
        "codex": "Codex CLI via Harbor agent 'codex'",
        "claude-code": "Claude Code via Harbor agent 'claude-code'",
        "terminal-bench:<agent>": "Arbitrary Harbor/terminal-bench agent id",
        "docker-oracle": "Local Docker: apply the task's own solve.sh (no LLM)",
        "docker-claude": "Local Docker: `claude -p` inside the task container, egress to the LLM only",
        "docker-claude-skill:<skill>": "docker-claude with a skill dir exposed (with-skill arm)",
        "docker-claude-skill-discover:<skill>": (
            "docker-claude-skill without the treatment notice (discovery arm)"
        ),
        "benchflow:<agent>": "BenchFlow `bench eval run` (SkillsBench runner), no-skill arm",
        "benchflow-skill:<agent>[:<skills_dir>]": (
            "BenchFlow with-skill arm: the task's environment/skills, or a skills dir"
        ),
        "benchflow-selfgen:<agent>[:<skill_creator_dir>]": (
            "BenchFlow self-generated-skill arm, optionally with a named skill-creator"
        ),
        "docker-probe": "Local Docker: no LLM; report what a docker-claude agent could reach",
        "docker-claude-host": "docker-claude with the agent on the host (absolute paths, network open)",
        "docker-claude-host-skill:<skill>": "docker-claude-host with a skill dir exposed",
        "docker-claude-host-skill-discover:<skill>": "docker-claude-host-skill without the notice",
        "docker-probe-host": "docker-probe for the docker-claude-host launch",
        "docker-probe:<skill>": "docker-probe that also forbids <skill> (the baseline's treatment)",
        "command:<template>": "Custom shell-like command template",
    }


def create_harness(harness: str, *, command_template: str | None = None,
                   runner: process.Runner = process.run):
    """Create a coding harness adapter; ``runner`` starts the processes it runs.

    Harbor-backed built-ins (``codex``, ``claude-code``) follow the
    Harbor/terminal-bench task format. The ``docker-*`` adapters need no Harbor:
    they build the task image, solve it (oracle, ``claude -p`` or the isolation
    probe), and verify
    locally while capturing LLM telemetry. A custom ``command:`` template may use
    placeholders ``{task_path}``, ``{task}``, ``{model}``, ``{output_dir}``,
    ``{prompt}``. The ``docker-*`` adapters start theirs themselves and ignore
    ``runner``.
    """
    adapter = _create(harness, command_template)
    if runner is process.run or "runner" not in {f.name for f in fields(adapter)}:
        return adapter
    return replace(adapter, runner=runner)


def _create(harness: str, command_template: str | None):
    if command_template:
        return CommandHarness(harness_id=harness, command_template=command_template, kind="command")
    if harness == "codex":
        return _harbor("codex", "codex")
    if harness == "claude-code":
        return _harbor("claude-code", "claude-code")
    if harness == "docker-oracle":
        return DockerSolverHarness(harness_id=harness, mode="oracle")
    for prefix, location in (("docker-claude-host", "host"), ("docker-claude", "container")):
        if harness == prefix:
            return DockerSolverHarness(harness_id=harness, mode="claude", agent_location=location)
        for arm, notice in (("skill", True), ("skill-discover", False)):
            if harness.startswith(f"{prefix}-{arm}:"):
                return DockerSolverHarness(harness_id=harness, mode="claude",
                                           skill_path=_skill_arg(harness, f"{prefix}-{arm}"),
                                           agent_location=location, skill_notice=notice)
    for name, location in (("docker-probe-host", "host"), ("docker-probe", "container")):
        if harness == name:
            return DockerSolverHarness(harness_id=harness, mode="probe", agent_location=location)
        if harness.startswith(f"{name}:"):
            return DockerSolverHarness(harness_id=harness, mode="probe",
                                       skill_path=_skill_arg(harness, name), agent_location=location)
    if harness.startswith("terminal-bench:"):
        agent = harness.split(":", 1)[1]
        if not agent:
            raise ValueError("terminal-bench harness requires an agent id, e.g. terminal-bench:codex")
        return _harbor(harness, agent)
    if harness.startswith(("benchflow:", "benchflow-skill:", "benchflow-selfgen:")):
        return _benchflow(harness)
    if harness.startswith("command:"):
        template = harness.split(":", 1)[1]
        if not template:
            raise ValueError("command harness requires a command template")
        return CommandHarness(harness_id=harness, command_template=template, kind="command")
    raise ValueError(
        f"Unknown harness '{harness}'. Known: {', '.join(known_harnesses())}; "
        "or pass --command-template."
    )


def with_skill_counterpart(harness: str) -> str | None:
    """The with-skill arm a ``docker-claude[-host]-skill-discover:<skill>`` arm pairs with.

    Same launch and skill path, notice on: the two differ by the notice only, so
    their pass-rate difference is the discovery gap. ``None`` for any other id.
    """
    for prefix in ("docker-claude-host", "docker-claude"):
        discover = f"{prefix}-skill-discover:"
        if harness.startswith(discover):
            return f"{prefix}-skill:{harness[len(discover):]}"
    return None


def _skill_arg(harness: str, prefix: str) -> str:
    """The ``<skill>`` of ``<prefix>:<skill>``; it must exist, so a typo is not a sealed run."""
    skill = harness.split(":", 1)[1]
    if not skill:
        raise ValueError(
            f"{prefix} harness requires a skill path, e.g. "
            f"{prefix}:/path/to/skills-repo/skills/<name>/SKILL.md"
        )
    if not Path(skill).exists():
        raise ValueError(f"{prefix}: skill path {skill!r} does not exist")
    return skill


def _harbor(harness_id: str, agent: str) -> CommandHarness:
    return CommandHarness(
        harness_id=harness_id,
        kind="harbor",
        command_template=(
            "harbor run -p {task_path} -a " + agent + " -m {model} "
            "--jobs-dir {output_dir}"
        ),
        agent=agent,
    )


# BenchFlow's "<ns>:<id>" agent shorthand (acp:pi, ai-sdk:codex, omnigent:pi);
# such an agent id spans two ':'-separated segments. (acpx is written
# acpx/<id> in 0.7.8, so it needs no special case.)
_BENCHFLOW_AGENT_NAMESPACES = ("acp", "ai-sdk", "omnigent")


def _benchflow(harness_id: str) -> BenchflowHarness:
    prefix, rest = harness_id.split(":", 1)
    agent, _, skills_dir = rest.partition(":")
    if agent in _BENCHFLOW_AGENT_NAMESPACES and skills_dir:
        ident, _, skills_dir = skills_dir.partition(":")
        agent = f"{agent}:{ident}"
    if not agent:
        raise ValueError(f"{prefix} harness requires a BenchFlow agent id, e.g. {prefix}:claude-agent-acp")
    mode = {
        "benchflow": SKILL_MODE_NO_SKILL,
        "benchflow-skill": SKILL_MODE_WITH_SKILL,
        "benchflow-selfgen": SKILL_MODE_SELF_GEN,
    }[prefix]
    if skills_dir and mode == SKILL_MODE_NO_SKILL:
        raise ValueError(f"{prefix} takes no skills dir; use benchflow-skill:{agent}:<skills_dir>")
    if mode == SKILL_MODE_SELF_GEN:
        # The optional third segment names the skill-creator BenchFlow uses;
        # without it BenchFlow searches the host (~/.claude/skills, ...), so
        # the arm would depend on whatever that host has installed.
        return BenchflowHarness(
            harness_id=harness_id, agent=agent, skill_mode=mode,
            skill_creator_dir=skills_dir or None,
        )
    return BenchflowHarness(
        harness_id=harness_id, agent=agent, skill_mode=mode, skills_dir=skills_dir or None,
    )
