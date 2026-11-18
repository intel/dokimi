---
depends_on:
  - dokimi/dokimi/eval/arms_run.py
  - dokimi/dokimi/eval/arms_refusals.py
  - dokimi/dokimi/eval/answerer.py
  - dokimi/dokimi/eval/reference_run.py
  - dokimi/dokimi/commands/answer_formats.py
  - dokimi/dokimi/llm_cli.py
  - dokimi/dokimi/records.py
  - dokimi/dokimi/report/single_condition_report.py
  - dokimi/dokimi/eval/diagnoser.py
---
<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Knowledge runs: what an agent knows, as shipped

A **knowledge run** asks one question set of one answering agent or model, with no
documentation, skill or persona injected, and grades the answers. It answers *what does
this model know about X by itself?* Run it before any with-docs comparison: the
`baseline` answers of that comparison are the same thing, and a knowledge run is the
reference the comparison's delta is read against.

It is one condition, so there is no delta, no pairing and no significance test. The
output is an aggregate per question and per condition. Nothing here measures tool use,
repository guidance or a task's pass rate; for those see [coding-harnesses.md](coding-harnesses.md).

## Two routes

| Route | Command | Output | Grading |
|---|---|---|---|
| A. plain answers file | `answers generate --doc-source none` | `answers.v1` with `without_docs` only | `eval score reference` (grading checks), `eval score pointwise` (judge) |
| B. one-arm comparison | `arms run --arms baseline` | `arms.v1` with one arm, `baseline` | `--judge` on the run, or `eval score reference` on its output |

Both take `--model`/`--provider`, and both accept a CLI answerer (below). Use route B
when the same run should later sit next to a `docs` or `skill:` arm: the arm file already
has the shape `arms run` writes for a comparison.

### Route A: an answers file, graded by reference checks

```bash
python cli.py answers generate --product demo --questions questions/demo.json \
  --doc-source none --provider amazon-bedrock --model claude-haiku-5-5 \
  --output knowledge/demo-answers.json
python cli.py eval score reference --answers knowledge/demo-answers.json \
  --questions questions/demo.json -o knowledge/demo-eval.json --no-judge
```

`--doc-source none` is what makes the run a knowledge run: no retrieval, and the answers
land under `without_docs` with `with_docs` null. The command prints `Baseline: n/N` and no
context-arm line, because there is no context arm.

`eval score reference` grades each question by its `grading` block and writes an
`eval.v1` artifact. Its `run_metadata.summary` has, per condition, `n`, `pass_rate` and
`mean_score`. A question with no `grading` block is counted in
`run_metadata.ungraded_questions` and not in the summary. When the `--answers` file is an
`arms.v1` file (route B), the artifact stays `arms.v1` and the same per-condition tally sits
under `reference_scoring.summary`. Drop `--no-judge` to let the judge grade the checks that need one
(`judge` checks); with `--no-judge` those are left unscored and the pass rate covers only the checks
that ran: read `judge_unscored` before you compare it with a judged run.

For a judge-only score with no checks, use `eval score pointwise --product demo --answers
knowledge/demo-answers.json --output knowledge/demo-judged.json`. Its per-question rows have
`with_docs: null`, and `delta` is `null` by design.

### Route B: a one-arm `arms.v1` run

```bash
python cli.py arms run --product demo --questions questions/demo.json \
  --arms baseline --provider amazon-bedrock --model claude-haiku-5-5 \
  --judge --judge-model claude-sonnet-5-5 --judge-provider amazon-bedrock \
  --out knowledge/demo-arms.json --out-md knowledge/demo-arms.md
```

`--arms baseline` is the only arm, and it is accepted: the file's `arms` list is
`["baseline"]`, `summary.per_arm.baseline.avg_aggregate` is the knowledge score, and its
delta is `null`. Without `--judge` the run only answers, which is cheap, and
`eval score reference --answers knowledge/demo-arms.json --questions ...` grades it later.

## Answering through an agent CLI

An answerer does not have to be an API. `--provider claude-cli` answers through the Claude
Code CLI the machine is signed in to. The CLI runs with no tools, no settings files and no
MCP servers, and the call's system prompt is the plain one dokimi sends. What the CLI still
adds to each call is covered in [subscription-backends.md](subscription-backends.md), which
also covers the login. It works on both routes:

```bash
python cli.py answers generate --product demo --questions questions/demo.json \
  --doc-source none --provider claude-cli --model sonnet --output knowledge/demo-cli.json
python cli.py arms run --product demo --questions questions/demo.json --arms baseline \
  --provider claude-cli --model sonnet --out knowledge/demo-cli-arms.json
```

Two limits apply. `claude-cli` takes no temperature or seed, so `arms run` refuses either
flag before it starts. An agentic arm (`agent:`, `skill-agent:`, `skill-catalog:`) is
refused on it too, because the CLI runs one prompt with no tools. A knowledge run has
neither, so that refusal does not affect this page.

`--provider copilot-cli` answers through the GitHub Copilot CLI the same way, on both routes.
`--provider codex-cli` answers through the OpenAI Codex CLI on a comparison, but not on a
knowledge run: Codex has no switch that turns its own tools off, so `--doc-source none` and
`arms run --arms baseline` refuse it before any call (exit 2). Use `copilot-cli` or `claude-cli`
for a knowledge run. Each CLI must be installed and signed in (`codex login`, or Copilot's own
login). `python cli.py backends` lists what dokimi can see, with no model call:

```bash
python cli.py answers generate --product demo --questions questions/demo.json \
  --doc-source none --provider copilot-cli --model claude-sonnet-4.5 --output knowledge/demo-copilot.json
```

The model name is passed to the CLI as given; dokimi does not check it against the CLI's
catalogue. Both are answering-only: no conversation. `copilot-cli` runs with no tools
(`--available-tools __none__`). `codex-cli` has no switch for its tools, so it is refused on a knowledge
run. Pass `--judge-model` and `--judge-provider` for an API judge, as the judge runs on an API
provider. See
[subscription-backends.md](subscription-backends.md) for the login and the per-call costs.

Isolation, per call, is the same idea as for `claude-cli`: a fixed empty work directory under
dokimi's cache, outside any repository (`GIT_CEILING_DIRECTORIES` stops the search for one), and
no user instructions, rules, or project docs read from it.

- **`codex-cli`** runs `codex exec --json --ephemeral --ignore-user-config --ignore-rules
  --skip-git-repo-check --sandbox read-only` with the prompt on stdin. `--sandbox read-only`
  limits writes, not file reads or shell commands, so this is not a no-tools route. The system text goes
  by a temporary file as the model instructions, and the project doc size is set to 0, so no
  `AGENTS.md` is read. Its API key and base-URL variables are removed from the environment, so
  the login is what answers; `CODEX_HOME` is kept. Token counts come from the event stream.
  Cost is not computed: a login is not billed per call.
- **`copilot-cli`** runs `copilot -p <prompt> -s` with no custom instructions, no built-in
  MCP servers, no auto-update, no questions to the user, and no tools
  (`--available-tools __none__`). It has no system-prompt flag, so the system text is prepended to
  the prompt, and the prompt is one command-line argument: a prompt over 100,000 bytes is
  refused before the call. GitHub token and BYOK provider variables are removed from the
  environment. The text output carries no token counts, so the record shows none and no cost.
  Its `detect` can say the CLI is installed but not whether the login works: a call that fails
  for want of a login is the first sign.

Errors are classed the same way on all three CLIs. A usage limit or a missing login stops the
run, since every later call would fail too; so does a command line Codex or Copilot rejects (a
flag a newer release removed). A rate limit, overload or timeout is retried; a
reply with no text is not retried. The timeout is per call.

Codex and Copilot take no temperature or seed, so `arms run` refuses either flag on them, as it
does for `claude-cli`. An agentic arm is refused on all three, for the same reason.

**Task harnesses are not answering routes.** `codex`, `claude-code`, `copilot`,
`command:*`, `terminal-bench:*`, `benchflow*` and `docker-*` name agent-CLI harnesses (`tasks run`,
`repo run`; see [coding-harnesses.md](coding-harnesses.md)): they run an agent in a sandbox
against executable tasks. `arms run --harness codex` is refused for that reason, because the
label would stamp answers that the model behind `--provider` produced. Use `--provider
codex-cli` (or `claude-cli`, `copilot-cli`) to answer through a CLI, or leave `--harness` out.

## Questions with grading checks

Route A grades with the `grading` block of each question. Two ways to get one:

- `eval import` translates an existing answer-only set (Inspect AI, OpenAI evals,
  promptfoo, skill-creator `evals.json`) into questions with checks. See
  [answer-formats.md](answer-formats.md).
- Write the block by hand. A `match` check passes when the value appears in the answer:

```json
{"id": "demo-1", "question": "Which function runs a parallel for-loop in oneTBB?",
 "grading": {"checks": [{"type": "match", "values": ["parallel_for"], "location": "any"}]}}
```

Route B's `--judge` needs no checks: the judge scores each answer on its own.

## Reading the result

- Compare a knowledge run with another knowledge run, not with a comparison's delta:
  `summary.per_arm` (judged arms) holds each condition's own average; `run_metadata.summary`
  (eval) and `reference_scoring.summary` (arms graded by reference) hold its pass rate.
- `report eval` on a knowledge run writes a one-condition page: one row for
  `without_docs (knowledge)` (questions, scored, mean, lowest, highest), or for the arm's
  name on a one-arm `arms.v1` file (route B), the same row by
  difficulty, the lowest-scoring questions, and every question with its score. A paired run
  keeps the paired report. On a binary knowledge run each question's diagnosis is
  `⚪ One condition (no delta)`, not insufficient data, and `eval score pointwise` gives the per-condition score the same way.
- Keep the answerer fixed across the knowledge run and the with-docs run that follows:
  the same `--provider`, `--model`, questions file and judge. A knowledge run made with
  `arms run --arms baseline` sends the same prompt as the `baseline` arm of the comparison,
  so the two line up question by question. The answers are sampled again, so they will not
  match word for word. A knowledge run made with `answers generate --doc-source none` uses a
  different prompt; compare it with another `answers generate` run, not with an arm:

```bash
python cli.py arms run --product demo --questions questions/demo.json \
  --arms "baseline,docs" --provider amazon-bedrock --model claude-haiku-5-5 \
  --judge --judge-model claude-sonnet-5-5 --judge-provider amazon-bedrock \
  --out results/arms/demo-with-docs.json
```

The judge gets no stored material for a `baseline` arm, so the knowledge run and the
`baseline` arm of the comparison are judged on the same footing. See
[evaluating-treatments.md](evaluating-treatments.md) for what the material changes on the
other arms.
