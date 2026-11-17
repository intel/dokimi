# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""subject.distribution_digest: the digest a .well-known/agent-skills index carries."""

from __future__ import annotations

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


@pytest.mark.skipif(sys.platform == "win32", reason="the pinned digest hashes the POSIX separator of sub/é.md")
def test_a_directory_ref_of_utf8_names_keeps_its_digest(tmp_path: Path):
    (tmp_path / "ok.md").write_bytes(b"ok\n")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "é.md").write_bytes(b"two\n")
    assert _digest_ref(str(tmp_path), base=tmp_path) == (
        "sha256:8bebaf0c5b5da4acb85facaa866ec8beabd87373df2c2f38f0fc86a655e1303f")  # gitleaks:allow


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


def test_an_arm_spec_digest_frames_the_spec_and_each_part_as_fixed_width_fields(tmp_path: Path):
    # #878: the spec and each part were joined with a bare NUL, so this literal moved.
    assert _digest_arm_spec("skill:./a+skill:./b", base=tmp_path) == (
        "sha256:8c142870b5eab2dd85f0380146b5b58e1de2576a17f49bc6312bbfce6b00d7cc")  # gitleaks:allow
    assert _digest_arm_spec("baseline", base=tmp_path) == (
        "sha256:f22917cace49ee841b942ac46ec9a946203ba2f89f8c816143557337cbcdf72c")  # gitleaks:allow


def test_a_file_ref_differs_from_a_missing_ref_spelled_as_its_content(tmp_path: Path):
    (tmp_path / "doc.md").write_bytes(b"missing.md")
    assert _digest_ref("doc.md", base=tmp_path) != _digest_ref("missing.md", base=tmp_path)


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
