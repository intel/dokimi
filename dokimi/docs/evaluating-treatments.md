---
depends_on:
  - dokimi/dokimi/treatments/**
  - dokimi/dokimi/commands/arms.py
  - dokimi/dokimi/schemas/arms.v1.json
  - dokimi/dokimi/skills/loader.py
  - dokimi/dokimi/agent_profiles/loader.py
  - dokimi/dokimi/eval/agent_runner.py
  - dokimi/dokimi/mcp/factory.py
  - dokimi/scripts/sync_skilltext_profile.py
---
<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Evaluating treatments (docs, MCP, skills, agent profiles)

The original LLM benchmark answers one question — *do retrieved docs improve
answers?* — by comparing two arms, `with_docs` and `without_docs`. The
**treatment-arm** framework generalizes that to an N-way comparison so you can
measure the marginal value of any context-augmentation artifact:

| Arm spec | Scenario | What it changes |
|---|---|---|
| `baseline` | control | nothing (the old `without_docs`) |
| `docs` / `docs:<doc-source>` | documentation injection | injects retrieved doc chunks (the old `with_docs`) |
| `mcp:<ref>` | MCP doc server | retrieves docs through a real MCP server |
| `profile:<path>` | agent persona prompt | swaps the answering agent's system prompt |
| `skill:<path>` | agent skill | injects a `SKILL.md` as context |
| `agent[:source]` | **agentic** doc use | gives the model a doc-search tool; it decides when to call it |
| `skill-agent:<path>` | **agentic** skill use | offers the skill via progressive disclosure |
| `skill-catalog:<dir>` | **agentic** skill selection | offers every skill under `<dir>` (a `template/` copy, a byte-identical mirror that loads under the same skill name, and anything under a vendored or VCS directory such as `.git` or `node_modules` are left out); scores the one loaded against `expected_skill` |
| `<spec1>+<spec2>[+...]` | **combined** treatment | merges multiple treatments into one arm (e.g., `docs+skill:path`) |

The last two run through a tool-calling loop
([`eval/agent_runner.py`](../dokimi/eval/agent_runner.py)): the model
*decides* whether to query docs or load a skill, and the report records how often
it actually did (the faithful test of whether a doc source or skill is *usable by
an agent*, not just whether its text is relevant). The rest are single-shot.

Each arm is a [`Treatment`](../dokimi/treatments/base.py) that produces
an `AgentConfig` (system prompt + injected context) per question. The answers
are scored by the same LLM-as-judge used elsewhere, and the report shows each
arm's average score and its delta vs the baseline arm.

The judge scores each answer against the material its arm was given: the full
injected chunks for `docs`, the skill body plus its bundled-file list for
`skill:`, and the same body and list for `skill_agent:`, whose agent reads them
only through a tool. A `skill-catalog:` arm gives the bodies of the skills its agent
loaded plus every catalog file list (see [Skill selection](#skill-selection-does-the-agent-load-the-right-skill)).
`baseline`, `mcp_agent:` and `profile:` arms give it none.
The output records this as `judge_context: treatment_material.v1` when every
score saw the stored material, and `judge_context_sources` counts the scores by
source. The artifact keeps the material: each arm record names its text by digest
(`judge_context_sha256`) and `judge_materials` holds one copy of each distinct
text — a skill arm's, identical for every question, is stored once (about 4,500
characters for the perf skill, +0.3% for the text itself on an 81-question run;
each arm record and stored score also carries a 108-byte digest line, about 2% more
for a baseline plus skill run). The material is kept whether or not `arms run` was
given `--judge`. For docs arms, the material is the retrieved chunks, rarely
identical across questions, so each question adds its own copy (up to the
15,000-char cut); on a synthetic 20-question docs run the artifact grew from
93,209 to 392,852 bytes (4.2x). Sharing an arms artifact shares the skill or doc
text it contains. With `cross-judge --judges`, the judge is given the same text
through `JudgeContextStore.from_artifact`, which skips any text that does not
match its digest. Question ids must be unique for this (a question with no id is
given a positional one; duplicated ids are refused).
Scores that fell back to the 200-character report snippets leave the run unstamped
if all of them did — the protocol runs used before the stamp existed — and stamp it
`treatment_material.v1+snippet_fallback` if only some did, which pools with nothing.
A `baseline` arm's empty material always counts as stored, so with a baseline arm a
fallback gives the mixed stamp. To compare with an older run, written before
`judge_materials`, run answering and judging again: re-judging that file sees only
the snippets.

What this changes is the evidence the judge has, not how well it separates good
answers from bad ones. On 81 skill-arm answers to the perf golden set (Haiku 4.5
judge, 24 bases, one run), the full material raised the mean intact score from
85.9 to 89.9, while pairwise accuracy on deterministic degradations went from
0.869 to 0.808: better on an appended contradiction of the skill (score drop
+11.5, 20/24 pairs, sign test p = 0.002), worse on wrong tool names (0.891 →
0.674, not significant at this size), unchanged on wrong numbers. A skill arm's
delta measured with the material is therefore on a higher level than one
measured without it, and the difference between the two is not evidence that
the skill helps. `eval judge-validity` passes no context by default; run it on an
arms artifact with `--context stored` to gate the judge the way it scored the
arms — each answer with its arm's stored material (bases with no stored entry are
dropped and counted, except baseline with the no-context marker). It exits 2
with a typed scorer or `--mode pairwise`, on a file with no non-empty verified
material, when an arm loses all its bases, has only `padding` pairs or gets none sampled by `--bases`,
when a `--sequential` run would pass with none of an arm's bases scored, or
when no pair is usable.

Runtime plugins are a separate run dimension, not arms. They modify every arm
inside the same `(model, harness, plugin_set)` cell, and the JSON/Markdown
outputs stamp `harness`, `plugin_set`, `plugin_set_id`, and concrete plugin
metadata. The initial supported plugin is `plugin:caveman[:lite|full|ultra]`,
which adds a brevity system instruction before answer generation.

Plugin effects are computed from paired runs, not from one artifact in
isolation. Run the same product, questions, arms, model, provider, and harness
once with no plugin and once with the plugin enabled, then compare the two
`arms.v1` artifacts:

```bash
python cli.py arms plugin-delta \
  --baseline-json results/arms/oneTBB-none.json \
  --plugin-json results/arms/oneTBB-caveman.json \
  --out-json results/arms/oneTBB-caveman-delta.json
```

The command writes a `plugin_delta.v1` artifact plus a Markdown report with
judge-score deltas, token/cost deltas, and answer-length deltas. It refuses
confounded comparisons where model, provider, harness, arms, baseline arm, or
question IDs/order differ. For judged artifacts it also requires both runs to
carry the same stamped judge model, provider and `judge_context`.

> **Naming:** an *agent profile* (`profile:`) is the answering agent's system
> prompt. It is **not** a `persona` — in this repo a persona is a synthetic
> *user* who asks questions (`dokimi/personas`).

> **Which commands run treatments:** `arms run`, `arms matrix-run`, and the
> `subjects` commands — they are the only callers of `create_treatments`. The
> legacy answer path (`benchmark run`, `answers generate`) predates arms: it has
> only the two fixed `with_docs`/`without_docs` conditions, no `--arms` flag, and
> no way to send a system prompt at all, so a `profile:` arm cannot be expressed
> there. `explore --arms` is an unrelated flag (`oracle`/`empty`/`command:`), not
> a treatment list.

## Quick start

```bash
python cli.py arms run \
  --product oneTBB \
  --questions <content-checkout>/questions/onetbb_golden.json \
  --arms "baseline,docs,profile:data/agent_profiles/concise_expert.md,skill:<content-checkout>/skills/onetbb-quickstart" \
  --judge \
  --out-json results/arms/oneTBB.json \
  --out-md results/arms/oneTBB.md
```

Question sets and product skills are evaluation content: they are not committed
here, so every example below names them as `<content-checkout>/…` — a checkout of
your own content repository. Agent profiles under `data/agent_profiles/` and
`data/skills/fastcontext/` are the fixtures this repo does commit.
[external-content.md](external-content.md) shows how to lay such a checkout out
and how to check it loads before spending money on a run.

Without `--judge` the command only generates answers (cheap, no judge calls).
Outputs default to `results/arms/<product>.{json,md}` (ignored by git).

Example Caveman plugin cell:

```bash
mkdir -p results/arms
cat > results/arms/matrix.json <<'JSON'
{
  "matrix": {
    "cells": [
      {
        "id": "openclaw-no-plugin",
        "model": "gpt-6.1-sol",
        "provider": "openai",
        "harness": "arms-runner",
        "plugins": []
      },
      {
        "id": "openclaw-caveman",
        "model": "gpt-6.1-sol",
        "provider": "openai",
        "harness": "arms-runner",
        "plugins": ["plugin:caveman"]
      }
    ]
  }
}
JSON

python cli.py arms matrix-run \
  --product oneTBB \
  --questions <content-checkout>/questions/onetbb_golden.json \
  --arms "baseline,docs,skill:<content-checkout>/skills/onetbb-quickstart" \
  --matrix-cells results/arms/matrix.json \
  --judge \
  --out-dir results/arms/oneTBB-matrix
```

`--matrix-cells` accepts JSON shaped as `{"matrix": {"cells": [...]}}`,
`{"cells": [...]}`, or a top-level list. Each cell can use `id` or
`name` as its identifier. A selected cell controls `model`, `provider`,
`harness`, and `plugins`, and the run refuses unsupported plugin/harness
combinations before calling the model. `arms matrix-run` writes one `arms.v1`
artifact per cell, a `matrix_rollup.v1` JSON/Markdown report, and paired
`plugin_delta.v1` artifacts for no-plugin/plugin cells that only differ by
`plugin_set`.

## Arm specs in detail

- **`docs:<doc-source>`** — `<doc-source>` is anything
  [`create_doc_source_client`](../dokimi/mcp/factory.py) accepts:
  `context7` (default), `local:<path>`, `url:<url>`, or `mcp:<ref>`. Bare
  `docs` uses Context7.
- **`mcp:<ref>`** — retrieve through a real MCP server. `<ref>` is
  `<transport>=<target>[;opt=val...]`:
    - `mcp:cmd=npx -y @upstash/context7-mcp` (stdio)
    - `mcp:http=https://mcp.context7.com/mcp;tool=get-library-docs` (streamable HTTP)
    - `mcp:sse=https://example.com/sse;id=uxlfoundation/oneTBB` (SSE)

    Options: `tool=` (docs tool name), `resolve=` (resolve tool), `id=` (fixed
    library id). Requires the optional `mcp` SDK: `pip install mcp`.

- **`profile:<path>`** — a Markdown file; the body (frontmatter stripped) is the
  system prompt. See [`data/agent_profiles/concise_expert.md`](../data/agent_profiles/concise_expert.md).
  The arm is **named after the frontmatter `id`, not the path**: an arm passed as
  `profile:data/agent_profiles/concise_expert.md` appears in reports and in `--baseline-arm`
  as `profile:concise_expert`, and a file renamed without touching its `id` keeps the old arm
  name. With no
  frontmatter — or with malformed YAML, which is ignored silently — the id falls back to
  the filename stem, so name profile files after their id.

    Because the arm name comes from the id, **editing a profile in place does not change the
    arm name**: old and new cells look identical in a report. Each answer therefore records
    `profile_path`, `profile_prompt_chars`, and `profile_prompt_sha256` (a digest of the
    prompt text, not the file, since the text is what reaches the model). Compare that digest
    across a batch before pooling cells — differing digests mean two different prompts were
    measured under one arm name. On a **combined** arm the same three fields live one level
    down, under the matching entry of `metadata["treatments"]` rather than at the top of the
    metadata — so anything reading them has to look in both places, `prompt_digests()`
    included.

    The prompt is capped at **12,000 characters**, the same cap `skill:` applies to a skill
    body. The parity is the point: comparing the system-prompt channel against the context
    channel only means something if one side cannot carry a payload the other would have had
    truncated. A capped arm records `profile_prompt_truncated: true` alongside
    `profile_prompt_chars` (sent) and `profile_prompt_chars_source` (on disk), logs a warning,
    and digests the *rendered* text — so a truncated cell is visibly a different treatment
    rather than a quietly shorter one. `skill:` arms record `skill_truncated` on the same
    terms, with three sizes rather than two: `skill_chars` (body after the cap) pairs with
    `skill_chars_source` (body on disk) the way the profile fields do, while
    `skill_chars_injected` is the whole rendered block — header and description included,
    which is why it exceeds the source for a skill that was never truncated and is not the
    number to compare against the cap. `ViewSkillTool` records `skill_file_truncated` /
    `skill_file_chars_source` when an agent views a bundled file that exceeds the cap.

    `profile_prompt_sha256` is the profile's *contribution*, not always the whole prompt sent:
    a `prompt_middleware` plugin appends to the system prompt after the arm has already
    digested it. `PluginWrappedTreatment` therefore also records `system_prompt_sha256` (the
    prompt as it leaves the wrapper) and `system_prompt_extended_by_plugins`, so a caveman
    cell and a bare profile cell are distinguishable in the artifact instead of sharing one
    profile digest.

    Where 12,000 comes from: it is the default the skill loader has always carried
    (≈3k tokens), an inherited convention rather than a tuned figure — no design note or commit
    justifies the number, and profiles adopted it for parity, not because it is optimal.
    Read it as a guardrail against a fixture that would dominate the prompt. If you change
    it, change it in **all** channels at once (`skills/loader.py`,
    `agent_profiles/loader.py`, `treatments/arms.py`, `treatments/tools.py`,
    `scripts/sync_skilltext_profile.py`): an unequal cap turns the channel comparison into a
    payload comparison. Raising it also re-truncates nothing retroactively — already-scored
    cells keep their old digests.

    Two tests guard the cap, and it is worth knowing exactly what each covers:

    - `test_no_injected_fixture_is_within_5_percent_of_the_cap` scans the **injected**
      payloads — every profile body and every `SKILL.md` body under `data/` — and fails at
      95%, so a fixture is caught while it is still growing rather than after it truncates.
      Its message prints the current largest fixture, so no percentage is hardcoded anywhere.
      It also fails when either channel has no fixture at all: a scan over an empty directory
      would otherwise report success while measuring nothing.
    - `test_over_cap_bundled_resources_are_known` covers the third channel, the bundled
      files `ViewSkillTool` serves. These are **not** in the injected scan. Its
      `OVER_CAP_RESOURCES` allowlist is currently empty — no committed resource exceeds the
      cap — and anything new going over fails the test until it is split or listed there with
      a reason, which keeps a truncation visible in code instead of silent.
    - Neither test sees a skill in an external content checkout. A skill you pass as
      `skill:<path>` is capped the same way at run time and records
      `skill_truncated` / `skill_chars_source`, so check those fields in the artifact.

- **`skill:<path>`** — a `SKILL.md` file or its directory, following the Agent
  Skills convention (frontmatter `name`/`description` + Markdown body). See
  [`data/skills/fastcontext/SKILL.md`](../data/skills/fastcontext/SKILL.md) for the shape;
  product skills come from a content checkout.
- **`agent[:<doc-source>]`** — agentic doc use. The model is given a
  `search_documentation` tool over `<doc-source>` (default `context7`) and
  chooses when and what to query. Tune the loop with `--max-iterations`.
- **`skill-agent:<path>`** — agentic skill use. The model sees only the skill's
  name + one-line description and calls a `view_skill_*` tool to load the full
  instructions on demand (progressive disclosure).
- **`skill-catalog:<dir>`** — skill selection. Every skill under `<dir>` is offered
  the same way, so the question is answered among its rivals. A `SKILL.md` under a
  `template/` or `templates/` directory, at any depth below `<dir>`, is not a skill and is
  skipped (a real skill kept under such a directory is skipped too). Vendored and VCS
  directories (`.git`, `.hg`, `.svn`, `node_modules`, `site-packages`, `.venv`, `venv`,
  `.tox`, `.nox`, `__pycache__`) below `<dir>` are not walked, and no note says so. A `SKILL.md`
  byte-identical to another under `<dir>` that loads under the same skill name (a plugin's
  copy of `skills/`) is offered once. Questions that carry `expected_skill` are scored
  on the skill loaded first; see
  [Skill selection](#skill-selection-does-the-agent-load-the-right-skill).
- **`<spec1>+<spec2>`** — combined treatment. Merges multiple treatments into a
  single arm by concatenating their contexts. Example:
  `docs+skill:<content-checkout>/skills/onetbb-quickstart`
  retrieves docs AND injects the skill, allowing measurement of synergistic effects.
  The `+` operator works between any treatment types except `baseline`.
  A `+` only starts a new part when the text after it starts a known kind, so a
  path may contain `+` (`skill:/skills/c++-guide`); a spec that *starts* with
  `mcp:` is never split, because an MCP URL may carry `+` in its query.

**Specs are not names.** `--arms` takes specs; the artifact's `arms` list, the
report and `--baseline-arm` use arm *names*: `docs:<source>` is named `docs`,
`mcp:<ref>` is `mcp_doc`, `agent[:<source>]` is `agent`, and `profile:`,
`skill:` and `skill-agent:` arms are named after the content they load —
`profile:<frontmatter id>`, `skill:<skill name>`, `skill_agent:<skill name>` (note
the underscore); a `skill-catalog:<dir>` arm is named `skill_catalog:<dir>`, after
its argument, so two catalogs of one size can run side by side. A combined arm is named by joining its parts' names with `+`.
The grammar lives in
[`treatments/specs.py`](../dokimi/treatments/specs.py).

Example comparing injection vs agentic use of the same MCP server:

```bash
python cli.py arms run --product oneTBB --questions <content-checkout>/questions/onetbb_golden.json \
  --arms "baseline,mcp:http=https://mcp.context7.com/mcp,agent:mcp:http=https://mcp.context7.com/mcp" \
  --judge --max-iterations 6
```

Example testing combined treatments (docs + skill together):

```bash
QUESTIONS=<content-checkout>/questions/onetbb_golden.json
SKILL=<content-checkout>/skills/onetbb-quickstart

# 4-way comparison: baseline, docs-only, skill-only, and docs+skill combined
python cli.py arms run --product oneTBB --questions "$QUESTIONS" \
  --arms "baseline,docs,skill:$SKILL,docs+skill:$SKILL" \
  --context7-id "uxlfoundation/onetbb" \
  --judge

# Or just baseline vs combined (to test synergy hypothesis)
python cli.py arms run --product oneTBB --questions "$QUESTIONS" \
  --arms "baseline,docs+skill:$SKILL" \
  --context7-id "uxlfoundation/onetbb" \
  --judge
```

The combined treatment concatenates contexts from all sub-treatments. Use this to
test whether treatments provide synergistic benefit (positive interaction effect)
or interfere with each other (negative interaction effect). Calculate interaction
as: `(docs+skill)_delta - docs_delta - skill_delta`. Positive = synergy, negative
= interference, zero = independent effects.

**More combined treatment examples:**

```bash
# Docs + profile (expert persona with comprehensive reference)
python cli.py arms run --product oneTBB --questions "$QUESTIONS" \
  --arms "baseline,docs+profile:data/agent_profiles/concise_expert.md" \
  --context7-id "uxlfoundation/onetbb" \
  --judge

# Triple combination (docs + profile + skill)
python cli.py arms run --product oneTBB --questions "$QUESTIONS" \
  --arms "baseline,docs+profile:data/agent_profiles/concise_expert.md+skill:$SKILL" \
  --context7-id "uxlfoundation/onetbb" \
  --judge

# Test if multiple skills complement each other
OTHER_SKILL=<content-checkout>/skills/onetbb-flow-graph
python cli.py arms run --product oneTBB --questions "$QUESTIONS" \
  --arms "baseline,skill:$SKILL,skill:$OTHER_SKILL,skill:$SKILL+skill:$OTHER_SKILL" \
  --judge
```

## Authoring fixtures

**Agent profile** (`agent_profiles/<id>.md`):

```markdown
---
id: concise_expert
name: Concise Expert
description: Terse, code-first senior engineer.
---
You are a senior systems engineer. Lead with code, be terse, …
```

A persona states *how to approach a problem*; a skill states *what is true about a
library*. Keeping them separate is what makes a comparison between them mean anything —
a persona that lists APIs is documentation delivered through the system-prompt channel,
and it is measuring the skill, not the persona. A persona measurement is therefore a ladder
of profiles that holds the channel fixed and varies only what each one carries:

| Rung | Carries | Role |
|------|---------|------|
| product persona | identity, convictions, reasoning order — no API names | the treatment under test |
| style-only variant | voice and stance, no product content | style floor: how much is tone alone |
| channel control | the skill's own text, verbatim | isolates the channel from the content |
| briefing | a product fact sheet | facts without a skill's structure |
| generic expert | expert framing with no product in it | negative control |

Only the two rungs that carry no product content are committed here —
`concise_expert.md` (style floor) and `generic-expert.md` (negative control). The product
persona, its style-only variant, the channel control and the briefing name a product, so
they are evaluation content: write them in your content repository and pass them as
`profile:<path>`.

`tests/test_agent_profiles.py` pins those roles for whatever profiles are committed. It
fails if a profile grows an API name — dotted names, keyword arguments, or a bare call — or
reuses five consecutive words of a committed skill, and if the negative control starts
naming a registered product. The denylist is `API_SHAPES` in that file, and companion tests
assert neither check is vacuous; it is a denylist, so it catches the shapes we have thought
of, not every possible leak. The same file also demonstrates paraphrase detection on
synthetic fixtures, so an external ladder can be checked the same way.

A channel control must stay byte-identical to the skill it mirrors — otherwise it varies
content *and* channel and isolates nothing — so generate it rather than hand-editing it.
Both paths are required, since skill and profile alike live wherever you keep them:

```bash
python scripts/sync_skilltext_profile.py \
  --skill <content-checkout>/skills/onetbb-quickstart \
  --profile <content-checkout>/agent_profiles/onetbb-skilltext.md \
  --check          # exit 1 if drifted, no write

python scripts/sync_skilltext_profile.py \
  --skill <content-checkout>/skills/onetbb-quickstart \
  --profile <content-checkout>/agent_profiles/onetbb-skilltext.md
                   # rewrite the body, keep the frontmatter
```

Re-syncing changes the prompt text and therefore `profile_prompt_sha256`, so cells already
scored on that arm measured the old text: re-run them rather than pooling.

**Skill** (`skills/<name>/SKILL.md`):

```markdown
---
name: my-skill
description: One line that tells the agent when to use this skill.
---
# Instructions
…
```

**Multi-file skills** (progressive disclosure):

Skills can contain multiple `.md` files alongside `SKILL.md`, or nested in
subdirectories (`references/`, `patterns/`). When used with
`skill-agent:<path>`, the tool offers a `file` parameter listing all available
files by path relative to the skill root. The model can load `SKILL.md` first
(typically a navigation table) and then load specific topic files as needed.

Example structure, flat or nested:
```
skills/my-skill/
  SKILL.md              # Navigation/overview with topic-to-file mapping
  getting-started.md    # Topic-specific guidance
  advanced.md           # Deep-dive content
  references/deep.md    # Nested files work too, offered as references/deep.md
```

When the `ViewSkillTool` detects bundled `.md` files, it automatically offers
them by path relative to the skill root, so a file nested under `references/`
is offered as `references/<name>.md`:
```json
{
  "file": {
    "type": "string",
    "enum": ["SKILL.md", "advanced.md", "getting-started.md", "references/deep.md"],
    "description": "Which file to load. Start with 'SKILL.md' to see the navigation..."
  }
}
```

The `SKILL.md` should contain a navigation table mapping user contexts to files:

```markdown
| Your context | Read this file |
|--------------|----------------|
| Installing and basic setup | `getting-started.md` |
| Advanced configuration | `advanced.md` |
| Deep dive on one topic | `references/deep.md` |
```

**Note:** When used with injected mode (`skill:<path>`), only `SKILL.md` is
loaded. Multi-file structure only activates in agentic mode (`skill-agent:<path>`).
For best results with injected mode, consolidate content into a single `SKILL.md`
file rather than relying on multi-file progressive disclosure.

## Skill selection: does the agent load the right skill?

Answer-quality arms ask whether a skill *helps once loaded*. Selection is a separate
question — whether the agent loads it at all, and loads it rather than a rival — and
it needs its own query set: the questions a skill helps with are usually not the
messages that make an agent reach for it.

**Relation to `novelty routing`.** [`novelty routing`](skill-novelty.md#can-the-artifact-be-selected-at-all)
asks the same question for one artifact: can its card win against named competitors?
The two differ in unit and in what the model sees:

| | `novelty routing` | `skill-catalog:` arm |
|---|---|---|
| Unit | one artifact vs `--competitors` | a whole catalog; every skill is a target |
| Model sees | cards (name + description) in one pick prompt, order permuted per query | one `view_skill_*` tool per skill in the agent loop; it may load none |
| Negatives | none; an abstention is a miss | `expected_skill: null`, scored as false triggers |
| Reports | BM25 floor; model `selection_rate`, recall@1/@3, MRR | accuracy, recall, misroute, false-trigger rate, confusion |
| Use it | while writing one skill's description, before content work | to compare models and find rival skills across a catalog |

Both are upper bounds on harness behaviour (see *Real harness* below). The positives of
a trigger set are routing queries for their skill:

```bash
jq '[.questions[] | select(.expected_skill == "linux-perf") | .question]' trigger_questions.json > linux-perf.queries.json
```

*Trigger set* here means a selection query set: messages labelled with the skill that
should load. It is unrelated to the `trigger` axis of the
[difficulty rubric](difficulty-rubric.md#axis-0--trigger-what-starts-the-work), which tags
what input starts a task (`counter`, `source`, `new_code`). `catalog trigger-set` does not
carry `metadata.trigger` into its output.

**Build the set.** `catalog trigger-set` labels question files, lints them, and writes
the format a selection tool reads:

```bash
python cli.py catalog trigger-set --catalog <skills-checkout>/skills \
  --questions <content>/questions/intel_performance_skills_golden.json=@metadata.skill_under_test \
  --questions <content>/questions/onetbb_golden.json=onetbb-quickstart \
  --questions <content>/questions/onedal_golden.json=none \
  --rewrite --model <model> --format reach --out trigger_set.json
```

- `PATH=LABEL`: a skill name, `none` (no catalog skill should load — the negatives),
  or `@field.path` read per question. A label that names no catalog skill fails the run;
  a question without the field is left unlabelled (`label_unresolved`), and a path that
  resolves for no question in its file fails the run as a typo. Unlabelled rows are
  dropped from `reach` and `skill-creator` (stderr warns; `--strict` fails instead).
  A question with no usable text (`text`, `question`, `prompt` or `question_text`) fails
  the run rather than becoming an empty query.
- `--rewrite` turns exam-style questions ("Scenario: … Describe how …") into the
  first-person request a developer would type. The rewriting model is given only the
  question text, never the catalog, so it cannot copy a description into the query.
- Lint per query, summarised on stdout: `names_skill` (the query tells the agent the
  answer), `echo_terms` (words only the expected skill's description uses),
  `exam_framing`, and `lexical_rank` (1 = word overlap alone picks the skill).
  `--strict` fails on named skills, exam framing or unresolved labels.
- `--format reach` writes a [google/skill-reach](https://github.com/google/skill-reach)
  `QuerySet`. A label listing several skills keeps the first as `expected_skill` and the
  rest as `acceptable_skills`, which skill-reach treats as neutral helpers rather than
  hits, so on those queries it is stricter than the `skill-catalog:` arm; `skill-creator --target <skill>` (a catalog skill) writes an
  [anthropics/skills skill-creator](https://github.com/anthropics/skills/tree/main/skills/skill-creator)
  eval set; `questions` writes `questions.v2` with `expected_skill` for the arm below.

**Proxy: the `skill-catalog:` arm.** Runs in the normal arms loop on any model, and the
report gains a *Skill selection* table (accuracy, recall, recall (any), multi-load,
misroute, false-trigger rate):

```bash
python cli.py arms run --product <p> --questions trigger_questions.json \
  --arms skill-catalog:<skills-checkout>/skills --model <model>
```

`expected_skill` labels are checked against the offered skills before any call. A cell
that fails is counted in the table's *Failed* column, not dropped; skills loaded together
in the first loading turn count as one choice. A first turn that loads several skills is a
hit only if all of them are acceptable; *Recall (any)* and *Multi-load* show the lenient
reading. When judged, the arm's reference is each skill the agent loaded (the
20,000-character budget split evenly among them) plus every catalog manifest. Skills that
were offered but not loaded are not shown.

It over-predicts: one tool per skill with the full description always visible, and no
reason to explore first. Use it to compare models and to find rival skills that absorb
each other's queries, not as the trigger rate users see.

**Real harness: an external tool.** Selection inside a coding agent depends on the
harness — how it renders the catalog, and whether it truncates it — so it is measured
by driving the harness itself, with a selection tool used as-is rather than vendored:

```bash
git clone https://github.com/google/skill-reach && cd skill-reach && uv sync
uv run reach eval --skills <skills-checkout>/skills --queries trigger_set.json \
  --agent claude-code --model <model> -O skill_listing_budget_fraction=0.03 \
  --attempts 3 --workdir /tmp/reach-run --out eval.json -y
uv run reach diff --vary description control.json treatment.json   # A/B vs noise floor
```

**Real harness, in dokimi: `catalog trigger-probe`.** Runs each query in a fresh
`claude -p`: a new git repository with the catalog committed in `.claude/skills/`, a
scratch `HOME` and `CLAUDE_CONFIG_DIR` (your own skills, settings and `CLAUDE.md` are not
loaded; the model can still read any host path it has access to, the trigger set and its
labels included), the query sent verbatim with no notice or task framing, and bundled skills
left on as a user has them (`--sealed` turns them off). The skill chosen is read from the
transcript's `Skill` calls, and the output's `skill_selection_summary` is scored by the
same code as the `skill-catalog:` arm, one column per trial. `max_turns_hit` counts runs
that ended on `--max-turns` (scored as no load), and the command exits 1 when Claude Code
did not list a catalog skill (`not_listed`). When no run listed any skill, `not_listed`
stays empty and a warning on stderr says it was not checked. A trigger set with no
labelled query exits 2 before any run. Records are kept in `<out>.partial` while the
probe runs:

```bash
python cli.py catalog trigger-probe --catalog <skills-checkout>/skills \
  --trigger-set trigger_set.json --model <model> --trials 3 --out probe.json
```

Two things decide whether such a number means anything:

- **Listing budget.** Claude Code shows skill descriptions only up to a budget (a
  fraction of the model's context window); past it, skills appear as bare names. The
  `skill-catalog:` arm records `catalog_listing_chars` so the size can be compared with
  the budget; pin `skillListingBudgetFraction` (above) or the run measures names, not
  descriptions.
- **Repeats.** Selection varies run to run; use `--attempts 3` or more and read the
  noise floor `reach diff` reports before calling a description change an improvement.

### How to measure tool choice

The same rules hold for skills, MCP tools and plain function tools.

- **Score the calls, not the judge.** Which tool the agent called is in the
  trace: the *Skill selection* table, `tool_preference.v1` `results`, a
  transcript's `tool_usage` (the turn is in `first_call_iteration_by_name`, and
  for skill tools in `skill.first_call_iteration`). A judge score mixes two things: whether
  the tool was picked and whether its content helped. A better answer can come
  from a tool the agent should not have needed. A call with arguments that do not
  fit the tool's schema is not run. It is kept with its `error` and counted in
  `tool_usage.call_errors`, and the other counters leave it out. Report it on its
  own line: the agent picked the tool and could not use it.
- **Compare against an equal alternative.** A choice tells you something only
  when another offered tool would have done the job just as well. Otherwise it
  measures whether the model can see that the task needs a tool. `tools
  preference` builds such pairs (twins) and controls that need no tool, and it
  swaps which twin is listed first. With both twins neutral, the first-listed one
  is still not chosen exactly half the time, so a comparison that never swaps the
  order mixes the position effect into the effect you are measuring.
- **Repeat; do not pin.** Current reasoning models accept only `temperature=1`
  ([Pinning sampling](benchmarking-and-comparison.md#pinning-sampling)), so the
  choice varies from run to run. Run 3 or more repeats (`tools preference --reps 3`,
  `reach eval --attempts 3`); for the other proxies, repeat the run. Read `same_choice_rate`, or the noise floor `reach diff` reports, before you read
  an effect. A change smaller than the variation between repeats of one setting
  is not an effect.
- **Treat a proxy as an upper bound on effect size.** A one-call proxy (the
  `skill-catalog:` arm, `tools preference`, `novelty routing`) shows every
  description in full and gives the model no reason to explore first. In one
  measured case the effect kept its direction on Claude Code but was about half
  as large (−0.42 on the proxy, −0.20 on the harness;
  [Tool preference](tool-preference.md)). Use the proxy to find and rank changes,
  then confirm the ones you ship on the real harness with a pinned listing budget.

## Comparing cells across runs

One treatment measured over several models is many artifacts, one per cell. Two tools read
them, and both refuse comparisons that would look fine and mean nothing.

**`report model-compare`** puts one row per run beside a shared baseline:

```bash
python cli.py report model-compare \
  --regular-runs results/arms/haiku_regular.json results/arms/opus_regular.json \
  --run-ids haiku,opus \
  --out results/arms/compare.md
```

It names the arm it scored — the "Models Compared" table gains a **Treatment arm** column,
and the summary/ranking headers read `profile:concise_expert` instead of a generic
"Context arm" — because a `profile:` report and a `docs:` report are otherwise the same
document. It aborts, rather than warns, when the runs cannot be pooled:

- **different `baseline_arm`** — the deltas are measured from different zeros;
- **different treatment arms** — each run's delta would measure a different treatment. Pass
  `--treatment-arm <name>` to pin one arm every run must be scored on (which is also
  required when a run holds more than one non-baseline arm);
- **one arm name carrying two prompt digests** — the arm name comes from the profile's
  frontmatter `id`, so an edited profile leaves stale cells indistinguishable by name.
  Artifacts written before provenance stamping carry no digest and are left alone.

A differing `question_set_hash` is a warning. Two runs of the same configuration are an
error unless `--pool-replicates` averages them into one row.

**`scripts/check_treatment_batch.py`** gates a whole batch directory before you read any
number out of it:

```bash
python scripts/check_treatment_batch.py results/arms/persona-batch \
  --profile data/agent_profiles/concise_expert.md
python scripts/check_treatment_batch.py results/arms/skills \
  --treatment-arm 'skill:*' --expect-n regular=40,golden=20 --expect-files 12
```

A batch judged before the 2026-10-13 default change names its judge, since the pin is
`anthropic/claude-sonnet-5-5`:

```bash
python scripts/check_treatment_batch.py results/arms/persona-batch \
  --profile data/agent_profiles/concise_expert.md \
  --judge-provider amazon-bedrock --judge-model global.anthropic.claude-sonnet-4-6
```

It checks schema, exactly two arms, the baseline arm, the treatment-arm pattern, a pinned
`--judge-model`/`--judge-provider`, that the judge is not the answering model
(self-judging), question and per-arm answer counts, and — for `profile:` arms — that the
system prompt reached every treatment answer and no baseline answer. With `--profile` it
compares each cell's stamped digest against the file's current text and reports
`STALE ARTIFACT`, which is what catches a cell a runner would otherwise skip as done.

`--expect-n` maps a *filename suffix* to a question count, and the defaults (`regular=40`,
`golden=20`) are one product's sizes only — observed golden sets run 20, 24 or 111 questions
depending on the product, so a batch over another product must pass its own. Two things keep that from
turning into a green row of wrong counts: a filename matching none of the suffixes is
reported rather than silently gated against the artifact's own `total_questions` (the runner
writes that field as `len(records)`, so every count check would hold by construction), and a
`--expect-n` value that disagrees with `total_questions` is flagged as a mis-specified map
rather than a short run.

Two gates are deliberately statistical rather than absolute:

- **coverage, not zero errors.** A judge call fails occasionally (~1.25% observed against
  the Intel proxy), and a failed call leaves `aggregate: null`. The gate requires ≥95% of
  questions scored in *both* arms.
- **paired deltas, not the artifact's own number.** `delta_vs_baseline` averages each arm
  over whatever it scored, so one failed call subtracts means taken over different
  questions (39 vs 40 was observed, worth ~0.4 points). The script recomputes over
  questions both arms scored. It pools questions; a mean of per-cell means differs slightly
  when the sets have different sizes — say which one a report uses.

## Headroom: items a comparison cannot learn from

A skill cannot raise an item the no-skill arm already answers, and an item no arm
answers points at the question or its grader, not at the skill. `eval headroom` flags
both, from one or more arms files of the same question set (one file per repetition):

```bash
python cli.py eval headroom run1/arms.json run2/arms.json run3/arms.json \
    --no-skill-arm baseline --ceiling 0.9 --floor 0 --min-reps 3 -o headroom.json
```

- **ceiling**: the no-skill arm's mean over the runs is at least `--ceiling` (0-1; the
  0-100 aggregate divided by 100). Default 0.9.
- **floor**: every arm's mean is at most `--floor`. Default 0.
- **provisional**: fewer than `--min-reps` runs scored the item (the smallest count over
  its arms). Default 3. One file gives every item one rep, so every item is provisional.

A run is one file. A file repeated on the command line, or byte-identical to an earlier
one, is skipped with a note, so it does not add a rep. A question id repeated within one
file is an error (exit 2): it would count as two runs.

The table on stdout lists flagged and provisional items, with the no-skill arm in its own
column; `-o` writes a `headroom.v1` artifact with every item. Scores are compared unrounded
and shown rounded down, so 0.89996 prints as 0.89. Without `--no-skill-arm`, the runs must
declare the same baseline; the default is that baseline. With `--no-skill-arm`, the arm must
be one the runs list (exit 2 otherwise, naming the arms that exist), and the declared
baselines are not checked. Only arms runs are read. The flags are descriptive: they do not
change any score or any gate.

## Scope and caveats

- **Three levels of fidelity for skills/MCP.** (1) `skill:` / `mcp:` inject
  content directly — cheapest, but a model can't *choose* not to use it.
  (2) `skill-agent:` / `agent:` let the model decide via the tool-calling loop —
  faithful to how skills/MCP are consumed, but still read-only and in-process.
  (3) Running a skill's bundled *scripts* needs real sandboxing and belongs on
  the executable [terminal-bench track](contributing-terminal-bench-task.md)
  — the in-process loop deliberately exposes only read-only tools.
- **Single-answer judging under-measures skills and persona prompts**, whose
  value is often procedural. Treat the deltas as a proxy; use the task track for
  behavioral signal.
- **Fair comparisons** hold the answer model, question set, and judge fixed
  across arms — only the treatment varies, and the judge's reference material
  varies with it (see above).
- **Combined treatments concatenate contexts in the order the arm spec lists them**,
  not in a fixed order by kind. `profile:` arms contribute a system prompt rather than
  injected context, so they never appear in that concatenation. Combining is consistent
  but may increase prompt token usage significantly — test on a small question set
  first to estimate cost impact before running full evaluations.
- **A combined arm sends only one system prompt.** If two `profile:` arms are combined,
  the first one wins, the rest are dropped with a warning and listed in the arm's
  `dropped_system_prompts` metadata. Compare personas as separate arms, not combined.
- **Interaction effects may not be additive.** `(docs+skill)_delta` may differ from
  `docs_delta + skill_delta` due to context interference, redundancy, or synergy.
  Always measure the combined treatment directly rather than assuming linear effects.
