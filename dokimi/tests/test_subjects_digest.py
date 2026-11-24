# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""subject.distribution_digest: the digest a .well-known/agent-skills index carries."""

from __future__ import annotations

import errno
import hashlib
import os
import sys
from pathlib import Path

import pytest

from dokimi.subjects.scorecard import _digest_arm_spec, _digest_ref, _digest_values, distribution_digest


def test_distribution_digest_matches_a_well_known_skill_md_entry(tmp_path: Path):
    # agentskills PR #254: a skill-md index entry carries sha256 of the SKILL.md bytes it serves.
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_bytes(b"---\r\nname: s\r\n---\r\nbody\r\n")
    expected = "sha256:" + hashlib.sha256(b"---\r\nname: s\r\n---\r\nbody\r\n").hexdigest()

    assert distribution_digest("skill", base=tmp_path) == expected
    assert distribution_digest("local:skill/SKILL.md", base=tmp_path) == expected

    # With resources the skill is served as an archive, whose bytes a checkout cannot predict.
    (skill / "ref.md").write_text("more", encoding="utf-8")
    assert distribution_digest("skill", base=tmp_path) is None
    assert distribution_digest("skill/SKILL.md", base=tmp_path) is None  # the same skill, by its file ref
    # A ref that is not a local path has no served bytes to hash.
    assert distribution_digest("context7:/org/lib", base=tmp_path) is None
    assert distribution_digest(None, base=tmp_path) is None
    # Only SKILL.md is served as a skill-md entry; another file has no index digest.
    (tmp_path / "notes.md").write_text("x", encoding="utf-8")
    assert distribution_digest("notes.md", base=tmp_path) is None


def test_a_resource_listed_after_skill_md_still_makes_an_archive(tmp_path: Path, monkeypatch):
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_bytes(b"body")
    (skill / "ref.md").write_text("more", encoding="utf-8")
    real_iterdir = Path.iterdir

    def skill_md_first(self: Path):
        if self == skill:  # the order a filesystem may list them in, whatever this one does
            return iter([skill / "SKILL.md", skill / "ref.md"])
        return real_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", skill_md_first)
    assert distribution_digest("skill", base=tmp_path) is None
    assert distribution_digest("skill/SKILL.md", base=tmp_path) is None


@pytest.mark.skipif(sys.platform == "win32", reason="a colon is invalid in a Windows directory name")
def test_a_ref_with_a_scheme_gets_no_digest_even_when_a_directory_collides(tmp_path: Path):
    colliding = tmp_path / "context7:" / "org" / "lib"
    colliding.mkdir(parents=True)
    (colliding / "SKILL.md").write_bytes(b"body")

    assert distribution_digest("context7:/org/lib", base=tmp_path) is None


def test_a_directory_ref_of_utf8_names_keeps_its_digest(tmp_path: Path):
    (tmp_path / "ok.md").write_bytes(b"ok\n")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "é.md").write_bytes(b"two\n")
    assert _digest_ref(str(tmp_path), base=tmp_path) == (
        "sha256:8bebaf0c5b5da4acb85facaa866ec8beabd87373df2c2f38f0fc86a655e1303f")  # gitleaks:allow


def test_a_directory_ref_digests_posix_names_in_case_sensitive_order(tmp_path: Path):
    # The same tree on Windows: "/" in the name, and B.md before a.md, as on Linux.
    # By components a/b comes before a-c, though the string "a-c" sorts before "a/b".
    (tmp_path / "a.md").write_bytes(b"a")
    (tmp_path / "B.md").write_bytes(b"b")
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "b").write_bytes(b"ab")
    (tmp_path / "a-c").write_bytes(b"ac")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "x.md").write_bytes(b"x")
    h = hashlib.sha256(b"d")
    for name, content in (("B.md", b"b"), ("a/b", b"ab"), ("a-c", b"ac"), ("a.md", b"a"), ("sub/x.md", b"x")):
        h.update(hashlib.sha256(name.encode()).digest() + hashlib.sha256(content).digest())
    assert _digest_ref(str(tmp_path), base=tmp_path) == "sha256:" + h.hexdigest()


@pytest.mark.skipif(sys.platform == "win32", reason="a name that is not UTF-8 cannot be created on Windows")
def test_a_directory_ref_hashes_the_bytes_of_a_file_name_that_is_not_utf8(tmp_path: Path):
    (tmp_path / "ok.md").write_bytes(b"ok\n")
    before = _digest_ref(str(tmp_path), base=tmp_path)
    try:
        with open(os.fsencode(tmp_path) + b"/n\xff.md", "wb") as handle:
            handle.write(b"x")
    except OSError:
        pytest.skip("the file system refuses a name that is not UTF-8")
    after = _digest_ref(str(tmp_path), base=tmp_path)
    assert after != before and after is not None and after.startswith("sha256:")
    os.rename(os.fsencode(tmp_path) + b"/n\xff.md", os.fsencode(tmp_path) + b"/n\xfe.md")
    assert _digest_ref(str(tmp_path), base=tmp_path) != after


def test_a_ref_that_is_no_path_keeps_its_digest(tmp_path: Path):
    assert _digest_ref("no-such-é", base=tmp_path) == (
        "sha256:3124a1d714c608077eca68f5b382e3db438cc8826e0523d3e82768e517f08f9b")  # gitleaks:allow


@pytest.mark.skipif(sys.platform == "win32", reason="a surrogate-escaped name is a POSIX file-name form")
def test_a_ref_that_is_no_path_and_holds_a_lone_surrogate_is_digested(tmp_path: Path):
    digest = _digest_ref("n\udcff", base=tmp_path)
    assert digest is not None and digest.startswith("sha256:")
    assert digest != _digest_ref("n\udcfe", base=tmp_path)


@pytest.mark.skipif(sys.platform == "win32", reason="a surrogate-escaped name is a POSIX file-name form")
def test_a_question_value_with_a_lone_surrogate_is_digested():
    digest = _digest_values(("q\udcff",))
    assert digest.startswith("sha256:") and digest != _digest_values(("q\udcfe",))


@pytest.mark.skipif(sys.platform == "win32", reason="a surrogate-escaped name is a POSIX file-name form")
def test_an_arm_spec_with_a_lone_surrogate_is_digested(tmp_path: Path):
    digest = _digest_arm_spec("skill:./n\udcff", base=tmp_path)
    assert digest.startswith("sha256:") and digest != _digest_arm_spec("skill:./n\udcfe", base=tmp_path)


@pytest.mark.skipif(sys.platform == "win32", reason="a surrogate-escaped name is a POSIX file-name form")
def test_each_part_of_a_combined_spec_with_a_lone_surrogate_is_digested(tmp_path: Path):
    from dokimi.treatments.specs import content_refs

    spec = "skill:./a+skill:./n\udcff"
    assert [part for part, _ in content_refs(spec)] == ["skill:./a", "skill:./n\udcff"]
    digest = _digest_arm_spec(spec, base=tmp_path)
    assert digest.startswith("sha256:") and digest != _digest_arm_spec("skill:./a+skill:./n\udcfe", base=tmp_path)


def _tree(root: Path, files: dict[str, bytes]) -> str:
    root.mkdir()
    for name, content in files.items():
        (root / name).write_bytes(content)
    digest = _digest_ref(str(root), base=root)
    assert digest is not None
    return digest


def test_a_nul_in_a_file_cannot_move_bytes_into_the_next_file_name(tmp_path: Path):
    # #878: a = "b" and c = "" hashed the same bytes as a = "b\0c\0" alone.
    two = _tree(tmp_path / "s1", {"a": b"b", "c": b""})
    one = _tree(tmp_path / "s2", {"a": b"b\0c\0"})
    assert two != one


def test_a_nul_in_a_file_cannot_move_bytes_into_the_next_value(tmp_path: Path):
    # #878: ("f", "c") with f = "b" hashed the same bytes as ("f",) with f = "bc\0".
    f = tmp_path / "f"
    f.write_bytes(b"b")
    pair = _digest_values((str(f), "c"))
    f.write_bytes(b"bc\0")
    assert _digest_values((str(f),)) != pair


def test_a_file_value_whose_content_spells_the_next_value_differs_from_the_two_values(tmp_path: Path):
    # #892 review: the file's content digest was the same bytes as the digest of a following value spelled as that content.
    f = tmp_path / "f"
    f.write_bytes(b"missing-question")
    single = _digest_values((str(f),))
    f.unlink()
    assert _digest_values((str(f), "missing-question")) != single


def test_a_file_ref_digest_is_the_sha256_of_its_content(tmp_path: Path):
    # The schema's ref_digest text and evidence.py's content_digest say a file's digest is its content digest.
    (tmp_path / "SKILL.md").write_bytes(b"body\n")
    assert _digest_ref("SKILL.md", base=tmp_path) == "sha256:" + hashlib.sha256(b"body\n").hexdigest()


def test_a_value_naming_no_file_is_tagged_as_text(tmp_path: Path):
    assert _digest_values((str(tmp_path / "nope"),)) == "sha256:" + hashlib.sha256(
        hashlib.sha256(str(tmp_path / "nope").encode()).digest() + b"t").hexdigest()


def test_a_value_naming_a_file_is_tagged_with_the_digest_of_its_content(tmp_path: Path):
    f = tmp_path / "f"
    f.write_bytes(b"body\n")
    assert _digest_values((str(f),)) == "sha256:" + hashlib.sha256(
        hashlib.sha256(str(f).encode()).digest() + b"f" + hashlib.sha256(b"body\n").digest()).hexdigest()


def test_an_arm_spec_digest_frames_the_spec_and_each_part_as_fixed_width_fields(tmp_path: Path):
    # #878: the spec and each part were joined with a bare NUL, so this literal moved.
    assert _digest_arm_spec("skill:./a+skill:./b", base=tmp_path) == (
        "sha256:379da7938eee39f099be7c7b413df44ddbce217b3d4918f8299cf7cf7090a6bb")  # gitleaks:allow
    assert _digest_arm_spec("baseline", base=tmp_path) == (
        "sha256:f22917cace49ee841b942ac46ec9a946203ba2f89f8c816143557337cbcdf72c")  # gitleaks:allow


def test_an_arm_spec_part_naming_a_file_differs_from_one_naming_no_file_spelled_as_its_content(tmp_path: Path):
    f = tmp_path / "x"
    f.write_bytes(b"t" + hashlib.sha256(b"./x").digest())
    present = _digest_arm_spec("skill:./x", base=tmp_path)
    f.unlink()
    assert _digest_arm_spec("skill:./x", base=tmp_path) != present


def test_an_arm_spec_part_naming_a_file_holding_d_differs_from_one_naming_an_empty_directory(tmp_path: Path):
    (tmp_path / "x").write_bytes(b"d")
    as_file = _digest_arm_spec("skill:./x", base=tmp_path)
    (tmp_path / "x").unlink()
    (tmp_path / "x").mkdir()
    assert _digest_arm_spec("skill:./x", base=tmp_path) != as_file


def test_a_file_ref_differs_from_a_missing_ref_spelled_as_its_content(tmp_path: Path):
    (tmp_path / "doc.md").write_bytes(b"missing.md")
    assert _digest_ref("doc.md", base=tmp_path) != _digest_ref("missing.md", base=tmp_path)


def test_a_value_naming_an_empty_file_differs_from_a_value_naming_no_file(tmp_path: Path):
    f = tmp_path / "f"
    f.write_bytes(b"")
    empty = _digest_values((str(f),))
    f.unlink()
    assert _digest_values((str(f),)) != empty


def test_an_empty_file_ref_differs_from_a_missing_ref_spelled_as_the_same_path(tmp_path: Path):
    (tmp_path / "present").mkdir()
    (tmp_path / "present" / "e.md").write_bytes(b"")
    (tmp_path / "absent").mkdir()
    assert _digest_ref("e.md", base=tmp_path / "present") != _digest_ref("e.md", base=tmp_path / "absent")


@pytest.mark.parametrize("digest", [
    lambda f, base: _digest_ref(str(f), base=base),
    lambda f, base: _digest_values((str(f),)),
], ids=["ref", "values"])
def test_rewriting_a_file_changes_its_digest(digest, tmp_path: Path):
    f = tmp_path / "f"
    f.write_bytes(b"a")
    before = digest(f, tmp_path)
    f.write_bytes(b"b")
    assert digest(f, tmp_path) != before


@pytest.mark.parametrize("digest", [
    lambda text, base: _digest_ref(text, base=base),
    lambda text, base: _digest_values((text,)),
    lambda text, base: _digest_arm_spec(f"skill:./{text}", base=base),
], ids=["ref", "values", "arm_spec"])
def test_a_high_surrogate_is_digested_and_told_apart(digest, tmp_path: Path):
    # #795: a JSON "\ud800" escape has no surrogateescape byte form, so os.fsencode raised UnicodeEncodeError.
    first = digest("n\ud800", tmp_path)
    assert first.startswith("sha256:") and first == digest("n\ud800", tmp_path)
    assert first != digest("n\ud801", tmp_path)
    assert digest("n\udcff", tmp_path).startswith("sha256:")


def test_the_scorecard_carries_the_distribution_digest(tmp_path: Path, monkeypatch):
    from dokimi.artifacts import validate_artifact
    from dokimi.subjects import build_subject_scorecard, load_subject

    (tmp_path / "skill").mkdir()
    (tmp_path / "skill" / "SKILL.md").write_bytes(b"body")
    (tmp_path / "subject.toml").write_text(
        '[subject]\nid = "s"\nkind = "skill"\nref = "skill"\n\n'
        '[suite]\nproducts = ["p"]\nquestions = ["q.json"]\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    scorecard = build_subject_scorecard(load_subject(tmp_path / "subject.toml"), awareness_runs=[], out_dir=tmp_path)

    assert scorecard["subject"]["distribution_digest"] == "sha256:" + hashlib.sha256(b"body").hexdigest()
    validate_artifact("subject_scorecard", scorecard)


def test_the_scorecard_carries_a_distribution_digest_per_member(tmp_path: Path, monkeypatch):
    from dokimi.subjects import build_subject_scorecard, load_subject

    (tmp_path / "standalone").mkdir()
    (tmp_path / "standalone" / "SKILL.md").write_bytes(b"member body")
    (tmp_path / "bundled").mkdir()
    (tmp_path / "bundled" / "SKILL.md").write_bytes(b"bundled body")
    (tmp_path / "bundled" / "ref.md").write_text("resource", encoding="utf-8")
    (tmp_path / "subject.toml").write_text(
        '[subject]\nid = "b"\nkind = "bundle"\n\n'
        '[[subject.members]]\nkind = "skill"\nref = "standalone"\n\n'
        '[[subject.members]]\nkind = "skill"\nref = "bundled/SKILL.md"\n\n'
        '[[subject.members]]\nkind = "skill"\nref = "context7:/org/lib"\n\n'
        '[suite]\nproducts = ["p"]\nquestions = ["q.json"]\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    scorecard = build_subject_scorecard(load_subject(tmp_path / "subject.toml"), awareness_runs=[], out_dir=tmp_path)

    # The standalone member has a digest; the bundled one has resources, so neither its file nor its directory does.
    digests = [member["distribution_digest"] for member in scorecard["subject"]["members"]]
    assert digests == ["sha256:" + hashlib.sha256(b"member body").hexdigest(), None, None]


_LONG = "x" * 5000  # past NAME_MAX and PATH_MAX, so the OS refuses the probe with ENAMETOOLONG


def test_a_question_value_longer_than_the_name_limit_digests_as_text():
    # #911: _digest_values(("x" * 300,)) raised OSError: [Errno 36] File name too long from Path.is_file().
    digest = _digest_values((_LONG,))
    assert digest == "sha256:" + hashlib.sha256(_field_of(_LONG) + b"t").hexdigest()
    assert digest == _digest_values((_LONG,)) != _digest_values((_LONG + "y",))


def test_a_ref_longer_than_the_name_limit_digests_as_naming_nothing(tmp_path: Path):
    # #911: _digest_ref("x" * 300, base=Path("/tmp")) raised OSError: [Errno 36] File name too long.
    digest = _digest_ref(_LONG, base=tmp_path)
    assert digest == "sha256:" + hashlib.sha256(b"t" + _field_of(_LONG)).hexdigest()
    assert digest != _digest_ref(_LONG + "y", base=tmp_path)
    assert _digest_ref("local:" + _LONG, base=tmp_path) == "sha256:" + hashlib.sha256(
        b"t" + _field_of("local:" + _LONG)).hexdigest()


def test_an_arm_spec_with_a_ref_longer_than_the_name_limit_is_digested(tmp_path: Path):
    # #911: the arm spec digest probes each part's ref the same way.
    digest = _digest_arm_spec(f"skill:./{_LONG}", base=tmp_path)
    assert digest.startswith("sha256:") and digest == _digest_arm_spec(f"skill:./{_LONG}", base=tmp_path)
    assert digest != _digest_arm_spec(f"skill:./{_LONG}y", base=tmp_path)


def test_a_distribution_digest_of_a_ref_longer_than_the_name_limit_is_none(tmp_path: Path):
    # #911: distribution_digest probed the ref with is_dir() and raised OSError.
    assert distribution_digest(_LONG, base=tmp_path) is None
    assert distribution_digest("local:" + _LONG, base=tmp_path) is None


def test_a_long_file_name_inside_a_directory_ref_is_still_hashed(tmp_path: Path):
    # #911: only the probe of a user text is guarded; the files of a directory are read as before.
    name = "y" * 200
    (tmp_path / name).write_bytes(b"a")
    before = _digest_ref(str(tmp_path), base=tmp_path)
    (tmp_path / name).write_bytes(b"b")
    assert _digest_ref(str(tmp_path), base=tmp_path) != before


def _refuse(monkeypatch, exc: Exception) -> None:
    # Fail the stat itself: Python 3.14's is_file()/is_dir() swallow every OSError, so a stub of them would hide that.
    def stat(self: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        raise exc

    monkeypatch.setattr(Path, "stat", stat)


def test_a_permission_error_from_the_stat_propagates_instead_of_digesting_as_text(tmp_path: Path, monkeypatch):
    # An existing file under an unsearchable directory must not get a text-only digest that ignores its content.
    _refuse(monkeypatch, PermissionError(13, "Permission denied"))
    with pytest.raises(PermissionError):
        _digest_values(("private/SKILL.md",))
    with pytest.raises(PermissionError):
        _digest_ref("private/SKILL.md", base=tmp_path)
    with pytest.raises(PermissionError):
        distribution_digest("private/SKILL.md", base=tmp_path)


@pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() == 0, reason="root reads through a mode 000 directory")
def test_a_file_under_an_unsearchable_directory_is_an_error_not_a_text_digest(tmp_path: Path):
    private = tmp_path / "private"
    private.mkdir()
    (private / "SKILL.md").write_text("body")
    private.chmod(0)
    try:
        with pytest.raises(PermissionError):
            _digest_ref("private/SKILL.md", base=tmp_path)
        with pytest.raises(PermissionError):
            _digest_values((str(private / "SKILL.md"),))
    finally:
        private.chmod(0o700)


def test_an_io_error_from_the_stat_propagates(tmp_path: Path, monkeypatch):
    _refuse(monkeypatch, OSError(5, "Input/output error"))
    with pytest.raises(OSError, match="Input/output error"):
        _digest_ref("a/SKILL.md", base=tmp_path)


@pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() == 0, reason="root reads through a mode 444 directory")
def test_a_directory_that_can_be_listed_but_not_searched_is_an_error_not_an_empty_tree(tmp_path: Path, monkeypatch):
    # Python 3.14's is_file() is False for every OSError; make 3.11-3.13 do the same so the test pins the digest code, not the version.
    real_is_file = Path.is_file

    def is_file(self: Path, **kwargs) -> bool:
        try:
            return real_is_file(self, **kwargs)
        except OSError:
            return False

    monkeypatch.setattr(Path, "is_file", is_file)
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("body")
    skill_dir.chmod(0o444)
    try:
        with pytest.raises(PermissionError):
            _digest_ref("skill", base=tmp_path)
        with pytest.raises(PermissionError):
            distribution_digest("skill", base=tmp_path)
    finally:
        skill_dir.chmod(0o700)


def _tree_with_unlistable(tmp_path: Path, *parts: str) -> tuple[Path, Path]:
    """A directory ``tree`` of ``a.txt`` and ``sub/b.txt`` (``sub/deeper/c.txt`` too when *parts* names it); returns it and the dir to chmod."""
    tree = tmp_path / "tree"
    (tree / "sub" / "deeper").mkdir(parents=True)
    (tree / "a.txt").write_text("a")
    (tree / "sub" / "b.txt").write_text("b")
    (tree / "sub" / "deeper" / "c.txt").write_text("c")
    return tree, tree.joinpath(*parts)


@pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() == 0, reason="root lists a mode 111 directory")
@pytest.mark.parametrize("parts", [(), ("sub",), ("sub", "deeper")])  # the directory ref itself, a subdirectory, one under a listable subdirectory
def test_a_subdirectory_that_cannot_be_listed_is_an_error_not_a_smaller_tree(tmp_path: Path, parts: tuple[str, ...]):
    tree, unlistable = _tree_with_unlistable(tmp_path, *parts)
    complete = _digest_ref("tree", base=tmp_path)
    unlistable.chmod(0o111)
    try:
        with pytest.raises(PermissionError, match=unlistable.name):
            _digest_ref("tree", base=tmp_path)
        with pytest.raises(PermissionError, match=unlistable.name):
            _digest_arm_spec("skill:./tree", base=tmp_path)
    finally:
        unlistable.chmod(0o700)
    assert _digest_ref("tree", base=tmp_path) == complete


def test_a_symlink_to_a_directory_adds_nothing_to_a_directory_digest(tmp_path: Path):
    tree, _ = _tree_with_unlistable(tmp_path, "sub")
    before = _digest_ref("tree", base=tmp_path)
    try:
        (tree / "link").symlink_to(tree / "sub", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available")
    assert _digest_ref("tree", base=tmp_path) == before


@pytest.mark.parametrize("error", [errno.ELOOP, errno.ENOTDIR])  # a symlink loop, a parent that is a file
def test_a_loop_or_a_file_parent_reads_as_naming_nothing(tmp_path: Path, monkeypatch, error):
    _refuse(monkeypatch, OSError(error, os.strerror(error)))
    assert _digest_ref("a/SKILL.md", base=tmp_path) == "sha256:" + hashlib.sha256(b"t" + _field_of("a/SKILL.md")).hexdigest()
    assert _digest_values(("a/SKILL.md",)) == "sha256:" + hashlib.sha256(_field_of("a/SKILL.md") + b"t").hexdigest()
    assert distribution_digest("a/SKILL.md", base=tmp_path) is None


def _winerror(code: int) -> OSError:
    exc = OSError("The name cannot be resolved")
    exc.winerror = code  # type: ignore[attr-defined]  # set by the OS on Windows only
    return exc


@pytest.mark.parametrize("winerror", [123, 206, 1921])  # invalid name, name too long, cannot resolve
def test_a_windows_error_for_a_name_that_cannot_exist_reads_as_naming_nothing(tmp_path: Path, monkeypatch, winerror):
    _refuse(monkeypatch, _winerror(winerror))
    assert _digest_values(("a/SKILL.md",)) == "sha256:" + hashlib.sha256(_field_of("a/SKILL.md") + b"t").hexdigest()
    assert distribution_digest("a/SKILL.md", base=tmp_path) is None


def test_a_device_that_is_not_ready_is_an_error_not_a_name_that_cannot_exist(tmp_path: Path, monkeypatch):
    _refuse(monkeypatch, _winerror(21))  # ERROR_NOT_READY
    with pytest.raises(OSError, match="cannot be resolved"):
        _digest_values(("a/SKILL.md",))
    with pytest.raises(OSError, match="cannot be resolved"):
        _digest_ref("a/SKILL.md", base=tmp_path)
    with pytest.raises(OSError, match="cannot be resolved"):
        distribution_digest("a/SKILL.md", base=tmp_path)


def test_a_value_with_a_nul_names_no_file(tmp_path: Path):
    assert _digest_values(("a\0b",)) == "sha256:" + hashlib.sha256(_field_of("a\0b") + b"t").hexdigest()
    assert distribution_digest("a\0b", base=tmp_path) is None


def _field_of(text: str) -> bytes:
    return hashlib.sha256(text.encode()).digest()
