# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The routing field: which catalog skills compete, and how disabled and copied skills are kept out."""

from __future__ import annotations

import json

import pytest

from dokimi.cli import build_parser
from dokimi.commands.novelty import _routing_field
from dokimi.novelty.routing import (
    catalog_competitors,
    competitor_left_out,
    disables_model_invocation,
)


def _parse(*argv):
    return build_parser().parse_args(list(argv))


def _skill(tmp_path, name, description, body="# B\n\n## A section here\n"):
    root = tmp_path / name
    root.mkdir()
    (root / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}", encoding="utf-8")
    return f"skill:{root}"


def _catalog_skill(root, name, description):
    skill_dir = root / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# B\n", encoding="utf-8")


def test_the_catalog_is_the_skills_yaml_manifest_when_there_is_one(tmp_path):
    # A manifest decides which skills the catalog has: a SKILL.md it does not list is not a
    # sibling, and a listed skill with no directory is reported rather than aborting the run.
    root = tmp_path / "catalog"
    _catalog_skill(root, "xpu", "profile a slow GPU kernel")
    _catalog_skill(root, "db", "tune a slow database query")
    _catalog_skill(root, "unlisted", "profile a slow thing")
    (root / "skills.yaml").write_text("- name: xpu\n- name: db\n- name: ghost\n",
                                      encoding="utf-8")
    notes: list[str] = []
    names = [c.name for c in catalog_competitors(str(root), {"perf"}, notes)]
    assert names == ["xpu", "db"]
    assert len(notes) == 1 and "ghost" in notes[0]


def test_a_non_skill_file_among_competitors_is_skipped_with_a_warning(tmp_path, capsys):
    # A shell glob over a skills directory passes the helper scripts and an empty directory
    # too; that used to abort the whole run on the first .mjs file.
    helper = tmp_path / "activation-rule.mjs"
    helper.write_text("export {}\n", encoding="utf-8")
    empty = tmp_path / "generated"
    empty.mkdir()
    queries = tmp_path / "q.json"
    queries.write_text(json.dumps(["profile a binary"]), encoding="utf-8")
    out = tmp_path / "routing.json"
    args = _parse("novelty", "routing",
                  "--artifact", _skill(tmp_path, "a", "profile a binary with perf"),
                  "--competitors", f"skill:{helper}", f"skill:{empty}",
                  _skill(tmp_path, "b", "tune a database"),
                  "--queries", str(queries), "--out", str(out))
    args.func(args)
    assert json.loads(out.read_text(encoding="utf-8"))["n_candidates"] == 2
    err = capsys.readouterr().err
    assert "skipped competitor" in err and "activation-rule.mjs is not a SKILL.md" in err
    assert "the directory has no SKILL.md" in err


def test_a_non_skill_artifact_is_still_refused(tmp_path):
    helper = tmp_path / "activation-rule.mjs"
    helper.write_text("export {}\n", encoding="utf-8")
    args = _parse("novelty", "routing", "--artifact", f"skill:{helper}",
                  "--competitors", _skill(tmp_path, "b", "tune a database"),
                  "--queries", "x")
    with pytest.raises(SystemExit, match="cannot load an artifact"):
        args.func(args)


def _model_hidden_skill(root, name, description):
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n"
        "disable-model-invocation: true\n---\n\n# B\n", encoding="utf-8")
    return f"skill:{skill_dir}"


def test_a_disable_model_invocation_skill_is_not_a_competitor_unless_asked(tmp_path, capsys):
    # ultracave and megacave set the flag: no harness lists them, so a card for them scores a
    # choice the model cannot make.
    catalog = tmp_path / "skills"
    catalog.mkdir()
    gold = _skill(catalog, "perf", "profile a slow binary with perf")
    _skill(catalog, "db", "tune a slow database query")
    _model_hidden_skill(catalog, "ultra", "compress output hard")
    queries = tmp_path / "q.json"
    queries.write_text(json.dumps(["my binary is slow"]), encoding="utf-8")
    out = tmp_path / "routing.json"
    args = _parse("novelty", "routing", "--artifact", gold, "--catalog", str(catalog),
                  "--queries", str(queries), "--out", str(out))
    args.func(args)
    assert json.loads(out.read_text(encoding="utf-8"))["n_candidates"] == 2
    assert "left out catalog skill 'ultra'" in capsys.readouterr().err

    kept = tmp_path / "kept.json"
    args = _parse("novelty", "routing", "--artifact", gold, "--catalog", str(catalog),
                  "--queries", str(queries), "--out", str(kept), "--include-disabled")
    args.func(args)
    assert json.loads(kept.read_text(encoding="utf-8"))["n_candidates"] == 3


def test_an_explicit_disabled_competitor_is_skipped_with_a_warning(tmp_path, capsys):
    queries = tmp_path / "q.json"
    queries.write_text(json.dumps(["profile a binary"]), encoding="utf-8")
    hidden = _model_hidden_skill(tmp_path, "ultra", "compress output hard")
    args = _parse("novelty", "routing", "--artifact", _skill(tmp_path, "a", "profile a binary"),
                  "--competitors", hidden, _skill(tmp_path, "b", "tune a database"),
                  "--queries", str(queries), "--out", str(tmp_path / "r.json"))
    args.func(args)
    assert json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))["n_candidates"] == 2
    assert f"skipped competitor {hidden}: disable-model-invocation" in capsys.readouterr().err


def test_include_disabled_keeps_a_disabled_skill_named_as_a_competitor(tmp_path, capsys):
    queries = tmp_path / "q.json"
    queries.write_text(json.dumps(["profile a binary"]), encoding="utf-8")
    hidden = _model_hidden_skill(tmp_path, "ultra", "compress output hard")
    out = tmp_path / "r.json"
    args = _parse("novelty", "routing", "--artifact", _skill(tmp_path, "a", "profile a binary"),
                  "--competitors", hidden, _skill(tmp_path, "b", "tune a database"),
                  "--queries", str(queries), "--out", str(out), "--include-disabled")
    args.func(args)
    assert json.loads(out.read_text(encoding="utf-8"))["n_candidates"] == 3
    assert "skipped competitor" not in capsys.readouterr().err


def test_a_disabled_artifact_is_still_routed_but_warned_about(tmp_path, capsys):
    queries = tmp_path / "q.json"
    queries.write_text(json.dumps(["profile a binary"]), encoding="utf-8")
    artifact = _model_hidden_skill(tmp_path, "ultra", "compress output hard")
    args = _parse("novelty", "routing", "--artifact", artifact,
                  "--competitors", _skill(tmp_path, "b", "tune a database"),
                  "--queries", str(queries), "--out", str(tmp_path / "r.json"))
    args.func(args)
    assert "the model cannot select it" in capsys.readouterr().err


@pytest.mark.parametrize("line, hidden", [
    ("disable-model-invocation: true", True), ("disable-model-invocation: false", False),
    ("disable-model-invocation: yes", True), ("description: x", False),
    # Unquoted yes/on parse to True; the quoted and numeric spellings reach the string branch.
    ("disable-model-invocation: 'yes'", True), ("disable-model-invocation: 'on'", True),
    ("disable-model-invocation: 1", True), ("disable-model-invocation: 0", False),
    ("disable-model-invocation: 'TRUE'", True), ("disable-model-invocation: 'no'", False),
])
def test_the_disable_flag_is_read_the_way_the_repo_budget_reads_it(tmp_path, line, hidden):
    path = tmp_path / "SKILL.md"
    path.write_text(f"---\nname: x\ndescription: x\n{line}\n---\n\nbody\n", encoding="utf-8")
    assert disables_model_invocation(path) is hidden


def test_a_mirror_of_a_catalog_skill_is_counted_once(tmp_path, capsys):
    # A plugin copy of the skills/ tree: byte-identical SKILL.md, same name. It used to make
    # the field name db twice and refuse the run.
    root = tmp_path / "catalog"
    _catalog_skill(root, "perf", "profile a slow binary with perf")
    _catalog_skill(root, "db", "tune a slow database query")
    mirror = root / "plugins" / "x" / "skills" / "db"
    mirror.mkdir(parents=True)
    (mirror / "SKILL.md").write_bytes((root / "skills" / "db" / "SKILL.md").read_bytes())
    queries = tmp_path / "q.json"
    queries.write_text(json.dumps(["my binary is slow"]), encoding="utf-8")
    out = tmp_path / "routing.json"
    args = _parse("novelty", "routing", "--artifact", f"skill:{root / 'skills' / 'perf'}",
                  "--catalog", str(root), "--queries", str(queries), "--out", str(out))
    args.func(args)
    assert json.loads(out.read_text(encoding="utf-8"))["n_candidates"] == 2
    err = capsys.readouterr().err
    assert "identical to" in err and "skills" in err


def test_the_mirror_kept_is_the_copy_nearest_the_catalog_root(tmp_path):
    root = tmp_path / "catalog"
    _catalog_skill(root, "db", "tune a slow database query")
    deep = root / "plugins" / "x" / "skills" / "db"
    deep.mkdir(parents=True)
    (deep / "SKILL.md").write_bytes((root / "skills" / "db" / "SKILL.md").read_bytes())
    notes: list[str] = []
    assert [c.name for c in catalog_competitors(str(root), set(), notes)] == ["db"]
    assert "plugins" in notes[0] and "identical to" in notes[0]
    # The note names the copy that was kept, not only the one left out.
    assert notes[0].endswith(f"identical to {root / 'skills' / 'db' / 'SKILL.md'}")


def test_mirrors_at_one_depth_keep_the_path_that_sorts_first(tmp_path):
    root = tmp_path / "catalog"
    for branch in ("b", "a"):
        copy = root / branch / "skills" / "db"
        copy.mkdir(parents=True)
        (copy / "SKILL.md").write_text(
            "---\nname: db\ndescription: tune a slow database query\n---\n\n# B\n",
            encoding="utf-8")
    notes: list[str] = []
    assert [c.name for c in catalog_competitors(str(root), set(), notes)] == ["db"]
    assert notes[0].endswith(f"identical to {root / 'a' / 'skills' / 'db' / 'SKILL.md'}")
    assert f"catalog skill 'db' at {root / 'b' / 'skills' / 'db' / 'SKILL.md'}" in notes[0]


def test_two_catalog_skills_with_one_name_and_different_content_are_skipped_with_a_warning(
    tmp_path, capsys
):
    # The catalog holds two different cards under one name. Routing does not abort the repo:
    # both copies of that name leave the field, the rest of it stays, and stderr names both paths.
    root = tmp_path / "catalog"
    _catalog_skill(root, "perf", "profile a slow binary with perf")
    _catalog_skill(root, "cache", "warm a cold cache before a benchmark")
    _catalog_skill(root, "db", "tune a slow database query")
    fork = root / "plugins" / "x" / "skills" / "db"
    fork.mkdir(parents=True)
    (fork / "SKILL.md").write_text(
        "---\nname: db\ndescription: a different card\n---\n\n# B\n", encoding="utf-8"
    )
    notes: list[str] = []
    names = [c.name for c in catalog_competitors(str(root), {"perf"}, notes)]
    assert names == ["cache"]
    assert len(notes) == 1 and "db" in notes[0] and "different content" in notes[0]
    assert str(root / "skills" / "db" / "SKILL.md") in notes[0]
    assert str(fork / "SKILL.md") in notes[0]

    queries = tmp_path / "q.json"
    queries.write_text(json.dumps(["my binary is slow"]), encoding="utf-8")
    args = _parse(
        "novelty",
        "routing",
        "--artifact",
        f"skill:{root / 'skills' / 'perf'}",
        "--catalog",
        str(root),
        "--queries",
        str(queries),
        "--out",
        str(tmp_path / "routing.json"),
    )
    left_out: list[str] = []
    field = _routing_field(args, {"perf"}, left_out)
    assert [c.name for c in field] == ["cache"]
    assert "WARNING: " + notes[0] in capsys.readouterr().err
    assert left_out == notes


def test_a_spec_error_copy_is_no_conflict_with_a_valid_copy_of_the_name(tmp_path):
    # the spec-error copy is left out by default, so the valid one stays and nothing conflicts
    root = tmp_path / "catalog"
    _catalog_skill(root, "perf", "profile a slow binary with perf")
    _catalog_skill(root, "db", "x" * 1100)
    fork = root / "plugins" / "x" / "skills" / "db"
    fork.mkdir(parents=True)
    (fork / "SKILL.md").write_text(
        "---\nname: db\ndescription: a valid card\n---\n\n# B\n", encoding="utf-8"
    )
    notes: list[str] = []
    names = [c.name for c in catalog_competitors(str(root), {"perf"}, notes)]
    assert names == ["db"]
    assert not [note for note in notes if "different content" in note]
    assert str(root / "skills" / "db" / "SKILL.md") in " ".join(notes)  # left out for its spec error


def test_include_invalid_makes_the_spec_error_copy_a_conflict_again(tmp_path):
    root = tmp_path / "catalog"
    _catalog_skill(root, "perf", "profile a slow binary with perf")
    _catalog_skill(root, "db", "x" * 1100)
    fork = root / "plugins" / "x" / "skills" / "db"
    fork.mkdir(parents=True)
    (fork / "SKILL.md").write_text(
        "---\nname: db\ndescription: a valid card\n---\n\n# B\n", encoding="utf-8"
    )
    notes: list[str] = []
    names = [c.name for c in catalog_competitors(str(root), {"perf"}, notes, include_invalid=True)]
    assert "db" not in names
    conflict = [note for note in notes if "different content" in note]
    assert len(conflict) == 1
    assert str(root / "skills" / "db" / "SKILL.md") in conflict[0]
    assert str(fork / "SKILL.md") in conflict[0]


def test_an_explicit_spec_error_competitor_is_skipped_with_a_warning(tmp_path, capsys):
    # a skill: competitor gets the same rule as a catalog skill: left out with a warning, not kept silently
    long_ref = _skill(tmp_path, "long", "x" * 1100)
    queries = tmp_path / "q.json"
    queries.write_text(json.dumps(["profile a binary"]), encoding="utf-8")
    argv = ["novelty", "routing", "--artifact", _skill(tmp_path, "a", "profile a binary"),
            "--competitors", long_ref, _skill(tmp_path, "b", "tune a database"),
            "--queries", str(queries), "--out", str(tmp_path / "r.json")]
    left_out: list[str] = []
    field = _routing_field(_parse(*argv), {"a"}, left_out)
    assert [c.name for c in field] == ["b"]
    assert f"skipped competitor {long_ref}: spec error" in capsys.readouterr().err
    assert any(long_ref in entry and "spec error" in entry for entry in left_out)
    field = _routing_field(_parse(*argv, "--include-invalid"), {"a"}, [])
    assert sorted(c.name for c in field) == ["b", "long"]


def test_the_spelling_of_an_explicit_competitor_does_not_change_its_verdict(tmp_path, monkeypatch):
    # ``skill:SKILL.md`` and ``skill:.`` (relative, from inside the skill) must get the verdict of
    # the absolute reference. A SKILL.md with no ``skills/`` parent is a root skill, as the catalog
    # reads it, so the directory name ("checkout") does not decide whether the field keeps ``db``.
    skill_dir = tmp_path / "checkout"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: db\ndescription: tune a database query\n---\n\n# B\n\n## A section\n",
        encoding="utf-8")
    absolute = competitor_left_out(f"skill:{skill_dir}/SKILL.md",
                                   include_disabled=False, include_invalid=False)
    assert absolute is None
    monkeypatch.chdir(skill_dir)
    for ref in ("skill:SKILL.md", "skill:.", f"skill:{skill_dir}"):
        assert competitor_left_out(ref, include_disabled=False, include_invalid=False) == absolute, ref
    assert competitor_left_out("skill:SKILL.md", include_disabled=False, include_invalid=True) is None


@pytest.mark.parametrize("skill_dir", ["skills/checkout", "plugins/x/skills/checkout"])
def test_a_skill_under_a_skills_directory_keeps_the_directory_name_check(tmp_path, skill_dir):
    # the catalog's directory-name check applies to a skill in a skills/ tree (only a root skill is exempt)
    path = tmp_path / skill_dir
    path.mkdir(parents=True)
    (path / "SKILL.md").write_text(
        "---\nname: db\ndescription: tune a database query\n---\n\n# B\n\n## A section\n",
        encoding="utf-8")
    for ref in (f"skill:{path}/SKILL.md", f"skill:{path}"):
        verdict = competitor_left_out(ref, include_disabled=False, include_invalid=False)
        assert verdict is not None and "not the directory name 'checkout'" in verdict, ref


def _db_with_a_hidden_copy(tmp_path):
    catalog = tmp_path / "catalog"
    catalog.mkdir()
    _catalog_skill(catalog, "db", "tune a slow database query")
    other = catalog / "plugins" / "b" / "skills" / "db"
    other.mkdir(parents=True)
    (other / "SKILL.md").write_text(
        "---\nname: db\ndescription: a different card\n---\n\n# B\n", encoding="utf-8")
    hidden = catalog / "plugins" / "c" / "skills" / "db"
    hidden.mkdir(parents=True)
    (hidden / "SKILL.md").write_text(
        "---\nname: db\ndescription: a hidden card\ndisable-model-invocation: true\n---\n\n# B\n",
        encoding="utf-8")
    return catalog, other, hidden


def test_a_disabled_copy_of_a_name_is_no_conflict_with_the_enabled_copies(tmp_path):
    # no harness lists the disabled copy, so the two listed copies are the conflict, and the
    # disabled one is not named in it
    catalog, other, hidden = _db_with_a_hidden_copy(tmp_path)
    notes: list[str] = []
    assert catalog_competitors(str(catalog), set(), notes) == []
    conflict = [note for note in notes if "different content" in note]
    assert len(conflict) == 1
    for path in (catalog / "skills" / "db", other):
        assert str(path / "SKILL.md") in conflict[0]
    assert str(hidden / "SKILL.md") not in conflict[0]


def test_include_disabled_brings_the_disabled_copy_back_into_the_conflict(tmp_path):
    catalog, other, hidden = _db_with_a_hidden_copy(tmp_path)
    notes: list[str] = []
    assert catalog_competitors(str(catalog), set(), notes, include_disabled=True) == []
    conflict = [note for note in notes if "different content" in note]
    assert len(conflict) == 1
    for path in (catalog / "skills" / "db", other, hidden):
        assert str(path / "SKILL.md") in conflict[0]


def test_two_differently_named_skills_with_identical_text_both_stay_in_the_field(tmp_path):
    # Without a frontmatter name the name is the directory's, so identical text under skills/a
    # and skills/b is two skills, not a mirror: both are siblings the field must show.
    text = "---\ndescription: tune a slow database query\n---\n\n# B\n"
    for name in ("a", "b"):
        (tmp_path / "skills" / name).mkdir(parents=True)
        (tmp_path / "skills" / name / "SKILL.md").write_text(text, encoding="utf-8")
    notes: list[str] = []
    names = [c.name for c in catalog_competitors(str(tmp_path), set(), notes)]
    assert names == ["a", "b"]
    assert not any("identical to" in note for note in notes)


def test_two_manifest_entries_with_identical_text_and_different_names_both_stay(tmp_path):
    # The same rule for a skills.yaml that lists two skills pointing at identical bytes.
    text = "---\ndescription: tune a slow database query\n---\n\n# B\n"
    for name in ("a", "b"):
        (tmp_path / "skills" / name).mkdir(parents=True)
        (tmp_path / "skills" / name / "SKILL.md").write_text(text, encoding="utf-8")
    (tmp_path / "skills.yaml").write_text("- name: a\n- name: b\n", encoding="utf-8")
    notes: list[str] = []
    names = [c.name for c in catalog_competitors(str(tmp_path), set(), notes)]
    assert names == ["a", "b"]
    assert not any("identical to" in note for note in notes)


def test_two_same_name_copies_that_differ_only_late_in_the_body_are_skipped(tmp_path):
    # The copies share the frontmatter and the first 100+ bytes, so a digest of a prefix would
    # call them mirrors; the whole file decides, and they clash, so the name leaves the field.
    head = "---\nname: db\ndescription: tune a slow database query\n---\n\n" + "x" * 120
    for branch, tail in (("a", "\nfirst ending\n"), ("b", "\nsecond ending\n")):
        copy = tmp_path / branch / "skills" / "db"
        copy.mkdir(parents=True)
        (copy / "SKILL.md").write_text(head + tail, encoding="utf-8")
    notes: list[str] = []
    assert catalog_competitors(str(tmp_path), set(), notes) == []
    assert len(notes) == 1 and "db" in notes[0]


def _long_description_catalog(tmp_path):
    root = tmp_path / "catalog"
    _catalog_skill(root, "perf", "profile a slow binary with perf")
    _catalog_skill(root, "long", "x" * 1100)
    return root


def test_a_catalog_skill_over_the_spec_limit_is_left_out_with_a_note(tmp_path):
    notes: list[str] = []
    root = _long_description_catalog(tmp_path)
    names = [c.name for c in catalog_competitors(str(root), set(), notes)]
    assert names == ["perf"]
    assert len(notes) == 1 and "long" in notes[0] and "description" in notes[0]


def test_include_invalid_keeps_a_catalog_skill_over_the_spec_limit(tmp_path):
    root = _long_description_catalog(tmp_path)
    names = [c.name for c in catalog_competitors(str(root), set(), [], include_invalid=True)]
    assert sorted(names) == ["long", "perf"]


def test_the_saved_field_rule_records_include_invalid(tmp_path):
    # The saved result states the field rule; a run that kept spec-error skills must say so.
    root = _long_description_catalog(tmp_path)
    _catalog_skill(root, "db", "tune a slow database query")
    queries = tmp_path / "q.json"
    queries.write_text(json.dumps(["profile my binary"]), encoding="utf-8")
    out = tmp_path / "r.json"
    argv = ["novelty", "routing", "--artifact", f"skill:{root / 'skills' / 'perf'}",
            "--catalog", str(root), "--queries", str(queries), "--out", str(out)]
    args = _parse(*argv)
    args.func(args)
    assert json.loads(out.read_text(encoding="utf-8"))["field"]["include_invalid"] is False
    args = _parse(*argv, "--include-invalid")
    args.func(args)
    assert json.loads(out.read_text(encoding="utf-8"))["field"]["include_invalid"] is True


def test_the_include_invalid_flag_reaches_the_routing_field(tmp_path, capsys):
    root = _long_description_catalog(tmp_path)
    queries = tmp_path / "q.json"
    queries.write_text(json.dumps(["profile my binary"]), encoding="utf-8")
    argv = ["novelty", "routing", "--artifact", f"skill:{root / 'skills' / 'perf'}",
            "--catalog", str(root), "--queries", str(queries), "--out", str(tmp_path / "r.json")]
    left_out: list[str] = []
    field = _routing_field(_parse(*argv), {"perf"}, left_out)
    assert field == []
    assert "long" in capsys.readouterr().err and left_out
    field = _routing_field(_parse(*argv, "--include-invalid"), {"perf"}, [])
    assert [c.name for c in field] == ["long"]
