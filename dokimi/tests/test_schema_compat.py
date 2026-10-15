# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Artifact schemas only widen in place, and real artifacts keep loading.

Two halves of one contract. ``schema_compat.compare`` is the static half: an edit to an
existing ``<kind>.v<N>.json`` may not narrow what it accepts. The fixtures under
``tests/fixtures/artifacts/`` are the empirical half: one artifact per schema, captured
from what the framework's own code paths write, which every later schema of that version
must still accept. Add a fixture when you add a schema; never edit one to make a schema
change pass -- that is the break the fixture exists to catch.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from dokimi.artifacts import (
    SCHEMA_DIR,
    SCHEMA_VERSIONS,
    SUPERSEDED_VERSIONS,
    validate_artifact,
)
from dokimi.schema_compat import compare

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "artifacts"
SCHEMAS = sorted(p.stem for p in SCHEMA_DIR.glob("*.json"))
KIND_OF = {
    version: kind
    for kind, current in SCHEMA_VERSIONS.items()
    for version in (current, *SUPERSEDED_VERSIONS.get(kind, ()))
}


def test_every_schema_has_a_fixture():
    assert sorted(p.stem for p in FIXTURES.glob("*.json")) == SCHEMAS


@pytest.mark.parametrize("version", SCHEMAS)
def test_fixture_validates_against_its_schema(version):
    data = json.loads((FIXTURES / f"{version}.json").read_text(encoding="utf-8"))
    if version in KIND_OF:
        # Through the loader's own path, so version dispatch is exercised too.
        validate_artifact(KIND_OF[version], data)
    else:
        schema = json.loads((SCHEMA_DIR / f"{version}.json").read_text(encoding="utf-8"))
        assert list(Draft202012Validator(schema).iter_errors(data)) == []


def test_every_schema_compares_clean_with_itself():
    for version in SCHEMAS:
        schema = json.loads((SCHEMA_DIR / f"{version}.json").read_text(encoding="utf-8"))
        diff = compare(schema, schema)
        assert (diff.breaking, diff.notes) == ([], []), version


OBJ = {"type": "object", "properties": {"a": {"type": "integer"}}, "required": ["a"]}


def _with(**changes):
    return {**OBJ, **changes}


@pytest.mark.parametrize("new, expected", [
    (_with(properties={"a": {"type": "integer"}, "b": {"type": "string"}}), []),
    (_with(properties={"a": {"type": ["integer", "null"]}}), []),
    (_with(properties={"a": {"type": "number"}}), []),
])
def test_widening_edits_pass(new, expected):
    assert compare(OBJ, new).breaking == expected


def test_a_bound_may_loosen_but_not_tighten_or_appear():
    assert compare({"minimum": 0, "maxLength": 5}, {"minimum": -5, "maxLength": 9}).breaking == []
    assert compare({"minimum": 0, "maxLength": 5}, {"minimum": 1, "maxLength": 4}).breaking == [
        "<root>: minimum raised from 0 to 1", "<root>: maxLength lowered from 5 to 4"]
    assert compare({}, {"minimum": -5}).breaking == ["<root>: minimum -5 added"]


@pytest.mark.parametrize("new, fragment", [
    (_with(required=["a", "b"]), "'b' is now required"),
    (_with(properties={}, additionalProperties=False), "property 'a' removed"),
    (_with(properties={"a": {"type": "string"}}), "type narrowed"),
    (_with(properties={"a": {"type": "integer", "minimum": 0}}), "minimum 0 added"),
    (_with(properties={"a": {"type": "integer", "maximum": 9}}), "maximum 9 added"),
    (_with(additionalProperties=False), "now rejects everything"),
    (_with(properties={"a": {"type": "integer", "const": 1}}), "const changed"),
])
def test_narrowing_edits_fail(new, fragment):
    breaking = compare(OBJ, new).breaking
    assert any(fragment in line for line in breaking), breaking


def test_enum_may_grow_but_not_shrink():
    old = {"enum": ["a", "b"]}
    assert compare(old, {"enum": ["a", "b", "c"]}).breaking == []
    assert compare(old, {"enum": ["a"]}).breaking == ["<root>: enum no longer accepts ['b']"]


def test_number_to_integer_is_a_narrowing():
    assert compare({"type": "number"}, {"type": "integer"}).breaking


def test_nested_items_and_defs_are_walked():
    old = {"$defs": {"row": {"type": "object", "properties": {"x": {}},
                             "additionalProperties": False}},
           "type": "array", "items": {"type": "object", "properties": {"y": {}}}}
    new = {"$defs": {"row": {"type": "object", "properties": {}, "additionalProperties": False}},
           "type": "array", "items": {"type": "object", "properties": {"y": {}}, "required": ["y"]}}
    breaking = compare(old, new).breaking
    assert "<root>: 'y' is now required" not in breaking
    assert "[]: 'y' is now required" in breaking
    assert "/$defs/row: property 'x' removed" in breaking


def test_a_changed_description_is_a_note_not_a_break():
    diff = compare({"type": "number"}, {"type": "number", "description": "now per scored cell"})
    assert diff.breaking == [] and diff.notes == ["<root>: description added"]


@pytest.mark.parametrize("key, value", [
    ("then", {"required": ["b"]}),
    ("not", {"required": ["a"]}),
    ("dependentSchemas", {"a": {"required": ["b"]}}),
    ("unevaluatedProperties", False),
    ("multipleOf", 2),
])
def test_an_unmodelled_keyword_that_appears_or_changes_fails_closed(key, value):
    breaking = compare(OBJ, _with(**{key: value})).breaking
    assert any(f"{key} added" in line for line in breaking), breaking
    assert compare(_with(**{key: value}), _with(**{key: value})).breaking == []


def test_a_dependency_on_what_was_required_rejects_nothing_old():
    old = {"type": "object", "required": ["a", "b", "c"]}
    new = {"type": "object", "required": ["a"],
           "dependentRequired": {"b": ["a", "c"], "c": ["b"], "x": ["a"]}}
    assert compare(old, new).breaking == []


def test_a_dependency_on_an_optional_property_narrows():
    old = {"type": "object", "required": ["a"], "dependentRequired": {"b": ["c"]}}
    assert compare(old, {**old, "dependentRequired": {"b": ["c"]}}).breaking == []
    assert compare(old, {**old, "dependentRequired": {"b": ["c", "d"], "e": ["a", "f"]}}).breaking == [
        "<root>: 'b' now requires ['d']", "<root>: 'e' now requires ['f']"]


def test_annotations_are_not_breaks():
    assert compare(OBJ, _with(title="t", examples=[{"a": 1}], format="date-time")).breaking == []


def test_main_against_a_base(monkeypatch, tmp_path, capsys):
    """The CLI path: list the base's schemas, diff each, report and set the exit code."""
    import dokimi.schema_compat as sc

    current = {"a.v1.json": OBJ, "b.v1.json": OBJ,
               "c.v1.json": _with(description="per scored cell")}
    for name, schema in current.items():
        (tmp_path / name).write_text(json.dumps(schema), encoding="utf-8")
    current["d.v1.json"] = _with(properties={"a": {"type": "integer"}, "b": {"type": "string"}})
    (tmp_path / "d.v1.json").write_text(json.dumps(current["d.v1.json"]), encoding="utf-8")
    base = {"a.v1.json": OBJ, "b.v1.json": _with(required=[]), "c.v1.json": OBJ,
            "d.v1.json": OBJ, "gone.v1.json": OBJ}
    fixtures = {"b.v1.json": {"a": 1}}

    def fake_git(*args):
        if args[0] == "ls-tree":
            return "\n".join([*base, "README.md"])
        rel = args[1].split(":./", 1)[1]
        if rel.startswith(sc.FIXTURE_DIR + "/"):
            name = rel.rsplit("/", 1)[1]
            if name not in fixtures:
                raise subprocess.CalledProcessError(128, ["git", *args])
            return json.dumps(fixtures[name])
        return json.dumps(base[rel])

    monkeypatch.setattr(sc, "SCHEMA_DIR", tmp_path)
    monkeypatch.setattr(sc, "_git", fake_git)
    assert sc.main(["--base", "X"]) == 1
    out = capsys.readouterr()
    assert "BREAKING b.v1.json <root>: 'a' is now required" in out.out
    assert "BREAKING gone.v1.json schema file removed" in out.out
    assert "note     c.v1.json <root>: description added" in out.out
    assert "WARNING  d.v1.json /b: was unconstrained" in out.out
    assert "2 breaking change(s)" in out.err

    base = {"a.v1.json": OBJ, "c.v1.json": OBJ, "d.v1.json": OBJ}
    assert sc.main(["--base", "X"]) == 0
    out = capsys.readouterr().out
    assert "WARNING  d.v1.json" in out
    assert "schema compatibility vs X: OK" in out

    fixtures["d.v1.json"] = {"a": 1, "b": 2}  # an existing producer writes "b"
    assert sc.main(["--base", "X"]) == 1
    assert "BREAKING d.v1.json /b: was unconstrained" in capsys.readouterr().out


# ── a new property: widening on a closed object, narrowing on an open one ─────

CLOSED = _with(additionalProperties=False)
TYPED_B = {"a": {"type": "integer"}, "b": {"type": "integer"}}


def test_a_new_typed_property_on_a_closed_object_widens():
    diff = compare(CLOSED, {**CLOSED, "properties": TYPED_B}, fixture={"a": 1})
    assert (diff.breaking, diff.warnings) == ([], [])


def test_a_property_removed_from_a_closed_object_narrows():
    diff = compare({**CLOSED, "properties": TYPED_B}, CLOSED)
    assert diff.breaking == ["<root>: property 'b' removed"]


@pytest.mark.parametrize("fixture, broken", [
    ({"a": 1, "b": 2}, True),   # an existing producer writes "b": old artifacts may hold any value
    ({"a": 1}, False),          # nothing known writes it: a warning, not a failure
])
def test_a_new_typed_property_on_an_open_object_narrows(fixture, broken):
    """Before, ``{"a": 1, "b": "n/a"}`` was valid; with ``b: integer`` it is not."""
    new = _with(properties=TYPED_B)
    assert Draft202012Validator(OBJ).is_valid({"a": 1, "b": "n/a"})
    assert not Draft202012Validator(new).is_valid({"a": 1, "b": "n/a"})
    diff = compare(OBJ, new, fixture=fixture)
    assert bool(diff.breaking) is broken
    assert bool(diff.warnings) is not broken
    assert "/b: was unconstrained, now constrained" in (diff.breaking or diff.warnings)[0]


def test_without_a_fixture_a_new_typed_property_on_an_open_object_warns():
    diff = compare(OBJ, _with(properties=TYPED_B))
    assert diff.breaking == [] and len(diff.warnings) == 1


def test_an_unconstrained_new_property_on_an_open_object_widens():
    new = _with(properties={"a": {"type": "integer"}, "b": {"description": "anything"}})
    diff = compare(OBJ, new, fixture={"a": 1, "b": "x"})
    assert (diff.breaking, diff.warnings) == ([], [])


def test_a_new_property_is_compared_with_the_old_additional_properties_schema():
    old = _with(additionalProperties={"type": "number"})
    widen = _with(additionalProperties={"type": "number"},
                  properties={"a": {"type": "integer"}, "b": {"type": ["number", "null"]}})
    narrow = _with(additionalProperties={"type": "number"}, properties=TYPED_B)
    assert compare(old, widen, fixture={"a": 1, "b": 2.5}).breaking == []
    assert compare(old, narrow, fixture={"a": 1, "b": 2.5}).breaking == [
        "/b: type narrowed from ['integer', 'number'] to ['integer'] (new property on an "
        "open object, and the base fixture writes 'b')"]


def test_a_property_removed_from_an_open_object_warns_and_checks_the_fallback():
    old = _with(properties=TYPED_B)
    diff = compare(old, OBJ, fixture={"a": 1, "b": 2})
    assert diff.breaking == [] and "property 'b' removed" in diff.warnings[0]
    # The key now falls to additionalProperties, which must still accept it.
    narrowed = compare(old, _with(additionalProperties={"type": "string"}))
    assert "/b: type narrowed from ['integer'] to ['string']" in narrowed.breaking


def test_the_fixture_is_followed_through_maps_arrays_and_refs():
    """#79's case: per_harness.<h> is a map of open objects; ``errored`` is new there."""
    row = {"type": "object", "properties": {"n": {"type": "integer"}}}
    old = {"type": "object", "properties": {
        "per_harness": {"type": "object", "additionalProperties": row},
        "rows": {"type": "array", "items": {"$ref": "#/$defs/row"}},
    }, "$defs": {"row": row}}
    grown = {**row, "properties": {**row["properties"], "errored": {"type": "integer"}}}
    new = {**old, "properties": {
        "per_harness": {"type": "object", "additionalProperties": grown},
        "rows": {"type": "array", "items": {"$ref": "#/$defs/row"}},
    }, "$defs": {"row": grown}}

    quiet = {"per_harness": {"x": {"n": 1}}, "rows": [{"n": 1}]}
    diff = compare(old, new, fixture=quiet)
    assert diff.breaking == []
    assert [w.split(":")[0] for w in diff.warnings] == [
        "/per_harness/<additionalProperties>/errored", "/$defs/row/errored"]

    writes = {"per_harness": {"x": {"n": 1, "errored": 0}}, "rows": [{"n": 1, "errored": 0}]}
    diff = compare(old, new, fixture=writes)
    assert diff.warnings == []
    assert [b.split(":")[0] for b in diff.breaking] == [
        "/per_harness/<additionalProperties>/errored", "/$defs/row/errored"]


def test_compare_against_reads_the_base_as_utf8(monkeypatch, tmp_path):
    """git's output is UTF-8 whatever the locale: decoded as cp1252 (Windows), an unchanged
    non-ASCII description reads as changed, and some characters do not decode at all."""
    import subprocess

    import dokimi.schema_compat as sc

    schema = {"type": "object", "description": "Spearman ρ — see § 2",
              "properties": {"k": {"enum": ["ρ"]}}}
    (tmp_path / "a.v1.json").write_text(json.dumps(schema, ensure_ascii=False), encoding="utf-8")
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "core.autocrlf=false"]
    subprocess.run([*git, "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run([*git, "add", "."], cwd=tmp_path, check=True)
    subprocess.run([*git, "commit", "-qm", "base"], cwd=tmp_path, check=True)
    monkeypatch.setattr(sc, "SCHEMA_DIR", tmp_path)
    assert sc.compare_against("HEAD") == {}


def test_compare_against_follows_schemas_that_moved(monkeypatch, tmp_path):
    """A PR that renames the project moves every schema. The base has nothing at the new path,
    so listing it compared zero schemas and passed; the base side is read where git's rename
    detection says the files were, the fixtures included."""
    import dokimi.schema_compat as sc

    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "core.autocrlf=false"]
    old_schemas, old_fixtures = tmp_path / "old/pkg_old/schemas", tmp_path / "old/tests/fixtures/artifacts"
    old_schemas.mkdir(parents=True)
    old_fixtures.mkdir(parents=True)
    (old_schemas / "a.v1.json").write_text(json.dumps(OBJ), encoding="utf-8")
    # Left unchanged, so git sees the move even though the tiny a.v1.json changes past its
    # similarity threshold -- as in a real rename, where most schemas only move.
    (old_schemas / "b.v1.json").write_text(json.dumps(_with(description="x" * 200)), encoding="utf-8")
    (old_fixtures / "a.v1.json").write_text(json.dumps({"a": 1, "b": 2}), encoding="utf-8")
    subprocess.run([*git, "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run([*git, "add", "."], cwd=tmp_path, check=True)
    subprocess.run([*git, "commit", "-qm", "base"], cwd=tmp_path, check=True)
    subprocess.run([*git, "mv", "old", "new"], cwd=tmp_path, check=True)
    subprocess.run([*git, "mv", "new/pkg_old", "new/pkg"], cwd=tmp_path, check=True)
    subprocess.run([*git, "commit", "-qm", "rename"], cwd=tmp_path, check=True)
    schemas = tmp_path / "new/pkg/schemas"
    (schemas / "a.v1.json").write_text(json.dumps(_with(properties=TYPED_B)), encoding="utf-8")
    monkeypatch.setattr(sc, "SCHEMA_DIR", schemas)

    results = sc.compare_against("HEAD~1")
    # Breaking, not a warning: only the base fixture (at its old path) shows "b" is written.
    assert list(results) == ["a.v1.json"]
    assert "/b: was unconstrained, now constrained" in results["a.v1.json"].breaking[0]


def test_main_warns_when_the_base_has_no_schemas_to_compare(monkeypatch, tmp_path, capsys):
    """Zero base schemas against a tree that has some is how the gate went vacuous: say so."""
    import dokimi.schema_compat as sc

    (tmp_path / "a.v1.json").write_text(json.dumps(OBJ), encoding="utf-8")
    monkeypatch.setattr(sc, "SCHEMA_DIR", tmp_path)
    monkeypatch.setattr(sc, "_git", lambda *args: "")
    monkeypatch.setattr(sc, "_renamed_from", lambda base, rel: None)
    assert sc.main(["--base", "X"]) == 0
    assert "WARNING  - no schemas found at X to compare against" in capsys.readouterr().out


@pytest.mark.parametrize("new", [
    {"oneOf": [{"type": "string"}, {"type": ["integer", "string"]}]},
    {"oneOf": [{"type": "string"}, {"type": "integer"}, {"type": ["string", "null"]}]},
])
def test_widening_a_oneof_branch_is_a_narrowing(new):
    """oneOf wants exactly one match: a wider or extra branch makes "x" match two."""
    old = {"oneOf": [{"type": "string"}, {"type": "integer"}]}
    assert Draft202012Validator(old).is_valid("x") and not Draft202012Validator(new).is_valid("x")
    assert compare(old, new).breaking
