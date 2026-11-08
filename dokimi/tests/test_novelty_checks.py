# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Program checks on novelty probes: the identifiers veto, roles, and judge-vs-check report."""

from __future__ import annotations

import json
import sys

import pytest

from dokimi.eval.reference_checks import grade_check, validate_grading
from dokimi.novelty.atoms import Atom
from dokimi.novelty.client import LLMClient
from dokimi.novelty.grounding import add_checks, help_identifiers, help_text, identifiers_check
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


def test_help_identifiers_lists_long_options_and_help():
    assert help_identifiers(PERF_HELP) == ["--children", "--help", "--input", "--sort", "--stdio"]


@pytest.mark.parametrize("answer, passed", [
    ("Run `perf report --stdio --sort=dso`.", True),
    ("Run `perf report --no-children`.", True),  # parser-made negation of a listed option
    ("Run `perf report --stdin`.", False),  # the invented flag of the study
    ("Use `perf record --call-graph dwarf` then read it.", True),  # another command: not checked
    ("No command line at all.", True),
    ("`perf report foo --stdin`", True),  # a positional first: not checked, conservative
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

