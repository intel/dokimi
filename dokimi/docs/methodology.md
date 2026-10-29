---
depends_on:
  - dokimi/dokimi/eval/judge.py
  - dokimi/dokimi/eval/panel.py
  - dokimi/dokimi/eval/rubric.py
  - dokimi/dokimi/gate/trust_gate.py
  - dokimi/dokimi/defaults.py
  - dokimi/dokimi/commands/benchmark.py
  - dokimi/dokimi/questions/artifact.py
  - dokimi/dokimi/schemas/questions.v*.json
---
<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Benchmark Methodology

This document describes how `dokimi` evaluates documentation and other agent-facing context. The central measurement is comparative: hold the task and answer model fixed, then compare performance with and without a controlled context layer.

## What Can Be Benchmarked

The current implementation focuses on product documentation, but the benchmark shape is intentionally broader:

- **Documentation**: Context7, local Markdown, URL-backed docs, and curated golden question sets.
- **Skills and playbooks**: reusable instructions or procedures that change how an agent approaches a task.
- **Agent profiles**: model/provider selection, system prompts, role definitions, tool policies, retrieval settings, and other runtime context.
- **Executable task environments**: Terminal-Bench/Harbor tasks where the final score comes from tests, not judge prose.

For non-documentation context, treat the artifact as the `with_context` condition and compare it against the same model/task without that artifact. Keep the question set, task set, model, temperature, tool availability, and judge setup fixed unless the experiment is explicitly about one of those variables.

## End-to-End LLM Evaluation

The high-level pipeline is:

```text
Library registry / CLI input
    -> persona discovery or cached personas (skipped with a golden set or --questions-from)
    -> topic extraction from docs
    -> question generation or fixed golden questions
    -> answer generation with_docs and without_docs
    -> LLM judge scoring
    -> optional panel/RAGAS checks
    -> trust gate, reports, dashboards, baselines
```

Use `benchmark run` for registered libraries:

```bash
python cli.py benchmark run \
  --library onetbb \
  --model gpt-6.1-sol \
  --judge-model claude-sonnet-5-5 \
  --judge-provider anthropic \
  --multi-run 3
```

Use `evaluate` for an ad hoc product:

```bash
python cli.py evaluate \
  --product oneDNN \
  --repo oneapi-src/oneDNN \
  --doc-source context7 \
  --context7-id intel/mkl-dnn
```

Outputs are written under `results/<library>/` or the requested `--output-dir`:

- `personas/<product>.json`: discovered or cached personas; not written when the
  questions come from a golden set or `--questions-from`.
- `questions/<product>.json`: generated, golden, manual, or reused questions.
- `answers/<product>.json`: paired `with_docs` and `without_docs` answers plus model/token metadata.
- `eval/<product>.json`: judge scores, deltas, diagnosis, and run metadata.
- `reports/<product>.md`: human-readable summary with weak spots and trust checks.

## Model Roles

The benchmark separates model responsibilities because self-judging can inflate scores.

- **Generator model**: creates personas and questions. Default `openai/gpt-6.1-sol` on every command.
- **Answer model**: answers every question in both conditions (`--model`). Default `openai/gpt-6.1-sol` on every command that has one; `tasks run`, `explore run`, `repo run`, `novelty routing` and `tools preference` have no default `--model`.
- **Judge model**: scores answers. It should differ from the answer model/provider for stronger evaluator independence. Default `anthropic/claude-sonnet-5-5` on every command.
- **Panel judges**: optional role-diverse judges for stricter review.

The defaults live in `dokimi/defaults.py` and changed on 2026-10-13; scores from earlier default-flag runs are not comparable with later ones — re-baseline ([Default models](benchmarking-and-comparison.md#default-models)).

`benchmark`, `eval score`, `eval panel-score` and `evaluate` warn when the normalized answer model and judge model match (`arms` and `subjects` do not check). Evaluation artifacts also store model/provider metadata and `question_set_hash` so later comparisons can prove they used the same question set.

Supported providers in the main pipeline include OpenAI, Anthropic, Google Vertex, OpenRouter, Amazon Bedrock, and OpenAI Codex where wired by the CLI.

## Question Sources

Questions can come from several sources:

- **Persona-based generated questions**: generated from discovered personas and extracted topics.
- **Chunk-grounded questions**: generated directly from documentation excerpts when a URL source is used; these carry `ground_truth_chunk`.
- **Golden questions**: curated regression sets such as `questions/onetbb_golden.json`, `questions/onedal_golden.json`, and `questions/onemkl_golden.json`.
- **Manual/custom questions**: passed through `--custom-questions`.
- **Reused questions**: loaded with `--questions-from` for fair multi-model comparisons.

Question records preserve source fields such as `source_type`, `question_source`, `persona`, `difficulty`, `topics`, `expected_topics`, and `ground_truth_chunk` when available.

### Question record format

A question set is a JSON object with a `questions` list. The framework writes it as `questions.v2` (`dokimi/schemas/questions.v2.json`, where every field is documented); `questions.v1` files are still read.

- **v2 (written):** each record needs `id` and a non-empty `question`. A v2 record may not also carry `text`, so no record holds two question texts that readers could disagree on.
- **v1 (still read):** the same record with the text under `text`. Curated golden sets in content repositories are v1, stamped `"schema_version": "questions.v1"` or unstamped; an unstamped file is read as whichever version it matches.

Every writer (`questions generate`, `questions refine`, the `evaluate`/`benchmark` pipeline's `questions/<product>.json`) renames `text` to `question` and validates the set against v2 before writing it; a set that fails is kept at `<name>.rejected`, before any answer is generated. The rename is the only change between the versions: `difficulty`, `metadata` and every other field are carried over as written, except a `null` the reader drops (below). New curated sets should use `question`. Records may carry fields the schema does not declare (writers pass curated records through verbatim), but the declared ones are type-checked.

**Reading is tolerant; input files are never rewritten by a read operation.** Every command that reads a question set (the pipeline's cached `questions/<product>.json`, `--questions-from`, `--custom-questions` and the auto-included golden set, `arms run`, `answers generate`, `report generate`, `questions refine`, `eval checklist`, `dataset export`) reads it through one function, `dokimi.questions.artifact.load_questions`. It accepts v1, v2, and the older shapes earlier releases wrote or passed through, and works on the set as v2 in memory:

| Read as | becomes |
|---|---|
| a bare JSON list of records | `{"questions": [...]}` |
| `question_text` (no `question`/`text`) | `question` |
| `question_id` (no `id`) | `id` |
| no `id` at all | `q0000`, `q0001`, ... by position, as the normalizer numbers them (`_`-prefixed if another record already has that id; `0` is an id) |
| `metadata` as a list | `{"items": [...]}` (v2 declares `metadata` an object; the list is kept, not dropped) |
| `null` in a key v1 does not declare and v2 types (`model`, `total_questions`, `persona_id`, `source_type`, `topics`, ...) | key dropped, without a notice. A `null` inside a typed value (`"topics": [null]`) is not dropped and is refused |

A set that needed any of these, or that is not valid under the version it declares (e.g. a set stamped `questions.v1` whose records use `question`, which `questions refine` wrote before v2), is logged once: `read <path> in legacy questions format (...); using it as questions.v2`. Reading these shapes is meant to be retired later; fix the file by hand to stop the notice (`question` for `text` or `question_text`, an `id` on each record, `metadata` as an object, and `schema_version` set to `questions.v2` or removed). No command rewrites a set unchanged: `questions refine` is not a re-save, it normalizes fields, drops trivial and duplicate questions and changes the `question_set_hash`, so do not use it on a curated set. A set that is still not valid v2 after reading (a record with an empty question, a field of the wrong type) fails before any model call, with each failing record and the fix named. The run then writes its own v2 copy to `<output-dir>/questions/<product>.json` with the `question_set_hash`. A `--questions-from` file is never written. A `--custom-questions` file is written only through the Python API (`EvaluationPipeline(custom_questions_path=<output-dir>/questions/<product>.json, force_regen=True)`), when the merge added a record to it; that file keeps each record's `source_type`, while any other custom or golden set is tagged `manual`. The cached file is the run's own and is rewritten as v2. No CLI command can reach this path: `evaluate` takes `--custom-questions` but not `--force-regen`, and `benchmark run` and `benchmark batch` take `--force-regen` but not `--custom-questions`. For sets already valid as v1 or v2, ids and `question_set_hash` do not change on the way through. `dataset export --kind questions` exports any of these in the v2 shape and records the source version (or `legacy`) in the dataset card.

`expected_topics` carries two different kinds of content depending on the source, and evaluation treats them differently. Persona-generated sets store short keyword labels ("version check", "import test"). Curated golden sets store verified answer statements, often with citations in `metadata` (`must_cite`, `github_issue`, `verified_by`). Only the latter are usable as judge ground truth — see [Grounded Judging](#grounded-judging).

## Question Types

Persona-based generation mixes these question families:

- **Conceptual**: what a feature is, why it exists, when to use it.
- **How-to**: installation, setup, configuration, and first working usage.
- **Troubleshooting**: errors, edge cases, common failure modes, and migration pitfalls.
- **Comparison**: differences between APIs, algorithms, modes, or competing approaches.
- **Performance**: expected speedups, limitations, overheads, and tuning trade-offs.

The prompt rejects broad "best practices" and generic programming questions because they are easy to answer without reading product docs and increase false positives.

## Difficulty Levels

The canonical difficulty labels are:

- **beginner**: installation, simple usage, terminology, basic examples.
- **intermediate**: integration, configuration, common patterns, API selection, troubleshooting.
- **advanced**: internals, performance tuning, architecture choices, edge cases, and low-level behavior.

Generated questions inherit difficulty from the persona skill level. The refiner normalizes legacy numeric difficulty values into these labels, removes trivial questions, deduplicates near matches, and can fill gaps against a target distribution. The default target distribution is balanced, while product config currently encodes `beginner: 2`, `intermediate: 3`, `advanced: 3` for a typical persona-sized set.

## Answer Generation

Each question receives two answers from the same answer model:

- **`with_docs`** retrieves documentation from the configured doc source, reranks the chunks, trims the context, and prompts the model to answer using only the provided documentation.
- **`without_docs`** uses the same question but no retrieved context, giving a baseline for what the model already knows.

Retrieval uses a single fixed semantic strategy. The current design decision is not to compare keyword, hybrid, or multiple `top_k` variants inside the same benchmark run. The primary comparison axis is `with_docs` vs `without_docs`.

When `--debug-retrieval` is enabled, answer artifacts include raw retrieval counts, reranked counts, scores, and fallback metadata. Token usage is accumulated for cost and latency analysis.

## Evaluation

The standard judge scores five dimensions from 0 to 100:

- **Correctness**: factual accuracy.
- **Completeness**: whether the answer covers the full question.
- **Specificity**: whether the identifiers the answer names belong to the product and fit the question. Reported, but not weighted.
- **Code quality**: whether included code is correct, runnable, and idiomatic.
- **Actionability**: whether the user can apply the answer immediately.

### Grounded Judging

When a question carries ground truth, the judge scores a sixth dimension, **factual grounding**, against it. Python computes the aggregate on both paths: correctness carries double weight, and in grounded runs factual grounding does too. Python also holds the prompt's cap: a `numeric_claims` item judged wrong lowers correctness to 40, and the judge's own score is kept under `caps`. Items with no readable verdict are listed by index under `unread_claims`.

Two kinds of question supply ground truth:

- **Chunk-grounded questions** supply it directly as `ground_truth_chunk`: the documentation excerpt the question was generated from.
- **Curated golden sets** supply it as `expected_topics`, promoted to ground truth when those entries are substantive answer statements rather than keyword labels. This path is opt-in via `--grounded-from-topics`; without the flag such questions stay ungrounded. An explicit `ground_truth_chunk` always wins when both are present and needs no flag.

The promotion is gated on mean `expected_topics` length (`MIN_GROUND_TRUTH_TOPIC_CHARS`, currently 40). Persona-generated sets store short topic labels in the same field — measured at 14-19 characters per entry across `dpnp.json`, `onetbb.json`, and `onedal.json` — and grading a full answer against a word list would penalise correct answers. Curated golden sets measure 78-195 characters per entry, so the two populations separate cleanly. Sets below the threshold keep using the ungrounded five-dimension rubric.

Grounding is scoped to the judge. Ground truth is never shown to an answering model, and both arms of a comparison are graded against the same key, so the treatment arm gains no privileged access from it.

Prefer grounding golden sets where ground truth exists. Without it the judge has no notion of the correct verdict, so it scores on the general qualities of an answer. That is weakest exactly where a golden set is most valuable: on adversarial questions whose correct answer is a refusal or a caution, where a confident and thorough-looking wrong answer can outscore a correct terse one.

The two rubrics are not interchangeable. Grounded scoring changes both the dimension set and the aggregate formula, so grounded and ungrounded scores are different scales and must not be averaged together or compared as absolute values. Runs record `grounded_from_topics` and `grounded_questions` so the rubric behind a given score is recoverable, and a partly-grounded question set is detectable after the fact.

The optional judge panel uses three roles:

- `technical_expert`: emphasizes correctness and technical depth.
- `developer_advocate`: emphasizes practical usefulness and actionability.
- `doc_reviewer`: emphasizes completeness and fidelity to context.

Panel aggregates are computed in Python from role-specific weights. The panel report includes mean score, standard deviation, agreement score, and disagreement flags. Every panel role carries the single judge's numeric-claims criterion and lists `numeric_claims` first; Python holds the same wrong-figure cap per vote, keeping the judged score under the vote's `caps`. Panel correctness scores from before this change had neither and are not comparable with later ones.

RAGAS meta-evaluation can be run after answer generation to add faithfulness and answer-relevancy signals, especially for the `with_docs` condition.

## Trust And Reproducibility

Every serious run should answer "Can we trust this run?" before interpreting deltas. The trust gate checks:

- minimum evaluated question count (both arms scored; a question with no document is not judged by default, so it is not counted here),
- zero-score fraction (over the both-arm-scored questions, plus the questions whose retrieval missed: those count as unusable, scored 0 or no document),
- minimum `with_docs` average (same subset),
- average delta (same subset),
- score coefficient of variation (same subset),
- inter-rater agreement when panel data exists,
- retrieval hit rate — the share of questions where at least one document reached the `with_docs` prompt, from the per-evaluation `retrieval_hit` the judge and panel record; eval files judged before that field existed show it as not measured, and so do files where fewer than 80% of evaluations record it (`min_retrieval_coverage`), since the rate would then describe a subset,
- multi-run variance when `--multi-run N` is used.

A check whose input the run did not produce passes without blocking or warning, but is shown as not measured, never as a pass: the verdict line reads e.g. `PASS (1 of 7 not measured)`, and the JSON report lists those checks under `trust.not_measured`.

For reproducibility:

- Prefer `--multi-run 3` or higher for stability-sensitive claims.
- Use `--questions-from` when comparing models so all models answer the same question set.
- Keep one fixed retrieval configuration per run.
- Preserve `question_set_hash`, model/provider metadata, and committed golden question sets.
- Save meaningful baselines with `python cli.py baseline save --from-eval ...`.

## Executable Agent Tasks

Executable tasks are Harbor-compatible task directories supplied from an external content repository via `--tasks-root` (see [external-content.md](external-content.md) and [contributing-terminal-bench-task.md](contributing-terminal-bench-task.md)). A task gives the agent instructions and a Docker environment, then scores the result with automated tests and `/logs/verifier/reward.txt`.

This is the right workflow when the benchmark question is not "did the answer sound good?" but "did the agent produce working code?"

These tasks can be used to evaluate:

- base agent vs context-augmented agent,
- base agent vs agent with a skill/playbook,
- different agent profiles on the same task set,
- documentation changes that should improve coding success rate.

Keep provenance and license notes alongside the tasks in the content repository, and verify oracle solutions offline with network disabled before relying on task scores.
