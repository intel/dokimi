<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Replay a product experiment

A product experiment can pair an advisory question with an executable task. The
advisory answer establishes what the agent recommended. A task verifier separately
checks the implementation and records evidence from execution. A recommendation,
import, backend call, and correct output are four independent observations.

| Operation | Dokimi entry point | Integration work |
| --- | --- | --- |
| Author questions or tasks | `eval questions generate`, `eval tasks generate` | Use a small template for the four ask/execute × free/named cells. Freeze the exact prompt bytes. |
| Ask under a treatment | `subjects run`, `arms run` | Supply the question set and rubric. For free choice, use a neutral workspace. Keep product aliases, ranks, and verbatim evidence in a domain grader. |
| Execute and repeat | `tasks run --reps` | Supply the task root, harness and verifier. The command harness also accepts historical outcomes. |
| Compare with and without a skill | `tasks run`, with the `docker-claude` and `docker-claude-skill:<skill>` harnesses | Keep the task, model and verifier fixed. Pin the skill revision alongside the frozen suite. |
| Check hidden-data accuracy and backend dispatch | Task verifier | Run hidden inputs and gather trusted runtime observations. An import or a source match can indicate intent. The backend verdict needs an execution observation. |
| Calibrate answer graders | `eval score`, `eval judge wording`, `eval judge agreement` | Keep a hand-labelled sample and record the rubric hash, judge and model. Repeat blind copies to check agreement. |
| Inspect evidence and failures | `runs show`, `trace inspect`, `trace consistency`, `backends --json` | Join trace references and typed verdicts to each attempt. Trace commands take supported trace formats. Preflight credentials separately from grading. |
| Gate a change | `make ci-local` | Replay frozen fixtures offline. Treat subscription runs as a separate evidence source. |

## Offline example

From the Dokimi project directory, run these commands with its development Python.
Use a fresh output directory for each run.

```bash
python scripts/replay_product_experiment.py --out results/product-fixture
python scripts/replay_product_experiment.py --skill-fixture --out results/skill-fixture
```

The script writes `suite.json`, a `task_runs.v1` artifact in `runs.json`, the
comparison in `runs.md`, and a client-facing example in `response.json`. Its
command harness writes `reward.txt` and a structured `evidence.json` for each
cell. The historical-control path uses saved evaluator observations as inputs to
the harness. The default inputs are synthetic. The script starts no model calls.

Example suite cell:

```json
{
  "version": "product-experiment.v1",
  "cases": [
    {
      "id": "ask-free-0",
      "mode": "ask",
      "cue": "free",
      "prompt": "Recommend an approach.\nA developer needs to train a linear regression model and make predictions.\nUse CPU. Predictions must match the reference on held-out data."
    }
  ]
}
```

The generator provides an original and two wordings per cell. It only swaps the
opening sentence. The task and constraints are copied exactly. The free cases
reject the literal product name case-insensitively. Review aliases and implicit
product cues when authoring a real suite. The offline harness executes the
original execute cases and writes ask cases for a separate question run.

To generate a different suite, pass `--generate-suite request.json --out
results/frozen-suite`. The request has a `task` string, `constraints` and
`success_criteria` string arrays, and an optional `product` string.
Without `product`, generation produces a free-choice suite. To replay the
product controls, supply `product` during generation so the suite also contains
the product-named prompts used by the patched controls.

After changing the supplied skill, reuse the same suite file:

```bash
python scripts/replay_product_experiment.py --skill-fixture \
  --suite results/skill-fixture/suite.json --skill-file /path/to/SKILL.md \
  --out results/skill-revision
```

The copied suite has the same SHA-256. For a live run, preserve the bytes and
record the separate skill or repository revision, agent version, served model,
credential kind and environment policy. The deterministic Python stand-in
exercises the adapter, repetition and comparison, with no claims about a coding
agent's ability to discover the skill.

| Offline comparison | Baseline | Treatment |
| --- | --- | --- |
| Product controls | Correctness 9/9 | Correctness and accelerated prediction 3/9 |
| Unicode fixture | Without skill 0/3 | With skill 3/3 |

For both synthetic negative controls, the output is correct and accelerated
prediction is unobserved. The positive control satisfies both gates. The
example uses prediction dispatch for the backend gate. A real task must define
which of fit, predict, both, or another operation it requires.

With a compatible external evaluator bundle, reuse saved controls and their
seeds to re-run verification:

```bash
python scripts/replay_product_experiment.py --controls /path/to/controls.json \
  --evaluator-root /path/to/evaluator-bundle \
  --evaluator-python /path/to/evaluator-venv/bin/python \
  --out results/verified-controls
```

The bundle supplies `starter/`, `controls/`, and `evaluator/verify.py`. This
example runs trusted fixtures locally. Production verification against agent
submissions needs isolation between the submitted code and the verifier.
`attempted` reflects product selection in changed source, `sklearnex_loaded`
is runtime import evidence, and the `fit_backend` and `predict_backend`
verdicts come from the subject evaluator. With only `--controls`, the wrapper
replays the stored grades and leaves unrecorded observations null.

For saved ask answers, map the question and completion to the Promptfoo log
shape and read it with `eval native-import`. Keep the original trace and
domain grader's observations next to the imported answers. A keyword match
shows a mention. Recommendation requires its own rubric and quoted evidence.

Example cell excerpt for a client:

```json
{
  "source": "test_fixture",
  "mode": "execute",
  "task": "late-patch",
  "arm": "correctness-and-backend",
  "repeat": 1,
  "seed": 1000,
  "status": "task_failed",
  "verdicts": {
    "recommended": null,
    "attempted": null,
    "predict_backend": "not_observed",
    "correct": true
  },
  "evidence": "cells/late-patch/correctness-and-backend/run1/evidence.json"
}
```

A null verdict is unmeasured. The wrapper uses an unreadable reward as the
command harness's existing no-verdict signal when evaluation cannot finish.
Use `task_failed` for an evaluated failure and `infrastructure_error` for missing
or unusable evaluation. Replay wall time is local processing time. Subscription
marginal cost may be unknown even if a CLI reports list-price-equivalent usage.

## Integration boundary

Keep task execution, repeated comparisons, raw evidence and schema validation in
Dokimi. The smallest next core addition is an optional, versioned command-verifier
sidecar with named observations, evidence references and explicit no-verdict
reasons. That also serves skill authors grading individual requirements and
repository authors who want actionable CI output for failed tasks.

Keep hidden inputs, instrumentation and correctness thresholds in the verifier.
The calling service owns the experiment question, product vocabulary, cohort
selection, signup and credential acquisition, publication and portfolio ranking.
A question generator can propose probes, while the calling service decides which
ones to freeze and approve. The same runner can then execute unchanged prompts
against a revised skill, README, or SDK.

Devtool Arena's fixed task/agent/model/grader across a tool category is a useful
comparison design. Its Stripe report puts execution, source links, generated files,
latency, cost and tool errors together. The run showed successful execution in
37 s with six tool calls and two tool errors. Adopt that evidence breakdown for
Dokimi reports and keep readiness checks and task outcomes as separate values.
The Arena's particular 70/30 rollup and price bands belong to the application.
[Methodology](https://devtoolarena.com/methodology) ·
[Stripe report](https://devtoolarena.com/claudecode/api/stripe)

Lightsage distributes both skills and a hosted MCP server, with installation and
fresh-session steps documented for individual clients. For Dokimi skill and tool
authors, this suggests an installation preflight and separate trials for discovery,
explicit invocation and tool availability. Record whether the skill was present
and loaded and whether the tool was actually callable.
[Lightsage Skills](https://github.com/lightsagehq/lightsage-skills)
