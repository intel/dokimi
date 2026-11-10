# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``bundle-file-unreferenced``: bundled scripts and documents SKILL.md never names.

In the GitSkills corpus 63% of bundled ``.py``, 45% of ``.sh`` and 47% of ``.md`` files are never
named in their SKILL.md (scratch X4): the agent finds them only by browsing.
"""

from __future__ import annotations

import os

import pytest

from dokimi.external import read_skill_manifest, review_skill_dir, review_skills
from tests.test_catalog_validate import _codes, _skill


def _review(tmp_path, body, files):
    skill_dir = _skill(tmp_path / "skills", "kit", name="kit", description="Use when testing.", body=body)
    for rel in files:
        target = skill_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x\n", encoding="utf-8")
    [review] = review_skills(read_skill_manifest(tmp_path))
    return review


@pytest.mark.parametrize(
    "body",
    [
        "Run `python scripts/build.py`.\n",  # the path
        "Run build.py from the scripts folder.\n",  # the file name
        "See [the builder](scripts/build.py).\n",  # a link
        "See [the builder](./scripts/build%2Epy).\n",  # a link, percent-encoded
        "Everything is in `scripts/`.\n",  # the folder
    ],
)
def test_a_named_file_is_fine(tmp_path, body):
    review = _review(tmp_path, body, ["scripts/build.py"])
    assert "bundle-file-unreferenced" not in _codes(review)


@pytest.mark.parametrize(
    ("body", "bundled", "unnamed"),
    [
        ("Run rebuild.py.\n", ["scripts/build.py", "scripts/rebuild.py"], "scripts/build.py"),  # a longer name
        ("Read guide.mdx.\n", ["guide.md", "guide.mdx"], "guide.md"),  # a longer extension
        ("Run scripts/rebuild.py.\n", ["build.py", "scripts/rebuild.py"], "build.py"),  # a longer path
    ],
)
def test_a_name_inside_a_longer_name_does_not_name_the_shorter_file(tmp_path, body, bundled, unnamed):
    review = _review(tmp_path, body, bundled)
    [finding] = [f for f in review.findings if f.code == "bundle-file-unreferenced"]
    assert finding.message.startswith("1 of 2") and unnamed in finding.message


def test_unnamed_files_are_one_warning_with_the_count(tmp_path):
    review = _review(
        tmp_path,
        "Run `python scripts/build.py`.\n",
        ["scripts/build.py", "scripts/helper.sh", "references/api.md", "references/old.md", "lib/x.ts", "lib/y.js"],
    )
    [finding] = [f for f in review.findings if f.code == "bundle-file-unreferenced"]
    assert finding.level == "warn"
    assert finding.message.startswith("5 of 6 bundled scripts and documents")
    assert "lib/x.ts, lib/y.js, references/api.md, ..." in finding.message


@pytest.mark.parametrize(
    "rel",
    [
        "LICENSE.txt",
        "README.md",
        "scripts/__init__.py",
        "tests/test_build.py",
        ".hidden/x.py",
        "node_modules/pkg/index.js",
        "assets/logo.png",
        "data/table.json",
        "scripts/.env.sh",
    ],
)
def test_files_the_agent_is_not_meant_to_open_are_not_counted(tmp_path, rel):
    review = _review(tmp_path, "# Body\n", [rel])
    assert "bundle-file-unreferenced" not in _codes(review)


@pytest.mark.parametrize("rel", ["scripts/test_build.py", "scripts/build_test.py", "scripts/build.test.js", "lib/build.spec.ts"])
def test_test_files_outside_a_tests_folder_are_not_counted(tmp_path, rel):
    review = _review(tmp_path, "# Body\n", [rel])
    assert "bundle-file-unreferenced" not in _codes(review)


@pytest.mark.parametrize("doc", ["README.md", "CONTRIBUTING.md", "AGENTS.md"])
def test_a_skipped_document_that_skill_md_names_is_still_read(tmp_path, doc):
    # Not counted itself, but an agent that opens it is pointed at the script it names.
    skill_dir = _skill(tmp_path / "skills", "kit", name="kit", description="Use when testing.", body=f"See {doc}.\n")
    (skill_dir / doc).write_text("Run `scripts/run.py` first.\n", encoding="utf-8")
    (skill_dir / "scripts").mkdir()
    (skill_dir / "scripts" / "run.py").write_text("x\n", encoding="utf-8")
    [review] = review_skills(read_skill_manifest(tmp_path))
    assert "bundle-file-unreferenced" not in _codes(review)


def test_a_nested_skill_is_not_part_of_the_bundle(tmp_path):
    _review(tmp_path, "# Body\n", [])
    _skill(tmp_path / "skills" / "kit", "inner", name="inner", description="Use when nested.")
    (tmp_path / "skills" / "kit" / "inner" / "run.py").write_text("x\n", encoding="utf-8")
    entries = read_skill_manifest(tmp_path)
    by_name = {e.name: r for e, r in zip(entries, review_skills(entries), strict=True)}
    assert "bundle-file-unreferenced" not in _codes(by_name["kit"])
    assert "bundle-file-unreferenced" in _codes(by_name["inner"])  # its own bundle


def test_a_skill_at_the_checkout_root_is_not_checked(tmp_path):
    (tmp_path / "SKILL.md").write_text("---\nname: root\ndescription: Use when rooted.\n---\n# Body\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x\n", encoding="utf-8")
    [review] = review_skills(read_skill_manifest(tmp_path))
    assert "bundle-file-unreferenced" not in _codes(review)


def test_a_standalone_review_checks_its_own_bundle(tmp_path):
    skill_dir = _skill(tmp_path, "solo", name="solo", description="Use when alone.")
    (skill_dir / "run.sh").write_text("x\n", encoding="utf-8")
    assert "bundle-file-unreferenced" in [f.code for f in review_skill_dir(skill_dir).findings]


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs named pipes")
@pytest.mark.timeout(20)
def test_a_named_pipe_a_document_names_is_not_read(tmp_path):
    # Reading a FIFO blocks until a writer opens it: the scan must not open it.
    skill_dir = _skill(tmp_path / "skills", "kit", name="kit", description="Use when testing.", body="See notes.md.\n")
    os.mkfifo(skill_dir / "notes.md")
    [review] = review_skills(read_skill_manifest(tmp_path))
    assert "bundle-file-unreferenced" not in _codes(review)


def test_case_is_ignored(tmp_path):
    review = _review(tmp_path, "Read FORMS.md first.\n", ["forms.md"])
    assert "bundle-file-unreferenced" not in _codes(review)


def test_a_file_named_only_in_a_named_document_counts(tmp_path):
    skill_dir = _skill(tmp_path / "skills", "kit", name="kit", description="Use when testing.", body="See forms.md.\n")
    (skill_dir / "forms.md").write_text("Run `scripts/fill.py`, then read deep.md.\n", encoding="utf-8")
    (skill_dir / "deep.md").write_text("Then `scripts/check.sh`.\n", encoding="utf-8")
    (skill_dir / "scripts").mkdir()
    for name in ("fill.py", "check.sh", "lost.py"):
        (skill_dir / "scripts" / name).write_text("x\n", encoding="utf-8")
    [review] = review_skills(read_skill_manifest(tmp_path))
    [finding] = [f for f in review.findings if f.code == "bundle-file-unreferenced"]
    assert finding.message.startswith("1 of 5") and "scripts/lost.py" in finding.message


@pytest.mark.parametrize("rel", ["scripts/_common.py", "AGENTS.md", "CLAUDE.md"])
def test_private_helpers_and_agent_files_are_not_counted(tmp_path, rel):
    review = _review(tmp_path, "# Body\n", [rel])
    assert "bundle-file-unreferenced" not in _codes(review)
