# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""End-to-end wiring: catalog-discovered external skills/tasks feed real CLI
commands (`tasks run`, `docker-claude-skill:<path>`) with no Docker required.

Docker itself is exercised separately in CI (`external-catalog-smoke` in
.github/workflows/agent-quality.yml) against a real uxlfoundation/skills
clone -- these tests cover the Python-side wiring: that a path a catalog
scan reports as loaded is actually usable, unmodified, by the harness
registry and the `tasks run` CLI.
"""

from __future__ import annotations

from pathlib import Path

from dokimi.cli import build_parser
from dokimi.external import read_skill_manifest, read_task_manifest
from dokimi.harnesses.registry import create_harness


def _write_external_catalog(root: Path) -> None:
    """A minimal uxlfoundation/skills-shaped external catalog."""

    skill_dir = root / "skills" / "uxl-onetbb"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: uxl-onetbb\ndescription: Use for oneTBB parallelism work.\n---\n"
        "# UXL oneTBB\nPrefer parallel_reduce over shared mutation.\n",
        encoding="utf-8",
    )

    task_dir = root / "evaluation" / "harbor" / "tasks" / "onetbb-histogram-local-aggregation"
    (task_dir / "environment").mkdir(parents=True)
    (task_dir / "tests").mkdir()
    (task_dir / "solution").mkdir()
    (task_dir / "instruction.md").write_text(
        "Parallelize histogram accumulation without a data race.", encoding="utf-8"
    )
    (task_dir / "task.toml").write_text(
        '[metadata]\ndifficulty = "medium"\n\n[agent]\ntimeout_sec = 300\n\n'
        '[verifier]\ntimeout_sec = 30\n\n[environment]\n',
        encoding="utf-8",
    )

    (root / "skills.yaml").write_text(
        'schema_version: "1"\n'
        'skills:\n'
        '  - name: "uxl-onetbb"\n'
        '    status: "incubating"\n'
        '    catalog_path: "skills/uxl-onetbb"\n',
        encoding="utf-8",
    )
    (root / "evaluation" / "harbor").mkdir(parents=True, exist_ok=True)
    (root / "evaluation" / "harbor" / "suites.json").write_text(
        """
        {
          "schema_version": "1.0",
          "suites": [
            {
              "skill": "uxl-onetbb",
              "tasks": [
                {"name": "onetbb-histogram-local-aggregation", "status": "implemented", "track": "executable"}
              ]
            }
          ]
        }
        """,
        encoding="utf-8",
    )


def test_catalog_discovered_skill_path_builds_a_docker_claude_skill_harness(tmp_path):
    _write_external_catalog(tmp_path)

    skills = read_skill_manifest(tmp_path)
    assert len(skills) == 1 and skills[0].loaded

    # The exact path catalog check reports plugs straight into the harness
    # alias, unmodified -- no extra copy/rewrite step.
    harness = create_harness(f"docker-claude-skill:{skills[0].skill.path}")
    assert harness.skill_path == str(skills[0].skill.path)


def test_catalog_discovered_task_runs_through_tasks_cli_dry_run(tmp_path):
    _write_external_catalog(tmp_path)

    tasks = read_task_manifest(tmp_path)
    assert len(tasks) == 1 and tasks[0].loaded
    task_name = tasks[0].name

    skills = read_skill_manifest(tmp_path)
    skill_path = skills[0].skill.path

    out = tmp_path / "runs.json"
    parser = build_parser()
    args = parser.parse_args([
        "tasks", "run",
        "--tasks", task_name,
        "--tasks-root", str(tmp_path / "evaluation" / "harbor" / "tasks"),
        "--harnesses", f"docker-claude-skill:{skill_path},docker-oracle",
        "--baseline-harness", "docker-oracle",
        "--out-json", str(out),
        "--dry-run",
    ])
    args.func(args)

    text = out.read_text(encoding="utf-8")
    assert task_name in text
    assert str(skill_path).replace("\\", "\\\\") in text or str(skill_path) in text
