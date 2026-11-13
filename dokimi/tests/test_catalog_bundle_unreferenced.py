# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``bundle-file-unreferenced``: bundled scripts and documents SKILL.md never names.

The agent finds such a file only by browsing, so the check names it.
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


@pytest.mark.parametrize("doc", ["README.md", "AGENTS.md"])
def test_a_skipped_document_that_skill_md_names_is_still_read(tmp_path, doc):
    # Not counted itself, but an agent that opens it is pointed at the script it names.
    skill_dir = _skill(tmp_path / "skills", "kit", name="kit", description="Use when testing.", body=f"See {doc}.\n")
    (skill_dir / doc).write_text("Run `scripts/run.py` first.\n", encoding="utf-8")
    (skill_dir / "scripts").mkdir()
    (skill_dir / "scripts" / "run.py").write_text("x\n", encoding="utf-8")
    [review] = review_skills(read_skill_manifest(tmp_path))
    assert "bundle-file-unreferenced" not in _codes(review)


@pytest.mark.parametrize(
    ("doc", "content", "script"),
    [
        ("forms.md", "Run [fill](scripts/fill%2Epy).\n", "scripts/fill.py"),  # percent-encoded
        ("references/forms.md", "Run [fill](../scripts/fill%2Epy).\n", "scripts/fill.py"),  # encoded, relative to its folder
        ("forms.md", "Run [fill](scripts/fill\\_x.py).\n", "scripts/fill_x.py"),  # backslash escape
    ],
)
def test_a_link_in_a_read_document_is_decoded_like_skill_md(tmp_path, doc, content, script):
    skill_dir = _skill(tmp_path / "skills", "kit", name="kit", description="Use when testing.", body=f"See {doc}.\n")
    (skill_dir / doc).parent.mkdir(parents=True, exist_ok=True)
    (skill_dir / doc).write_text(content, encoding="utf-8")
    (skill_dir / script).parent.mkdir(parents=True, exist_ok=True)
    (skill_dir / script).write_text("x\n", encoding="utf-8")
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


def _unreferenced(review):
    return [f.message for f in review.findings if f.code == "bundle-file-unreferenced"]


def test_a_name_followed_by_a_dotted_suffix_does_not_name_the_shorter_file(tmp_path):
    review = _review(tmp_path, "Keep a copy as build.py.bak before editing.\n", ["scripts/build.py"])
    [message] = _unreferenced(review)
    assert message.startswith("1 of 1") and "scripts/build.py" in message


@pytest.mark.parametrize(
    "body",
    [
        "Helpers in ./scripts/ do the work.\n",
        "Helpers in ${CLAUDE_SKILL_DIR}/scripts/ do the work.\n",
        "See [helpers](./scripts/).\n",
        "See [helpers](${CLAUDE_SKILL_DIR}/scripts/).\n",
        "The helpers live in scripts/.\n",
    ],
)
def test_a_folder_written_from_the_skill_root_names_its_files(tmp_path, body):
    review = _review(tmp_path, body, ["scripts/fill.py"])
    assert _unreferenced(review) == []


def _review_with_texts(tmp_path, body, texts):
    skill_dir = _skill(tmp_path / "skills", "kit", name="kit", description="Use when testing.", body=body)
    for rel, text in texts.items():
        target = skill_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    [review] = review_skills(read_skill_manifest(tmp_path))
    return review


def test_a_folder_linked_from_a_nested_document_names_its_files_there(tmp_path):
    review = _review_with_texts(
        tmp_path,
        "Read [guide](references/guide.md).\n",
        {"references/guide.md": "See [scripts](scripts/).\n", "references/scripts/run.py": "x\n"},
    )
    assert _unreferenced(review) == []


def test_a_nested_folder_link_does_not_name_a_root_folder_of_the_same_name(tmp_path):
    review = _review_with_texts(
        tmp_path,
        "Read [guide](references/guide.md).\n",
        {
            "references/guide.md": "See [scripts](scripts/).\n",
            "references/scripts/run.py": "x\n",
            "scripts/other.py": "x\n",
        },
    )
    [message] = _unreferenced(review)
    assert message.startswith("1 of 3") and "scripts/other.py" in message


def test_a_skill_dir_link_in_a_nested_document_resolves_from_the_skill_root(tmp_path):
    review = _review_with_texts(
        tmp_path,
        "Read [guide](references/guide.md).\n",
        {"references/guide.md": "[run](${CLAUDE_SKILL_DIR}/scripts/my%20tool.py)\n", "scripts/my tool.py": "x\n"},
    )
    assert _unreferenced(review) == []


def test_a_bundle_over_the_file_cap_is_a_checkout_and_is_not_checked(tmp_path, monkeypatch):
    from dokimi.external import bundle_refs

    monkeypatch.setattr(bundle_refs, "WALK_MAX_FILES", 5)
    review = _review(tmp_path, "No names here.\n", [f"scripts/f{i}.py" for i in range(6)])
    assert _unreferenced(review) == []


def test_a_bundle_at_the_file_cap_is_checked_with_its_true_count(tmp_path, monkeypatch):
    from dokimi.external import bundle_refs

    monkeypatch.setattr(bundle_refs, "WALK_MAX_FILES", 5)
    review = _review(tmp_path, "No names here.\n", [f"scripts/f{i}.py" for i in range(5)])
    [message] = _unreferenced(review)
    assert message.startswith("5 of 5")


def test_a_named_document_past_the_read_cap_is_not_judged(tmp_path, monkeypatch):
    from dokimi.external import bundle_refs

    monkeypatch.setattr(bundle_refs, "DOCS_READ_MAX", 2)
    review = _review_with_texts(
        tmp_path,
        "Read [a](docs/a.md), [b](docs/b.md), [c](docs/c.md).\n",
        {"docs/a.md": "A.\n", "docs/b.md": "B.\n", "docs/c.md": "Run `scripts/run.py`.\n", "scripts/run.py": "x\n"},
    )
    assert _unreferenced(review) == []


def test_a_bundle_with_more_documents_than_the_walk_cap_is_not_judged(tmp_path, monkeypatch):
    from dokimi.external import bundle_refs

    monkeypatch.setattr(bundle_refs, "WALK_MAX_FILES", 3)
    # ``_`` documents are not counted, so the document walk, not the file count, is what is over the cap.
    texts = {f"docs/_{name}.md": "Nothing.\n" for name in "abcd"}
    texts["docs/_z.md"] = "Run `scripts/run.py`.\n"
    texts["scripts/run.py"] = "x\n"
    review = _review_with_texts(tmp_path, "Read [z](docs/_z.md).\n", texts)
    assert _unreferenced(review) == []


def test_a_plain_folder_mention_in_a_nested_document_names_that_documents_folder(tmp_path):
    review = _review_with_texts(
        tmp_path,
        "Read [guide](references/guide.md).\n",
        {
            "references/guide.md": "Helpers in ./scripts/ do the work.\n",
            "references/scripts/run.py": "x\n",
            "scripts/other.py": "x\n",
        },
    )
    [message] = _unreferenced(review)
    assert "scripts/other.py" in message
    assert "references/scripts/run.py" not in message


@pytest.mark.parametrize("folder", ["tests", "test", "__tests__", "evals", ".github"])
def test_a_named_document_in_an_excluded_folder_names_its_files(tmp_path, folder):
    # The folder is left out of the count, but a document in it that SKILL.md names still names what it links.
    review = _review_with_texts(
        tmp_path,
        f"See {folder}/guide.md.\n",
        {f"{folder}/guide.md": "Run `scripts/run.py`.\n", "scripts/run.py": "x\n"},
    )
    assert _unreferenced(review) == []


@pytest.mark.parametrize("doc", ["CHANGELOG.md", "CONTRIBUTING.md", "NOTICE.md"])
def test_a_changelog_contributing_or_notice_does_not_name_files(tmp_path, doc):
    # Only README and AGENTS-type documents supply references; a changelog that mentions a script does not.
    review = _review_with_texts(tmp_path, f"See {doc}.\n", {doc: "Run `scripts/run.py`.\n", "scripts/run.py": "x\n"})
    [message] = _unreferenced(review)
    assert "1 of 1" in message and "scripts/run.py" in message


def test_a_readme_names_the_files_it_points_to(tmp_path):
    review = _review_with_texts(tmp_path, "See README.md.\n", {"README.md": "Run `scripts/run.py`.\n", "scripts/run.py": "x\n"})
    assert _unreferenced(review) == []


def test_a_named_document_over_the_read_cap_gives_no_claim(tmp_path, monkeypatch):
    from dokimi.external import bundle_refs

    monkeypatch.setattr(bundle_refs, "DOC_MAX_BYTES", 32)
    review = _review_with_texts(
        tmp_path,
        "See guide.md.\n",
        {"guide.md": "Run `scripts/run.py` first, then read the rest of this guide.\n", "scripts/run.py": "x\n"},
    )
    assert _unreferenced(review) == []


def test_a_named_document_that_is_not_utf8_gives_no_claim(tmp_path):
    skill_dir = _skill(tmp_path / "skills", "kit", name="kit", description="Use when testing.", body="See bad.md.\n")
    (skill_dir / "bad.md").write_bytes(b"\xff\xfeRun scripts/run.py\n")
    (skill_dir / "scripts").mkdir()
    (skill_dir / "scripts" / "run.py").write_text("x\n", encoding="utf-8")
    [review] = review_skills(read_skill_manifest(tmp_path))
    assert _unreferenced(review) == []


def test_a_named_symlinked_document_gives_no_claim(tmp_path):
    skill_dir = _skill(tmp_path / "skills", "kit", name="kit", description="Use when testing.", body="See link.md.\n")
    (skill_dir / "real.md").write_text("Run `scripts/run.py`.\n", encoding="utf-8")
    os.symlink(skill_dir / "real.md", skill_dir / "link.md")
    (skill_dir / "scripts").mkdir()
    (skill_dir / "scripts" / "run.py").write_text("x\n", encoding="utf-8")
    [review] = review_skills(read_skill_manifest(tmp_path))
    assert _unreferenced(review) == []


def test_a_bundle_with_many_files_and_large_documents_is_judged_quickly(tmp_path):
    import time

    from dokimi.external.bundle_refs import unreferenced_message
    from dokimi.external.validate import _link_files

    # Only the bundle search is timed, in CPU time: a shared CI runner under load runs several times slower.
    # The bound is loose; the quadratic scan this guards against took minutes on the same input.
    docs = {
        f"docs/d{i:02d}.md": "filler line for the document\n" * 2000 + f"Run `scripts/s{i:02d}.py`.\n"
        for i in range(30)
    }
    texts = dict(docs)
    texts.update({f"scripts/s{i:02d}.py": "x\n" for i in range(30)})
    texts.update({f"scripts/u{i:03d}.py": "x\n" for i in range(300)})
    body = "See " + ", ".join(docs) + ".\n"
    skill_dir = _skill(tmp_path / "skills", "kit", name="kit", description="Use when testing.", body=body)
    for rel, text in texts.items():
        target = skill_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    links = _link_files(body)
    started = time.process_time()
    message = unreferenced_message(skill_dir / "SKILL.md", links, at_root=False, doc_links=_link_files)
    elapsed = time.process_time() - started
    assert message is not None
    assert "300 of 360" in message  # 30 documents, 30 scripts they name, and 300 they do not
    assert elapsed < 30


def test_a_document_named_by_a_hidden_file_name_is_read(tmp_path):
    review = _review_with_texts(
        tmp_path,
        "Read [guide](.guide.md).\n",
        {".guide.md": "Run scripts/run.py.\n", "scripts/run.py": "x\n"},
    )
    assert _unreferenced(review) == []


def test_a_named_document_over_the_read_cap_names_nothing_it_holds(tmp_path, monkeypatch):
    # Reading the over-cap document would name run.py and leave other.py unnamed: the claim must be withheld.
    from dokimi.external import bundle_refs

    monkeypatch.setattr(bundle_refs, "DOC_MAX_BYTES", 32)
    review = _review_with_texts(
        tmp_path,
        "Read big.md.\n",
        {
            "big.md": "Run scripts/run.py first, then read the rest of this document.\n",
            "scripts/run.py": "x\n",
            "scripts/other.py": "x\n",
        },
    )
    assert _unreferenced(review) == []


def test_a_folder_written_with_a_backslash_names_its_files(tmp_path):
    review = _review(tmp_path, "Helpers are in `scripts\\`.\n", ["scripts/helper.py"])
    assert _unreferenced(review) == []


@pytest.mark.parametrize(
    "guide",
    [
        'See [helpers](scripts/ "Helpers").\n',  # a title after the folder
        "See [helpers](<scripts/>).\n",  # angle brackets around the folder
        "See [helpers][h].\n\n[h]: scripts/\n",  # a reference definition for the folder
    ],
)
def test_a_folder_link_with_a_title_or_brackets_names_the_folder_of_its_document(tmp_path, guide):
    review = _review_with_texts(
        tmp_path,
        "Read [guide](references/guide.md).\n",
        {
            "references/guide.md": guide,
            "references/scripts/run.py": "x\n",
            "scripts/other.py": "x\n",
        },
    )
    [message] = _unreferenced(review)
    assert "(scripts/other.py)" in message
    assert "references/scripts/run.py" not in message


def test_the_unnamed_files_are_listed_in_path_order(tmp_path):
    review = _review(tmp_path, "Use it.\n", ["zeta.py", "scripts/a.py"])
    [message] = _unreferenced(review)
    assert "(scripts/a.py, zeta.py)" in message
