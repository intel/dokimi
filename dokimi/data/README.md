<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# `data/` — curated framework fixtures

This directory holds the **curated, version-controlled inputs** the framework's own
examples and tests run on. Everything here is meant to be small, reviewed, and
reproducible — it is the committed counterpart to the throwaway output that CLI runs
write to the git-ignored `results/`, `reports/`, and the default scratch dirs
(`answers/`, `eval/`, `questions/`, `personas/`, `baselines/`) at the repo root.

> Keep these names distinct from the Python package: `data/skills/` is *sample skill
> content*, while `dokimi/skills/` is the *loader code* for it. The same holds
> for `data/agent_profiles`.

## Layout

| Path | What it is | Produced by / consumed by |
| --- | --- | --- |
| `skills/fastcontext/SKILL.md` | The one committed skill: a product-neutral "explore before you act" skill, used by the explore track and as the skill fixture in examples | `skill:`/`skill-agent:` treatment arms |
| `agent_profiles/concise_expert.md` | An answer-style persona (terse, code-first) | `profile:` treatment arm |
| `agent_profiles/generic-expert.md` | Negative control: an expert framing that names no product, to pair with a product persona | `profile:` treatment arm |
| `explore_tasks/<name>.yaml` | Localization tasks over this repository's own code, with known-good references | `cli.py explore run` |

## What does not live here

**Evaluation content.** Product skills, question sets, product personas, executable
tasks and subject descriptors are content, not framework code: they live in a separate
content repository and are supplied to the framework by path —
`skill:<checkout>/skills/<name>`, `profile:<path>.md`, `--questions <path>`,
`--questions-root <dir>`, `--tasks-root <dir>`. Nothing in the framework resolves them
from `data/`.

**Captured runs.** Answer runs, judge scores and static-benchmark snapshots are
*outputs*, so none are committed. `cli.py answers generate`, `cli.py eval score` and
`cli.py run` write them to the git-ignored scratch dirs (`answers/`, `eval/`,
`results/`, `reports/`, `baselines/`); the schema each one must satisfy is locked by
`dokimi/schemas/*.json` and by `tests/test_artifacts.py`, not by a committed
example. For the compare flow, take your own base snapshot — see `../Makefile` and
[`../docs/benchmarking-and-comparison.md`](../docs/benchmarking-and-comparison.md).

## Adding or regenerating a fixture

A fixture belongs here only if the framework itself needs it: something a test or a
documented example runs on, and that stays true regardless of which product is being
measured. All three current fixtures are hand-written and reviewed, so adding one is an
edit plus the test that reads it — `tests/test_agent_profiles.py` for a profile,
`tests/test_explore_track.py` for an explore task.

Generated artifacts are the other case, and they do not land here. A question-generation
run writes to the git-ignored scratch dir, and the result is evaluation content:

```bash
python cli.py questions generate --product oneTBB \
  --personas personas/onetbb.json \
  --output questions/onetbb.json      # git-ignored; commit it to the content repo
```

Anything product-specific belongs in the content repository instead, where it can be
versioned with the product it describes.
