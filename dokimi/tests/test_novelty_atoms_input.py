# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`novelty probe` and `novelty shorten` on an --atoms file they cannot read."""

from __future__ import annotations

import json

import pytest

from dokimi.cli import build_parser


def _bundle(extra, end="5"):
    row = '{"role": "entry", "path": "SKILL.md", "end": %s}' % end
    inner = '"include_resources": true, "files": [%s]' % row
    return '{"atoms": [], "spans": [], "bundle": {%s%s}}' % (inner, ", " + extra if extra else "")


_SPAN = {"id": "s0001", "kind": "sentence", "text": "Text.", "start": 0, "end": 5}
_ATOM = {"id": "a1", "statement": "Text.", "question": "Q?", "span_ids": ["s0001"]}


def _record(span=None, atom=None):
    """An atoms record of one span and one atom, with the given fields replaced."""
    return json.dumps({"atoms": [{**_ATOM, **(atom or {})}],
                       "spans": [{**_SPAN, **(span or {})}]})


_WRONG_SHAPES = {
    "top_level_list": "[]",
    "top_level_null": "null",
    "top_level_number": "5",
    "wrong_shape": '{"atoms": [{"unexpected": 1}], "spans": []}',
    "atoms_not_a_list": '{"atoms": "x"}',
    "bundle_is_a_list": '{"atoms": [], "spans": [], "bundle": [1]}',
    "bundle_is_a_number": '{"atoms": [], "spans": [], "bundle": 5}',
    "bundle_files_not_objects":
        '{"atoms": [], "spans": [], "bundle": {"include_resources": true, "files": [1]}}',
    "bundle_files_not_a_list": '{"atoms": [], "spans": [], "bundle": {"files": "x"}}',
    "bundle_max_file_words_is_a_list": _bundle('"max_file_words": [1]'),
    "bundle_max_total_words_is_a_list": _bundle('"max_total_words": [1]'),
    "span_text_is_a_number": _record(span={"text": 5}),
    "span_start_is_a_string": _record(span={"start": "a"}),
    "span_start_is_a_bool": _record(span={"start": True}),
    "span_end_is_null": _record(span={"end": None}),
    "atom_id_is_a_list": _record(atom={"id": [1]}),
    "atom_question_is_a_number": _record(atom={"question": 5}),
    "atom_statement_is_null": _record(atom={"statement": None}),
    "atom_span_ids_has_a_list": _record(atom={"span_ids": [[1]]}),
    "atom_span_ids_is_a_string": _record(atom={"span_ids": "s0001"}),
    "span_id_is_a_list": _record(span={"id": [1]}),
    "span_kind_is_a_number": _record(span={"kind": 5}),
    "span_heading_path_is_a_string": _record(span={"heading_path": "x"}),
    "span_protection_has_a_number": _record(span={"protection": [1]}),
    "spans_not_a_list": json.dumps({"atoms": [], "spans": "x"}),
    "spans_has_a_string": json.dumps({"atoms": [], "spans": ["x"]}),
    "atoms_has_a_number": json.dumps({"atoms": [5], "spans": []}),
    "bundle_max_file_words_is_a_bool": _bundle('"max_file_words": true'),
    "bundle_max_total_words_is_a_bool": _bundle('"max_total_words": true'),
    "bundle_roles_is_a_number": _bundle('"resource_roles": 5'),
    "bundle_roles_is_a_string": _bundle('"resource_roles": "entry"'),
    "bundle_roles_has_a_number": _bundle('"resource_roles": ["a", 1]'),
    "bundle_excluded_not_objects": _bundle('"excluded": ["x"]'),
    "bundle_excluded_path_is_a_list": _bundle('"excluded": [{"path": ["x"]}]'),
    "bundle_excluded_path_is_an_object": _bundle('"excluded": [{"path": {}}]'),
    "bundle_file_end_is_a_string": _bundle('', end='"x"'),
    "bundle_file_end_is_a_bool": _bundle('', end='true'),
    "bundle_file_words_is_a_string": _bundle('', end='5, "words": "x"'),
    "bundle_file_role_is_a_list": _bundle('', end='5, "role": ["entry"]'),
    "bundle_file_path_is_a_number": _bundle('', end='5, "path": 5'),
    **{f"span_has_no_{key}": json.dumps({"atoms": [], "spans": [
        {k: v for k, v in _SPAN.items() if k != key}]})
       for key in ("id", "kind", "text", "start", "end")},
    "atoms_is_a_number": '{"atoms": 5, "spans": []}',
    "spans_is_null": '{"atoms": [], "spans": null}',
    **{f"bundle_{key}_is_falsy": json.dumps({"atoms": [], "spans": [], "bundle": {key: value}})
       for key, value in (("max_file_words", False), ("max_total_words", False),
                          ("n_files", False), ("resource_words_measured", False),
                          ("artifact_name", 0), ("resource_roles", ""),
                          ("files", 0), ("excluded", 0))},
    "extraction_is_falsy": json.dumps({"atoms": [], "spans": [], "extraction": 0}),
    "extraction_unread_is_falsy": json.dumps(
        {"atoms": [], "spans": [], "extraction": {"unread_span_ids": ""}}),
    "span_atom_ids_has_a_number": _record(span={"atom_ids": [1]}),
    "atom_answer_is_a_number": _record(atom={"answer": 5}),
    "atom_kind_is_a_list": _record(atom={"kind": ["x"]}),
    "atom_faithfulness_is_a_number": _record(atom={"faithfulness": 5}),
    "atom_tags_is_a_string": _record(atom={"tags": "x"}),
    "atom_meta_is_a_list": _record(atom={"meta": [1]}),
    "extraction_is_a_list": json.dumps({"atoms": [], "spans": [], "extraction": [1]}),
    "extraction_unread_has_a_list": json.dumps(
        {"atoms": [], "spans": [], "extraction": {"unread_span_ids": [[1]]}}),
    "bundle_file_has_no_path": '{"atoms": [], "spans": [], "bundle": {"include_resources": '
                               'true, "files": [{"role": "entry", "end": 5}]}}',
    "bundle_file_start_is_a_string": _bundle('', end='5, "start": "x"'),
    "bundle_file_sha256_is_a_number": _bundle('', end='5, "sha256": 5'),
    "bundle_artifact_name_is_a_list": _bundle('"artifact_name": ["s"]'),
    "bundle_n_files_is_a_string": _bundle('"n_files": "1"'),
    "bundle_resource_words_is_a_list": _bundle('"resource_words_measured": [1]'),
    "bundle_excluded_role_is_a_number": _bundle('"excluded": [{"path": "a", "role": 5}]'),
    "bundle_excluded_words_is_a_string": _bundle('"excluded": [{"path": "a", "words": "x"}]'),
    "nested_deeply": "[" * 100000 + "]" * 100000,
    "atoms_nested_deeply": '{"atoms": ' + "[" * 100000 + "]" * 100000 + "}",
}


def _atoms_file(tmp_path, kind):
    path = tmp_path / "atoms.json"
    if kind == "invalid_json":
        path.write_text("{not json", encoding="utf-8")
    elif kind == "not_utf8":
        path.write_bytes(b"\xff\xfe\x00")
    elif kind == "directory":
        path.mkdir()
    elif kind in _WRONG_SHAPES:
        path.write_text(_WRONG_SHAPES[kind], encoding="utf-8")
    return path


@pytest.mark.parametrize("sub", ["probe", "shorten"])
@pytest.mark.parametrize("kind", ["missing", "invalid_json", "not_utf8", "directory", *_WRONG_SHAPES])
def test_an_unreadable_atoms_file_exits_with_a_message_naming_it(tmp_path, sub, kind):
    skill = tmp_path / "SKILL.md"
    skill.write_text("---\nname: s\ndescription: d\n---\n\n# H\n\nText.\n", encoding="utf-8")
    atoms = _atoms_file(tmp_path, kind)
    args = build_parser().parse_args(
        ["novelty", sub, "--artifact", f"skill:{skill}", "--atoms", str(atoms)]
    )
    with pytest.raises(SystemExit) as excinfo:
        args.func(args)
    message = str(excinfo.value)
    assert message.startswith("cannot read atoms from ")
    assert str(atoms) in message
    assert "has no attribute" not in message


@pytest.mark.parametrize("bundle", ['null', '{}', '{"files": []}',
                                    '{"files": [{"path": "SKILL.md", "sha256": null}]}'])
def test_a_bundle_block_of_the_right_shape_is_not_refused(tmp_path, bundle):
    from dokimi.novelty.atoms import load_atoms_or_exit

    path = tmp_path / "atoms.json"
    path.write_text('{"atoms": [], "spans": [], "bundle": %s}' % bundle, encoding="utf-8")
    atoms, spans, record = load_atoms_or_exit(path)
    assert (atoms, spans, record["bundle"]) == ([], [], json.loads(bundle))


def test_an_atom_with_only_a_statement_is_accepted(tmp_path):
    from dokimi.novelty.atoms import load_atoms_or_exit

    path = tmp_path / "atoms.json"
    span = {"id": "s0001", "kind": "sentence", "text": "S.", "start": 0, "end": 2}
    path.write_text(json.dumps({"atoms": [{"statement": "S."}], "spans": [span]}), encoding="utf-8")
    atoms, spans, _ = load_atoms_or_exit(path)
    assert [(a.statement, a.question, a.span_ids) for a in atoms] == [("S.", "", ())]
    assert [(s.id, s.heading_path, s.protection) for s in spans] == [("s0001", (), ())]


@pytest.mark.parametrize("record, fragment", [
    pytest.param(_record(span={"text": 5}), "'text' is not a string", id="span_text"),
    pytest.param(_record(span={"start": True}), "'start' is not an integer",
                 id="span_start_bool"),
    pytest.param(_record(span={"end": None}), "'end' is not an integer",
                 id="span_end_null"),
    pytest.param(_record(span={"id": [1]}), "'spans' has a row whose 'id' is not a string", id="span_id"),
    pytest.param(_record(span={"kind": 5}), "'kind' is not a string", id="span_kind"),
    pytest.param(_record(span={"protection": [1]}), "'protection' is not a list",
                 id="span_protection"),
    pytest.param(_record(span={"heading_path": "x"}), "'heading_path' is not a list",
                 id="span_heading_path"),
    pytest.param(_record(atom={"id": [1]}), "'atoms' has a row whose 'id' is not a string",
                 id="atom_id"),
    pytest.param(_record(atom={"question": 5}), "'question' is not a string",
                 id="atom_question"),
    pytest.param(_record(atom={"statement": None}),
                 "'statement' is not a string", id="atom_statement"),
    pytest.param(_record(atom={"span_ids": "s0001"}), "'span_ids' is not a list of strings",
                 id="atom_span_ids"),
    pytest.param(json.dumps({"atoms": [], "spans": "x"}), "'spans' is not a list of objects",
                 id="spans_not_a_list"),
    pytest.param(json.dumps({"atoms": [5], "spans": []}), "'atoms' is not a list of objects",
                 id="atoms_has_a_number"),
])
def test_a_wrong_span_or_atom_field_is_named_in_the_refusal(tmp_path, record, fragment):
    from dokimi.novelty.atoms import load_atoms_or_exit

    path = tmp_path / "atoms.json"
    path.write_text(record, encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        load_atoms_or_exit(path)
    assert fragment in str(exc.value)


@pytest.mark.parametrize("kind, fragment", [
    ("span_has_no_id", "'spans' has a row without 'id'"),
    ("span_atom_ids_has_a_number", "'atom_ids' is not a list of strings"),
    ("atom_answer_is_a_number", "'answer' is not a string"),
    ("atom_kind_is_a_list", "'atoms' has a row whose 'kind' is not a string"),
    ("atom_faithfulness_is_a_number", "'faithfulness' is not a string"),
    ("atom_tags_is_a_string", "'tags' is not a list of strings"),
    ("atom_meta_is_a_list", "'meta' is not an object"),
    ("extraction_is_a_list", "'extraction' is not an object"),
    ("extraction_unread_has_a_list", "'extraction.unread_span_ids' is not a list of strings"),
    ("bundle_file_has_no_path", "'bundle.files' has a row without 'path'"),
    ("bundle_file_start_is_a_string", "'start' is not an integer"),
    ("bundle_file_sha256_is_a_number", "'sha256' is not a string or null"),
    ("bundle_artifact_name_is_a_list", "'bundle.artifact_name' is not a string"),
    ("bundle_n_files_is_a_string", "'bundle.n_files' is not an integer"),
    ("bundle_resource_words_is_a_list", "'bundle.resource_words_measured' is not an integer"),
    ("bundle_excluded_role_is_a_number", "'bundle.excluded' has a row whose 'role'"),
    ("bundle_excluded_words_is_a_string", "'bundle.excluded' has a row whose 'words'"),
])
def test_every_field_a_reader_uses_is_checked(tmp_path, kind, fragment):
    from dokimi.novelty.atoms import load_atoms_or_exit

    with pytest.raises(SystemExit) as exc:
        load_atoms_or_exit(_atoms_file(tmp_path, kind))
    assert fragment in str(exc.value)


def test_a_file_row_without_a_role_is_left_to_the_answer_key_check(tmp_path):
    from dokimi.novelty.atoms import load_atoms_or_exit

    path = tmp_path / "atoms.json"
    path.write_text(json.dumps({"atoms": [], "spans": [], "bundle": {
        "files": [{"path": "SKILL.md", "end": 5}]}}), encoding="utf-8")
    assert load_atoms_or_exit(path)[2]["bundle"]["files"][0]["path"] == "SKILL.md"
