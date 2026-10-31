# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""dokimi_config.v1: discovery, schema, subject and suite resolution, skill_sha, changed-file impact."""

from __future__ import annotations

from pathlib import Path

import pytest

from dokimi.repo_config import ConfigError, Subject, discover, find_repo_root, load, load_repo_config

HEADER = "schema_version: dokimi_config.v1\n"
BESIDE_CONFIG = HEADER + "defaults:\n  skills: skills/*\n"
CENTRAL_CONFIG = (
    HEADER
    + "defaults:\n  skills: skills/*\n"
    + "subjects:\n"
    + "  - id: skill/onetbb\n"
    + "    path: skills/onetbb\n"
    + "    suite:\n"
    + "      harbor: evaluation/harbor/tasks/onetbb-*\n"
)
BESIDE = {
    ".github/dokimi/config.yaml": BESIDE_CONFIG,
    "skills/onetbb/SKILL.md": "# onetbb\n",
    "skills/onetbb/scripts/run.py": "print('run')\n",
    "skills/onetbb/evals/task-1/task.toml": "id = 'one'\n",
    "skills/onetbb/evals/questions.json": "[]\n",
    "skills/fastcontext/SKILL.md": "# fastcontext\n",
    "README.md": "readme\n",
}
CENTRAL = {
    ".github/dokimi/config.yaml": CENTRAL_CONFIG,
    "skills/onetbb/SKILL.md": "# onetbb\n",
    "evaluation/harbor/tasks/onetbb-001/task.toml": "id = '1'\n",
    "evaluation/harbor/tasks/onetbb-002/task.toml": "id = '2'\n",
    "evaluation/harbor/tasks/other-001/task.toml": "id = 'other'\n",
}


def _tree(root: Path, files: dict[str, str]) -> Path:
    """A repository at ``root`` (it always has a ``.git``) holding ``files``; returns the resolved root."""
    (root / ".git").mkdir(parents=True, exist_ok=True)
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root.resolve()


def _subjects(root: Path) -> dict[str, Subject]:
    return {s.id: s for s in load_repo_config(root).subjects()}


# --- discovery ---------------------------------------------------------------------------------


def test_discovery_prefers_github_then_falls_back_to_dokimi(tmp_path):
    root = _tree(tmp_path / "repo", {".dokimi/config.yaml": BESIDE_CONFIG})
    assert discover(root) == root / ".dokimi/config.yaml"
    _tree(root, {".github/dokimi/config.yaml": BESIDE_CONFIG})
    assert discover(root) == root / ".github/dokimi/config.yaml"


def test_an_explicit_path_wins_over_the_repository_config(tmp_path):
    root = _tree(tmp_path / "repo", {".github/dokimi/config.yaml": BESIDE_CONFIG, "ci/dokimi.yaml": BESIDE_CONFIG})
    assert discover(root, explicit=Path("ci/dokimi.yaml")) == root / "ci/dokimi.yaml"
    assert discover(root, explicit=root / "ci/dokimi.yaml") == root / "ci/dokimi.yaml"


def test_an_explicit_path_that_does_not_exist_is_an_error_not_a_fallback(tmp_path):
    root = _tree(tmp_path / "repo", {".github/dokimi/config.yaml": BESIDE_CONFIG})
    with pytest.raises(ConfigError, match="config not found"):
        discover(root, explicit=Path("missing.yaml"))


def test_no_config_is_none_from_discover_and_an_error_from_load_repo_config(tmp_path):
    root = _tree(tmp_path / "repo", {"README.md": "x\n"})
    assert discover(root) is None
    with pytest.raises(ConfigError, match="no dokimi config under"):
        load_repo_config(root)


def test_the_root_is_found_from_a_subdirectory(tmp_path):
    root = _tree(tmp_path / "repo", {**BESIDE})
    assert find_repo_root(root / "skills" / "onetbb" / "evals") == root
    assert discover(root / "skills" / "onetbb") == root / ".github/dokimi/config.yaml"
    assert load_repo_config(root / "skills" / "onetbb").root == root


# --- schema ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "needle"),
    [
        ("defaults:\n  skills: skills/*\n", "schema_version"),
        ("schema_version: dokimi_config.v2\n", "schema_version"),
        (HEADER + "bogus: 1\n", "bogus"),
        (HEADER + "subjects:\n  - id: skill/x\n    kind: repo\n", "manifest"),
        (HEADER + "subjects:\n  - id: skill/x\n", "path"),
        (HEADER + "subjects:\n  - id: skill/x\n    path: skills/x\n    manifest: m.yaml\n", "manifest"),
        (HEADER + "subjects:\n  - id: skill/x\n    kind: catalog\n    path: skills/x\n", "kind"),
        (HEADER + "baseline:\n  store: s3://bucket\n", "store"),
        (HEADER + "plan:\n  reps: 0\n", "reps"),
        (HEADER + "budget:\n  pr_max_usd: -1\n", "pr_max_usd"),
        ("- just\n- a list\n", "mapping"),
        (HEADER + "subjects: [unclosed\n", "cannot read"),
    ],
)
def test_an_invalid_config_is_refused_with_the_file_and_the_reason(tmp_path, body, needle):
    root = _tree(tmp_path / "repo", {".github/dokimi/config.yaml": body})
    with pytest.raises(ConfigError, match=needle) as err:
        load_repo_config(root)
    assert ".github/dokimi/config.yaml" in str(err.value)


def test_a_valid_full_config_loads_with_plan_budget_and_baseline(tmp_path):
    body = (
        HEADER
        + "shared: [scripts/harness/**]\n"
        + "plan: {models: [sonnet], reps: 3, arms: [none, approved, candidate]}\n"
        + "budget: {pr_max_usd: 5, record_max_usd: 30}\n"
        + "baseline: {store: 'branch:dokimi-baselines'}\n"
    )
    root = _tree(tmp_path / "repo", {".github/dokimi/config.yaml": body})
    config = load_repo_config(root)
    assert config.data["plan"]["reps"] == 3
    assert config.data["baseline"]["store"] == "branch:dokimi-baselines"


# --- subject resolution ------------------------------------------------------------------------


def test_beside_layout_gives_one_skill_subject_per_directory_with_its_evals(tmp_path):
    root = _tree(tmp_path / "repo", BESIDE)
    subjects = load_repo_config(root).subjects(hashes=False)
    assert [s.id for s in subjects] == ["skill/fastcontext", "skill/onetbb"]
    onetbb = subjects[1]
    assert (onetbb.kind, onetbb.path, onetbb.suite_globs) == ("skill", "skills/onetbb", ("skills/onetbb/evals",))
    assert onetbb.skill_sha is None and onetbb.suite_hash is None


def test_central_layout_uses_the_explicit_suite_glob(tmp_path):
    root = _tree(tmp_path / "repo", CENTRAL)
    subjects = {s.id: s for s in load_repo_config(root).subjects(hashes=False)}
    assert set(subjects) == {"skill/onetbb"}
    assert subjects["skill/onetbb"].suite_globs == ("evaluation/harbor/tasks/onetbb-*",)


def test_an_explicit_subject_replaces_the_default_with_the_same_id(tmp_path):
    root = _tree(tmp_path / "repo", CENTRAL)
    assert len(load_repo_config(root).subjects(hashes=False)) == 1


def test_an_explicit_subject_replaces_the_default_with_the_same_directory(tmp_path):
    body = HEADER + "defaults:\n  skills: skills/*\nsubjects:\n  - id: skill/ot\n    path: skills/onetbb\n"
    root = _tree(tmp_path / "repo", {**BESIDE, ".github/dokimi/config.yaml": body})
    ids = [s.id for s in load_repo_config(root).subjects(hashes=False)]
    assert ids == ["skill/fastcontext", "skill/ot"]


def test_a_repository_subject_takes_its_manifest_and_no_skill(tmp_path):
    body = HEADER + "subjects:\n  - id: repo\n    kind: repo\n    manifest: .github/dokimi/repo.yaml\n"
    root = _tree(tmp_path / "repo", {".github/dokimi/config.yaml": body, ".github/dokimi/repo.yaml": "x: 1\n"})
    (subject,) = load_repo_config(root).subjects(hashes=False)
    assert (subject.kind, subject.path, subject.manifest) == ("repo", ".", ".github/dokimi/repo.yaml")
    assert subject.suite_globs == ()


@pytest.mark.parametrize(
    ("body", "files", "needle"),
    [
        (HEADER + "subjects:\n  - id: skill/a\n    path: skills/a\n  - id: skill/a\n    path: skills/b\n",
         {"skills/a/SKILL.md": "a", "skills/b/SKILL.md": "b"}, "more than one subject"),
        (HEADER + "subjects:\n  - id: skill/a\n    path: skills/a\n  - id: skill/b\n    path: skills/a\n",
         {"skills/a/SKILL.md": "a"}, "share the skill directory"),
        (HEADER + "subjects:\n  - id: skill/a\n    path: skills/missing\n", {}, "is not a directory"),
        (HEADER + "subjects:\n  - id: skill/a\n    path: ../outside\n", {}, "inside the repository"),
        (HEADER + "subjects:\n  - id: repo\n    kind: repo\n    manifest: nope.yaml\n", {}, "not a file"),
        (HEADER + "subjects:\n  - id: repo\n    kind: repo\n    manifest: m.yaml\n    suite: '{skill}/evals'\n",
         {"m.yaml": "m"}, "needs a skill subject"),
        (HEADER + "subjects:\n  - id: skill/a\n    path: skills/a\n    suite: '{skill}'\n",
         {"skills/a/SKILL.md": "a"}, "whole skill directory"),
    ],
)
def test_a_subject_that_does_not_resolve_is_refused(tmp_path, body, files, needle):
    root = _tree(tmp_path / "repo", {".github/dokimi/config.yaml": body, **files})
    with pytest.raises(ConfigError, match=needle):
        load_repo_config(root).subjects(hashes=False)


def test_two_skill_directories_with_one_name_need_an_explicit_id(tmp_path):
    body = HEADER + "defaults:\n  skills: skills/*/*\n"
    files = {".github/dokimi/config.yaml": body, "skills/a/x/SKILL.md": "a", "skills/b/x/SKILL.md": "b"}
    root = _tree(tmp_path / "repo", files)
    with pytest.raises(ConfigError, match="more than one subject"):
        load_repo_config(root).subjects(hashes=False)


# --- skill_sha and suite_hash ------------------------------------------------------------------


def test_eval_edits_do_not_move_skill_sha_but_move_suite_hash(tmp_path):
    root = _tree(tmp_path / "repo", BESIDE)
    before = _subjects(root)["skill/onetbb"]
    (root / "skills/onetbb/evals/questions.json").write_text("[1]\n", encoding="utf-8")
    (root / "skills/onetbb/evals/task-2").mkdir()
    (root / "skills/onetbb/evals/task-2/task.toml").write_text("id = 'two'\n", encoding="utf-8")
    after = _subjects(root)["skill/onetbb"]
    assert after.skill_sha == before.skill_sha
    assert after.suite_hash != before.suite_hash


def test_a_skill_edit_moves_skill_sha_and_a_file_outside_the_skill_does_not(tmp_path):
    root = _tree(tmp_path / "repo", BESIDE)
    before = _subjects(root)["skill/onetbb"].skill_sha
    (root / "README.md").write_text("changed\n", encoding="utf-8")
    assert _subjects(root)["skill/onetbb"].skill_sha == before
    (root / "skills/onetbb/SKILL.md").write_text("# changed\n", encoding="utf-8")
    assert _subjects(root)["skill/onetbb"].skill_sha != before


def test_skill_sha_is_the_same_for_the_same_skill_content_at_another_path(tmp_path):
    a = _tree(tmp_path / "a", BESIDE)
    b = _tree(tmp_path / "b", BESIDE)
    assert _subjects(a)["skill/onetbb"].skill_sha == _subjects(b)["skill/onetbb"].skill_sha
    assert _subjects(a)["skill/onetbb"].skill_sha.startswith("sha256:")


def test_central_layout_suite_is_outside_the_skill_so_its_edits_leave_skill_sha_alone(tmp_path):
    root = _tree(tmp_path / "repo", CENTRAL)
    before = _subjects(root)["skill/onetbb"]
    (root / "evaluation/harbor/tasks/onetbb-001/task.toml").write_text("id = 'edited'\n", encoding="utf-8")
    after = _subjects(root)["skill/onetbb"]
    assert after.skill_sha == before.skill_sha
    assert after.suite_hash != before.suite_hash


def test_a_suite_glob_matches_only_what_it_names(tmp_path):
    root = _tree(tmp_path / "repo", CENTRAL)
    before = _subjects(root)["skill/onetbb"].suite_hash
    (root / "evaluation/harbor/tasks/other-001/task.toml").write_text("id = 'x'\n", encoding="utf-8")
    assert _subjects(root)["skill/onetbb"].suite_hash == before


def test_a_repository_subject_hashes_its_manifest_and_has_no_skill_sha(tmp_path):
    body = HEADER + "subjects:\n  - id: repo\n    kind: repo\n    manifest: m.yaml\n"
    root = _tree(tmp_path / "repo", {".github/dokimi/config.yaml": body, "m.yaml": "a: 1\n"})
    before = _subjects(root)["repo"]
    assert before.skill_sha is None and before.suite_hash.startswith("sha256:")
    (root / "m.yaml").write_text("a: 2\n", encoding="utf-8")
    assert _subjects(root)["repo"].suite_hash != before.suite_hash


# --- changed files -> subjects -----------------------------------------------------------------


def test_changed_files_map_to_skills_and_suites_and_the_rest_is_unclaimed(tmp_path):
    root = _tree(tmp_path / "repo", BESIDE)
    impact = load_repo_config(root).subjects_for_changed([
        "skills/onetbb/SKILL.md",
        "skills/onetbb/evals/task-1/task.toml",
        "./skills/fastcontext/SKILL.md",
        "README.md",
    ])
    assert impact.skills == {"skill/onetbb": ["skills/onetbb/SKILL.md"], "skill/fastcontext": ["skills/fastcontext/SKILL.md"]}
    assert impact.suites == {"skill/onetbb": ["skills/onetbb/evals/task-1/task.toml"]}
    assert impact.subjects == ["skill/fastcontext", "skill/onetbb"]
    assert impact.unclaimed == ["README.md"]
    assert impact.shared == []


def test_a_deleted_eval_is_still_claimed_by_its_suite_glob(tmp_path):
    root = _tree(tmp_path / "repo", CENTRAL)
    impact = load_repo_config(root).subjects_for_changed(["evaluation/harbor/tasks/onetbb-009/task.toml"])
    assert impact.suites == {"skill/onetbb": ["evaluation/harbor/tasks/onetbb-009/task.toml"]}
    assert impact.unclaimed == [] and impact.skills == {}


def test_shared_paths_and_the_config_itself_are_smoke_and_not_unclaimed(tmp_path):
    body = CENTRAL_CONFIG + "shared: [scripts/harness/**]\n"
    root = _tree(tmp_path / "repo", {**CENTRAL, ".github/dokimi/config.yaml": body})
    impact = load_repo_config(root).subjects_for_changed([
        "scripts/harness/run.sh",
        ".github/dokimi/config.yaml",
        "docs/unrelated.md",
    ])
    assert impact.shared == ["scripts/harness/run.sh", ".github/dokimi/config.yaml"]
    assert impact.unclaimed == ["docs/unrelated.md"]


def test_a_shared_file_that_a_subject_claims_is_both_smoke_and_attributed(tmp_path):
    body = CENTRAL_CONFIG + "shared: [skills/onetbb/SKILL.md]\n"
    root = _tree(tmp_path / "repo", {**CENTRAL, ".github/dokimi/config.yaml": body})
    impact = load_repo_config(root).subjects_for_changed(["skills/onetbb/SKILL.md"])
    assert impact.shared == ["skills/onetbb/SKILL.md"]
    assert impact.skills == {"skill/onetbb": ["skills/onetbb/SKILL.md"]}
    assert impact.unclaimed == []


def test_a_changed_path_that_climbs_out_or_is_absolute_is_unclaimed(tmp_path):
    root = _tree(tmp_path / "repo", BESIDE)
    impact = load_repo_config(root).subjects_for_changed(["/etc/passwd", "../secret", ""])
    assert impact.unclaimed == ["/etc/passwd", "../secret", ""]


def test_a_repository_subject_claims_its_manifest_and_nothing_else(tmp_path):
    body = HEADER + "subjects:\n  - id: repo\n    kind: repo\n    manifest: .github/dokimi/repo.yaml\n"
    root = _tree(tmp_path / "repo", {".github/dokimi/config.yaml": body, ".github/dokimi/repo.yaml": "x: 1\n"})
    impact = load_repo_config(root).subjects_for_changed([".github/dokimi/repo.yaml", "src/main.py"])
    assert impact.suites == {"repo": [".github/dokimi/repo.yaml"]}
    assert impact.unclaimed == ["src/main.py"]


def test_a_file_goes_to_the_deepest_of_two_nested_skills(tmp_path):
    body = HEADER + (
        "subjects:\n"
        "  - id: skill/outer\n    path: skills/outer\n"
        "  - id: skill/inner\n    path: skills/outer/inner\n"
    )
    files = {
        ".github/dokimi/config.yaml": body,
        "skills/outer/SKILL.md": "outer\n",
        "skills/outer/inner/SKILL.md": "inner\n",
    }
    root = _tree(tmp_path / "repo", files)
    impact = load_repo_config(root).subjects_for_changed(["skills/outer/inner/SKILL.md", "skills/outer/notes.md"])
    assert impact.skills == {"skill/inner": ["skills/outer/inner/SKILL.md"], "skill/outer": ["skills/outer/notes.md"]}


def test_load_reads_a_file_and_roots_it_where_told(tmp_path):
    root = _tree(tmp_path / "repo", BESIDE)
    config = load(root / ".github/dokimi/config.yaml", root=root)
    assert config.root == root and config.path == root / ".github/dokimi/config.yaml"
