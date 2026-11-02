# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Exercise catalog evaluation through the actual CLI and local git histories."""

from __future__ import annotations

import json
import contextlib
import io
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from dokimi.artifacts import load_artifact
from dokimi.cli import main
from dokimi.runner.plan import completeness

PROJECT = Path(__file__).resolve().parents[1]


def _catalog(tmp_path: Path) -> Path:
    root = tmp_path / "catalog"
    root.mkdir()
    (root / "skills.yaml").write_text("- name: alpha\n- name: beta\n", encoding="utf-8")
    for name in ("alpha", "beta"):
        skill = root / "skills" / name
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: Use for {name} tasks.\n---\nRun the checks.\n",
            encoding="utf-8",
        )
    return root


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def _commit(root: Path) -> str:
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Catalog Test")
    _git(root, "config", "user.email", "catalog@example.invalid")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "fixture")
    return _git(root, "rev-parse", "HEAD")


def _run(root: Path, out: Path, *args: str) -> subprocess.CompletedProcess[str]:
    argv = ["catalog", "eval", str(root), "--tier", "static", "--out-dir", str(out), *args]
    stdout, stderr = io.StringIO(), io.StringIO()
    code = 0
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), patch.object(sys, "argv", ["dokimi", *argv]):
        try:
            main()
        except SystemExit as exc:
            code = int(exc.code or 0)
    return subprocess.CompletedProcess(argv, code, stdout.getvalue(), stderr.getvalue())


def test_cli_writes_complete_static_receipts_without_runtime_claim(tmp_path):
    root = _catalog(tmp_path)
    out = tmp_path / "out"
    done = _run(root, out)
    assert done.returncode == 0, done.stderr
    report = load_artifact("catalog_eval", out / "catalog.json")
    plan = load_artifact("plan", out / "plan.json")
    result = load_artifact("result", out / "result.json")
    assert completeness(plan, result).complete
    assert {row["evaluation"] for row in report["skills"]} == {"no_tasks"}
    assert {cell["model"] for cell in plan["cells"]} == {None}
    assert len(plan["cells"]) == 3
    assert "No model" in (out / "report.md").read_text()


def test_saved_catalog_receipt_opens_through_runs_show(tmp_path):
    root = _catalog(tmp_path)
    out = tmp_path / "out"
    assert _run(root, out).returncode == 0
    done = subprocess.run(
        [sys.executable, "-m", "dokimi", "runs", "show", "catalog.json", "--json"],
        cwd=PROJECT, env={**os.environ, "PYTHONPATH": str(PROJECT), "DOKIMI_RUNS_DIR": str(out)},
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert done.returncode == 0, done.stderr
    shown = json.loads(done.stdout)
    assert shown["schema_valid"]
    assert shown["contents"]["skills"] == 2


def test_changed_from_uses_working_resource_and_records_source(tmp_path):
    root = _catalog(tmp_path)
    sha = _commit(root)
    (root / "skills/alpha/reference.md").write_text("new guidance", encoding="utf-8")
    out = tmp_path / "out"
    done = _run(root, out, "--changed-from", sha)
    assert done.returncode == 0, done.stderr
    report = json.loads((out / "catalog.json").read_text())
    assert report["selection"]["selected"] == ["alpha"]
    assert report["source"] == {"commit": sha, "dirty": True}


def test_unresolvable_base_reports_conservative_selection(tmp_path):
    root = _catalog(tmp_path)
    _commit(root)
    out = tmp_path / "out"
    done = _run(root, out, "--changed-from", "missing-ref")
    assert done.returncode == 0, done.stderr
    report = json.loads((out / "catalog.json").read_text())
    assert report["selection"]["selected"] == ["alpha", "beta"]
    assert "missing-ref" in (out / "report.md").read_text()


def test_failed_rerun_replaces_success_receipts(tmp_path):
    root = _catalog(tmp_path)
    out = tmp_path / "out"
    assert _run(root, out).returncode == 0
    (root / "skills/alpha/SKILL.md").write_text("broken skill", encoding="utf-8")
    done = _run(root, out)
    assert done.returncode == 1, done.stderr
    report = load_artifact("catalog_eval", out / "catalog.json")
    result = load_artifact("result", out / "result.json")
    assert report["status"] == "fail"
    assert any(cell["status"] == "fail" for cell in result["cells"])


def test_input_error_removes_old_success_receipts(tmp_path):
    root = _catalog(tmp_path)
    out = tmp_path / "out"
    assert _run(root, out).returncode == 0
    (root / "skills.yaml").write_text("skills: [\n", encoding="utf-8")
    done = _run(root, out)
    assert done.returncode == 2
    assert not (out / "catalog.json").exists()
    assert not (out / "plan.json").exists()
    assert not (out / "result.json").exists()
    assert "Input error" in (out / "report.md").read_text()


@pytest.mark.parametrize("output", [".", "skills", "skills/alpha", "evaluation/harbor"])
def test_output_cannot_replace_catalog_inputs(tmp_path, output):
    root = _catalog(tmp_path)
    original = (root / "skills.yaml").read_bytes()
    done = _run(root, root / output)
    assert done.returncode == 2
    assert (root / "skills.yaml").read_bytes() == original
    assert not (root / "plan.json").exists()


@pytest.mark.skipif(os.name == "nt", reason="Windows file names cannot hold control characters")
def test_changed_paths_are_nul_delimited_and_never_shell_executed(tmp_path):
    root = _catalog(tmp_path)
    sha = _commit(root)
    payload = root / "skills/alpha/line\nbreak; touch PWNED.md"
    payload.write_text("safe text", encoding="utf-8")
    out = tmp_path / "out"
    done = _run(root, out, "--changed-from", sha)
    assert done.returncode == 0, done.stderr
    report = json.loads((out / "catalog.json").read_text())
    assert report["selection"]["selected"] == ["alpha"]
    assert payload.relative_to(root).as_posix() in report["selection"]["changed_paths"]
    assert not (root / "PWNED.md").exists()


def test_custom_skill_output_overlap_does_not_rewrite_source(tmp_path):
    root = _catalog(tmp_path)
    (root / "custom").mkdir()
    (root / "skills/alpha").rename(root / "custom/alpha")
    (root / "skills.yaml").write_text(
        "skills:\n  - name: alpha\n    catalog_path: custom/alpha\n", encoding="utf-8")
    resource = root / "custom/alpha/reference"
    resource.mkdir()
    original = resource / "report.md"
    original.write_text("source instructions must survive", encoding="utf-8")
    done = _run(root, resource)
    assert done.returncode == 2
    assert original.read_text() == "source instructions must survive"
    assert not (resource / "plan.json").exists()


def test_linked_catalog_root_is_rejected_by_cli(tmp_path):
    root = _catalog(tmp_path)
    link = tmp_path / "linked-catalog"
    link.symlink_to(root, target_is_directory=True)
    out = tmp_path / "out"
    done = _run(link, out)
    assert done.returncode == 2
    assert "symlink" in done.stderr
    assert not (out / "catalog.json").exists()


def test_duplicate_skill_validation_has_complete_failure_receipts(tmp_path):
    root = _catalog(tmp_path)
    (root / "skills.yaml").write_text("- name: alpha\n- name: alpha\n", encoding="utf-8")
    out = tmp_path / "out"
    done = _run(root, out)
    assert done.returncode == 1, done.stderr
    report = load_artifact("catalog_eval", out / "catalog.json")
    plan = load_artifact("plan", out / "plan.json")
    result = load_artifact("result", out / "result.json")
    assert report["status"] == "fail"
    assert completeness(plan, result).complete
    assert {cell["status"] for cell in result["cells"]} == {"fail"}


def test_report_lists_each_finding_once(tmp_path):
    root = _catalog(tmp_path)
    (root / "skills.yaml").write_text("- name: alpha\n- name: alpha\n", encoding="utf-8")
    out = tmp_path / "out"
    assert _run(root, out).returncode == 1
    assert (out / "report.md").read_text().count("duplicate-skill") == 1


@pytest.mark.parametrize("receipt", ["report.md", "plan.json"])
def test_output_dir_with_a_directory_named_like_a_receipt_is_preserved(tmp_path, receipt):
    root = _catalog(tmp_path)
    out = tmp_path / "out"
    (out / receipt).mkdir(parents=True)
    (out / receipt / "keep.txt").write_text("valuable")
    done = _run(root, out)
    assert done.returncode == 2
    assert (out / receipt / "keep.txt").read_text() == "valuable"


def test_looping_symlink_paths_are_usage_errors(tmp_path):
    root = _catalog(tmp_path)
    loop = tmp_path / "loop"
    loop.symlink_to(loop)
    assert _run(loop, tmp_path / "out").returncode == 2
    assert _run(root, loop).returncode == 2


def test_unresolvable_paths_raising_runtime_error_are_usage_errors(tmp_path, monkeypatch):
    root = _catalog(tmp_path)

    def loop(self, strict=False):
        raise RuntimeError("Symlink loop")

    monkeypatch.setattr(Path, "resolve", loop)
    assert _run(root, tmp_path / "out").returncode == 2


def test_configured_git_filters_are_refused_not_run(tmp_path):
    root = _catalog(tmp_path)
    (root / ".gitattributes").write_text("skills/alpha/* filter=payload\n")
    _commit(root)
    marker = tmp_path / "EXECUTED"
    _git(root, "config", "filter.payload.clean", f"touch {marker}; cat")
    (root / "skills/alpha/SKILL.md").write_text("---\nname: alpha\ndescription: Use for alpha tasks.\n---\nchanged\n")
    done = _run(root, tmp_path / "out")
    assert done.returncode == 2
    assert "content filters" in done.stderr
    assert not marker.exists()


def test_submodule_checkout_is_refused_before_git_inspects_it(tmp_path):
    root = _catalog(tmp_path)
    _commit(root)
    sub = tmp_path / "sub"
    sub.mkdir()
    _git(sub, "init", "-q")
    _git(sub, "config", "user.name", "T")
    _git(sub, "config", "user.email", "t@example.invalid")
    (sub / "f").write_text("x")
    _git(sub, "add", ".")
    _git(sub, "commit", "-qm", "s")
    _git(root, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(sub), "vendor/sub")
    done = _run(root, tmp_path / "out")
    assert done.returncode == 2
    assert "submodules" in done.stderr


def test_task_loader_type_error_replaces_earlier_receipts_with_an_error(tmp_path, monkeypatch):
    root = _catalog(tmp_path)
    out = tmp_path / "out"
    assert _run(root, out).returncode == 0
    task = root / "evaluation/harbor/tasks/t1"
    task.mkdir(parents=True)
    (task / "task.toml").write_text('[metadata]\nskill = "alpha"\n')
    (task / "instruction.md").write_text("Read.")

    def boom(path):
        raise TypeError("unhashable type: 'dict'")

    monkeypatch.setattr("dokimi.external.catalog_eval_inventory.load_task", boom)
    done = _run(root, out)
    assert done.returncode == 2
    assert not (out / "result.json").exists()
    assert "t1" in (out / "report.md").read_text()
