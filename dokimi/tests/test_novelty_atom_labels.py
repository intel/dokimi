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
