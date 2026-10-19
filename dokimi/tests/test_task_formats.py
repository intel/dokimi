# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for the two executable-task layouts (Harbor and BenchFlow ``task.md``).

The ``task.md`` fixture mirrors a real SkillsBench task
(benchflow-ai/skillsbench ``tasks/parallel-tfidf-search``, schema 1.3): YAML
frontmatter with a ``sandbox`` table, ``oracle/``, ``verifier/`` and
task-bundled ``environment/skills``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from dokimi.coverage import _task_product_tags
from dokimi.external.catalog import read_task_manifest
from dokimi.harnesses import discover_tasks, load_task
from dokimi.harnesses.command import _parse_reward_text
from dokimi.harnesses.tasks import (
    FORMAT_HARBOR,
    FORMAT_TASK_MD,
    read_task_config,
    task_format,
)

_TASK_MD = """---
schema_version: '1.3'
metadata:
  author_name: Someone
  difficulty: medium
  category: software-engineering
  tags:
  - parallel
  - onetbb
verifier:
  type: test-script
  timeout_sec: 900.0
  env:
    LITERAL: "1"
    SECRET: ${HOST_SECRET}
agent:
  timeout_sec: 1800.0
sandbox:
  network_mode: public
  cpus: 8
  memory_mb: 4096
  gpus: 0
oracle:
  env:
    MODE: oracle
---

# Parallel TF-IDF Similarity Search

In `/root/workspace/`, parallelize the engine.
"""


def _task_md(root: Path, name: str = "sb-task", text: str = _TASK_MD) -> Path:
    task = root / name
    for sub in ("environment/skills/python-parallelization", "oracle", "verifier"):
        (task / sub).mkdir(parents=True, exist_ok=True)
    (task / "environment" / "Dockerfile").write_text("FROM python:3.12-slim\n", encoding="utf-8")
    (task / "environment" / "skills" / "python-parallelization" / "SKILL.md").write_text(
        "---\nname: python-parallelization\ndescription: d\n---\nbody\n", encoding="utf-8"
    )
    (task / "oracle" / "solve.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    (task / "verifier" / "test.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    (task / "task.md").write_text(text, encoding="utf-8")
    return task


def _harbor(root: Path, name: str = "hb-task") -> Path:
    task = root / name
    for sub in ("environment", "solution", "tests"):
        (task / sub).mkdir(parents=True)
    (task / "solution" / "solve.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    (task / "tests" / "test.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    (task / "instruction.md").write_text("Fix it.", encoding="utf-8")
    (task / "task.toml").write_text(
        '[metadata]\ntags = ["onedal"]\n\n[environment]\nworkdir = "/app"\n', encoding="utf-8"
    )
    return task


# ── loader ────────────────────────────────────────────────────────────────────


def test_task_md_loads_frontmatter_and_body(tmp_path):
    task = load_task(_task_md(tmp_path))
    assert task.format == FORMAT_TASK_MD
    assert task.schema_version == "1.3"
    assert task.metadata["tags"] == ["parallel", "onetbb"]
    assert task.timeout_sec == 1800.0
    assert task.verifier["timeout_sec"] == 900.0
    assert task.instruction.startswith("# Parallel TF-IDF")
    assert task.instruction.endswith("parallelize the engine.")  # stripped, as BenchFlow
    assert "---" not in task.instruction


_MINIMAL_FRONTMATTER = "---\nschema_version: '1.3'\nsandbox:\n  network_mode: public\n---\n"


def test_task_md_prompt_section_is_the_instruction(tmp_path):
    """Only ``## prompt`` reaches the agent; role/scene/persona text stays out (BenchFlow)."""
    body = (
        "## prompt\n\nreal prompt\n\n## role:reviewer\n\nsecret reviewer text\n\n"
        "## scene:review\n\nscene text\n\n## user-persona\n\npersona text\n"
    )
    task = load_task(_task_md(tmp_path, text=_MINIMAL_FRONTMATTER + body))
    assert task.instruction == "real prompt"


@pytest.mark.parametrize("heading", ["## user-persona", "## scene:review", "## role:x"])
def test_task_md_any_reserved_heading_ends_the_prompt(tmp_path, heading):
    body = f"## prompt\n\nreal prompt\n\n{heading}\n\nnot for the agent\n"
    task = load_task(_task_md(tmp_path, text=_MINIMAL_FRONTMATTER + body))
    assert task.instruction == "real prompt"


def test_task_md_without_prompt_heading_uses_the_preamble(tmp_path):
    body = "Do the thing.\n\n## role:reviewer\n\nsecret reviewer text\n"
    task = load_task(_task_md(tmp_path, text=_MINIMAL_FRONTMATTER + body))
    assert task.instruction == "Do the thing."


def test_task_md_escaped_section_heading_is_restored_in_the_prompt(tmp_path):
    body = "## prompt\n\nKeep this line:\n\\## role:x\n"
    task = load_task(_task_md(tmp_path, text=_MINIMAL_FRONTMATTER + body))
    assert task.instruction == "Keep this line:\n## role:x"


def test_task_md_duplicate_section_is_rejected(tmp_path):
    body = "## prompt\n\na\n\n## PROMPT\n\nb\n"
    with pytest.raises(ValueError, match="duplicate section"):
        load_task(_task_md(tmp_path, text=_MINIMAL_FRONTMATTER + body))


def test_task_md_verifier_path_shorthand_is_rejected(tmp_path):
    path = _task_md(tmp_path, text="---\nverifier: checks\n---\nDo it.\n")
    (path / "checks").mkdir()
    (path / "checks" / "test.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    with pytest.raises(ValueError, match="verifier"):
        load_task(path)


def test_task_md_oracle_path_shorthand_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="oracle"):
        load_task(_task_md(tmp_path, text="---\noracle: gold\n---\nDo it.\n"))


@pytest.mark.parametrize(
    "line, key",
    [
        ("profile: code-change", "profile"),
        ("profiles: [code-change]", "profiles"),
        ("image: python:3.12", "image"),
    ],
)
def test_task_md_profile_and_image_shorthand_are_rejected(tmp_path, line, key):
    with pytest.raises(ValueError, match=f"'{key}:'"):
        load_task(_task_md(tmp_path, text=f"---\n{line}\n---\nDo it.\n"))


def test_task_md_sandbox_table_feeds_environment(tmp_path):
    """Schema 1.3 renamed [environment] to [sandbox]; consumers read .environment."""
    task = load_task(_task_md(tmp_path))
    assert task.environment["network_mode"] == "public"
    assert task.environment["cpus"] == 8


def test_as_dict_carries_every_table(tmp_path):
    """The run record's tasks block keeps oracle settings, e.g. oracle.env."""
    record = load_task(_task_md(tmp_path)).as_dict()
    assert record["oracle"] == {"env": {"MODE": "oracle"}}
    assert record["format"] == FORMAT_TASK_MD
    assert record["environment"]["cpus"] == 8


def test_task_md_layout_dirs(tmp_path):
    task = load_task(_task_md(tmp_path))
    assert task.solution_dir.name == "oracle"
    assert task.tests_dir.name == "verifier"
    assert task.skills_dir is not None and task.skills_dir.name == "skills"


def test_harbor_layout_dirs_and_no_skills(tmp_path):
    task = load_task(_harbor(tmp_path))
    assert task.format == FORMAT_HARBOR
    assert task.solution_dir.name == "solution"
    assert task.tests_dir.name == "tests"
    assert task.skills_dir is None


def test_layout_dirs_fall_back_to_the_other_convention(tmp_path):
    """BenchFlow accepts tests/ and solution/ as aliases inside a task.md task."""
    path = _task_md(tmp_path)
    (path / "oracle").rename(path / "solution")
    (path / "verifier").rename(path / "tests")
    task = load_task(path)
    assert task.solution_dir.name == "solution"
    assert task.tests_dir.name == "tests"


def test_directory_with_both_files_loads_as_task_md(tmp_path):
    """`bench tasks migrate` without --remove-legacy leaves both behind."""
    path = _task_md(tmp_path)
    (path / "task.toml").write_text("[metadata]\ntags = ['stale']\n", encoding="utf-8")
    (path / "instruction.md").write_text("stale", encoding="utf-8")
    assert task_format(path) == FORMAT_TASK_MD
    with pytest.warns(UserWarning, match="differ in metadata"):  # the stale split files
        assert load_task(path).metadata["tags"] == ["parallel", "onetbb"]


@pytest.mark.parametrize(
    "text, match",
    [
        ("# no frontmatter\n", "no YAML frontmatter"),
        ("---\nmetadata: {}\n", "not closed"),
        ("---\n- a\n- b\n---\nbody\n", "must be a mapping"),
        ("---\nmetadata: [unclosed\n---\nbody\n", "not valid YAML"),
    ],
)
def test_malformed_task_md_is_a_value_error(tmp_path, text, match):
    with pytest.raises(ValueError, match=match):
        load_task(_task_md(tmp_path, text=text))


def test_discover_mixes_both_layouts(tmp_path):
    _task_md(tmp_path, "a-sb")
    _harbor(tmp_path, "b-hb")
    (tmp_path / "c-not-a-task").mkdir()
    tasks = discover_tasks(tmp_path)
    assert [(t.name, t.format) for t in tasks] == [
        ("a-sb", FORMAT_TASK_MD), ("b-hb", FORMAT_HARBOR),
    ]


def test_read_task_config_needs_no_instruction(tmp_path):
    path = _harbor(tmp_path)
    (path / "instruction.md").unlink()
    assert read_task_config(path)["metadata"]["tags"] == ["onedal"]


def test_read_task_config_bad_toml_is_a_value_error(tmp_path):
    path = _harbor(tmp_path)
    (path / "task.toml").write_text("[metadata\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid TOML"):
        read_task_config(path)


def test_coverage_reads_tags_from_both_layouts(tmp_path):
    _task_md(tmp_path, "a-sb")
    _harbor(tmp_path, "b-hb")
    tags = _task_product_tags(tmp_path)
    assert tags == {"a-sb": {"parallel", "onetbb"}, "b-hb": {"onedal"}}


def test_catalog_scan_accepts_task_md(tmp_path):
    _task_md(tmp_path / "evaluation" / "harbor" / "tasks", "a-sb")
    entries = read_task_manifest(tmp_path)
    assert [(e.name, e.failed, e.skipped) for e in entries] == [("a-sb", False, False)]


def test_catalog_suites_manifest_entry_can_be_a_task_md_task(tmp_path):
    import json

    _task_md(tmp_path / "evaluation" / "harbor" / "tasks", "a-sb")
    (tmp_path / "evaluation" / "harbor" / "suites.json").write_text(
        json.dumps({"suites": [{"tasks": [{"name": "a-sb", "track": "executable"}]}]}),
        encoding="utf-8",
    )
    (entry,) = read_task_manifest(tmp_path)
    assert (entry.failed, entry.skipped) == (False, False)
    assert entry.task is not None and entry.task.format == FORMAT_TASK_MD


def test_coverage_skips_a_task_that_does_not_parse(tmp_path):
    _task_md(tmp_path, "a-bad", text="---\nmetadata: [unclosed\n---\nbody\n")
    _harbor(tmp_path, "b-hb")
    assert _task_product_tags(tmp_path) == {"b-hb": {"onedal"}}


# ── reward parsing ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text, expected",
    [("1", 1.0), ("0\n", 0.0), ("0.75", 0.75), ("1.0", 1.0), ("reward: 0.5", 0.5),
     ("1.5", None), ("-0.1", None), ("", None), ("nan", None), ("inf", None)],
)
def test_reward_file_accepts_partial_credit(text, expected):
    assert _parse_reward_text(text, reward_file=True) == expected


@pytest.mark.parametrize("text, expected", [("0.5", None), ("1e-3", None), ("1", 1.0), ("reward: 0.5", 0.5)])
def test_command_output_keeps_the_old_reward_rule(text, expected):
    """A command printing a bare 0.5 still falls back to its exit code."""
    assert _parse_reward_text(text) == expected


def test_find_reward_keeps_a_fractional_reward_file(tmp_path):
    from dokimi.harnesses.command import _find_reward

    (tmp_path / "verifier").mkdir()
    (tmp_path / "verifier" / "reward.txt").write_text("0.75\n", encoding="utf-8")
    assert _find_reward(tmp_path, "", "") == 0.75


def test_command_harness_fractional_reward_is_kept_and_fails(tmp_path):
    """Exit 0 used to make this a pass (exit-code fallback); 0.75 is now the reward."""
    from dokimi.harnesses.command import CommandHarness

    task = load_task(_harbor(tmp_path / "tasks"))
    harness = CommandHarness(
        "fake",
        "sh -c 'mkdir -p {output_dir}/verifier && echo 0.75 > {output_dir}/verifier/reward.txt'",
    )
    result = harness.run(task, output_dir=tmp_path / "out")
    assert result.returncode == 0
    assert (result.reward, result.passed) == (0.75, False)


@pytest.mark.parametrize("text, reward", [("0.75\n", 0.75), ("1.00\n", 1.0)])
def test_docker_verify_keeps_a_fractional_reward_file(tmp_path, monkeypatch, text, reward):
    import io

    from docker_cli_fake import DockerCLI
    from dokimi.harnesses.docker_solver import DockerSolverHarness

    out = tmp_path / "o"
    out.mkdir()
    task = load_task(_harbor(tmp_path))
    harness = DockerSolverHarness(harness_id="docker-oracle", mode="oracle", runner=DockerCLI(reward=text))
    assert harness._verify(task, "c1", out, log=io.StringIO()) == reward


def test_yaml_dates_stay_json_serialisable(tmp_path):
    import json

    text = _TASK_MD.replace("  author_name: Someone\n", "  author_name: Someone\n  created: 2024-01-02\n")
    record = load_task(_task_md(tmp_path, text=text)).as_dict()
    assert record["metadata"]["created"] == "2024-01-02"
    json.dumps(record)


def test_yaml_date_keys_sets_and_toml_dates_survive_the_run_artifact(tmp_path, monkeypatch):
    """Converted when the task is loaded, not after every cell has run."""
    from dokimi.artifacts import save_artifact
    from dokimi.harnesses import TaskSuiteRunner

    root = tmp_path / "tasks"
    text = _TASK_MD.replace(
        "  author_name: Someone\n",
        "  author_name: Someone\n  history: {2024-01-02: created}\n  labels: !!set {e, b, d, a, c}\n",
    )
    _task_md(root, "a-sb", text=text)
    hb = _harbor(root, "b-hb")
    (hb / "task.toml").write_text(
        '[metadata]\ncreated = 2024-01-02\nupdated = 2024-01-02T10:30:00\nat = 07:32:00\n',
        encoding="utf-8",
    )
    sb, harbor = discover_tasks(root)
    assert sb.metadata["history"] == {"2024-01-02": "created"}
    assert sb.metadata["labels"] == ["a", "b", "c", "d", "e"]
    assert harbor.metadata == {
        "created": "2024-01-02", "updated": "2024-01-02T10:30:00", "at": "07:32:00",
    }
    assert read_task_config(hb)["metadata"]["created"] == "2024-01-02"
    out = TaskSuiteRunner([sb, harbor], ["docker-oracle"], output_root=tmp_path / "runs",
                          dry_run=True).run()
    save_artifact("task_runs", out, tmp_path / "task_runs.json")
    assert (tmp_path / "task_runs.json").is_file()


def test_note_task_md_without_frontmatter_does_not_shadow_task_toml(tmp_path):
    path = _harbor(tmp_path)
    (path / "task.md").write_text("# Notes for task authors\n", encoding="utf-8")
    assert task_format(path) == FORMAT_HARBOR
    assert [t.format for t in discover_tasks(tmp_path)] == [FORMAT_HARBOR]


@pytest.mark.parametrize(
    "text",
    [
        "---\ntitle: Notes on this task\nlayout: page\n---\nAuthor notes: the solution uses X.\n",
        "---\nmetadata: {}\n",  # unclosed block
        "---\n\nA horizontal rule, then notes.\n\n---\nmore\n",  # not a mapping
    ],
    ids=["jekyll", "unclosed", "horizontal-rule"],
)
def test_non_benchflow_task_md_next_to_task_toml_loads_task_toml(tmp_path, text):
    """A docs page next to a Harbor task must not turn it into a task-md task."""
    path = _harbor(tmp_path)
    (path / "task.md").write_text(text, encoding="utf-8")
    with pytest.warns(UserWarning, match="loading task.toml") as caught:
        assert task_format(path) == FORMAT_HARBOR
        (task,) = discover_tasks(tmp_path)
        assert read_task_config(path)["metadata"]["tags"] == ["onedal"]
    assert len(caught) == 1  # once, though task_format ran three times
    assert task.format == FORMAT_HARBOR
    assert task.instruction == "Fix it."
    assert task.metadata["tags"] == ["onedal"]
    assert task.environment == {"workdir": "/app"}


def test_benchflow_task_md_next_to_task_toml_wins(tmp_path):
    path = _harbor(tmp_path)
    (path / "task.md").write_text("---\nmetadata:\n  tags: [sb]\n---\nDo it.\n", encoding="utf-8")
    with pytest.warns(UserWarning, match="differ in metadata, environment, instruction"):
        task = load_task(path)
    assert task.format == FORMAT_TASK_MD
    assert task.metadata["tags"] == ["sb"]


def test_stray_task_md_notes_do_not_break_discovery_of_the_root(tmp_path):
    """BenchFlow's batch discovery skips a malformed task; so do we, loudly."""
    root = tmp_path / "evaluation" / "harbor" / "tasks"
    _harbor(root, "good-harbor")
    _task_md(root, "good-sb")
    notes = root / "docs-notes"
    notes.mkdir()
    (notes / "task.md").write_text("# planning notes\n", encoding="utf-8")
    with pytest.warns(UserWarning, match="docs-notes: task.md has no YAML frontmatter"):
        assert [t.name for t in discover_tasks(root)] == ["good-harbor", "good-sb"]
        entries = read_task_manifest(tmp_path)
    assert [e.name for e in entries] == ["good-harbor", "good-sb"]
    assert set(_task_product_tags(root)) == {"good-harbor", "good-sb"}


def test_lone_task_md_without_frontmatter_is_still_an_error(tmp_path):
    with pytest.raises(ValueError, match="no YAML frontmatter"):
        load_task(_task_md(tmp_path, text="# notes\n"))


def test_environment_and_sandbox_together_is_rejected(tmp_path):
    text = _TASK_MD.replace("sandbox:\n", "environment:\n  cpus: 2\nsandbox:\n")
    with pytest.raises(ValueError, match="both 'environment' and 'sandbox'"):
        load_task(_task_md(tmp_path, text=text))


def test_docker_claude_host_errors_on_a_task_md_task(tmp_path, monkeypatch):
    """The host-side agent cannot run a task.md task (its instruction names
    container paths): the cell is an error (missing data), with no docker call."""
    from docker_cli_fake import DockerCLI
    from dokimi.harnesses import TaskSuiteRunner

    cli = DockerCLI()
    task = load_task(_task_md(tmp_path / "tasks"))
    out = TaskSuiteRunner([task], ["docker-claude-host"], output_root=tmp_path / "runs", runner=cli).run()
    (row,) = out["results"]
    assert row["reward"] is None and row["passed"] is False
    assert "cannot be solved by a host-side agent" in row["metrics"]["error"]
    assert cli.calls == []


def test_docker_oracle_runs_a_task_md_task_from_its_own_dirs(tmp_path, monkeypatch):
    """docker-oracle runs a task.md task: oracle/ and verifier/ are copied in,
    not solution/ and tests/ (this replaces #69's temporary refusal)."""
    from docker_cli_fake import DockerCLI
    from dokimi.harnesses import TaskSuiteRunner

    cli = DockerCLI()
    calls = cli.args
    task = load_task(_task_md(tmp_path / "tasks",
                              text=_TASK_MD.replace("    SECRET: ${HOST_SECRET}\n", "")))
    out = TaskSuiteRunner([task], ["docker-oracle"], output_root=tmp_path / "runs", runner=cli).run()
    (row,) = out["results"]
    assert "error" not in row["metrics"]
    sources = {c[1] for c in calls if c[0] == "cp" and c[2].startswith("dokimi-run-")}
    assert sources == {f"{task.path / 'oracle'}/.", f"{task.path / 'verifier'}/."}


@pytest.mark.parametrize("harness_id", ["terminal-bench:oracle", "codex", "claude-code"])
def test_harbor_harness_errors_on_a_task_md_task(tmp_path, monkeypatch, harness_id):
    """Harbor reads only task.toml: its non-zero exit must not read as a fail."""
    from dokimi.harnesses import TaskSuiteRunner
    from dokimi.process import FakeRunner

    never = FakeRunner(lambda call: pytest.fail("harbor must not be started for a task.md task"))
    task = load_task(_task_md(tmp_path / "tasks"))
    out = TaskSuiteRunner([task], [harness_id], output_root=tmp_path / "runs", runner=never).run()
    (row,) = out["results"]
    assert row["reward"] is None and row["passed"] is False
    assert "cannot run it: no task.toml" in row["metrics"]["error"]



_MIGRATED = (
    "---\nschema_version: '1.3'\nmetadata:\n  tags: [onedal]\nsandbox:\n  workdir: /app\n---\n\n"
    "## prompt\nFix it.\n"
)


def _recording_docker():
    """The docker CLI (verifier reward 1), harbor and claude: every call exits 0 and is recorded."""
    from docker_cli_fake import DockerCLI

    return DockerCLI(reward="1")


def test_migrated_dir_with_task_toml_and_task_md_runs_everywhere(tmp_path, monkeypatch):
    """`bench tasks migrate` without --remove-legacy leaves task.toml, solution/,
    tests/ and a task.md. Every built-in harness read those files before task.md
    was understood, so none may refuse the directory."""
    from dokimi.harnesses import TaskSuiteRunner

    path = _harbor(tmp_path / "tasks")
    (path / "task.md").write_text(_MIGRATED, encoding="utf-8")
    task = load_task(path)
    assert task.format == FORMAT_TASK_MD and task.tests_dir.name == "tests"
    cli = _recording_docker()
    calls = cli.args
    ids = ["docker-oracle", "docker-claude", "terminal-bench:oracle", "codex"]
    out = TaskSuiteRunner([task], ids, output_root=tmp_path / "runs", runner=cli).run()
    # docker-claude starts an egress proxy, which may be missing on the test host;
    # what it must never do is refuse the directory.
    errors = {r["harness"]: (r["metrics"] or {}).get("error", "") for r in out["results"]}
    assert "do not run" not in errors["docker-claude"], errors
    assert not any(v for k, v in errors.items() if k != "docker-claude"), errors
    cps = [(Path(c[1]).name, c[2].split(":", 1)[1]) for c in calls if c[0] == "cp" and Path(c[1]).is_dir()]
    assert ("solution", "/solution") in cps and ("tests", "/tests") in cps
    assert any(c.argv[0] == "harbor" for c in cli.calls)


def test_task_toml_with_only_oracle_and_verifier_dirs_runs_in_docker(tmp_path, monkeypatch):
    """docker-* copy the directories the task has to both layouts' paths, so the
    scripts find their siblings wherever they look."""
    from dokimi.harnesses import TaskSuiteRunner

    path = _harbor(tmp_path / "tasks")
    (path / "solution").rename(path / "oracle")
    (path / "tests").rename(path / "verifier")
    cli = _recording_docker()
    calls = cli.args
    task = load_task(path)
    assert task.format == FORMAT_HARBOR
    out = TaskSuiteRunner([task], ["docker-oracle"], output_root=tmp_path / "runs", runner=cli).run()
    (row,) = out["results"]
    assert "error" not in row["metrics"]
    copied = {(Path(c[1].removesuffix("/.")).name, c[2].split(":", 1)[1])
              for c in calls if c[0] == "cp" and c[1].endswith("/.")}
    assert copied == {("oracle", "/solution"), ("oracle", "/oracle"),
                      ("verifier", "/tests"), ("verifier", "/verifier")}


def test_docker_probe_copies_neither_directory_so_it_is_not_refused(tmp_path):
    from dokimi.harnesses.docker_solver import DockerSolverHarness

    path = _harbor(tmp_path / "tasks")
    (path / "solution").rename(path / "oracle")
    (path / "tests").rename(path / "verifier")
    result = DockerSolverHarness(harness_id="docker-probe", mode="probe").run(
        load_task(path), dry_run=True
    )
    assert result.metrics["dry_run"] is True


def test_command_harness_still_runs_a_task_md_task(tmp_path):
    from dokimi.harnesses.command import CommandHarness

    task = load_task(_task_md(tmp_path / "tasks"))
    harness = CommandHarness(
        "fake", "sh -c 'mkdir -p {output_dir}/verifier && echo 1 > {output_dir}/verifier/reward.txt'"
    )
    result = harness.run(task, output_dir=tmp_path / "out")
    assert (result.reward, result.passed) == (1.0, True)


@pytest.mark.parametrize(
    "key, value",
    [
        ("schema_version", "'1.3'"),
        ("metadata", "{tags: [sb]}"),
        ("verifier", "{timeout_sec: 5}"),
        ("agent", "{timeout_sec: 5}"),
        ("sandbox", "{cpus: 1}"),
        ("environment", "{cpus: 1}"),
        ("oracle", "{env: {A: b}}"),
    ],
)
def test_every_benchflow_key_makes_task_md_win_next_to_task_toml(tmp_path, key, value):
    path = _harbor(tmp_path)
    (path / "task.md").write_text(f"---\n{key}: {value}\n---\nDo it.\n", encoding="utf-8")
    assert task_format(path) == FORMAT_TASK_MD


# ── harness refusals, task.md/task.toml drift, hostile frontmatter ──────────


def _run(tasks, harness_ids, tmp_path, runner):
    from dokimi.harnesses import TaskSuiteRunner

    out = TaskSuiteRunner(tasks, harness_ids, output_root=tmp_path / "runs", runner=runner).run()
    return {r["harness"]: r for r in out["results"]}


def _no_harbor():
    """A runner standing for the harbor CLI: every call exits 0 and is recorded."""
    from dokimi.process import FakeRunner

    return FakeRunner()


@pytest.mark.parametrize(
    "remove, problem",
    [
        ("instruction.md", "no instruction.md"),
        ("environment", "no environment/"),
        ("tests/test.sh", "no tests/test.sh"),
    ],
)
def test_harbor_harness_refuses_what_harbor_itself_rejects(tmp_path, monkeypatch, remove, problem):
    """Harbor 0.24's Task.is_valid_dir rejects these; its exit 1 would read as a fail."""
    import shutil

    path = _harbor(tmp_path / "tasks")
    target = path / remove
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    harbor = _no_harbor()
    task = load_task(path) if remove != "instruction.md" else None
    if task is None:  # a Harbor-layout task needs instruction.md to load at all
        (path / "task.md").write_text(_MIGRATED, encoding="utf-8")
        (path / "solution").rename(path / "oracle")
        (path / "tests").rename(path / "verifier")
        task = load_task(path)  # the `migrate --remove-legacy` that kept task.toml
    rows = _run([task], ["terminal-bench:oracle", "codex"], tmp_path, harbor)
    for row in rows.values():
        assert row["reward"] is None and problem in row["metrics"]["error"], row["metrics"]
    assert harbor.calls == []


def test_harbor_oracle_needs_a_solve_script_and_other_agents_do_not(tmp_path, monkeypatch):
    path = _harbor(tmp_path / "tasks")
    (path / "solution" / "solve.sh").unlink()
    harbor = _no_harbor()
    rows = _run([load_task(path)], ["terminal-bench:oracle", "codex"], tmp_path, harbor)
    assert "no solution/solve.sh" in rows["terminal-bench:oracle"]["metrics"]["error"]
    assert "error" not in rows["codex"]["metrics"]
    assert len(harbor.calls) == 1


@pytest.mark.parametrize(
    "verifier, bundled",
    [
        ("", False),  # shared mode: a tests/Dockerfile bundles nothing
        ('[verifier]\nenvironment_mode = "separate"\n', True),
        ('[verifier.environment]\ncpus = 1\n', True),  # an environment table implies separate
        ('[verifier]\nenvironment_mode = "shared"\n', False),
    ],
)
def test_harbor_bundled_verifier_tests_need_no_test_script(tmp_path, verifier, bundled):
    from dokimi.harnesses.tasks import harbor_task_problem

    path = _harbor(tmp_path)
    (path / "tests" / "test.sh").unlink()
    (path / "tests" / "Dockerfile").write_text("FROM python:3.12-slim\n", encoding="utf-8")
    with (path / "task.toml").open("a", encoding="utf-8") as fh:
        fh.write("\n" + verifier)
    assert harbor_task_problem(load_task(path)) == (None if bundled else "no tests/test.sh")


def test_harbor_separate_verifier_image_bundles_its_tests(tmp_path):
    from dokimi.harnesses.tasks import harbor_task_problem

    path = _harbor(tmp_path)
    (path / "tests" / "test.sh").unlink()
    with (path / "task.toml").open("a", encoding="utf-8") as fh:
        fh.write('\n[verifier.environment]\ndocker_image = "grader:1"\n')
    assert harbor_task_problem(load_task(path)) is None


def test_harbor_windows_task_needs_test_bat_and_a_task_with_steps_is_left_to_harbor(tmp_path):
    from dokimi.harnesses.tasks import harbor_task_problem

    path = _harbor(tmp_path)
    (path / "task.toml").write_text('[environment]\nos = "windows"\n', encoding="utf-8")
    assert harbor_task_problem(load_task(path)) == "no tests/test.bat"
    (path / "tests" / "test.bat").write_text("@echo off\n", encoding="utf-8")
    assert harbor_task_problem(load_task(path), needs_solution=True) is None  # solve.sh is accepted
    (path / "solution" / "solve.sh").unlink()
    assert harbor_task_problem(load_task(path), needs_solution=True) == "no solution/solve.bat or solve.sh"
    (path / "solution" / "solve.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    (path / "tests" / "test.bat").unlink()
    with (path / "task.toml").open("a", encoding="utf-8") as fh:
        fh.write('\n[[steps]]\nname = "one"\n')
    assert harbor_task_problem(load_task(path)) is None


def _migrated(root: Path, task_md: str = _MIGRATED) -> Path:
    path = _harbor(root)
    (path / "task.md").write_text(task_md, encoding="utf-8")
    return path


def test_migrated_dir_whose_task_md_and_task_toml_agree_has_no_drift(tmp_path):
    from dokimi.harnesses.tasks import harbor_task_problem, legacy_drift

    task = load_task(_migrated(tmp_path))
    assert task.format == FORMAT_TASK_MD
    assert legacy_drift(task) == [] and harbor_task_problem(task) is None


@pytest.mark.parametrize(
    "edit, section",
    [
        (lambda p: p.joinpath("task.toml").write_text(
            '[metadata]\ntags = ["onedal"]\n\n[environment]\nworkdir = "/app"\ncpus = 8\n',
            encoding="utf-8"), "environment"),
        (lambda p: p.joinpath("instruction.md").write_text("Fix it.\nAnd more.\n", encoding="utf-8"),
         "instruction"),
    ],
    ids=["config", "prompt"],
)
def test_task_md_and_task_toml_drift_warns_and_harbor_refuses(tmp_path, monkeypatch, edit, section):
    """docker-* and the artifact use task.md; harbor run would read task.toml."""
    from dokimi.harnesses.tasks import legacy_drift

    path = _migrated(tmp_path / "tasks")
    edit(path)
    with pytest.warns(UserWarning, match=f"differ in {section}"):
        task = load_task(path)
    assert legacy_drift(task) == [section]
    cli = _recording_docker()
    rows = _run([task], ["codex", "docker-oracle"], tmp_path, cli)
    assert f"differ in {section}" in rows["codex"]["metrics"]["error"]
    assert "error" not in (rows["docker-oracle"]["metrics"] or {})
    assert not any(c.argv[0] == "harbor" for c in cli.calls)


_ALIAS_BOMB = (
    "---\nschema_version: '1.3'\nmetadata:\n  l0: &l0 [x, x, x]\n  l1: &l1 [*l0, *l0, *l0]\n---\nDo it.\n"
)


@pytest.mark.parametrize(
    "text, match",
    [
        (_ALIAS_BOMB, "uses a YAML alias, which is refused"),
        ("---\nmetadata: &m\n  self: *m\n---\nDo it.\n", "uses a YAML alias"),
        ("---\nmetadata:\n  x: " + "[" * 3000 + "]" * 3000 + "\n---\nDo it.\n", "nested too deeply"),
        ("---\n[]\n---\nnotes\n", "must be a mapping"),
    ],
    ids=["alias-bomb", "self-alias", "deep-nesting", "empty-list"],
)
def test_hostile_frontmatter_is_a_value_error_and_skipped_by_scanners(tmp_path, text, match):
    """#118: aliases expanded without bound, and deep nesting overflowed the
    stack (RecursionError escaped the scanners' handlers)."""
    root = tmp_path / "evaluation" / "harbor" / "tasks"
    _harbor(root, "good-harbor")
    bad = _task_md(root, "zz-bad", text=text)
    with pytest.raises(ValueError, match=match):
        load_task(bad)
    assert set(_task_product_tags(root)) == {"good-harbor"}


def test_hostile_frontmatter_next_to_task_toml_falls_back_to_task_toml(tmp_path):
    path = _harbor(tmp_path)
    (path / "task.md").write_text(_ALIAS_BOMB, encoding="utf-8")
    with pytest.warns(UserWarning, match="uses a YAML alias"):
        assert load_task(path).format == FORMAT_HARBOR


def test_deeply_nested_task_toml_is_a_value_error(tmp_path):
    path = _harbor(tmp_path)
    (path / "task.toml").write_text("x = " + "[" * 3000 + "]" * 3000 + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="nested too deeply"):
        read_task_config(path)


@pytest.mark.parametrize("harness_id", ["docker-oracle", "docker-claude", "docker-probe"])
def test_docker_runs_scripts_that_read_benchflow_mounts(tmp_path, harness_id):
    """What `bench tasks export --target harbor` leaves: solution/ and tests/, but
    /oracle and /verifier paths. docker-* mount both names, so only the
    Harbor-backed harnesses refuse it."""
    from dokimi.harnesses import create_harness

    path = _harbor(tmp_path)
    (path / "solution" / "solve.sh").write_text("python3 /oracle/solution.py\n", encoding="utf-8")
    (path / "tests" / "test.sh").write_text(
        "#!/bin/bash\n# It will be copied to /verifier/test.sh\nmkdir -p /logs/verifier\n"
        "pytest /verifier/test_outputs.py && echo 1 > /logs/verifier/reward.txt\n",
        encoding="utf-8",
    )
    assert create_harness(harness_id).run(load_task(path), dry_run=True).metrics["dry_run"] is True


def test_reward_dir_and_comments_are_not_benchflow_mounts(tmp_path):
    from dokimi.harnesses.tasks import benchflow_mount_refs

    script = tmp_path / "test.sh"
    script.write_text(
        "# copied to /verifier/test.sh\nmkdir -p /logs/verifier\ncp $SCRIPT_DIR/oracle_solver.py .\n"
        "ls /tmp/oracle ./verifier\n",
        encoding="utf-8",
    )
    assert benchflow_mount_refs(script) == []
    script.write_text('cd "/oracle" && python3 /oracle/solve.py\n', encoding="utf-8")
    assert benchflow_mount_refs(script) == ["/oracle"]


def test_harbor_refuses_a_test_script_that_reads_verifier(tmp_path, monkeypatch):
    path = _harbor(tmp_path / "tasks")
    (path / "tests" / "test.sh").write_text("pytest /verifier/test_outputs.py\n", encoding="utf-8")
    harbor = _no_harbor()
    rows = _run([load_task(path)], ["codex"], tmp_path, harbor)
    assert "Harbor mounts tests/ at /tests" in rows["codex"]["metrics"]["error"]
    assert harbor.calls == []


@pytest.mark.parametrize(
    "text, why",
    [
        ("---\ntitle: Notes on this task\nmetadata: {author: me}\n---\nAuthor notes.\n",
         "keys BenchFlow does not know (title)"),
        ("---\nagent: claude\n---\nnotes\n", "non-table agent"),
        ("---\nenvironment: staging\n---\nnotes\n", "non-table environment"),
    ],
    ids=["unknown-key", "agent-string", "environment-string"],
)
def test_note_with_some_benchflow_keys_next_to_task_toml_loads_task_toml(tmp_path, text, why):
    """One BenchFlow key present is not enough: BenchFlow refuses these files."""
    path = _harbor(tmp_path)
    (path / "task.md").write_text(text, encoding="utf-8")
    with pytest.warns(UserWarning, match=re.escape(why)):
        task = load_task(path)
    assert task.format == FORMAT_HARBOR and task.instruction == "Fix it."


@pytest.mark.filterwarnings("ignore:task.md and the task.toml")
@pytest.mark.parametrize(
    "key, value",
    [("task", "{name: org/x}"), ("version", "'1.3'"), ("steps", "[]"), ("solution", "{env: {A: b}}"),
     ("source", "{repo: x}"), ("reward", "mean"), ("multi_step_reward_strategy", "mean"),
     ("artifacts", "[]"), ("agents", "{}"), ("benchflow", "{}"), ("scenes", "[]"), ("user", "{}"),
     ("image", "python:3.12"), ("name", "x"), ("profile", "code-change"), ("profiles", "[a]")],
)
def test_other_benchflow_keys_also_make_task_md_win(tmp_path, key, value):
    path = _harbor(tmp_path)
    (path / "task.md").write_text(f"---\n{key}: {value}\n---\nDo it.\n", encoding="utf-8")
    assert task_format(path) == FORMAT_TASK_MD


@pytest.mark.parametrize("harness_id", ["codex", "claude-code", "terminal-bench:oracle"])
def test_refusals_hold_on_a_dry_run(tmp_path, harness_id):
    from dokimi.harnesses import create_harness

    with pytest.raises(ValueError):
        create_harness(harness_id).run(load_task(_task_md(tmp_path)), dry_run=True)


@pytest.mark.parametrize(
    "harness_id, template",
    [("command:sh -c 'echo reward: 1'", None), ("codex", "sh -c 'echo reward: 1'")],
    ids=["command:", "--command-template"],
)
def test_command_harnesses_built_by_the_registry_run_a_task_md_task(tmp_path, harness_id, template):
    from dokimi.harnesses import create_harness

    harness = create_harness(harness_id, command_template=template)
    result = harness.run(load_task(_task_md(tmp_path / "tasks")), output_dir=tmp_path / "out")
    assert (result.reward, result.passed) == (1.0, True)


@pytest.mark.parametrize(
    "front", ["title: Planning notes\nlayout: page\n", "tags: [notes]\naliases: [plan]\n", ""],
    ids=["jekyll", "obsidian", "empty"],
)
def test_lone_task_md_with_frontmatter_is_a_task(tmp_path, front):
    """Documented: a lone task.md with frontmatter is a task, whatever its keys."""
    notes = tmp_path / "zz-notes"
    notes.mkdir()
    (notes / "task.md").write_text(f"---\n{front}---\nnotes\n", encoding="utf-8")
    assert [(t.name, t.format) for t in discover_tasks(tmp_path)] == [("zz-notes", FORMAT_TASK_MD)]


def test_harbor_oracle_refuses_a_solve_script_that_reads_oracle(tmp_path):
    from dokimi.harnesses.tasks import harbor_task_problem

    path = _harbor(tmp_path)
    (path / "solution" / "solve.sh").write_text("python3 /oracle/solution.py\n", encoding="utf-8")
    assert "solution/solve.sh reads /oracle" in harbor_task_problem(load_task(path), needs_solution=True)
    assert harbor_task_problem(load_task(path)) is None  # an agent other than the oracle never runs it


@pytest.mark.parametrize(
    "front, why",
    [("", "its frontmatter is empty"), ("task: org/x\n", "non-table task"),
     ("sandbox: big\n", "non-table sandbox"), ("metadata: notes\n", "non-table metadata")],
)
def test_task_md_that_is_not_a_benchflow_document_does_not_shadow_task_toml(tmp_path, front, why):
    path = _harbor(tmp_path)
    (path / "task.md").write_text(f"---\n{front}---\nnotes\n", encoding="utf-8")
    with pytest.warns(UserWarning, match=why):
        assert task_format(path) == FORMAT_HARBOR


def test_drift_ignores_crlf_and_trailing_whitespace_and_reads_a_solution_table(tmp_path):
    from dokimi.harnesses.tasks import legacy_drift

    path = _migrated(tmp_path, _MIGRATED.replace("metadata:", "oracle:\n  env: {A: b}\nmetadata:"))
    (path / "instruction.md").write_bytes(b"Fix it.  \r\n\r\n")
    with (path / "task.toml").open("a", encoding="utf-8") as fh:
        fh.write('\n[solution.env]\nA = "b"\n')
    assert legacy_drift(load_task(path)) == []


@pytest.mark.parametrize(
    "toml_tail, section",
    [('\n[verifier]\ntimeout_sec = 9\n', "verifier"), ('\n[agent]\ntimeout_sec = 9\n', "agent")],
)
def test_drift_names_each_differing_table(tmp_path, toml_tail, section):
    from dokimi.harnesses.tasks import legacy_drift

    path = _migrated(tmp_path)
    with (path / "task.toml").open("a", encoding="utf-8") as fh:
        fh.write(toml_tail)
    with pytest.warns(UserWarning, match=f"differ in {section}"):
        assert legacy_drift(load_task(path)) == [section]


def test_drift_with_an_unreadable_legacy_side_is_reported_not_raised(tmp_path):
    from dokimi.harnesses.tasks import harbor_task_problem, legacy_drift

    path = _migrated(tmp_path / "tasks")
    (path / "instruction.md").write_bytes(b"caf\xe9\n")
    with pytest.warns(UserWarning, match="differ in instruction"):
        assert [t.name for t in discover_tasks(tmp_path / "tasks")] == ["hb-task"]
    (path / "task.toml").write_text("not = [valid\n", encoding="utf-8")
    with pytest.warns(UserWarning, match="differ in task.toml"):
        task = load_task(path)
    assert legacy_drift(task) == ["task.toml"]
    assert harbor_task_problem(task).startswith("task.toml is not valid TOML")


@pytest.mark.parametrize(
    "line",
    ["ls /oracle_x /oracle-data /verifier.d", "cp ${ROOT}/oracle/x .", 'cd $(dirname "$0")/verifier',
     "ls ~/oracle"],
)
def test_names_that_only_look_like_benchflow_mounts_are_not_refused(tmp_path, line):
    from dokimi.harnesses.tasks import benchflow_mount_refs

    script = tmp_path / "test.sh"
    script.write_text(line + "\n", encoding="utf-8")
    assert benchflow_mount_refs(script) == []


def test_catalog_manifest_marks_a_hostile_task_failed_and_keeps_the_rest(tmp_path):
    import json

    root = tmp_path / "evaluation" / "harbor" / "tasks"
    _harbor(root, "good-harbor")
    _task_md(root, "zz-bad", text=_ALIAS_BOMB)
    (tmp_path / "evaluation" / "harbor" / "suites.json").write_text(
        json.dumps({"suites": [{"tasks": [{"name": n, "track": "executable"} for n in ("good-harbor", "zz-bad")]}]}),
        encoding="utf-8",
    )
    entries = {e.name: e for e in read_task_manifest(tmp_path)}
    assert not entries["good-harbor"].failed and entries["zz-bad"].failed


def test_docker_refuses_a_task_without_the_directory_its_mode_copies(tmp_path):
    """docker cp would fail only at verify time: after the build and, for
    docker-claude, after the paid agent run."""
    import shutil

    from dokimi.harnesses.docker_solver import _refuse_unrunnable

    path = _harbor(tmp_path)
    shutil.rmtree(path / "solution")
    task = load_task(path)
    with pytest.raises(ValueError, match="no solution/ directory to copy to /solution"):
        _refuse_unrunnable(task, "oracle", "container")
    _refuse_unrunnable(task, "claude", "container")  # the agent arm copies only tests/
    shutil.rmtree(path / "tests")
    for mode in ("oracle", "claude"):
        with pytest.raises(ValueError, match="no tests/ directory to copy to /tests"):
            _refuse_unrunnable(load_task(path), mode, "container")
    _refuse_unrunnable(load_task(path), "probe", "container")  # the probe copies neither

    md = _task_md(tmp_path / "md")
    shutil.rmtree(md / "verifier")
    with pytest.raises(ValueError, match="no verifier/ directory to copy to /tests"):
        _refuse_unrunnable(load_task(md), "oracle", "container")
