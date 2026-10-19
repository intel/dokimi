# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""How the ``docker-claude-skill[-discover]:<path>`` arms offer their skill to the solver.

The skill is installed the way Claude Code installs any skill, so the agent
loads it with its own ``Skill`` tool:

* it is wrapped in a one-skill plugin and passed with ``--plugin-dir``. The
  launch keeps ``--setting-sources ""``, under which a skill in
  ``$CLAUDE_CONFIG_DIR/skills`` is *not* loaded (measured, Claude Code 2.1.286),
  and ``--setting-sources project`` would also read the task tree's own
  ``.claude/settings.json`` and ``CLAUDE.md``;
* the prompt opens with :data:`TREATMENT_NOTICE`, which says a skill is
  installed and names nothing about the task. Without it the arm would also
  measure whether the description gets the skill picked, a second variable in
  the reward delta. The wording follows intel/skills'
  ``evaluation/harbor/instructions/use-<skill>.md``.

The ``docker-claude-skill-discover:<path>`` arm is that second variable on
purpose: the same plugin and tool set, and no notice, so the agent has only
the skill's listing entry (name and description) to find it by. Its pass rate
next to the skill arm's is the discovery gap (see :func:`skill_listing` for
whether the description was shown in full).

Both arms get the ``Skill`` tool (see ``SOLVER_TOOLS``), so the arms differ by
the skill and the notice only. :func:`stream_stats` reads the agent's
stream-json output, so each run records which skills were offered and whether
the treatment was actually loaded.

The skill's frontmatter is part of the treatment, and :func:`check_skill`
refuses the keys that would change more than the guidance: ``model:`` and
``context: fork`` (a forked subagent can switch models mid-cell, and its tokens
are not in the result's ``usage``), and ``disable-model-invocation: true`` (the
agent could never load it, so the arm would be the baseline plus a notice).
"""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Optional

from dokimi.skills import load_skill
from dokimi.utils import parse_frontmatter

TREATMENT_NOTICE = (
    "A skill named `{skill_id}` is installed. Load it with the Skill tool before you "
    "start, and follow its guidance where it applies to this task.\n\n"
    "If the skill does not cover something the task needs, solve that part however "
    "you normally would. If the skill contradicts what you observe in the "
    "environment, trust the environment and say so in your final message.\n\n"
)

# Claude Code's skill listing (2.1.296): each entry is ``description`` (plus
# `` - when_to_use``) cut to ``skillListingMaxDescChars`` (default 1536), and the
# whole listing shortened to fit ``skillListingBudgetFraction`` (default 0.01)
# of the context window at 4 characters per token, 8000 characters at 200k.
LISTING_MAX_DESC_CHARS = 1536
LISTING_BUDGET_CHARS = 8000

# Skills both arms may be offered besides the treatment: the core ``doctor``
# command, and on the host route ``design``, which the model cannot invoke
# (Claude Code 2.1.288; the container's CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC
# drops it). Anything else is recorded as ``unexpected_skills``.
_EXPECTED_SKILLS = frozenset({"doctor", "design"})


@dataclass(frozen=True)
class Treatment:
    """The installed skill: its own name, and the plugin id it loads under."""

    name: str
    plugin: str

    @property
    def skill_id(self) -> str:
        """``<plugin>:<skill>``, as the CLI lists it; the skill dir is named like the plugin."""
        return f"{self.plugin}:{self.plugin}"

    def is_call(self, requested: str) -> bool:
        """Whether a ``Skill`` input or ``commandName`` names this treatment.

        The Skill tool strips a leading ``/`` and accepts the frontmatter name as
        an alias of ``<plugin>:<skill>`` (2.1.288). A bare plugin id is not the
        treatment unless it is also the name: the CLI rejects it, and where it
        matches a core command (``doctor``) a successful call is that command.
        """
        s = requested.strip().lstrip("/")
        return s == self.name or s.startswith(f"{self.plugin}:")

    def as_metrics(self) -> dict[str, str]:
        return {"skill": self.name, "plugin": self.plugin, "delivery": "plugin-dir"}


def plugin_slug(name: str) -> str:
    """A plugin/directory name Claude Code accepts, and that cannot leave its parent.

    Claude Code refuses a plugin name with whitespace or any of ``@ : / \\``
    (2.1.288), and ``/`` or ``..`` would place the copy elsewhere. The slug keeps
    ``[a-z0-9_-]`` only.
    """
    slug = re.sub(r"[^a-z0-9_-]+", "-", name.lower()).strip("-_")
    return slug or "skill"


def check_skill(skill_dir: Path) -> str:
    """Validate *skill_dir* as a treatment and return its name; ``ValueError`` if it is not one.

    Cheap, so the harness runs it before the image build: a bad skill path
    fails the cell without the docker setup.
    """
    if not (skill_dir / "SKILL.md").is_file():
        raise ValueError(f"no SKILL.md in skill directory {skill_dir}")
    _check_links(skill_dir)
    try:
        skill = load_skill(skill_dir)
    except (FileNotFoundError, ValueError) as exc:
        raise ValueError(f"cannot load skill at {skill_dir}: {exc}") from exc
    meta, _ = parse_frontmatter(skill.path.read_text(encoding="utf-8"))
    refused = []
    # The CLI reads a boolean as 1/true/yes/on, trimmed, in any case, quoted or not.
    if str(meta.get("disable-model-invocation", "")).strip().lower() in ("1", "true", "yes", "on"):
        refused.append("disable-model-invocation: true (the agent could not load it)")
    if str(meta.get("context", "")).lower() == "fork":
        refused.append("context: fork (a subagent outside the cell's usage)")
    if meta.get("model"):
        refused.append(f"model: {meta['model']} (the cell would mix models)")
    if meta.get("hooks"):
        refused.append("hooks (code or another model in the skill arm only, outside tool_calls)")
    if refused:
        raise ValueError(f"skill frontmatter at {skill.path} is part of the treatment; "
                         f"not supported: {'; '.join(refused)}")
    return skill.name


def _check_links(skill_dir: Path) -> None:
    """Refuse a link that leaves the skill, points into its ``evals/``, loops, or dangles.

    The copy follows links (a relative one would dangle in the plugin), so a
    link is how content from outside the skill would reach the agent: a
    sibling task's ``solution/`` and ``tests/``, or an ancestor that copies
    the checkout into itself until ``ELOOP``.
    """
    root = skill_dir.resolve()
    for dirpath, dirnames, filenames in os.walk(skill_dir):  # does not descend into links
        here = Path(dirpath)
        if here == skill_dir:  # the top-level evals/ is not copied, see build_plugin
            dirnames[:] = [d for d in dirnames if d != "evals"]
            filenames = [f for f in filenames if f != "evals"]
        for entry in [*dirnames, *filenames]:
            link = here / entry
            if not link.is_symlink():
                continue
            try:
                target = link.resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                raise ValueError(f"skill link {link} does not resolve: {exc}") from exc
            if not target.is_relative_to(root) or target.is_relative_to(root / "evals"):
                raise ValueError(f"skill link {link} -> {target} leaves the skill directory {root}")
            if target.is_dir() and link.parent.resolve().is_relative_to(target):
                raise ValueError(f"skill link {link} -> {target} points at its own ancestor")
    _check_link_loops(skill_dir, root)


def _check_link_loops(skill_dir: Path, root: Path) -> None:
    """Refuse links that loop through each other (``a/b -> ../c`` with ``c/d -> ../a``).

    Each link passes the ancestor rule on its own, but the copy follows both until
    ``ELOOP``. Walk the directory graph with links followed, keeping the stack of
    real directories; one met again on its own path is a loop. A directory already
    walked is not walked twice, so many links to one place stay cheap.
    """
    done: set[Path] = set()

    def visit(directory: Path, real: Path, stack: tuple[Path, ...]) -> None:
        try:
            with os.scandir(directory) as entries:
                children = [Path(e.path) for e in entries if e.is_dir()
                            and not (directory == skill_dir and e.name == "evals")]
        except OSError:
            return  # unreadable, as os.walk would have skipped it: the copy reports it
        for child in children:
            target = child.resolve()
            if not target.is_relative_to(root) or target in done:
                continue  # outside the skill: refused above
            if target in stack:
                raise ValueError(f"skill link {child} -> {target} loops back into the skill")
            visit(child, target, (*stack, target))
        done.add(real)

    visit(skill_dir, root, (root,))


def build_plugin(skill_dir: Path, dest: Path) -> Treatment:
    """Write a one-skill plugin for *skill_dir* at *dest* (see :func:`check_skill`)."""
    name = check_skill(skill_dir)
    treatment = Treatment(name=name, plugin=plugin_slug(name))
    (dest / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (dest / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": treatment.plugin, "version": "0.0.0"}), encoding="utf-8")
    # Follow links (all inside the skill, see _check_links): a relative one would
    # dangle in the copy. The skill's own evals/ (its prompts and expected
    # outputs) is an answer key, not part of the skill.
    root = str(skill_dir)

    def skip_evals(directory: str, names: list[str]) -> list[str]:
        return [n for n in names if n == "evals"] if directory == root else []

    shutil.copytree(skill_dir, dest / "skills" / treatment.plugin, symlinks=False,
                    ignore=skip_evals)
    return treatment


def treatment_notice(treatment: Optional[Treatment]) -> str:
    return TREATMENT_NOTICE.format(skill_id=treatment.skill_id) if treatment else ""


def skill_listing(skill_dir: Path) -> dict[str, Any]:
    """What of the skill's description the agent's skill listing can show.

    Without the notice the listing entry is all the agent has to find the skill
    by. The run offers this one plugin skill and ``doctor``, so the listing's
    default budget (:data:`LISTING_BUDGET_CHARS`) is not reached; a description
    over :data:`LISTING_MAX_DESC_CHARS` is cut, and ``truncated`` says so.
    """
    path = skill_dir / "SKILL.md"
    meta, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
    text = str(meta.get("description") or "")
    if meta.get("when_to_use") is not None:
        text = f"{text} - {meta['when_to_use']}"
    return {"description_chars": len(text), "max_desc_chars": LISTING_MAX_DESC_CHARS,
            "budget_chars": LISTING_BUDGET_CHARS,
            "truncated": len(text) > LISTING_MAX_DESC_CHARS}


def stream_stats(stream: str, treatment: Optional[Treatment] = None) -> dict[str, Any]:
    """Tool use and skill reach, read from ``claude -p --output-format stream-json``.

    * ``tool_calls`` -- ``tool_use`` blocks by tool name;
    * ``offered_skills`` / ``offered_plugins`` -- what the session's ``init``
      event lists; ``plugin_errors`` when it reports any;
    * ``unexpected_skills`` -- offered skills that are neither the treatment
      nor :data:`_EXPECTED_SKILLS`, so a CLI that adds a skill to both arms
      shows up instead of passing silently;
    * ``n_skill_invocations`` (with a treatment only) -- ``Skill`` calls for the
      treatment, by input or by the ``commandName`` the CLI resolved, whose
      result was not an error. A call the CLI rejected is not evidence the
      skill reached the agent.
    """
    tools: dict[str, int] = {}
    init: Optional[dict] = None
    skill_calls: set[str] = set()
    treatment_calls: set[str] = set()
    failed: set[str] = set()
    for event in _events(stream):
        if event.get("type") == "system" and event.get("subtype") == "init" and init is None:
            init = event
        message = event.get("message")
        blocks = message.get("content") if isinstance(message, dict) else None
        resolved = event.get("tool_use_result")
        command = str(resolved.get("commandName") or "") if isinstance(resolved, dict) else ""
        for block in blocks if isinstance(blocks, list) else []:
            if not isinstance(block, dict):
                continue
            if event.get("type") == "assistant" and block.get("type") == "tool_use":
                tool = str(block.get("name") or "?")
                tools[tool] = tools.get(tool, 0) + 1
                if tool != "Skill":
                    continue
                skill_calls.add(str(block.get("id")))
                inp = block.get("input")
                requested = str(inp.get("skill") or "") if isinstance(inp, dict) else ""
                if treatment and treatment.is_call(requested):
                    treatment_calls.add(str(block.get("id")))
            elif block.get("type") == "tool_result":
                if block.get("is_error"):
                    failed.add(str(block.get("tool_use_id")))
                elif treatment and command and treatment.is_call(command):
                    treatment_calls.add(str(block.get("tool_use_id")))
    stats: dict[str, Any] = {"tool_calls": tools, "offered_skills": None}
    if init is not None:
        skills = init.get("skills")
        offered = [str(s) for s in skills] if isinstance(skills, list) else []
        stats["offered_skills"] = offered
        plugins = init.get("plugins")
        if isinstance(plugins, list):
            stats["offered_plugins"] = [str(p.get("name")) if isinstance(p, dict) else str(p)
                                        for p in plugins]
        if init.get("plugin_errors"):
            stats["plugin_errors"] = init["plugin_errors"]
        unexpected = [s for s in offered if s not in _EXPECTED_SKILLS
                      and not (treatment and s.startswith(f"{treatment.plugin}:"))]
        if unexpected:
            stats["unexpected_skills"] = unexpected
    if treatment is not None:
        stats["n_skill_invocations"] = len((treatment_calls & skill_calls) - failed)
    return stats


def treatment_loaded(stats: dict[str, Any], treatment: Treatment) -> Optional[bool]:
    """Whether the ``init`` event shows the treatment's plugin; ``None`` without one.

    Read from ``init.plugins``, not ``init.skills``: the latter lists what a
    user can invoke, and a ``user-invocable: false`` skill the model can load
    is missing from it. An older CLI with no ``plugins`` field falls back to it.
    """
    if "offered_plugins" in stats:
        return treatment.plugin in stats["offered_plugins"]
    if stats.get("offered_skills") is None:
        return None
    return any(s.startswith(f"{treatment.plugin}:") for s in stats["offered_skills"])


def _events(stream: str) -> Iterator[dict]:
    for line in (stream or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            yield event
