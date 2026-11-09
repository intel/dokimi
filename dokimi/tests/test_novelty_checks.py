# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Program checks on novelty probes: the identifiers veto, roles, and judge-vs-check report."""

from __future__ import annotations

import json
import sys
import time

import pytest

from dokimi.eval.reference_checks import grade_check, validate_grading
from dokimi.novelty.atoms import Atom
from dokimi.novelty.client import LLMClient
from dokimi.novelty.grounding import (add_checks, help_identifiers, help_text, identifiers_check,
                                     verify_keys)
from dokimi.novelty.measure import resolve_verdict, run_checks, run_measurement
from dokimi.novelty.probes import build_probes
from dokimi.utils import InputError

PERF_HELP = """
 usage: perf report [<options>]
    -i, --input <file>    input file name
        --stdio           Use the stdio interface
        --sort <key[,key2...]>  sort by key(s)
        --children        Accumulate callchains of children
"""


def _veto(**extra) -> dict:
    return {**identifiers_check("perf report", PERF_HELP), **extra}


def test_help_identifiers_lists_the_long_options_the_help_lists():
    assert help_identifiers(PERF_HELP) == ["--children", "--input", "--sort", "--stdio"]


@pytest.mark.parametrize("answer, passed", [
    ("Run `perf report --stdio --sort=dso`.", True),
    ("Run `perf report --stdin`.", False),  # the invented flag of the study
    ("Use `perf record --call-graph dwarf` then read it.", True),  # another command: not checked
    ("No command line at all.", True),
    ("`perf report foo --stdin`", True),  # a positional first: not checked, conservative
    ("Do not use `perf report --stdin`; use `perf report --stdio`.", True),  # a negated mention
    ("Use `perf report --stdio` instead of `perf report --stdin`.", True),
    ("perf report --stdio # collect with perf record --call-graph dwarf", True),  # a comment
    ("Use `perf report --stdin`, not `perf report --stdio`.", False),  # a positive mention
    ("Instead of perf script, run `perf report --stdin`.", False),  # the negation is another tool's
    ("If you don't have symbols, run `perf report --stdin`.", False),  # a clause before it
])
def test_identifiers_check_vetoes_only_unlisted_flags_of_the_tool(answer, passed):
    result = grade_check(_veto(), "q", answer, None)
    assert result.passed is passed
    if not passed:
        assert "--stdin" in result.detail


def test_a_tool_check_leaves_its_subcommands_alone():
    check = identifiers_check("perf", "usage: perf [--version] [--help] COMMAND")
    assert grade_check(check, "q", "perf report --stdio", None).passed is True
    assert grade_check(check, "q", "perf --vershun", None).passed is False


def test_identifiers_check_validates():
    assert validate_grading({"checks": [_veto()]}) == []
    problems = validate_grading({"checks": [{"type": "identifiers", "known": [], "role": "boss",
                                             "tier": "vibes", "within": "("}]})
    text = "; ".join(problems)
    assert "known" in text and "role" in text and "tier" in text and "compile" in text


@pytest.mark.parametrize("judge, checks, final", [
    ("correct_complete", [{"role": "veto", "passed": False}], ("wrong_other", "check")),
    ("correct_complete", [{"role": "veto", "passed": True}], ("correct_complete", "judge")),
    ("wrong_other", [{"role": "sufficient", "passed": True}], ("correct_complete", "check")),
    ("abstained", [{"role": "sufficient", "passed": True}], ("abstained", "judge")),
    ("partially_correct", [{"role": "evidence", "passed": False}], ("partially_correct", "judge")),
])
def test_resolve_verdict_applies_roles(judge, checks, final):
    assert resolve_verdict(judge, checks) == final


def test_run_checks_skips_judge_checks_and_keeps_roles():
    grading = {"checks": [_veto(), {"type": "judge", "criterion": "x"}]}
    out = run_checks(grading, "q", "perf report --stdin")
    assert [(c["type"], c["role"], c["passed"]) for c in out] == [("identifiers", "veto", False)]


def test_probe_carries_atom_grading_except_reversal():
    atom = Atom(statement="s", question="q", answer="a", span_ids=("s1",),
                meta={"grading": {"checks": [_veto()]}})
    probes = build_probes([atom])
    assert probes[0].grading and probes[0].as_dict()["grading"]["checks"][0]["role"] == "veto"


def test_invalid_atom_grading_is_refused():
    atom = Atom(statement="s", question="q", answer="a", span_ids=("s1",), id="a1",
                meta={"grading": {"checks": [{"type": "identifiers"}]}})
    with pytest.raises(InputError, match="a1"):
        build_probes([atom])


def test_veto_overrules_judge_and_the_record_reports_it():
    atom = Atom(statement="perf report shows text with --stdio", question="How to get text?",
                answer="perf report --stdio", span_ids=("s1",),
                meta={"grading": {"checks": [_veto()]}})
    answerer = LLMClient(model="a", transport=lambda p, s: ("perf report --stdin", None))
    judge = LLMClient(model="b",
                      transport=lambda p, s: (json.dumps({"verdict": "correct_complete"}), None))
    result = run_measurement([atom], build_probes([atom]), "artifact", answerer=answerer,
                             judge=judge, concurrency=1)
    run = result.runs[0]
    assert (run.verdict, run.judge_verdict, run.verdict_source) == (
        "wrong_other", "correct_complete", "check")
    summary = result.checks_summary()
    assert summary["vetoed"] == summary["judge_passed_vetoed"] == len(result.runs)
    assert run.as_dict()["checks"][0]["detail"].startswith("not in source")


def test_runs_without_grading_serialize_as_before():
    atom = Atom(statement="s", question="q", answer="a", span_ids=("s1",))
    client = LLMClient(model="a", transport=lambda p, s: (json.dumps({"verdict": "wrong_other"}), None))
    judge = LLMClient(model="b", transport=client.transport)
    result = run_measurement([atom], build_probes([atom]), "t", answerer=client, judge=judge,
                             concurrency=1)
    assert "checks" not in result.runs[0].as_dict()
    assert result.checks_summary() is None


def test_add_checks_targets_atoms_naming_the_tool_and_replaces_its_own():
    atoms = [{"statement": "Use `perf report --stdio`.", "answer": "x"},
             {"statement": "Unrelated", "answer": "y"}]
    check = _veto()
    assert add_checks(atoms, [check], ["perf report"]) == 1
    assert add_checks(atoms, [check], ["perf report"]) == 1
    assert len(atoms[0]["meta"]["grading"]["checks"]) == 1
    assert "meta" not in atoms[1]


def test_help_text_reads_a_real_help():
    text = help_text(f'"{sys.executable}" -m pip')
    assert "--version" in help_identifiers(text)


def test_help_text_names_a_missing_tool():
    with pytest.raises(InputError, match="not installed"):
        help_text("no-such-tool-dokimi-xyz")



def test_verify_keys_flags_a_claim_naming_an_option_the_help_lacks():
    atoms = [{"statement": "Text output", "answer": "Run `perf report --stdio`."},
             {"statement": "Bad key", "answer": "Run `perf report --stdin --stdio`."},
             {"statement": "perf report reads perf.data", "answer": "perf report shows it"},
             {"statement": "Unrelated", "answer": "y"}]
    for atom in atoms[:3]:
        atom["meta"] = {"grading": {"checks": [_veto()]}}
    assert verify_keys(atoms) == {"verified": 1, "not_found": 1, "unverifiable": 1}
    assert atoms[1]["meta"]["key_status"] == {"status": "not_found", "missing": ["--stdin"]}
    assert atoms[2]["meta"]["key_status"] == {"status": "unverifiable"}
    assert "meta" not in atoms[3]


def test_identifiers_scan_is_bounded_by_the_regex_timeout(monkeypatch):
    # The scan of ``within`` and ``pattern`` runs under REGEX_TIMEOUT, like a regex check: a
    # pathological pattern is left unscored instead of stalling a measurement worker.
    from dokimi.eval import reference_checks
    monkeypatch.setattr(reference_checks, "REGEX_TIMEOUT", 0.2)
    check = {"type": "identifiers", "known": ["--x"], "within": r"(a|aa)+$"}
    started = time.monotonic()
    result = grade_check(check, "q", "a" * 34 + "!", None)
    assert result.passed is None and "not scored" in result.detail
    assert time.monotonic() - started < 5


def test_identifiers_pattern_group_that_did_not_participate_is_the_whole_match():
    # The first group is unset when the other alternative matches: the name is the full match.
    check = {"type": "identifiers", "known": ["--input", "ENV_FOO"], "pattern": r"(--[a-z]+)|ENV_[A-Z]+"}
    assert grade_check(check, "q", "ENV_FOO", None).passed is True
    assert grade_check(check, "q", "run with --stdin", None).passed is False


def test_a_negated_flag_passes_only_when_the_check_opts_in_to_negations():
    assert grade_check(_veto(), "q", "Run `perf report --no-input`.", None).passed is False
    opted = grade_check(_veto(negations=True), "q", "Run `perf report --no-input`.", None)
    assert opted.passed is True


def test_help_text_does_not_take_a_rejected_help_flag_for_a_listing(tmp_path):
    # A tool that knows only -h rejects --help by naming it on stderr: that is no help listing.
    tool = tmp_path / "honly.py"
    tool.write_text(
        "import sys\n"
        "if '-h' in sys.argv:\n"
        "    print('usage: honly [--input X]\\n  --input X'); sys.exit(0)\n"
        "print(\"unrecognized option '--help'\", file=sys.stderr); sys.exit(2)\n",
        encoding="utf-8")
    text = help_text(f'"{sys.executable}" "{tool}"')
    assert "--input" in help_identifiers(text)


def test_help_is_not_listed_unless_the_help_lists_it():
    # A parser built with add_help=False has -h only: --help is not a name the tool has.
    text = "usage: tool [-h] [--input INPUT]\n  -h\n  --input INPUT\n"
    assert help_identifiers(text) == ["--input"]
    assert grade_check(identifiers_check("tool", text), "q", "run tool --help", None).passed is False


@pytest.mark.parametrize("tool, message", [("", "empty"), ('perf "report', "does not parse")])
def test_help_text_refuses_an_empty_or_unparsable_tool(tool, message):
    with pytest.raises(InputError, match=message):
        help_text(tool)


def test_a_check_attaches_to_an_atom_that_names_the_tool_without_an_option():
    # The answer "perf report" names the tool with no option yet: the veto must still attach,
    # or a later answer's invented --stdin would go unchecked.
    atoms = [{"statement": "perf report displays recorded samples", "answer": "perf report"}]
    assert add_checks(atoms, [_veto()], ["perf report"]) == 1
    assert len(atoms[0]["meta"]["grading"]["checks"]) == 1


def _judge_fails_check_run(answer: str, checks: list[dict]):
    atom = Atom(statement="perf report shows text with --stdio", question="How to get text?",
                answer="perf report --stdio", span_ids=("s1",), meta={"grading": {"checks": checks}})
    answerer = LLMClient(model="a", transport=lambda p, s: (answer, None))
    judge = LLMClient(model="b", transport=lambda p, s: (json.dumps({"verdict": None}), None))
    return run_measurement([atom], build_probes([atom]), "artifact", answerer=answerer,
                           judge=judge, concurrency=1).runs[0]


def test_a_judge_failure_keeps_its_checks_and_a_sufficient_check_does_not_decide_it():
    sufficient = {"type": "regex", "values": ["--stdio"], "role": "sufficient"}
    run = _judge_fails_check_run("perf report --stdio", [sufficient])
    assert (run.verdict, run.verdict_source) == ("error", "judge")
    assert run.checks and run.checks[0]["passed"] is True


def test_a_failed_veto_is_wrong_even_when_the_judge_failed():
    run = _judge_fails_check_run("perf report --stdin", [_veto()])
    assert (run.verdict, run.verdict_source) == ("wrong_other", "check")


def test_a_help_that_describes_options_with_rejection_words_is_still_a_listing(tmp_path):
    # "skip unknown options" is a description, not a rejection: the listing is read as help.
    tool = tmp_path / "listing.py"
    tool.write_text(
        "print('usage: listing [--ignore-unknown] [--strict]')\n"
        "print('  --ignore-unknown  skip unknown options')\n"
        "print('  --strict          fail on invalid arguments')\n",
        encoding="utf-8")
    text = help_text(f'"{sys.executable}" "{tool}"')
    assert help_identifiers(text) == ["--ignore-unknown", "--strict"]


def test_a_negated_mention_in_the_same_sentence_does_not_hide_a_later_flag():
    # "no" inside "--no-children" is no negation word: the later invented flag is still vetoed.
    check = identifiers_check("perf report", "usage: perf report [--[no-]children]\n")
    answer = "perf report --no-children && perf report --stdin"
    assert grade_check(check, "q", answer, None).passed is False


def test_a_negation_the_help_shows_with_brackets_is_listed():
    text = "usage: perf report [--[no-]children]\n  --[no-]children  accumulate callchains\n"
    names = help_identifiers(text)
    assert {"--children", "--no-children"} <= set(names)
    check = identifiers_check("perf report", text)
    assert grade_check(check, "q", "Run `perf report --no-children`.", None).passed is True
    assert grade_check(_veto(), "q", "Run `perf report --no-children`.", None).passed is False
