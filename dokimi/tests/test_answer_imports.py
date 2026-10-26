# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for the answer-only format importers (``dokimi.task_formats.imports``) and
``questions import``.

Fixtures are hand-written from the upstream shapes the importers cite:

- Inspect AI: ``Sample`` rows (``input``/``target``/``choices``/``id``), ``@task`` files with
  ``json_dataset(..., FieldSpec(...))``, ``multiple_choice()``, ``choice()``/``match()``/
  ``model_graded_qa()`` (inspect-ai 0.3.283).
- OpenAI evals: ``registry/data/born_first`` rows (chat ``input``, ``ideal``) and registry YAMLs
  naming ``basic.match:Match``, ``basic.includes:Includes``, ``modelgraded.classify`` with ``fact``.
- skill-creator: ``references/schemas.md`` ``evals.json``.
- promptfoo: ``examples/compare-openai-models/promptfooconfig.yaml`` (``icontains``,
  ``icontains-any``, ``llm-rubric``, ``cost``) plus ``equals``/``regex``/``is-json``.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

from dokimi.cli import build_parser
from dokimi.eval.reference_checks import grade
from dokimi.questions.artifact import load_questions
from dokimi.task_formats.imports import import_path


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _jsonl(path: Path, rows: list) -> Path:
    return _write(path, "".join(json.dumps(r) + "\n" for r in rows))


def _checks(imp, i=0):
    return imp.questions[i]["grading"]["checks"]


# --- Inspect AI ----------------------------------------------------------------------------------


def test_inspect_samples_mcq_renders_the_template_and_grades_letters(tmp_path):
    path = _jsonl(tmp_path / "arc.jsonl", [
        {"id": "a1", "input": "Which is a mammal?", "choices": ["Shark", "Whale"], "target": "B"},
        {"input": "Sky colour?", "target": "blue"},
    ])
    imp = import_path(path, fmt="inspect-samples")
    mcq, plain = imp.questions
    assert mcq["id"] == "a1"
    assert "'ANSWER: $LETTER'" in mcq["question"]
    assert "B) Whale" in mcq["question"]
    assert _checks(imp) == [{"type": "choice", "values": ["B"], "choices": ["Shark", "Whale"]}]
    assert mcq["expected_topics"] == ["B) Whale"]
    assert plain["id"] == "2"  # Inspect numbers id-less samples from 1
    assert _checks(imp, 1)[0]["type"] == "match"
    assert mcq["prompt_style"] == "verbatim"


def test_inspect_samples_refuses_an_unknown_scorer(tmp_path):
    path = _jsonl(tmp_path / "s.jsonl", [{"input": "q", "target": "a"}])
    with pytest.raises(ValueError, match="unknown Inspect scorer"):
        import_path(path, fmt="inspect-samples", scorer="bleu")


def test_inspect_task_is_read_statically_with_field_spec(tmp_path):
    _jsonl(tmp_path / "bio.jsonl", [{"qid": "q1", "question": "Hansen's disease?", "answer": "Leprosy"}])
    task = _write(tmp_path / "bio_task.py", (
        "raise SystemExit('this file must never run')\n"
        "from inspect_ai import Task, task\n"
        "@task\n"
        "def bio():\n"
        "    return Task(dataset=json_dataset('bio.jsonl', FieldSpec(input='question', target='answer', id='qid')),\n"
        "                solver=[system_message('Be brief.'), generate()], scorer=model_graded_qa())\n"))
    imp = import_path(task, fmt="inspect-task")
    q = imp.questions[0]
    assert (q["id"], q["question"]) == ("q1", "Be brief.\n\nHansen's disease?")
    assert _checks(imp) == [{"type": "judge", "reference": "Leprosy", "native": "model_graded_qa"}]
    assert imp.native_scorer == "model_graded_qa"
    assert "model_graded_qa: C/P/I grade approximated by a yes/no judge" in imp.lossy


def test_inspect_task_inline_samples_and_scorer_kwargs(tmp_path):
    task = _write(tmp_path / "t.py", (
        "@task\n"
        "def t():\n"
        "    return Task(dataset=[Sample(input='2+2?', target='4')], solver=[chain_of_thought(), generate()],\n"
        "                scorer=match(location='any', numeric=True), sandbox='docker')\n"))
    imp = import_path(task, fmt="inspect-task")
    check = _checks(imp)[0]
    assert (check["location"], check["numeric"]) == ("any", True)
    assert {"solver `chain_of_thought` not reproduced", "sandbox not provided (answer-only)"} <= set(imp.lossy)


def test_inspect_multiple_correct_is_asked_for_several_letters(tmp_path):
    task = _write(tmp_path / "t.py", (
        "@task\n"
        "def t():\n"
        "    return Task(dataset=[Sample(input='Even?', choices=['2', '4', '5'], target=['A', 'B'])],\n"
        "                solver=multiple_choice(multiple_correct=True), scorer=choice())\n"))
    q = import_path(task, fmt="inspect-task").questions[0]
    assert "'ANSWER: $LETTERS'" in q["question"] and "one or more of A,B,C" in q["question"]
    assert grade(q["grading"], q["question"], "ANSWER: A, B").passed is True


def test_inspect_pattern_scorer_is_not_a_bare_regex_search(tmp_path):
    task = _write(tmp_path / "t.py", (
        "@task\n"
        "def t():\n"
        "    return Task(dataset=[Sample(input='2+2?', target='4')], scorer=pattern(r'ANSWER: (\\d+)'))\n"))
    imp = import_path(task, fmt="inspect-task")
    assert _checks(imp) == [{"type": "unsupported", "native": "pattern"}]
    assert grade(imp.questions[0]["grading"], "2+2?", "ANSWER: 5").passed is None
    samples = _jsonl(tmp_path / "s.jsonl", [{"input": "2+2?", "target": "4"}])
    with pytest.raises(ValueError, match="needs the pattern"):
        import_path(samples, fmt="inspect-samples", scorer="pattern")


def test_inspect_partial_field_spec_keeps_the_default_id_and_choices_columns(tmp_path):
    _jsonl(tmp_path / "geo.jsonl", [{"id": "geo-7", "question": "Capital?", "choices": ["Lyon", "Paris"],
                                     "answer": "B"}])
    task = _write(tmp_path / "t.py", (
        "@task\n"
        "def t():\n"
        "    return Task(dataset=json_dataset('geo.jsonl', FieldSpec(input='question', target='answer')),\n"
        "                solver=multiple_choice(), scorer=choice())\n"))
    imp = import_path(task, fmt="inspect-task")
    assert imp.questions[0]["id"] == "geo-7"
    assert _checks(imp) == [{"type": "choice", "values": ["B"], "choices": ["Lyon", "Paris"]}]


def test_inspect_csv_choices_string_splits_like_read_choices():
    path = Path(__file__).parent / "fixtures/task_formats/inspect/test_dataset/samples-mcq.csv"
    imp = import_path(path, fmt="inspect-samples")
    assert _checks(imp) == [{"type": "choice", "values": ["A"], "choices": [str(n) for n in range(1, 11)]}]


def test_inspect_sample_whose_target_matches_no_choice_is_skipped(tmp_path):
    path = _jsonl(tmp_path / "s.jsonl", [{"id": "q1", "input": "x?", "choices": ["x", "y"], "target": "B"},
                                         {"id": "q2", "input": "two?", "choices": ["2", "3"], "target": "two"}])
    imp = import_path(path, fmt="inspect-samples")
    assert [q["id"] for q in imp.questions] == ["q1"]
    assert "sample whose target matches no choice skipped" in imp.lossy


@pytest.mark.parametrize("dataset,message", [
    ("hf_dataset('x')", "run it natively"),
    ("json_dataset(PATH)", "computed"),
    ("json_dataset('../outside.jsonl')", "outside the task's directory"),
])
def test_inspect_task_refuses_what_it_cannot_read_statically(tmp_path, dataset, message):
    task = _write(tmp_path / "d" / "t.py", f"@task\ndef t():\n    return Task(dataset={dataset}, scorer=match())\n")
    with pytest.raises(ValueError, match=message):
        import_path(task, fmt="inspect-task")


def test_inspect_task_with_several_tasks_needs_a_name(tmp_path):
    body = "@task\ndef {n}():\n    return Task(dataset=[Sample(input='q{n}', target='a')])\n"
    task = _write(tmp_path / "t.py", body.format(n="a") + body.format(n="b"))
    with pytest.raises(ValueError, match="found: a, b"):
        import_path(task, fmt="inspect-task")
    assert import_path(task, fmt="inspect-task", task="b").questions[0]["question"] == "qb"


# --- OpenAI evals --------------------------------------------------------------------------------

_BORN = [{"input": [{"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": "Was Lincoln born before Pierce? Answer Y or N."}], "ideal": "N"}]


def test_openai_match_from_registry_yaml(tmp_path):
    data = _jsonl(tmp_path / "born.jsonl", _BORN)
    yaml = _write(tmp_path / "born-first.yaml", (
        "born-first.dev.v0:\n  class: evals.elsuite.basic.match:Match\n  args:\n    samples_jsonl: born.jsonl\n"))
    imp = import_path(data, fmt="openai-evals", grader=str(yaml))
    q = imp.questions[0]
    assert q["question"].startswith("[system]\nYou are a helpful assistant.")
    assert _checks(imp) == [{"type": "match", "values": ["N"], "location": "begin", "ignore_case": False,
                             "ignore_punctuation": False, "strip": False}]
    assert grade(q["grading"], q["question"], "N").passed is True
    assert grade(q["grading"], q["question"], "n").passed is False


def test_openai_includes_and_modelgraded_fact(tmp_path):
    data = _jsonl(tmp_path / "s.jsonl", [{"input": [{"role": "user", "content": "Solve"}], "ideal": ["x = 3"]}])
    inc = import_path(data, fmt="openai-evals", grader="includes")
    assert (_checks(inc)[0]["location"], inc.questions[0]["question"]) == ("any", "Solve")
    yaml = _write(tmp_path / "fact.yaml", (
        "l.dev.v0:\n  class: evals.elsuite.modelgraded.classify:ModelBasedClassify\n"
        "  args:\n    samples_jsonl: s.jsonl\n    modelgraded_spec: fact\n"))
    fact = import_path(data, fmt="openai-evals", grader=str(yaml))
    assert _checks(fact)[0]["type"] == "judge"
    assert _checks(fact)[0]["reference"] == "x = 3"


def test_openai_json_match_with_no_json_ideal_is_not_any_json(tmp_path):
    data = _jsonl(tmp_path / "s.jsonl", [{"input": "q", "ideal": "not json"}, {"input": "r", "ideal": '{"a": 1}'}])
    imp = import_path(data, fmt="openai-evals", grader="json_match")
    assert _checks(imp) == [{"type": "unsupported", "native": "json_match"}]
    assert grade(imp.questions[0]["grading"], "q", '{"anything": 1}').passed is None
    assert _checks(imp, 1) == [{"type": "json", "values": [{"a": 1}]}]


_EVALS = Path(__file__).parent / "fixtures" / "task_formats" / "openai-evals" / "registry"


def test_openai_modelgraded_rows_without_ideal_import():
    data = _EVALS / "data" / "test_modelgraded" / "joke_fruits.jsonl"
    humor = import_path(data, fmt="openai-evals", grader=str(_EVALS / "evals" / "test-modelgraded.yaml"))
    assert humor.questions[0]["question"] == "write a 1-2 funny lines about apple"
    # The humor spec's prompt is not in the registry YAML: nothing to grade against, so not counted.
    assert _checks(humor) == [{"type": "unsupported", "native": "modelgraded:humor"}]
    with pytest.raises(ValueError, match="no questions"):  # a Match row still needs its ideal
        import_path(data, fmt="openai-evals", grader="match")


def test_an_ambiguous_file_is_refused_unless_the_format_is_named():
    # Alone, joke_fruits.jsonl reads as Inspect samples or OpenAI evals rows (`tasks inspect`
    # lists the second as an alternative); only test-modelgraded.yaml says which it is.
    data = _EVALS / "data" / "test_modelgraded" / "joke_fruits.jsonl"
    with pytest.raises(ValueError, match=r"ambiguous format \(inspect-samples, openai-evals\).*--format"):
        import_path(data)
    named = import_path(data, fmt="openai-evals", grader=str(_EVALS / "evals" / "test-modelgraded.yaml"))
    assert named.source_format == "openai-evals"
    plain = import_path(_EVALS / "data" / "allergen-information" / "samples.jsonl")
    assert plain.source_format == "openai-evals" and plain.questions


def test_openai_custom_eval_class_is_refused(tmp_path):
    data = _jsonl(tmp_path / "s.jsonl", _BORN)
    yaml = _write(tmp_path / "c.yaml", "c.dev.v0:\n  class: evals.elsuite.custom:Thing\n  args: {}\n")
    with pytest.raises(ValueError, match="custom code"):
        import_path(data, fmt="openai-evals", grader=str(yaml))


# --- skill-creator -------------------------------------------------------------------------------


def test_skill_creator_expectations_become_judge_checks(tmp_path):
    path = _write(tmp_path / "evals" / "evals.json", json.dumps({"skill_name": "ec2", "evals": [
        {"id": 1, "prompt": "Launch a t3.micro", "expected_output": "CLI commands", "files": ["in.csv"],
         "expectations": ["Creates a key pair", "Opens port 22"]},
        {"id": 2, "prompt": "Explain AMIs", "expected_output": "Defines an AMI"},
        {"id": 3, "prompt": "  "},
    ]}))
    imp = import_path(path, fmt="skill-creator-evals")
    assert [q["id"] for q in imp.questions] == ["1", "2"]
    assert [c["criterion"] for c in _checks(imp)] == ["Creates a key pair", "Opens port 22"]
    assert _checks(imp, 1) == [{"type": "judge", "criterion": "Defines an AMI", "native": "expected_output"}]
    assert imp.questions[0]["metadata"]["files"] == ["in.csv"]
    assert [q["expected_skill"] for q in imp.questions] == ["ec2", "ec2"]  # feeds trigger eval too
    assert {"input files not attached (answer-only)", "eval without prompt skipped"} <= set(imp.lossy)


def test_skill_creator_expected_skill_falls_back_to_the_skill_directory(tmp_path):
    rows = {"evals": [{"id": 1, "prompt": "Launch it", "expectations": ["Uses the CLI"]}]}
    nested = _write(tmp_path / "ec2" / "evals" / "evals.json", json.dumps(rows))
    assert import_path(nested, fmt="skill-creator-evals").questions[0]["expected_skill"] == "ec2"
    loose = _write(tmp_path / "loose.json", json.dumps(rows))
    imp = import_path(loose, fmt="skill-creator-evals")
    assert "expected_skill" not in imp.questions[0]
    assert any("expected_skill not set" in n for n in imp.lossy)


# --- promptfoo -----------------------------------------------------------------------------------

_PF = """\
prompts:
  - 'Solve this riddle: {{riddle}}'
providers: [openai:a, openai:b]
defaultTest:
  assert:
    - type: cost
      threshold: 0.15
tests:
  - vars: {riddle: 'I speak without a mouth.'}
    assert:
      - type: icontains
        value: echo
      - type: llm-rubric
        value: Do not apologize
  - vars: {riddle: 'Who makes it, sells it.'}
    assert:
      - type: icontains-any
        value: [coffin, casket]
  - vars: {riddle: 'two plus two'}
    threshold: 0.5
    assert:
      - type: equals
        value: '4'
      - type: not-regex
        value: 'sorry'
      - type: is-json
        weight: 2
"""


def test_promptfoo_tests_become_questions_with_mapped_asserts(tmp_path):
    imp = import_path(_write(tmp_path / "promptfooconfig.yaml", _PF), fmt="promptfoo")
    assert [q["id"] for q in imp.questions] == ["0", "1", "2"]
    assert imp.questions[0]["question"] == "Solve this riddle: I speak without a mouth."
    kinds = [[c["type"] for c in _checks(imp, i)] for i in range(3)]
    assert kinds == [["unsupported", "match", "judge"], ["unsupported", "match"],
                     ["unsupported", "match", "regex", "json"]]
    rubric = _checks(imp)[2]
    assert (rubric["criterion"], rubric["native"]) == ("Do not apologize", "llm-rubric")
    third = imp.questions[2]["grading"]
    assert third["threshold"] == 0.5
    assert third["checks"][2]["negate"] is True
    assert third["checks"][3]["weight"] == 2.0
    assert grade(imp.questions[1]["grading"], "q", "A CASKET.").passed is True
    assert {"assert `cost` not translated", "several providers: the answer track's model replaces them"} <= set(imp.lossy)


def test_promptfoo_two_prompts_key_by_test_and_prompt(tmp_path):
    cfg = "prompts: ['A {{x}}', 'B {{x}}']\ntests:\n  - vars: {x: 1}\n    assert: [{type: contains, value: '1'}]\n"
    imp = import_path(_write(tmp_path / "promptfooconfig.yaml", cfg), fmt="promptfoo")
    assert [(q["id"], q["question"]) for q in imp.questions] == [("0.p0", "A 1"), ("0.p1", "B 1")]


def test_promptfoo_text_prompt_file_splits_on_separator_lines():
    cfg = Path(__file__).parent / "fixtures/task_formats/promptfoo/eval-custom-grader-csv/promptfooconfig.yaml"
    imp = import_path(cfg, fmt="promptfoo")
    assert [q["id"] for q in imp.questions] == [f"{t}.p{p}" for t in range(4) for p in (0, 1)]
    assert imp.questions[0]["question"].startswith("Rephrase this in French: ")
    assert imp.questions[1]["question"].startswith("Rephrase this like a pirate: ")
    assert "---" not in imp.questions[0]["question"]
    assert [q["metadata"]["prompt"] for q in imp.questions[:2]] == ["p0.0", "p0.1"]


def test_promptfoo_file_refs_stay_inside_the_config_directory(tmp_path):
    _write(tmp_path / "tests.csv", "q,__expected\nhi,contains:hello\n")
    cfg = _write(tmp_path / "c" / "promptfooconfig.yaml", "prompts: ['{{q}}']\ntests: file://../tests.csv\n")
    with pytest.raises(ValueError, match="outside the config's directory"):
        import_path(cfg, fmt="promptfoo")
    _write(tmp_path / "c" / "tests.csv", "q,__expected\nhi,contains:hello\n")
    _write(cfg, "prompts: ['{{q}}']\ntests: file://tests.csv\n")
    imp = import_path(cfg, fmt="promptfoo")
    assert _checks(imp)[0]["values"] == ["hello"]


def test_promptfoo_contains_with_an_empty_value_is_not_a_pass_for_every_answer(tmp_path):
    cfg = _write(tmp_path / "promptfooconfig.yaml",
                 "prompts: ['{{q}}']\ntests:\n  - vars: {q: x}\n    assert: [{type: contains-any, value: ''}]\n")
    assert _checks(import_path(cfg, fmt="promptfoo")) == [{"type": "unsupported", "native": "contains-any"}]


def test_promptfoo_csv_expected_cells_follow_assertion_from_string(tmp_path):
    # promptfoo's assertionFromString: code is not run, grade: is a rubric, not-X negates,
    # and a *-any / *-all value is a comma list.
    _write(tmp_path / "tests.csv",
           'q,__expected1,__expected2,__expected3,__expected4\n'
           'hi,"javascript:output.length > 3","not-contains: bye","grade: is funny",'
           '"contains-any: apple,banana"\n')
    cfg = _write(tmp_path / "promptfooconfig.yaml", "prompts: ['{{q}}']\ntests: file://tests.csv\n")
    checks = _checks(import_path(cfg, fmt="promptfoo"))
    assert [c["type"] for c in checks] == ["unsupported", "match", "judge", "match"]
    assert checks[0]["native"] == "javascript"
    assert checks[1]["values"] == ["bye"] and checks[1]["negate"] is True
    assert checks[2]["criterion"] == "is funny"
    assert checks[3]["values"] == ["apple", "banana"] and checks[3].get("mode", "any") == "any"


_EVAL = '{{"evals": [{{"id": 1, "prompt": "p", "expected_output": "o", {}}}]}}'


@pytest.mark.parametrize("name, text, fmt, message", [
    ("t.py", "@task\ndef t(:\n", "inspect-task", "does not parse as Python"),
    ("t.py", "x = 1\0\n", "inspect-task", "does not parse as Python"),
    ("evals.json", _EVAL.format('"expectations": "abc"'), "skill-creator-evals", "`expectations` is not a list"),
    ("evals.json", _EVAL.format('"files": "a.pdf"'), "skill-creator-evals", "`files` is not a list"),
    ("promptfooconfig.yaml", "prompts: ['{{q}}']\ntests:\n  - vars: {q: x}\n    assert:\n"
     "      - {type: contains, value: x, weight: null}\n", "promptfoo", "weight None is not a number"),
    ("promptfooconfig.yaml", "prompts: ['{{q}}']\ntests:\n  - vars: file://v.yaml\n    assert: [{type: contains, value: x}]\n",
     "promptfoo", "`vars` is not a mapping"),
    ("promptfooconfig.yaml", "prompts: [file://p.json]\ntests:\n  - assert: [{type: contains, value: x}]\n",
     "promptfoo", "prompt file does not parse"),
])
def test_malformed_input_is_an_import_error(tmp_path, name, text, fmt, message):
    _write(tmp_path / "p.json", '{"truncated": ')
    path = _write(tmp_path / name, text)
    with pytest.raises(ValueError, match=message):
        import_path(path, fmt=fmt)


def test_skill_creator_null_expectations_read_as_none(tmp_path):
    path = _write(tmp_path / "evals.json", _EVAL.format('"expectations": null'))
    assert _checks(import_path(path, fmt="skill-creator-evals"))[0]["criterion"] == "o"


# --- detection, CLI, answerer --------------------------------------------------------------------


def test_import_path_detects_the_format_and_refuses_one_without_an_adapter(tmp_path):
    path = _jsonl(tmp_path / "samples.jsonl", _BORN)
    assert import_path(path).source_format == "openai-evals"
    with pytest.raises(ValueError, match="no import adapter"):
        import_path(path, fmt="swe-bench")


def test_questions_import_cli_writes_a_valid_questions_artifact(tmp_path, capsys):
    data = _jsonl(tmp_path / "born.jsonl", _BORN)
    out = tmp_path / "q.json"
    args = build_parser().parse_args(["questions", "import", str(data), "--grader", "match", "-o", str(out)])
    args.func(args)
    loaded = load_questions(out)
    assert loaded.source_version == "questions.v2"
    assert loaded.artifact["questions"][0]["grading"]["checks"][0]["values"] == ["N"]
    assert json.loads(capsys.readouterr().out.split("\nWrote")[0])["checks"] == {"match": 1}


def test_eval_reference_cli_without_judge(tmp_path):
    q = tmp_path / "q.json"
    q.write_text(json.dumps({"questions": [{"id": "1", "question": "Q", "grading": {"checks": [
        {"type": "match", "values": ["yes"], "location": "any"}, {"type": "judge", "criterion": "polite"}]}}]}))
    a = tmp_path / "a.json"
    a.write_text(json.dumps({"answers": [{"question_id": "1", "question_text": "Q", "without_docs": {"answer": "yes"}}]}))
    out = tmp_path / "e.json"
    args = build_parser().parse_args(["eval", "reference", "--answers", str(a), "--questions", str(q),
                                      "-o", str(out), "--no-judge"])
    args.func(args)
    block = json.loads(out.read_text())["evaluations"][0]["without_docs"]
    assert (block["passed"], [c["passed"] for c in block["checks"]]) == (True, [True, None])


def test_eval_reference_cli_without_judge_on_an_arms_run(tmp_path):
    q = tmp_path / "q.json"
    q.write_text(json.dumps({"questions": [{"id": "1", "question": "Q", "grading": {"checks": [
        {"type": "match", "values": ["paris"], "location": "any"}]}}]}))
    a = tmp_path / "a.json"
    a.write_text(json.dumps({"baseline_arm": "baseline", "arms": ["baseline"], "answers": [
        {"question_id": "1", "question_text": "Q", "arms": {"baseline": {"answer": "Paris"}}}]}))
    out = tmp_path / "r.json"
    args = build_parser().parse_args(["eval", "reference", "--answers", str(a), "--questions", str(q),
                                      "-o", str(out), "--no-judge"])
    args.func(args)
    data = json.loads(out.read_text())
    assert (data["judge_model"], data["judge_provider"]) == ("reference", "reference")
    assert data["evaluations"][0]["scores"]["baseline"]["passed"] is True


def test_arms_run_sends_a_verbatim_question_unwrapped(monkeypatch, demo_skill):
    import dokimi.eval.arm_runner as arm_runner_mod
    from dokimi.eval.arm_runner import ArmRunner
    from dokimi.skills import load_skill
    from dokimi.treatments import BaselineTreatment, SkillTreatment

    captured = []

    def fake_call(prompt, model, provider="openai", api_key=None, system=None, **kw):
        captured.append({"prompt": prompt, "cache_prefix": kw.get("cache_prefix")})
        return "ANSWER: A", {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}

    monkeypatch.setattr(arm_runner_mod, "llm_call_with_usage", fake_call)
    runner = ArmRunner([BaselineTreatment(), SkillTreatment(load_skill(demo_skill))])
    runner.run("oneTBB", [{"id": "v", "question": "Reply with A or B.", "prompt_style": "verbatim"}],
               concurrency=1)
    baseline, skill = captured
    assert baseline["prompt"] == "Reply with A or B."
    assert skill["prompt"].endswith("\n\nReply with A or B.") or skill["prompt"] == "Reply with A or B."
    assert "**Question:**" not in skill["prompt"]
    assert "Relevant context" in (skill["cache_prefix"] or skill["prompt"])


def test_answerer_sends_a_verbatim_question_unwrapped():
    from dokimi.eval.answerer import Answerer

    answerer = Answerer(mcp_client=None, model="m", provider="openai")
    with mock.patch("dokimi.eval.answerer.llm_call_with_usage", return_value=("ANSWER: B", {})) as call:
        pair = answerer._generate_answer_pair("lib", "lib", {"id": "1", "question": "Pick one.",
                                                             "prompt_style": "verbatim"}, 1000)
        assert call.call_args.kwargs["prompt"] == "Pick one."
        answerer._generate_answer_pair("lib", "lib", {"id": "2", "question": "Pick one."}, 1000)
        assert call.call_args.kwargs["prompt"] != "Pick one."
    assert pair["without_docs"]["answer"] == "ANSWER: B"


def test_answerer_puts_docs_before_an_unwrapped_verbatim_question():
    from dokimi.eval.answerer import Answerer

    answerer = Answerer(mcp_client=object(), model="m", provider="openai")
    docs = [{"content": "DOC TEXT", "source": "s"}]
    with mock.patch.object(Answerer, "_retrieve_docs", return_value=(docs, {})), \
            mock.patch("dokimi.eval.answerer.llm_call_with_usage", return_value=("ANSWER: B", {})) as call:
        answerer._generate_answer_pair("lib", "lib", {"id": "1", "question": "Pick one.",
                                                      "prompt_style": "verbatim"}, 1000)
    with_docs, without = (c.kwargs["prompt"] for c in call.call_args_list)
    assert with_docs == "Relevant documentation:\nDOC TEXT\n\n---\n\nPick one."
    assert without == "Pick one."


_M = {"ignore_punctuation": False, "strip": False}


def test_each_native_grader_maps_to_its_exact_check(tmp_path):
    # One row per mapping, asserted whole, so a changed default cannot pass unnoticed.
    row = _jsonl(tmp_path / "o.jsonl", [{"input": "q", "ideal": "x"}])
    includes = import_path(row, fmt="openai-evals", grader="includes")
    assert _checks(includes) == [{"type": "match", "values": ["x"], "location": "any", "ignore_case": False, **_M}]
    sky = _jsonl(tmp_path / "i.jsonl", [{"input": "Sky?", "target": "blue"},
                                        {"input": "Pet?", "choices": ["Shark", "Whale"], "target": "Whale"}])
    inspect = import_path(sky, fmt="inspect-samples")
    assert _checks(inspect) == [{"type": "match", "values": ["blue"], "location": "end", "ignore_case": True,
                                 "ignore_punctuation": True, "strip": True}]
    assert _checks(inspect, 1) == [{"type": "choice", "values": ["B"], "choices": ["Shark", "Whale"]}]
    cfg = ("prompts: ['{{q}}']\ntests:\n  - vars: {q: x}\n    assert:\n"
           "      - {type: contains-all, value: [a, b]}\n      - {type: icontains, value: c}\n"
           "      - {type: starts-with, value: d}\n")
    pf = import_path(_write(tmp_path / "promptfooconfig.yaml", cfg), fmt="promptfoo")
    assert _checks(pf) == [
        {"type": "match", "values": ["a", "b"], "location": "any", "ignore_case": False, **_M, "mode": "all"},
        {"type": "match", "values": ["c"], "location": "any", "ignore_case": True, **_M},
        {"type": "match", "values": ["d"], "location": "begin", "ignore_case": False, **_M},
    ]


def test_importable_formats_match_the_adapters_and_show_as_consumer(tmp_path):
    from dokimi.task_formats import detect
    from dokimi.task_formats.model import IMPORTABLE
    from dokimi.task_formats.imports import ADAPTERS

    assert IMPORTABLE == frozenset(ADAPTERS)
    report = detect(_jsonl(tmp_path / "samples.jsonl", _BORN))
    assert report.consumers == ["questions import"]
