# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""False-claims prototype: a stored answer graded against stored atoms, no model calls."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dokimi import caching
from dokimi.novelty.atoms import Atom
from dokimi.novelty.client import LLMClient
from dokimi.novelty.false_claims import (
    client_judge,
    find_false_claims,
    lexical_conflict,
    pair_atoms,
    split_sentences,
)

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
        ATOMS[2].statement: {"verdict": "supports", "quote": "supported on Windows"},  # verbatim
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


def test_a_camelcase_product_name_alone_does_not_pair():
    atom = Atom.from_dict({**FIXTURE["atoms"][0], "statement": "OpenVINO supports Python 3.9."})
    assert pair_atoms("OpenVINO 2024.1 is not available on ARM.", [atom]) == []
    assert pair_atoms("OpenVINO also supports Python 3.10.", [atom]) == [atom]


def test_a_negative_default_differs_from_the_positive_default():
    assert lexical_conflict("The request timeout defaults to -1.",
                            "The request timeout defaults to 1.") == ("lexical:value", "1")


def test_version_conflict_quotes_the_driver_version():
    assert lexical_conflict("The accelerator requires driver version 2.1.",
                            "Windows 11 needs driver version 3.0.") == ("lexical:value", "3.0")


def test_version_prefix_does_not_change_the_value():
    assert lexical_conflict("The accelerator requires driver 2.1.",
                            "The accelerator needs driver v2.1.") is None


def test_abbreviations_neither_split_sentences_nor_anchor_pairs():
    answer = "Use the cache, e.g. for builds. Use _build, i.e. the fallback."
    assert [sentence.text for sentence in split_sentences(answer)] == [
        "Use the cache, e.g. for builds.", "Use _build, i.e. the fallback.",
    ]
    atom = Atom.from_dict({**FIXTURE["atoms"][0], "statement": "Use other drivers, e.g. virtual drivers."})
    assert pair_atoms("Use the cache, e.g. for builds.", [atom]) == []


def test_compact_unit_conflict_quotes_the_whole_value():
    statement = "The request timeout defaults to 30ms."
    assert lexical_conflict(statement, "The request timeout defaults to 60ms.") == ("lexical:value", "60ms")
    assert lexical_conflict(statement, "The request timeout defaults to 30 ms.") is None
    assert lexical_conflict(statement, "The request timeout defaults to 60 ms.") == ("lexical:value", "60 ms")
    assert lexical_conflict("The request timeout defaults to 1000ms.", "The request timeout defaults to 1s.") is None
    assert lexical_conflict("Use product 30alpha.", "Use product 60alpha.") is None
    assert lexical_conflict("Timeout defaults to 30 ms.", "Timeout defaults to 60 milliseconds.") == (
        "lexical:value", "60 milliseconds")
    assert lexical_conflict("Timeout defaults to 1,000ms.", "Timeout defaults to 2,000ms.") == (
        "lexical:value", "2,000ms")
    assert lexical_conflict("Throughput is 30MB/s.", "Throughput is 60MB/s.") == ("lexical:value", "60MB/s")


def test_quantity_variants_share_dimensions_and_preserve_quotes():
    assert lexical_conflict("Timeout defaults to 30 seconds.", "Timeout defaults to 60 sec.") == (
        "lexical:value", "60 sec")
    assert lexical_conflict("Throughput is 30 MB/s.", "Throughput is 60 GB/s.") == ("lexical:value", "60 GB/s")
    assert lexical_conflict("Throughput is 1000 MB/s.", "Throughput is 1 GB/s.") is None
    assert lexical_conflict("Retry after 1 second with a 30 ms timeout.",
                            "Retry after 1000 ms with a 60 ms timeout.") == ("lexical:value", "60 ms")
    assert lexical_conflict("Retry after 1 second.", "60 ms and 1 second are supported retry values.") is None
    assert lexical_conflict("Size is 1,000.", "Size is 2,000.") == ("lexical:value", "2,000")


def test_case_sensitive_cli_flags_remain_distinct():
    atom = Atom.from_dict({**FIXTURE["atoms"][0], "statement": "Pass -O to enable optimization."})
    answer = "Use -o to optimize."
    report = find_false_claims(answer, [atom], judge=lambda _: {"verdict": "contradicts", "quote": "-o"})
    assert report.n_pairs == 1 and [claim.quote for claim in report.claims] == ["-o"]
    assert lexical_conflict("Pass -O to enable optimization.",
                            "Pass -o to enable optimization.") == ("lexical:value", "-o")
    assert lexical_conflict("Pass -o to enable optimization.",
                            "Pass -O to enable optimization.") == ("lexical:value", "-O")


def test_dotted_identifiers_with_digits_reach_the_judge():
    atom = Atom.from_dict({**FIXTURE["atoms"][0], "statement":
                          "torch.float32 configures the numeric precision used for accelerator computations."})
    tried = []

    def judge(prompt):
        tried.append(prompt)
        return {"verdict": "contradicts", "quote": "torch.float32 is unsupported"}

    report = find_false_claims("torch.float32 is unsupported.", [atom], judge=judge)
    assert len(tried) == report.n_pairs == 1 and report.n_unchecked == 0
    assert [claim.quote for claim in report.claims] == ["torch.float32 is unsupported"]
    version = Atom.from_dict({**FIXTURE["atoms"][0], "statement":
                              "Version 32.1.6 configures the numeric precision used for accelerator computations."})
    assert pair_atoms("Version 32.1.6 is unsupported.", [version]) == []


def test_only_a_final_standalone_numeric_confidence_line_is_metadata():
    answer = ("Confidence: the accelerator is supported on Windows.\n"
              "Restart the shell afterwards.\n  Confidence: .9e0  \n\n")
    sentences = split_sentences(answer)
    assert [sentence.text for sentence in sentences] == [
        "Confidence: the accelerator is supported on Windows.", "Restart the shell afterwards.",
    ]
    assert all(answer[sentence.start:sentence.end] == sentence.text for sentence in sentences)
    report = find_false_claims(answer, ATOMS)
    assert report.n_sentences == 2
    assert any(claim.atom_id == "a_windows" for claim in report.claims)
    assert [sentence.text for sentence in split_sentences("Restart.\rConfidence: 0.7")] == ["Restart."]
    assert [sentence.text for sentence in split_sentences("Restart.\nConfidence:\u00a00.7")] == ["Restart."]
    for text in ("Confidence: this is prohibited.", "Restart. Confidence: 0.7", "Confidence: 0.7\nRestart."):
        assert any(sentence.text.startswith("Confidence:") for sentence in split_sentences(text))


@pytest.mark.parametrize("token", ["--fp16", "api2.float32"])
def test_distinctive_tokens_pair_without_an_alphabetic_word_anchor(token):
    atom = Atom.from_dict({**FIXTURE["atoms"][0], "statement": f"Use {token}."})
    quote = f"{token} is unsupported"
    report = find_false_claims(f"{quote}.", [atom],
                               judge=lambda _: {"verdict": "contradicts", "quote": quote})
    assert report.n_pairs == 1 and report.n_unchecked == 0
    assert [claim.quote for claim in report.claims] == [quote]


@pytest.mark.parametrize(("statement", "answer"), [
    ("Use 12.", "Use 12."),
    ("Use 1_000.", "1_000 is unsupported."),
    ("Use --fp16.", "--fp16.extra is unsupported."),
    ("Use --fp16.", "---fp16 is unsupported."),
])
def test_partial_or_numeric_tokens_do_not_pair_without_a_word_anchor(statement, answer):
    atom = Atom.from_dict({**FIXTURE["atoms"][0], "statement": statement})
    assert pair_atoms(answer, [atom]) == []


@pytest.mark.parametrize(("statement", "answer"), [
    ("Use --fp16 to reduce memory consumption.", "--fp16/--bf16 is unsupported."),
    ("Use _123 to configure matrix precision.", "_123 is unsupported."),
])
def test_existing_identifier_anchors_survive_token_boundaries(statement, answer):
    atom = Atom.from_dict({**FIXTURE["atoms"][0], "statement": statement})
    assert pair_atoms(answer, [atom]) == [atom]


def test_a_lone_number_against_a_documented_version_is_a_conflict():
    assert lexical_conflict("The accelerator requires driver 2.1.",
                            "The accelerator needs driver 2.") == ("lexical:value", "2")


@pytest.mark.parametrize(("documented", "stated"), [("2.1", "2.1.0"), ("2.1.0", "2.1"), ("3", "3.0")])
def test_trailing_zero_groups_spell_the_same_version(documented, stated):
    assert lexical_conflict(f"The accelerator requires driver {documented}.",
                            f"The accelerator needs driver {stated}.") is None


def test_a_version_that_differs_beyond_trailing_zeros_still_conflicts():
    assert lexical_conflict("The accelerator requires driver 2.1.",
                            "The accelerator needs driver 2.10.") == ("lexical:value", "2.10")


def test_hyphenated_range_compares_the_second_quantity():
    assert lexical_conflict("Latency is 10-20 ms.", "Latency is 10-30 ms.") == ("lexical:value", "30 ms")
    assert lexical_conflict("Latency is -10--20 ms.", "Latency is -10--30 ms.") == ("lexical:value", "-30 ms")
    assert lexical_conflict("Use build-10 ms.", "Use build-20 ms.") is None
    assert lexical_conflict("Use build10-20 ms.", "Use build10-30 ms.") is None


def test_frequency_units_compare_by_scale():
    assert lexical_conflict("Clock is 1000 kHz.", "Clock is 1 MHz.") is None
    assert lexical_conflict("Clock is 1000 MHz.", "Clock is 1 GHz.") is None
    assert lexical_conflict("Clock is 1 MHz.", "Clock is 2 MHz.") == ("lexical:value", "2 MHz")


def test_microsecond_units_compare_by_scale():
    assert lexical_conflict("Wait 1000 µs.", "Wait 1 ms.") is None
    assert lexical_conflict("Wait 1000 μs.", "Wait 1 ms.") is None
    assert lexical_conflict("Wait 1000 us.", "Wait 1 ms.") is None


def test_different_flags_need_more_than_one_shared_word_for_a_multiword_atom():
    short = Atom.from_dict({**FIXTURE["atoms"][0], "statement": "Use --enable-accel."})
    long = Atom.from_dict({**FIXTURE["atoms"][0], "statement": "Use --enable-accel for the accelerator."})
    assert pair_atoms("Use --use-accel.", [short]) == []
    assert pair_atoms("Use --use-accel for the accelerator.", [long]) == [long]


@pytest.mark.parametrize("separator", ["\r", "\r\n", "\n"])
def test_line_breaks_keep_the_windows_and_linux_claims_separate(separator):
    statement = "The accelerator is not supported on Windows."
    atom = Atom.from_dict({**FIXTURE["atoms"][0], "statement": statement})
    first = "The accelerator is supported on Windows"
    answer = first + separator + "The accelerator is not supported on Linux"
    sentences = split_sentences(answer)
    assert len(sentences) == 2
    assert all(answer[s.start:s.end] == s.text for s in sentences)
    assert [claim.quote for claim in find_false_claims(answer, [atom]).claims] == [first]


@pytest.mark.parametrize(("opening", "closing"), [('"', '"'), ("“", "”"), ("‘", "’")])
def test_closing_quotes_keep_two_claims_separate(opening, closing):
    statement = "The accelerator is not supported on Windows."
    atom = Atom.from_dict({**FIXTURE["atoms"][0], "statement": statement})
    first = f"{opening}The accelerator is supported on Windows.{closing}"
    second = "The accelerator is not supported on Linux."
    answer = f"  {first} {second}"
    sentences = split_sentences(answer)
    assert [sentence.text for sentence in sentences] == [first, second]
    assert all(answer[sentence.start:sentence.end] == sentence.text for sentence in sentences)
    report = find_false_claims(answer, [atom])
    assert report.n_sentences == 2
    assert [claim.quote for claim in report.claims] == [first]


@pytest.mark.parametrize(("documented", "listed"), [
    ("api2.float32", "api2.float32/api2.float64"),
    ("_123", "_124/_123"),
])
def test_slash_separated_identifiers_reach_the_judge(documented, listed):
    atom = Atom.from_dict({**FIXTURE["atoms"][0], "statement": f"Use {documented}."})
    answer = f"{listed} are unsupported."
    prompts = []

    def judge(prompt):
        prompts.append(prompt)
        return {"verdict": "contradicts", "quote": "unsupported"}

    report = find_false_claims(answer, [atom], judge=judge)
    assert len(prompts) == report.n_pairs == 1 and report.n_unchecked == 0
    assert atom.statement in prompts[0] and answer in prompts[0]
    assert [(claim.atom_id, claim.how, claim.quote) for claim in report.claims] == [
        (atom.id, "judge", "unsupported"),
    ]


@pytest.mark.parametrize("marker", ["**", "__", "*"])
def test_closing_emphasis_keeps_two_claims_separate(marker):
    atom = Atom.from_dict({**FIXTURE["atoms"][0], "statement": "The accelerator is not supported on Windows."})
    first = f"{marker}The accelerator is supported on Windows.{marker}"
    answer = f"{first} The accelerator is not supported on Linux."
    assert [sentence.text for sentence in split_sentences(answer)] == [
        first, "The accelerator is not supported on Linux."]
    assert [claim.quote for claim in find_false_claims(answer, [atom]).claims] == [first]


def test_numbered_list_markers_are_not_counted_as_unchecked_sentences():
    items = ["Install driver version 2.1 for the accelerator.",
             "The accelerator requires driver version 2.1 or newer.", "Restart the accelerator driver."]
    numbered = "To set it up:\n\n" + "\n".join(f"{i}. {item}" for i, item in enumerate(items, 1))
    bulleted = "To set it up:\n\n" + "\n".join(f"- {item}" for item in items)
    reports = [find_false_claims(answer, ATOMS[:1]) for answer in (numbered, bulleted)]
    assert [(r.n_sentences, r.n_unchecked) for r in reports] == [(4, 1), (4, 1)]
    atom = Atom.from_dict({**FIXTURE["atoms"][0], "statement": "Use _123 to configure matrix precision."})
    assert find_false_claims("_123.", [atom]).n_pairs == 1


def test_an_off_scale_client_verdict_is_asked_again_not_replayed(provider_calls, monkeypatch, tmp_path):
    monkeypatch.setenv(caching.RESPONSE_CACHE_ENV, str(tmp_path / "responses"))
    provider_calls.push('{"verdict": "maybe"}', '{"verdict": "contradicts", "quote": "3.0"}')
    answer = "The accelerator requires driver version 3.0."
    runs = [find_false_claims(answer, ATOMS[:1], judge=client_judge(LLMClient(model="gpt-4o")))
            for _ in range(2)]
    assert [(r.n_judge_errors, [c.quote for c in r.claims]) for r in runs] == [(1, []), (0, ["3.0"])]
    assert len(provider_calls.calls) == 2
