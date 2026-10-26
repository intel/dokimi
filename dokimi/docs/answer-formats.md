---
depends_on:
  - dokimi/dokimi/task_formats/imports/**
  - dokimi/dokimi/eval/reference_checks.py
  - dokimi/dokimi/eval/reference_run.py
  - dokimi/dokimi/commands/answer_formats.py
---
<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Answer-only eval formats

Inspect AI, OpenAI evals, skill-creator `evals.json` and promptfoo sets run on
dokimi's answer track. There is no separate runner. `questions import`
translates a set into a questions.v2 file in which every question carries a
`grading` block of format-neutral checks. The usual commands then answer it
(`answers generate`, `arms run`), and `eval reference` scores the answers
against those checks.

```bash
python cli.py questions import evals/born_first.jsonl --grader registry/evals/born-first.yaml -o q.json
python cli.py answers generate --product born-first --questions q.json --doc-source none \
    --model claude-haiku-5-5 --provider amazon-bedrock --output answers.json
python cli.py eval reference --answers answers.json --questions q.json -o eval.json
```

`tasks inspect <path>` names the format, and `questions import` uses the same
detection. Use `--format` to override it. A file that fits several formats
(`tasks inspect` lists the others under `alternatives`) is refused until
`--format` names one.

## The canonical record

| Key | From |
|---|---|
| `question` | The prompt as the native tool sends it. Chat inputs are flattened to one prompt, with `[role]` headings when there is more than one turn. MCQ rows are rendered with Inspect's single-answer template, or its multiple-answer one under `multiple_choice(multiple_correct=True)`. |
| `prompt_style: verbatim` | The answerer sends `question` as it is, without its expert-answer wrapper, so that `ANSWER: X` and exact-match graders see a reply to the original prompt. A context arm, in `answers generate` or `arms run`, puts the docs first and the prompt after them. A multi-turn session arm still wraps its opening turn. |
| `expected_topics` | The reference answers, assertion values or expectations. `eval checklist` and the judge read them. |
| `grading.checks` | One check per native grader or assertion (below). Optional fields: `threshold`, `native_scorer`, `source_format`. |
| `expected_skill` | skill-creator only: the skill the `evals.json` belongs to (`skill_name`, else the `<skill>/evals/` directory), so the set also scores [skill selection](evaluating-treatments.md#skill-selection-does-the-agent-load-the-right-skill) on a `skill-catalog:` arm. |
| `metadata` | Sample metadata, promptfoo vars and prompt label, and the skill-creator `files`. |

Ids match the native tool's, so a native log joins on the same ids. An Inspect
sample keeps its `id`; samples without one are numbered from 1. A promptfoo
question is `<testIdx>` when the config has one prompt and
`<testIdx>.p<promptIdx>` when it has several. Other formats use the row's
`id` when it has one and its position otherwise.

## Checks

| Check | Semantics | Native graders mapped to it |
|---|---|---|
| `match` | The value at `location` (`begin`, `end`, `any`, `exact`), with optional `ignore_case`, `ignore_punctuation`, `strip`, `numeric` (Inspect `match_str`) | Inspect `match`, `includes`; OpenAI `Match` (case-sensitive `startswith`), `Includes`; promptfoo `contains*`, `icontains*`, `equals`, `starts-with` |
| `exact` | SQuAD-normalised equality | Inspect `exact` |
| `fuzzy` | Normalised containment, in either direction | OpenAI `FuzzyMatch` |
| `regex` | `regex.search`, with a 1 s limit per check; a check that runs past it is unscored, not failed | promptfoo `regex` |
| `choice` | The letters of the last `ANSWER: ...` line, parsed the way Inspect parses them | Inspect `choice` with `multiple_choice()` |
| `json` | Parses as JSON. With `values`, it must equal one of them. With `contains`, the answer may be any JSON value, or else it is the first JSON object or array anywhere in the answer. | OpenAI `JsonMatch`; promptfoo `is-json`, `contains-json`, `equals` with an object value |
| `judge` | One required point (`criterion`, `reference` or both), asked of the [checklist](benchmarking-and-comparison.md) judge in both polarities. 1 when the two agree that it is covered, 0 when they agree that it is not, 0.5 when they split. | Inspect `model_graded_qa`/`model_graded_fact`; OpenAI `ModelBasedClassify` specs; promptfoo `llm-rubric`, `g-eval`, `factuality`, `model-graded-closedqa`; every skill-creator expectation |
| `unsupported` | Listed with the native name and never counted | promptfoo `javascript`, `python`, `similar`, `cost`, `latency`, negated judge asserts (`not-llm-rubric`) and other asserts; Inspect `pattern` (it compares an extracted group with the target) and unknown Inspect scorers; a row whose `ideal` or `target` is blank or missing |

A blank value is not a check. A `match` value that is blank once stripped, or
an empty `regex` pattern, is found in every answer, so a row whose
`ideal`/`target` is blank imports as `unsupported`, and a hand-written `values`
list with such an entry is refused by `eval reference`. A promptfoo assert
whose value is blank (whitespace included) imports as `unsupported` too.

Every check accepts `mode` (`any`/`all` over its `values`), `negate` and
`weight`. A question's score is the weighted mean of the checks that were
counted. It passes when it reaches `threshold`; without a threshold, it passes
when every counted check passes. A failed answer call leaves its checks
unscored rather than failed. `--no-judge` leaves the `judge` checks unscored
and makes no model calls; an arms artifact written that way has `judge_model`
and `judge_provider` set to `reference`. The summary's `judge_unscored` counts the
counted answers whose pass leaves out a judge check that had no verdict. The
summary's `check_unscored` does the same for a deterministic check that did not
run to a result (a `regex` past its limit): the pass is decided on the checks
that ran, and the count shows how many passes left one out.

`eval reference` writes eval.v1 for an `answers generate` file and an arms
artifact for an `arms run` file. Each block carries `scorer: reference`, and
the run's pass rates and mean scores are under `run_metadata.summary` (or
`reference_scoring`).

## What does not carry over

| Format | Lossy |
|---|---|
| All | Only one message is sent: system prompts and few-shot turns are folded into the prompt. Tools, files and sandboxes are not provided. |
| Inspect `@task` | The file is parsed with `ast` and is never imported or run. It reads `Sample(...)` literals, `json_dataset("<file inside the task dir>", FieldSpec(...))`, the `multiple_choice()` (with `multiple_correct`) and `system_message("...")` solvers and the literal arguments of the first scorer. A computed dataset, `hf_dataset` or `csv_dataset` is refused. Other solvers (`chain_of_thought`, ...) are noted and not reproduced. |
| Inspect model-graded | The C/P/I grade becomes the checklist's covered/split/absent verdict. Inspect's grader template is not used. |
| OpenAI evals | The registry YAML (`--grader`) names the class. With no YAML, `match` is the default. Custom `evals.elsuite` classes are refused. `ModelBasedClassify` specs (`fact`, `closedqa`, ...) and their choice scores become one judge check against `ideal`. |
| skill-creator | The grader marks expectations against the run's transcript and output files. Here they are judged on the answer text. `files` are recorded but not attached. |
| promptfoo | `providers` are replaced by the answer track's model. Only `{{ var }}` substitution is done (filters, tags and `file://` vars are left as written). Prompt files must be text/JSON/YAML inside the config directory (`.js`/`.py` prompts are not run). Assert `transform`, `scenarios` and the schema of `is-json` are not applied. |

## Delegating to the native tool

When a set cannot be translated (a computed dataset, a custom scorer, a
tool-using solver), run it with its own CLI. Then read the log with
`eval native-import`:

```bash
inspect eval task.py --model bedrock/... --log-format json --log-dir logs
python cli.py eval native-import logs/<run>.json --answers-out native_answers.json --eval-out native_eval.json
promptfoo eval -c promptfooconfig.yaml -o out.json
python cli.py eval native-import out.json --answers-out native_answers.json --eval-out native_eval.json
```

The completions become an answers.v1 file. They are stored as `without_docs`,
because the native run had no dokimi doc context. The tool's own verdicts
become an eval.v1 file with `scorer: native:<tool>`. Running `eval reference`
on that answers file re-grades the same completions with dokimi's checks,
which shows where the two graders disagree. An Inspect sample that errored
keeps its error on the answer, so `eval reference` leaves it unscored, as
Inspect does. A promptfoo output must come from
a single provider. Supported log formats are Inspect `json` logs (inspect-ai
0.3.283) and promptfoo `-o *.json` (0.125.0).
