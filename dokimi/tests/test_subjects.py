# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for subject descriptors and scorecards."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import dokimi.eval.arm_runner as arm_runner_mod
from dokimi import __version__
from dokimi.artifacts import validate_artifact
from dokimi.cli import build_parser
from dokimi.commands.subjects import (
    _baseline_arm_name,
    _matrix_config_for_run,
    _run_subject_work,
    _safe_subject_id,
    _validate_subject_work_args,
    _validate_unique_awareness_arm_names,
    _validate_unique_product_output_dirs,
)
from dokimi.report.subject_report import render_subject_scorecard
from dokimi.subjects import build_subject_scorecard, load_subject
from dokimi.subjects.novelty_track import CAVEATS as NOVELTY_CAVEATS
from dokimi.subjects.loader import awareness_arm_specs, matrix_config, work_arm_specs


def test_load_subject_maps_skill_to_awareness_and_work_arms(tmp_path: Path):
    descriptor_path = tmp_path / "subject.toml"
    descriptor_path.write_text(
        """
baseline = "baseline"

[subject]
id = "skill-subject"
kind = "skill"
ref = "data/skills/example"

[suite]
products = ["oneTBB"]
questions = ["questions.json"]
tasks = ["tasks/example"]

[[matrix.cells]]
id = "agent-none"
model = "m"
provider = "openai"
harness = "agent"
plugins = []
""".strip(),
        encoding="utf-8",
    )

    descriptor = load_subject(descriptor_path)

    assert descriptor.id == "skill-subject"
    assert awareness_arm_specs(descriptor) == ["baseline", "skill:data/skills/example"]
    assert work_arm_specs(descriptor) == ["baseline", "skill-agent:data/skills/example"]
    assert matrix_config(descriptor)["matrix"]["cells"][0]["id"] == "agent-none"


def test_subjects_show_parser_accepts_descriptor(tmp_path: Path):
    """No descriptor is committed any more — a subject points at content supplied by path —
    so the parser is handed one written here rather than a repo-relative literal that would
    keep this test green while naming a file that does not exist."""
    descriptor_path = tmp_path / "subject.toml"
    descriptor_path.write_text(
        """
baseline = "baseline"

[subject]
id = "demo-skill"
kind = "skill"
ref = "/path/to/skills-repo/skills/demo-skill"

[suite]
products = ["demo"]
questions = ["/path/to/content-repo/questions/demo_golden.json"]
tasks = []
""".strip(),
        encoding="utf-8",
    )

    args = build_parser().parse_args(["subjects", "show", str(descriptor_path)])

    assert args.cmd == "subjects"
    assert args.subjects_cmd == "show"
    assert Path(args.subject) == descriptor_path
    assert load_subject(descriptor_path).id == "demo-skill"


def test_mcp_subject_normalizes_transport_prefix(tmp_path: Path):
    descriptor_path = tmp_path / "subject.toml"
    descriptor_path.write_text(
        """
[subject]
id = "mcp-subject"
kind = "mcp"
ref = "mcp:http=https://mcp.context7.com/mcp"

[suite]
products = ["oneTBB"]
questions = ["questions.json"]
""".strip(),
        encoding="utf-8",
    )

    descriptor = load_subject(descriptor_path)

    assert awareness_arm_specs(descriptor) == ["baseline", "mcp:http=https://mcp.context7.com/mcp"]
    assert work_arm_specs(descriptor) == ["baseline", "agent:mcp:http=https://mcp.context7.com/mcp"]


def test_subject_run_default_cell_honors_cli_model_provider(tmp_path: Path):
    descriptor_path = tmp_path / "subject.toml"
    descriptor_path.write_text(
        """
[subject]
id = "docs-subject"
kind = "doc-source"
ref = "local:docs"

[suite]
products = ["oneTBB"]
questions = ["questions.json"]
""".strip(),
        encoding="utf-8",
    )
    descriptor = load_subject(descriptor_path)
    args = build_parser().parse_args([
        "subjects",
        "run",
        str(descriptor_path),
        "--model",
        "custom-model",
        "--provider",
        "openrouter",
    ])

    cell = _matrix_config_for_run(descriptor, args)["matrix"]["cells"][0]

    assert cell["model"] == "custom-model"
    assert cell["provider"] == "openrouter"


def test_subject_run_derives_baseline_arm_name_from_spec(tmp_path: Path):
    docs = tmp_path / "docs.md"
    docs.write_text("# Docs\n", encoding="utf-8")
    args = build_parser().parse_args([
        "subjects",
        "run",
        "subject.toml",
    ])

    assert _baseline_arm_name(f"docs:local:{docs}", args) == "docs"


def test_bundle_with_first_mcp_keeps_later_members(tmp_path: Path):
    descriptor_path = tmp_path / "subject.toml"
    descriptor_path.write_text(
        """
[subject]
id = "bundle-subject"
kind = "bundle"
members = [
  {kind = "mcp", ref = "mcp:http=https://mcp.context7.com/mcp"},
  {kind = "skill", ref = "data/skills/example"},
]

[suite]
products = ["oneTBB"]
questions = ["questions.json"]
""".strip(),
        encoding="utf-8",
    )

    descriptor = load_subject(descriptor_path)

    assert awareness_arm_specs(descriptor) == [
        "baseline",
        "skill:data/skills/example+mcp:http=https://mcp.context7.com/mcp",
    ]


def test_all_mcp_bundle_is_rejected_until_names_are_unique(tmp_path: Path):
    descriptor_path = tmp_path / "subject.toml"
    descriptor_path.write_text(
        """
[subject]
id = "bundle-subject"
kind = "bundle"
members = [
  {kind = "mcp", ref = "mcp:http=https://mcp.context7.com/a"},
  {kind = "mcp", ref = "mcp:http=https://mcp.context7.com/b"},
]

[suite]
products = ["oneTBB"]
questions = ["questions.json"]
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="all-MCP bundles are not supported"):
        load_subject(descriptor_path)


def test_subject_rejects_suite_scoped_baseline(tmp_path: Path):
    descriptor_path = tmp_path / "subject.toml"
    descriptor_path.write_text(
        """
[subject]
id = "docs-subject"
kind = "doc-source"
ref = "local:docs"

[suite]
products = ["oneTBB"]
questions = ["questions.json"]
baseline = "docs:local:old"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="baseline must be top-level"):
        load_subject(descriptor_path)


def test_subject_scorecard_validates_and_renders(tmp_path: Path):
    descriptor_path = tmp_path / "subject.toml"
    descriptor_path.write_text(
        """
[subject]
id = "docs-subject"
kind = "doc-source"
ref = "local:docs"

[suite]
products = ["oneTBB"]
questions = ["questions.json"]
""".strip(),
        encoding="utf-8",
    )
    descriptor = load_subject(descriptor_path)
    rollup = {
        "schema_version": "matrix_rollup.v1",
        "library_name": "oneTBB",
        "cells": [
            {
                "matrix_cell": "agent-none",
                "artifact": "out/agent-none.json",
                "model": "m",
                "provider": "openai",
                "harness": "agent",
                "plugin_set": "none",
                "summary": {},
                "cost_summary": {},
            }
        ],
        "plugin_deltas": [],
        "warnings": [],
    }

    scorecard = build_subject_scorecard(
        descriptor,
        awareness_runs=[{
            "product": "oneTBB",
            "questions": "questions.json",
            "matrix_rollup": "out/matrix-rollup.json",
            "rollup": rollup,
        }],
        out_dir=tmp_path,
    )

    validate_artifact("subject_scorecard", scorecard)
    assert scorecard["run_manifest"]["dokimi_version"] == __version__
    md = render_subject_scorecard(scorecard)
    assert "# Subject scorecard: docs-subject" in md
    assert "`out/matrix-rollup.json`" in md


def _minimal_subject(tmp_path: Path):
    descriptor_path = tmp_path / "subject.toml"
    descriptor_path.write_text(
        """
[subject]
id = "docs-subject"
kind = "doc-source"
ref = "local:docs"

[suite]
products = ["oneTBB"]
questions = ["questions.json"]
""".strip(),
        encoding="utf-8",
    )
    return load_subject(descriptor_path)


def _novelty_artifact(**overrides):
    artifact = {
        "artifact": {"ref": "skill:skills/accel-tuning", "kind": "skill"},
        "model": "m",
        "provider": "openai",
        "judge_model": "j",
        "atoms": {"n": 40, "n_probed": 40, "sampling": "all"},
        "paired": {
            "lenient": {"added": 0.42, "n_paired": 40},
            "strict": {"added": 0.30, "n_paired": 40},
        },
        "k0": {"lenient": 0.5, "strict": 0.6},
        "categories": {"harmful_conflicting": 0},
        "spans": {"n": 60, "tokens": 900, "frac_carrying_nothing": 0.12,
                  "capped_atom_set": False},
        "shortening": [],
        "attribution": {},
        "warnings": [],
    }
    artifact.update(overrides)
    return artifact


def test_attached_novelty_artifacts_land_on_the_scorecard_per_cell(tmp_path: Path):
    scorecard = build_subject_scorecard(
        _minimal_subject(tmp_path),
        awareness_runs=[],
        out_dir=tmp_path,
        novelty_artifacts=[_novelty_artifact(), _novelty_artifact(model="m2")],
        novelty_sources=["a.json", "b.json"],
    )

    validate_artifact("subject_scorecard", scorecard)
    assert scorecard["schema_version"] == "subject_scorecard.v2"
    assert scorecard["novelty"]["status"] == "measured"
    assert [cell["model"] for cell in scorecard["novelty"]["cells"]] == ["m", "m2"]

    md = render_subject_scorecard(scorecard)
    assert "## Novelty" in md
    assert "0.420 / 0.300" in md
    assert "not comparable across cells" in md


def test_a_novelty_cell_warning_becomes_a_subject_warning(tmp_path: Path):
    scorecard = build_subject_scorecard(
        _minimal_subject(tmp_path),
        awareness_runs=[],
        out_dir=tmp_path,
        novelty_artifacts=[_novelty_artifact(categories={"harmful_conflicting": 3})],
    )

    validate_artifact("subject_scorecard", scorecard)
    assert any("harmful_atoms" in warning for warning in scorecard["warnings"])
    md = render_subject_scorecard(scorecard)
    assert "Soft-gate warnings (none of these block)" in md
    assert scorecard["novelty"]["cells"][0]["gate"]["blocking"] is False


def test_a_novelty_cell_that_skipped_stages_does_not_render_a_bare_pass(tmp_path: Path):
    scorecard = build_subject_scorecard(
        _minimal_subject(tmp_path), awareness_runs=[], out_dir=tmp_path,
        novelty_artifacts=[_novelty_artifact(categories={})],
    )
    validate_artifact("subject_scorecard", scorecard)
    assert "| PASS (8 not measured) |" in render_subject_scorecard(scorecard)


def test_a_novelty_cell_marks_the_text_extraction_never_read(tmp_path: Path):
    partial = {"n": 50, "tokens": 900, "frac_carrying_nothing": 0.0, "capped_atom_set": False,
               "n_unread": 49, "tokens_unread": 882}
    scorecard = build_subject_scorecard(
        _minimal_subject(tmp_path), awareness_runs=[], out_dir=tmp_path,
        novelty_artifacts=[_novelty_artifact(spans=partial), _novelty_artifact()],
    )
    validate_artifact("subject_scorecard", scorecard)
    cells = scorecard["novelty"]["cells"]
    assert (cells[0]["spans"]["n_unread"], cells[0]["spans"]["tokens_unread"]) == (49, 882)
    assert "dead_tokens" in cells[0]["gate"]["not_measured"]
    md = render_subject_scorecard(scorecard)
    assert "| 0.000 (98% unread) |" in md
    assert "| 0.120 |" in md  # a fully read cell carries no marker
    tiny = dict(partial, n_unread=1, tokens_unread=3)
    scorecard = build_subject_scorecard(
        _minimal_subject(tmp_path), awareness_runs=[], out_dir=tmp_path,
        novelty_artifacts=[_novelty_artifact(spans=tiny)],
    )
    assert "| 0.000 (<1% unread) |" in render_subject_scorecard(scorecard)


def test_a_novelty_artifact_warning_reaches_the_scorecard_warnings(tmp_path: Path):
    note = "judge and answerer are the same model (--allow-self-judge): scores are inflated"
    scorecard = build_subject_scorecard(
        _minimal_subject(tmp_path), awareness_runs=[], out_dir=tmp_path,
        novelty_artifacts=[_novelty_artifact(warnings=[note])],
    )
    assert f"novelty [skill:skills/accel-tuning on m]: {note}" in scorecard["warnings"]
    assert note in render_subject_scorecard(scorecard)


def test_a_novelty_cell_keeps_the_run_configuration(tmp_path: Path):
    run_config = {"seed": 7, "paraphrases": False}
    scorecard = build_subject_scorecard(
        _minimal_subject(tmp_path), awareness_runs=[], out_dir=tmp_path,
        novelty_artifacts=[_novelty_artifact(run_config=run_config), _novelty_artifact()],
    )
    validate_artifact("subject_scorecard", scorecard)
    cells = scorecard["novelty"]["cells"]
    assert cells[0]["run_config"] == run_config
    assert "run_config" not in cells[1]


def test_a_scorecard_without_novelty_renders_the_not_measured_line(tmp_path: Path):
    scorecard = build_subject_scorecard(
        _minimal_subject(tmp_path), awareness_runs=[], out_dir=tmp_path
    )
    md = render_subject_scorecard(scorecard)
    assert "## Novelty" in md
    assert "not measured" in md


def test_subject_scorecard_embeds_work_task_run_summary(tmp_path: Path):
    descriptor_path = tmp_path / "subject.toml"
    descriptor_path.write_text(
        """
[subject]
id = "skill-subject"
kind = "skill"
ref = "data/skills/example"

[suite]
products = ["oneTBB"]
questions = ["questions.json"]
tasks = ["tasks-root/example"]
""".strip(),
        encoding="utf-8",
    )
    descriptor = load_subject(descriptor_path)
    scorecard = build_subject_scorecard(
        descriptor,
        awareness_runs=[],
        out_dir=tmp_path,
        work_run={
            "status": "run",
            "tasks": ["tasks-root/example"],
            "arm_specs": ["baseline", "skill-agent:data/skills/example"],
            "harnesses": ["codex"],
            "task_runs": "out/task-runs.json",
            "task_report": "out/task-runs.md",
            "summary": {
                "per_harness": {
                    "codex": {
                        "n": 1,
                        "passed": 0,
                        "pass_rate": 0.0,
                    }
                }
            },
        },
    )

    validate_artifact("subject_scorecard", scorecard)
    assert scorecard["work"]["status"] == "run"
    assert scorecard["work"]["task_runs"] == "out/task-runs.json"
    md = render_subject_scorecard(scorecard)
    assert "## Work runs" in md
    assert "`out/task-runs.json`" in md
    assert "| Harness | Pass rate | Passed | Scored | Errored |" in md
    assert "| `codex` | 0.00% | 0 | 1 |" in md


def test_subject_rejects_subject_scoped_baseline(tmp_path: Path):
    descriptor_path = tmp_path / "subject.toml"
    descriptor_path.write_text(
        """
[subject]
id = "docs-subject"
kind = "doc-source"
ref = "local:docs"
baseline = "docs:local:old"

[suite]
products = ["oneTBB"]
questions = ["questions.json"]
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="baseline must be top-level"):
        load_subject(descriptor_path)


def test_subject_rejects_empty_top_level_baseline(tmp_path: Path):
    descriptor_path = tmp_path / "subject.toml"
    descriptor_path.write_text(
        """
baseline = ""

[subject]
id = "docs-subject"
kind = "doc-source"
ref = "local:docs"

[suite]
products = ["oneTBB"]
questions = ["questions.json"]
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="baseline must not be empty"):
        load_subject(descriptor_path)


def test_subject_rejects_explicit_empty_matrix_cells(tmp_path: Path):
    descriptor_path = tmp_path / "subject.toml"
    descriptor_path.write_text(
        """
[subject]
id = "docs-subject"
kind = "doc-source"
ref = "local:docs"

[suite]
products = ["oneTBB"]
questions = ["questions.json"]

[matrix]
cells = []
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="matrix.cells must contain at least one cell"):
        load_subject(descriptor_path)


def test_subject_rejects_explicit_empty_matrix_table(tmp_path: Path):
    descriptor_path = tmp_path / "subject.toml"
    descriptor_path.write_text(
        """
[subject]
id = "docs-subject"
kind = "doc-source"
ref = "local:docs"

[suite]
products = ["oneTBB"]
questions = ["questions.json"]

[matrix]
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="matrix must declare cells"):
        load_subject(descriptor_path)


def test_subject_scorecard_hashes_local_baseline_content(tmp_path: Path):
    docs = tmp_path / "old-docs.md"
    docs.write_text("version one", encoding="utf-8")
    descriptor_path = tmp_path / "subject.toml"
    descriptor_path.write_text(
        f"""
baseline = "docs:local:{docs.as_posix()}"

[subject]
id = "docs-subject"
kind = "doc-source"
ref = "local:docs"

[suite]
products = ["oneTBB"]
questions = ["questions.json"]
""".strip(),
        encoding="utf-8",
    )
    descriptor = load_subject(descriptor_path)

    first = build_subject_scorecard(descriptor, awareness_runs=[], out_dir=tmp_path)["baseline_digest"]
    docs.write_text("version two", encoding="utf-8")
    second = build_subject_scorecard(descriptor, awareness_runs=[], out_dir=tmp_path)["baseline_digest"]

    assert first != second


def test_subject_scorecard_hashes_local_baseline_path_with_plus(tmp_path: Path):
    docs = tmp_path / "old+b.md"
    docs.write_text("version one", encoding="utf-8")
    descriptor_path = tmp_path / "subject.toml"
    descriptor_path.write_text(
        f"""
baseline = "docs:local:{docs.as_posix()}"

[subject]
id = "docs-subject"
kind = "doc-source"
ref = "local:docs"

[suite]
products = ["oneTBB"]
questions = ["questions.json"]
""".strip(),
        encoding="utf-8",
    )
    descriptor = load_subject(descriptor_path)

    first = build_subject_scorecard(descriptor, awareness_runs=[], out_dir=tmp_path)["baseline_digest"]
    docs.write_text("version two", encoding="utf-8")
    second = build_subject_scorecard(descriptor, awareness_runs=[], out_dir=tmp_path)["baseline_digest"]

    assert first != second


def test_subjects_reject_colliding_product_output_dirs():
    with pytest.raises(ValueError, match="collide after output directory sanitization"):
        _validate_unique_product_output_dirs(("one/TBB", "one-TBB"))


def test_subjects_reject_dot_only_product_output_dirs():
    with pytest.raises(ValueError, match="must not resolve"):
        _validate_unique_product_output_dirs(("..",))


def test_subjects_reject_duplicate_runtime_arm_names(tmp_path: Path):
    old_docs = tmp_path / "old.md"
    new_docs = tmp_path / "new.md"
    old_docs.write_text("old", encoding="utf-8")
    new_docs.write_text("new", encoding="utf-8")
    args = build_parser().parse_args(["subjects", "run", "subject.toml"])

    with pytest.raises(ValueError, match="duplicate runtime names"):
        _validate_unique_awareness_arm_names(
            [f"docs:local:{old_docs}", f"docs:local:{new_docs}"],
            args,
        )


def test_subjects_default_out_dir_sanitizes_subject_id(tmp_path: Path, monkeypatch):
    def fake_matrix_run(config, *, out_dir, **kw):
        run_dir = Path(out_dir)
        run_dir.mkdir(parents=True)
        (run_dir / "matrix-rollup.json").write_text(json.dumps({
            "schema_version": "matrix_rollup.v1",
            "library_name": config.product,
            "cells": [],
            "plugin_deltas": [],
            "warnings": [],
        }), encoding="utf-8")

    monkeypatch.setattr("dokimi.commands.arms.run_matrix", fake_matrix_run)
    descriptor_path = tmp_path / "subject.toml"
    questions = tmp_path / "questions.json"
    questions.write_text(json.dumps([{"id": "q1", "question": "Q?"}]), encoding="utf-8")
    descriptor_path.write_text(
        f"""
[subject]
id = "../unsafe"
kind = "doc-source"
ref = "local:docs"

[suite]
products = ["oneTBB"]
questions = ["{questions.as_posix()}"]
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    args = build_parser().parse_args(["subjects", "run", str(descriptor_path)])

    args.func(args)

    assert (tmp_path / "results" / "subjects" / "..-unsafe" / "subject-scorecard.json").is_file()
    assert not (tmp_path / "results" / "unsafe").exists()


def test_safe_subject_id_rejects_dot_dot():
    with pytest.raises(ValueError, match="subject.id must not resolve"):
        _safe_subject_id("..")


def test_subject_digest_uses_runtime_root_for_repo_relative_refs(tmp_path: Path, monkeypatch):
    repo_root = tmp_path
    skill_dir = repo_root / "data" / "skills" / "example"
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text("version one", encoding="utf-8")
    subjects_dir = repo_root / "subjects"
    subjects_dir.mkdir()
    descriptor_path = subjects_dir / "subject.toml"
    descriptor_path.write_text(
        """
[subject]
id = "skill-subject"
kind = "skill"
ref = "data/skills/example"

[suite]
products = ["oneTBB"]
questions = ["questions.json"]
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.chdir(repo_root)
    descriptor = load_subject(descriptor_path)

    first = build_subject_scorecard(descriptor, awareness_runs=[], out_dir=tmp_path)["subject"]["ref_digest"]
    skill_file.write_text("version two", encoding="utf-8")
    second = build_subject_scorecard(descriptor, awareness_runs=[], out_dir=tmp_path)["subject"]["ref_digest"]

    assert first != second


def test_subjects_run_writes_scorecard(tmp_path: Path, monkeypatch):
    def fake_call(prompt, model, provider="openai", api_key=None, system=None, **kw):
        return "answer", {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
        }

    monkeypatch.setattr(arm_runner_mod, "llm_call_with_usage", fake_call)
    docs = tmp_path / "docs.md"
    docs.write_text("# oneTBB\nUse parallel_for for loops.\n", encoding="utf-8")
    questions = tmp_path / "questions.json"
    questions.write_text(json.dumps([{"id": "q1", "question": "What API runs loops?"}]), encoding="utf-8")
    descriptor = tmp_path / "subject.toml"
    descriptor.write_text(
        f"""
[subject]
id = "docs-subject"
kind = "doc-source"
ref = "local:{docs.as_posix()}"

[suite]
products = ["oneTBB"]
questions = ["{questions.as_posix()}"]

[[matrix.cells]]
id = "arms-runner"
model = "m"
provider = "openai"
harness = "arms-runner"
plugins = []
""".strip(),
        encoding="utf-8",
    )
    out_dir = tmp_path / "out"
    args = build_parser().parse_args([
        "subjects",
        "run",
        str(descriptor),
        "--out-dir",
        str(out_dir),
    ])

    args.func(args)

    scorecard = json.loads((out_dir / "subject-scorecard.json").read_text(encoding="utf-8"))
    assert scorecard["schema_version"] == "subject_scorecard.v2"
    assert scorecard["novelty"] == {
        "status": "not_run", "n_cells": 0, "cells": [], "caveats": list(NOVELTY_CAVEATS),
    }
    assert scorecard["subject"]["id"] == "docs-subject"
    assert (out_dir / "oneTBB" / "matrix-rollup.json").is_file()


def test_subject_work_requires_explicit_harness_when_tasks_declared(tmp_path: Path):
    descriptor_path = tmp_path / "subject.toml"
    descriptor_path.write_text(
        """
[subject]
id = "skill-subject"
kind = "skill"
ref = "data/skills/example"

[suite]
products = ["oneTBB"]
questions = ["questions.json"]
tasks = ["tasks-root/example"]
""".strip(),
        encoding="utf-8",
    )
    descriptor = load_subject(descriptor_path)
    args = build_parser().parse_args(["subjects", "run", str(descriptor_path)])

    with pytest.raises(ValueError, match="pass --work-harnesses"):
        _run_subject_work(descriptor, args, out_dir=tmp_path)


def test_subject_work_resolves_bare_task_names_against_tasks_root(tmp_path: Path):
    """`suite.tasks` may name tasks the way `tasks run --tasks` does, so the root that
    makes a bare name resolvable has to be nameable here too. Without it the loader's
    error pointed at a `--tasks-root` flag this subcommand did not accept."""
    tasks_root = tmp_path / "harbor" / "tasks"
    task_dir = tasks_root / "demo-widget"
    task_dir.mkdir(parents=True)
    (task_dir / "instruction.md").write_text("Edit the code.", encoding="utf-8")
    (task_dir / "task.toml").write_text(
        '[metadata]\ndifficulty = "smoke"\ntags = ["demo"]\n', encoding="utf-8"
    )
    descriptor_path = tmp_path / "subject.toml"
    descriptor_path.write_text(
        """
[subject]
id = "skill-subject"
kind = "skill"
ref = "/tmp/skills/example"

[suite]
products = ["oneTBB"]
questions = ["questions.json"]
tasks = ["demo-widget"]
""".strip(),
        encoding="utf-8",
    )
    descriptor = load_subject(descriptor_path)

    without_root = build_parser().parse_args(
        ["subjects", "run", str(descriptor_path), "--work-harnesses", "codex"]
    )
    with pytest.raises(ValueError, match="--tasks-root"):
        _validate_subject_work_args(descriptor, without_root)

    with_root = build_parser().parse_args([
        "subjects", "run", str(descriptor_path),
        "--work-harnesses", "codex",
        "--tasks-root", str(tasks_root),
    ])
    tasks = _validate_subject_work_args(descriptor, with_root)
    assert [task.name for task in tasks] == ["demo-widget"]
    assert tasks[0].path == task_dir.resolve()


def test_subject_work_preflight_loads_tasks_before_awareness(tmp_path: Path, monkeypatch, capsys):
    def fail_if_called(config, **kw):
        raise AssertionError("awareness matrix should not run before task preflight")

    monkeypatch.setattr("dokimi.commands.arms.run_matrix", fail_if_called)
    questions = tmp_path / "questions.json"
    questions.write_text(json.dumps([{"id": "q1", "question": "Q?"}]), encoding="utf-8")
    docs = tmp_path / "docs.md"
    docs.write_text("# Docs\n", encoding="utf-8")
    missing_task = tmp_path / "missing-task"
    descriptor = tmp_path / "subject.toml"
    descriptor.write_text(
        f"""
[subject]
id = "docs-subject"
kind = "doc-source"
ref = "local:{docs.as_posix()}"

[suite]
products = ["oneTBB"]
questions = ["{questions.as_posix()}"]
tasks = ["{missing_task.as_posix()}"]
""".strip(),
        encoding="utf-8",
    )
    args = build_parser().parse_args([
        "subjects",
        "run",
        str(descriptor),
        "--work-harnesses",
        "codex",
    ])

    with pytest.raises(SystemExit) as excinfo:
        args.func(args)

    assert excinfo.value.code == 1
    assert "Error loading subject work: Task directory not found" in capsys.readouterr().err


def test_subjects_run_writes_work_task_runs_when_requested(tmp_path: Path, monkeypatch):
    def fake_matrix_run(config, *, out_dir, **kw):
        run_dir = Path(out_dir)
        run_dir.mkdir(parents=True)
        (run_dir / "matrix-rollup.json").write_text(json.dumps({
            "schema_version": "matrix_rollup.v1",
            "library_name": config.product,
            "cells": [],
            "plugin_deltas": [],
            "warnings": [],
        }), encoding="utf-8")

    monkeypatch.setattr("dokimi.commands.arms.run_matrix", fake_matrix_run)
    task_dir = tmp_path / "task-one"
    (task_dir / "environment").mkdir(parents=True)
    (task_dir / "tests").mkdir()
    (task_dir / "tests" / "test.sh").write_text("#!/bin/bash\n", encoding="utf-8")  # Harbor needs one
    (task_dir / "instruction.md").write_text("Edit the code.", encoding="utf-8")
    (task_dir / "task.toml").write_text(
        """
[metadata]
difficulty = "smoke"
tags = ["onetbb"]
""".strip(),
        encoding="utf-8",
    )
    questions = tmp_path / "questions.json"
    questions.write_text(json.dumps([{"id": "q1", "question": "Q?"}]), encoding="utf-8")
    docs = tmp_path / "docs.md"
    docs.write_text("# Docs\n", encoding="utf-8")
    descriptor = tmp_path / "subject.toml"
    descriptor.write_text(
        f"""
[subject]
id = "docs-subject"
kind = "doc-source"
ref = "local:{docs.as_posix()}"

[suite]
products = ["oneTBB"]
questions = ["{questions.as_posix()}"]
tasks = ["{task_dir.as_posix()}"]
""".strip(),
        encoding="utf-8",
    )
    out_dir = tmp_path / "out"
    args = build_parser().parse_args([
        "subjects",
        "run",
        str(descriptor),
        "--out-dir",
        str(out_dir),
        "--work-harnesses",
        "codex",
        "--work-dry-run",
    ])

    args.func(args)

    scorecard = json.loads((out_dir / "subject-scorecard.json").read_text(encoding="utf-8"))
    assert scorecard["work"]["status"] == "run"
    assert scorecard["work"]["harnesses"] == ["codex"]
    assert scorecard["work"]["summary"]["per_harness"]["codex"]["n"] == 1
    assert (out_dir / "work" / "task-runs.json").is_file()
    task_runs = json.loads((out_dir / "work" / "task-runs.json").read_text(encoding="utf-8"))
    assert task_runs["schema_version"] == "task_runs.v1"
    assert task_runs["results"][0]["metrics"]["dry_run"] is True


def test_subjects_run_forwards_run_config_to_the_arms_runner(tmp_path: Path, monkeypatch):
    """A budget set on `subjects run` must reach the runner, not fall back to the default.

    subjects run drives an arms run through a hand-built Namespace, so a field it
    forgets to copy is read back with a getattr default and is indistinguishable
    from a user who chose that default.
    """
    def fake_call(prompt, model, provider="openai", api_key=None, system=None, **kw):
        return "answer", {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}

    monkeypatch.setattr(arm_runner_mod, "llm_call_with_usage", fake_call)
    docs = tmp_path / "docs.md"
    docs.write_text("# oneTBB\nUse parallel_for for loops.\n", encoding="utf-8")
    questions = tmp_path / "questions.json"
    questions.write_text(
        json.dumps([{"id": "q1", "question": "What API runs loops?"}]), encoding="utf-8"
    )
    descriptor = tmp_path / "subject.toml"
    descriptor.write_text(
        f"""
[subject]
id = "docs-subject"
kind = "doc-source"
ref = "local:{docs.as_posix()}"

[suite]
products = ["oneTBB"]
questions = ["{questions.as_posix()}"]

[[matrix.cells]]
id = "arms-runner"
model = "m"
provider = "openai"
harness = "arms-runner"
plugins = []
""".strip(),
        encoding="utf-8",
    )
    out_dir = tmp_path / "out"
    args = build_parser().parse_args([
        "subjects", "run", str(descriptor),
        "--out-dir", str(out_dir),
        "--max-tokens", "32000",
        "--grounded-from-topics",
    ])

    args.func(args)

    cell = json.loads(
        (out_dir / "oneTBB" / "cells" / "arms-runner.json").read_text(encoding="utf-8")
    )
    assert cell["run_config"]["max_tokens"] == 32000
    assert cell["run_config"]["grounded_from_topics"] is True


def test_subjects_list_without_a_root_names_the_flag():
    """Descriptors live in a content checkout, so there is no in-repo default root."""
    parser = build_parser()
    args = parser.parse_args(["subjects", "list"])
    with pytest.raises(SystemExit) as excinfo:
        args.func(args)
    assert "--subjects-root" in str(excinfo.value)


def test_subjects_list_rejects_a_root_that_is_not_a_directory(tmp_path: Path):
    parser = build_parser()
    args = parser.parse_args(["subjects", "list", "--subjects-root", str(tmp_path / "typo")])
    with pytest.raises(SystemExit) as excinfo:
        args.func(args)
    assert "--subjects-root is not a directory" in str(excinfo.value)


def test_subjects_list_prints_descriptors_from_a_named_root(tmp_path: Path, capsys):
    root = tmp_path / "subjects"
    root.mkdir()
    (root / "demo.toml").write_text(
        """
[subject]
id = "demo-subject"
kind = "skill"
ref = "/path/to/content-repo/skills/demo-skill"

[suite]
products = ["demo"]
questions = ["questions.json"]

[[matrix.cells]]
id = "agent-none"
model = "m"
provider = "openai"
harness = "agent"
plugins = []
""".strip(),
        encoding="utf-8",
    )
    parser = build_parser()
    args = parser.parse_args(["subjects", "list", "--subjects-root", str(root)])
    args.func(args)
    out = capsys.readouterr().out
    assert "demo-subject" in out
    assert "skill" in out
