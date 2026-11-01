# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Synthetic skill-repo layouts, one per shape seen in public skill catalogs.

Each shape writes a small invented repo under a directory: skill names, descriptions and
bodies are made up here, so no third-party text enters the suite. ``build_shape`` returns
the catalog root for a shape; ``PROBE_QUERIES`` are requests that name no skill in particular.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

# Over the 1024-character limit of a skill description.
LONG_DESCRIPTION = "Use when polishing widgets for a demo. " + "It covers polish steps. " * 45

PROBE_QUERIES = [
    "polish a widget before a demo",
    "check a door latch by hand",
    "lint a gizmo config",
]


def skill_md(name: str, description: str = "Use when polishing widgets before a demo.", *,
             body: str | None = None, extra: str = "") -> str:
    """A SKILL.md with frontmatter ``name``/``description``, *extra* lines, and a body."""
    if body is None:
        body = f"# {name}\n\n## Steps\n\nRun the {name} steps in order.\n"
    return f"---\nname: {name}\ndescription: {description}\n{extra}---\n\n{body}"


def _put(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _door_check(root: Path) -> None:
    _put(root, "skills/door-check/SKILL.md",
         skill_md("door-check", "Use when checking a door latch by hand."))


def _widget_polish(root: Path) -> None:
    _put(root, "skills/widget-polish/SKILL.md", skill_md("widget-polish"))


def byte_identical_mirror(root: Path) -> None:
    text = skill_md("widget-polish")
    _put(root, "skills/widget-polish/SKILL.md", text)
    _put(root, "plugins/kit/skills/widget-polish/SKILL.md", text)
    _door_check(root)


def drifted_mirror(root: Path) -> None:
    _widget_polish(root)
    _put(root, "plugins/kit/skills/widget-polish/SKILL.md",
         skill_md("widget-polish", body="# widget-polish\n\n## Steps\n\nRun the steps, then the "
                                        "extra step.\n"))
    _door_check(root)


def same_name_distinct_dirs(root: Path) -> None:
    _widget_polish(root)
    _put(root, "packs/widget-polish/SKILL.md",
         skill_md("widget-polish", "Use when buffing widget casings.",
                  body="# widget-polish\n\n## Buff\n\nBuff the casing.\n"))
    _door_check(root)


def disable_model_invocation(root: Path) -> None:
    _put(root, "skills/gizmo-lint/SKILL.md",
         skill_md("gizmo-lint", "Use when linting a gizmo config by hand.",
                  extra="disable-model-invocation: true\n"))
    _widget_polish(root)
    _door_check(root)


def template_dir(root: Path) -> None:
    _put(root, "template/SKILL.md",
         skill_md("skill-name", "Use when starting a new skill from the scaffold."))
    _widget_polish(root)
    _door_check(root)


def vendored_dir(root: Path) -> None:
    _widget_polish(root)
    _door_check(root)
    _put(root, "node_modules/kit/skills/door-check/SKILL.md",
         skill_md("door-check", "Use when checking a door latch by hand."))
    _put(root, ".venv/lib/python3.12/site-packages/gizmo-lint/SKILL.md",
         skill_md("gizmo-lint", "Use when linting a gizmo config by hand."))


def zero_skill(root: Path) -> None:
    _put(root, "README.md", "# Empty catalog\n\nNo skills here yet.\n")
    _put(root, "docs/guide.md", "# Guide\n\nNothing to see.\n")


def plugins_nesting(root: Path) -> None:
    _put(root, "plugins/kit/.claude-plugin/plugin.json", '{"name": "kit", "version": "0.1.0"}\n')
    _put(root, "plugins/kit/skills/widget-polish/SKILL.md", skill_md("widget-polish"))
    _put(root, "plugins/kit/skills/door-check/SKILL.md",
         skill_md("door-check", "Use when checking a door latch by hand."))
    _put(root, "plugins/tools/skills/gizmo-lint/SKILL.md",
         skill_md("gizmo-lint", "Use when linting a gizmo config by hand."))


def description_too_long(root: Path) -> None:
    _put(root, "skills/widget-polish/SKILL.md", skill_md("widget-polish", LONG_DESCRIPTION))
    _door_check(root)


def name_not_dir(root: Path) -> None:
    _put(root, "skills/widget-polish-old/SKILL.md", skill_md("widget-polish"))
    _door_check(root)


def title_case_name(root: Path) -> None:
    _put(root, "skills/widget-polish/SKILL.md", skill_md("Widget Polish"))
    _door_check(root)


def links_escape(root: Path) -> None:
    body = ("# widget-polish\n\n## Steps\n\nSee [the shared guide](../../shared/guide.md) and "
            "[the tables](../../references/tables.md).\n")
    _put(root, "skills/widget-polish/SKILL.md", skill_md("widget-polish", body=body))
    _put(root, "shared/guide.md", "# Shared guide\n")
    _door_check(root)


def no_usable_headings(root: Path) -> None:
    _widget_polish(root)
    _put(root, "skills/door-check/SKILL.md",
         skill_md("door-check", "Use when checking a door latch by hand.",
                  body="Check the latch by hand. Then check the hinge.\n"))


SHAPES: dict[str, Callable[[Path], None]] = {
    "byte_identical_mirror": byte_identical_mirror,
    "drifted_mirror": drifted_mirror,
    "same_name_distinct_dirs": same_name_distinct_dirs,
    "disable_model_invocation": disable_model_invocation,
    "template_dir": template_dir,
    "vendored_dir": vendored_dir,
    "zero_skill": zero_skill,
    "plugins_nesting": plugins_nesting,
    "description_too_long": description_too_long,
    "name_not_dir": name_not_dir,
    "title_case_name": title_case_name,
    "links_escape": links_escape,
    "no_usable_headings": no_usable_headings,
}


def build_shape(name: str, root: Path) -> Path:
    """Write the shape *name* under *root* (created) and return it, the catalog root."""
    root.mkdir(parents=True, exist_ok=True)
    SHAPES[name](root)
    return root


def probe_artifact(root: Path) -> Path:
    """A skill outside every catalog, so the artifact never collides with a competitor name."""
    skill_dir = root / "probe-artifact"
    _put(skill_dir.parent, "probe-artifact/SKILL.md",
         skill_md("probe-artifact", "Use when probing routing against a catalog."))
    return skill_dir
