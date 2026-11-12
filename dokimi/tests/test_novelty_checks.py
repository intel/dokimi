# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Program checks on novelty probes: the identifiers veto, roles, and judge-vs-check report."""

from __future__ import annotations

import json
import sys
import time

import pytest

from dokimi.eval.reference_checks import grade_check, validate_grading
from dokimi.metrics.usage import UsageRecord
from dokimi.novelty.atoms import Atom
from dokimi.novelty.client import LLMClient, SpendCap
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


def test_an_identifiers_check_is_never_sufficient():
    # It only rules names out: a sufficient one would make an answer that names nothing correct.
    problems = validate_grading({"checks": [_veto(role="sufficient")]})
    assert any("sufficient" in p for p in problems)
    assert validate_grading({"checks": [_veto(role="veto")]}) == []


def test_an_identifiers_pattern_or_within_is_checked_with_the_engine_that_runs_it():
    # The check runs its patterns with the regex module, so a pattern it accepts must validate.
    assert validate_grading({"checks": [_veto(within=r"\p{L}+")]}) == []
    assert validate_grading({"checks": [_veto(pattern=r"(?<=a|bc)x")]}) == []
    assert validate_grading({"checks": [_veto(pattern="(--[a-z]+")]}) != []


def test_a_standalone_double_dash_ends_the_options_of_a_command():
    # After "--" the words are operands: "--foo" there is a file name, not an option of the tool.
    assert grade_check(_veto(), "q", "perf report --stdio -- --foo", None).passed is True
    assert grade_check(_veto(), "q", "perf report --stdin -- --foo", None).passed is False
    # A dot in a path is not the end of the clause: the operands run on to the sentence end.
    assert grade_check(_veto(), "q", "perf report --stdio -- ./perf.data --foo", None).passed is True


def test_a_comma_ends_the_operands_of_a_prose_double_dash():
    # In prose " -- " is a dash: the operands run to the comma, so a later flag is still checked.
    assert grade_check(_veto(), "q", "Use perf report --stdio -- it, and --bogus works too", None).passed is False


def test_a_comma_inside_a_word_after_a_standalone_double_dash_is_an_operand():
    # "0,1" is one word of the wrapped command: the operands run on past it to the flag after.
    assert grade_check(_veto(), "q", "perf report --stdio -- taskset -c 0,1 ./prog --foo", None).passed is True


@pytest.mark.parametrize("answer", [
    "Run perf report --stdio. Try --bogus elsewhere.",  # the sentence end
    "perf report --stdio | grep --bogus",  # a pipe
    "`perf report --stdio` and `ls --bogus`",  # a backtick
    "perf report --stdio # then --bogus",  # a comment
])
def test_each_stop_of_the_within_scope_keeps_a_later_flag_of_another_clause_out_of_the_check(answer):
    assert grade_check(_veto(), "q", answer, None).passed is True


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


def test_verify_keys_does_not_verify_a_key_whose_only_option_is_denied():
    # The key denies the flag it names: the veto passes it, and the denied flag is no name the key checks.
    atoms = [{"statement": "No such flag", "answer": "`perf report --stdin` does not exist.",
              "meta": {"grading": {"checks": [_veto()]}}}]
    assert verify_keys(atoms) == {"verified": 0, "not_found": 0, "unverifiable": 1}
    assert atoms[0]["meta"]["key_status"] == {"status": "unverifiable"}


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


def test_a_judge_reply_the_cap_refused_after_it_came_back_still_gets_the_checks():
    # The judge's reply came back under the cap, so its verdict is kept; the veto must still run.
    atom = Atom(statement="perf report shows text with --stdio", question="How to get text?",
                answer="perf report --stdio", span_ids=("s1",),
                meta={"grading": {"checks": [_veto()]}})
    answerer = LLMClient(model="a", transport=lambda p, s: ("perf report --stdin", None))
    judge = LLMClient(model="b", cap=SpendCap(limit_usd=1.0), transport=lambda p, s:
                      (json.dumps({"verdict": "correct_complete"}), UsageRecord()))
    result = run_measurement([atom], build_probes([atom]), "artifact", answerer=answerer,
                             judge=judge, arms=("full",), concurrency=1)
    run = result.runs[0]
    assert result.stop[0] == "no_price"
    assert (run.verdict, run.verdict_source) == ("wrong_other", "check")
    assert run.checks and run.checks[0]["passed"] is False


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


def test_an_affirmative_not_only_or_no_problem_does_not_hide_an_invented_flag():
    # "not only" and "no problem" affirm the sentence: the invented --stdin is still a veto.
    check = _veto()
    assert grade_check(check, "q", "Not only does perf report --stdin show output.", None).passed is False
    assert grade_check(check, "q", "No problem: perf report --stdin works.", None).passed is False
    assert grade_check(check, "q", "Not perf report --stdin but --stdio.", None).passed is True
    # A negation before a colon still negates what follows it.
    assert grade_check(check, "q", "There is no such option: perf report --stdin", None).passed is True
    assert grade_check(check, "q", "Never run this: perf report --stdin", None).passed is True


def test_an_existence_denial_right_after_a_flag_is_no_claim_of_it():
    # "does not exist" and "is not an option" right after the flag deny it: that answer is correct.
    check = _veto()
    assert grade_check(check, "q", "perf report --stdin does not exist.", None).passed is True
    assert grade_check(check, "q", "perf report --stdin is not an option.", None).passed is True
    # The usual markdown form: the flag is in a code span, the denial is after it.
    assert grade_check(check, "q", "`perf report --stdin` does not exist.", None).passed is True
    # Other words after the flag leave its claim standing, and so does a second invented flag.
    assert grade_check(check, "q", "perf report --stdin is deprecated.", None).passed is False
    assert grade_check(check, "q", "perf report --stdin does not work.", None).passed is False
    assert grade_check(check, "q", "perf report --stdin does not exist, but perf report --stdin2 works.",
                       None).passed is False


def test_a_contracted_denial_or_a_curly_apostrophe_is_the_same_denial_and_negation():
    # Models write "doesn't" and the curly apostrophe; both deny the flag and negate the mention.
    check = _veto()
    assert grade_check(check, "q", "perf report --stdin doesn't exist.", None).passed is True
    assert grade_check(check, "q", "perf report --stdin isn’t an option.", None).passed is True
    assert grade_check(check, "q", "perf report --stdio (don’t use --stdin)", None).passed is True
    assert grade_check(check, "q", "perf report --stdio (don't use --stdin)", None).passed is True


def test_help_text_accepts_a_help_whose_only_options_are_bracketed_negations(tmp_path):
    tool = tmp_path / "bracketed.py"
    tool.write_text(
        "print('usage: bracketed [--[no-]children]')\n"
        "print('  --[no-]children  accumulate callchains of children')\n",
        encoding="utf-8")
    text = help_text(f'"{sys.executable}" "{tool}"')
    assert {"--children", "--no-children"} <= set(help_identifiers(text))


def test_a_correct_answer_with_two_commands_on_one_line_is_not_judged_against_the_other():
    # The second command's flag belongs to "perf record", not to "perf report".
    check = _veto()
    answer = "Run perf report --stdio after collecting with perf record --call-graph dwarf."
    assert grade_check(check, "q", answer, None).passed is True
    assert grade_check(check, "q", answer.replace("--stdio", "--stdin"), None).passed is False


def test_a_negated_mention_later_in_a_retained_scope_is_no_claim():
    # "not --stdin" negates its own mention, though the command's scope runs on past the comma.
    assert grade_check(_veto(), "q", "Run perf report --stdio, not --stdin.", None).passed is True
    # A negation phrase right after a flag still negates its mention; a flag's value does not.
    assert grade_check(_veto(), "q", "Run perf report --stdio rather than --stdin.", None).passed is True
    assert grade_check(_veto(), "q", "Run perf report --stdio not --stdin.", None).passed is True
    assert grade_check(_veto(), "q", "Run perf report --stdio, not --stdin, with --stdin.", None).passed is False


def test_a_clause_word_or_a_contraction_after_a_flag_keeps_its_clause():
    # "then" starts a clause that affirms the flag after it: that flag is judged.
    assert grade_check(_veto(), "q", "Run perf report --stdio, not --tui then --stdin.", None).passed is False
    # "doesn't" after a flag still negates the mention that follows it.
    assert grade_check(_veto(), "q", "Run perf report --stdio doesn't take --stdin.", None).passed is True
    assert grade_check(_veto(), "q", "Run perf report --stdio doesn’t take --stdin.", None).passed is True
    # A comma ends an assigned value: the flag after it is no value of --input, so "not" does not reach it.
    answer = "perf report --stdio not --input=a.data, --stdin"
    assert "--stdin" in grade_check(_veto(), "q", answer, None).detail
    # A flag is no value of the flag before it: the "no" after --sort is --sort's value, not a negation.
    assert grade_check(_veto(), "q", "Run perf report --stdio --sort no --stdin.", None).passed is False
    assert grade_check(_veto(), "q", "perf report --input=perf.data --sort no --stdin", None).passed is False


def test_an_assigned_value_is_no_option_of_its_own():
    # The value of --input is a file name that happens to look like an option.
    assert grade_check(_veto(), "q", "perf report --input=--stdin", None).passed is True
    assert grade_check(_veto(), "q", "perf report --input=--stdin --stdin", None).passed is False
    # A custom pattern reads the text as written: its group may take the value itself.
    custom = {"type": "identifiers", "known": ["comm", "dso"], "pattern": r"--sort=(\w+)"}
    assert grade_check(custom, "q", "perf report --sort=bogus", None).passed is False


def test_help_identifiers_reads_only_the_options_the_help_declares():
    # A description that names another command's option declares nothing about this tool.
    text = ("usage: tool [--input FILE]\n  --input FILE  Input file\n"
            "For advanced processing, run other --advanced.\n")
    assert help_identifiers(text) == ["--input"]
    assert grade_check(identifiers_check("tool", text), "q", "run tool --advanced", None).passed is False
    # A usage line keeps its spacing (docker writes "Usage:  tool"), and a value is no negation word.
    docker = "Usage:  tool [--bar]\n\nOptions:\n  --foo  Set foo\n"
    assert help_identifiers(docker) == ["--bar", "--foo"]
    # wget pads the column after a short name with two spaces: the long name is still declared.
    wget = "Usage: wget [OPTION]... [URL]...\n\n  -V,  --version           display the version\n"
    assert help_identifiers(wget) == ["--version"]
    assert help_identifiers("Usage: tool\n  -V,    --version  display\n") == ["--version"]
    # A short name with no comma before the long one: the gap before "--verbose" is no split.
    assert help_identifiers("Usage: tool\n  -v  --verbose   be loud\n  -q   --quiet  be quiet\n") == [
        "--quiet", "--verbose"]
    # A tab separates the description from the names, as it does in some tools' help.
    assert help_identifiers("Usage: tool\n  -a,\t--all\tall files\n") == ["--all"]
    # Two spaces after a declared option with a value end its declaration, even when the
    # description starts with a long option of another command: that option is no option here.
    after_value = "usage: tool [--input FILE]\n  --input FILE  --advanced belongs to another command.\n"
    assert help_identifiers(after_value) == ["--input"]
    assert grade_check(identifiers_check("tool", after_value), "q", "run tool --advanced", None).passed is False
    # A short-only line: the gap after "-d" starts its description, and "-1  --one-line" joins its names.
    assert help_identifiers("usage: cp\n  -d                           same as --no-dereference --preserve=links\n") == []
    assert help_identifiers("usage: tool\n  -1  --one-line  print\n") == ["--one-line"]
    assert help_identifiers("usage: tool\n  -n  -1 disables the limit, see --other\n") == []
    assert grade_check(_veto(), "q", "Run perf report --sort no --stdin", None).passed is False


def test_verify_keys_does_not_verify_a_key_while_a_check_could_not_finish(monkeypatch):
    from dokimi.eval import reference_checks
    from dokimi.novelty import grounding
    real = grounding.grade_check

    def stalled_for_one(check, question, answer, ask):
        if check.get("within") == "stalled":
            return reference_checks.CheckResult("identifiers", None, None, detail="not scored")
        return real(check, question, answer, ask)

    monkeypatch.setattr(grounding, "grade_check", stalled_for_one)
    atoms = [{"statement": "x", "answer": "Run perf report --stdio.",
              "meta": {"grading": {"checks": [_veto(), dict(_veto(), within="stalled")]}}}]
    assert verify_keys(atoms) == {"verified": 0, "not_found": 0, "unverifiable": 1}
    assert atoms[0]["meta"]["key_status"] == {"status": "unverifiable"}


def test_novelty_ground_creates_the_output_directory(tmp_path, monkeypatch):
    import argparse

    from dokimi.commands import novelty_ground
    from dokimi.novelty import grounding
    from dokimi.novelty.atoms import Atom, Span, load_atoms_record, save_atoms
    atoms_path = tmp_path / "atoms.json"
    span = Span(id="s1", kind="prose", text="perf report shows samples", start=0, end=25)
    save_atoms([Atom(statement="perf report shows samples", question="How?",
                     answer="perf report --stdio", span_ids=("s1",))], [span], atoms_path)
    monkeypatch.setattr(grounding, "help_text", lambda tool, **k: PERF_HELP)
    out = tmp_path / "new-dir" / "atoms.json"
    args = argparse.Namespace(atoms=str(atoms_path), out=str(out), tool=["perf report"],
                              tool_version=None)
    novelty_ground.cmd_novelty_ground(args)
    _, _, data = load_atoms_record(out)
    assert data["atoms"][0]["meta"]["grading"]["checks"][0]["type"] == "identifiers"


def test_verify_keys_counts_a_key_only_once_a_checked_option_passed(monkeypatch):
    from dokimi.eval import reference_checks
    from dokimi.novelty import grounding
    # A key that names no long option is unverifiable, even though the command line matches.
    atoms = [{"statement": "Read recorded samples", "answer": "perf report -i data",
              "meta": {"grading": {"checks": [_veto()]}}}]
    assert verify_keys(atoms) == {"verified": 0, "not_found": 0, "unverifiable": 1}
    # A check that could not be scored (a timeout) verifies nothing.
    atoms = [{"statement": "x", "answer": "Run perf report --stdio.",
              "meta": {"grading": {"checks": [_veto()]}}}]
    monkeypatch.setattr(grounding, "grade_check",
                        lambda *a, **k: reference_checks.CheckResult("identifiers", None, None,
                                                                     detail="not scored"))
    assert verify_keys(atoms) == {"verified": 0, "not_found": 0, "unverifiable": 1}


def test_novelty_ground_keeps_the_atoms_file_when_the_write_fails(tmp_path, monkeypatch):
    import argparse
    from pathlib import Path

    from dokimi.commands import novelty_ground
    from dokimi.novelty import grounding
    from dokimi.novelty.atoms import Atom, Span, save_atoms
    atoms_path = tmp_path / "atoms.json"
    span = Span(id="s1", kind="prose", text="perf report shows samples", start=0, end=25)
    save_atoms([Atom(statement="perf report shows samples", question="How?",
                     answer="perf report --stdio", span_ids=("s1",))], [span], atoms_path)
    before = atoms_path.read_bytes()
    monkeypatch.setattr(grounding, "help_text", lambda tool, **k: PERF_HELP)
    real_write = Path.write_text

    def failing_write(self, data, *args, **kwargs):
        real_write(self, data[: len(data) // 2], *args, **kwargs)  # the disk fills mid-write
        raise OSError("No space left on device")

    monkeypatch.setattr(Path, "write_text", failing_write)
    args = argparse.Namespace(atoms=str(atoms_path), out=None, tool=["perf report"],
                              tool_version=None)
    # A failed write is a usage error with a one-line message, not a traceback.
    with pytest.raises(InputError, match="cannot write"):
        novelty_ground.cmd_novelty_ground(args)
    assert atoms_path.read_bytes() == before


def test_verify_keys_reports_every_unknown_option_not_only_the_first_ten():
    names = [f"--x{i}" for i in range(12)]
    atoms = [{"statement": "x", "answer": "Run perf report " + " ".join(names),
              "meta": {"grading": {"checks": [_veto()]}}}]
    assert verify_keys(atoms) == {"verified": 0, "not_found": 1, "unverifiable": 0}
    assert atoms[0]["meta"]["key_status"]["missing"] == names


@pytest.mark.parametrize("judge, checks, final", [
    ("correct_complete", [{"role": "sufficient", "passed": False}], ("wrong_other", "check")),
    ("correct_complete", [{"role": "sufficient", "passed": True},
                          {"role": "sufficient", "passed": False}], ("wrong_other", "check")),
    ("wrong_other", [{"role": "sufficient", "passed": True}], ("correct_complete", "check")),
    ("abstained", [{"role": "sufficient", "passed": False}], ("abstained", "judge")),
    ("correct_complete", [{"role": "sufficient", "passed": None}], ("correct_complete", "judge")),
    ("correct_complete", [{"role": "veto", "passed": None}], ("correct_complete", "judge")),
    ("correct_complete", [{"role": "veto", "passed": None},
                          {"role": "sufficient", "passed": True}], ("correct_complete", "check")),
])
def test_resolve_verdict_judges_each_sufficient_and_undecided_check(judge, checks, final):
    assert resolve_verdict(judge, checks) == final


def test_a_failed_answer_runs_no_checks():
    # A provider failure is a missing datum: its "<error: ...>" placeholder is not graded.
    from dokimi.novelty.atoms import Atom
    from dokimi.novelty.client import LLMClient
    from dokimi.novelty.probes import build_probes
    from dokimi.novelty.measure import run_measurement

    def down(prompt, system):
        raise RuntimeError("provider down")

    atom = Atom(statement="perf report shows text with --stdio", question="How to get text?",
                answer="perf report --stdio", span_ids=("s1",),
                meta={"grading": {"checks": [_veto()]}})
    answerer = LLMClient(model="a", transport=down)
    judge = LLMClient(model="b", transport=lambda p, s: (json.dumps({"verdict": None}), None))
    run = run_measurement([atom], build_probes([atom]), "artifact", answerer=answerer,
                          judge=judge, concurrency=1).runs[0]
    assert not run.checks


def test_a_path_or_quoted_value_after_a_flag_names_no_option():
    # "no" inside a file name is no negation: the --stdin after the path is still checked.
    assert grade_check(_veto(), "q", "perf report --input /tmp/no/data --stdin", None).passed is False
    assert grade_check(_veto(), "q", "perf report --input \"my no.data\" --stdin", None).passed is False
    # A quoted value that holds a flag name is the file's name: the flag inside it is no option.
    assert grade_check(_veto(), "q", 'Run perf report --input "no --stdin"', None).passed is True
    assert grade_check(_veto(), "q", "Run perf report --input 'no --stdin'", None).passed is True
    # An apostrophe that closes no quote is no value: the option after it is still checked.
    assert grade_check(_veto(), "q", "Run perf report --stdio 'cause --bogus 'x'", None).passed is False
    # A quoted value that is an option is no file name: the option it names is checked.
    assert grade_check(_veto(), "q", 'Run perf report --sort "--bogus"', None).passed is False


def test_an_option_inside_a_quoted_flag_value_is_a_file_name_not_an_option():
    assert grade_check(_veto(), "q", "perf report --input \"no --stdin\"", None).passed is True
    assert grade_check(_veto(), "q", "perf report --input 'no --stdin'", None).passed is True
