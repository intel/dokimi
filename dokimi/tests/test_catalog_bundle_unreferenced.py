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
        {
            "docs/a.md": "A.\n",
            "docs/b.md": "B.\n",
            "docs/c.md": "Run `scripts/run.py`.\n",
            "scripts/run.py": "x\n",
            "scripts/other.py": "x\n",
        },
    )
    # The third document is past the cap, so the search is incomplete and nothing is claimed. With ``>`` it would be
    # read, name ``scripts/run.py`` and leave ``scripts/other.py`` unnamed: the claim this test must not make.
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


def test_a_nested_document_in_a_folder_with_capitals_names_that_folder(tmp_path):
    # The folder keeps its capitals in the walk, but the document's text is lowercased: its ./ path must match.
    review = _review_with_texts(
        tmp_path,
        "Read [guide](References/guide.md).\n",
        {
            "References/guide.md": "Helpers in ./scripts/ do the work.\n",
            "References/scripts/run.py": "x\n",
            "scripts/other.py": "x\n",
        },
    )
    [message] = _unreferenced(review)
    assert "scripts/other.py" in message
    assert "References/scripts/run.py" not in message


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


def test_a_named_word_list_of_350_kib_is_read_whole(tmp_path):
    # Under the 1 MiB read cap: the list names no script, so the script it does not name is reported.
    review = _review_with_texts(
        tmp_path,
        "It reads data/words.txt.\n",
        {"data/words.txt": "abcdefg\n" * 44800, "scripts/unused.py": "x\n"},  # exactly 350 KiB
    )
    [message] = _unreferenced(review)
    assert "(scripts/unused.py)" in message


def test_a_named_document_over_the_read_cap_is_said_to_be_truncated(tmp_path):
    # Over 1 MiB the document is not read, so no claim is made, and the note names the file and says it was truncated.
    review = _review_with_texts(
        tmp_path,
        "It reads data/words.txt.\n",
        {"data/words.txt": "abcdefg\n" * 150000, "scripts/unused.py": "x\n"},
    )
    assert _unreferenced(review) == []
    [note] = [f for f in review.findings if f.code == "bundle-file-unchecked"]
    assert "data/words.txt" in note.message and "truncated" in note.message


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


@pytest.mark.parametrize(
    ("body", "texts"),
    [
        # ``../`` from the skill root leaves the skill: it names nothing inside it
        ("Helpers live in ../scripts/.\n", {}),
        # from a nested folder, two levels up is above the skill root too
        ("Read [g](references/g.md).\n", {"references/g.md": "Run the tools in ../../scripts/.\n"}),
    ],
)
def test_a_folder_written_above_the_skill_root_names_no_bundled_folder(tmp_path, body, texts):
    texts = {**texts, "scripts/unused.py": "x\n"}
    review = _review_with_texts(tmp_path, body, texts)
    [message] = _unreferenced(review)
    assert message.startswith("1 of ")  # the nested case also counts the document it names
    assert "(scripts/unused.py)" in message


def test_a_bundle_with_many_files_and_large_documents_is_judged_quickly(tmp_path):
    import time

    from dokimi.external.bundle_refs import unreferenced_message
    from dokimi.external.validate import _link_files

    # Only the bundle search is timed, in CPU time, and the bound is loose: a loaded runner is several times slower.
    # A search per file took 32 s on this input; the name lookup takes about 2 s.
    docs = {
        f"docs/d{i:02d}.md": "filler line for the document\n" * 7500 + f"Run `scripts/s{i:02d}.py`.\n"
        for i in range(50)
    }
    texts = dict(docs)
    texts.update({f"scripts/s{i:02d}.py": "x\n" for i in range(50)})
    texts.update({f"scripts/u{i:04d}.py": "x\n" for i in range(1900)})
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
    assert "1900 of 2000" in message  # 50 documents, 50 scripts they name, and 1900 they do not
    assert elapsed < 10


@pytest.mark.parametrize(
    "bomb",
    ["](/" * 15000, "](" + "/#" * 15000, "](a/ (" * 20000, "[x]: " + "/#" * 15000 + "<"],
    ids=["unclosed-links", "one-unclosed-link", "unclosed-titles", "reference-definition"],
)
def test_a_hostile_line_does_not_stall_the_check(tmp_path, bomb):
    import time

    # Each line took quadratic time in the folder-link scan: tens of seconds here, and hours at the 1 MiB read cap.
    texts = {"references/notes.md": bomb + "\nRun scripts/run.py.\n", "scripts/run.py": "x\n", "scripts/unused.py": "x\n"}
    started = time.process_time()
    review = _review_with_texts(tmp_path, bomb + "\nRead references/notes.md.\n", texts)
    elapsed = time.process_time() - started
    [message] = _unreferenced(review)
    assert message.startswith("1 of ") and "(scripts/unused.py)" in message
    assert elapsed < 5


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
        "See [helpers][h].\n\n[h]: scripts/ \"Helpers\"\n",  # a reference definition with a title
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


def test_a_backslash_folder_in_a_nested_document_names_its_own_folder(tmp_path):
    review = _review_with_texts(
        tmp_path,
        "Read [guide](references/guide.md).\n",
        {
            "references/guide.md": "Helpers in .\\scripts\\ do the work.\n",
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


def test_an_unreadable_folder_withholds_the_claim(tmp_path, monkeypatch):
    real = os.scandir

    def scandir(path="."):
        if os.path.basename(os.fspath(path)) == "references":
            raise PermissionError(13, "Permission denied", os.fspath(path))
        return real(path)

    monkeypatch.setattr(os, "scandir", scandir)
    review = _review_with_texts(
        tmp_path,
        "Read references/guide.md.\n",
        {"references/guide.md": "Run scripts/run.py.\n", "scripts/run.py": "x\n"},
    )
    assert _unreferenced(review) == []


@pytest.mark.parametrize("link", ["scripts\\", ".\\scripts\\", "${CLAUDE_SKILL_DIR}\\scripts\\"])
def test_a_folder_link_with_a_backslash_names_its_files(tmp_path, link):
    review = _review_with_texts(tmp_path, f"Use [helpers]({link}).\n", {"scripts/helper.py": "x\n"})
    assert _unreferenced(review) == []


def test_a_nested_folder_link_with_a_backslash_names_its_own_folder(tmp_path):
    review = _review_with_texts(
        tmp_path,
        "Read [guide](references/guide.md).\n",
        {
            "references/guide.md": "See [h](..\\scripts\\).\n",
            "scripts/helper.py": "x\n",
            "references/scripts/run.py": "x\n",
        },
    )
    [message] = _unreferenced(review)
    assert "(references/scripts/run.py)" in message


@pytest.mark.parametrize("suffix", ["#section", "?view=all"])
def test_a_nested_folder_link_with_a_fragment_or_query_names_its_own_folder(tmp_path, suffix):
    review = _review_with_texts(
        tmp_path,
        "Read [guide](references/guide.md).\n",
        {
            "references/guide.md": f"See [helpers](scripts/{suffix}).\n",
            "references/scripts/run.py": "x\n",
            "scripts/other.py": "x\n",
        },
    )
    [message] = _unreferenced(review)
    assert "(scripts/other.py)" in message


def test_a_link_to_the_skill_root_names_the_files_under_it(tmp_path):
    review = _review_with_texts(tmp_path, "Use [helpers](./).\n", {"scripts/helper.py": "x\n"})
    assert _unreferenced(review) == []


@pytest.mark.parametrize("back", ["../", "${CLAUDE_SKILL_DIR}/"])
def test_a_nested_back_link_to_the_skill_root_names_no_file(tmp_path, back):
    # Only a root link in SKILL.md names every bundled file: a root link in a reference document names its target.
    review = _review_with_texts(
        tmp_path,
        "Read [guide](references/guide.md).\n",
        {"references/guide.md": f"Use [back]({back}).\n", "scripts/helper.py": "x\n"},
    )
    [message] = _unreferenced(review)
    assert message.startswith("1 of 2") and "scripts/helper.py" in message and "guide.md" not in message


def test_a_root_level_document_linking_home_names_no_file(tmp_path):
    # A document beside SKILL.md is not SKILL.md: its link to the root names its target, not every file.
    review = _review_with_texts(
        tmp_path,
        "Read [guide](guide.md).\n",
        {"guide.md": "Use [home](./).\n", "scripts/helper.py": "x\n"},
    )
    [message] = _unreferenced(review)
    assert message.startswith("1 of 2") and "scripts/helper.py" in message


def test_a_root_link_in_skill_md_names_every_file_even_when_a_document_links_back(tmp_path):
    review = _review_with_texts(
        tmp_path,
        "Use [home](./) and read [guide](references/guide.md).\n",
        {"references/guide.md": "Use [back](../).\n", "scripts/helper.py": "x\n"},
    )
    assert _unreferenced(review) == []


@pytest.mark.parametrize(("prose", "name"), [("Run my\\_script.py.\n", "my_script.py"), ("Run my\\~script.py.\n", "my~script.py"), ("Run my\\*script.py.\n", "my*script.py")])
def test_a_markdown_escape_in_prose_is_not_a_folder_separator(tmp_path, prose, name):
    # A backslash before _, * or ~ is a Markdown escape, not a Windows path separator.
    review = _review_with_texts(tmp_path, prose, {name: "x\n"})
    assert _unreferenced(review) == []


@pytest.mark.parametrize(
    ("prose", "name"),
    [
        ("Run `scripts/my\\ run.py`.\n", "scripts/my run.py"),  # a backslash escapes a space
        ("Run `python scripts/a\\;b.py`.\n", "scripts/a;b.py"),  # a backslash escapes a semicolon
    ],
)
def test_a_markdown_escape_of_a_space_or_semicolon_names_the_file(tmp_path, prose, name):
    review = _review_with_texts(tmp_path, prose, {name: "x\n"})
    assert _unreferenced(review) == []


@pytest.mark.parametrize(
    "dest",
    [
        "${CLAUDE_SKILL_DIR}/",
        "${CLAUDE_SKILL_DIR}",
        "<${CLAUDE_SKILL_DIR}/>",
        "%24%7BCLAUDE_SKILL_DIR%7D/",
        "\\${CLAUDE_SKILL_DIR}/",
        "${CLAUDE_SKILL_DIR}/#top",
        "${CLAUDE_SKILL_DIR}/?a=1",
    ],
)
def test_a_link_to_the_skill_dir_variable_names_the_files_under_it(tmp_path, dest):
    # Same as [home](./): the variable form names the skill root too.
    review = _review_with_texts(tmp_path, f"Use [home]({dest}).\n", {"scripts/helper.py": "x\n"})
    assert _unreferenced(review) == []


@pytest.mark.parametrize(
    ("body", "texts", "unnamed"),
    [
        # _mentions checks every occurrence, not only the first, which is a longer name here
        ("Run rebuild.py and then build.py.\n", {"build.py": "x\n", "rebuild.py": "x\n"}, None),
        ("Run my-build.py.\n", {"build.py": "x\n"}, "build.py"),  # a name after "-" is a longer name
        ("Run build.py-old.\n", {"build.py": "x\n"}, "build.py"),  # a name followed by "-" is a longer name
        ("Read GUIDE.MD.\n", {"GUIDE.MD": "See extra.md.\n", "extra.md": "x\n"}, None),  # a document's suffix is any case
        ("Run helper.py.\n", {"helper.py": "See guide.md.\n", "guide.md": "x\n"}, "guide.md"),  # a script is not read for links
        ("Run it.\n", {"tests/helper.py": "x\n", "Tests/other.py": "x\n"}, None),  # a test folder is not counted, any case
    ],
)
def test_the_name_search_and_the_document_reads_hold_at_the_edges(tmp_path, body, texts, unnamed):
    review = _review_with_texts(tmp_path, body, texts)
    if unnamed is None:
        assert _unreferenced(review) == []
    else:
        [message] = _unreferenced(review)
        assert unnamed in message


def test_a_windows_path_with_an_escapable_name_still_names_its_file(tmp_path):
    # Read both ways: the escape reading drops the separator, so the separator reading must stay.
    review = _review_with_texts(tmp_path, "Run `scripts\\~helper.py`.\n", {"scripts/~helper.py": "x\n"})
    assert _unreferenced(review) == []


def test_a_search_that_stops_at_the_read_cap_says_so_in_a_note(tmp_path):
    # 51 named documents: the search reads 50 and cannot see the 51st, so it gives no warning, and a note says why.
    body = "".join(f"Read [d{i}](docs/d{i}.md).\n" for i in range(51))
    review = _review(tmp_path, body, [f"docs/d{i}.md" for i in range(51)] + ["scripts/unused.py"])
    assert _unreferenced(review) == []
    [note] = [f for f in review.findings if f.code == "bundle-file-unchecked"]
    assert note.level == "note" and "50 named documents" in note.message


def test_a_named_document_that_cannot_be_read_says_so_in_a_note(tmp_path):
    skill_dir = _skill(tmp_path / "skills", "kit", name="kit", description="Use when testing.",
                       body="Read [guide](docs/guide.md).\n")
    (skill_dir / "docs").mkdir()
    (skill_dir / "docs" / "other.md").write_text("x\n", encoding="utf-8")
    os.symlink("other.md", skill_dir / "docs" / "guide.md")  # a symlink: its names are not read
    (skill_dir / "scripts").mkdir()
    (skill_dir / "scripts" / "unused.py").write_text("x\n", encoding="utf-8")
    [review] = review_skills(read_skill_manifest(tmp_path))
    assert _unreferenced(review) == []
    [note] = [f for f in review.findings if f.code == "bundle-file-unchecked"]
    assert note.level == "note" and "symlink" in note.message


def test_an_unreadable_named_document_with_every_file_named_gives_no_note(tmp_path):
    # Nothing unnamed, so no count could be given: the note would say a check was skipped that had nothing to find.
    skill_dir = _skill(tmp_path / "skills", "kit", name="kit", description="Use when testing.",
                       body="Read [guide](docs/guide.md) and run `scripts/run.py`.\n")
    (skill_dir / "docs").mkdir()
    (tmp_path / "shared.md").write_text("x\n", encoding="utf-8")  # outside the bundle: the link's target
    os.symlink(tmp_path / "shared.md", skill_dir / "docs" / "guide.md")
    (skill_dir / "scripts").mkdir()
    (skill_dir / "scripts" / "run.py").write_text("x\n", encoding="utf-8")
    [review] = review_skills(read_skill_manifest(tmp_path / "skills"))
    assert _codes(review).count("bundle-file-unchecked") == 0 and "bundle-file-unreferenced" not in _codes(review)


@pytest.mark.skipif(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0), reason="needs POSIX permissions")
def test_an_unlistable_hidden_folder_with_every_file_named_gives_no_note(tmp_path):
    # The document walk reads hidden folders and the counted walk does not: an unlistable one names no counted file.
    skill_dir = _skill(tmp_path / "skills", "kit", name="kit", description="Use when testing.",
                       body="Run `scripts/run.py`.\n")
    (skill_dir / "scripts").mkdir()
    (skill_dir / "scripts" / "run.py").write_text("x\n", encoding="utf-8")
    cache = skill_dir / ".cache"
    cache.mkdir()
    cache.chmod(0)
    try:
        [review] = review_skills(read_skill_manifest(tmp_path / "skills"))
    finally:
        cache.chmod(0o755)
    assert "bundle-file-unchecked" not in _codes(review)
