# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The saved answer key's bundle record, parsed once: what the parse keeps and what checks do."""

from __future__ import annotations

import pytest

from dokimi.novelty.answer_key import ABSENT, AnswerKeyRecord, RecordedFile, parse_answer_key
from dokimi.novelty.bundle import check_answer_key, entry_point_only, load_bundle
from dokimi.novelty.spans import segment

_SKILL = "---\nname: keyrec\ndescription: use when testing keys\n---\n\nUse `--accel` always.\n"


def _tree(tmp_path):
    root = tmp_path / "keyrec"
    (root / "references").mkdir(parents=True)
    (root / "SKILL.md").write_text(_SKILL, encoding="utf-8")
    (root / "references" / "tuning.md").write_text("Never exceed 64 threads.\n", encoding="utf-8")
    return root


def test_a_legacy_or_empty_record_parses_to_an_absent_record():
    for raw in (None, {}):
        key = parse_answer_key(raw)
        assert key == AnswerKeyRecord()
        assert key.present is False and key.files == () and key.excluded is None


def test_an_absent_excluded_key_is_not_the_same_as_an_empty_one():
    assert parse_answer_key({"files": []}).excluded is None
    assert parse_answer_key({"excluded": []}).excluded == ()
    # Stored as null, the key is present: the check reads it as an empty list.
    assert parse_answer_key({"excluded": None}).excluded == ()


def test_parsing_keeps_stored_values_unconverted():
    # A bad budget fails inside the check that replays it, where it always has (`unloadable`),
    # not at parse time.
    key = parse_answer_key({"include_resources": 1, "max_file_words": "x",
                            "max_total_words": "y", "files": []})
    assert key.include_resources is True
    assert (key.max_file_words, key.max_total_words) == ("x", "y")
    assert key.resource_words_measured == 0 and key.n_files is None


def test_a_row_must_carry_a_path_and_a_role_to_be_usable():
    assert RecordedFile(path="SKILL.md", role="entry_point").usable
    assert not RecordedFile(path="SKILL.md").usable
    assert not RecordedFile(role="reference").usable
    assert not RecordedFile().usable
    key = parse_answer_key({"files": [{"path": "SKILL.md", "role": "entry_point"}, "x", 5]})
    assert [row.usable for row in key.files] == [True, False, False]
    assert not key.usable_files
    assert parse_answer_key({"files": "SKILL.md"}).files == ()


def test_a_parsed_record_is_returned_as_it_is():
    key = parse_answer_key({"files": [{"path": "SKILL.md", "role": "entry_point"}]})
    assert parse_answer_key(key) is key


def test_checks_take_the_parsed_record_and_give_the_same_verdict_as_the_dict(tmp_path):
    root = _tree(tmp_path)
    record = load_bundle(f"skill:{root}", include_resources=True).summary()
    (root / "references" / "tuning.md").write_text("Never exceed 128.\n", encoding="utf-8")
    for raw in (record, {"include_resources": True, "files": []}, None):
        from_dict = check_answer_key(f"skill:{root}", raw, source="a.json")
        from_parsed = check_answer_key(f"skill:{root}", parse_answer_key(raw), source="a.json")
        assert (from_parsed.problem, from_parsed.message, from_parsed.notes,
                from_parsed.edited) == (from_dict.problem, from_dict.message,
                                        from_dict.notes, from_dict.edited)
    verdict = check_answer_key(f"skill:{root}", record, source="a.json")
    assert (verdict.problem, verdict.edited) == ("files_changed", ("references/tuning.md",))


@pytest.mark.parametrize("files", ["SKILL.md", {"path": "SKILL.md"}, 3])
def test_a_files_value_that_is_not_a_list_is_a_refusal_not_a_traceback(tmp_path, files):
    root = _tree(tmp_path)
    verdict = check_answer_key(f"skill:{root}", {"include_resources": True, "files": files},
                               source="a.json")
    assert (verdict.ok, verdict.problem) == (False, "no_file_rows")
    assert "without usable file rows" in verdict.message


def test_a_non_object_row_is_a_refusal_not_a_traceback(tmp_path):
    root = _tree(tmp_path)
    record = {"include_resources": True,
              "files": [{"path": "SKILL.md", "role": "entry_point"}, 5]}
    verdict = check_answer_key(f"skill:{root}", record, source="a.json")
    assert (verdict.ok, verdict.problem) == (False, "no_file_rows")


def test_shortening_takes_the_parsed_record_and_refuses_a_bundle_without_rows(tmp_path):
    root = _tree(tmp_path)
    bundle = load_bundle(f"skill:{root}", include_resources=True)
    spans = segment(bundle.text)
    record = bundle.summary()
    assert entry_point_only(parse_answer_key(record), spans, [])[2] == (
        entry_point_only(record, spans, [])[2])
    with pytest.raises(ValueError, match="file rows"):
        entry_point_only({"include_resources": True, "files": "SKILL.md"}, [], [])


def test_a_row_with_no_span_range_is_named_in_the_error_even_without_a_path():
    key = parse_answer_key({"include_resources": True, "files": [{"role": "entry_point"}]})
    assert key.files[0].path is ABSENT and key.files[0].end is ABSENT
    with pytest.raises(ValueError, match="span range for None"):
        entry_point_only(key, [], [])


def test_an_entry_point_row_without_a_path_is_refused_by_name():
    key = parse_answer_key({"include_resources": True,
                            "files": [{"role": "entry_point", "end": 9}]})
    with pytest.raises(ValueError, match="entry point has no path"):
        entry_point_only(key, [], [])


def test_an_entry_point_row_that_is_not_an_object_is_refused_as_a_file_row_error():
    key = parse_answer_key({"include_resources": True, "files": [5]})
    assert key.files[0].is_object is False
    with pytest.raises(ValueError, match="without file rows"):
        entry_point_only(key, [], [])
