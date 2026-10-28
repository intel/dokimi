# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Detect the skill and rule package formats of other agent ecosystems.

The loader reads one format, the Agent Skills ``SKILL.md`` package
(agentskills.io), which Claude Code, OpenAI Codex, Gemini CLI and GitHub Copilot
all read. Other ecosystems ship guidance in shapes the loader does not load;
this module names them so a catalog scan can say what a checkout holds and
which part of it dokimi can evaluate. Nothing here loads or runs a package.

Formats, and what selects each one at run time:

- ``agent-skill`` -- ``SKILL.md`` with ``name``/``description`` frontmatter; the
  description triggers it. Linted against the agentskills.io field limits. The
  host is read from the path (``.claude/skills``, ``.agents/skills``, ...); a
  Codex skill may add ``agents/openai.yaml`` (display metadata only);
- ``cursor-rule`` -- ``.cursor/rules/*.mdc``; ``alwaysApply``, ``globs`` or (an
  "agent requested" rule) its ``description`` selects it;
- ``copilot-instructions`` -- ``*.instructions.md`` with ``applyTo`` globs, and
  the repo-wide ``.github/copilot-instructions.md``; file globs select them;
- ``gemini-extension`` -- ``gemini-extension.json``: a bundle (context file, MCP
  servers, commands, and skills under ``skills/``), installed, not triggered;
- ``claude-plugin`` -- ``.claude-plugin/plugin.json``: a bundle whose
  ``skills/`` hold agent skills; installed, not triggered.

Only ``agent-skill`` is loadable (``discover_skills``); the other rows are
reported so a reader sees what a description-trigger eval does not cover.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from dokimi.skills.loader import VENDORED_DIR_NAMES, skill_files
from dokimi.utils import parse_frontmatter

FORMAT_AGENT_SKILL = "agent-skill"
FORMAT_CURSOR_RULE = "cursor-rule"
FORMAT_COPILOT = "copilot-instructions"
FORMAT_GEMINI_EXTENSION = "gemini-extension"
FORMAT_CLAUDE_PLUGIN = "claude-plugin"

# agentskills.io/specification: name 1-64 chars of [a-z0-9-], no leading,
# trailing or doubled hyphen, equal to the directory name; description 1-1024.
_SPEC_NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
SPEC_NAME_MAX = 64
SPEC_DESCRIPTION_MAX = 1024

# Path markers of the hosts that read SKILL.md, first match wins.
_HOSTS = (
    (".claude/skills", "claude-code"),
    (".codex/skills", "codex"),
    (".gemini/skills", "gemini-cli"),
    (".github/skills", "copilot"),
    (".agents/skills", "agents"),  # the cross-tool alias Codex and Gemini CLI read
    ("environment/skills", "task-bundled"),  # a SkillsBench / Harbor task's own skills
)
_SKIP_DIRS = VENDORED_DIR_NAMES  # the same dependency and VCS dirs discover_skills does not walk


@dataclass
class SkillPackage:
    """One package found under a root."""

    path: Path
    format: str
    name: str = ""
    description: str = ""
    selected_by: str = ""  # description | globs | always | install
    loadable: bool = False
    host: Optional[str] = None
    problems: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self, root: Optional[Path] = None) -> Dict[str, Any]:
        path = self.path.relative_to(root) if root else self.path
        return {"path": path.as_posix(), "format": self.format, "name": self.name,
                "selected_by": self.selected_by, "loadable": self.loadable, "host": self.host,
                "description_chars": len(self.description), "problems": list(self.problems),
                "details": dict(self.details)}


def _frontmatter(text: str) -> Dict[str, Any]:
    """Frontmatter, or flat ``key: value`` lines where YAML refuses it.

    Cursor rules write ``globs: **/*``, which YAML reads as an alias and rejects.
    """
    meta, _ = parse_frontmatter(text)
    if meta or not text.startswith("---"):
        return meta
    flat: Dict[str, Any] = {}
    for line in text.splitlines()[1:]:
        if line.strip() == "---":
            break
        key, sep, value = line.partition(":")
        if sep and key.strip() and not key.startswith(" "):
            flat[key.strip()] = value.strip().strip("'\"")
    return flat


def _host(path: Path) -> Optional[str]:
    posix = path.as_posix()
    for marker, host in _HOSTS:
        if f"{marker}/" in posix:
            return host
    return None


def lint_agent_skill(meta: Dict[str, Any], skill_file: Path) -> List[str]:
    """Where a SKILL.md breaks the agentskills.io field limits."""
    problems = []
    name = str(meta.get("name") or "")
    desc = str(meta.get("description") or "")
    if not name:
        problems.append("no name")
    elif len(name) > SPEC_NAME_MAX or not _SPEC_NAME.match(name):
        problems.append(f"name {name!r} is not 1-{SPEC_NAME_MAX} chars of a-z, 0-9, single hyphens")
    elif name != skill_file.parent.name:
        problems.append(f"name {name!r} differs from its directory {skill_file.parent.name!r}")
    if not desc.strip():
        problems.append("no description")
    elif len(desc) > SPEC_DESCRIPTION_MAX:
        problems.append(f"description is {len(desc)} chars, over {SPEC_DESCRIPTION_MAX}")
    return problems


def _agent_skill(path: Path) -> SkillPackage:
    meta = _frontmatter(path.read_text(encoding="utf-8", errors="replace"))
    pkg = SkillPackage(path, FORMAT_AGENT_SKILL, str(meta.get("name") or path.parent.name),
                       str(meta.get("description") or ""), "description",
                       loadable=bool(str(meta.get("description") or "").strip()), host=_host(path))
    pkg.problems = lint_agent_skill(meta, path)
    if (path.parent / "agents" / "openai.yaml").is_file():
        pkg.details["codex_metadata"] = "agents/openai.yaml"
    return pkg


def _cursor_rule(path: Path) -> SkillPackage:
    meta = _frontmatter(path.read_text(encoding="utf-8", errors="replace"))
    always = str(meta.get("alwaysApply", "")).lower() == "true"
    globs = meta.get("globs")
    desc = str(meta.get("description") or "")
    selected = "always" if always else "globs" if globs else "description" if desc else "manual"
    pkg = SkillPackage(path, FORMAT_CURSOR_RULE, path.stem, desc, selected)
    if globs:
        pkg.details["globs"] = globs if isinstance(globs, (str, list)) else str(globs)
    return pkg


def _copilot(path: Path) -> SkillPackage:
    meta = _frontmatter(path.read_text(encoding="utf-8", errors="replace"))
    name = path.name.removesuffix(".instructions.md").removesuffix(".md")
    pkg = SkillPackage(path, FORMAT_COPILOT, name, str(meta.get("description") or ""),
                       "globs" if meta.get("applyTo") else "always")
    if meta.get("applyTo"):
        pkg.details["applyTo"] = str(meta["applyTo"])
    return pkg


def _manifest(path: Path, fmt: str) -> SkillPackage:
    pkg = SkillPackage(path, fmt, selected_by="install")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        pkg.problems.append(f"unreadable JSON: {exc}")
        return pkg
    if not isinstance(data, dict):
        pkg.problems.append("manifest is not an object")
        return pkg
    pkg.name, pkg.description = str(data.get("name") or ""), str(data.get("description") or "")
    if not pkg.name:
        pkg.problems.append("no name")
    bundle = path.parent if fmt == FORMAT_GEMINI_EXTENSION else path.parent.parent
    pkg.details["skills"] = len(list((bundle / "skills").glob("*/SKILL.md")))
    if fmt == FORMAT_GEMINI_EXTENSION:
        pkg.details["context_file"] = data.get("contextFileName") or (
            "GEMINI.md" if (bundle / "GEMINI.md").is_file() else None)
        servers = data.get("mcpServers") or {}
        if isinstance(servers, dict):
            pkg.details["mcp_servers"] = len(servers)
        else:
            pkg.problems.append("mcpServers is not an object")
    return pkg


def classify(path: Path) -> Optional[SkillPackage]:
    """The package *path* is, or ``None``."""
    name = path.name
    if name == "SKILL.md":
        return _agent_skill(path)
    if name.endswith(".mdc"):  # Cursor's own extension
        return _cursor_rule(path)
    if name.endswith(".instructions.md") or (
            name == "copilot-instructions.md" and path.parent.name == ".github"):
        return _copilot(path)
    if name == "gemini-extension.json":
        return _manifest(path, FORMAT_GEMINI_EXTENSION)
    if name == "plugin.json" and path.parent.name == ".claude-plugin":
        return _manifest(path, FORMAT_CLAUDE_PLUGIN)
    return None


def detect_skill_packages(root) -> List[SkillPackage]:
    """Every package under *root*, in path order (VCS and dependency dirs skipped)."""
    root = Path(root)
    kept = set(skill_files(root))  # a template or a mirror is not loadable, as discover_skills
    found = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or _SKIP_DIRS.intersection(path.relative_to(root).parts):
            continue
        pkg = classify(path)
        if pkg is not None:
            pkg.loadable = pkg.loadable and path in kept
            found.append(pkg)
    return found


def summarize(packages: List[SkillPackage]) -> Dict[str, Any]:
    """Counts per format, how each is selected, and spec problems."""
    out: Dict[str, Any] = {}
    for p in packages:
        row = out.setdefault(p.format, {"n": 0, "loadable": 0, "with_problems": 0,
                                        "selected_by": {}, "hosts": {}})
        row["n"] += 1
        row["loadable"] += p.loadable
        row["with_problems"] += bool(p.problems)
        row["selected_by"][p.selected_by] = row["selected_by"].get(p.selected_by, 0) + 1
        if p.host:
            row["hosts"][p.host] = row["hosts"].get(p.host, 0) + 1
    return out
