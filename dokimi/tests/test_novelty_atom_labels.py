# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Labels attached to analysed atoms (`skill novelty view --atom-labels`): the known/new shares per label."""

from __future__ import annotations

import argparse
import json

import pytest

from dokimi.cli import build_parser
from dokimi.novelty.atom_labels import label_shares, load_atom_labels
from dokimi.utils import InputError
from tests.test_novelty_atom_verdicts import _analyze, _run
from tests.test_novelty_atom_verdicts import mixed_models as _mixed_models
from tests.test_novelty_atom_verdicts import skill as _skill

skill, mixed_models = _skill, _mixed_models  # the atom verdicts tests' fixtures, under their names

REF = "skill:skills/accel-tuning"


def _rows():
    return [
        {"id": "a1", "category": "known_useful"},
        {"id": "a2", "category": "novel_useful"},
        {"id": "a3", "category": "novel_inert"},
        {"id": "a4", "category": "scaffolding"},
        {"id": "a5", "category": None},
        {"id": "a6", "category": "known_inert"},
    ]


def test_jsonl_and_csv_labels_load_to_the_same_keys(tmp_path):
    jsonl = tmp_path / "labels.jsonl"
    jsonl.write_text('{"skill": "skills/accel-tuning", "atom_id": "a1", "label": "setup"}\n'
                     '{"skill": "skill:skills/accel-tuning", "atom_id": "a2", "label": "api"}\n',
                     encoding="utf-8")
    csv_file = tmp_path / "labels.csv"
    csv_file.write_text("skill,atom_id,label\nskills/accel-tuning,a1,setup\nskills/accel-tuning,a2,api\n",
                        encoding="utf-8")
    expected = {("skills/accel-tuning", "a1"): "setup", ("skills/accel-tuning", "a2"): "api"}
    assert load_atom_labels(jsonl) == expected
    assert load_atom_labels(csv_file) == expected


def test_a_label_row_without_its_key_or_label_is_refused(tmp_path):
    path = tmp_path / "labels.jsonl"
    path.write_text('{"skill": "skills/accel-tuning", "atom_id": "a1"}\n', encoding="utf-8")
    with pytest.raises(InputError, match="row 1 needs non-empty skill, atom_id, label"):
        load_atom_labels(path)


def test_one_atom_with_two_labels_is_refused(tmp_path):
    path = tmp_path / "labels.jsonl"
    path.write_text(json.dumps({"skill": "s", "atom_id": "a1", "label": "x"}) + "\n"
                    + json.dumps({"skill": "s", "atom_id": "a1", "label": "y"}) + "\n", encoding="utf-8")
    with pytest.raises(InputError, match="two labels"):
        load_atom_labels(path)


def test_known_and_new_shares_are_counted_per_label_over_categorised_atoms():
    labels = {("skills/accel-tuning", "a1"): "setup", ("skills/accel-tuning", "a2"): "setup",
              ("skills/accel-tuning", "a3"): "api", ("skills/accel-tuning", "a4"): "api"}
    shares = label_shares(_rows(), REF, labels)

    assert shares["by_label"]["setup"] == {"atoms": 2, "known": 1, "new": 1, "other": 0,
                                           "uncategorised": 0, "known_share": 0.5, "new_share": 0.5}
    assert shares["by_label"]["api"]["known"] == 0
    assert shares["by_label"]["api"]["new"] == 1
    assert shares["by_label"]["api"]["other"] == 1
    # a5 (no label, no category) is uncategorised; a6 (no label) is a known atom in the unlabelled bucket
    assert shares["by_label"]["unlabelled"]["atoms"] == 1
    assert shares["by_label"]["unlabelled"]["known"] == 1
    assert shares["by_label"]["unlabelled"]["uncategorised"] == 1
    assert shares["all"]["atoms"] == 5 and shares["all"]["uncategorised"] == 1
    assert shares["all"]["known_share"] == 0.4 and shares["all"]["new_share"] == 0.4


def test_view_takes_atom_labels_and_needs_atom_verdicts_to_join_them(capsys):
    from dokimi.commands.novelty import cmd_novelty_view

    args = argparse.Namespace(novelty="n.json", atoms="a.json", atom_verdicts=None, atom_labels="l.csv",
                              format="html", out=None)
    with pytest.raises(InputError, match="--atom-labels needs --atom-verdicts"):
        cmd_novelty_view(args)
    parsed = build_parser().parse_args(["skill", "novelty", "view", "--novelty", "n.json", "--atoms", "a.json",
                                        "--atom-verdicts", "v.json", "--atom-labels", "l.csv"])
    assert parsed.atom_labels == "l.csv"


def test_a_line_that_is_not_json_is_refused(tmp_path):
    path = tmp_path / "labels.jsonl"
    path.write_text('{"skill": "s", "atom_id": "a1", "label": "x"}\nnot json\n', encoding="utf-8")
    with pytest.raises(InputError, match="line 2 is not JSON"):
        load_atom_labels(path)


def test_the_shares_table_has_a_row_per_label_and_the_whole_run():
    from dokimi.novelty.atom_labels import format_shares

    labels = {("skills/accel-tuning", "a1"): "setup", ("skills/accel-tuning", "a3"): "api"}
    lines = format_shares(label_shares(_rows(), REF, labels)).splitlines()
    assert lines[0].startswith("known/new shares for skill:skills/accel-tuning")
    assert [line.split()[0] for line in lines[2:]] == ["api", "setup", "unlabelled", "all"]
    setup = next(line for line in lines if line.startswith("setup"))
    assert setup.split()[-2:] == ["100.0", "0.0"]
    assert lines[1].split()[5] == "uncat."
    # a5 has no category: it is shown as uncategorised, not dropped from the table
    assert next(line for line in lines if line.startswith("all")).split()[1:6] == ["5", "2", "2", "1", "1"]


def test_labels_that_match_no_atom_are_reported():
    from dokimi.novelty.atom_labels import unmatched_note

    labels = {("skills/accel-tuning", "a1"): "setup", ("skills/accel-tuning/", "a2"): "api",
              ("skills/accel-tuning", "typo"): "api"}
    assert unmatched_note(_rows(), REF, labels).startswith(
        "2 of 3 labels match no atom of skill:skills/accel-tuning")
    assert unmatched_note(_rows(), REF, {("skills/accel-tuning", "a1"): "setup"}) is None


def test_a_csv_with_a_utf8_bom_loads(tmp_path):
    path = tmp_path / "labels.csv"
    path.write_text("\ufeffskill,atom_id,label\nskills/accel-tuning,a1,setup\n", encoding="utf-8")
    assert load_atom_labels(path) == {("skills/accel-tuning", "a1"): "setup"}


def test_a_missing_or_undecodable_labels_file_is_an_input_error(tmp_path):
    with pytest.raises(InputError, match="cannot read"):
        load_atom_labels(tmp_path / "absent.csv")
    path = tmp_path / "labels.jsonl"
    path.write_bytes(b"\xff\xfe not utf-8\n")
    with pytest.raises(InputError, match="cannot read"):
        load_atom_labels(path)


def test_view_prints_the_shares_of_the_labels_joined_to_the_run(tmp_path, skill, mixed_models, capsys):
    out_dir = _analyze(tmp_path, skill)
    sidecar = json.loads((out_dir / "novelty.atom_verdicts.json").read_text(encoding="utf-8"))
    first, second = sidecar["atoms"]
    labels = tmp_path / "labels.csv"
    labels.write_text(f"skill,atom_id,label\n{sidecar['artifact_ref']},{first['id']},setup\n"
                      f"{sidecar['artifact_ref']}/,{second['id']},api\n", encoding="utf-8")
    capsys.readouterr()

    _run("skill", "novelty", "view", "--novelty", str(out_dir / "novelty.json"), "--atoms",
         str(out_dir / "novelty.atoms.json"), "--atom-verdicts", str(out_dir / "novelty.atom_verdicts.json"),
         "--atom-labels", str(labels), "--format", "md", "--out", str(tmp_path / "v.md"))
    said = capsys.readouterr()

    rows = {line.split()[0]: line.split()[1:] for line in said.out.splitlines()[-3:]}
    assert said.out.splitlines()[-4].startswith("label ")
    assert set(rows) == {"setup", "unlabelled", "all"}  # the trailing-slash row matched nothing
    assert rows["all"][0] == "2"
    assert "1 of 2 labels match no atom of" in said.err
