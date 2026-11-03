# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Exercise catalog evaluation through the actual CLI and local git histories."""

from __future__ import annotations

import json
import contextlib
import io
import os
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from dokimi.artifacts import load_artifact
from dokimi.cli import build_parser, main
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


def _statuses(out: Path) -> dict[str, str]:
    """Result status of each plan task, by task id."""
    task = {cell["cell_id"]: cell["task"] for cell in load_artifact("plan", out / "plan.json")["cells"]}
    return {task[cell["cell_id"]]: cell["status"] for cell in load_artifact("result", out / "result.json")["cells"]}


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
    assert sorted(cell["status"] for cell in result["cells"]) == ["fail", "pass"]


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
    build_parser()  # import every command module before Path.resolve is patched

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


def test_task_loader_type_error_is_a_finding_that_replaces_earlier_receipts(tmp_path, monkeypatch):
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
    assert done.returncode == 1
    assert "task-load" in (out / "report.md").read_text()
    assert _statuses(out)["catalog-integrity"] == "fail"


@pytest.mark.parametrize("name,content", [("report.md", "my notes"), ("plan.json", "{}"), ("result.json", "[1]"), ("catalog.json", "not json"),
                                          ("result.json", '{"cells": []}'), ("catalog.json", '{"schema_version": "need_graph.v1"}'),
                                          ("plan.json", "[" * 2000 + "0" + "]" * 2000)])
def test_unrelated_file_named_like_a_receipt_is_preserved(tmp_path, name, content):
    root = _catalog(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    (out / name).write_text(content)
    assert _run(root, out).returncode == 2
    assert (out / name).read_text() == content


@pytest.mark.parametrize("name", ["plan.v1.json", "result.v1.json"])
def test_shared_format_artifact_from_another_run_is_preserved(tmp_path, name):
    root = _catalog(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    fixture = Path(__file__).parent / "fixtures" / "artifacts" / name
    target = out / name.replace(".v1", "")
    target.write_bytes(fixture.read_bytes())
    assert _run(root, out).returncode == 2
    assert target.read_bytes() == fixture.read_bytes()


def test_overflowing_task_limit_is_a_finding_that_replaces_earlier_receipts(tmp_path, monkeypatch):
    root = _catalog(tmp_path)
    out = tmp_path / "out"
    assert _run(root, out).returncode == 0
    task = root / "evaluation" / "harbor" / "tasks" / "t1"
    task.mkdir(parents=True)
    (task / "task.toml").write_text('[metadata]\nskill = "alpha"\n')
    (task / "instruction.md").write_text("Read.")

    def boom(path):
        raise OverflowError("cannot convert float infinity to integer")

    monkeypatch.setattr("dokimi.external.catalog_eval_inventory.load_task", boom)
    done = _run(root, out)
    assert done.returncode == 1
    assert "task-load" in (out / "report.md").read_text()
    assert _statuses(out)["catalog-integrity"] == "fail"


def test_multiline_filter_value_is_refused_not_a_crash(tmp_path):
    root = _catalog(tmp_path)
    _commit(root)
    _git(root, "config", "filter.payload.clean", "echo first\ncat")
    done = _run(root, tmp_path / "out")
    assert done.returncode == 2
    assert re.search(r"inspection: payload$", done.stderr, re.MULTILINE)


def test_failed_rollback_keeps_the_previous_receipts(tmp_path):
    root = _catalog(tmp_path)
    out = tmp_path / "out"
    assert _run(root, out).returncode == 0
    before = (out / "report.md").read_text()
    real = os.replace
    def flaky(src, dst):
        if Path(src).parent.name in {"new", "old"}:
            raise OSError("disk gone")
        return real(src, dst)

    with patch("dokimi.commands.catalog_eval.os.replace", flaky):
        done = _run(root, out)
    assert done.returncode != 0
    kept = [p for p in out.glob(".catalog-eval-*/old") if (p / "report.md").read_text() == before]
    assert kept and str(kept[0]) in done.stderr


def test_deeply_nested_skill_tree_is_bundled(tmp_path):
    root = _catalog(tmp_path)
    deep = root / "skills" / "alpha"
    for _ in range(400):
        deep = deep / "d"
    deep.mkdir(parents=True)
    (deep / "x.txt").write_text("x")
    assert _run(root, tmp_path / "out").returncode == 0


@pytest.mark.skipif(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0), reason="needs POSIX permissions")
def test_unreadable_directory_without_a_manifest_is_an_input_error(tmp_path):
    root = tmp_path / "catalog"
    for name in ("alpha", "beta"):
        (root / name).mkdir(parents=True)
        (root / name / "SKILL.md").write_text(f"---\nname: {name}\ndescription: Use for {name} tasks.\n---\nx\n")
    (root / "beta").chmod(0)
    try:
        done = _run(root, tmp_path / "out")
    finally:
        (root / "beta").chmod(0o755)
    assert done.returncode == 2
    assert "beta" in done.stderr


def test_interrupt_between_backup_and_publish_keeps_the_previous_receipts(tmp_path):
    root = _catalog(tmp_path)
    out = tmp_path / "out"
    assert _run(root, out).returncode == 0
    before = (out / "report.md").read_text()
    real = os.replace

    def interrupted(src, dst):
        if Path(src).parent.name == "new":
            raise KeyboardInterrupt
        return real(src, dst)

    with patch("dokimi.commands.catalog_eval.os.replace", interrupted), pytest.raises(KeyboardInterrupt):
        _run(root, out)
    assert (out / "report.md").read_text() == before
    assert (out / "result.json").is_file()
    assert not list(out.glob(".catalog-eval-*"))


@pytest.mark.skipif(os.name == "nt", reason="needs undecodable POSIX filenames")
def test_undecodable_filename_in_an_input_error_still_invalidates_receipts(tmp_path):
    root = _catalog(tmp_path)
    odd = Path(os.fsdecode(os.fsencode(root) + b"bad" + bytes([255])))
    root.rename(odd)
    out = tmp_path / "out"
    assert _run(odd, out).returncode == 0
    (odd / "skills.yaml").write_text("skills: not-a-list" + chr(10))
    done = _run(odd, out)
    assert done.returncode == 2
    assert not (out / "result.json").exists()


def test_ignored_files_count_as_changes_and_dirty_state(tmp_path):
    root = _catalog(tmp_path)
    (root / ".gitignore").write_text("build.log\n")
    sha = _commit(root)
    (root / "skills/alpha/build.log").write_text("generated")
    out = tmp_path / "out"
    assert _run(root, out, "--changed-from", sha).returncode == 0
    report = json.loads((out / "catalog.json").read_text())
    assert report["selection"]["selected"] == ["alpha"]
    assert report["source"]["dirty"] is True


@pytest.mark.skipif(os.name == "nt", reason="backslash is a path separator on Windows")
def test_backslash_in_a_changed_filename_is_a_literal_name(tmp_path):
    root = _catalog(tmp_path)
    sha = _commit(root)
    (root / ("skills/alpha/weird" + chr(92) + "name.md")).write_text("x")
    out = tmp_path / "out"
    done = _run(root, out, "--changed-from", sha)
    assert done.returncode == 0, done.stderr
    assert json.loads((out / "catalog.json").read_text())["selection"]["selected"] == ["alpha"]


def test_inherited_git_dir_does_not_redirect_the_source_stamp(tmp_path, monkeypatch):
    root = _catalog(tmp_path)
    sha = _commit(root)
    other = tmp_path / "other"
    other.mkdir()
    (other / "f").write_text("x")
    _commit(other)
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    out = tmp_path / "out"
    done = _run(root, out, "--changed-from", sha)
    assert done.returncode == 0, done.stderr
    report = json.loads((out / "catalog.json").read_text())
    assert report["source"] == {"commit": sha, "dirty": False}
    assert report["selection"]["selected"] == []


def test_a_worktree_checkout_has_the_digests_of_its_main_checkout(tmp_path):
    root = tmp_path / "catalog"
    root.mkdir()
    (root / "SKILL.md").write_text("---\nname: alpha\ndescription: Use for alpha tasks.\n---\nRun.\n")
    _commit(root)
    _git(root, "worktree", "add", "-q", str(tmp_path / "wt"))
    reports = []
    for checkout in (root, tmp_path / "wt"):
        out = tmp_path / f"out-{checkout.name}"
        assert _run(checkout, out).returncode == 0
        reports.append(json.loads((out / "catalog.json").read_text()))
    assert reports[0]["catalog_digest"] == reports[1]["catalog_digest"]
    assert reports[0]["skills"] == reports[1]["skills"]


@pytest.mark.skipif(os.name == "nt", reason="the marker transport is a shell script")
def test_partial_clone_never_fetches_through_its_configured_transport(tmp_path):
    origin = _catalog(tmp_path)
    base = _commit(origin)
    (origin / "skills/alpha/reference.md").write_text("new guidance")
    _git(origin, "add", ".")
    _git(origin, "commit", "-qm", "second")
    _git(origin, "config", "uploadpack.allowFilter", "true")
    clone = tmp_path / "clone"
    subprocess.check_call(["git", "clone", "-q", "--filter=tree:0", "--no-local", origin.as_uri(), str(clone)])
    marker = tmp_path / "FETCHED"
    transport = tmp_path / "upload.sh"
    transport.write_text(f'#!/bin/sh\ntouch {marker}\nexec git upload-pack "$@"\n')
    transport.chmod(0o755)
    _git(clone, "config", "remote.origin.uploadpack", str(transport))
    assert _run(clone, tmp_path / "full").returncode == 0
    done = _run(clone, tmp_path / "out", "--changed-from", base)
    assert done.returncode == 2
    assert not marker.exists()


def test_out_dir_that_is_the_current_directory_survives(tmp_path, monkeypatch):
    root = _catalog(tmp_path)
    out = tmp_path / "receipts"
    out.mkdir()
    monkeypatch.chdir(out)
    assert _run(root, Path(".")).returncode == 0
    assert (out / "result.json").is_file()
    assert Path.cwd().samefile(out)


def test_receipts_are_written_into_the_existing_directory_without_touching_its_parent(tmp_path):
    root = _catalog(tmp_path)
    holder = tmp_path / "holder"
    out = holder / "out"
    out.mkdir(parents=True)
    assert _run(root, out).returncode == 0
    inode = out.stat().st_ino
    assert _run(root, out).returncode == 0
    assert out.stat().st_ino == inode
    assert sorted(p.name for p in holder.iterdir()) == ["out"]
    assert sorted(p.name for p in out.iterdir()) == ["catalog.json", "plan.json", "report.md", "result.json"]


def test_scratch_left_by_an_interrupted_run_does_not_block_the_next_run(tmp_path):
    root = _catalog(tmp_path)
    out = tmp_path / "out"
    (out / ".catalog-eval-stale" / "old").mkdir(parents=True)
    assert _run(root, out).returncode == 0


def _tracked_catalog(tmp_path: Path) -> tuple[Path, str]:
    root = _catalog(tmp_path)
    (root / ".gitignore").write_text(".pytest_cache/\nvenv/\n")
    return root, _commit(root)


def _selected(out: Path) -> list[str]:
    return json.loads((out / "catalog.json").read_text())["selection"]["selected"]


def test_an_ignored_file_outside_every_bundle_selects_nothing(tmp_path):
    root, sha = _tracked_catalog(tmp_path)
    (root / ".pytest_cache").mkdir()
    (root / ".pytest_cache/cache").write_text("x")
    out = tmp_path / "out"
    assert _run(root, out, "--changed-from", sha).returncode == 0
    assert _selected(out) == []
    assert json.loads((out / "catalog.json").read_text())["source"]["dirty"] is False


def test_an_untracked_file_in_a_task_named_like_a_vendored_directory_selects_its_skill(tmp_path):
    root = _catalog(tmp_path)
    task = root / "evaluation/harbor/tasks/venv"
    task.mkdir(parents=True)
    (task / "task.toml").write_text('[metadata]\nskill = "alpha"\n')
    (task / "instruction.md").write_text("Read.")
    sha = _commit(root)
    (task / "oracle.sh").write_text("true")
    out = tmp_path / "out"
    assert _run(root, out, "--changed-from", sha).returncode == 0
    assert _selected(out) == ["alpha"]


def test_a_task_moved_to_another_skill_selects_both_without_a_suites_file(tmp_path):
    root = _catalog(tmp_path)
    task = root / "evaluation/harbor/tasks/t1"
    task.mkdir(parents=True)
    (task / "task.toml").write_text('[metadata]\nskill = "alpha"\n')
    (task / "instruction.md").write_text("Read.")
    sha = _commit(root)
    (task / "task.toml").write_text('[metadata]\nskill = "beta"\n')
    out = tmp_path / "out"
    assert _run(root, out, "--changed-from", sha).returncode == 0
    assert _selected(out) == ["alpha", "beta"]


def test_a_broken_skill_outside_the_selection_does_not_fail_the_cli(tmp_path):
    root, sha = _tracked_catalog(tmp_path)
    (root / "skills/beta/SKILL.md").write_text("broken", encoding="utf-8")
    _git(root, "commit", "-qam", "break beta")
    sha = _git(root, "rev-parse", "HEAD")
    (root / "skills/alpha/new.md").write_text("x")
    out = tmp_path / "out"
    done = _run(root, out, "--changed-from", sha)
    assert done.returncode == 0, done.stderr
    assert _statuses(out) == {"catalog-integrity": "pass", "skill/alpha": "pass"}
