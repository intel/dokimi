# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""False-claims prototype: a stored answer graded against stored atoms, no model calls."""

from __future__ import annotations

import json
from pathlib import Path

from dokimi.novelty.atoms import Atom
from dokimi.novelty.false_claims import find_false_claims, lexical_conflict, pair_atoms, split_sentences

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "false_claims" / "accel.json")
                     .read_text(encoding="utf-8"))
ATOMS = [Atom.from_dict(a) for a in FIXTURE["atoms"]]
ANSWER = FIXTURE["answer"]


def test_sentences_skip_the_confidence_line_and_keep_offsets():
    sents = split_sentences(ANSWER)
    assert [s.text for s in sents][-1] == "Restart the shell afterwards."
    assert all(ANSWER[s.start:s.end] == s.text for s in sents)
    assert len(sents) == 5


def test_lexical_pass_flags_version_flag_and_polarity_conflicts():
    report = find_false_claims(ANSWER, ATOMS)
    got = {(c.atom_id, c.how, c.sentence) for c in report.claims}
    assert got == {
        ("a_driver", "lexical:value", "To use the accelerator you need driver version 3.0."),
        ("a_flag", "lexical:value", "Turn it on with --use-accel."),
        ("a_windows", "lexical:polarity", "The accelerator is supported on Windows and Linux."),
    }  # the driver sentence shares only "accelerator" with the Windows atom: not paired
    driver = next(c for c in report.claims if c.atom_id == "a_driver")
    assert driver.quote == "3.0" and driver.span_ids == ("s0001",)
    assert driver.as_dict()["quote"] == "3.0"
    assert report.as_dict()["claims"] == [claim.as_dict() for claim in report.claims]
    assert report.as_dict()["n_pairs"] == report.n_pairs > 0


def test_a_distorted_atom_is_never_used_as_ground_truth():
    report = find_false_claims(ANSWER, ATOMS)
    assert "a_bad" not in {c.atom_id for c in report.claims}


def test_a_sentence_no_atom_is_about_is_unchecked_not_true():
    report = find_false_claims(ANSWER, ATOMS)
    assert report.n_sentences == 5
    assert report.n_unchecked >= 1  # "Restart the shell afterwards."


def _scripted(replies: dict[str, dict]):
    """A judge that answers by which atom statement the prompt carries."""
    def judge(prompt: str) -> dict:
        for statement, reply in replies.items():
            if statement in prompt:
                if isinstance(reply, Exception):
                    raise reply
                return reply
        return {"verdict": "unrelated", "quote": ""}
    return judge


def test_judge_contradictions_need_a_verbatim_quote():
    judge = _scripted({
        ATOMS[0].statement: {"verdict": "contradicts", "quote": "driver version 3.0"},
        ATOMS[1].statement: {"verdict": "contradicts", "quote": "--use-the-accel"},  # not verbatim
        ATOMS[2].statement: {"verdict": "supports", "quote": ""},
    })
    report = find_false_claims(ANSWER, ATOMS, judge=judge)
    assert [(c.atom_id, c.quote, c.how) for c in report.claims] == [
        ("a_driver", "driver version 3.0", "judge")]
    assert report.n_unverified_quote >= 1


def test_judge_failures_are_counted_not_read_as_clean():
    judge = _scripted({ATOMS[0].statement: RuntimeError("timeout"),
                       ATOMS[1].statement: {"verdict": "maybe"}})
    report = find_false_claims(ANSWER, ATOMS, judge=judge)
    assert report.n_judge_errors >= 2
    assert report.claims == []


def test_sentence_final_identifier_still_pairs():
    atom = Atom.from_dict({**FIXTURE["atoms"][0], "statement":
                          "cache_size configures the maximum number of entries stored in the accelerator cache."})
    assert pair_atoms("There is no cache_size.", [atom]) == [atom]


def test_an_exact_flag_is_enough_to_pair():
    atom = Atom.from_dict({**FIXTURE["atoms"][0], "statement": "Use --cache to enable caching."})
    assert pair_atoms("--cache is unsupported.", [atom]) == [atom]


def test_a_negative_default_differs_from_the_positive_default():
    assert lexical_conflict("The request timeout defaults to -1.",
                            "The request timeout defaults to 1.") == ("lexical:value", "1")


def test_version_conflict_quotes_the_driver_version():
    assert lexical_conflict("The accelerator requires driver version 2.1.",
                            "Windows 11 needs driver version 3.0.") == ("lexical:value", "3.0")


def test_version_prefix_does_not_change_the_value():
    assert lexical_conflict("The accelerator requires driver 2.1.",
                            "The accelerator needs driver v2.1.") is None
