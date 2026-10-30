<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Reviewing a skill or a repository

The usual request is "use dokimi to review this skill" or "review this
repository's agent setup". This page is for whoever runs that request, a
person or a coding agent. Run the steps in order: each one costs more than
the step before it, and the first ones are free.

Setup, from [`dokimi/`](../):

```bash
cd dokimi
pip install -e .                 # installs the `dokimi` command
```

The `novelty` and `arms` steps take `--model` and `--provider`; `arms run` also
needs `--judge-provider` (it does not fall back to `--provider` for the judge);
`tasks run` and `repo` take `--model` only. The provider is one of `openai`,
`anthropic`, `amazon-bedrock`, `google-vertex`, `openrouter` or `openai-codex`;
`arms run --judge-provider` takes `google`, an alias of `google-vertex`, but not
`openai-codex`.
Credentials are read from the environment: `OPENAI_API_KEY`,
`ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY`, `AWS_REGION` with
`AWS_BEARER_TOKEN_BEDROCK` for Bedrock, and Google application-default
credentials (`GOOGLE_APPLICATION_CREDENTIALS`, with `VERTEXAI_PROJECT` and
`VERTEXAI_LOCATION`) for Vertex. For an OpenAI-compatible server (vLLM, a
gateway), use provider `openai` with `OPENAI_API_BASE` (or `OPENAI_BASE_URL`)
set to its `/v1` URL. A model name that litellm does not take for OpenAI's own
goes to that server, such as `glm-5.3` or `meta-llama/Llama-3.1-8B`
(`--judges openai/glm-5.3` in `eval cross-judge`); so does a bare `claude-*`,
Bedrock or Vertex id, which needs its cloud's provider to reach that cloud. A
name that starts with a litellm provider prefix (`deepseek/`, `groq/`,
`ollama/`) keeps that provider's route: write it as `openai/<name>` to send it
to the server. `OPENAI_API_KEY` must be set even when the server ignores it.
litellm has no price for such a model, so cost is reported as unknown and
`novelty --max-usd` stops the run after its first call.

The judge must be a different model from the one under measurement, because a
model that grades its own answers inflates the scores. `novelty` refuses a
self-judge; `arms run` does not check, so name
`--judge --judge-model <other> --judge-provider <judge-provider>` yourself.

## A skill

| Step | Command | Cost |
|---|---|---|
| 1. Does it load? | `dokimi catalog scan --root <catalog> --kind skills [--strict]` | free |
| 2. Is it ever picked? | `dokimi novelty routing --artifact skill:<dir> --catalog <catalog> --queries queries.json [--model …]` | free without `--model` |
| 3. What will step 4 cost? | `dokimi novelty analyze --artifact skill:<dir> --tier triage --include-resources --estimate-only` | free |
| 4. What does it add? | the same command without `--estimate-only`, plus `--model`, `--provider`, `--judge-model` and `--max-usd` | ~100 calls |
| 5a. Do answers improve? | `dokimi arms run --product <p> --questions <set.json> --arms baseline,skill:<dir> --model <model> --provider <provider> --judge --judge-model <judge-model> --judge-provider <judge-provider>` | one answer and one grade per question and arm |
| 5b. Does code improve? | `dokimi tasks run --tasks-root <checkout>/evaluation/harbor/tasks --all --harnesses docker-claude,docker-claude-skill:<dir> --baseline-harness docker-claude` ([coding-harnesses.md](coding-harnesses.md)) | one agent run per task and harness; needs Docker and Claude credentials (`AWS_BEARER_TOKEN_BEDROCK`, `ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN`), not the provider keys above; run with `--dry-run` first |

- **Competitors** are the other skills an agent sees next to this one when it
  picks a skill to load. For a catalog skill they are its siblings in the
  same catalog: `--catalog <dir>` takes every other skill the catalog lists: a root
  `skills.yaml` decides what it lists; without one, every `SKILL.md` under it
  counts except a `template/` copy (vendored and VCS directories such as `.git`,
  `node_modules`, `site-packages` and virtualenvs are not walked). A
  byte-identical mirror that loads under the same skill name counts once, and a
  skill that sets `disable-model-invocation` is left out unless
  `--include-disabled` is passed
  ([the full rule](skill-novelty.md#can-the-artifact-be-selected-at-all)). And
  `--competitors skill:<path> …` names them one by one. Routing asks whether
  this skill wins the user requests meant for it. If you only have the one
  skill, skip step 2 and say so in the review: a number measured against
  invented competitors measures nothing.
- **Queries** are 5–10 requests written the way a user would phrase them.
  Write them without looking at the skill's own headings.
- **Stop after triage (step 4)** and report. The `standard` and `study`
  tiers cost 5–20 times more, so run them only when whoever asked agrees to
  the bill. Pass `--max-usd` to `novelty` steps: the run stops once it has
  spent that much (calls in flight can overshoot), writes no artifact when the
  cap trips, and stops at the first call to a model with no known price. Set
  the cap above the expected dollar spend: the triage example below was about
  $1.80. `arms run` and `tasks run` have no cap, so size them first: questions
  times arms, or tasks times harnesses. One measured example: triage on a
  19k-token skill with a mid-size model was 103 calls, about $1.80; `study` on
  the same skill was 1,900 calls, about $14.50.
- **At triage** with `--include-resources`, the 25-atom cap takes atoms in
  document order, and the entry point comes first: the flag reaches
  `references/` only if `SKILL.md` has fewer than 25 atoms. For such skills,
  use `--tier standard` (60 atoms), or add `--all-atoms` to the tier command for
  a full sweep; run `--estimate-only` first, it costs more.
- Step 5 needs evaluation content: a golden question set for 5a, executable
  tasks for 5b. `arms run` takes no tasks. That content lives outside this repository
  ([external-content.md](external-content.md)). Without it, the
  review covers selection and content, not outcomes, and should say so.

The full path with reading notes: [skill-novelty-walkthrough.md](skill-novelty-walkthrough.md).

## A repository's agent setup

| Step | Command | Cost |
|---|---|---|
| 1. Do the paths, links and commands named in the guidance exist? | `dokimi repo scan <repo>` | free |
| 2. Which guidance files does the harness actually load? | `dokimi repo reach <repo> --model <id> --dry-run`, then a few `--only <scenario>` with `--budget` | capped by `--budget` (default $0.10) per scenario; needs the `claude` binary and `ANTHROPIC_API_KEY`, or `AWS_BEARER_TOKEN_BEDROCK` with `AWS_REGION` and `CLAUDE_CODE_USE_BEDROCK` |
| 3. What does the root guidance add? | `dokimi novelty analyze --artifact text:<repo>/AGENTS.md --tier triage …` | ~100 calls |
| 4. Does the guidance change task outcomes? | `dokimi repo run <repo> --manifest-file … --model <id> --batch <name> --arm guidance:none,guidance:raw --agent-network host --budget <usd> --max-batch-usd <usd>` | needs the repository's own tasks, Docker, `claude` binary on PATH (or `--bin`); `--budget` defaults to $2.00 per agent run with no batch cap unless you pass `--max-batch-usd` |

With no `repo-eval.yaml` in the repository, steps 1 and 2 check the default
guidance globs (`<repo>/**/AGENTS.md`, `<repo>/**/CLAUDE.md`,
`<repo>/.github/copilot-instructions.md`, `<repo>/.github/instructions/*.md`)
and say so.
To choose the files for `scan`, write a guidance-only manifest outside the tree and pass
it with `--manifest-file`:

```yaml
schema: repo_manifest.v1
id: my-repo
guidance:
  globs: ["**/AGENTS.md", "**/CLAUDE.md", ".github/copilot-instructions.md"]
```

A glob that matches no file is only a warning. To scan a file the globs leave out,
add `also_scan: ["CONTRIBUTING.md"]` under `guidance`: `also_scan` names files the
repository has and the gate must see, so a missing one makes the scan `incomplete`
and `repo scan --fail-on-fail` exits 1.

The `id` must be lowercase: start with a letter or digit, then use only letters, digits, `.`, `_` and `-`.
`reach` always adds the harness's own guidance file names (for example `CLAUDE.md`, `CLAUDE.local.md` or `AGENTS.md`).

A guidance-only manifest has no tasks, so step 4 refuses it.

How to read the results:

- **Scan:** `fail` counts against the guidance and each one still needs a
  look. Harness tool names (`run_in_background`) and placeholders
  (`<name>`, `caseNN_*`) can show up as `fail` too. `unresolved` also needs
  a look: it can be a path the scan could not check, or one whose directory
  is gone. The scan checks that a path, identifier or command exists, not
  that what the guidance says about it is true: a count or a description of
  the code (`public api` where the code declares `cdef api`) can be stale
  while every claim is `ok`. A review that lists the scan under what it
  checked names what the scan covers.
- **Reach:** a guidance file nobody loads cannot help anyone. Run `reach`
  before you judge the guidance's content. `loaded` comes from the harness
  itself, not from the harness's documentation, so a result that contradicts
  the docs is a finding. `reach` probes the tree as it is: with no
  `CLAUDE.md` in the tree, it does not show what one would turn off.
- **Step 4:** without tasks there is no outcome measurement. The review
  should say so rather than infer one from steps 1–3.

A review also says which of its points come from reading rather than from a command:

- **Past review comments:** no command counts how many of them the guidance
  would have prevented. To count by hand, take the comments the author then
  addressed, and read the reply before counting a miss: a change the author
  meant, or declined with a reason, is none. Count each comment once, and
  check that a fix the review cites is still on the default branch. The
  count takes every addressed comment, not only issues raised in at least
  two PRs: that filter picks which comments become review rules
  ([preparing-a-repository.md](preparing-a-repository.md#6-review-rules-learned-from-past-reviews)),
  while the count asks what the guidance would have caught. To measure it
  instead, replay the merged pull requests as `dokimi:review_replay` tasks
  and compare `guidance:none` with `guidance:raw` in step 4.
- **A rule the review proposes:** read every file it names; that the name
  exists is not enough (a workflow that calls another one holds no matrix of
  its own). Check it against the existing guidance: a nested `AGENTS.md` may
  keep old code on purpose. Put what applies to one directory in that
  directory's file.

The contract a repository codes against: [repo-evaluation-contract.md](repo-evaluation-contract.md).
