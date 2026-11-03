# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Catalog static checks preserve ownership, locality and evidence boundaries."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml


TASKS = "evaluation/harbor/tasks"


def evaluate(root: Path, changed_paths: list[str] | None = None) -> dict:
    from dokimi.external.catalog_eval import evaluate_catalog

    return evaluate_catalog(root, changed_paths)


def skill(root: Path, name: str = "alpha", **metadata) -> Path:
    path = root / "skills" / name
    path.mkdir(parents=True, exist_ok=True)
    (path / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Use to inspect {name} inputs.\n---\nInspect safely.\n",
        encoding="utf-8",
    )
    manifest = root / "skills.yaml"
    entries = yaml.safe_load(manifest.read_text()) if manifest.exists() else []
    entries.append({"name": name, **metadata})
    manifest.write_text(yaml.safe_dump(entries), encoding="utf-8")
    return path


def task(root: Path, name: str = "unrelated-name", owner: object = "alpha") -> Path:
    path = root / TASKS / name
    path.mkdir(parents=True, exist_ok=True)
    (path / "task.toml").write_text(
        "[metadata]\nskill = " + json.dumps(owner) + "\n", encoding="utf-8"
    )
    (path / "instruction.md").write_text("Read the inputs.", encoding="utf-8")
    return path


def suites(root: Path, entries: list[dict]) -> None:
    path = root / "evaluation/harbor/suites.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"suites": entries}), encoding="utf-8")


def row(report: dict, name: str = "alpha") -> dict:
    return next(item for item in report["skills"] if item["name"] == name)


def codes(report: dict) -> set[str]:
    return {f["code"] for f in report["problems"]}


def test_metadata_owns_undeclared_actual_task(tmp_path):
    skill(tmp_path)
    task(tmp_path)
    suites(tmp_path, [{"skill": "alpha", "tasks": []}])
    report = evaluate(tmp_path)
    item = row(report)
    assert report["status"] == "pass"
    assert item["evaluation"] == "not_measured"
    assert item["tasks"][0]["name"] == "unrelated-name"
    assert item["tasks"][0]["declared"] is False
    assert "undeclared-task" in codes(report)
    assert item["checks"]["status"] == "pass"


def test_missing_implemented_task_fails_but_planned_is_no_tasks(tmp_path):
    skill(tmp_path)
    suites(tmp_path, [{"skill": "alpha", "tasks": [
        {"name": "later", "status": "planned", "track": "executable"},
        {"name": "missing", "status": "implemented", "track": "executable"},
    ]}])
    report = evaluate(tmp_path)
    assert report["status"] == "fail"
    assert row(report)["evaluation"] == "no_tasks"
    assert row(report)["planned_tasks"] == ["later"]
    assert "missing-task" in codes(report)


def test_planned_only_is_a_passing_static_check_without_runtime_evidence(tmp_path):
    skill(tmp_path)
    suites(tmp_path, [{"skill": "alpha", "tasks": [{"name": "later", "status": "planned"}]}])
    report = evaluate(tmp_path)
    assert report["status"] == "pass"
    assert row(report)["evaluation"] == "no_tasks"
    assert row(report)["checks"]["status"] == "pass"
    assert row(report)["tasks"] == []


def test_imported_owner_remains_checks_only_even_with_task(tmp_path):
    skill(tmp_path)
    task(tmp_path)
    suites(tmp_path, [{"skill": "alpha", "evaluated_here": False, "tasks": []}])
    report = evaluate(tmp_path)
    assert row(report)["evaluation"] == "checks_only"
    assert row(report)["evaluated_here"] is False
    assert len(row(report)["tasks"]) == 1


@pytest.mark.parametrize("flag", ["false", 0, None, [], {}])
def test_evaluated_here_requires_boolean(tmp_path, flag):
    skill(tmp_path)
    suites(tmp_path, [{"skill": "alpha", "evaluated_here": flag, "tasks": []}])
    report = evaluate(tmp_path)
    assert report["status"] == "fail"
    assert "evaluated-here-type" in codes(report)


@pytest.mark.parametrize("owner", ["unknown", ["alpha"], 7, ""])
def test_actual_task_rejects_unknown_or_malformed_owner(tmp_path, owner):
    skill(tmp_path)
    task(tmp_path, owner=owner)
    report = evaluate(tmp_path)
    assert report["status"] == "fail"
    assert "task-owner" in codes(report)
    assert row(report)["evaluation"] == "no_tasks"


def test_suite_cannot_reassign_actual_task_owner(tmp_path):
    skill(tmp_path)
    skill(tmp_path, "beta")
    task(tmp_path)
    suites(tmp_path, [{"skill": "beta", "tasks": [{"name": "unrelated-name", "status": "implemented"}]}])
    report = evaluate(tmp_path)
    assert report["status"] == "fail"
    assert "task-owner-conflict" in codes(report)
    assert len(row(report, "alpha")["tasks"]) == 1
    assert row(report, "beta")["tasks"] == []


@pytest.mark.parametrize("kind", ["skill", "suite", "task"])
def test_duplicate_identities_fail(tmp_path, kind):
    skill(tmp_path)
    if kind == "skill":
        skill(tmp_path)
    else:
        entry = {"skill": "alpha", "tasks": [{"name": "later", "status": "planned"}]}
        suites(tmp_path, [entry, entry] if kind == "suite" else [
            {"skill": "alpha", "tasks": entry["tasks"] * 2}
        ])
    report = evaluate(tmp_path)
    assert report["status"] == "fail"
    assert f"duplicate-{kind}" in codes(report)


@pytest.mark.parametrize("catalog_path", ["../outside", "/etc", "C:/private", "skills\\alpha"])
def test_manifest_path_locality_is_checked_before_loading(tmp_path, catalog_path):
    skill(tmp_path)
    (tmp_path / "skills.yaml").write_text(yaml.safe_dump([
        {"name": "alpha", "catalog_path": catalog_path}
    ]))
    report = evaluate(tmp_path)
    assert report["status"] == "fail"
    assert "unsafe-path" in codes(report)


@pytest.mark.parametrize("target", ["skill", "resource", "task", "instruction", "manifest", "tasks-root"])
def test_symlink_is_rejected_before_target_read(tmp_path, monkeypatch, target):
    root = tmp_path / "catalog"
    sk = skill(root)
    ta = task(root)
    outside = tmp_path / "outside"
    outside.mkdir()
    forbidden = outside / "payload"
    forbidden.write_text("must never be read")
    destinations = {
        "skill": sk / "SKILL.md", "resource": sk / "resource",
        "task": ta / "task.toml", "instruction": ta / "instruction.md",
        "manifest": root / "skills.yaml", "tasks-root": root / TASKS,
    }
    destination = destinations[target]
    if destination.is_dir():
        shutil.rmtree(destination)
        destination.symlink_to(outside, target_is_directory=True)
    else:
        destination.unlink(missing_ok=True)
        destination.symlink_to(forbidden)
    original = Path.read_bytes
    original_text = Path.read_text

    def guarded_bytes(path):
        assert not path.is_symlink(), "read reached linked payload"
        return original(path)

    def guarded_text(path, *args, **kwargs):
        assert not path.is_symlink(), "read reached linked payload"
        return original_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", guarded_bytes)
    monkeypatch.setattr(Path, "read_text", guarded_text)
    report = evaluate(root)
    assert report["status"] == "fail"
    assert "unsafe-path" in codes(report)


def test_symlinked_root_is_an_unusable_invocation(tmp_path):
    skill(tmp_path / "actual")
    link = tmp_path / "linked"
    link.symlink_to(tmp_path / "actual", target_is_directory=True)
    with pytest.raises(ValueError, match="linked"):
        evaluate(link)


def test_linked_ancestor_of_the_root_is_resolved_not_refused(tmp_path):
    skill(tmp_path / "actual" / "catalog")
    (tmp_path / "linked").symlink_to(tmp_path / "actual", target_is_directory=True)
    report = evaluate(tmp_path / "linked" / "catalog")
    assert report["status"] == "pass"
    assert report["root"] == str((tmp_path / "actual" / "catalog").resolve())


def test_catalog_path_entry_without_a_name_loads_like_catalog_scan(tmp_path):
    skill(tmp_path)
    (tmp_path / "skills.yaml").write_text("skills:\n  - catalog_path: skills/alpha\n", encoding="utf-8")
    report = evaluate(tmp_path)
    assert report["status"] == "pass"
    assert row(report)["path"] == "skills/alpha"


@pytest.mark.parametrize("contents", ["", "skills:\n", "[]\n"])
def test_empty_manifest_is_an_empty_catalog_not_an_input_error(tmp_path, contents):
    (tmp_path / "skills.yaml").write_text(contents, encoding="utf-8")
    report = evaluate(tmp_path)
    assert codes(report) == {"empty-catalog"}


@pytest.mark.parametrize("file,contents", [
    ("skills.yaml", "skills: ["),
    ("evaluation/harbor/suites.json", "["),
    (f"{TASKS}/unrelated-name/task.toml", "[metadata"),
])
def test_malformed_input_raises_with_its_path(tmp_path, file, contents):
    skill(tmp_path)
    task(tmp_path)
    path = tmp_path / file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents)
    with pytest.raises(ValueError, match=path.name):
        evaluate(tmp_path)


def test_unreadable_input_has_a_controlled_path_diagnostic(tmp_path, monkeypatch):
    skill(tmp_path)
    original = Path.read_bytes

    def denied(path):
        if path.name == "SKILL.md":
            raise PermissionError("denied")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", denied)
    with pytest.raises(ValueError, match="SKILL.md"):
        evaluate(tmp_path)


@pytest.mark.parametrize("frontmatter", ["name: alpha", "name: Wrong\ndescription: useful"])
def test_missing_description_or_spec_error_fails_static_checks(tmp_path, frontmatter):
    path = skill(tmp_path)
    (path / "SKILL.md").write_text(f"---\n{frontmatter}\n---\nBody")
    report = evaluate(tmp_path)
    assert report["status"] == "fail"
    assert row(report)["checks"]["status"] == "fail"


def test_resource_bytes_change_digest_and_impact_only_owner(tmp_path):
    path = skill(tmp_path)
    skill(tmp_path, "beta")
    resource = path / "data.bin"
    resource.write_bytes(b"one")
    before = evaluate(tmp_path)
    resource.write_bytes(b"two")
    after = evaluate(tmp_path, ["skills/alpha/data.bin"])
    assert row(before)["bundle_digest"] != row(after)["bundle_digest"]
    assert before["catalog_digest"] != after["catalog_digest"]
    assert after["selection"]["selected"] == ["alpha"]
    assert row(before, "beta")["bundle_digest"] == row(after, "beta")["bundle_digest"]


def test_task_file_change_selects_metadata_owner_and_binds_verifier_bytes(tmp_path):
    skill(tmp_path)
    skill(tmp_path, "beta")
    path = task(tmp_path)
    verifier = path / "tests/check.py"
    verifier.parent.mkdir()
    verifier.write_text("assert True")
    before = evaluate(tmp_path)
    verifier.write_text("assert False")
    after = evaluate(tmp_path, [f"{TASKS}/unrelated-name/tests/check.py"])
    assert after["selection"]["selected"] == ["alpha"]
    assert row(before)["tasks"][0]["digest"] != row(after)["tasks"][0]["digest"]


@pytest.mark.parametrize("changed", ["skills.yaml", "evaluation/harbor/suites.json", ".github/workflows/dokimi.yml", "tools/check.py", "unknown.xyz"])
def test_shared_and_unclaimed_change_conservatively_selects_all(tmp_path, changed):
    skill(tmp_path)
    skill(tmp_path, "beta")
    report = evaluate(tmp_path, [changed])
    assert report["selection"]["selected"] == ["alpha", "beta"]
    assert report["selection"]["reasons"]["alpha"]
    if changed == "unknown.xyz":
        assert report["selection"]["unclaimed"] == [changed]


def test_removed_and_rename_paths_are_explicit_and_conservative(tmp_path):
    skill(tmp_path, "renamed")
    report = evaluate(tmp_path, ["skills/deleted/SKILL.md", "skills/renamed/SKILL.md"])
    assert report["selection"]["selected"] == ["renamed"]
    assert report["selection"]["removed"] == ["skills/deleted/SKILL.md"]
    assert any("conservative" in reason for reason in report["selection"]["reasons"]["renamed"])


@pytest.mark.parametrize("changed", [[], ["README.md"], ["docs/guide.md"]])
def test_no_relevant_change_has_no_selected_skills_but_integrity_runs(tmp_path, changed):
    skill(tmp_path)
    report = evaluate(tmp_path, changed)
    assert report["selection"]["selected"] == []
    assert report["status"] == "pass"
    assert row(report)["checks"]["status"] == "pass"


def test_empty_root_is_failed_integrity_not_an_empty_success(tmp_path):
    report = evaluate(tmp_path)
    assert report["status"] == "fail"
    assert report["skills"] == []
    assert "empty-catalog" in codes(report)


def test_payload_scripts_never_execute_and_digests_survive_relocation(tmp_path):
    root = tmp_path / "first"
    path = skill(root)
    ta = task(root)
    marker = tmp_path / "EXECUTED"
    script = path / "script.py"
    script.write_text(f"#!/usr/bin/env python3\nfrom pathlib import Path\nPath({str(marker)!r}).touch()\n")
    script.chmod(0o755)
    for target in (ta / "test.sh", ta / "solve.sh"):
        target.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
        target.chmod(0o755)
    first = evaluate(root)
    shutil.copytree(root, tmp_path / "second")
    second = evaluate(tmp_path / "second")
    assert not marker.exists()
    assert first["catalog_digest"] == second["catalog_digest"]
    assert row(first)["bundle_digest"] == row(second)["bundle_digest"]
    assert row(first)["tasks"] == row(second)["tasks"]


@pytest.mark.parametrize("changed", [["../outside"], ["C:/outside"], ["/etc"], ["bad\x00path"]])
def test_changed_paths_must_be_local_catalog_paths(tmp_path, changed):
    skill(tmp_path)
    with pytest.raises(ValueError, match="changed"):
        evaluate(tmp_path, changed)


def test_report_validates_against_packaged_schema(tmp_path):
    from jsonschema import Draft202012Validator

    import dokimi

    skill(tmp_path)
    schema = Path(dokimi.__file__).parent / "schemas/catalog_eval.v1.json"
    assert list(Draft202012Validator(json.loads(schema.read_text())).iter_errors(evaluate(tmp_path))) == []


def test_schema_accepts_existing_run_contract_digests(tmp_path):
    from jsonschema import Draft202012Validator

    import dokimi

    skill(tmp_path)
    report = evaluate(tmp_path)
    report["plan_digest"] = "sha256:" + "a" * 64
    report["checker"] = {"dokimi_version": "0.2", "python_version": "3.12", "package_sha256": "sha256:" + "b" * 64}
    schema = Path(dokimi.__file__).parent / "schemas/catalog_eval.v1.json"
    assert list(Draft202012Validator(json.loads(schema.read_text())).iter_errors(report)) == []


def test_filesystem_discovery_uses_frontmatter_identity_at_root(tmp_path):
    (tmp_path / "SKILL.md").write_text("---\nname: alpha\ndescription: Inspect root.\n---\nBody")
    report = evaluate(tmp_path)
    assert report["status"] == "pass"
    assert row(report)["path"] == "."
    assert report["selection"]["selected"] == ["alpha"]


def test_deleted_owned_resource_is_recorded(tmp_path):
    skill(tmp_path)
    report = evaluate(tmp_path, ["skills/alpha/deleted.txt"])
    assert report["selection"]["selected"] == ["alpha"]
    assert report["selection"]["removed"] == ["skills/alpha/deleted.txt"]


def test_changed_paths_rejects_a_string_instead_of_list(tmp_path):
    skill(tmp_path)
    with pytest.raises(ValueError, match="changed"):
        evaluate(tmp_path, "README.md")


def test_nonexecutable_suite_entry_does_not_require_a_runtime_task(tmp_path):
    skill(tmp_path)
    suites(tmp_path, [{"skill": "alpha", "tasks": [
        {"name": "questions", "status": "published", "track": "answer-quality"}
    ]}])
    report = evaluate(tmp_path)
    assert report["status"] == "pass"
    assert row(report)["evaluation"] == "no_tasks"


@pytest.mark.parametrize("metadata", [
    {"external-repo": True},
    {"external-repo": "https://example.com/repo", "external-commit": "pin", "external-path": "../escape", "external-license": "MIT"},
])
def test_import_metadata_types_and_locality_are_validated(tmp_path, metadata):
    skill(tmp_path, **metadata)
    report = evaluate(tmp_path)
    assert report["status"] == "fail"
    assert row(report)["evaluation"] == "checks_only"
    assert codes(report).intersection({"import-type", "unsafe-path"})


def test_import_metadata_is_checks_only_without_a_suite(tmp_path):
    skill(tmp_path, **{"external-repo": "https://example.com/repo", "external-commit": "pin", "external-path": "skills/alpha", "external-license": "MIT"})
    report = evaluate(tmp_path)
    assert report["status"] == "pass"
    assert row(report)["evaluated_here"] is False
    assert row(report)["evaluation"] == "checks_only"


def test_unknown_suite_owner_fails_integrity(tmp_path):
    skill(tmp_path)
    suites(tmp_path, [{"skill": "missing", "tasks": []}])
    report = evaluate(tmp_path)
    assert report["status"] == "fail"
    assert "suite-owner" in codes(report)


def test_missing_task_owner_is_not_guessed_from_task_name(tmp_path):
    skill(tmp_path)
    path = task(tmp_path, "alpha-convincing-name")
    (path / "task.toml").write_text("[metadata]\ntags = ['alpha']\n")
    report = evaluate(tmp_path)
    assert report["status"] == "fail"
    assert "task-owner" in codes(report)
    assert row(report)["tasks"] == []


def test_missing_task_instruction_is_validation_failure(tmp_path):
    skill(tmp_path)
    path = task(tmp_path)
    (path / "instruction.md").unlink()
    report = evaluate(tmp_path)
    assert report["status"] == "fail"
    assert "task-load" in codes(report)
    assert row(report)["evaluation"] == "no_tasks"


@pytest.mark.parametrize("value", ["../outside", "/private/data", "D:/outside"])
def test_task_config_paths_cannot_escape_task_bundle(tmp_path, value):
    skill(tmp_path)
    path = task(tmp_path)
    with (path / "task.toml").open("a") as stream:
        stream.write("\n[environment]\ndockerfile_path = " + json.dumps(value) + "\n")
    report = evaluate(tmp_path)
    assert report["status"] == "fail"
    assert "unsafe-path" in codes(report)
    assert row(report)["tasks"] == []


@pytest.mark.parametrize("link_target", ["missing", "loop"])
def test_broken_or_looping_resource_link_never_looks_like_empty_package(tmp_path, link_target):
    path = skill(tmp_path)
    link = path / "resource"
    link.symlink_to(link if link_target == "loop" else path / "absent")
    report = evaluate(tmp_path)
    assert report["status"] == "fail"
    assert "unsafe-path" in codes(report)


def test_dirty_loader_read_is_refused_instead_of_getting_a_stale_digest(tmp_path, monkeypatch):
    import dokimi.external.catalog_eval_inventory as inventory

    path = skill(tmp_path)
    original = inventory.read_skill_manifest

    def mutate(root):
        entries = original(root)
        (path / "SKILL.md").write_text("different bytes")
        return entries

    monkeypatch.setattr(inventory, "read_skill_manifest", mutate)
    with pytest.raises(ValueError, match="changed while checking"):
        evaluate(tmp_path)


def test_task_md_normalization_preserves_metadata_ownership(tmp_path):
    skill(tmp_path)
    path = tmp_path / TASKS / "portable"
    path.mkdir(parents=True)
    (path / "task.md").write_text("---\nmetadata:\n  skill: alpha\n---\n## prompt\nInspect inputs.\n")
    report = evaluate(tmp_path)
    assert report["status"] == "pass"
    assert row(report)["tasks"][0]["name"] == "portable"
    assert row(report)["evaluation"] == "not_measured"


def test_skill_file_manifest_binds_sibling_resources(tmp_path):
    path = skill(tmp_path)
    (tmp_path / "skills.yaml").write_text(yaml.safe_dump([
        {"name": "alpha", "catalog_path": "skills/alpha/SKILL.md"}
    ]))
    resource = path / "resource.txt"
    resource.write_text("before")
    before = evaluate(tmp_path)
    resource.write_text("after")
    after = evaluate(tmp_path, ["skills/alpha/resource.txt"])
    assert row(after)["path"] == "skills/alpha"
    assert row(before)["bundle_digest"] != row(after)["bundle_digest"]
    assert after["selection"]["selected"] == ["alpha"]


def test_owner_conflict_fails_both_affected_skill_checks(tmp_path):
    skill(tmp_path)
    skill(tmp_path, "beta")
    task(tmp_path)
    suites(tmp_path, [{"skill": "beta", "tasks": [{"name": "unrelated-name", "status": "implemented"}]}])
    report = evaluate(tmp_path)
    assert row(report, "alpha")["checks"]["status"] == "fail"
    assert row(report, "beta")["checks"]["status"] == "fail"


@pytest.mark.parametrize("has_task,imported,state", [(False, False, "no_tasks"), (True, False, "not_measured"), (False, True, "checks_only")])
def test_evaluation_state_has_a_concrete_evidence_reason(tmp_path, has_task, imported, state):
    skill(tmp_path)
    if has_task:
        task(tmp_path)
    if imported:
        suites(tmp_path, [{"skill": "alpha", "evaluated_here": False, "tasks": []}])
    report = evaluate(tmp_path)
    assert row(report)["evaluation"] == state
    assert row(report)["evaluation_reason"]


@pytest.mark.parametrize("resource", ["README.md", "docs/reference.md"])
def test_root_skill_owns_readme_and_docs_resources(tmp_path, resource):
    (tmp_path / "SKILL.md").write_text("---\nname: alpha\ndescription: Inspect root.\n---\nBody")
    path = tmp_path / resource
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("before")
    before = evaluate(tmp_path)
    path.write_text("after")
    after = evaluate(tmp_path, [resource])
    assert row(before)["bundle_digest"] != row(after)["bundle_digest"]
    assert after["selection"]["selected"] == ["alpha"]
    assert after["selection"]["reasons"]["alpha"] == [f"owned input changed: {resource}"]
    assert after["selection"]["unclaimed"] == []


def test_discovered_skills_keep_the_loader_order_for_prefixed_names(tmp_path):
    for name in ("alpha", "alpha-beta"):
        d = tmp_path / "skills" / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: Use to inspect {name} inputs.\n---\nbody\n")
    report = evaluate(tmp_path)
    assert {r["name"]: r["path"] for r in report["skills"]} == {"alpha": "skills/alpha", "alpha-beta": "skills/alpha-beta"}


def test_skill_md_directory_is_a_failed_row_not_a_crash(tmp_path):
    skill(tmp_path)
    (tmp_path / "skills.yaml").unlink()
    (tmp_path / "skills/beta/SKILL.md").mkdir(parents=True)
    report = evaluate(tmp_path)
    assert report["status"] == "fail"
    assert row(report, "beta")["checks"]["status"] == "fail"


def test_padded_manifest_name_is_normalised_before_hashing(tmp_path):
    skill(tmp_path / "plain")
    expected = row(evaluate(tmp_path / "plain"))["bundle_digest"]
    skill(tmp_path)
    (tmp_path / "skills.yaml").write_text("- name: ' alpha '\n", encoding="utf-8")
    report = evaluate(tmp_path)
    assert report["status"] == "pass"
    assert row(report)["bundle_digest"] == expected


def test_padded_manifest_name_still_refuses_a_linked_skill_md(tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("---\nname: alpha\ndescription: Use to inspect alpha inputs.\n---\nbody\n")
    path = skill(tmp_path)
    (path / "SKILL.md").unlink()
    (path / "SKILL.md").symlink_to(outside)
    (tmp_path / "skills.yaml").write_text("- name: ' alpha '\n", encoding="utf-8")
    assert "unsafe-path" in codes(evaluate(tmp_path))


def test_nameless_skill_md_file_reference_has_no_identity_conflict(tmp_path):
    skill(tmp_path)
    (tmp_path / "skills.yaml").write_text("skills:\n  - catalog_path: skills/alpha/SKILL.md\n", encoding="utf-8")
    report = evaluate(tmp_path)
    assert "skill-identity" not in codes(report)
    assert report["status"] == "pass"


@pytest.mark.parametrize("ref", ["SKILL.md", "."])
def test_nameless_root_skill_takes_its_loaded_name_and_owns_tasks(tmp_path, ref):
    (tmp_path / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: Use to inspect alpha inputs.\n---\nInspect safely.\n", encoding="utf-8")
    (tmp_path / "skills.yaml").write_text(f"skills:\n  - catalog_path: {ref}\n", encoding="utf-8")
    task(tmp_path)
    report = evaluate(tmp_path)
    assert [item["name"] for item in report["skills"]] == ["alpha"]
    assert row(report)["tasks"][0]["name"] == "unrelated-name"
    assert "task-owner" not in codes(report)


def test_planning_notes_and_foreign_task_yaml_are_not_tasks(tmp_path):
    skill(tmp_path)
    notes = tmp_path / TASKS / "later"
    notes.mkdir(parents=True)
    (notes / "task.md").write_text("# planning notes\n", encoding="utf-8")
    other = tmp_path / TASKS / "other"
    other.mkdir()
    (other / "task.yaml").write_text("name: x\n", encoding="utf-8")
    report = evaluate(tmp_path)
    assert report["status"] == "pass"
    assert row(report)["tasks"] == []


def test_reread_with_different_bytes_is_rejected_even_after_first_hash(tmp_path):
    from dokimi.external.catalog_eval_inputs import Inputs

    f = tmp_path / "a.txt"
    f.write_text("one")
    inputs = Inputs(tmp_path)
    inputs.read(f)
    f.write_text("two")
    with pytest.raises(ValueError, match="changed while checking"):
        inputs.read(f)


def test_windows_reparse_point_counts_as_a_redirect(tmp_path, monkeypatch):
    from dokimi.external import catalog_eval_inputs as inputs

    class Stat:
        st_mode = 0o040000
        st_file_attributes = 0x400

    monkeypatch.setattr(Path, "lstat", lambda self: Stat())
    assert inputs.redirected(tmp_path)


def test_regular_resource_named_like_a_vendored_directory_is_hashed(tmp_path):
    path = skill(tmp_path)
    (path / "venv").write_text("one")
    first = evaluate(tmp_path)
    (path / "venv").write_text("two")
    second = evaluate(tmp_path)
    assert row(first)["bundle_digest"] != row(second)["bundle_digest"]
    assert first["catalog_digest"] != second["catalog_digest"]


def test_task_directory_named_like_a_vendored_directory_is_discovered(tmp_path):
    skill(tmp_path)
    task(tmp_path, name="venv", owner="ghost")
    report = evaluate(tmp_path)
    assert "task-owner" in codes(report)


def test_moving_a_suite_declared_task_also_selects_the_suite_owner(tmp_path):
    skill(tmp_path)
    skill(tmp_path, "beta")
    suites(tmp_path, [{"skill": "alpha", "tasks": [{"name": "t1"}]}])
    task(tmp_path, "t1", owner="beta")
    report = evaluate(tmp_path, [f"{TASKS}/t1/task.toml"])
    assert row(report)["checks"]["status"] == "fail"
    assert report["selection"]["selected"] == ["alpha", "beta"]
