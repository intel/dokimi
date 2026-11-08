---
depends_on:
  - dokimi/dokimi/external/**
  - dokimi/dokimi/commands/catalog.py
  - dokimi/dokimi/commands/catalog_eval.py
---
<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Pointing Dokimi At An External Content Repository

## Key-free catalog planning and checks

Run the deterministic catalog path against a local checkout:

```bash
dokimi catalog eval /path/to/catalog --tier static --out-dir /path/to/receipts
dokimi catalog eval /path/to/catalog --tier static --out-dir /path/to/receipts --changed-from BASE
```

The dedicated output directory contains `catalog.json` (`catalog_eval.v1`),
`plan.json` (`plan.v1`), `result.json` (`result.v1`) and `report.md`. The plan
declares the always-run catalog integrity check and one static check per selected
skill, with no model. The result accounts for exactly those checks. The installed
checker fingerprint and input bundle/task digests bind the receipts to the bytes
checked (vendored and VCS directories such as `.git`, `node_modules` and `.venv` inside a skill
or task are not part of its digest). A catalog root that is itself a symlink is refused; a
symlink in a parent directory is resolved once, and no link below the root is followed (with no `skills.yaml`, a linked directory beside a discovered skill or under `skills/` is an `unsafe-path` finding, not skipped; links elsewhere, such as `.claude/skills`, are ignored). No model, Docker, skill script, oracle or task verifier runs here.

Tasks are discovered on disk and assigned using their `metadata.skill` field.
`evaluation/harbor/suites.json` is cross-checked: a task present on disk but absent
from a suite remains visible with a warning; a missing declared implemented task,
unknown owner or contradictory ownership fails validation. A task that cannot be
loaded (for example invalid TOML) is a `task-load` finding on its declaring skill, or on
the catalog when no suite declares it. Planned tasks remain
listed separately. Imported skills or suites with `evaluated_here: false` receive
`checks_only`; local skills without actual tasks receive `no_tasks`; other skills
receive `not_measured`. A static pass establishes none of their runtime outcomes,
skill usefulness, discovery rate or hardware performance, and changes no catalog
promotion status.

`--changed-from` compares a locally available base commit with working-tree bytes,
including untracked and ignored files that a skill or task bundle hashes (files
under a vendored directory such as `node_modules` and any `.git` are not hashed, so
they select nothing). Skill resources and owned tasks select their skill, and a
changed or removed task also selects the skill that owned it at the base;
registry, suite, shared machinery and unclaimed changes select conservatively.
Selection limits what can fail: a finding in a skill that is not selected is
listed in the report but does not fail the run, while a catalog-wide problem
(manifest, suites, tasks root, a task with no known owner) always does.
Removed skill paths remain visible in the selection receipt. An unavailable base
selects all skills with an explicit note. A docs-only change can select no skills;
the catalog integrity check still runs. Git paths are read as NUL-delimited data,
and candidate hooks, external diff tools and filesystem monitors are not run.

Exit 0 means the deterministic checks passed, exit 1 reports validation findings,
and exit 2 reports unusable input or output. A failed validation rewrites the
receipts with failure; an input error removes earlier success receipts and leaves
an error report when the output directory is usable. The directory must be empty
or contain only this command's earlier receipts, and must be outside the entire
catalog checkout and its ancestors. The directory itself is never renamed or
removed, so it may be a mount point, the current directory or sit under a
read-only parent; receipts are staged in a scratch directory inside it. Linked catalog roots and inputs are refused.
Successful outputs are prepared and schema-checked before replacement.

This path can run in a read-only, key-free catalog workflow. It is not a substitute
for qualified oracle/negative controls and complete paired trials before a scored
skill promotion. The existing `repo-eval.yml` workflow reads repository task
manifests rather than a Harbor skill catalog.

This repository is the measuring instrument. The things it measures —
skills, question sets, executable tasks, exploration tasks, agent profiles —
live in a separate content repository (for example `uxlfoundation/skills`).
Nothing here clones anything: you check the content repo out yourself and name
the directory on the command line.

Every content location is an explicit flag. There is no search path and no
implicit default that points at a sibling checkout:

| Flag | What it names | Commands |
|---|---|---|
| `--root` | a whole content checkout to scan | `catalog check` |
| `--questions-root` | directory holding `<product_key>_golden.json` | `benchmark preflight/run/batch`, `evaluate`, `coverage`, `config_check` |
| `--tasks-root` | directory of executable task dirs (each with `task.toml` or a BenchFlow `task.md`), or of exploration task YAML files | `eval tasks list`/`tasks run`, `explore list/run`, `coverage`, `config_check` |
| `--repo-root` | the source tree an exploration task is scored against | `explore run` |
| `--registry` | a `products.yaml` describing products this repo has never heard of | `benchmark preflight/run` |
| `--questions` | one question-set file | `arms run`, `answers generate` |

Skills, profiles and subjects are named by path inside the arm spec itself
(`skill:<path>`, `profile:<path>`, `subjects show <descriptor>`), so they need
no root flag.

The transcripts below are real runs against a small demo checkout laid out the
way the readers expect. Substitute your own checkout path.

## The layout the readers expect

```text
<checkout>/
  skills.yaml                        # optional manifest: name, status, catalog_path
  skills/<skill-name>/SKILL.md
  evaluation/harbor/suites.json      # optional manifest: suites -> tasks (status, track)
  evaluation/harbor/tasks/<task>/    # task.toml + instruction.md + tests/ + solution/
  questions/<product_key>_golden.json
  explore_tasks/<task>.yaml
  agent_profiles/<persona>.md
  products.yaml                      # optional: products this repo's registry lacks
```

Both manifests are optional — with no manifest, the readers fall back to a
plain filesystem scan of the same directories (a `SKILL.md` under a `template/`
or `templates/` directory is a scaffold and is left out; vendored and VCS directories such
as `.git`, `node_modules`, `site-packages` and `.venv` are not walked). `skills.yaml` comes in two
shapes, and both hold a *list* of entries:

```yaml
# skills.yaml, uxlfoundation/skills: entries under `skills:`, catalog_path required
skills:
  - name: demo-skill
    status: published
    catalog_path: skills/demo-skill
```

```yaml
# skills.yaml, intel/skills: the file is the list, the skill is at skills/<name>
- name: demo-skill
  maintainer: "github-handle"
```

In the second shape `name` must be a single directory name. An entry without
`status` scans with status `-`; intel/skills reads that as `published`.

```json
// evaluation/harbor/suites.json
{"suites": [{"name": "demo", "tasks": [{"name": "demo-widget", "status": "published", "track": "executable"}]}]}
```

 A checkout that puts tasks somewhere other than
`evaluation/harbor/tasks` can be scanned with `catalog check --tasks-subdir
<relative-path>`; the run commands take the full path as `--tasks-root`
regardless of layout.

## 1. Scan the checkout before you spend anything

`catalog check` is a pre-flight: it reads the manifests, resolves each declared
entry on disk, and reports one line per entry. It never calls a model, so it
costs nothing and is the cheapest way to find out that a suite you were about
to pay to run is half-declared.

```console
$ python cli.py catalog check --root C:/Users/Public/content-repo
# skills (2 declared under C:\Users\Public\content-repo; 1 ok, 0 warn, 1 error)
demo-skill      published       C:\Users\Public\content-repo\skills\demo-skill  ok
not-written-yet planned C:\Users\Public\content-repo\skills\not-written-yet      ERROR: Skill path does not exist: C:\Users\Public\content-repo\skills\not-written-yet

# tasks (3 declared under C:\Users\Public\content-repo)
demo-widget     published       executable      ok
demo-quiz       published       answer-quality  skipped: no task.toml or task.md under C:\Users\Public\content-repo\evaluation\harbor\tasks\demo-quiz (manifest status='published')
demo-later      planned executable      skipped: no task.toml or task.md under C:\Users\Public\content-repo\evaluation\harbor\tasks\demo-later (manifest status='planned')
$ echo $?
1
```

Read that output as three outcomes, not two:

- `ok` — the entry loaded and can be run.
- `skipped` — a declared entry the scan is *expected* to pass over: a
  `planned` task with no directory yet, or a `track` that `tasks run` does not
  drive (`answer-quality` entries are question-set material, not Docker tasks).
- `ERROR` — an entry that should have worked and did not.

Only load errors change the exit code, so `catalog check` exiting 0 means
"everything declared as runnable is runnable". A spec error (below) changes it
only under `--strict`. A root with no `SKILL.md` at all (and no skills.yaml entry)
prints `no SKILL.md found under <root>` to stderr and exits 1. With `--kind all`, a
tasks-only root only warns, and exits 1 under `--strict`. Note the asymmetry above: a `planned` *task*
with no directory is skipped, while a `planned` *skill* with no directory is an
error — the skill reader has no notion of a not-yet-written entry. Either drop
such a skill from `skills.yaml` or expect the non-zero exit.

A loaded skill is also checked against the Agent Skills spec. Its `name` must be its directory name
and kebab-case (at most 64 characters; a `SKILL.md` directly in the scanned root is not compared
with the root directory's name), and its `description` at most 1024 characters: a violation is an
`error`, printed under the skill. It exits 1 only with `--strict`; without it, the scan reports the
error and still exits 0. A `name` missing from the frontmatter, or a `SKILL.md` byte-identical to
another skill's, is a `warn`. So is a skill whose `name` another skill shares with different content
(`name-conflict`), when both are listed: neither is `disable-model-invocation` or has a spec error
(a description over 1024 characters, as the only error, does not unlist a skill for routing).
The finding names the other path, and `dokimi novelty routing --catalog` skips that name rather than
the whole catalog. Of identical copies, the one nearest the root (fewest path parts, ties to path
order) is the original, the copy the `skill-catalog:` arm offers; each other copy is the `warn` and
names it. Warnings never change the exit code. `disable-model-invocation: true` is printed as a
`note`, because the model is never offered that skill.

The scan also checks the bundle and the description. A relative Markdown link
to a file the skill does not have is a `warn` (`link-missing`), and says when the
file exists from the catalog root instead. Example links such as `[Title](URL)`
are common, so `--strict` does not fail on them. A link, or a `../` code span,
that reaches a file outside the skill directory is a `warn`
(`link-escapes-skill`, `path-escapes-skill`), since it breaks when the skill is
installed alone. A description with no "when" clause (`use when ...`, `when deploying ...`) is a `warn`
(`description-no-when`), and so is one that overlaps another skill's by at least
half and shares three words (`description-near-duplicate`). Two skills in different paths with the same
directory name and description but different `SKILL.md` text are a `warn` on both
(`mirror-drift`), naming the other copies. Skills with
`disable-model-invocation: true` are exempt from the description checks.

Frontmatter that is not valid YAML (a bare colon in `description:`, say) stops
the skill from loading. The scan reports that as a `frontmatter-parse` error
carrying the YAML message, and it exits 1 with or without `--strict`, as a load
error does. The YAML message is on the indented `frontmatter-parse` line under
the skill (and in its `findings` in JSON); the row's main line and its JSON
`error` field say the frontmatter has invalid YAML, with the line, not that the
skill is missing a `description`.

`--strict` makes `catalog check` a gate on the spec as well as on loading. The
text output and `--format json` (one object: `state`, `findings`,
`content_sha256` of the `SKILL.md`, `skill_sha256` of the skill directory,
`duplicate_of`, for a script or a CI gate) are the same either way.

Restrict the scan with `--kind`:

```console
$ python cli.py catalog check --root C:/Users/Public/content-repo --kind tasks
# tasks (3 declared under C:\Users\Public\content-repo)
demo-widget     published       executable      ok
demo-quiz       published       answer-quality  skipped: no task.toml or task.md under C:\Users\Public\content-repo\evaluation\harbor\tasks\demo-quiz (manifest status='published')
demo-later      planned executable      skipped: no task.toml or task.md under C:\Users\Public\content-repo\evaluation\harbor\tasks\demo-later (manifest status='planned')
$ echo $?
0
```

A large catalog is easier to scan one skill at a time:

- `--per-skill` reviews each skill alone. The four cross-catalog checks
  (`duplicate`, `name-conflict`, `mirror-drift`, `description-near-duplicate`)
  compare a skill with the others, so they do not run in this mode; every other
  check does.
- A `SKILL.md` inside another skill's directory is marked with `nested_in`
  (the enclosing skill's path) in JSON and with a `note` in text. It is counted
  by default; `--skip-nested` leaves it out of the scan.
- `--ignore CODE` (repeatable) leaves the findings with that code out of the scan:
  they are not printed, and they do not set the state or the `--strict` exit. The
  codes are the `code` of each finding in the JSON output (`name-mismatch`,
  `description-no-when`, ...); an unknown code is a usage error.
- `--jsonl PATH` writes one JSON row per skill as it is reviewed and prints only
  the counts. `--jobs N` reviews N skills in parallel. `--resume` keeps the rows
  already in PATH and reviews only the skills without one (a half-written last
  line from a killed run is dropped). Each row carries `skill_sha256`, the
  digest of every file in the skill's directory, and `ignored`, the sorted
  `--ignore` codes in effect (`[]` when none). `--resume` keeps a row only
  while that is unchanged and the `--ignore` codes are the same: a skill whose
  `SKILL.md` or bundled file changed, a row written under other codes, or a row
  without `skill_sha256` is reviewed again. Without `--resume`, PATH is replaced.
  Every `SKILL.md` is loaded before the first row, so memory still grows with
  the catalog. A `--jobs` worker that dies ends the scan with exit 1.

`--jsonl`, `--jobs` and `--resume` need `--per-skill`, and `--jsonl` does not
combine with `--format json`; any other misuse exits 2. From Python,
`dokimi.external.review_skill_dir(path)` returns the `SkillReview` of the one
skill in `path`.

## 2. The awareness half: question sets

A question set is `<questions-root>/<product_key>_golden.json`, in the format
described in [methodology.md](methodology.md#question-record-format): new sets
put each question's text under `question` (`questions.v2`); existing sets that
use `text` (`questions.v1`) are still read, as are older hand-written shapes
(no `id`, `question_id`/`question_text`, a list `metadata`), with a one-line
notice. The golden set is only ever read; the run writes its own v2 copy. Point
`--questions-root` at the checkout's `questions/` directory and the pipeline
picks up the golden set for the product it is running:

```console
$ python -m dokimi.coverage \
    --tasks-root C:/Users/Public/content-repo/evaluation/harbor/tasks \
    --questions-root C:/Users/Public/content-repo/questions
| Product | Awareness (questions) | Work (tasks) | Status |
| --- | --- | --- | --- |
...
| `onetbb` | yes | 1 | covered |
```

(One row per registered product — 24 of them; only the row this checkout
covers is shown.) `onetbb` is `covered` because the checkout has both
`questions/onetbb_golden.json` and one task tagged `onetbb`.

Resolution is logged, including the miss:

```text
INFO Using golden questions: C:\Users\Public\content-repo\questions\onetbb_golden.json
WARNING questions root C:\Users\Public\content-repo\questions has no golden set for onemkl; running without a golden question set
```

`benchmark run` logs a warning and continues with generated questions only.
`benchmark preflight` refuses a missing golden set and exits 2.
If you are comparing arms, that is usually not what you want; check the coverage matrix first.

`benchmark preflight` resolves the whole plan without calling a model, and
echoes the root it will read:

```console
$ python cli.py benchmark preflight --library demo \
    --registry C:/Users/Public/content-repo/products.yaml \
    --questions-root C:/Users/Public/content-repo/questions
Resolved benchmark plan
  target: demo
  name: demo
  description: Worked example product for the external-content walkthrough.
  doc_source: context7
  output_dir: results/demo_final
  answer: openai/gpt-6.1-sol
  judge: anthropic/claude-sonnet-5-5
  questions_root: C:/Users/Public/content-repo/questions
  retrieval: semantic-only, top_k=3
  arms: without_docs vs with_docs
  max_tokens_per_question: 4000
  concurrency: 5
  context7_id: demo-org/demo
  golden questions: C:\Users\Public\content-repo\questions\demo_golden.json (2 items)
```

(It exited 2 here because no API keys were set: `✗ missing OPENAI_API_KEY for
provider openai`. Key checks are part of the pre-flight.)

For a direct N-arm comparison, hand `arms run` the question file itself:

```bash
python cli.py arms run --product demo \
  --questions C:/Users/Public/content-repo/questions/demo_golden.json \
  --arms baseline,skill:C:/Users/Public/content-repo/skills/demo-skill \
  --judge --judge-model <a-different-model>
```

This is the one command on this page that bills a provider, so no transcript is
shown; [evaluating-treatments.md](evaluating-treatments.md) covers reading its
report. Section 5 shows how to check the arm specs resolve first.

## 3. The work half: executable tasks

`--tasks-root` is the directory that *contains* task directories:

```console
$ python cli.py eval tasks list --tasks-root C:/Users/Public/content-repo/evaluation/harbor/tasks
demo-widget     easy    demo,widget
onetbb-parallel-reduce  medium  onetbb
```

The third column is `[metadata] tags`; those tags are what tie a task to a
product in the coverage matrix (a tag matches a product's key in
`products.yaml`, or one of that product's `tag_aliases:`).

A with-skill treatment arm over an external skill is two harnesses over one
task set — `--dry-run` validates the wiring without Docker or a model:

```console
$ python cli.py tasks run --tasks demo-widget \
    --tasks-root C:/Users/Public/content-repo/evaluation/harbor/tasks \
    --harnesses docker-oracle,docker-claude-skill:C:/Users/Public/content-repo/skills/demo-skill \
    --baseline-harness docker-oracle \
    --model us.anthropic.claude-haiku-4-5-20251001-v1:0 \
    --dry-run --out-json results/demo-widget.json
Saved task run artifact: results\demo-widget.json
Saved task run report:   results\demo-widget.md
docker-oracle: 0/1 pass (0.00%), ops=1, elapsed=0.00s
docker-claude-skill:C:/Users/Public/content-repo/skills/demo-skill: 0/1 pass (0.00%), ops=1, elapsed=0.00s
docker-claude-skill:C:/Users/Public/content-repo/skills/demo-skill vs docker-oracle: pass-rate delta +0.00%
```

A dry run reports every cell as not passed; only the matrix shape and the
artifact are being checked. See
[coding-harnesses.md](coding-harnesses.md) for the harness list, the real
Docker flow, and the paired statistics on this track.

## 4. Exploration tasks: `--tasks-root` plus `--repo-root`

Exploration tasks are YAML files that ask where something is, and are scored
against a source tree. The two roots are separate because the question and the
code being searched normally live in different checkouts — `--tasks-root` is
the YAML directory, `--repo-root` the tree a task is scored against when its
own YAML omits `repo_root:`.

```console
$ python cli.py explore list --tasks-root C:/Users/Public/content-repo/explore_tasks
demo-locate-widget      demo    1 ref(s)        Where is the widget width clamped?

$ python cli.py explore run --all \
    --tasks-root C:/Users/Public/content-repo/explore_tasks \
    --repo-root C:/Users/Public/content-repo/product-checkout \
    --arms oracle,empty --baseline-arm empty --out-json results/explore.json
Saved explore run artifact: results\explore.json
oracle: n=1 file_f1=1.000 line_f1=n/a validity=1.000
empty: n=1 file_f1=0.000 line_f1=n/a validity=1.000
oracle vs empty: file_f1 Δ +1.000
```

The `oracle` and `empty` arms are the ceiling and floor: oracle cites the
task's own references (F1 1.000), empty cites nothing (0.000). Getting those
two numbers is how you confirm `--repo-root` points at the right tree before
running a model arm — an oracle F1 below 1.000 means the references do not
resolve inside the tree you named.

## 5. Skills and profiles as arms

Arms name paths directly, so an external skill or persona needs no root flag.
Arm names are derived from the file or directory name, not the full path:

```console
$ python -c "from dokimi.treatments import create_treatments; [print(a.name) for a in create_treatments(['baseline','skill:C:/Users/Public/content-repo/skills/demo-skill','profile:C:/Users/Public/content-repo/agent_profiles/demo-expert.md'])]"
baseline
skill:demo-skill
profile:demo-expert
```

A path that does not exist fails immediately, before any model call:

```text
FileNotFoundError: Skill path does not exist: C:\Users\Public\no-such-checkout\skills\demo-skill
```

A subject descriptor bundles the artifact with the suite it should be measured
on, and may point at the external checkout throughout. `subjects show`
resolves it — including the arm specs it will run — without executing
anything:

```console
$ python cli.py subjects show C:/Users/Public/content-repo/demo-subject.toml
{
  "id": "demo-skill",
  "subject": {
    "kind": "skill",
    "ref": "C:/Users/Public/content-repo/skills/demo-skill",
    "members": []
  },
  "suite": {
    "products": ["demo"],
    "questions": ["C:/Users/Public/content-repo/questions/demo_golden.json"],
    "tasks": ["demo-widget"]
  },
  "baseline": "baseline",
  "awareness_arm_specs": [
    "baseline",
    "skill:C:/Users/Public/content-repo/skills/demo-skill"
  ],
  "work_arm_specs": [
    "baseline",
    "skill-agent:C:/Users/Public/content-repo/skills/demo-skill"
  ],
  ...
}
```

(Reformatted here for width; the command prints one JSON key per line.)

`suite.tasks` entries are resolved the way `tasks run --tasks` resolves them: a
bare name such as `demo-widget` needs a root, so `subjects run` takes the same
`--tasks-root <checkout>/evaluation/harbor/tasks`. A full path to a task
directory works without it.

## 6. Products this repository has never heard of

The committed registry is the bundled `dokimi/products.yaml`. A product outside it
is unknown, and `benchmark` says so rather than inventing one:

```console
$ python cli.py benchmark preflight --library demo
Error: "Product 'demo' not found in registry. Available: advisor, dpctl, dpnp, ..."
```

Supply the product with `--registry <checkout>/products.yaml` (as in section 2).
An entry there can declare `context7_id:`, which is what documentation and MCP
arms resolve against.

There is no name-to-library-id guessing. If a documentation arm cannot find an
id, it stops and tells you both ways to give it one:

```console
$ python cli.py answers generate --product widgetlib \
    --questions C:/Users/Public/content-repo/questions/demo_golden.json
Loaded 2 questions from C:/Users/Public/content-repo/questions/demo_golden.json
Documentation source: context7
Error: 'widgetlib' is not a Context7 library ID. Context7 IDs look like 'uxlfoundation/oneTBB' (GitHub repo) or 'intelpython_github_io_dpnp' (docs website), and cannot be derived from a product name. Either declare 'context7_id:' for the product in products.yaml, or pass --context7-id on the command line.
```

Resolution order for documentation and MCP arms is: explicit
`--context7-id`, then the `;id=` of an `mcp:` source, then the product's
`context7_id:` in the registry, then the error above.

A *registered* product that declares no `context7_id:` is the same error, named
per product. That case needs its own check because a Context7 website id looks
like `host_path` — a registry key with an underscore in it (`mkl_extensions`)
would satisfy that shape and be sent as an id, retrieving nothing while the run
still reports a documentation arm.

## 7. What happens when a root is missing

Worth knowing before you read a suspiciously clean result:

| Command | Root missing | Exit |
|---|---|---|
| `catalog check --root <missing>` | `Error: --root is not a directory: <path>` | 2 |
| `catalog check --root <no SKILL.md> --kind skills` | `no SKILL.md found under <path>` | 1 |
| `tasks run --tasks-root <missing>` | `Error: --tasks-root is not a directory: <path>` | 2 |
| `eval tasks list --tasks-root <missing>` | `Error: --tasks-root is not a directory: <path>` | 2 |
| `explore list --tasks-root <missing>` | `Error: --tasks-root is not a directory: <path>` | 2 |
| `coverage` with no roots | every product reads `no`/`0`, plus an explicit note | 0 |
| `evaluate`/`benchmark run` with a root lacking the golden set | `WARNING ... has no golden set for <product>` and continues | — |

`eval tasks list` is the trap for an existing directory: an empty listing and a correct listing
both exit 0, so an empty checkout looks like a correct one. A missing root is refused with exit 2,
not a silent success. `explore list` refuses an empty root with exit 2. The `coverage` matrix is explicit about it instead:

```console
$ python -m dokimi.coverage
...
Note: no --tasks-root given; the work half is unresolved.
Note: no --questions-root given; the awareness half is unresolved.
```

Use `config_check` as the CI-friendly form of the same question; it validates
the registries and, when given roots, the coverage contract:

```console
$ python -m dokimi.config_check \
    --tasks-root C:/Users/Public/content-repo/evaluation/harbor/tasks \
    --questions-root C:/Users/Public/content-repo/questions
✓ product and intent registries are consistent
```
