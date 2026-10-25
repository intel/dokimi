# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for eval-task format detection (``dokimi.task_formats``, ``tasks inspect``).

Each fixture is hand-written from the upstream shape it imitates:

- terminal-bench v1: laude-institute/terminal-bench ``original-tasks/hello-world``.
- SWE-bench: ``SWEbenchInstance`` in SWE-bench/SWE-bench ``swebench/types.py``
  (``FAIL_TO_PASS`` as the JSON string the HF datasets ship).
- Inspect AI: ``Sample`` fields (``input``, ``target``, ``id``, ``sandbox``) and an
  ``examples/hello_world.py``-style ``@task`` file.
- OpenAI evals: ``evals/registry/data/coqa/match.jsonl`` (chat ``input``, ``ideal``).
- skill-creator: ``references/schemas.md`` ``evals.json`` and the ``SKILL.md`` trigger set.
- skill-reach: ``QuerySet`` in google/skill-reach ``src/reach/queries.py``.
- promptfoo: ``examples/*/promptfooconfig.yaml``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dokimi.cli import build_parser
from dokimi.external.trigger_set import TriggerQuery, to_reach, to_skill_creator
from dokimi.harnesses import discover_tasks
from dokimi.task_formats import detect, inspect_path


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _jsonl(path: Path, rows: list) -> Path:
    return _write(path, "".join(json.dumps(r) + "\n" for r in rows))


def _harbor(root: Path, name: str = "hb") -> Path:
    task = root / name
    _write(task / "task.toml", '[metadata]\ndifficulty = "easy"\ntags = ["x"]\n')
    _write(task / "instruction.md", "Fix it.")
    (task / "environment").mkdir()
    _write(task / "solution" / "solve.sh", "#!/bin/bash\n")
    _write(task / "tests" / "test.sh", "#!/bin/bash\n")
    return task


def _task_md(root: Path, name: str = "tm") -> Path:
    task = root / name
    _write(task / "task.md", "---\nschema_version: '1.3'\nmetadata:\n  difficulty: medium\n"
                             "verifier:\n  timeout_sec: 60\n---\n\nDo the thing.\n")
    _write(task / "oracle" / "solve.sh", "#!/bin/bash\n")
    _write(task / "verifier" / "test.sh", "#!/bin/bash\n")
    return task


def _tb1(root: Path, name: str = "hello-world") -> Path:
    task = root / name
    _write(task / "task.yaml", "instruction: |\n  Create hello.txt with 'Hello, world!'.\n"
                               "author_name: a\ndifficulty: easy\ncategory: file-operations\n"
                               "tags:\n  - file-operations\nparser_name: pytest\n")
    _write(task / "Dockerfile", "FROM ubuntu:24.04\nWORKDIR /app\n")
    _write(task / "docker-compose.yaml", "services: {}\n")
    _write(task / "run-tests.sh", "#!/bin/bash\npytest $TEST_DIR/test_outputs.py\n")
    _write(task / "solution.sh", "echo 'Hello, world!' > hello.txt\n")
    _write(task / "tests" / "test_outputs.py", "def test_hello():\n    pass\n")
    return task


_SWE_ROW = {
    "repo": "astropy/astropy", "instance_id": "astropy__astropy-12907",
    "base_commit": "d16bfe05a744909de4b27f5875fe0d4ed41ce607", "patch": "diff --git a/x b/x\n",
    "test_patch": "diff --git a/t b/t\n", "problem_statement": "separability_matrix is wrong",
    "hints_text": "", "created_at": "2022-03-03T15:14:54Z", "version": "4.3",
    "FAIL_TO_PASS": '["astropy/modeling/tests/test_separable.py::test_separable[compound_model6]"]',
    "PASS_TO_PASS": "[]", "environment_setup_commit": "298ccb478e6bf092953bca67a3d29dc6c35f6752",
}
_CHAT = [{"role": "system", "content": "Answer briefly."}, {"role": "user", "content": "2+2?"}]


def _one(path: Path):
    reports = inspect_path(path)
    assert len(reports) == 1, [r.as_dict() for r in reports]
    return reports[0]


# ── one probe per format ──────────────────────────────────────────────────────


def test_harbor_task_is_executable_and_consumable_by_harbor_and_docker(tmp_path):
    r = _one(_harbor(tmp_path))
    assert (r.format, r.kind, r.consumers) == ("harbor", "executable", ["harbor", "docker-*"])
    assert r.missing == [] and r.problems == []


def test_harbor_task_without_environment_reports_why_harbor_refuses_it(tmp_path):
    task = _harbor(tmp_path)
    (task / "environment").rmdir()
    r = _one(task)
    assert "environment" in r.missing
    assert r.consumers == ["docker-*"]
    assert any("Harbor would refuse it: no environment/" in p for p in r.problems)


def test_task_md_task_is_consumable_by_benchflow(tmp_path):
    r = _one(_task_md(tmp_path))
    assert (r.format, r.kind) == ("task-md", "executable")
    assert r.consumers == ["benchflow", "docker-*"]
    assert r.details["schema_version"] == "1.3"


def test_terminal_bench_v1_task_has_every_part_and_no_consumer_yet(tmp_path):
    r = _one(_tb1(tmp_path))
    assert (r.format, r.kind, r.missing) == ("terminal-bench-v1", "executable", [])
    assert r.as_dict()["consumers"] == ["none-yet"]
    assert r.details["metadata"]["difficulty"] == "easy"


def test_swe_bench_jsonl_counts_instances_and_parses_string_test_lists(tmp_path):
    other = {**_SWE_ROW, "instance_id": "astropy__astropy-14182"}
    r = _one(_jsonl(tmp_path / "lite.jsonl", [_SWE_ROW, other]))
    assert (r.format, r.kind, r.items) == ("swe-bench", "executable", 2)
    assert r.missing == [] and r.problems == []
    assert r.details["repos"] == ["astropy/astropy"]


def test_swe_bench_lints_missing_fields_and_duplicate_ids(tmp_path):
    broken = {k: v for k, v in _SWE_ROW.items() if k != "FAIL_TO_PASS"}
    r = _one(_jsonl(tmp_path / "bad.jsonl", [_SWE_ROW, broken, {**_SWE_ROW, "FAIL_TO_PASS": "nope"}]))
    assert r.format == "swe-bench"
    assert "1 of 3 items have no FAIL_TO_PASS" in r.problems
    assert "1 items have a FAIL_TO_PASS that is not a list of test ids" in r.problems
    assert "duplicate ids: astropy__astropy-12907" in r.problems


def test_inspect_samples_jsonl_is_answer_only(tmp_path):
    rows = [{"id": 1, "input": "Capital of France?", "target": "Paris"},
            {"id": 2, "input": _CHAT, "target": "4", "sandbox": "docker"}]
    r = _one(_jsonl(tmp_path / "samples.jsonl", rows))
    assert (r.format, r.kind, r.items) == ("inspect-samples", "answer-only", 2)
    assert "environment" in r.present and "verifier" in r.missing


def test_inspect_task_file_is_scanned_not_imported(tmp_path):
    marker = tmp_path / "imported"
    src = (
        "from inspect_ai import Task, task\nfrom inspect_ai.dataset import Sample\n"
        f"open({str(marker)!r}, 'w').write('ran')\n\n"
        "@task\ndef hello():\n    return Task(dataset=[Sample(input='hi', target='hi')],\n"
        "                solver=[generate()], scorer=exact(), sandbox='docker')\n"
    )
    r = _one(_write(tmp_path / "hello.py", src))
    assert (r.format, r.kind, r.items) == ("inspect-task", "executable", 1)
    assert {"instruction", "environment", "verifier"} <= set(r.present)
    assert not marker.exists()


def test_python_without_inspect_import_is_not_an_inspect_task(tmp_path):
    r = _one(_write(tmp_path / "x.py", "@task\ndef f():\n    pass\n"))
    assert r.format == "unknown"


def test_openai_evals_chat_input_with_ideal(tmp_path):
    r = _one(_jsonl(tmp_path / "match.jsonl", [{"input": _CHAT, "ideal": "4"}] * 3))
    assert (r.format, r.kind, r.items) == ("openai-evals", "answer-only", 3)
    assert r.details["chat_inputs"] == 3
    assert r.alternatives == []


def test_skill_creator_evals_json(tmp_path):
    data = {"skill_name": "pdf", "evals": [
        {"id": 1, "prompt": "Extract the tables", "expected_output": "a CSV",
         "files": ["evals/files/a.pdf"], "expectations": ["The output includes X"]},
        {"id": 1, "prompt": "Split it", "expectations": "not a list"},
    ]}
    r = _one(_write(tmp_path / "evals" / "evals.json", json.dumps(data)))
    assert (r.format, r.kind, r.items) == ("skill-creator-evals", "answer-only", 2)
    assert r.details["skill_name"] == "pdf"
    assert "1 items have expectations that are not a list" in r.problems
    assert "duplicate ids: 1" in r.problems


def _queries() -> list[TriggerQuery]:
    return [TriggerQuery(id="q1", text="load my pdf", expected_skill="pdf"),
            TriggerQuery(id="q2", text="what's the weather", expected_skill=None)]


def test_skill_creator_trigger_set_written_by_dokimi_is_detected(tmp_path):
    path = _write(tmp_path / "trigger.json", json.dumps(to_skill_creator(_queries(), "pdf")))
    r = _one(path)
    assert (r.format, r.kind, r.items) == ("skill-creator-trigger", "trigger", 2)
    assert (r.details["should_trigger"], r.details["should_not_trigger"]) == (1, 1)


def test_skill_reach_queryset_written_by_dokimi_is_detected_and_clean(tmp_path):
    path = _write(tmp_path / "queries.json", json.dumps(to_reach(_queries(), "cat")))
    r = _one(path)
    assert (r.format, r.kind, r.items) == ("skill-reach", "trigger", 2)
    assert r.problems == []
    assert r.details["out_of_scope"] == 1


def test_skill_reach_lints_what_reach_would_refuse(tmp_path):
    data = {"queries": [{"query_id": "a", "text": "t", "expected_skill": None},
                        {"query_id": "a", "text": "u", "expected_skill": "s", "extra": 1}],
            "owner": "me"}
    r = _one(_write(tmp_path / "q.json", json.dumps(data)))
    assert r.format == "skill-reach"
    assert "QuerySet keys skill-reach refuses: owner" in r.problems
    assert "a: kind=None disagrees with expected_skill=None" in r.problems
    assert "a: keys skill-reach refuses: extra" in r.problems
    assert "duplicate ids: a" in r.problems


def test_promptfoo_config(tmp_path):
    cfg = ("description: suite\nprompts:\n  - 'Answer: {{query}}'\nproviders:\n  - openai:gpt-4o\n"
           "tests:\n  - vars: {query: hi}\n    assert:\n      - type: contains\n        value: hi\n"
           "  - vars: {query: yo}\n")
    r = _one(_write(tmp_path / "promptfooconfig.yaml", cfg))
    assert (r.format, r.kind, r.items) == ("promptfoo", "answer-only", 2)
    assert r.present == ["instruction", "environment", "verifier", "metadata"]


def test_promptfoo_tests_from_a_file_reference(tmp_path):
    cfg = "prompts: [p.txt]\nproviders: [echo]\ntests: file://tests.csv\n"
    r = _one(_write(tmp_path / "eval.yaml", cfg))
    assert (r.format, r.items, r.details["tests_from"]) == ("promptfoo", None, "file://tests.csv")


def test_dokimi_questions_v2_is_consumable_by_the_questions_track(tmp_path):
    data = {"schema_version": "questions.v2", "product": "p",
            "questions": [{"id": "q1", "question": "How?", "expected_topics": ["a"]}]}
    r = _one(_write(tmp_path / "golden.json", json.dumps(data)))
    assert (r.format, r.kind, r.items, r.consumers) == ("dokimi-questions", "answer-only", 1, ["questions track"])
    assert r.problems == []


def test_dokimi_questions_with_expected_skill_are_trigger_and_v1_loads(tmp_path):
    data = {"questions": [{"id": "q1", "text": "How?", "expected_skill": "pdf"}]}
    r = _one(_write(tmp_path / "v1.json", json.dumps(data)))
    assert (r.format, r.kind, r.consumers) == ("dokimi-questions", "trigger", ["questions track"])


def test_dokimi_questions_the_loader_refuses_is_a_problem_not_a_consumer(tmp_path):
    data = {"schema_version": "questions.v2", "questions": [{"id": "q1", "question": ""}]}
    r = _one(_write(tmp_path / "bad.json", json.dumps(data)))
    assert r.format == "dokimi-questions"
    assert r.consumers == [] and r.problems


# ── ambiguity, unknown, malformed ─────────────────────────────────────────────


def test_rows_with_target_and_ideal_list_the_other_format_as_ambiguous(tmp_path):
    rows = [{"input": _CHAT, "target": "4", "ideal": "4"}] * 2
    r = _one(_jsonl(tmp_path / "both.jsonl", rows))
    assert r.format == "openai-evals"
    assert [a["format"] for a in r.alternatives] == ["inspect-samples"]
    assert r.alternatives[0]["evidence"] == ["input", "target"]


def test_directory_with_task_toml_and_task_yaml_lists_terminal_bench_as_alternative(tmp_path):
    task = _harbor(tmp_path)
    _write(task / "task.yaml", "instruction: hi\n")
    _write(task / "run-tests.sh", "#!/bin/bash\n")
    r = _one(task)
    assert r.format == "harbor"
    assert [a["format"] for a in r.alternatives] == []  # 0.6 is not within the margin of 1.0
    assert detect(task).format == "harbor"


def test_unknown_directory_lists_prompt_and_verifier_candidates(tmp_path):
    _write(tmp_path / "prompt.md", "do x")
    _write(tmp_path / "check.sh", "exit 0")
    _write(tmp_path / "notes.txt", "n")
    r = _one(tmp_path)
    assert (r.format, r.kind) == ("unknown", "unknown")
    assert r.details["prompt_candidates"] == ["prompt.md"]
    assert r.details["verifier_candidates"] == ["check.sh"]


def test_unknown_jsonl_reports_row_keys(tmp_path):
    r = _one(_jsonl(tmp_path / "qa.jsonl", [{"question": "q", "answer": "a"}]))
    assert (r.format, r.items, r.details["row_keys"]) == ("unknown", 1, ["answer", "question"])


@pytest.mark.parametrize("name, text, problem", [
    ("bad.json", "{not json", "not valid JSON"),
    ("bad.yaml", "a: [1, 2\n", "not valid YAML"),
    ("alias.yaml", "a: &x [1]\nb: *x\n", "uses YAML aliases"),
    ("bad.jsonl", '{"input": "a", "target": "b"}\n{oops\n', "line 2: not valid JSON"),
])
def test_malformed_data_file_is_a_lint_problem_not_a_crash(tmp_path, name, text, problem):
    r = _one(_write(tmp_path / name, text))
    assert any(problem in p for p in r.problems), r.problems


def test_malformed_jsonl_keeps_its_good_rows(tmp_path):
    r = _one(_write(tmp_path / "s.jsonl", '{"input": "a", "target": "b"}\n{oops\n'))
    assert (r.format, r.items) == ("inspect-samples", 1)


def test_malformed_task_toml_is_a_problem(tmp_path):
    task = _harbor(tmp_path)
    _write(task / "task.toml", "[metadata\n")
    r = _one(task)
    assert r.format == "harbor"
    assert any("not valid TOML" in p for p in r.problems)


def test_malformed_tb1_task_yaml_is_a_problem(tmp_path):
    task = _tb1(tmp_path)
    _write(task / "task.yaml", "instruction: [\n")
    r = _one(task)
    assert r.format == "terminal-bench-v1"
    assert any(p.startswith("task.yaml: not valid YAML") for p in r.problems)


# ── scan ──────────────────────────────────────────────────────────────────────


def test_scan_of_a_mixed_tree_reports_each_item_once(tmp_path):
    _harbor(tmp_path / "tasks", "a")
    _task_md(tmp_path / "tasks", "b")
    _tb1(tmp_path / "tb")
    _jsonl(tmp_path / "data" / "swe.jsonl", [_SWE_ROW])
    _write(tmp_path / "data" / "package.json", '{"name": "x"}')  # not an eval: skipped
    _write(tmp_path / ".git" / "q.json", json.dumps(to_reach(_queries())))  # never entered
    found = {(r.path.relative_to(tmp_path).as_posix(), r.format) for r in inspect_path(tmp_path)}
    assert found == {("tasks/a", "harbor"), ("tasks/b", "task-md"),
                     ("tb/hello-world", "terminal-bench-v1"), ("data/swe.jsonl", "swe-bench")}


def test_inspect_missing_path_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        inspect_path(tmp_path / "nope")


def test_existing_discovery_is_unchanged_by_new_formats_in_the_root(tmp_path):
    _harbor(tmp_path, "a")
    _task_md(tmp_path, "b")
    _tb1(tmp_path, "c")
    assert [(t.name, t.format) for t in discover_tasks(tmp_path)] == [("a", "harbor"), ("b", "task-md")]


# ── CLI ───────────────────────────────────────────────────────────────────────


def _run(argv: list[str]) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


def test_cli_inspect_prints_a_table_line_per_item(tmp_path, capsys):
    _harbor(tmp_path)
    _jsonl(tmp_path / "m.jsonl", [{"input": _CHAT, "ideal": "4"}])
    _run(["tasks", "inspect", str(tmp_path)])
    out = capsys.readouterr().out.splitlines()
    assert out[0] == f"{tmp_path / 'hb'}\tharbor\texecutable\t-> harbor, docker-*"
    assert f"{tmp_path / 'm.jsonl'}\topenai-evals\tanswer-only\t1 item\t-> none-yet" in out
    assert out[-1] == "2 items: harbor 1, openai-evals 1"


def test_cli_inspect_json(tmp_path, capsys):
    _tb1(tmp_path)
    _run(["tasks", "inspect", str(tmp_path), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert [i["format"] for i in payload["items"]] == ["terminal-bench-v1"]
    assert payload["items"][0]["consumers"] == ["none-yet"]


def test_cli_inspect_shows_ambiguity_and_problems(tmp_path, capsys):
    _write(tmp_path / "x.jsonl", '{"input": "a", "target": "b", "ideal": "b"}\n{bad\n')
    _run(["tasks", "inspect", str(tmp_path / "x.jsonl")])
    out = capsys.readouterr().out
    assert "  ambiguous: also openai-evals" in out
    assert "  problem: line 2: not valid JSON" in out


def test_cli_inspect_missing_path_exits_with_a_message(tmp_path):
    with pytest.raises(SystemExit) as excinfo:
        _run(["tasks", "inspect", str(tmp_path / "nope")])
    assert "no such file or directory" in str(excinfo.value)


def test_cli_inspect_json_is_a_stamped_artifact_and_out_writes_the_same(tmp_path, capsys):
    from dokimi.artifacts import validate_artifact

    _tb1(tmp_path)
    out = tmp_path / "report.json"
    _run(["tasks", "inspect", str(tmp_path), "--json", "--out", str(out)])
    printed = json.loads(capsys.readouterr().out)
    assert printed["schema_version"] == "task_inspect.v1"
    validate_artifact("task_inspect", printed)
    assert json.loads(out.read_text()) == printed


def test_cli_inspect_out_to_an_unwritable_path_exits_with_a_message(tmp_path):
    _tb1(tmp_path)
    (tmp_path / "f").write_text("")
    with pytest.raises(SystemExit) as excinfo:
        _run(["tasks", "inspect", str(tmp_path), "--out", str(tmp_path / "f" / "r.json")])
    assert "cannot write" in str(excinfo.value)


# ── configs that name their datasets ───────────────────────────────────────────


def test_promptfoo_config_without_tests_or_prompts_is_linted(tmp_path):
    _write(tmp_path / "promptfooconfig.yaml", "providers: [openai:gpt-4.1-mini]\n")
    [report] = inspect_path(tmp_path / "promptfooconfig.yaml")
    assert (report.format, report.problems) == ("promptfoo", ["no tests", "no prompts"])


def test_promptfoo_url_tests_are_not_followed_and_yaml_tests_are_promptfoo_tests(tmp_path):
    _write(tmp_path / "promptfooconfig.yaml",
           "prompts: [p]\nproviders: [echo]\ntests: https://example.com/t.csv\n")
    _write(tmp_path / "cases.yaml", "- vars: {q: a}\n  assert: [{type: equals, value: a}]\n")
    by_name = {Path(r.path).name: r for r in inspect_path(tmp_path)}
    assert "references" not in by_name["promptfooconfig.yaml"].details
    assert by_name["cases.yaml"].format == "promptfoo-tests"


def test_openai_spec_lints_samples_it_cannot_find(tmp_path):
    _write(tmp_path / "registry/evals/x.yaml",
           "x.dev.v0:\n  class: evals.elsuite.basic.match:Match\n  args: {samples_jsonl: x/s.jsonl}\n")
    [report] = inspect_path(tmp_path)
    assert report.format == "openai-evals-spec"
    assert report.problems == ["1 samples files not found: s.jsonl"]


def test_inspect_task_follows_local_files_not_hub_datasets(tmp_path):
    _write(tmp_path / "t.py", "from inspect_ai import Task, task\n"
           "from inspect_ai.dataset import FieldSpec, csv_dataset, hf_dataset\n"
           "@task\ndef t():\n"
           "    a = hf_dataset(path='org/data', sample_fields=to_sample)\n"
           "    return Task(dataset=csv_dataset('d.csv', FieldSpec(input='q', target='a')))\n")
    _write(tmp_path / "d.csv", "q,a\n1+1?,2\n")
    by_name = {Path(r.path).name: r for r in inspect_path(tmp_path)}
    task = by_name["t.py"].details
    assert task["record_to_sample"] is True
    assert [r["path"] for r in task["references"]] == [str(tmp_path / "d.csv")]
    assert (by_name["d.csv"].format, by_name["d.csv"].items) == ("inspect-samples", 1)


def test_a_config_does_not_relabel_a_file_another_format_owns_outright(tmp_path):
    _harbor(tmp_path)
    _write(tmp_path / "promptfooconfig.yaml", "prompts: [p]\nproviders: [echo]\ntests: file://hb\n")
    assert {r.format for r in inspect_path(tmp_path)} == {"harbor", "promptfoo"}


def test_explore_task_that_explore_would_refuse_has_a_problem_and_no_consumer(tmp_path):
    _write(tmp_path / "e.yaml", "id: e\nquery: where?\nreferences: [{path: a.py}, {lines: [1]}]\n")
    [report] = inspect_path(tmp_path / "e.yaml")
    assert report.format == "dokimi-explore-task"
    assert report.problems and "explore track" not in report.consumers


def test_task_md_with_foreign_frontmatter_is_a_task_to_lint(tmp_path):
    _write(tmp_path / "t/task.md", "---\ntitle: x\n---\nDo it.\n")
    [report] = inspect_path(tmp_path)
    assert report.format == "task-md" and report.confidence == 0.7


def test_unreadable_csv_and_binary_files_are_not_claimed(tmp_path):
    _write(tmp_path / "bad.csv", 'a,b\n"x,1\n')
    (tmp_path / "data.parquet").write_bytes(b"PAR1\x00")
    assert [r.format for r in inspect_path(tmp_path)] == ["unknown"]


@pytest.mark.skipif(not Path("/dev/null").exists(), reason="needs /dev/null")
def test_a_path_that_is_neither_file_nor_directory_is_read_as_a_file():
    [report] = inspect_path(Path("/dev/null"))
    assert report.format == "unknown"


# ── one bad value does not stop a tree scan ───────────────────────────────────


@pytest.mark.parametrize("name, text", [
    ("promptfooconfig.json",
     json.dumps({"prompts": ["p"], "providers": ["x"], "tests": "file://a\0b.csv"})),
    ("task.py", 'from inspect_ai import Task, task\n@task\ndef t():\n'
                '    return Task(dataset=json_dataset("a\0b.jsonl"))\n'),
])
def test_a_config_naming_a_path_with_a_nul_byte_names_nothing(tmp_path, name, text):
    _write(tmp_path / name, text)
    _jsonl(tmp_path / "ok" / "s.jsonl", [{"input": "q", "target": "a"}])
    formats = sorted(r.format for r in inspect_path(tmp_path))
    assert "inspect-samples" in formats and len(formats) == 2, formats


def test_skill_reach_keys_yaml_reads_as_bool_or_int_are_linted_not_a_crash(tmp_path):
    _write(tmp_path / "q.yaml", "yes: 1\nqueries:\n- query_id: q1\n  text: hi\n  expected_skill: s\n"
                                "  on: x\n  2: y\n")
    _jsonl(tmp_path / "ok" / "s.jsonl", [{"input": "q", "target": "a"}])
    reports = {r.path.name: r for r in inspect_path(tmp_path)}
    assert "QuerySet keys skill-reach refuses: True" in reports["q.yaml"].problems
    assert "q1: keys skill-reach refuses: 2, True" in reports["q.yaml"].problems
    assert reports["s.jsonl"].format == "inspect-samples"


def test_cli_inspect_out_writes_yaml_dates_sets_and_nan_as_plain_json(tmp_path, capsys):
    task = _tb1(tmp_path)
    _write(task / "task.yaml", "instruction: do it\ndifficulty: 2024-01-01\n"
                               "tags: !!set {b: null, a: null}\ncategory: .nan\n")
    out = tmp_path / "report.json"
    _run(["tasks", "inspect", str(tmp_path), "--json", "--out", str(out)])
    printed = json.loads(capsys.readouterr().out)

    def refuse(name):
        raise ValueError(name)

    written = json.loads(out.read_text(), parse_constant=refuse)
    assert written == printed
    meta = written["items"][0]["details"]["metadata"]
    assert (meta["difficulty"], meta["tags"], meta["category"]) == ("2024-01-01", ["a", "b"], None)
