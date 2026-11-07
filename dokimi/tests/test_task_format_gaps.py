# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Format gaps closed in the importers: custom Inspect scorers judged on their docstring, the
optional ``hf_dataset`` loader, promptfoo configs written as code (read by node), the Harbor
``suites.json`` manifest, and an OpenAI model-graded spec's own prompt as the criterion.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import types
from pathlib import Path

import pytest

from dokimi.eval.reference_checks import grade
from dokimi.task_formats.detect import detect
from dokimi.task_formats.imports import import_path

CORPUS = Path(__file__).parent / "fixtures" / "task_formats"


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _jsonl(path: Path, rows: list) -> Path:
    return _write(path, "".join(json.dumps(r) + "\n" for r in rows))


def _checks(imp, i=0):
    return imp.questions[i]["grading"]["checks"]


_SCORER_TASK = (
    "@scorer(metrics=[accuracy()])\n"
    "def {name}():\n"
    "    {doc}"
    "    async def score(state, target):\n"
    "        ...\n"
    "    return score\n\n"
    "@task\n"
    "def t():\n"
    "    return Task(dataset=[Sample(input='2+2?', target='4')], scorer={name}())\n"
)


# --- (a) custom Inspect scorers -------------------------------------------------------------


def test_custom_scorer_with_a_docstring_is_judged_on_it(tmp_path):
    task = _write(tmp_path / "t.py", _SCORER_TASK.format(
        name="concise", doc='"""Passes an answer that states the target\n    in under ten words."""\n'))
    imp = import_path(task, fmt="inspect-task")
    assert _checks(imp) == [{"type": "judge",
                             "criterion": "Passes an answer that states the target in under ten words.",
                             "reference": "4", "native": "concise"}]
    assert imp.questions[0]["grading"]["native_scorer"] == "concise"
    assert imp.questions[0]["metadata"]["native_scorer"] == "concise"
    assert "scorer `concise` approximated by a judge on its docstring" in imp.lossy
    # Without a judge model the check stays unscored, not passed.
    assert grade(imp.questions[0]["grading"], "2+2?", "4").passed is None


def test_custom_scorer_without_a_docstring_stays_unsupported(tmp_path):
    task = _write(tmp_path / "t.py", _SCORER_TASK.format(name="bare", doc="pass\n"))
    imp = import_path(task, fmt="inspect-task")
    assert _checks(imp) == [{"type": "unsupported", "native": "bare"}]
    assert "scorer `bare` has no translation" in imp.lossy


def test_documented_function_without_the_scorer_decorator_stays_unsupported(tmp_path):
    text = _SCORER_TASK.replace("@scorer(metrics=[accuracy()])\n", "")
    task = _write(tmp_path / "t.py", text.format(name="helper", doc='"""Exact match on the number."""\n'))
    imp = import_path(task, fmt="inspect-task")
    assert _checks(imp) == [{"type": "unsupported", "native": "helper"}]
    assert "scorer `helper` has no translation" in imp.lossy


@pytest.mark.parametrize("sample", ["Sample(input='2+2?', target='')", "Sample(input='2+2?')"])
def test_custom_scorer_row_without_a_target_is_not_graded(tmp_path, sample):
    text = _SCORER_TASK.replace("Sample(input='2+2?', target='4')", sample)
    task = _write(tmp_path / "t.py", text.format(name="last", doc='"""The last number equals the target."""\n'))
    imp = import_path(task, fmt="inspect-task")
    assert _checks(imp) == [{"type": "unsupported", "native": "last"}]
    assert "sample without a usable target: not graded" in imp.lossy


# --- (b) hf_dataset ---------------------------------------------------------------------------


def test_hf_dataset_without_the_optional_package_says_how_to_get_it(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "datasets", None)  # `import datasets` now fails
    _jsonl(tmp_path / "s.jsonl", [{"input": "2+2?", "target": "4"}])
    task = _write(tmp_path / "t.py", (
        "@task\ndef t():\n"
        "    return Task(dataset=hf_dataset('json', data_files='s.jsonl', split='train'), scorer=match())\n"))
    with pytest.raises(ValueError, match="hf_dataset needs the `datasets` package"):
        import_path(task, fmt="inspect-task")


def test_hf_dataset_rows_map_through_the_field_spec_offline(tmp_path):
    pytest.importorskip("datasets")
    _jsonl(tmp_path / "s.jsonl", [{"question": "2+2?", "answer": "4"}])
    task = _write(tmp_path / "t.py", (
        "@task\ndef t():\n"
        "    return Task(dataset=hf_dataset('json', data_files='s.jsonl', split='train',\n"
        "                                   sample_fields=FieldSpec(input='question', target='answer')),\n"
        "                scorer=match())\n"))
    imp = import_path(task, fmt="inspect-task")
    assert imp.questions[0]["question"] == "2+2?"
    assert _checks(imp)[0]["values"] == ["4"]


def test_hf_dataset_refuses_trust_remote_code_before_loading(tmp_path):
    task = _write(tmp_path / "t.py", (
        "@task\ndef t():\n"
        "    return Task(dataset=hf_dataset('org/repo', split='train', trust_remote_code=True), scorer=match())\n"))
    with pytest.raises(ValueError, match="trust_remote_code= would run"):
        import_path(task, fmt="inspect-task")


def test_hf_dataset_data_files_outside_the_task_are_refused(tmp_path):
    pytest.importorskip("datasets")
    task = _write(tmp_path / "d" / "t.py", (
        "@task\ndef t():\n"
        "    return Task(dataset=hf_dataset('json', data_files='../outside.jsonl', split='train'), scorer=match())\n"))
    with pytest.raises(ValueError, match="outside the task's directory"):
        import_path(task, fmt="inspect-task")


def _linked_task(tmp_path: Path, args: str) -> Path:
    secret = _jsonl(tmp_path / "outside" / "secret.jsonl", [{"input": "SECRET?", "target": "x"}])
    _jsonl(tmp_path / "task" / "own.jsonl", [{"input": "ok?", "target": "x"}])
    _jsonl(tmp_path / "task" / "ok" / "a.jsonl", [{"input": "fine?", "target": "x"}])
    (tmp_path / "task" / "d").mkdir()
    try:
        os.symlink(secret, tmp_path / "task" / "leak.jsonl")
        os.symlink(secret, tmp_path / "task" / "d" / "leak.jsonl")
        os.symlink(secret.parent, tmp_path / "task" / "sub", target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are not supported here")
    return _write(tmp_path / "task" / "t.py", (
        f"@task\ndef t():\n    return Task(dataset=hf_dataset('json', {args}, split='train'), scorer=match())\n"))


@pytest.mark.parametrize("args", ["data_files='leak.jsonl'", "data_files='*.jsonl'", "data_files=['lea?.jsonl']",
                                  "data_files='d/**/*.jsonl'", "data_dir='d'", "data_dir='d', data_files='*.jsonl'"])
def test_hf_dataset_files_the_loader_expands_through_a_symlink_out_of_the_task_are_refused(tmp_path, args):
    pytest.importorskip("datasets")
    with pytest.raises(ValueError, match="outside the task's directory"):
        import_path(_linked_task(tmp_path, args), fmt="inspect-task")


@pytest.mark.parametrize("args", ["data_dir='ok'", "data_files='ok/*.jsonl'"])
def test_hf_dataset_glob_and_data_dir_inside_the_task_still_read(tmp_path, args):
    pytest.importorskip("datasets")
    imp = import_path(_linked_task(tmp_path, args), fmt="inspect-task")
    assert [q["question"] for q in imp.questions] == ["fine?"]


@pytest.mark.parametrize("spec", ["FieldSpec(input=COL, target='answer')", "FieldSpec('question', target='answer')",
                                  "FieldSpec(**{'input': 'question'})"])
def test_hf_dataset_refuses_a_computed_field_spec_before_loading(tmp_path, spec):
    task = _write(tmp_path / "t.py", (
        "COL = 'question'\n"
        "@task\ndef t():\n"
        f"    return Task(dataset=hf_dataset('json', data_files='s.jsonl', split='train', sample_fields={spec}),\n"
        "                scorer=match())\n"))
    with pytest.raises(ValueError, match="FieldSpec.*literal keyword"):
        import_path(task, fmt="inspect-task")


def _capture_hf_load(monkeypatch):
    """Replace the loader (no network, no datasets package) and record the arguments it is given."""
    from dokimi.task_formats.imports import inspect_ai
    seen = []
    rows = [{"input": "2+2?", "target": "4"}]
    monkeypatch.setattr(inspect_ai, "_hf_load", lambda args, kwargs: seen.append((args, kwargs)) or rows)
    return seen


def test_hf_dataset_hub_name_and_its_data_dir_are_left_as_they_are(tmp_path, monkeypatch):
    seen = _capture_hf_load(monkeypatch)
    (tmp_path / "d" / "en").mkdir(parents=True)  # a task folder named like the hub subfolder must not capture it
    task = _write(tmp_path / "d" / "t.py", (
        "@task\ndef t():\n"
        "    return Task(dataset=hf_dataset('org/ds', data_dir='en', split='train'), scorer=match())\n"))
    import_path(task, fmt="inspect-task")
    assert seen == [(["org/ds"], {"data_dir": "en", "split": "train"})]


def test_hf_dataset_builder_name_is_not_a_task_path(tmp_path, monkeypatch):
    seen = _capture_hf_load(monkeypatch)
    (tmp_path / "d" / "json").mkdir(parents=True)
    task = _write(tmp_path / "d" / "t.py", (
        "@task\ndef t():\n"
        "    return Task(dataset=hf_dataset('json', data_dir='sub', split='train'), scorer=match())\n"))
    import_path(task, fmt="inspect-task")
    assert seen[0][0] == ["json"]
    assert seen[0][1]["data_dir"] == str((tmp_path / "d" / "sub").resolve())


@pytest.mark.parametrize("call", [
    "hf_dataset('data', split='train')",
    "hf_dataset('data', data_dir='en', split='train')",
    "hf_dataset('data', data_files='s.jsonl', split='train')",
    "hf_dataset('secrets', split='train')",
])
def test_hf_dataset_local_dataset_folder_is_refused_not_read(tmp_path, monkeypatch, call):
    seen = _capture_hf_load(monkeypatch)
    (tmp_path / "d" / "data" / "en").mkdir(parents=True)
    (tmp_path / "cwd" / "secrets").mkdir(parents=True)
    monkeypatch.chdir(tmp_path / "cwd")  # a name that exists only in the working directory is local too
    task = _write(tmp_path / "d" / "t.py", f"@task\ndef t():\n    return Task(dataset={call}, scorer=match())\n")
    with pytest.raises(ValueError, match="is not read"):
        import_path(task, fmt="inspect-task")
    assert seen == []


def test_hf_dataset_data_files_with_a_data_dir_are_read_from_that_folder(tmp_path, monkeypatch):
    seen = _capture_hf_load(monkeypatch)
    (tmp_path / "d" / "en").mkdir(parents=True)
    task = _write(tmp_path / "d" / "t.py", (
        "@task\ndef t():\n"
        "    return Task(dataset=hf_dataset('json', data_dir='en', data_files='s.jsonl', split='train'), scorer=match())\n"))
    import_path(task, fmt="inspect-task")
    assert seen[0][1]["data_files"] == str((tmp_path / "d" / "en" / "s.jsonl").resolve())


def test_hf_dataset_path_and_data_dir_outside_the_task_are_refused(tmp_path):
    (tmp_path / "outside").mkdir()
    _jsonl(tmp_path / "outside" / "train.jsonl", [{"question": "SECRET?", "answer": "x"}])
    for call in (f"hf_dataset({str(tmp_path / 'outside')!r}, split='train')",
                 f"hf_dataset('json', data_dir={str(tmp_path / 'outside')!r}, split='train')",
                 "hf_dataset('json', data_dir='../outside', split='train')",
                 "hf_dataset('../outside', split='train')"):
        task = _write(tmp_path / "d" / "t.py", f"@task\ndef t():\n    return Task(dataset={call}, scorer=match())\n")
        with pytest.raises(ValueError, match="is not read|outside the task's directory"):
            import_path(task, fmt="inspect-task")



# --- (c) promptfoo configs written as code ----------------------------------------------------


def test_promptfoo_code_config_is_detected_by_name_and_not_run_by_inspect():
    report = detect(CORPUS / "promptfoo" / "code-config" / "promptfooconfig.mjs")
    assert report.format == "promptfoo"
    assert report.details["code_config"].startswith("read by node at import")


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_promptfoo_code_config_is_read_by_node(tmp_path):
    imp = import_path(CORPUS / "promptfoo" / "code-config" / "promptfooconfig.mjs", fmt="promptfoo")
    assert [q["question"] for q in imp.questions] == [
        "Answer in one word: What is the capital of France?",
        "Answer in one word: What is the capital of Japan?",
    ]
    assert [c["values"] for c in _checks(imp)] == [["Paris"]]
    assert [c["values"] for c in _checks(imp, 1)] == [["tokyo"]]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_promptfoo_code_config_that_throws_names_the_error(tmp_path):
    config = _write(tmp_path / "promptfooconfig.mjs", "throw new Error('no config here');\n")
    with pytest.raises(ValueError, match="node could not read the config.*no config here"):
        import_path(config, fmt="promptfoo")


def test_promptfoo_code_config_without_node_refuses_with_a_clear_message(tmp_path, monkeypatch):
    config = _write(tmp_path / "promptfooconfig.ts", "export default { prompts: [] };\n")
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: None)
    with pytest.raises(ValueError, match="read with node, which is not on PATH"):
        import_path(config, fmt="promptfoo")


# --- (d) Harbor suites.json -------------------------------------------------------------------


def test_suites_manifest_with_the_wrong_shape_is_a_problem(tmp_path):
    report = detect(_write(tmp_path / "evaluation" / "harbor" / "suites.json", '{"suites": 5}'))
    assert report.format == "harbor-suites"
    assert report.problems == ["suites must be a list of objects"]
    assert report.consumers == ["catalog"]


def test_suites_manifest_counts_the_tasks_it_names(tmp_path):
    report = detect(_write(tmp_path / "evaluation" / "harbor" / "suites.json", json.dumps(
        {"suites": [{"skill": "a", "tasks": [{"name": "x"}, {"name": "y"}]}, {"skill": "b"}]})))
    assert (report.format, report.items, report.problems) == ("harbor-suites", 2, [])


# --- OpenAI evals: a model-graded spec's own prompt --------------------------------------------


def test_openai_modelgraded_spec_prompt_is_the_criterion_when_there_is_no_ideal(tmp_path):
    reg = tmp_path / "registry"
    _write(reg / "modelgraded" / "fact-lite.yaml",
           "fact-lite:\n  prompt: |-\n    Grade whether [Submission]: {completion} agrees with [Question]: {input}.\n"
           "  choice_strings: AB\n")
    grader = _write(reg / "evals" / "lite.yaml",
                    "lite:\n  id: lite\n  class: modelgraded.classify:ModelBasedClassify\n"
                    "  args:\n    modelgraded_spec: fact-lite\n")
    data = _jsonl(tmp_path / "data" / "s.jsonl", [{"input": "What is 2+2?"}])
    imp = import_path(data, fmt="openai-evals", grader=str(grader))
    assert _checks(imp) == [{"type": "judge",
                             "criterion": "Grade whether [Submission]: the answer agrees with [Question]: the question.",
                             "native": "modelgraded:fact-lite"}]


def test_openai_modelgraded_spec_name_with_a_path_is_not_read(tmp_path):
    # The registry file one level up would be read if the spec name were joined to the path unchecked.
    reg = tmp_path / "registry"
    (reg / "modelgraded").mkdir(parents=True)
    # The key is the spec name as written, so only the joined path can find this prompt.
    _write(reg / "x.yaml", "'../x':\n  prompt: |-\n    Grade whether [Submission]: {completion} agrees with [Question]: {input}.\n"
                           "  choice_strings: AB\n")
    grader = _write(reg / "evals" / "lite.yaml",
                    "lite:\n  id: lite\n  class: modelgraded.classify:ModelBasedClassify\n"
                    "  args:\n    modelgraded_spec: ../x\n")
    data = _jsonl(tmp_path / "data" / "s.jsonl", [{"input": "What is 2+2?"}])
    imp = import_path(data, fmt="openai-evals", grader=str(grader))
    assert _checks(imp) == [{"type": "unsupported", "native": "modelgraded:../x"}]


def test_harbor_suites_manifest_with_a_non_object_suite_reports_it(tmp_path):
    manifest = _write(tmp_path / "evaluation" / "harbor" / "suites.json", '{"suites": [1]}\n')
    report = detect(manifest)
    assert report.format == "harbor-suites"
    assert "suites must be a list of objects" in report.problems


# --- review of #402: the importers' edge cases -------------------------------------------------


def test_hf_dataset_data_files_collections_outside_the_task_are_refused(tmp_path):
    pytest.importorskip("datasets")
    for files in ("['/tmp/outside.jsonl']", "{'train': ['/tmp/outside.jsonl']}", "['../outside.jsonl']"):
        task = _write(tmp_path / "d" / "t.py", (
            "@task\ndef t():\n"
            f"    return Task(dataset=hf_dataset('json', data_files={files}, split='train'), scorer=match())\n"))
        with pytest.raises(ValueError, match="outside the task's directory"):
            import_path(task, fmt="inspect-task")


def test_hf_dataset_data_files_collection_inside_the_task_loads_relative_to_it(tmp_path):
    pytest.importorskip("datasets")
    _jsonl(tmp_path / "data" / "s.jsonl", [{"question": "2+2?", "answer": "4"}])
    task = _write(tmp_path / "t.py", (
        "@task\ndef t():\n"
        "    return Task(dataset=hf_dataset('json', data_files={'train': ['data/s.jsonl']}, split='train',\n"
        "                                   sample_fields=FieldSpec(input='question', target='answer')),\n"
        "                scorer=match())\n"))
    imp = import_path(task, fmt="inspect-task")
    assert imp.questions[0]["question"] == "2+2?"


def test_hf_dataset_refuses_a_sample_fields_converter_it_cannot_read(tmp_path):
    task = _write(tmp_path / "t.py", (
        "@task\ndef t():\n"
        "    return Task(dataset=hf_dataset('json', data_files='s.jsonl', split='train', sample_fields=to_sample),\n"
        "                scorer=match())\n"))
    with pytest.raises(ValueError, match="sample_fields= must be a FieldSpec"):
        import_path(task, fmt="inspect-task")


def test_hf_dataset_accepts_the_path_as_a_keyword(tmp_path):
    pytest.importorskip("datasets")
    _jsonl(tmp_path / "s.jsonl", [{"question": "2+2?", "answer": "4"}])
    task = _write(tmp_path / "t.py", (
        "@task\ndef t():\n"
        "    return Task(dataset=hf_dataset(path='json', data_files='s.jsonl', split='train',\n"
        "                                   sample_fields=FieldSpec(input='question', target='answer')),\n"
        "                scorer=match())\n"))
    imp = import_path(task, fmt="inspect-task")
    assert imp.questions[0]["question"] == "2+2?"


def test_hf_dataset_limit_applies_to_the_import_and_is_not_a_loader_argument(tmp_path):
    pytest.importorskip("datasets")
    _jsonl(tmp_path / "s.jsonl", [{"question": f"q{i}", "answer": "a"} for i in range(3)])
    task = _write(tmp_path / "t.py", (
        "@task\ndef t():\n"
        "    return Task(dataset=hf_dataset('json', data_files='s.jsonl', split='train', limit=2,\n"
        "                                   sample_fields=FieldSpec(input='question', target='answer')),\n"
        "                scorer=match())\n"))
    imp = import_path(task, fmt="inspect-task")
    assert [q["question"] for q in imp.questions] == ["q0", "q1"]


def test_hf_dataset_stops_reading_the_split_at_the_limit(tmp_path, monkeypatch):
    def rows():  # a stream that fails if the importer asks for a row past the limit
        yield {"question": "q0", "answer": "a"}
        yield {"question": "q1", "answer": "a"}
        raise AssertionError("read past limit=2")

    fake = types.ModuleType("datasets")
    fake.load_dataset = lambda *args, **kwargs: rows()
    monkeypatch.setitem(sys.modules, "datasets", fake)
    task = _write(tmp_path / "t.py", (
        "@task\ndef t():\n"
        "    return Task(dataset=hf_dataset('org/big', split='train', streaming=True, limit=2,\n"
        "                                   sample_fields=FieldSpec(input='question', target='answer')),\n"
        "                scorer=match())\n"))
    imp = import_path(task, fmt="inspect-task")
    assert [q["question"] for q in imp.questions] == ["q0", "q1"]


def test_hf_dataset_refuses_a_computed_loader_argument_instead_of_forwarding_none(tmp_path, monkeypatch):
    calls = []
    fake = types.ModuleType("datasets")
    fake.load_dataset = lambda *args, **kwargs: calls.append(kwargs) or []
    monkeypatch.setitem(sys.modules, "datasets", fake)
    task = _write(tmp_path / "t.py", (
        "@task\ndef t():\n"
        "    return Task(dataset=hf_dataset('org/repo', split='train', revision=REVISION), scorer=match())\n"))
    with pytest.raises(ValueError, match="revision= must be a literal"):
        import_path(task, fmt="inspect-task")
    assert calls == []  # the loader was never asked for the default revision


# --- review of #402: promptfoo code configs ----------------------------------------------------


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_promptfoo_code_config_that_logs_on_import_still_reads(tmp_path):
    config = _write(tmp_path / "promptfooconfig.mjs", (
        "console.log('loading');\n"
        "process.stdout.write('module output\\n');\n"
        "export default { prompts: ['hi'], tests: [{ vars: {}, assert: [{ type: 'contains', value: 'hi' }] }] };\n"))
    imp = import_path(config, fmt="promptfoo")
    assert len(imp.questions) == 1
    assert _checks(imp)[0]["values"] == ["hi"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_promptfoo_code_config_keeps_a_large_prompt_whole(tmp_path):
    config = _write(tmp_path / "promptfooconfig.mjs", (
        "export default { prompts: ['x'.repeat(2 * 1024 * 1024)],\n"
        "  tests: [{ vars: {}, assert: [{ type: 'contains', value: 'x' }] }] };\n"))
    imp = import_path(config, fmt="promptfoo")
    assert len(imp.questions) == 1
    assert len(imp.questions[0]["question"]) >= 2 * 1024 * 1024


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_promptfoo_code_config_function_transform_is_flagged_not_dropped(tmp_path):
    config = _write(tmp_path / "promptfooconfig.mjs", (
        "export default { prompts: ['hi'], tests: [{ vars: {}, assert: [\n"
        "  { type: 'equals', value: 'Paris', transform: (output) => output.trim() }] }] };\n"))
    imp = import_path(config, fmt="promptfoo")
    assert imp.lossy["assert transform not applied"] == 1


def test_hf_dataset_limit_must_be_a_literal_count(tmp_path):
    task = _write(tmp_path / "t.py", (
        "@task\ndef t():\n"
        "    return Task(dataset=hf_dataset('json', data_files='s.jsonl', split='train', limit=N), scorer=match())\n"))
    with pytest.raises(ValueError, match="limit= must be a literal count"):
        import_path(task, fmt="inspect-task")


@pytest.mark.skipif(os.name == "nt", reason="needs a POSIX shell script as a stand-in node")
def test_promptfoo_typescript_config_on_an_old_node_says_what_it_needs(tmp_path, monkeypatch):
    fake = _write(tmp_path / "bin" / "node", (
        "#!/bin/sh\n"
        "for a in \"$@\"; do case \"$a\" in --experimental-strip-types) echo 'node: bad option: --experimental-strip-types' >&2; exit 9;; esac; done\n"
        "exit 0\n"))
    fake.chmod(0o755)
    config = _write(tmp_path / "promptfooconfig.ts", "export default { prompts: [] };\n")
    real_which = shutil.which
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: str(fake) if name == "node" else real_which(name, *a, **k))
    with pytest.raises(ValueError, match="needs Node 22.6 or later"):
        import_path(config, fmt="promptfoo")


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_promptfoo_typescript_config_is_read_by_node(tmp_path):
    import subprocess
    # The flag alone is not enough: a node without type stripping can accept it and still refuse a .ts file.
    probe_file = _write(tmp_path / "probe.ts", "const n: number = 1;\nconsole.log(n);\n")
    probe = subprocess.run(["node", "--experimental-strip-types", str(probe_file)], capture_output=True)
    if probe.returncode != 0:
        pytest.skip("node cannot load a .ts file (older than 22.6, or built without type stripping)")
    config = _write(tmp_path / "promptfooconfig.ts", (
        "type Cfg = { prompts: string[] };\n"
        "const cfg: Cfg = { prompts: ['hi'], tests: [{ vars: {}, assert: [{ type: 'contains', value: 'hi' }] }] };\n"
        "export default cfg;\n"))
    imp = import_path(config, fmt="promptfoo")
    assert len(imp.questions) == 1


# --- coverage of the refusals this PR adds (codecov on 2ced76d1) -------------------------------


def test_hf_dataset_refuses_a_star_star_options_argument(tmp_path):
    task = _write(tmp_path / "t.py", (
        "@task\ndef t():\n"
        "    return Task(dataset=hf_dataset('json', data_files='s.jsonl', split='train', **OPTIONS), scorer=match())\n"))
    with pytest.raises(ValueError, match=r"\*\*options is not read"):
        import_path(task, fmt="inspect-task")


def test_hf_dataset_refuses_a_data_files_value_that_is_not_a_path(tmp_path):
    task = _write(tmp_path / "t.py", (
        "@task\ndef t():\n"
        "    return Task(dataset=hf_dataset('json', data_files=5, split='train'), scorer=match())\n"))
    with pytest.raises(ValueError, match="data_files must be literal paths"):
        import_path(task, fmt="inspect-task")


def test_hf_dataset_refuses_a_loader_call_without_a_split(tmp_path):
    task = _write(tmp_path / "t.py", (
        "@task\ndef t():\n"
        "    return Task(dataset=hf_dataset('json', data_files='s.jsonl'), scorer=match())\n"))
    with pytest.raises(ValueError, match="the path and split= must be literal strings"):
        import_path(task, fmt="inspect-task")


def test_hf_dataset_names_the_loaders_own_error(tmp_path, monkeypatch):
    def load(*args, **kwargs):
        raise RuntimeError("no such split")

    fake = types.ModuleType("datasets")
    fake.load_dataset = load
    monkeypatch.setitem(sys.modules, "datasets", fake)
    task = _write(tmp_path / "t.py", (
        "@task\ndef t():\n"
        "    return Task(dataset=hf_dataset('org/repo', split='train'), scorer=match())\n"))
    with pytest.raises(ValueError, match="hf_dataset could not load org/repo: no such split"):
        import_path(task, fmt="inspect-task")


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_promptfoo_code_config_that_writes_no_json_is_refused(tmp_path):
    config = _write(tmp_path / "promptfooconfig.mjs", "process.exit(0);\n")
    with pytest.raises(ValueError, match="node wrote no JSON for the config"):
        import_path(config, fmt="promptfoo")


def test_promptfoo_code_config_without_node_is_a_problem_in_detect(tmp_path, monkeypatch):
    config = _write(tmp_path / "promptfooconfig.mjs", "export default {};\n")
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: None)
    report = detect(config)
    assert report.format == "promptfoo"
    assert "a JS/TS promptfoo config is read by node, which is not on PATH" in report.problems


def test_suites_manifest_whose_tasks_are_not_a_list_is_a_problem(tmp_path):
    report = detect(_write(tmp_path / "evaluation" / "harbor" / "suites.json", json.dumps(
        {"suites": [{"skill": "a", "tasks": 5}]})))
    assert report.problems == ["each suite's tasks must be a list of objects"]


@pytest.mark.parametrize("body", ["  prompt: '   '\n", ""], ids=["blank-prompt", "no-prompt"])
def test_openai_modelgraded_spec_without_a_usable_prompt_gives_no_criterion(tmp_path, body):
    reg = tmp_path / "registry"
    _write(reg / "modelgraded" / "fact-blank.yaml", "fact-blank:\n" + body + "  choice_strings: AB\n")
    grader = _write(reg / "evals" / "blank.yaml",
                    "blank:\n  id: blank\n  class: modelgraded.classify:ModelBasedClassify\n"
                    "  args:\n    modelgraded_spec: fact-blank\n")
    data = _jsonl(tmp_path / "data" / "s.jsonl", [{"input": "What is 2+2?"}])
    imp = import_path(data, fmt="openai-evals", grader=str(grader))
    assert _checks(imp) == [{"type": "unsupported", "native": "modelgraded:fact-blank"}]


# --- round 2 of review: Inspect's own hf_dataset options, local paths, docstrings, spec slots ----


def _hf_task(tmp_path, call):
    return _write(tmp_path / "d" / "t.py", f"@task\ndef t():\n    return Task(dataset={call}, scorer=match())\n")


def test_hf_dataset_builder_without_files_is_refused_not_read_from_the_cwd(tmp_path, monkeypatch):
    # Thread PRRT_kwDOVBg7g860Mhbp
    seen = _capture_hf_load(monkeypatch)
    elsewhere = tmp_path / "cwd"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    with pytest.raises(ValueError, match="names no data_files= or data_dir="):
        import_path(_hf_task(tmp_path, "hf_dataset('json', split='train')"), fmt="inspect-task")
    assert seen == []


@pytest.mark.parametrize("opts", ["shuffle=True, seed=3", "auto_id=True", "shuffle_choices=True", "cached=False",
                                  "trust=False"])
def test_hf_dataset_inspect_only_options_are_not_loader_arguments(tmp_path, monkeypatch, opts):
    # Thread PRRT_kwDOVBg7g860Mhbv
    seen = _capture_hf_load(monkeypatch)
    imp = import_path(_hf_task(tmp_path, f"hf_dataset('json', data_files='s.jsonl', split='train', {opts})"),
                      fmt="inspect-task")
    assert set(seen[0][1]) == {"split", "data_files"}
    if "shuffle" in opts:
        assert any("shuffle" in n for n in imp.lossy)


def test_hf_dataset_positional_split_and_trust_true(tmp_path, monkeypatch):
    # Thread PRRT_kwDOVBg7g860Mhbv
    seen = _capture_hf_load(monkeypatch)
    import_path(_hf_task(tmp_path, "hf_dataset('json', 'train', data_files='s.jsonl')"), fmt="inspect-task")
    assert seen[0][0] == ["json"] and seen[0][1]["split"] == "train"
    with pytest.raises(ValueError, match="trust=True or trust_remote_code= would run"):
        import_path(_hf_task(tmp_path, "hf_dataset('org/ds', split='train', trust=True)"), fmt="inspect-task")


def test_hf_dataset_url_and_hub_data_files_are_left_as_they_are(tmp_path, monkeypatch):
    # Thread PRRT_kwDOVBg7g860Mhb0
    seen = _capture_hf_load(monkeypatch)
    import_path(_hf_task(tmp_path, "hf_dataset('json', data_files='https://host/x.jsonl', split='train')"),
                fmt="inspect-task")
    import_path(_hf_task(tmp_path, "hf_dataset('org/ds', data_files={'test': 'data/test-*.parquet'}, split='test')"),
                fmt="inspect-task")
    assert seen[0][1]["data_files"] == "https://host/x.jsonl"
    assert seen[1][1]["data_files"] == {"test": "data/test-*.parquet"}
    with pytest.raises(ValueError, match="outside the task's directory"):
        import_path(_hf_task(tmp_path, "hf_dataset('org/ds', data_files='../x.jsonl', split='test')"),
                    fmt="inspect-task")
    for url in ("file:///etc/passwd", "zip://x.zip", "https://h/x::file:///etc/passwd"):
        with pytest.raises(ValueError, match="path, which is not read"):
            import_path(_hf_task(tmp_path, f"hf_dataset('json', data_files='{url}', split='train')"), fmt="inspect-task")


def test_hf_dataset_builders_come_from_the_installed_datasets(tmp_path, monkeypatch):
    # Thread PRRT_kwDOVBg7g860MhcD
    pytest.importorskip("datasets")
    from datasets.packaged_modules import _PACKAGED_DATASETS_MODULES
    from dokimi.task_formats.imports import inspect_ai
    monkeypatch.setitem(_PACKAGED_DATASETS_MODULES, "fasta-like", ("x", ""))
    assert "fasta-like" in inspect_ai._hf_builders()
    seen = _capture_hf_load(monkeypatch)
    monkeypatch.chdir(tmp_path)
    import_path(_hf_task(tmp_path, "hf_dataset('fasta-like', data_dir='seqs', split='train')"), fmt="inspect-task")
    assert seen[0][1]["data_dir"] == str((tmp_path / "d" / "seqs").resolve())


def test_custom_scorer_criterion_is_the_docstrings_first_paragraph(tmp_path):
    # Thread PRRT_kwDOVBg7g860MhcI
    doc = ('"""Scorer for GSM8K.\n\n    Args:\n        tolerance: absolute tolerance.\n\n'
           '    Returns:\n        Scorer: a scorer.\n    """\n')
    imp = import_path(_write(tmp_path / "t.py", _SCORER_TASK.format(name="gsm", doc=doc)), fmt="inspect-task")
    assert _checks(imp)[0]["criterion"] == "Scorer for GSM8K."
    doc = '"""Grades the number.\n    Args:\n        tolerance: absolute tolerance.\n    """\n'
    imp = import_path(_write(tmp_path / "u.py", _SCORER_TASK.format(name="g2", doc=doc)), fmt="inspect-task")
    assert _checks(imp)[0]["criterion"] == "Grades the number."


def test_suites_json_outside_evaluation_harbor_is_not_a_harbor_manifest(tmp_path):
    # Thread PRRT_kwDOVBg7g860MhcO
    report = detect(_write(tmp_path / "tests" / "suites.json", '{"suites": []}'))
    assert report is None or report.format != "harbor-suites"


def test_field_spec_column_that_is_not_a_string_is_noted(tmp_path, monkeypatch):
    # Thread PRRT_kwDOVBg7g860MhcS
    _capture_hf_load(monkeypatch)
    imp = import_path(_hf_task(tmp_path, "hf_dataset('json', data_files='s.jsonl', split='train', "
                                         "sample_fields=FieldSpec(input='input', metadata=['level', 'type']))"),
                      fmt="inspect-task")
    assert any("FieldSpec metadata=" in n for n in imp.lossy)


@pytest.mark.parametrize("prompt", ["Compare {completion} with [Expert]: {ideal}.",
                                    "Does {completion} meet {criteria}?", "Grade {completion} for {topic}."])
def test_openai_modelgraded_spec_prompt_with_an_unfilled_slot_is_not_used(tmp_path, prompt):
    # Thread PRRT_kwDOVBg7g860Mhb7
    reg = tmp_path / "registry"
    _write(reg / "modelgraded" / "fact.yaml", f"fact:\n  prompt: '{prompt}'\n  choice_strings: AB\n")
    grader = _write(reg / "evals" / "f.yaml", "f:\n  id: f\n  class: modelgraded.classify:ModelBasedClassify\n"
                                              "  args:\n    modelgraded_spec: fact\n")
    imp = import_path(_jsonl(tmp_path / "data" / "s.jsonl", [{"input": "What is 2+2?"}]),
                      fmt="openai-evals", grader=str(grader))
    assert _checks(imp) == [{"type": "unsupported", "native": "modelgraded:fact"}]


def test_hf_dataset_computed_positional_argument_is_refused(tmp_path, monkeypatch):
    # Thread PRRT_kwDOVBg7g860Mhbv
    seen = _capture_hf_load(monkeypatch)
    with pytest.raises(ValueError, match="name must be a literal value"):
        import_path(_hf_task(tmp_path, "hf_dataset('org/ds', 'test', CONFIG)"), fmt="inspect-task")
    assert seen == []
