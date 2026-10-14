# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Fidelity of generated probe wordings: a mutation suite, and what the rejects cost.

Each paraphrase case is a deliberate corruption of one faithful rewording, so a check
that silently stops working fails here rather than in a run whose answer key is already
wrong. The checks are conservative by design: they reject on evidence, so "no reasons"
means no detectable corruption, not proof of fidelity.
"""

from __future__ import annotations

import json

import pytest

from dokimi.novelty.atoms import Atom
from dokimi.novelty.client import LLMClient
from dokimi.novelty.probes import (
    anchors,
    build_probe_set,
    build_probes,
    check_wording,
)

QUESTION = "Which flag enables the accelerator on driver 2.1?"
STATEMENT = "The --accel flag requires driver 2.1 or newer."


def _atom(statement: str = STATEMENT, question: str = QUESTION, answer: str = "--accel") -> Atom:
    return Atom(statement=statement, question=question, answer=answer, span_ids=("s0001",))


@pytest.mark.parametrize(
    ("candidate", "reason"),
    [
        ("Which option turns the accelerator on under driver 2.1?", None),
        (QUESTION, "identical"),
        ("Which flag enables the accelerator?", "anchor_dropped"),
        ("Which flag enables the accelerator on driver 2.1 with 4 GB of memory?",
         "anchor_added"),
        ("Which flag enables the accelerator on driver 2.2?", "anchor_dropped"),
        ("Which flag must not be used to enable the accelerator on driver 2.1?",
         "polarity_flipped"),
        ("Which flag?", "length_drift"),
        ("", "empty"),
    ],
)
def test_a_paraphrase_is_rejected_for_exactly_one_detectable_corruption(candidate, reason):
    reasons = check_wording("paraphrase", source=QUESTION, candidate=candidate, answer="x")
    if reason is None:
        assert reasons == []
    else:
        assert reason in reasons


def test_a_paraphrase_that_states_the_answer_is_not_a_probe():
    leaked = "Is `--accel` the flag that enables the accelerator on driver 2.1?"
    assert "answer_leak" in check_wording(
        "paraphrase", source=QUESTION, candidate=leaked, answer="`--accel`"
    )
    # The same wording is fine when the answer was already in the question being reworded.
    assert "answer_leak" not in check_wording(
        "paraphrase", source="Does `--accel` enable the accelerator on driver 2.1?",
        candidate=leaked, answer="`--accel`",
    )


@pytest.mark.parametrize(
    ("candidate", "reason"),
    [
        ("Isn't it true that --accel works on driver 2.1 and older?", None),
        ("Is it not the case that --accel needs no driver newer than 2.1?", None),
        ("The --accel flag requires driver 2.1 or newer.", "not_a_question"),
        ("Does the --accel flag require driver 2.1 or newer?", "restates_source"),
        ("Isn't it true that --accel requires driver 2.2 or newer?", "anchor_dropped"),
    ],
)
def test_a_reversal_must_actually_reverse_and_keep_its_anchors(candidate, reason):
    reasons = check_wording("reversal", source=STATEMENT, candidate=candidate)
    if reason is None:
        assert reasons == []
    else:
        assert reason in reasons


def test_anchors_are_the_tokens_an_atom_is_about():
    found = anchors("Set `MKL_NUM_THREADS` before oneDAL 2026.1 with --threads 8")
    assert {"mkl_num_threads", "2026.1", "--threads", "8"} <= found


def test_sentence_punctuation_and_unit_spacing_do_not_change_anchors():
    # Atom statements end in a period; the question form of the same number ends in "?".
    assert "8" in anchors("The default pool size is 8.")
    assert "2.1" in anchors("It requires driver 2.1.") and "2" not in anchors("driver 2.1.")
    assert check_wording(
        "reversal", source="The default pool size is 8.",
        candidate="Isn't it true that the default pool size is not 8?",
    ) == []
    assert check_wording(
        "paraphrase", source="Does it need 4GB of memory?",
        candidate="Is 4 GB of memory required?", answer="yes",
    ) == []


def test_echoed_list_numbering_is_not_an_added_anchor():
    def transport(prompt: str, system: str | None):
        return json.dumps(["1. Which option turns the accelerator on under driver 2.1?"]), None

    client = LLMClient(model="extractor-m", role="extractor", transport=transport)
    probe_set = build_probe_set([_atom()], client=client, paraphrases=1, reversals=False)
    assert probe_set.fidelity["paraphrase"]["rejected"] == 0


def test_rejected_wordings_are_dropped_and_counted_not_probed():
    atoms = [_atom(), _atom(statement="The cache is off by default.",
                           question="Is the cache on by default?", answer="No.")]

    def transport(prompt: str, system: str | None):
        if "Reword each question" in prompt:
            # The first rewording drops the version bound; the second is faithful.
            return json.dumps([
                "Which flag enables the accelerator?",
                "By default, is the cache enabled?",
            ]), None
        if "Rewrite each statement" in prompt:
            # Neither reverses: one restates, one is not even a question.
            return json.dumps([
                "Does the --accel flag require driver 2.1 or newer?",
                "The cache is off by default.",
            ]), None
        return "[]", None

    client = LLMClient(model="extractor-m", role="extractor", transport=transport)
    probe_set = build_probe_set(atoms, client=client, paraphrases=1, reversals=True)

    kinds = [p.kind for p in probe_set.probes]
    assert kinds.count("direct") == 2
    assert kinds.count("paraphrase") == 1          # the anchor-dropping one is gone
    assert kinds.count("reversal") == 0            # neither candidate reversed anything
    paraphrase = probe_set.fidelity["paraphrase"]
    assert (paraphrase["generated"], paraphrase["kept"], paraphrase["rejected"]) == (2, 1, 1)
    assert paraphrase["reasons"]["anchor_dropped"] == 1
    assert probe_set.fidelity["reversal"]["rejected"] == 2
    assert probe_set.malformed_rate("reversal") == 1.0
    assert 0.5 < probe_set.malformed_rate() < 1.0

    summary = probe_set.as_dict()
    assert summary["n_probes"] == 3
    assert summary["by_kind"]["paraphrase"]["malformed_rate"] == 0.5


def test_without_a_generator_there_is_nothing_to_check():
    probe_set = build_probe_set([_atom()], client=None)
    assert [p.kind for p in probe_set.probes] == ["direct"]
    assert probe_set.fidelity == {}
    assert probe_set.malformed_rate() == 0.0
    assert build_probes([_atom()], client=None) == probe_set.probes


def test_markup_is_not_a_fact_so_backticks_do_not_change_the_anchor_set():
    assert anchors("Run `make -j8` now") == anchors("Run make -j8 now") == {"-j8"}
    # A paraphrase that only loses the author's backticks is not a changed question.
    assert check_wording(
        "paraphrase", source="Read `references/tuning.md` for the table.",
        candidate="Where is the table described in references/tuning.md?",
    ) == []


def test_a_shouted_english_word_is_not_an_env_var():
    assert anchors("Is it NOT the case that this works?") == set()
    assert "mkl_num_threads" in anchors("Set `MKL_NUM_THREADS` first")
    # The reversal wording a generator most naturally produces must survive.
    assert check_wording(
        "reversal", source="The --accel flag requires driver 2.1 or newer.",
        candidate="Is it NOT the case that --accel requires driver 2.1 or newer?",
    ) == []


def test_an_identifier_or_file_name_anchors_an_atom_like_a_number_does():
    assert check_wording(
        "paraphrase", source="Call `tbb_init_pool` before the first task.",
        candidate="What must be called before the first task?",
    ) == ["anchor_dropped"]


@pytest.mark.parametrize(
    ("source", "candidate"),
    [
        ("Is bf16 the default dtype on Battlemage?", "Is fp16 the default dtype on Battlemage?"),
        ("How much VRAM does the Arc Pro B60 have?", "How much VRAM does the Arc Pro B70 have?"),
        ("Does the kernel finish within 100us?", "Does the kernel finish within 200us?"),
        ("Does `dpnp.asnumpy` copy to the host?", "Does `dpnp.asarray` copy to the host?"),
        ("Does a 7B model fit in 16 GB?", "Does a 70B model fit in 16 GB?"),
        ("Is v1 of the API still served?", "Is v2 of the API still served?"),
    ],
)
def test_a_letter_and_digit_token_or_a_dotted_name_anchors_an_atom(source, candidate):
    # bf16 -> fp16 changes the fact as surely as 2.1 -> 2.2 does; accepted, the probe would
    # ask about fp16 while `expected` holds the bf16 answer.
    reasons = check_wording("paraphrase", source=source, candidate=candidate)
    assert {"anchor_dropped", "anchor_added"} <= set(reasons)


def test_the_new_anchor_classes_leave_flags_versions_urls_and_abbreviations_alone():
    assert anchors("See e.g. https://github.com/intel/dpnp, then run make -j8 on v2.1") == {
        "-j8", "2.1",
    }
    assert anchors("`np.fft.fft` on a B60") == anchors("np.fft.fft on a B60") == {
        "np.fft.fft", "b60",
    }


def test_an_identifier_anchor_splits_at_its_first_underscore():
    # One way to split `a_b_c` (CodeQL py/redos on the old `(?:_\w+)+`); the matches are
    # unchanged, doubled underscores included, and a long `0_` run is not a slow path.
    assert anchors("Set tbb__pool_size and KMP_x before x_ runs.") == {
        "tbb__pool_size", "kmp_x",
    }
    assert len(anchors("a" + "0_" * 20000 + "!")) == 1


def test_the_reversal_prompt_asks_for_what_the_check_requires():
    from dokimi.novelty.probes import _REVERSAL_PROMPT

    # The check rejects a reversal that moves a value, so the prompt has to say so, or the
    # generator is penalised for following its instructions on exactly the value atoms.
    assert "invert the claim, not" in _REVERSAL_PROMPT
    assert "2.0" in _REVERSAL_PROMPT          # the counter-example is spelled out


def test_a_generator_that_produced_nothing_is_not_a_perfect_run():
    atoms = [_atom(), _atom(statement="s2", question="q2 about 8 threads?", answer="a2")]

    def broken(prompt: str, system: str | None):
        if "Reword each question" in prompt:
            raise RuntimeError("provider refused")
        if "Rewrite each statement" in prompt:
            return json.dumps(["Is it safe to ignore any of this: anything at all?"]), None
        return "[]", None

    client = LLMClient(model="m", role="extractor", transport=broken)
    probe_set = build_probe_set(atoms, client=client, paraphrases=1, reversals=True)

    # Both atoms were asked for a paraphrase and neither got one: that is a 100% malformed
    # rate for the kind, not an empty tally that reads like "no generator ran".
    paraphrase = probe_set.fidelity["paraphrase"]
    assert (paraphrase["generated"], paraphrase["kept"]) == (2, 0)
    assert paraphrase["reasons"]["empty"] == 2
    assert probe_set.malformed_rate("paraphrase") == 1.0
    # The reversal array was truncated to one row for two atoms: the missing row counts.
    reversal = probe_set.fidelity["reversal"]
    assert reversal["generated"] == 2 and reversal["rejected"] >= 1


def test_a_non_string_row_is_a_rejected_wording_not_a_silently_skipped_atom():
    atoms = [_atom()]

    def structured(prompt: str, system: str | None):
        if "Reword each question" in prompt:
            return json.dumps([{"question": "Which flag turns the accelerator on?"}]), None
        return "[]", None

    client = LLMClient(model="m", role="extractor", transport=structured)
    probe_set = build_probe_set(atoms, client=client, paraphrases=1, reversals=False)
    assert probe_set.fidelity["paraphrase"] == {
        "generated": 1, "kept": 0, "rejected": 1, "reasons": {"empty": 1},
    }


def test_reversal_that_removes_a_negation_is_kept():
    # Inverting a statement that says "not" removes the negation, so the candidate's words
    # are the source's. That is the inversion, not a restatement.
    assert check_wording(
        "reversal", source="X does not support Y.", candidate="Is it true that X supports Y?",
    ) == []
    # Moving the negation onto another of the source's words inverts it too.
    assert check_wording(
        "reversal",
        source="Shard keys are computed with BLAKE3 over the sorted list of input file "
               "paths, not their contents.",
        candidate="Isn't it true that shard keys are computed over the contents of input "
                  "file paths, NOT with BLAKE3 over the sorted list?",
    ) == []


@pytest.mark.parametrize(
    ("source", "candidate"),
    [
        ("Exceptions propagate out of the algorithm, not out of the body.",
         "Isn't it true that exceptions propagate out of the algorithm rather than out of "
         "the body?"),
        ("Shard keys are computed over the paths, not their contents.",
         "Isn't it true that shard keys are computed over the paths instead of their "
         "contents?"),
        ("parallel_reduce is not deterministic in floating point.",
         "Isn't it true that parallel_reduce is non-deterministic in floating point?"),
        ("parallel_reduce is not deterministic in floating point.",
         "Isn't it true that parallel_reduce is nondeterministic in floating point?"),
    ],
)
def test_a_negation_written_as_its_synonym_is_still_the_same_negation(source, candidate):
    # "rather than" for ", not" removes nothing: the premise is still true, so the
    # candidate is a restatement and must not ship with `expect_refusal`.
    assert check_wording("reversal", source=source, candidate=candidate) != []


def test_negation_synonyms_do_not_hide_a_genuine_swap_or_a_faithful_paraphrase():
    assert check_wording(
        "reversal", source="Shard keys are computed over the paths, not their contents.",
        candidate="Isn't it true that shard keys are computed over their contents instead "
                  "of the paths?",
    ) == []
    assert check_wording(
        "reversal", source="The pool uses nonzero timeouts.",
        candidate="Isn't it true that the pool uses zero timeouts?",
    ) == []
    assert check_wording(
        "paraphrase", source="Is parallel_reduce not deterministic in floating point?",
        candidate="Is parallel_reduce nondeterministic in floating point?",
    ) == []


def test_a_negative_question_that_restates_the_source_is_still_rejected():
    # "Doesn't X support Y?" asserts X supports Y: the opening "Doesn't" is question
    # phrasing, not a negation of the claim.
    assert "restates_source" in check_wording(
        "reversal", source="X supports Y.", candidate="Doesn't X support Y?",
    )
    assert "restates_source" in check_wording(
        "reversal", source="X does not support Y.",
        candidate="Isn't it true that X does not support Y?",
    )
    # A negation that stays put on a reworded verb is not evidence of inversion.
    assert "polarity_not_flipped" in check_wording(
        "reversal", source="X does not support Y.",
        candidate="Is it true that X does not handle Y?",
    )


@pytest.mark.parametrize(
    "candidate",
    [
        "Is it true that the --accel flag still requires driver 2.1 or newer?",
        "Is it true that the --accel flag also requires driver 2.1 or newer?",
        "Is it true that the --accel flag even requires driver 2.1 or newer?",
        "Is it true that the --accel flag works only with driver 2.1 or newer?",
        "Is it true that driver 2.1 or newer is supported by the --accel flag?",
        "Is it true that the --accel flag requires driver 2.1 or newer instead?",
    ],
)
def test_a_filler_word_does_not_turn_a_restated_truth_into_a_reversal(candidate):
    # One added word defeats the scaffolding test, so the marker list must not contain
    # words that a restatement adds as readily as an inversion does. Each of these asserts
    # the TRUE premise and would ship with `expect_refusal`.
    assert check_wording("reversal", source=STATEMENT, candidate=candidate) != []


def test_reversal_length_cap_allows_a_leading_frame():
    source = "zorblat stores its config in ~/.config/zorblat/zorblat.toml."
    candidate = ("Isn't it true that zorblat does NOT store its config in "
                 "~/.config/zorblat/zorblat.toml, but rather in a different location?")
    assert check_wording("reversal", source=source, candidate=candidate) == []
    # The frame is bounded: a reversal that runs on is still length drift.
    rambling = candidate[:-1] + " " + " ".join(["and more"] * 12) + "?"
    assert "length_drift" in check_wording("reversal", source=source, candidate=rambling)
