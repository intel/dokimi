# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The wiring of `novelty probe` and `novelty shorten` to the saved answer key: each command
parses the bundle block once at load and hands the checks an AnswerKeyRecord."""

from __future__ import annotations

import pytest

import dokimi.novelty.answer_key_check as checks
import dokimi.novelty.bundle as bundle_mod
from dokimi.cli import build_parser
from dokimi.novelty.answer_key import AnswerKeyRecord
from dokimi.novelty.atoms import save_atoms
from dokimi.novelty.bundle import load_bundle
from dokimi.novelty.spans import segment
from dokimi.files import InputError

_SKILL = """\
---
name: accel-tuning
description: Configure the accelerator
---
# Tuning

Use 64 threads.
"""


def _parse(*argv):
    return build_parser().parse_args(list(argv))


def _bundle_skill(tmp_path):
    root = tmp_path / "accel-tuning"
    (root / "references").mkdir(parents=True)
    (root / "SKILL.md").write_text(_SKILL, encoding="utf-8")
    (root / "references" / "tuning.md").write_text(
        "# Tuning\n\nNever exceed 64 threads.\n", encoding="utf-8")
    return root


def test_probe_and_shorten_hand_the_answer_key_checks_a_parsed_record(tmp_path, monkeypatch):
    """Each command parses the saved bundle block once at load and passes the AnswerKeyRecord
    to the check, so no check reads the raw dict. The messages are unchanged."""
    seen: list[type] = []
    real_check, real_entry = checks.check_answer_key, bundle_mod.entry_point_only

    def check_spy(ref, record, **kwargs):
        seen.append(type(record))
        return real_check(ref, record, **kwargs)

    def entry_spy(record, *args, **kwargs):
        seen.append(type(record))
        return real_entry(record, *args, **kwargs)

    monkeypatch.setattr(checks, "check_answer_key", check_spy)
    monkeypatch.setattr(bundle_mod, "entry_point_only", entry_spy)

    root = _bundle_skill(tmp_path)
    bundle = load_bundle(f"skill:{root}", include_resources=True)
    atoms = tmp_path / "atoms.json"
    save_atoms([], segment(bundle.text), atoms, extractor="e", cap=None, bundle=bundle.summary())
    (root / "references" / "tuning.md").write_text("Never exceed 128 threads.\n", encoding="utf-8")
    probe = _parse("skill", "novelty", "probe", "--artifact", f"skill:{root}",
                   "--atoms", str(atoms))
    with pytest.raises(InputError, match="files changed"):
        probe.func(probe)
    assert seen == [AnswerKeyRecord]

    seen.clear()
    shorten = _parse("skill", "shorten", "--artifact", f"skill:{root}", "--atoms", str(atoms),
                     "--lexical-only")
    shorten.func(shorten)
    assert seen == [AnswerKeyRecord]
